"""Inicialização da interface gráfica."""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from .. import APP_NAME, __version__
from ..config import app_data_dir


def _install_excepthook(log_dir: Path):
    """Exceções na interface são registradas, mas NUNCA encerram o programa (a gravação continua)."""
    def hook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        try:
            with open(log_dir / "erros_interface.log", "a", encoding="utf-8") as f:
                f.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S}\n{text}")
        except Exception:
            pass
        sys.__stderr__ and sys.__stderr__.write(text)
    sys.excepthook = hook


def run_gui(cfg) -> int:
    if os.name == "nt":
        try:  # ícone próprio na barra de tarefas
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FBG.SpectraLog")
        except Exception:
            pass
    QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(
        QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(True)

    logs = app_data_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    _install_excepthook(logs)

    lock = QtCore.QLockFile(str(app_data_dir() / "instancia.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(200):
        QtWidgets.QMessageBox.warning(None, APP_NAME, "O FBG SpectraLog já está aberto neste computador.\n"
                                                      "Use a janela existente (duas cópias gravando ao mesmo "
                                                      "tempo duplicariam os dados).")
        return 1

    from ..engine import Engine
    from .dialogs import RecoveryDialog
    from .main_window import MainWindow

    engine = Engine(cfg)
    win = MainWindow(cfg, engine)

    # recuperação de ensaio interrompido (queda de energia, reinício do Windows, travamento)
    rec = cfg["recovery"]
    last = rec.get("last_test_dir", "")
    if rec.get("recording_active") and last and Path(last).exists():
        if rec.get("auto_resume_after_crash", True):
            dlg = RecoveryDialog(last, int(rec.get("resume_countdown_s", 60)), win)
            if dlg.exec() == QtWidgets.QDialog.Accepted:
                win.schedule_resume(last)
            else:
                cfg.set("recovery.recording_active", False)
                cfg.save()
        else:
            engine.log("AVISO", f"O ensaio {last} foi interrompido inesperadamente. "
                                f"Use Ensaio > Continuar ensaio existente para continuar.")
            cfg.set("recovery.recording_active", False)

    def session_ending(_manager):
        # Windows está desligando/reiniciando: fecha os arquivos com segurança e mantém a
        # marca de "gravando" para retomar o ensaio automaticamente na próxima abertura.
        if engine.recording:
            engine.log("LACUNA", "Windows encerrando a sessão: dados gravados e arquivos fechados.")
            engine.stop_recording("interrompido_pelo_windows")
            cfg.set("recovery.recording_active", True)
            cfg.save()
    app.commitDataRequest.connect(session_ending)

    engine.start()
    win.show()
    code = app.exec()
    try:
        if not win._quitting:
            engine.shutdown()
    finally:
        lock.unlock()
    return code
