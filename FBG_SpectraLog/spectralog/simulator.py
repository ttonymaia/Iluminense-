"""Simulador do web service ILLWS do ILLumiSense (mesmas rotas e mesmo JSON).

Serve para testar o FBG SpectraLog sem o interrogador. Modela:
  CH1  três FBG uniformes sob carregamento cíclico (deformação senoidal)
  CH2  uma FBG chirped (~4 nm) com dano progressivo (vale central + alargamento)
  CH3  duas FBG, a segunda sofrendo divisão de pico (carga transversal/birrefringência)
  CH4  uma FBG com deriva térmica (inativa por padrão, como no ILLumiSense)

Uso:  python -m spectralog --simulador [--porta 8002]
"""
from __future__ import annotations

import json
import math
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np


class SimDevice:
    def __init__(self, n_points: int = 1024, scan_rate: float = 20.0, base: int = 1,
                 active=(1, 1, 1, 0), damage_minutes: float = 10.0, active_format: str = "flags",
                 drifting_axis: bool = False, seed: int = 1):
        self.n = n_points
        self.scan_rate = scan_rate
        self.base = base
        self.active = list(active)
        self.damage_s = damage_minutes * 60.0
        self.active_format = active_format
        self.drifting_axis = drifting_axis
        self.t0 = time.time()
        self.rng = np.random.default_rng(seed)
        self.wav0 = np.linspace(1510.0, 1590.0, n_points)
        self._cache: dict = {}
        self._lock = threading.Lock()

    # --------------------------------------------------------------- modelo
    def _frame(self) -> int:
        return int((time.time() - self.t0) * self.scan_rate)

    def _state(self, k: int):
        t = k / self.scan_rate
        strain = 600e-6 * math.sin(2 * math.pi * t / 20.0)          # carregamento cíclico, período 20 s
        dmg = min(1.0, t / self.damage_s) if self.damage_s > 0 else 0.0
        temp = 3.0 * math.sin(2 * math.pi * t / 300.0)              # ±3 °C, período 5 min
        return t, strain, dmg, temp

    @staticmethod
    def _gauss(w, c, fwhm, amp):
        s = fwhm / 2.3548
        return amp * np.exp(-0.5 * ((w - c) / s) ** 2)

    def _lin_spectrum(self, ch: int, w: np.ndarray, k: int):
        t, eps, dmg, temp = self._state(k)
        lin = np.zeros_like(w)
        peaks = []
        if ch == 1:
            for c0, a in ((1530.0, 1.0), (1545.0, 0.8), (1560.0, 0.9)):
                c = c0 * (1 + 0.78 * eps)
                lin += self._gauss(w, c, 0.22, a * 3.2e-3)
                peaks.append(c)
        elif ch == 2:
            c, bw = 1550.0 * (1 + 0.78 * eps * 0.5), 4.0 * (1 + 0.35 * dmg)
            edge = 0.18
            x = (w - c) / (bw / 2)
            top = 1 / (1 + np.exp((np.abs(x) - 1) / (edge / (bw / 2))))
            ripple = 1 + 0.08 * np.sin(2 * np.pi * (w - c) / 0.35)
            notch = 1 - 0.75 * dmg * np.exp(-0.5 * ((w - c - 0.4) / (0.25 + 0.3 * dmg)) ** 2)
            tilt = 1 + 0.25 * dmg * x
            lin += 1.6e-3 * top * ripple * notch * np.clip(tilt, 0.2, None)
            peaks.append(c)
        elif ch == 3:
            c1 = 1535.0 * (1 + 0.78 * eps * 0.3)
            lin += self._gauss(w, c1, 0.25, 2.5e-3)
            c2 = 1570.0 * (1 + 0.78 * eps * 0.3)
            split = 0.45 * dmg
            lin += self._gauss(w, c2 - split / 2, 0.24, 2.2e-3 * (1 - 0.3 * dmg))
            lin += self._gauss(w, c2 + split / 2, 0.24, 2.2e-3 * (0.4 + 0.3 * dmg)) * (dmg > 0.02)
            peaks += [c1, c2 - split / 2] + ([c2 + split / 2] if dmg > 0.15 else [])
        else:
            c = 1555.0 + 0.0102 * temp      # ~10 pm/°C
            lin += self._gauss(w, c, 0.2, 3.0e-3)
            peaks.append(c)
        return lin, peaks

    def spectrum(self, ch: int):
        k = self._frame()
        key = (ch, k)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            w = self.wav0.copy()
            if self.drifting_axis:
                w = w + 0.002 * math.sin(k / 50.0)
            lin, peaks = self._lin_spectrum(ch, w, k)
            rng = np.random.default_rng(hash(key) & 0xFFFFFFFF)
            floor = 10 ** (-62 / 10) * (1 + 0.2 * np.sin(w / 7.0))
            db = 10 * np.log10(lin + floor) + rng.normal(0, 0.25, w.size)
            pk_pow = [float(np.interp(p, w, db)) for p in peaks]
            res = (w, db, np.array(peaks), np.array(pk_pow))
            if len(self._cache) > 64:
                self._cache.clear()
            self._cache[key] = res
            return res

    def logical(self, phys: int) -> int | None:
        ch = phys - self.base + 1
        return ch if 1 <= ch <= 4 else None


def _num_list(a) -> str:
    return "[" + ",".join("%.6f" % v for v in np.asarray(a, dtype=float)) + "]"


