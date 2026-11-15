"""Motor de aquisição: detecção/conexão automática, agendamento preciso e gravação.

Threads:
  Monitor     detecta o ILLumiSense.exe, conecta ao web service, lê canais ativos,
              reconecta sozinho se o ILLumiSense for fechado/reaberto.
  Aquisição   busca os espectros na frequência configurada (agenda absoluta, sem
              deriva), entrega à gravação e à tela.
  Análise     calcula métricas para a tela (nunca bloqueia a gravação).
  Writer      (storage.TestWriter) grava em disco em ciclos.
  PowerGuard  mantém o Windows acordado com a tela desligada.

Nada aqui depende da interface gráfica: o mesmo motor roda em modo --headless.
"""
from __future__ import annotations

import collections
import math
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from . import power
from .analysis import analyze_spectrum, compare_reference
from .config import Config, app_data_dir
from .discovery import ProcessInfo, find_process, ports_from_conf, process_alive
from .illws_client import ILLWSClient, ILLWSConnectionError, ILLWSError, interpret_active_channels
from .storage import TestWriter, new_test_dir

EMPTY = np.empty(0)


@dataclass
class ChannelFrame:
    ch: int
    t_unix: float
    wav: np.ndarray = field(default_factory=lambda: EMPTY)
    pw: np.ndarray = field(default_factory=lambda: EMPTY)
    peaks_wav: np.ndarray | None = None
    peaks_pow: np.ndarray | None = None
    ok: bool = True
    error: str = ""
    dup: bool = False


@dataclass
class Sample:
    seq: int
    index: int
    t_unix: float
    t_mono: float
    frames: dict
    acq_ms: float
    recorded: bool
    burst: int | None = None


class Schedule:
    """Instantes de aquisição absolutos: contínuo (t0 + n·dt) ou rajadas
    (t0 + k·P + n·dt, com n·dt < D). Não acumula deriva."""

    def __init__(self, t0: float, rate_hz: float, mode: str = "continuous",
                 burst_duration_s: float = 10.0, burst_period_s: float = 600.0):
        self.t0 = t0
        self.dt = 1.0 / max(1e-6, float(rate_hz))
        self.burst = mode == "burst" and burst_duration_s < burst_period_s
        self.D = max(self.dt * 0.5, float(burst_duration_s))
        self.P = max(1e-3, float(burst_period_s))

    def next_tick(self, t: float) -> tuple[float, int | None]:
        t = max(t, self.t0)
        if not self.burst:
            n = math.ceil((t - self.t0) / self.dt - 1e-9)
            return self.t0 + n * self.dt, None
        k = math.floor((t - self.t0) / self.P)
        ws = self.t0 + k * self.P
        n = math.ceil((t - ws) / self.dt - 1e-9)
        if n * self.dt < self.D - 1e-9:
            return ws + n * self.dt, k + 1
        return self.t0 + (k + 1) * self.P, k + 2


class EventLog:
    def __init__(self, maxlen: int = 3000):
        self.items: collections.deque = collections.deque(maxlen=maxlen)
        self.seq = 0
        self.lock = threading.Lock()
        self.listeners = []
        self._file = None
        try:
            d = app_data_dir() / "logs"
            d.mkdir(exist_ok=True)
            self._file = open(d / f"spectralog_{datetime.now():%Y%m%d}.log", "a", encoding="utf-8", buffering=1)
        except Exception:
            self._file = None

    def add(self, level: str, msg: str) -> None:
        t = time.time()
        with self.lock:
            self.seq += 1
            self.items.append((self.seq, t, level, msg))
        if self._file:
            try:
                self._file.write(f"{datetime.fromtimestamp(t):%Y-%m-%d %H:%M:%S}\t{level}\t{msg}\n")
            except Exception:
                pass
        for fn in list(self.listeners):
            try:
                fn(t, level, msg)
            except Exception:
                pass

    def since(self, seq: int) -> list:
        with self.lock:
            return [e for e in self.items if e[0] > seq]


