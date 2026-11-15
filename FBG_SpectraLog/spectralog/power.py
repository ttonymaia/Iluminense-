"""Proteção do ensaio contra suspensão/economia de energia do Windows.

O desligamento da TELA não interrompe processos; o que interrompe um ensaio é o
Windows entrar em suspensão/hibernação, suspender a porta USB do interrogador ou
estrangular (EcoQoS) processos em segundo plano. Este módulo:

  * cria uma "power request" (PowerCreateRequest/PowerSetRequest) do tipo
    SystemRequired + ExecutionRequired  -> o sistema não suspende, a tela pode apagar;
  * reforça com SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED);
  * desativa o power throttling (EcoQoS) deste processo e, opcionalmente, do ILLumiSense;
  * eleva a prioridade do processo e a resolução do timer do Windows (1 ms);
  * oferece um ajuste opcional do plano de energia via powercfg.

Em sistemas que não são Windows, tudo vira no-op.
"""
from __future__ import annotations

import os
import subprocess
import threading

IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ES_AWAYMODE_REQUIRED = 0x00000040

    POWER_REQUEST_CONTEXT_VERSION = 0
    POWER_REQUEST_CONTEXT_SIMPLE_STRING = 0x1
    PowerRequestSystemRequired = 1
    PowerRequestExecutionRequired = 3

    ProcessPowerThrottling = 4
    PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
    PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
    PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION = 0x4
    PROCESS_SET_INFORMATION = 0x0200

    class _DETAILED(ctypes.Structure):
        _fields_ = [("LocalizedReasonModule", wintypes.HMODULE),
                    ("LocalizedReasonId", wintypes.ULONG),
                    ("ReasonStringCount", wintypes.ULONG),
                    ("ReasonStrings", ctypes.POINTER(wintypes.LPWSTR))]

    class _REASON(ctypes.Union):
        _fields_ = [("Detailed", _DETAILED), ("SimpleReasonString", wintypes.LPWSTR)]

    class REASON_CONTEXT(ctypes.Structure):
        _fields_ = [("Version", wintypes.ULONG), ("Flags", wintypes.DWORD), ("Reason", _REASON)]

    class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
        _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG),
                    ("StateMask", wintypes.ULONG)]

    _k32.SetThreadExecutionState.argtypes = [wintypes.DWORD]
    _k32.SetThreadExecutionState.restype = wintypes.DWORD
    _k32.PowerCreateRequest.argtypes = [ctypes.POINTER(REASON_CONTEXT)]
    _k32.PowerCreateRequest.restype = wintypes.HANDLE
    _k32.PowerSetRequest.argtypes = [wintypes.HANDLE, ctypes.c_int]
    _k32.PowerSetRequest.restype = wintypes.BOOL
    _k32.PowerClearRequest.argtypes = [wintypes.HANDLE, ctypes.c_int]
    _k32.PowerClearRequest.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    try:
        _k32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        _k32.SetProcessInformation.restype = wintypes.BOOL
    except AttributeError:  # Windows < 8
        pass


def _disable_throttling_handle(handle) -> bool:
    if not IS_WINDOWS or not hasattr(_k32, "SetProcessInformation"):
        return False
    st = PROCESS_POWER_THROTTLING_STATE(
        PROCESS_POWER_THROTTLING_CURRENT_VERSION,
        PROCESS_POWER_THROTTLING_EXECUTION_SPEED | PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION,
        0)
    return bool(_k32.SetProcessInformation(handle, ProcessPowerThrottling,
                                           ctypes.byref(st), ctypes.sizeof(st)))


def disable_power_throttling(pid: int | None = None) -> bool:
    """Desativa o EcoQoS/power throttling deste processo (pid=None) ou de outro processo."""
    if not IS_WINDOWS:
        return False
    try:
        if pid is None:
            return _disable_throttling_handle(_k32.GetCurrentProcess())
        h = _k32.OpenProcess(PROCESS_SET_INFORMATION, False, int(pid))
        if not h:
            return False
        try:
            return _disable_throttling_handle(h)
        finally:
            _k32.CloseHandle(h)
    except Exception:
        return False


def raise_priority() -> bool:
    try:
        import psutil
        p = psutil.Process()
        if IS_WINDOWS:
            p.nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
        return True
    except Exception:
        return False


def set_timer_resolution_1ms() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return ctypes.WinDLL("winmm").timeBeginPeriod(1) == 0
    except Exception:
        return False


