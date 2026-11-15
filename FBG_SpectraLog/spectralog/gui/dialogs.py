"""Diálogos: diagnóstico da conexão, energia do Windows e recuperação de ensaio."""
from __future__ import annotations

import os
import platform
import threading
import time

from PySide6 import QtCore, QtGui, QtWidgets

from .. import __version__, power
from ..discovery import find_process, ports_from_conf
from ..illws_client import ILLWSClient, parse_labview_json
from .widgets import hint


class _Signals(QtCore.QObject):
    line = QtCore.Signal(str)
    done = QtCore.Signal()


class DiagnosticsDialog(QtWidgets.QDialog):
    """Testa todas as rotas de leitura do ILLWS e mostra as respostas brutas."""

    def __init__(self, cfg, engine, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.engine = engine
        self.setWindowTitle("Diagnóstico da conexão com o ILLumiSense")
        self.resize(900, 620)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(hint("Testa o web service do ILLumiSense rota por rota (somente leitura; nada é alterado no "
                           "equipamento). Se algo falhar, envie este relatório para análise."))
        self.text = QtWidgets.QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        lay.addWidget(self.text, 1)
        row = QtWidgets.QHBoxLayout()
        self.btn_run = QtWidgets.QPushButton("Executar novamente")
        self.btn_run.clicked.connect(self.run)
        b_copy = QtWidgets.QPushButton("Copiar relatório")
        b_copy.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.text.toPlainText()))
        b_save = QtWidgets.QPushButton("Salvar relatório…")
        b_save.clicked.connect(self._save)
        b_close = QtWidgets.QPushButton("Fechar")
        b_close.clicked.connect(self.accept)
        for b in (self.btn_run, b_copy, b_save):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(b_close)
        lay.addLayout(row)
        self.sig = _Signals()
        self.sig.line.connect(self.text.appendPlainText)
        self.sig.done.connect(lambda: self.btn_run.setEnabled(True))
        QtCore.QTimer.singleShot(50, self.run)

    def _save(self):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Salvar relatório", "diagnostico_spectralog.txt",
                                                     "Texto (*.txt)")
        if p:
            with open(p, "w", encoding="utf-8") as f:
                f.write(self.text.toPlainText())

    def run(self):
        self.text.clear()
        self.btn_run.setEnabled(False)
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        out = self.sig.line.emit
        cc = self.cfg["connection"]
        out(f"FBG SpectraLog {__version__} · {platform.platform()} · Python {platform.python_version()}")
        out(f"Data: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        p = find_process(cc.get("process_name", "ILLumiSense"))
        if p:
            out(f"[processo] ILLumiSense em execução: PID {p.pid}  {p.exe}")
            ports = ports_from_conf(p.folder)
            out(f"[processo] portas no niembeddedws.conf: {ports or 'arquivo não encontrado'}")
        else:
            out("[processo] ILLumiSense.exe NÃO encontrado entre os processos em execução.")
        port = self.engine._effective_port()
        cl = ILLWSClient(cc["host"], port, cc["service"], max(3.0, float(cc["timeout_s"])))
        out(f"[http] URL base: {cl.base_url}\n")
        routes = ["GetHWInformation/", "GetSerialNumber/", "GetSWVersion/", "GetFWVersion/", "GetScanRate/",
                  "GetActiveChannels/"]
        for ch in range(0, 5):
            routes += [f"GetChannelSettings/{ch}", f"GetChannelStatus/{ch}", f"GetPeaks/{ch}", f"GetSpectrum/{ch}"]
        ok_any = False
        for r in routes:
            try:
                code, txt, ms = cl.get_raw(r)
                ok_any = ok_any or code == 200
                summary = ""
                try:
                    o = parse_labview_json(txt)
                    parts = []
                    for k, v in o.items():
                        if isinstance(v, list):
                            if v and isinstance(v[0], (int, float)):
                                parts.append(f"{k}[{len(v)}] {min(v):.6g}…{max(v):.6g}")
                            else:
                                parts.append(f"{k}[{len(v)}]={v[:8]}")
                        else:
                            parts.append(f"{k}={v}")
                    summary = "; ".join(parts)
                except Exception as exc:
                    summary = f"(JSON não interpretado: {exc}) {txt[:200]!r}"
                out(f"{r:<26} HTTP {code}  {ms:7.1f} ms  {summary}")
            except Exception as exc:
                out(f"{r:<26} FALHOU: {exc}")
                if not ok_any and r == routes[0]:
                    out("\nNenhuma resposta do web service. Verifique:\n"
                        "  1. o ILLumiSense está aberto e conectado ao interrogador;\n"
                        "  2. a porta (padrão 8002, ver niembeddedws.conf na pasta do ILLumiSense);\n"
                        "  3. o Firewall do Windows não bloqueia o ILLumiSense na rede local;\n"
                        "  4. teste no navegador: " + cl.base_url + "GetHWInformation/")
                    break
        st = self.engine.status()
        out(f"\n[motor] conectado={st['connected']} numeração={st['base']} ativos={st['active']} "
            f"gravando_canais={st['enabled']} leitura={st['fetch_ms']:.0f} ms")
        self.sig.done.emit()


class PowerDialog(QtWidgets.QDialog):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Energia do Windows para ensaios longos")
        self.resize(720, 520)
        lay = QtWidgets.QVBoxLayout(self)
        st = engine.status()
        lay.addWidget(QtWidgets.QLabel("<b>Proteção automática (já ativa enquanto o programa estiver aberto)</b>"))
        lay.addWidget(hint("• O Windows não entra em suspensão/hibernação por inatividade.\n"
                           "• A TELA PODE DESLIGAR normalmente — isso não interrompe o ensaio.\n"
                           "• Economia de energia (EcoQoS) desativada para este programa e para o ILLumiSense.\n"
                           "Estado: " + ("ATIVA — " + "; ".join(st["power_details"]) if st["power"]
                                         else "inativa (" + "; ".join(st["power_details"]) + ")")))
        lay.addSpacing(8)
        lay.addWidget(QtWidgets.QLabel("<b>Ajuste opcional do plano de energia (recomendado para ensaios de dias)</b>"))
        lay.addWidget(hint("Aplica ao plano atual: suspensão e hibernação = nunca; suspensão seletiva de USB "
                           "desativada (evita que a porta USB do interrogador seja desligada); fechar a tampa do "
                           "notebook = não fazer nada. O tempo de desligamento da TELA não é alterado. "
                           "Lembre-se também de adiar o Windows Update durante o ensaio."))
        self.out = QtWidgets.QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setPlainText("\n".join(" ".join(c) for c, _ in power.POWERCFG_COMMANDS))
        lay.addWidget(self.out, 1)
        row = QtWidgets.QHBoxLayout()
        b_apply = QtWidgets.QPushButton("Aplicar ajustes")
        b_apply.setObjectName("primary")
        b_apply.setEnabled(power.IS_WINDOWS)
        b_apply.clicked.connect(self._apply)
        b_req = QtWidgets.QPushButton("Verificar (powercfg /requests)")
        b_req.setEnabled(power.IS_WINDOWS)
        b_req.clicked.connect(lambda: self.out.setPlainText(power.powercfg_requests()))
        b_close = QtWidgets.QPushButton("Fechar")
        b_close.clicked.connect(self.accept)
        row.addWidget(b_apply)
        row.addWidget(b_req)
        row.addStretch(1)
        row.addWidget(b_close)
        lay.addLayout(row)

    def _apply(self):
        res = power.optimize_windows_power_plan()
        lines = [("OK    " if ok else "FALHA ") + label + (f"  ({msg})" if msg and not ok else "")
                 for label, ok, msg in res]
        self.out.setPlainText("\n".join(lines))
        self.engine.log("INFO", "Plano de energia do Windows ajustado para ensaio longo.")


class RecoveryDialog(QtWidgets.QDialog):
    """Pergunta (com contagem regressiva) se deve continuar o ensaio interrompido."""

    def __init__(self, test_dir: str, seconds: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ensaio interrompido")
        self.remaining = int(seconds)
        lay = QtWidgets.QVBoxLayout(self)
        lab = QtWidgets.QLabel(f"<b>O último ensaio não foi encerrado normalmente</b><br>"
                               f"(queda de energia, reinício do Windows ou fechamento inesperado).<br><br>"
                               f"Pasta: <code>{test_dir}</code><br><br>"
                               f"A gravação continuará na MESMA pasta, em um novo ciclo, assim que o "
                               f"ILLumiSense estiver conectado.")
        lab.setWordWrap(True)
        lab.setTextFormat(QtCore.Qt.RichText)
        lay.addWidget(lab)
        self.count = QtWidgets.QLabel()
        lay.addWidget(self.count)
        row = QtWidgets.QHBoxLayout()
        yes = QtWidgets.QPushButton("Continuar o ensaio agora")
        yes.setObjectName("primary")
        yes.clicked.connect(self.accept)
        no = QtWidgets.QPushButton("Não continuar")
        no.clicked.connect(self.reject)
        row.addWidget(yes)
        row.addStretch(1)
        row.addWidget(no)
        lay.addLayout(row)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self._tick(first=True)

    def _tick(self, first=False):
        if not first:
            self.remaining -= 1
        self.count.setText(f"Continuando automaticamente em {self.remaining} s…")
        if self.remaining <= 0:
            self.timer.stop()
            self.accept()


def install_startup(enable: bool) -> str:
    """Cria/remove um atalho .bat na pasta Inicializar do Windows."""
    if os.name != "nt":
        return ""
    import sys
    startup = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
    path = os.path.join(startup, "FBG_SpectraLog.bat")
    if enable:
        if getattr(sys, "frozen", False):
            cmd = f'start "" "{sys.executable}"'
        else:
            main = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "run_spectralog.py"))
            pyw = sys.executable.replace("python.exe", "pythonw.exe")
            cmd = f'start "" "{pyw}" "{main}"'
        with open(path, "w", encoding="utf-8") as f:
            f.write("@echo off\r\nREM Abre o FBG SpectraLog ao iniciar o Windows\r\n"
                    "timeout /t 20 /nobreak >nul\r\n" + cmd + "\r\n")
    elif os.path.exists(path):
        os.remove(path)
    return path


def startup_installed() -> bool:
    if os.name != "nt":
        return False
    return os.path.exists(os.path.join(os.environ.get("APPDATA", ""),
                                       r"Microsoft\Windows\Start Menu\Programs\Startup\FBG_SpectraLog.bat"))
