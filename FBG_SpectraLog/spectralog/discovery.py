"""Detecção do ILLumiSense em execução e da porta do seu web service."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


@dataclass
class ProcessInfo:
    pid: int
    name: str
    exe: str

    @property
    def folder(self) -> str:
        return os.path.dirname(self.exe) if self.exe else ""


def find_process(name_hint: str = "ILLumiSense") -> ProcessInfo | None:
    """Procura o processo do ILLumiSense (ILLumiSense.exe).

    Também aceita processos cujo comando contenha o nome (ex.: o simulador em Python),
    o que permite testar o programa sem o equipamento.
    """
    if psutil is None:
        return None
    hint = name_hint.lower().replace(".exe", "")
    me = os.getpid()
    fallback = None
    for p in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            if p.info["pid"] == me:
                continue
            name = (p.info.get("name") or "")
            if name.lower().replace(".exe", "") == hint:
                return ProcessInfo(p.info["pid"], name, p.info.get("exe") or "")
            if fallback is None:
                cmd = " ".join(p.info.get("cmdline") or []).lower()
                if hint in cmd and "spectralog" not in cmd and "pytest" not in cmd:
                    fallback = ProcessInfo(p.info["pid"], name, p.info.get("exe") or "")
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception:
            continue
    return fallback


def process_alive(pid: int) -> bool:
    if psutil is None:
        return False
    try:
        return psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except Exception:
        return False


def ports_from_conf(folder: str) -> list[int]:
    """Lê as portas HTTP (sem SSL) do niembeddedws.conf que acompanha o ILLumiSense."""
    if not folder:
        return []
    conf = Path(folder) / "niembeddedws.conf"
    try:
        text = conf.read_text(encoding="latin-1", errors="ignore")
    except OSError:
        return []
    listen = [int(p) for p in re.findall(r"^\s*Listen\s+(?:[\w.\[\]:]*:)?(\d+)\s*$", text, re.M)]
    ssl_ports = {int(p) for p in re.findall(r"<VirtualHost\s+[^>]*:(\d+)\s*>", text)}
    plain = [p for p in listen if p not in ssl_ports]
    return plain or listen