class PowerGuard:
    """Mantém o Windows acordado enquanto o programa estiver aberto (a tela pode apagar)."""

    REASON = "FBG SpectraLog: ensaio óptico em andamento (gravação de espectro)"

    def __init__(self):
        self._handle = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.active = False
        self.details: list[str] = []

    def start(self) -> bool:
        if not IS_WINDOWS:
            self.details = ["Sistema não-Windows: proteção de energia não necessária."]
            self.active = False
            return False
        if self._thread and self._thread.is_alive():
            return self.active
        self._stop.clear()
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), name="PowerGuard", daemon=True)
        self._thread.start()
        ready.wait(2.0)
        return self.active

    def _run(self, ready: threading.Event) -> None:
        details = []
        try:
            self._reason_buf = ctypes.create_unicode_buffer(self.REASON)
            ctx = REASON_CONTEXT()
            ctx.Version = POWER_REQUEST_CONTEXT_VERSION
            ctx.Flags = POWER_REQUEST_CONTEXT_SIMPLE_STRING
            ctx.Reason.SimpleReasonString = ctypes.cast(self._reason_buf, wintypes.LPWSTR)
            h = _k32.PowerCreateRequest(ctypes.byref(ctx))
            if h and h != wintypes.HANDLE(-1).value:
                self._handle = h
                ok1 = _k32.PowerSetRequest(h, PowerRequestSystemRequired)
                ok2 = _k32.PowerSetRequest(h, PowerRequestExecutionRequired)
                details.append(f"PowerRequest SystemRequired={'ok' if ok1 else 'falhou'}, "
                               f"ExecutionRequired={'ok' if ok2 else 'n/d'}")
        except Exception as exc:
            details.append(f"PowerCreateRequest indisponível: {exc}")
        flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
        prev = _k32.SetThreadExecutionState(flags)
        if prev == 0:
            flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
            prev = _k32.SetThreadExecutionState(flags)
        details.append("SetThreadExecutionState " + ("ok" if prev else "falhou"))
        self.active = bool(prev) or self._handle is not None
        self.details = details
        ready.set()
        # renova periodicamente (o estado é por thread; esta thread vive enquanto o app vive)
        while not self._stop.wait(30.0):
            _k32.SetThreadExecutionState(flags)
        _k32.SetThreadExecutionState(ES_CONTINUOUS)
        if self._handle:
            try:
                _k32.PowerClearRequest(self._handle, PowerRequestSystemRequired)
                _k32.PowerClearRequest(self._handle, PowerRequestExecutionRequired)
                _k32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None
        self.active = False

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)


# GUID do subgrupo USB e da configuração "Suspensão seletiva de USB"
_USB_SUBGROUP = "2a737441-1930-4402-8d77-b2bebba308a3"
_USB_SELECTIVE = "48e6b7a6-50f5-4782-a5d4-53bb8f07e226"

POWERCFG_COMMANDS = [
    (["powercfg", "/change", "standby-timeout-ac", "0"], "Suspensão (na tomada): nunca"),
    (["powercfg", "/change", "hibernate-timeout-ac", "0"], "Hibernação (na tomada): nunca"),
    (["powercfg", "/change", "standby-timeout-dc", "0"], "Suspensão (bateria): nunca"),
    (["powercfg", "/change", "hibernate-timeout-dc", "0"], "Hibernação (bateria): nunca"),
    (["powercfg", "/SETACVALUEINDEX", "SCHEME_CURRENT", _USB_SUBGROUP, _USB_SELECTIVE, "0"],
     "Suspensão seletiva de USB (tomada): desativada"),
    (["powercfg", "/SETDCVALUEINDEX", "SCHEME_CURRENT", _USB_SUBGROUP, _USB_SELECTIVE, "0"],
     "Suspensão seletiva de USB (bateria): desativada"),
    (["powercfg", "/SETACVALUEINDEX", "SCHEME_CURRENT", "SUB_BUTTONS", "LIDACTION", "0"],
     "Fechar a tampa (notebook, tomada): não fazer nada"),
    (["powercfg", "/SETACTIVE", "SCHEME_CURRENT"], "Aplicar plano de energia"),
]


def optimize_windows_power_plan() -> list[tuple[str, bool, str]]:
    """Aplica o ajuste do plano de energia. Mantém o desligamento da tela como está."""
    results = []
    if not IS_WINDOWS:
        return [("Apenas no Windows", False, "")]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for cmd, label in POWERCFG_COMMANDS:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15, creationflags=flags)
            results.append((label, r.returncode == 0, (r.stdout + r.stderr).strip()))
        except Exception as exc:
            results.append((label, False, str(exc)))
    return results


def powercfg_requests() -> str:
    if not IS_WINDOWS:
        return ""
    try:
        r = subprocess.run(["powercfg", "/requests"], capture_output=True, text=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.stdout
    except Exception as exc:
        return str(exc)
