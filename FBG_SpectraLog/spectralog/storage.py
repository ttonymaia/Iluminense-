"""Gravação robusta do ensaio em ciclos (rotação de arquivos), append-only, com fsync.

Estrutura de um ensaio:

    <pasta_base>/<AAAA-MM-DD_HHMMSS>_<nome>/
        ensaio_info.json          metadados (equipamento, configuração, início/fim, status)
        eventos.log               conexões, quedas, rotações de ciclo, avisos
        resumo_ciclos.csv         um registro por ciclo fechado
        referencias/              espectros de referência capturados
        ciclo_0001/
            amostras.csv          amostra, data/hora legível, t_unix, t_ensaio, rajada ...
            CH1_potencia.csv      amostra,t_unix,t_ensaio_s,rajada,repetido,P0001..PNNNN
            CH1_lambda.csv        amostra,t_unix,t_ensaio_s,rajada,L0001..LNNNN  (nm)
            picos.csv             picos calculados pelo ILLumiSense (GetPeaks)
            metricas.csv          métricas espectrais calculadas pelo FBG SpectraLog
        ciclo_0002/ ...

Os CSV de espectro são puramente numéricos (uma linha de cabeçalho), o que permite
ler diretamente com numpy.loadtxt(..., skiprows=1), pandas.read_csv ou MATLAB readmatrix.
Uma queda de energia perde no máximo o intervalo de flush (padrão 2 s).
"""
from __future__ import annotations

import gzip
import io
import json
import os
import queue
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from . import __version__
from .analysis import analyze_spectrum


def safe_name(name: str) -> str:
    name = re.sub(r"[^\w\-. ]+", "_", name.strip(), flags=re.U).strip(" .")
    return name[:80] or "ensaio"