class _Handler(BaseHTTPRequestHandler):
    server_version = "LabVIEW/21.0.1 (simulador)"
    protocol_version = "HTTP/1.1"
    dev: SimDevice = None  # type: ignore
    conns: set = None  # type: ignore

    def setup(self):
        super().setup()
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        self.conns.add(self.connection)

    def finish(self):
        try:
            super().finish()
        finally:
            self.conns.discard(self.connection)

    def log_message(self, fmt, *args):  # silencioso
        pass

    def _send(self, body: str, code: int = 200):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        # cabeçalho + corpo num único write (evita atraso Nagle/delayed-ACK)
        self._headers_buffer.append(b"\r\n")
        self._headers_buffer.append(data)
        self.flush_headers()

    def do_GET(self):  # noqa: N802
        d = self.dev
        path = self.path.split("?")[0]
        m = re.match(r"^/ILLWS/([A-Za-z]+)/?(.*)$", path)
        if not m:
            return self._send('{"error":"not found"}', 404)
        route, rest = m.group(1), [p for p in m.group(2).split("/") if p]
        try:
            if route == "GetHWInformation":
                return self._send('{"Error":0,"TypeHW":"FBG-scan 804D (SIMULADO)","NumberOfChannels":4,'
                                  '"ScanRates":[1,2,5,10,20,50,100]}')
            if route == "GetSerialNumber":
                return self._send('{"Error":0,"SN":"SIM-0000"}')
            if route == "GetSWVersion":
                return self._send('{"Error":0,"SWVersion":"ILLumiSense simulado 1.0"}')
            if route == "GetFWVersion":
                return self._send('{"Error":0,"FWVersion":"sim"}')
            if route == "GetScanRate":
                return self._send(json.dumps({"Error": 0, "ScanRate": int(d.scan_rate)}))
            if route == "GetActiveChannels":
                if d.active_format == "flags":
                    arr = d.active
                else:
                    arr = [i + d.base for i, a in enumerate(d.active) if a]
                return self._send(json.dumps({"Error": 0, "ActiveChannels": arr}))
            if route == "EnableChannel" and len(rest) == 2:
                ch = d.logical(int(rest[1]))
                if ch:
                    d.active[ch - 1] = 1 if int(rest[0]) else 0
                return self._send('{"Error":0,"Peaks":0}')
            ch = d.logical(int(rest[-1])) if rest else None
            if route in ("GetSpectrum", "GetSpectrumBasic", "GetPeaks", "GetChannelSettings", "GetChannelStatus"):
                if ch is None:
                    return self._send('{"Error":1,"SpecWav":[],"SpecPow":[]}')
                if route == "GetChannelSettings":
                    return self._send('{"Error":0,"SensitivityMode":0,"IntegrationTime":100,"ThresholdLevel":-45.0}')
                if route == "GetChannelStatus":
                    return self._send('{"Error":0,"ErrorStatus":[0]}')
                if not d.active[ch - 1]:
                    if route == "GetPeaks":
                        return self._send('{"Error":0,"PeakWavelengths":[],"PeakPowers":[]}')
                    return self._send('{"Error":0,"SpecWav":[],"SpecPow":[]}')
                w, p, pkw, pkp = d.spectrum(ch)
                if route == "GetSpectrum":
                    return self._send('{"Error":0,"SpecWav":%s,"SpecPow":%s}' % (_num_list(w), _num_list(p)))
                if route == "GetSpectrumBasic":
                    return self._send('{"Error":0,"SpecPow":%s}' % _num_list(p))
                return self._send('{"Error":0,"PeakWavelengths":%s,"PeakPowers":%s}' % (_num_list(pkw), _num_list(pkp)))
            return self._send('{"Error":0}')
        except Exception as exc:  # pragma: no cover
            return self._send(json.dumps({"Error": 99, "msg": str(exc)}), 500)


class Simulator:
    def __init__(self, port: int = 8002, host: str = "127.0.0.1", **dev_kwargs):
        self.port = port
        self.host = host
        self.dev = SimDevice(**dev_kwargs)
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.conns: set = set()

    def start(self) -> "Simulator":
        handler = type("Handler", (_Handler,), {"dev": self.dev, "conns": self.conns})
        ThreadingHTTPServer.allow_reuse_address = True
        self.httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.2},
                                       name="Simulador", daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        """Para o servidor e derruba as conexões abertas (como o fechamento do ILLumiSense)."""
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        for c in list(self.conns):
            try:
                c.shutdown(socket.SHUT_RDWR)
                c.close()
            except OSError:
                pass
        self.conns.clear()

    @property
    def running(self) -> bool:
        return self.httpd is not None


def run_forever(port: int = 8002, outage_every: float = 0, outage_for: float = 0, **kw) -> None:
    sim = Simulator(port=port, **kw).start()
    print(f"Simulador ILLumiSense em http://127.0.0.1:{sim.port}/ILLWS/  (Ctrl+C para sair)")
    try:
        while True:
            if outage_every > 0:
                time.sleep(outage_every)
                print("  [simulando queda do ILLumiSense]")
                sim.stop()
                time.sleep(outage_for)
                sim = Simulator(port=port, **kw).start()
                print("  [ILLumiSense de volta]")
            else:
                time.sleep(3600)
    except KeyboardInterrupt:
        sim.stop()
