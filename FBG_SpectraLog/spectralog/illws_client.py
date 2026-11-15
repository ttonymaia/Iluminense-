"""Cliente HTTP para o web service "ILLWS" embutido no ILLumiSense (FBGS FBG-scan).

Rotas extraídas do pacote ILLWS.lvws distribuído junto com o ILLumiSense
(todas GET, resposta JSON, sem autenticação):

    /ILLWS/GetHWInformation/            -> Error, TypeHW, NumberOfChannels, ScanRates[]
    /ILLWS/GetSerialNumber/             -> Error, SN
    /ILLWS/GetSWVersion/                -> Error, SWVersion
    /ILLWS/GetFWVersion/                -> Error, FWVersion
    /ILLWS/GetScanRate/                 -> Error, ScanRate
    /ILLWS/GetActiveChannels/           -> Error, ActiveChannels[]
    /ILLWS/GetChannelSettings/:Channel  -> Error, SensitivityMode, IntegrationTime, ThresholdLevel
    /ILLWS/GetChannelStatus/:Channel    -> Error, ErrorStatus[]
    /ILLWS/GetSpectrum/:Channel         -> Error, SpecWav[], SpecPow[]      <- espectro bruto
    /ILLWS/GetSpectrumBasic/:Channel    -> Error, SpecPow[]
    /ILLWS/GetPeaks/:Channel            -> Error, PeakWavelengths[], PeakPowers[]

Este programa usa apenas rotas de LEITURA: nunca altera a configuração do ILLumiSense.
"""
from __future__ import annotations

import json
import re
import threading
import time

import numpy as np
import requests


class ILLWSError(RuntimeError):
    """O ILLumiSense respondeu, mas indicou erro (campo Error != 0) ou resposta inválida."""

    def __init__(self, msg: str, code: int | None = None):
        super().__init__(msg)
        self.code = code


class ILLWSConnectionError(ConnectionError):
    """Não foi possível falar com o web service (ILLumiSense fechado, porta errada, firewall...)."""

    def __init__(self, msg: str, reason: str = ""):
        super().__init__(msg)
        self.reason = reason or msg


def _reason(exc: Exception) -> str:
    txt = str(exc).lower()
    if isinstance(exc, requests.exceptions.Timeout) or "timed out" in txt:
        return "tempo de resposta esgotado (ILLumiSense ocupado ou travado?)"
    if "refused" in txt or "10061" in txt or "errno 111" in txt:
        return "conexão recusada (ILLumiSense fechado ou web service desativado)"
    if "reset" in txt or "aborted" in txt or "10054" in txt or "remotedisconnected" in txt:
        return "conexão interrompida pelo ILLumiSense"
    return exc.__class__.__name__


_INF_RE = re.compile(r'(?<![\w"])(-?)(?:Inf|inf|INF)(?:inity)?(?![\w"])')
_NAN_RE = re.compile(r'(?<![\w"])(?:nan|NAN)(?![\w"])')


def parse_labview_json(text: str) -> dict:
    """Interpreta o JSON do LabVIEW, tolerando NaN/Inf em formatos não padronizados."""
    try:
        obj = json.loads(text)
    except ValueError:
        fixed = _INF_RE.sub(lambda m: f"{m.group(1)}Infinity", text)
        fixed = _NAN_RE.sub("NaN", fixed)
        obj = json.loads(fixed)
    if not isinstance(obj, dict):
        raise ILLWSError(f"resposta JSON inesperada: {text[:120]!r}")
    return obj


def _field(obj: dict, name: str, default=None):
    if name in obj:
        return obj[name]
    low = name.lower()
    for k, v in obj.items():
        if k.lower() == low:
            return v
    return default


def to_float_array(values) -> np.ndarray:
    if values is None:
        return np.empty(0, dtype=float)
    if isinstance(values, (int, float)):
        return np.asarray([values], dtype=float)
    try:
        return np.asarray(values, dtype=float)
    except (TypeError, ValueError):
        out = []
        for v in values:
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                out.append(float("nan"))
        return np.asarray(out, dtype=float)


def interpret_active_channels(raw, n_channels: int | None, channel_base: int) -> list[int]:
    """Converte ActiveChannels do ILLumiSense para canais lógicos 1..N.

    O LabVIEW devolve um array de bytes que pode ser (a) um vetor de flags
    [1,0,1,1] (um elemento por canal) ou (b) a lista de números dos canais ativos.
    """
    vals = [int(round(float(v))) for v in (raw or [])]
    if not vals:
        return []
    n = n_channels or 4
    if len(vals) == n and all(v in (0, 1) for v in vals):
        return [i + 1 for i, v in enumerate(vals) if v]
    chans = sorted({v - channel_base + 1 for v in vals})
    return [c for c in chans if 1 <= c <= max(n, 4)]