class Engine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.events = EventLog()
        cc = cfg["connection"]
        self.client = ILLWSClient(cc["host"], cc["port"], cc["service"], cc["timeout_s"])
        self.power = power.PowerGuard()
        self._stop = threading.Event()
        self._acq_wake = threading.Event()
        self._mon_wake = threading.Event()
        self._rec_lock = threading.RLock()
        self._hist_lock = threading.Lock()
        self._analysis_q: queue.Queue = queue.Queue(maxsize=3)
        self.threads: list[threading.Thread] = []

        # estado (leitura livre pela GUI)
        self.proc: ProcessInfo | None = None
        self.connected = False
        self.device: dict = {}
        self.channel_base = 1
        self.active_channels: list[int] | None = None
        self.port_detected: int | None = None
        self.last_conn_error = ""
        self.latency_ms = float("nan")
        self.fetch_ms = float("nan")
        self.lost_at: float | None = None

        self.writer: TestWriter | None = None
        self.recording = False
        self.test_dir: Path | None = None
        self.rec_index = 0
        self.rec_started_unix = 0.0
        self.missed_ticks = 0
        self.dup_count = 0
        self.gaps = 0
        self._rec_times: collections.deque = collections.deque(maxlen=40)
        self._schedule: Schedule | None = None
        self._next_tick = math.inf
        self._next_burst = None
        self._last_hash_rec: dict = {}
        self._last_hash_prev: dict = {}

        self.seq = 0
        self.latest: Sample | None = None
        self.latest_analysis: tuple[int, dict] = (0, {})
        self.reference: dict[int, tuple[np.ndarray, np.ndarray, float]] = {}
        self.history: collections.deque = collections.deque(maxlen=int(cfg["ui"].get("history_len", 20000)))

    # ================================================================ util
    def log(self, level: str, msg: str) -> None:
        self.events.add(level, msg)
        w = self.writer
        if w is not None:
            w.event(level, msg)

    def start(self) -> None:
        pw = self.cfg["power"]
        if pw.get("keep_awake", True):
            if self.power.start():
                self.log("OK", "Proteção de energia ativa: o Windows não entrará em suspensão "
                               "(a tela pode desligar normalmente).")
        power.set_timer_resolution_1ms()
        power.disable_power_throttling()
        if pw.get("high_priority", True):
            power.raise_priority()
        for target, name in ((self._monitor_loop, "Monitor"), (self._acq_loop, "Aquisicao"),
                             (self._analysis_loop, "Analise")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self.threads.append(t)
        self.log("INFO", "Aguardando o ILLumiSense... (detecção automática ativa)")

    def shutdown(self) -> None:
        if self.recording:
            self.stop_recording("finalizado")
        self._stop.set()
        self._acq_wake.set()
        self._mon_wake.set()
        for t in self.threads:
            t.join(timeout=5)
        self.power.stop()

    def set_keep_awake(self, on: bool) -> None:
        if on:
            self.power.start()
        else:
            self.power.stop()

    def reconnect_now(self) -> None:
        with self._rec_lock:
            self.connected = False
        self.client.reset_session()
        self._mon_wake.set()

    # ================================================================ canais
    def n_channels(self) -> int:
        n = int(self.device.get("n_canais") or 4)
        return max(1, min(4, n))

    def enabled_channels(self) -> list[int]:
        n = self.n_channels()
        follow = self.cfg["connection"].get("follow_active_channels", True)
        if follow and self.active_channels:
            return [c for c in self.active_channels if 1 <= c <= n]
        manual = self.cfg["channels"]["enabled"]
        return [i + 1 for i in range(n) if i < len(manual) and manual[i]]

    def physical(self, ch: int) -> int:
        return ch - 1 + self.channel_base

    # ================================================================ monitor
    def _effective_port(self) -> int:
        cc = self.cfg["connection"]
        if cc.get("port_from_illumisense", True) and self.port_detected:
            return self.port_detected
        return int(cc["port"])

    def _check_process(self) -> None:
        cc = self.cfg["connection"]
        if not cc.get("detect_process", True):
            self.proc = None
            return
        if self.proc and process_alive(self.proc.pid):
            return
        p = find_process(cc.get("process_name", "ILLumiSense"))
        if p and (self.proc is None or self.proc.pid != p.pid):
            self.log("INFO", f"ILLumiSense detectado em execução (PID {p.pid}"
                             f"{', ' + p.exe if p.exe else ''}).")
            if self.cfg["power"].get("protect_illumisense", True) and power.IS_WINDOWS:
                if power.disable_power_throttling(p.pid):
                    self.log("INFO", "Economia de energia (EcoQoS) desativada para o processo do ILLumiSense.")
            ports = ports_from_conf(p.folder)
            if ports:
                self.port_detected = ports[0]
                if cc.get("port_from_illumisense", True) and ports[0] != int(cc["port"]):
                    self.log("INFO", f"Porta do web service lida do niembeddedws.conf: {ports[0]}")
        elif p is None and self.proc is not None:
            self.log("AVISO", "O ILLumiSense foi fechado. O programa reconectará automaticamente quando ele "
                              "for aberto novamente.")
        self.proc = p

    def _determine_base(self, n: int) -> int:
        cb = self.cfg["connection"].get("channel_base", "auto")
        if str(cb) in ("0", "1"):
            return int(cb)

        def has(ch: int) -> bool:
            try:
                return self.client.get_spectrum(ch)[1].size > 0
            except ILLWSError:
                return False

        ok0, okn = has(0), has(n)
        if ok0 and not okn:
            self.log("INFO", f"Numeração de canais detectada: 0..{n - 1} (exibidos como CH1..CH{n}).")
            return 0
        if okn and not ok0:
            self.log("INFO", f"Numeração de canais detectada: 1..{n}.")
        return 1

    def _read_active(self, n: int, base: int) -> list[int] | None:
        try:
            raw = self.client.get_active_channels_raw()
        except (ILLWSError, ILLWSConnectionError) as exc:
            self.log("AVISO", f"Não foi possível ler os canais ativos ({exc}); usando seleção manual.")
            return None
        return interpret_active_channels(raw, n, base)

    def _try_connect(self) -> bool:
        cc = self.cfg["connection"]
        self.client.configure(host=cc["host"], port=self._effective_port(), service=cc["service"],
                              timeout=cc["timeout_s"])
        try:
            hw = self.client.get_hw_information()
        except ILLWSConnectionError as exc:
            self.last_conn_error = exc.reason
            return False
        except ILLWSError as exc:
            self.last_conn_error = f"o web service respondeu com erro: {exc}"
            return False
        info = {"tipo": hw["type"], "n_canais": hw["n_channels"] or 4, "scan_rates": hw["scan_rates"]}
        for key, fn in (("sn", self.client.get_serial_number), ("versao_sw", self.client.get_sw_version),
                        ("versao_fw", self.client.get_fw_version), ("taxa_varredura_hz", self.client.get_scan_rate)):
            try:
                info[key] = fn()
            except Exception:
                info[key] = ""
        n = max(1, min(4, int(info["n_canais"])))
        try:
            base = self._determine_base(n)
        except ILLWSConnectionError:
            return False
        active = self._read_active(n, base)
        with self._rec_lock:
            self.device = info
            self.channel_base = base
            self.active_channels = active
            self.connected = True
            self.last_conn_error = ""
            self.latency_ms = self.client.last_latency_ms
        w = self.writer
        if w is not None:  # ensaio iniciado antes da conexão: completa os dados do equipamento
            w.device = dict(info, url=self.client.base_url, numeracao_canais=base,
                            canais_gravados=self.enabled_channels(), processo=self.proc.exe if self.proc else "")
        act = ", ".join(f"CH{c}" for c in (active or [])) or "nenhum informado (seleção manual)"
        self.log("OK", f"Conectado ao ILLumiSense ({self.client.base_url}) — {info['tipo'] or 'FBG-scan'}"
                       f"{' SN ' + str(info['sn']) if info.get('sn') else ''}; canais ativos: {act}.")
        if self.lost_at is not None:
            dt = time.time() - self.lost_at
            self.lost_at = None
            if self.recording:
                self.log("LACUNA", f"Conexão restabelecida após {dt:.1f} s; gravação retomada automaticamente.")
        self._acq_wake.set()
        if self.cfg["acquisition"].get("auto_start_on_connect") and not self.recording:
            try:
                self.start_recording()
            except Exception as exc:
                self.log("ERRO", f"Início automático falhou: {exc}")
        return True

    def _refresh_light(self) -> None:
        try:
            n = self.n_channels()
            act = self._read_active(n, self.channel_base)
            if act is not None and act != self.active_channels:
                old = ", ".join(map(str, self.active_channels or [])) or "-"
                self.log("INFO", f"Canais ativos no ILLumiSense mudaram: {old} -> {', '.join(map(str, act)) or '-'}")
                self.active_channels = act
            try:
                self.device["taxa_varredura_hz"] = self.client.get_scan_rate()
            except ILLWSError:
                pass
            self.latency_ms = self.client.last_latency_ms
        except ILLWSConnectionError as exc:
            self._on_connection_lost(exc.reason)

    def _monitor_loop(self) -> None:
        last_light = 0.0
        while not self._stop.is_set():
            cc = self.cfg["connection"]
            try:
                self._check_process()
                if cc.get("auto_connect", True):
                    if not self.connected:
                        self._try_connect()
                    elif time.monotonic() - last_light > 5.0:
                        self._refresh_light()
                        last_light = time.monotonic()
            except Exception as exc:
                self.log("ERRO", f"Monitor: {exc!r}")
            self._mon_wake.wait(float(cc.get("monitor_interval_s", 2.0)))
            self._mon_wake.clear()

    def connect_once(self) -> bool:
        """Conexão manual (quando auto_connect está desligado)."""
        return self._try_connect()

    def _on_connection_lost(self, err: str) -> None:
        with self._rec_lock:
            was = self.connected
            self.connected = False
        if was:
            self.lost_at = time.time()
            self.gaps += 1 if self.recording else 0
            self.log("AVISO" if not self.recording else "LACUNA",
                     f"Conexão com o ILLumiSense perdida: {err[:160]}. Reconectando automaticamente...")
            self._mon_wake.set()

    # ================================================================ aquisição
    def _acquire(self, record: bool, burst: int | None = None) -> Sample | None:
        chans = self.enabled_channels()
        save_peaks = self.cfg["acquisition"].get("save_peaks", True)
        hashes = self._last_hash_rec if record else self._last_hash_prev
        frames: dict[int, ChannelFrame] = {}
        m0 = time.monotonic()
        t_start = time.time()
        for ch in chans:
            phys = self.physical(ch)
            tw = time.time()
            try:
                wav, pw = self.client.get_spectrum(phys)
                fr = ChannelFrame(ch, tw, wav, pw)
                if pw.size == 0:
                    fr.ok, fr.error = False, "espectro vazio (canal inativo no ILLumiSense?)"
                if save_peaks:
                    try:
                        fr.peaks_wav, fr.peaks_pow = self.client.get_peaks(phys)
                    except ILLWSError:
                        pass
                h = hash(pw.tobytes())
                fr.dup = hashes.get(ch) == h
                hashes[ch] = h
            except ILLWSConnectionError as exc:
                self._on_connection_lost(exc.reason)
                return None
            except ILLWSError as exc:
                fr = ChannelFrame(ch, tw, ok=False, error=str(exc))
            frames[ch] = fr
        acq_ms = (time.monotonic() - m0) * 1000.0
        self.fetch_ms = acq_ms if not math.isfinite(self.fetch_ms) else 0.8 * self.fetch_ms + 0.2 * acq_ms
        self.latency_ms = self.client.last_latency_ms
        self.seq += 1
        s = Sample(self.seq, 0, t_start, m0, frames, acq_ms, False, burst)
        if record and any(f.ok for f in frames.values()):
            with self._rec_lock:
                w = self.writer
                if w is not None:
                    self.rec_index += 1
                    s.index = self.rec_index
                    s.recorded = True
                    self.dup_count += sum(1 for f in frames.values() if f.ok and f.dup)
                    w.put(s)
                    self._rec_times.append(m0)
        self.latest = s
        try:
            self._analysis_q.put_nowait(s)
        except queue.Full:
            pass
        return s

    def _acq_loop(self) -> None:
        next_preview = 0.0
        while not self._stop.is_set():
            try:
                if not self.connected:
                    self._acq_wake.wait(0.5)
                    self._acq_wake.clear()
                    continue
                now = time.monotonic()
                tick = self._next_tick if self.recording else math.inf
                if now >= tick:
                    burst = self._next_burst
                    self._acquire(True, burst)
                    after = time.monotonic()
                    sch = self._schedule
                    if sch is not None:
                        expected = tick + sch.dt
                        nt, nb = sch.next_tick(max(after, expected))
                        if not sch.burst and nt > expected + 1e-6:
                            self.missed_ticks += int(round((nt - expected) / sch.dt))
                        self._next_tick, self._next_burst = nt, nb
                    continue
                prev_hz = float(self.cfg["acquisition"].get("preview_hz", 2.0))
                est = (self.fetch_ms if math.isfinite(self.fetch_ms) else 100.0) / 1000.0
                if prev_hz > 0 and now >= next_preview and (tick - now) > max(1.5 * est, 0.05):
                    s = self.latest
                    # se a gravação já atualiza a tela com frequência suficiente, não faz prévia extra
                    if not (self.recording and s is not None and now - s.t_mono < 1.0 / prev_hz):
                        self._acquire(False)
                    next_preview = time.monotonic() + 1.0 / prev_hz
                    continue
                wake = min(tick, next_preview if prev_hz > 0 else math.inf)
                timeout = max(0.0, min(wake - now, 0.5))
                if timeout > 0.002:
                    self._acq_wake.wait(timeout - 0.001)
                    self._acq_wake.clear()
                else:
                    time.sleep(0.0005)
            except Exception as exc:
                self.log("ERRO", f"Aquisição: {exc!r}")
                time.sleep(0.5)

    # ================================================================ análise
    def _analysis_loop(self) -> None:
        while not self._stop.is_set():
            try:
                s = self._analysis_q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                an = self.cfg["analysis"]
                res = {}
                for ch, fr in s.frames.items():
                    if not fr.ok:
                        continue
                    a = analyze_spectrum(fr.wav, fr.pw, an.get("power_unit", "auto"), an.get("prominence_db", 3.0),
                                         an.get("min_distance_nm", 0.3), int(an.get("max_peaks", 16)),
                                         an.get("bw_db", 10.0))
                    ref = self.reference.get(ch)
                    if ref is not None:
                        a["ref"] = compare_reference(fr.wav, fr.pw, ref[0], ref[1], a["unit"])
                    res[ch] = a
                self.latest_analysis = (s.seq, res)
                if s.recorded or not self.recording:
                    pt = {"t": s.t_unix, "rec": s.recorded, "ch": {}}
                    for ch, fr in s.frames.items():
                        if not fr.ok:
                            continue
                        a = res.get(ch, {})
                        pk = a.get("peaks", [])
                        pt["ch"][ch] = {
                            "ill": None if fr.peaks_wav is None else
                            np.asarray([w for w in fr.peaks_wav if w > 0], dtype=float),
                            "lam": np.asarray([p["lambda_max_nm"] for p in pk], dtype=float),
                            "fwhm": np.asarray([p["fwhm_nm"] for p in pk], dtype=float),
                            "pmax": np.asarray([p["P_max"] for p in pk], dtype=float),
                            "shift": a.get("ref", {}).get("shift_nm", float("nan")),
                            "corr": a.get("ref", {}).get("correlacao", float("nan")),
                        }
                    with self._hist_lock:
                        self.history.append(pt)
            except Exception as exc:
                self.log("ERRO", f"Análise: {exc!r}")

    def history_copy(self) -> list:
        with self._hist_lock:
            return list(self.history)

    # ================================================================ gravação
    def start_recording(self, name: str | None = None, resume_dir: str | None = None) -> Path:
        with self._rec_lock:
            if self.writer is not None:
                raise RuntimeError("Já existe um ensaio em gravação.")
            st = self.cfg["storage"]
            t0 = time.time()
            if resume_dir:
                d, resume = Path(resume_dir), True
            else:
                d, resume = new_test_dir(st["base_dir"], name or st["test_name"], t0), False
            device = dict(self.device)
            device.update({"url": self.client.base_url, "numeracao_canais": self.channel_base,
                           "canais_gravados": self.enabled_channels(),
                           "processo": self.proc.exe if self.proc else ""})
            snap = self.cfg.snapshot()
            snap.pop("recovery", None)
            self.writer = TestWriter(d, snap, device, self.events.add, t0, resume)
            self.rec_index = self.writer.last_sample_index
            self.rec_started_unix = self.writer.t0
            self.test_dir = d
            self.missed_ticks = self.dup_count = self.gaps = 0
            self._rec_times.clear()
            self._last_hash_rec.clear()
            with self._hist_lock:
                self.history.clear()
            self._make_schedule()
            self.recording = True
            self.cfg.set("recovery.recording_active", True)
            self.cfg.set("recovery.last_test_dir", str(d))
            try:
                self.cfg.save()
            except Exception:
                pass
        ac = self.cfg["acquisition"]
        mode = "intermitente" if ac.get("mode") == "burst" else "contínuo"
        self.log("OK", f"{'Ensaio RETOMADO' if resume else 'Gravação iniciada'}: {d} — "
                       f"{ac['rate_hz']:g} Hz, modo {mode}, canais {self.enabled_channels()}.")
        if resume:
            self.writer.event("LACUNA", "Ensaio retomado após interrupção do programa.")
        self._acq_wake.set()
        return d

    def _make_schedule(self) -> None:
        ac = self.cfg["acquisition"]
        self._schedule = Schedule(time.monotonic(), float(ac["rate_hz"]), ac.get("mode", "continuous"),
                                  float(ac.get("burst_duration_s", 10)), float(ac.get("burst_period_s", 600)))
        self._next_tick, self._next_burst = self._schedule.next_tick(time.monotonic())

    def update_schedule(self) -> None:
        """Chamar quando a frequência/modo mudar durante a gravação."""
        if self.recording:
            self._make_schedule()
            ac = self.cfg["acquisition"]
            self.log("INFO", f"Agenda de aquisição alterada: {ac['rate_hz']:g} Hz, modo "
                             f"{'intermitente' if ac.get('mode') == 'burst' else 'contínuo'}.")
            self._acq_wake.set()

    def stop_recording(self, status: str = "finalizado") -> None:
        with self._rec_lock:
            w = self.writer
            if w is None:
                return
            self.recording = False
            self.writer = None
            self._next_tick = math.inf
        self.log("INFO", "Encerrando gravação, gravando dados pendentes em disco...")
        w.close(status)
        self.cfg.set("recovery.recording_active", False)
        try:
            self.cfg.save()
        except Exception:
            pass
        self.events.add("OK", f"Gravação encerrada ({status}). {w.last_sample_index} amostras em {w.dir}")

    def effective_rate(self) -> float:
        t = list(self._rec_times)
        if len(t) < 2 or t[-1] <= t[0]:
            return float("nan")
        return (len(t) - 1) / (t[-1] - t[0])

    # ================================================================ referência
    def capture_reference(self) -> list[int]:
        s = self.latest
        if s is None:
            return []
        done = []
        for ch, fr in s.frames.items():
            if fr.ok and fr.pw.size:
                self.reference[ch] = (fr.wav.copy(), fr.pw.copy(), s.t_unix)
                done.append(ch)
                w = self.writer
                if w is not None:
                    try:
                        w.save_reference(ch, fr.wav, fr.pw, s.t_unix)
                    except Exception as exc:
                        self.log("ERRO", f"Falha ao salvar referência: {exc}")
        if done:
            self.log("INFO", f"Espectro de referência capturado: {', '.join(f'CH{c}' for c in done)}")
        return done

    def clear_reference(self) -> None:
        self.reference.clear()
        self.log("INFO", "Espectros de referência removidos.")

    # ================================================================ status
    def status(self) -> dict:
        w = self.writer
        return {
            "proc": self.proc, "connected": self.connected, "url": self.client.base_url,
            "device": dict(self.device), "active": self.active_channels, "enabled": self.enabled_channels(),
            "base": self.channel_base, "latency_ms": self.latency_ms, "fetch_ms": self.fetch_ms,
            "last_conn_error": self.last_conn_error, "recording": self.recording,
            "test_dir": str(self.test_dir) if self.test_dir else "", "rec_index": self.rec_index,
            "rec_started": self.rec_started_unix, "eff_rate": self.effective_rate(),
            "missed": self.missed_ticks, "dups": self.dup_count, "gaps": self.gaps,
            "cycle": w.cycle if w else 0, "pending": w.pending() if w else 0,
            "disk_free_gb": w.disk_free_gb if w else float("nan"),
            "write_errors": w.errors if w else 0, "power": self.power.active,
            "power_details": list(self.power.details), "next_burst": self._next_burst,
        }
