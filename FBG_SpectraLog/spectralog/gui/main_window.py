"""Janela principal do FBG SpectraLog."""
from __future__ import annotations

import html
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import pyqtgraph as pg
import pyqtgraph.exporters  # noqa: F401
from PySide6 import QtCore, QtGui, QtWidgets

from .. import APP_NAME, __version__
from ..storage import CsvFormat
from . import dialogs
from .settings_panel import SettingsPanel
from .theme import STATUS, apply_pg_defaults, channel_color, palette, qss
from .views import EvolutionView, SpectrumView, WaterfallView
from .widgets import FlowLayout, StatusPill, fmt_duration, fmt_int

LEVEL_COLORS = {"OK": STATUS["good"], "AVISO": STATUS["serious"], "LACUNA": STATUS["serious"],
                "ERRO": STATUS["critical"], "INFO": None}


def open_folder(path: str) -> None:
    if not path:
        return
    if os.name == "nt":
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, cfg, engine, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.engine = engine
        self.theme = palette(cfg.get("ui.theme", "light"))
        apply_pg_defaults(self.theme)
        self.setWindowTitle(f"{APP_NAME} {__version__} — espectro bruto do ILLumiSense (FBG-scan)")
        self.resize(1500, 920)
        self._last_seq = 0
        self._last_event = 0
        self._last_table = 0.0
        self._last_evo = 0.0
        self._last_status = 0.0
        self._pending_resume: str | None = None
        self._sim = None
        self._quitting = False

        self._build_actions()
        self._build_menu()
        self._build_toolbar()
        self._build_central()
        self._build_dock()
        self.statusBar().showMessage("Pronto.")
        self.setStyleSheet(qss(self.theme))

        self._save_timer = QtCore.QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_cfg)
        self.ui_timer = QtCore.QTimer(self)
        self.ui_timer.timeout.connect(self._tick)
        self.ui_timer.start(int(1000 / max(1, int(cfg.get("ui.plot_fps", 10)))))
        self._tick()

    # ================================================================== construção
    def _act(self, text, slot, shortcut=None, tip=""):
        a = QtGui.QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(shortcut)
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        return a

    def _build_actions(self):
        self.a_start = self._act("▶  Iniciar ensaio", self.start_test, "F5", "Começa a gravar em uma nova pasta")
        self.a_resume = self._act("Continuar ensaio existente…", self.resume_test, None,
                                  "Continua gravando em uma pasta de ensaio já existente (novo ciclo)")
        self.a_stop = self._act("■  Parar ensaio", self.stop_test, "Shift+F5", "Encerra a gravação com segurança")
        self.a_ref = self._act("Capturar referência", self.capture_ref, "F8",
                               "Guarda o espectro atual como referência (ex.: antes do carregamento)")
        self.a_clear_ref = self._act("Limpar referência", self.clear_ref)
        self.a_png = self._act("Exportar gráfico (PNG/SVG)…", self.export_plot, "Ctrl+E")
        self.a_csv = self._act("Exportar espectro atual (CSV)…", self.export_current_csv)
        self.a_folder = self._act("Abrir pasta do ensaio", self.open_test_folder)
        self.a_viewer = self._act("Visualizador de ensaios…", self.open_viewer, "Ctrl+O")
        self.a_diag = self._act("Diagnóstico da conexão…", self.open_diag)
        self.a_power = self._act("Energia do Windows…", self.open_power)
        self.a_sim = self._act("Iniciar simulador do ILLumiSense", self.toggle_sim, None,
                               "Simula o ILLumiSense para treinar/testar sem o equipamento")
        self.a_h5 = self._act("Converter ensaio para HDF5…", lambda: self.convert("h5"))
        self.a_mat = self._act("Converter ensaio para MATLAB (.mat)…", lambda: self.convert("mat"))
        self.a_theme = self._act("Tema escuro", self.toggle_theme)
        self.a_theme.setCheckable(True)
        self.a_theme.setChecked(self.theme["name"] == "dark")
        self.a_about = self._act("Sobre", self.about)
        self.a_quit = self._act("Sair", self.close, "Ctrl+Q")

    def _build_menu(self):
        mb = self.menuBar()
        m = mb.addMenu("&Ensaio")
        for a in (self.a_start, self.a_resume, self.a_stop, None, self.a_folder, None, self.a_quit):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Análise")
        for a in (self.a_ref, self.a_clear_ref, None, self.a_png, self.a_csv):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Ferramentas")
        for a in (self.a_viewer, self.a_h5, self.a_mat, None, self.a_diag, self.a_power, None, self.a_sim):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("E&xibir")
        m.addAction(self.a_theme)
        self.dock_action_menu = m
        m = mb.addMenu("Aj&uda")
        m.addAction(self.a_about)

    def _build_toolbar(self):
        tb = QtWidgets.QToolBar("Principal")
        tb.setMovable(False)
        tb.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.addToolBar(tb)
        tb.addAction(self.a_start)
        tb.widgetForAction(self.a_start).setObjectName("primary")
        tb.addAction(self.a_stop)
        tb.widgetForAction(self.a_stop).setObjectName("danger")
        tb.addSeparator()
        for a in (self.a_ref, self.a_png, self.a_folder, self.a_viewer, self.a_diag):
            tb.addAction(a)

    def _build_central(self):
        central = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        # faixa de estado
        ribbon = QtWidgets.QFrame()
        ribbon.setObjectName("statusbar")
        h = FlowLayout(ribbon, spacing=6)
        h.setContentsMargins(8, 6, 8, 6)
        self.pill_proc = StatusPill("ILLumiSense")
        self.pill_conn = StatusPill("Web service")
        self.pill_dev = StatusPill("Equipamento")
        self.pill_rec = StatusPill("Gravação")
        self.pill_disk = StatusPill("Disco")
        self.pill_pow = StatusPill("Energia")
        for p in (self.pill_proc, self.pill_conn, self.pill_dev, self.pill_rec, self.pill_disk, self.pill_pow):
            h.addWidget(p)
        ribbon.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Minimum)
        v.addWidget(ribbon)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setDocumentMode(False)
        # aba espectro (gráfico + tabela de métricas)
        spec_split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.spectrum = SpectrumView(self.theme)
        spec_split.addWidget(self.spectrum)
        self.table = QtWidgets.QTableWidget(0, 12)
        self.table.setHorizontalHeaderLabels(
            ["Canal", "Pico", "λ máx (nm)", "λ centroide (nm)", "P máx", "FWHM (nm)", "BW −X dB (nm)",
             "Assimetria", "Skewness", "Sub-picos", "Δλ ref (pm)", "ρ ref"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        hh.setStretchLastSection(True)
        hh.setMinimumSectionSize(70)
        self.table.setAlternatingRowColors(False)
        spec_split.addWidget(self.table)
        spec_split.setSizes([640, 200])
        self.tabs.addTab(spec_split, "Espectro bruto")
        self.waterfall = WaterfallView(self.theme, int(self.cfg.get("ui.waterfall_len", 600)))
        self.tabs.addTab(self.waterfall, "Cascata (espectrograma)")
        self.evolution = EvolutionView(self.theme)
        self.tabs.addTab(self.evolution, "Evolução temporal")
        self.log_view = QtWidgets.QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(5000)
        self.tabs.addTab(self.log_view, "Eventos")
        wrap = QtWidgets.QWidget()
        wl = QtWidgets.QVBoxLayout(wrap)
        wl.setContentsMargins(6, 6, 6, 6)
        wl.addWidget(self.tabs)
        v.addWidget(wrap, 1)
        self.setCentralWidget(central)

    def _build_dock(self):
        self.settings = SettingsPanel(self.cfg, self.theme)
        self.settings.changed.connect(self._setting_changed)
        self.settings.reconnect_clicked.connect(self._reconnect)
        self.settings.optimize_power_clicked.connect(self.open_power)
        self.settings.startup.setChecked(dialogs.startup_installed())
        self.settings.startup.toggled.connect(self._toggle_startup)
        dock = QtWidgets.QDockWidget("Configurações", self)
        dock.setObjectName("settings")
        dock.setWidget(self.settings)
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetFloatable)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock)
        self.resizeDocks([dock], [430], QtCore.Qt.Horizontal)
        self.dock_action_menu.addAction(dock.toggleViewAction())

    # ================================================================== configurações
    def _setting_changed(self, key: str):
        self._save_timer.start(600)
        if key.startswith("acquisition.rate_hz") or key in ("acquisition.mode", "acquisition.burst_duration_s",
                                                           "acquisition.burst_period_s"):
            self.engine.update_schedule()
        elif key.startswith("connection.") and key not in ("connection.follow_active_channels",
                                                           "connection.auto_connect"):
            self.engine.reconnect_now()
        elif key == "power.keep_awake":
            self.engine.set_keep_awake(bool(self.cfg.get(key)))
        elif key == "ui.theme":
            pass

    def _save_cfg(self):
        try:
            self.cfg.save()
        except Exception as exc:
            self.statusBar().showMessage(f"Não foi possível salvar a configuração: {exc}", 8000)

    def _reconnect(self):
        self.engine.reconnect_now()
        self.statusBar().showMessage("Reconectando ao ILLumiSense…", 4000)

    def _toggle_startup(self, on: bool):
        try:
            p = dialogs.install_startup(on)
            self.engine.log("INFO", ("Inicialização automática ativada: " if on else
                                     "Inicialização automática removida: ") + p)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Não foi possível alterar a inicialização: {exc}")

    # ================================================================== ensaio
    def start_test(self):
        if self.engine.recording:
            return
        if not self.engine.connected:
            r = QtWidgets.QMessageBox.question(
                self, APP_NAME, "O ILLumiSense ainda não está conectado.\n\nIniciar o ensaio mesmo assim? "
                "A gravação começará automaticamente assim que a conexão for estabelecida.")
            if r != QtWidgets.QMessageBox.Yes:
                return
        if not self.engine.enabled_channels() and self.engine.connected:
            QtWidgets.QMessageBox.warning(self, APP_NAME, "Nenhum canal selecionado/ativo para gravar.")
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Iniciar ensaio", "Nome do ensaio:",
                                                  text=self.cfg.get("storage.test_name"))
        if not ok:
            return
        name = name.strip() or "ensaio"
        self.cfg.set("storage.test_name", name)
        self.settings.test_name.setText(name)
        try:
            Path(self.cfg.get("storage.base_dir")).mkdir(parents=True, exist_ok=True)
            d = self.engine.start_recording(name=name)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, APP_NAME, f"Não foi possível iniciar a gravação:\n{exc}")
            return
        self.statusBar().showMessage(f"Gravando em {d}", 10000)
        self.waterfall.clear()

    def resume_test(self, path: str | None = None):
        if self.engine.recording:
            return
        if not path:
            path = QtWidgets.QFileDialog.getExistingDirectory(self, "Pasta do ensaio a continuar",
                                                              self.cfg.get("storage.base_dir"))
        if not path:
            return
        if not (Path(path) / "ensaio_info.json").exists():
            QtWidgets.QMessageBox.warning(self, APP_NAME, "Esta pasta não parece ser um ensaio do FBG SpectraLog "
                                                          "(ensaio_info.json não encontrado).")
            return
        try:
            self.engine.start_recording(resume_dir=path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, APP_NAME, f"Não foi possível continuar o ensaio:\n{exc}")

    def schedule_resume(self, path: str):
        """Continua um ensaio interrompido assim que o ILLumiSense estiver conectado."""
        self._pending_resume = path
        self.engine.log("INFO", f"Retomada agendada: aguardando conexão para continuar {path}")

    def stop_test(self, ask: bool = True):
        if not self.engine.recording:
            return
        if ask:
            st = self.engine.status()
            r = QtWidgets.QMessageBox.question(
                self, "Parar ensaio", f"Parar a gravação do ensaio?\n\n{st['test_dir']}\n"
                f"{fmt_int(st['rec_index'])} amostras gravadas.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            if r != QtWidgets.QMessageBox.Yes:
                return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.engine.stop_recording()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def capture_ref(self):
        chs = self.engine.capture_reference()
        self.spectrum.refresh_legend()
        if not chs:
            self.statusBar().showMessage("Sem espectro válido para usar como referência.", 5000)

    def clear_ref(self):
        self.engine.clear_reference()
        self.spectrum.refresh_legend()

    def open_test_folder(self):
        st = self.engine.status()
        open_folder(st["test_dir"] or self.cfg.get("storage.base_dir"))

    # ================================================================== exportação
    def export_plot(self):
        tab = self.tabs.currentWidget()
        if tab is self.waterfall:
            target = self.waterfall.glw.ci
        elif tab is self.evolution:
            target = self.evolution.glw.ci
        else:
            target = self.spectrum.export_target()
        base = self.engine.status()["test_dir"] or str(Path.home())
        default = os.path.join(base, f"grafico_{time.strftime('%Y%m%d_%H%M%S')}.png")
        path, flt = QtWidgets.QFileDialog.getSaveFileName(self, "Exportar gráfico", default,
                                                          "PNG alta resolução (*.png);;SVG vetorial (*.svg)")
        if not path:
            return
        try:
            if path.lower().endswith(".svg"):
                ex = pg.exporters.SVGExporter(target)
            else:
                ex = pg.exporters.ImageExporter(target)
                ex.parameters()["width"] = 2400
                ex.parameters()["antialias"] = True
            ex.export(path)
            self.statusBar().showMessage(f"Gráfico exportado: {path}", 8000)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Falha ao exportar: {exc}")

    def export_current_csv(self):
        s = self.engine.latest
        if s is None:
            return
        base = self.engine.status()["test_dir"] or str(Path.home())
        default = os.path.join(base, f"espectro_{time.strftime('%Y%m%d_%H%M%S')}.csv")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Exportar espectro atual", default, "CSV (*.csv)")
        if not path:
            return
        F = CsvFormat(self.cfg.get("storage.csv_format", "intl"))
        chans = [c for c in sorted(s.frames) if s.frames[c].ok]
        n = max(s.frames[c].pw.size for c in chans) if chans else 0
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"# {APP_NAME} — espectro de {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(s.t_unix))}\n")
            f.write(F.sep.join(sum(([f"CH{c}_lambda_nm", f"CH{c}_potencia"] for c in chans), [])) + "\n")
            for i in range(n):
                row = []
                for c in chans:
                    fr = s.frames[c]
                    if i < fr.pw.size:
                        row += [F.f(fr.wav[i], "%.10g"), F.f(fr.pw[i], "%.8g")]
                    else:
                        row += ["", ""]
                f.write(F.sep.join(row) + "\n")
        self.statusBar().showMessage(f"Espectro exportado: {path}", 8000)

    def convert(self, kind: str):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Pasta do ensaio a converter",
                                                       self.cfg.get("storage.base_dir"))
        if not d:
            return
        from .. import reader
        prog = QtWidgets.QProgressDialog("Convertendo…", None, 0, 100, self)
        prog.setWindowModality(QtCore.Qt.WindowModal)
        prog.show()

        def cb(i, n):
            prog.setValue(int(100 * i / max(1, n)))
            QtWidgets.QApplication.processEvents()
        try:
            out = reader.export_hdf5(d, progress=cb) if kind == "h5" else reader.export_mat(d, progress=cb)
            prog.close()
            QtWidgets.QMessageBox.information(self, APP_NAME, f"Conversão concluída:\n{out}")
        except Exception as exc:
            prog.close()
            QtWidgets.QMessageBox.critical(self, APP_NAME, f"Falha na conversão:\n{exc}")

    # ================================================================== ferramentas
    def open_viewer(self):
        from .viewer import ViewerWindow
        w = ViewerWindow(self.cfg, self.theme, self)
        w.show()

    def open_diag(self):
        dialogs.DiagnosticsDialog(self.cfg, self.engine, self).exec()

    def open_power(self):
        dialogs.PowerDialog(self.engine, self).exec()

    def toggle_sim(self):
        from ..simulator import Simulator
        if self._sim is None:
            if self.engine.proc is not None:
                QtWidgets.QMessageBox.information(self, APP_NAME, "O ILLumiSense real está aberto; o simulador "
                                                                  "não é necessário.")
                return
            try:
                self._sim = Simulator(port=int(self.engine._effective_port())).start()
            except OSError as exc:
                QtWidgets.QMessageBox.warning(self, APP_NAME, f"Não foi possível abrir a porta do simulador: {exc}")
                return
            self.a_sim.setText("Parar simulador do ILLumiSense")
            self.engine.log("INFO", f"Simulador do ILLumiSense iniciado na porta {self._sim.port} (dados SIMULADOS).")
            self.engine.reconnect_now()
        else:
            self._sim.stop()
            self._sim = None
            self.a_sim.setText("Iniciar simulador do ILLumiSense")
            self.engine.log("INFO", "Simulador parado.")

    def toggle_theme(self):
        name = "dark" if self.a_theme.isChecked() else "light"
        self.cfg.set("ui.theme", name)
        self._save_timer.start(600)
        self.theme = palette(name)
        apply_pg_defaults(self.theme)
        self.setStyleSheet(qss(self.theme))
        for v in (self.spectrum, self.waterfall, self.evolution, self.settings):
            v.apply_theme(self.theme)
        self._last_table = 0

    def about(self):
        QtWidgets.QMessageBox.about(
            self, f"Sobre o {APP_NAME}",
            f"<b>{APP_NAME} {__version__}</b><br>Aquisição e gravação do <b>espectro bruto</b> de interrogadores "
            f"FBGS FBG-scan por meio do web service do ILLumiSense (ILLWS), em paralelo ao ILLumiSense.<br><br>"
            f"Somente leitura: o programa não altera nenhuma configuração do ILLumiSense.<br>"
            f"Dados: CSV por ciclo (potência e comprimento de onda de cada espectro), picos, métricas e eventos.")

    # ================================================================== atualização periódica
    def _tick(self):
        now = time.monotonic()
        eng = self.engine
        # retomada agendada após falha
        if self._pending_resume and eng.connected and not eng.recording:
            path, self._pending_resume = self._pending_resume, None
            try:
                eng.start_recording(resume_dir=path)
            except Exception as exc:
                eng.log("ERRO", f"Não foi possível retomar o ensaio: {exc}")
        self._update_events()
        minimized = self.isMinimized() or not self.isVisible()
        if now - self._last_status > (1.0 if minimized else 0.25):
            self._update_status()
            self._last_status = now
        if minimized:
            return
        s = eng.latest
        if s is not None and s.seq != self._last_seq:
            self._last_seq = s.seq
            an_seq, an = eng.latest_analysis
            self.waterfall.push(s)
            cur = self.tabs.currentIndex()
            if cur == 0:
                self.spectrum.update_data(s, an, eng.reference)
                if now - self._last_table > 0.5:
                    self._update_table(s, an)
                    self._last_table = now
            elif cur == 1:
                self.waterfall.render(self.spectrum.unit)
        if self.tabs.currentIndex() == 2 and now - self._last_evo > 1.0:
            st = eng.status()
            self.evolution.render(eng.history_copy(), st["rec_started"] if st["recording"] else None,
                                  self.spectrum.unit)
            self._last_evo = now

    def _update_events(self):
        evs = self.engine.events.since(self._last_event)
        if not evs:
            return
        self._last_event = evs[-1][0]
        for _, t, lvl, msg in evs:
            color = LEVEL_COLORS.get(lvl) or self.theme["ink2"]
            ts = time.strftime("%d/%m %H:%M:%S", time.localtime(t))
            self.log_view.append(f'<span style="color:{self.theme["muted"]}">{ts}</span> '
                                 f'<b style="color:{color}">{lvl}</b> '
                                 f'<span style="color:{self.theme["ink"]}">{html.escape(msg)}</span>')
            if lvl in ("OK", "AVISO", "ERRO", "LACUNA"):
                self.statusBar().showMessage(f"{lvl}: {msg}", 15000)

    def _update_status(self):
        st = self.engine.status()
        p = st["proc"]
        if p:
            self.pill_proc.set("good", f"em execução (PID {p.pid})", p.exe)
        elif not self.cfg.get("connection.detect_process", True):
            self.pill_proc.set("idle", "detecção desligada")
        else:
            self.pill_proc.set("warning" if not st["connected"] else "idle",
                               "não detectado" if not st["connected"] else "remoto/simulado")
        if st["connected"]:
            lat = st["latency_ms"]
            self.pill_conn.set("good", f"conectado · {lat:.0f} ms" if math.isfinite(lat) else "conectado", st["url"])
        else:
            err = st["last_conn_error"] or "aguardando"
            self.pill_conn.set("critical" if st["recording"] else "warning", "desconectado", f"{st['url']}\n{err}")
        d = st["device"]
        if d:
            sr = d.get("taxa_varredura_hz", "")
            self.pill_dev.set("good" if st["connected"] else "idle",
                              f"{d.get('tipo') or 'FBG-scan'} · {len(st['enabled'])}/{d.get('n_canais', 4)} canais"
                              + (f" · {sr:g} Hz" if isinstance(sr, (int, float)) and math.isfinite(sr) else ""),
                              f"SN {d.get('sn', '')}\nSW {d.get('versao_sw', '')}\nFW {d.get('versao_fw', '')}")
        else:
            self.pill_dev.set("idle", "—")
        if st["recording"]:
            el = time.time() - st["rec_started"]
            rate = st["eff_rate"]
            txt = (f"GRAVANDO {fmt_duration(el)} · {fmt_int(st['rec_index'])} amostras · ciclo {st['cycle']:04d}"
                   + (f" · {rate:.3g} Hz" if math.isfinite(rate) else ""))
            state = "good" if st["connected"] and not st["write_errors"] else "critical"
            tip = (f"{st['test_dir']}\nperdidos (atraso): {st['missed']} · repetidos: {st['dups']} · "
                   f"lacunas: {st['gaps']} · erros de gravação: {st['write_errors']} · fila: {st['pending']}")
            if st["next_burst"] is not None and self.cfg.get("acquisition.mode") == "burst":
                txt += f" · rajada {st['next_burst']}"
            self.pill_rec.set(state, txt, tip)
        else:
            self.pill_rec.set("idle", "parada")
        free = st["disk_free_gb"]
        if not math.isfinite(free):
            try:
                import shutil
                base = Path(self.cfg.get("storage.base_dir"))
                while not base.exists() and base.parent != base:
                    base = base.parent
                free = shutil.disk_usage(base).free / 1e9
            except Exception:
                free = float("nan")
        if math.isfinite(free):
            low = free < float(self.cfg.get("storage.min_free_gb", 2.0))
            self.pill_disk.set("critical" if low else "good", f"{free:,.1f} GB livres".replace(",", "."))
        if os.name == "nt":
            self.pill_pow.set("good" if st["power"] else "warning",
                              "protegido · tela pode apagar" if st["power"] else "sem proteção",
                              "\n".join(st["power_details"]))
        else:
            self.pill_pow.set("idle", "não aplicável")
        self.a_start.setEnabled(not st["recording"])
        self.a_resume.setEnabled(not st["recording"])
        self.a_stop.setEnabled(st["recording"])
        self.settings.set_recording(st["recording"])
        self.settings.update_status(st, self.theme)

    def _update_table(self, s, an: dict):
        rows = []
        ref = self.engine.reference
        for ch in sorted(s.frames):
            a = an.get(ch)
            if not a:
                continue
            r = a.get("ref", {})
            for k, p in enumerate(a["peaks"], 1):
                rows.append((ch, k, p, r if k == 1 and ch in ref else {}))
            if not a["peaks"]:
                rows.append((ch, 0, None, {}))
        self.table.setRowCount(len(rows))
        u = "dBm" if self.spectrum.unit == "dB" else "u.a."
        bw = self.cfg.get("analysis.bw_db", 10.0)
        self.table.setHorizontalHeaderLabels(
            ["Canal", "Pico", "λ máx (nm)", "λ centroide (nm)", f"P máx ({u})", "FWHM (nm)", f"BW −{bw:g} dB (nm)",
             "Assimetria", "Skewness", "Sub-picos", "Δλ ref (pm)", "ρ ref"])

        def fmt(v, f):
            return "—" if v is None or (isinstance(v, float) and not math.isfinite(v)) else f % v

        for i, (ch, k, p, r) in enumerate(rows):
            vals = [f"CH{ch}", str(k) if k else "—"]
            if p:
                vals += [fmt(p["lambda_max_nm"], "%.4f"), fmt(p["lambda_centroide_nm"], "%.4f"),
                         fmt(p["P_max"], "%.2f"), fmt(p["fwhm_nm"], "%.4f"), fmt(p["bw_x_nm"], "%.4f"),
                         fmt(p["assimetria"], "%.3f"), fmt(p["skewness"], "%.3f"), str(p["sub_picos"])]
            else:
                vals += ["sem picos"] + ["—"] * 7
            vals += [fmt(r.get("shift_nm", float("nan")) * 1000 if r else float("nan"), "%.1f"),
                     fmt(r.get("correlacao") if r else None, "%.4f")]
            for j, v in enumerate(vals):
                item = self.table.item(i, j)
                if item is None:
                    item = QtWidgets.QTableWidgetItem()
                    item.setTextAlignment(QtCore.Qt.AlignCenter)
                    self.table.setItem(i, j, item)
                item.setText(v)
                if j == 0:
                    item.setForeground(QtGui.QColor(channel_color(self.theme, ch)))
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)

    # ================================================================== encerramento
    def closeEvent(self, ev: QtGui.QCloseEvent):  # noqa: N802
        if self.engine.recording and not self._quitting:
            r = QtWidgets.QMessageBox.warning(
                self, APP_NAME, "Há um ensaio em GRAVAÇÃO.\n\nFechar o programa encerra a gravação "
                "(os dados já gravados ficam salvos). Deseja realmente fechar?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            if r != QtWidgets.QMessageBox.Yes:
                ev.ignore()
                return
        self._quitting = True
        self.ui_timer.stop()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.engine.shutdown()
            if self._sim:
                self._sim.stop()
            self._save_cfg()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        ev.accept()