class ILLWSClient:
    """Cliente thread-safe (uma sessão HTTP keep-alive por thread)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8002, service: str = "ILLWS",
                 timeout: float = 3.0):
        self.host = host
        self.port = int(port)
        self.service = service.strip("/")
        self.timeout = float(timeout)
        self._local = threading.local()
        self.last_latency_ms = float("nan")

    # ------------------------------------------------------------------ infra
    @property
    def base_url(self) -> str:
        svc = f"/{self.service}" if self.service else ""
        return f"http://{self.host}:{self.port}{svc}/"

    def configure(self, host=None, port=None, service=None, timeout=None) -> None:
        changed = False
        if host is not None and host != self.host:
            self.host, changed = host, True
        if port is not None and int(port) != self.port:
            self.port, changed = int(port), True
        if service is not None and service.strip("/") != self.service:
            self.service, changed = service.strip("/"), True
        if timeout is not None:
            self.timeout = float(timeout)
        if changed:
            self._local = threading.local()

    def _session(self) -> requests.Session:
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.trust_env = False  # nunca passar por proxy do sistema para o localhost
            adapter = requests.adapters.HTTPAdapter(pool_connections=2, pool_maxsize=4, max_retries=0)
            s.mount("http://", adapter)
            s.headers.update({"Accept": "application/json", "Connection": "keep-alive"})
            self._local.session = s
        return s

    def reset_session(self) -> None:
        s = getattr(self._local, "session", None)
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
        self._local.session = None

    def get_raw(self, route: str, timeout: float | None = None) -> tuple[int, str, float]:
        url = self.base_url + route.lstrip("/")
        t0 = time.perf_counter()
        try:
            r = self._session().get(url, timeout=timeout or self.timeout)
        except requests.exceptions.RequestException as exc:
            self.reset_session()
            raise ILLWSConnectionError(f"{url}: {exc.__class__.__name__}: {exc}", _reason(exc)) from exc
        dt = (time.perf_counter() - t0) * 1000.0
        self.last_latency_ms = dt
        return r.status_code, r.text, dt

    def call(self, route: str, timeout: float | None = None) -> dict:
        status, text, _ = self.get_raw(route, timeout)
        if status != 200:
            raise ILLWSError(f"HTTP {status} em {route}: {text[:160]!r}", code=status)
        obj = parse_labview_json(text)
        err = _field(obj, "Error", 0)
        try:
            err = int(err)
        except (TypeError, ValueError):
            err = 0 if not err else -1
        if err != 0:
            raise ILLWSError(f"ILLumiSense retornou Error={err} em {route}", code=err)
        return obj

    # ------------------------------------------------------------ informações
    def get_hw_information(self) -> dict:
        o = self.call("GetHWInformation/")
        return {
            "type": str(_field(o, "TypeHW", "") or ""),
            "n_channels": int(_field(o, "NumberOfChannels", 0) or 0),
            "scan_rates": [int(x) for x in (_field(o, "ScanRates", []) or [])],
        }

    def get_serial_number(self) -> str:
        return str(_field(self.call("GetSerialNumber/"), "SN", ""))

    def get_sw_version(self) -> str:
        return str(_field(self.call("GetSWVersion/"), "SWVersion", ""))

    def get_fw_version(self) -> str:
        return str(_field(self.call("GetFWVersion/"), "FWVersion", ""))

    def get_scan_rate(self) -> float:
        return float(_field(self.call("GetScanRate/"), "ScanRate", float("nan")))

    def get_active_channels_raw(self) -> list:
        return list(_field(self.call("GetActiveChannels/"), "ActiveChannels", []) or [])

    def get_channel_settings(self, ch: int) -> dict:
        o = self.call(f"GetChannelSettings/{int(ch)}")
        return {
            "sensitivity_mode": _field(o, "SensitivityMode"),
            "integration_time": _field(o, "IntegrationTime"),
            "threshold_level": _field(o, "ThresholdLevel"),
        }

    def get_channel_status(self, ch: int) -> list:
        return list(_field(self.call(f"GetChannelStatus/{int(ch)}"), "ErrorStatus", []) or [])

    # ------------------------------------------------------------------ dados
    def get_spectrum(self, ch: int) -> tuple[np.ndarray, np.ndarray]:
        o = self.call(f"GetSpectrum/{int(ch)}")
        wav = to_float_array(_field(o, "SpecWav"))
        pw = to_float_array(_field(o, "SpecPow"))
        if wav.size != pw.size:
            n = min(wav.size, pw.size)
            wav, pw = wav[:n], pw[:n]
        return wav, pw

    def get_spectrum_basic(self, ch: int) -> np.ndarray:
        return to_float_array(_field(self.call(f"GetSpectrumBasic/{int(ch)}"), "SpecPow"))

    def get_peaks(self, ch: int) -> tuple[np.ndarray, np.ndarray]:
        o = self.call(f"GetPeaks/{int(ch)}")
        return to_float_array(_field(o, "PeakWavelengths")), to_float_array(_field(o, "PeakPowers"))

    def ping(self) -> float:
        self.get_hw_information()
        return self.last_latency_ms
