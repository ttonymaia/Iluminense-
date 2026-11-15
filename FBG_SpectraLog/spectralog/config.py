"""Configuração persistente (JSON) do FBG SpectraLog."""
from __future__ import annotations

import copy
import json
import os
import sys
import threading
from pathlib import Path


def app_data_dir() -> Path:
    """Pasta de configuração/logs do usuário (%APPDATA%\\FBG_SpectraLog no Windows)."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    p = base / "FBG_SpectraLog"
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_data_dir() -> str:
    docs = Path.home() / "Documents"
    if not docs.exists():
        docs = Path.home()
    return str(docs / "FBG_SpectraLog" / "Ensaios")


DEFAULTS: dict = {
    "connection": {
        "host": "127.0.0.1",
        "port": 8002,              # porta do servidor web embutido do ILLumiSense (niembeddedws.conf)
        "port_from_illumisense": True,  # lê a porta do niembeddedws.conf da pasta do ILLumiSense
        "service": "ILLWS",        # nome do web service LabVIEW do ILLumiSense
        "channel_base": "auto",    # "auto", 1 (canais 1..4) ou 0 (canais 0..3)
        "timeout_s": 3.0,
        "auto_connect": True,
        "detect_process": True,
        "process_name": "ILLumiSense",
        "follow_active_channels": True,
        "monitor_interval_s": 2.0,
    },
    "channels": {
        "enabled": [True, True, True, True],
        "names": ["CH1", "CH2", "CH3", "CH4"],
    },
    "acquisition": {
        "rate_hz": 1.0,
        "rate_unit": "Hz",          # "Hz", "s" ou "min" (apenas para exibição)
        "mode": "continuous",       # "continuous" ou "burst"
        "burst_duration_s": 10.0,
        "burst_period_s": 600.0,
        "save_peaks": True,         # também grava os picos (GetPeaks) do ILLumiSense
        "skip_duplicates": False,   # não grava espectros idênticos ao anterior
        "auto_resume": True,        # continua gravando ao reconectar
        "auto_start_on_connect": False,
        "preview_hz": 2.0,          # atualização da tela quando não está gravando
    },
    "storage": {
        "base_dir": default_data_dir(),
        "test_name": "ensaio_FBG",
        "cycle_mode": "time",       # "time", "samples" ou "burst"
        "cycle_minutes": 60.0,
        "cycle_samples": 3600,
        "flush_interval_s": 2.0,
        "save_wavelength": "always",  # "always" ou "on_change"
        "compress_closed": False,
        "save_metrics": True,
        "csv_format": "intl",       # "intl" (, e .) ou "br" (; e ,)
        "min_free_gb": 2.0,
    },
    "analysis": {
        "power_unit": "auto",       # "auto", "dB" ou "linear"
        "bw_db": 10.0,              # largura de banda adicional a -X dB (além da FWHM -3 dB)
        "prominence_db": 3.0,
        "min_distance_nm": 0.3,
        "max_peaks": 16,
    },
    "power": {
        "keep_awake": True,
        "protect_illumisense": True,
        "high_priority": True,
    },
    "recovery": {
        "auto_resume_after_crash": True,
        "resume_countdown_s": 60,
        "recording_active": False,
        "last_test_dir": "",
    },
    "ui": {
        "theme": "light",
        "plot_fps": 10,
        "layout": "overlay",       # "overlay" ou "grid"
        "waterfall_len": 600,
        "history_len": 20000,
        "language": "pt",
    },
}


def _merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v
    return dst


class Config:
    """Configuração em dicionário aninhado, com leitura/gravação atômica."""

    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else app_data_dir() / "config.json"
        self.data = copy.deepcopy(DEFAULTS)
        self._lock = threading.Lock()

    def __getitem__(self, section: str) -> dict:
        return self.data[section]

    def get(self, dotted: str, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value) -> None:
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def load(self) -> "Config":
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                _merge(self.data, stored)
        except FileNotFoundError:
            pass
        except Exception as exc:  # arquivo corrompido: mantém padrões e guarda cópia
            try:
                os.replace(self.path, str(self.path) + ".corrompido")
            except OSError:
                pass
            print(f"[config] falha ao ler {self.path}: {exc}", file=sys.stderr)
        return self

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(self.path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)

    def snapshot(self) -> dict:
        return copy.deepcopy(self.data)