def fmt_dt(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


class CsvFormat:
    def __init__(self, kind: str = "intl"):
        self.kind = kind
        self.sep = ";" if kind == "br" else ","

    def num(self, values, fmt: str) -> str:
        s = self.sep.join([fmt % v for v in values])
        return s.replace(".", ",") if self.kind == "br" else s

    def f(self, value: float, fmt: str = "%.3f") -> str:
        s = fmt % value
        return s.replace(".", ",") if self.kind == "br" else s

    def row(self, items) -> str:
        """Floats são formatados com até 8 algarismos significativos; strings vão como estão."""
        out = []
        for it in items:
            if isinstance(it, float):
                out.append(self.f(it, "%.10g") if np.isfinite(it) else "nan")
            else:
                out.append(str(it))
        return self.sep.join(out)


class _File:
    def __init__(self, path: Path, header: str):
        new = not path.exists() or path.stat().st_size == 0
        self.path = path
        self.f = open(path, "a", encoding="utf-8", newline="\n", buffering=1 << 16)
        if new:
            self.f.write(header + "\n")
        self.dirty = True

    def write(self, line: str) -> None:
        self.f.write(line + "\n")
        self.dirty = True

    def sync(self) -> None:
        if self.dirty:
            self.f.flush()
            os.fsync(self.f.fileno())
            self.dirty = False

    def close(self) -> None:
        try:
            self.sync()
        finally:
            self.f.close()


class TestWriter:
    """Thread de gravação. O motor de aquisição apenas enfileira amostras (nunca bloqueia)."""

    __test__ = False  # (não é um teste do pytest; "Test" = ensaio)

    def __init__(self, test_dir: Path, cfg: dict, device: dict, log_event, t0_unix: float,
                 resume: bool = False):
        self.dir = Path(test_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cfg = cfg
        self.st = cfg["storage"]
        self.an = cfg["analysis"]
        self.device = device
        self.log_event = log_event
        self.t0 = t0_unix
        self.fmt = CsvFormat(self.st.get("csv_format", "intl"))
        self.q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.cycle = 0
        self.cycle_dir: Path | None = None
        self.cycle_start = 0.0
        self.cycle_samples = 0
        self.cycle_burst = None
        self.cycle_first_sample = None
        self.files: dict[str, _File] = {}
        self.npts: dict[int, int] = {}
        self.last_lambda: dict[int, np.ndarray] = {}
        self.samples_written = 0
        self.last_sample_index = 0
        self.bytes_hint = 0
        self.errors = 0
        self.last_error = ""
        self.disk_free_gb = float("nan")
        self._last_sync = time.monotonic()
        self._last_disk = 0.0
        self._compress_q: queue.Queue = queue.Queue()
        self.info_path = self.dir / "ensaio_info.json"
        self.events_path = self.dir / "eventos.log"
        self._events = open(self.events_path, "a", encoding="utf-8", buffering=1)
        if resume:
            self._prepare_resume()
        self._write_info(status="gravando", first=not resume)
        self.thread = threading.Thread(target=self._run, name="Writer", daemon=True)
        self.thread.start()
        if self.st.get("compress_closed"):
            threading.Thread(target=self._compress_loop, name="Compressor", daemon=True).start()

    # ------------------------------------------------------------- metadados
    def _write_info(self, status: str, first: bool = False, extra: dict | None = None) -> None:
        info = {}
        if self.info_path.exists():
            try:
                info = json.loads(self.info_path.read_text(encoding="utf-8"))
            except Exception:
                info = {}
        if first or "inicio_unix" not in info:
            info.update({
                "programa": f"FBG SpectraLog {__version__}",
                "inicio": fmt_dt(self.t0), "inicio_unix": self.t0,
                "equipamento": self.device,
                "configuracao": self.cfg,
                "formato": {
                    "csv": "separador ';' decimal ','" if self.fmt.kind == "br" else "separador ',' decimal '.'",
                    "espectro_potencia": "CHn_potencia.csv: amostra,t_unix,t_ensaio_s,rajada,repetido,P0001..PNNNN",
                    "espectro_lambda": "CHn_lambda.csv: amostra,t_unix,t_ensaio_s,rajada,L0001..LNNNN (nm)",
                    "eixo_lambda": self.st.get("save_wavelength", "always"),
                    "tempo": "t_unix = segundos desde 1970-01-01 UTC; t_ensaio_s = segundos desde o início",
                },
            })
        if self.device and self.device.get("tipo"):
            info["equipamento"] = self.device
        info["status"] = status
        info["atualizado"] = fmt_dt(time.time())
        info["amostras_gravadas"] = self.last_sample_index
        info["ciclos"] = self.cycle
        if extra:
            info.update(extra)
        tmp = self.info_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(info, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, self.info_path)

    def event(self, level: str, msg: str, t: float | None = None) -> None:
        try:
            with self._lock:
                self._events.write(f"{fmt_dt(t or time.time())}\t{level}\t{msg}\n")
                self._events.flush()
        except Exception:
            pass

    def _prepare_resume(self) -> None:
        cycles = sorted(int(m.group(1)) for p in self.dir.glob("ciclo_*")
                        if (m := re.match(r"ciclo_(\d+)$", p.name)))
        self.cycle = cycles[-1] if cycles else 0
        last = 0
        for c in reversed(cycles):
            path = self.dir / f"ciclo_{c:04d}" / "amostras.csv"
            if path.exists():
                last = _last_sample_index(path, self.fmt.sep)
                if last:
                    break
        self.last_sample_index = last
        try:
            info = json.loads(self.info_path.read_text(encoding="utf-8"))
            self.t0 = float(info.get("inicio_unix", self.t0))
        except Exception:
            pass

    # ------------------------------------------------------------- ciclos
    def _open_cycle(self, t: float, burst) -> None:
        self._close_cycle(t)
        self.cycle += 1
        self.cycle_dir = self.dir / f"ciclo_{self.cycle:04d}"
        self.cycle_dir.mkdir(parents=True, exist_ok=True)
        self.cycle_start = t
        self.cycle_samples = 0
        self.cycle_burst = burst
        self.cycle_first_sample = None
        self.last_lambda.clear()
        sep = self.fmt.sep
        self.files["amostras"] = _File(self.cycle_dir / "amostras.csv", sep.join(
            ["amostra", "data_hora", "t_unix", "t_ensaio_s", "rajada", "canais_ok", "duracao_aquisicao_ms"]))
        if self.cfg["acquisition"].get("save_peaks", True):
            self.files["picos"] = _File(self.cycle_dir / "picos.csv", sep.join(
                ["amostra", "t_unix", "t_ensaio_s", "canal", "fbg", "lambda_nm", "potencia"]))
        if self.st.get("save_metrics", True):
            self.files["metricas"] = _File(self.cycle_dir / "metricas.csv", sep.join(
                ["amostra", "t_unix", "t_ensaio_s", "canal", "pico", "lambda_max_nm", "lambda_centroide_nm",
                 "P_max", "fwhm_nm", f"bw_{self.an.get('bw_db', 10):g}dB_nm", "assimetria", "skewness",
                 "sub_picos", "n_picos_canal", "unidade"]))
        self.event("INFO", f"Ciclo {self.cycle:04d} aberto ({self.cycle_dir.name})", t)
        self.log_event("INFO", f"Gravação: ciclo {self.cycle:04d} iniciado")
        self._write_info("gravando")

    def _close_cycle(self, t: float) -> None:
        if not self.files:
            return
        for f in list(self.files.values()):
            try:
                f.close()
            except Exception as exc:
                self._error(f"erro ao fechar {f.path.name}: {exc}")
        self.files.clear()
        self.npts.clear()
        resumo = self.dir / "resumo_ciclos.csv"
        new = not resumo.exists()
        with open(resumo, "a", encoding="utf-8", newline="\n") as f:
            sep = self.fmt.sep
            if new:
                f.write(sep.join(["ciclo", "inicio", "fim", "amostras", "primeira_amostra", "pasta"]) + "\n")
            f.write(sep.join([str(self.cycle), fmt_dt(self.cycle_start), fmt_dt(t), str(self.cycle_samples),
                              str(self.cycle_first_sample or ""), self.cycle_dir.name]) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.event("INFO", f"Ciclo {self.cycle:04d} fechado com {self.cycle_samples} amostras", t)
        if self.st.get("compress_closed") and self.cycle_dir is not None:
            self._compress_q.put(self.cycle_dir)

    def _need_rotation(self, sample) -> bool:
        if not self.files:
            return True
        mode = self.st.get("cycle_mode", "time")
        if mode == "samples":
            return self.cycle_samples >= max(1, int(self.st.get("cycle_samples", 3600)))
        if mode == "burst":
            return sample.burst is not None and sample.burst != self.cycle_burst
        return sample.t_unix - self.cycle_start >= max(1.0, float(self.st.get("cycle_minutes", 60)) * 60.0)

    # ------------------------------------------------------------- escrita
    def _channel_file(self, ch: int, kind: str, n: int) -> _File:
        key = f"{kind}{ch}"
        f = self.files.get(key)
        if f is None:
            sep = self.fmt.sep
            if kind == "P":
                head = ["amostra", "t_unix", "t_ensaio_s", "rajada", "repetido"] + [f"P{i + 1:04d}" for i in range(n)]
                name = f"CH{ch}_potencia.csv"
            else:
                head = ["amostra", "t_unix", "t_ensaio_s", "rajada"] + [f"L{i + 1:04d}" for i in range(n)]
                name = f"CH{ch}_lambda.csv"
            f = _File(self.cycle_dir / name, sep.join(head))
            self.files[key] = f
        return f

    def _write_sample(self, s) -> None:
        if self.cycle_dir is None or self._need_rotation(s):
            self._open_cycle(s.t_unix, s.burst)
        # se o nº de pontos do espectro mudar, abre novo ciclo para manter colunas consistentes
        for ch, fr in s.frames.items():
            if fr.ok and ch in self.npts and self.npts[ch] != fr.pw.size:
                self.event("AVISO", f"CH{ch}: nº de pontos mudou {self.npts[ch]} -> {fr.pw.size}; novo ciclo")
                self._open_cycle(s.t_unix, s.burst)
                break
        F = self.fmt
        burst = "" if s.burst is None else str(s.burst)
        ok_ch = [ch for ch, fr in s.frames.items() if fr.ok]
        self.files["amostras"].write(F.row([s.index, fmt_dt(s.t_unix), F.f(s.t_unix),
                                            F.f(s.t_unix - self.t0), burst,
                                            "+".join(map(str, ok_ch)), F.f(s.acq_ms, "%.1f")]))
        save_lambda = self.st.get("save_wavelength", "always")
        skip_dup = self.cfg["acquisition"].get("skip_duplicates", False)
        for ch in sorted(s.frames):
            fr = s.frames[ch]
            if not fr.ok or fr.pw.size == 0:
                continue
            if skip_dup and fr.dup:
                continue
            n = fr.pw.size
            self.npts[ch] = n
            tr = F.f(fr.t_unix)
            te = F.f(fr.t_unix - self.t0)
            meta = F.sep.join([str(s.index), tr, te, burst])
            self._channel_file(ch, "P", n).write(meta + F.sep + ("1" if fr.dup else "0") + F.sep +
                                                 F.num(fr.pw.tolist(), "%.8g"))
            prev = self.last_lambda.get(ch)
            if save_lambda == "always" or prev is None or prev.size != fr.wav.size or \
                    not np.array_equal(prev, fr.wav):
                self._channel_file(ch, "L", n).write(meta + F.sep + F.num(fr.wav.tolist(), "%.10g"))
                self.last_lambda[ch] = fr.wav.copy()
            if "picos" in self.files and fr.peaks_wav is not None:
                for k, (lw, lp) in enumerate(zip(fr.peaks_wav.tolist(), fr.peaks_pow.tolist()), 1):
                    if lw == 0 and lp == 0:
                        continue
                    self.files["picos"].write(F.row([s.index, tr, te, ch, k, float(lw), float(lp)]))
            if "metricas" in self.files:
                try:
                    a = analyze_spectrum(fr.wav, fr.pw, self.an.get("power_unit", "auto"),
                                         self.an.get("prominence_db", 3.0), self.an.get("min_distance_nm", 0.3),
                                         int(self.an.get("max_peaks", 16)), self.an.get("bw_db", 10.0))
                    for k, p in enumerate(a["peaks"], 1):
                        self.files["metricas"].write(F.row([
                            s.index, tr, te, ch, k, p["lambda_max_nm"], p["lambda_centroide_nm"], p["P_max"],
                            p["fwhm_nm"], p["bw_x_nm"], p["assimetria"], p["skewness"], p["sub_picos"],
                            a["n_peaks"], a["unit"]]))
                except Exception as exc:
                    self._error(f"métricas CH{ch}: {exc}")
        if self.cycle_first_sample is None:
            self.cycle_first_sample = s.index
        self.cycle_samples += 1
        self.samples_written += 1
        self.last_sample_index = s.index

    def _error(self, msg: str) -> None:
        self.errors += 1
        self.last_error = msg
        self.event("ERRO", msg)
        self.log_event("ERRO", msg)

    def _sync_all(self) -> None:
        for f in list(self.files.values()):
            f.sync()

    def _check_disk(self) -> None:
        try:
            self.disk_free_gb = shutil.disk_usage(self.dir).free / 1e9
            if self.disk_free_gb < float(self.st.get("min_free_gb", 2.0)):
                self.log_event("AVISO", f"Pouco espaço em disco: {self.disk_free_gb:.1f} GB livres")
        except Exception:
            pass

    def _run(self) -> None:
        pending = None
        self._check_disk()
        while True:
            try:
                if pending is None:
                    try:
                        pending = self.q.get(timeout=0.25)
                    except queue.Empty:
                        pending = None
                if pending is not None:
                    if pending == "STOP":
                        break
                    self._write_sample(pending)
                    pending = None
                now = time.monotonic()
                if now - self._last_sync >= float(self.st.get("flush_interval_s", 2.0)):
                    self._sync_all()
                    self._last_sync = now
                if now - self._last_disk >= 30.0:
                    self._check_disk()
                    self._last_disk = now
                    self._write_info("gravando")
            except Exception as exc:
                # erro de E/S (disco cheio, pen drive removido...): mantém a amostra e tenta de novo
                self._error(f"falha de gravação: {exc} — nova tentativa em 2 s (dados mantidos em memória)")
                time.sleep(2.0)
                try:
                    for f in list(self.files.values()):
                        try:
                            f.f.close()
                        except Exception:
                            pass
                    self.files.clear()
                    self.npts.clear()
                    self.cycle_dir = None
                except Exception:
                    pass
        # encerramento limpo
        try:
            self._close_cycle(time.time())
        except Exception as exc:
            self._error(f"erro ao fechar ciclo: {exc}")

    # ------------------------------------------------------------- API
    def put(self, sample) -> None:
        self.q.put(sample)

    def pending(self) -> int:
        return self.q.qsize()

    def save_reference(self, ch: int, wav: np.ndarray, pw: np.ndarray, t: float) -> Path:
        d = self.dir / "referencias"
        d.mkdir(exist_ok=True)
        path = d / f"referencia_CH{ch}_{datetime.fromtimestamp(t).strftime('%Y%m%d_%H%M%S')}.csv"
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(self.fmt.sep.join(["lambda_nm", "potencia"]) + "\n")
            for a, b in zip(wav.tolist(), pw.tolist()):
                f.write(self.fmt.num([a], "%.10g") + self.fmt.sep + self.fmt.num([b], "%.8g") + "\n")
        self.event("INFO", f"Espectro de referência CH{ch} salvo em {path.name}", t)
        return path

    def close(self, status: str = "finalizado") -> None:
        self.q.put("STOP")
        self.thread.join(timeout=120)
        self._stop.set()
        self._write_info(status, extra={"fim": fmt_dt(time.time()), "fim_unix": time.time()})
        self.event("INFO", f"Ensaio {status}. Amostras: {self.last_sample_index}")
        try:
            self._events.close()
        except Exception:
            pass

    # ------------------------------------------------------------- compressão
    def _compress_loop(self) -> None:
        while not self._stop.is_set() or not self._compress_q.empty():
            try:
                d = self._compress_q.get(timeout=1.0)
            except queue.Empty:
                continue
            for p in sorted(Path(d).glob("*.csv")):
                try:
                    gz = p.with_suffix(".csv.gz")
                    with open(p, "rb") as src, gzip.open(gz, "wb", compresslevel=6) as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
                    with gzip.open(gz, "rb") as chk:  # verifica integridade antes de apagar
                        n = 0
                        while (b := chk.read(1 << 20)):
                            n += len(b)
                    if n == p.stat().st_size:
                        p.unlink()
                    else:
                        gz.unlink()
                except Exception as exc:
                    self.event("ERRO", f"compressão de {p.name} falhou: {exc}")


def _last_sample_index(path: Path, sep: str) -> int:
    try:
        with open(path, "rb") as f:
            f.seek(0, io.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 4096))
            lines = f.read().decode("utf-8", "ignore").strip().splitlines()
        for line in reversed(lines):
            first = line.split(sep)[0]
            if first.isdigit():
                return int(first)
    except Exception:
        pass
    return 0


def new_test_dir(base_dir: str, name: str, t: float) -> Path:
    stamp = datetime.fromtimestamp(t).strftime("%Y-%m-%d_%H%M%S")
    d = Path(base_dir) / f"{stamp}_{safe_name(name)}"
    d.mkdir(parents=True, exist_ok=False)
    return d
