"""Visualizador de ensaios gravados (pós-processamento)."""
from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from .. import reader
from ..analysis import analyze_spectrum
from ..storage import CsvFormat
from .theme import qss, sequential_cmap, style_plot
from .views import unit_label
from .widgets import Crosshair, hint


class ViewerWindow(QtWidgets.QMainWindow):
    def __init__(self, cfg, theme: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.theme = theme
        self.data = None
        self.test_dir: Path | None = None
        self.setWindowTitle("Visualizador de ensaios — FBG SpectraLog")
        self.resize(1400, 860)
        self.setStyleSheet(qss(theme))

        tb = QtWidgets.QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addAction("Abrir ensaio…", self.open_dialog)
        tb.addAction("Exportar espectro selecionado (CSV)…", self.export_csv)
        tb.addAction("Exportar figura (PNG/SVG)…", self.export_fig)
        self.path_label = QtWidgets.QLabel("  Nenhum ensaio aberto")
        self.path_label.setObjectName("muted")
        tb.addWidget(self.path_label)

        split = QtWidgets.QSplitter()
        left = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(left)
        ll.setContentsMargins(6, 6, 6, 6)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Ciclo / canal"])
        self.tree.itemDoubleClicked.connect(self._load_item)
        ll.addWidget(QtWidgets.QLabel("Dados gravados"))
        ll.addWidget(self.tree, 1)
        ll.addWidget(hint("Duplo clique em um canal para carregar todos os ciclos, ou em um canal dentro de um "
                          "ciclo para carregar só aquele ciclo."))
        self.info = QtWidgets.QPlainTextEdit()
        self.info.setReadOnly(True)
        self.info.setMaximumHeight(230)
        ll.addWidget(self.info)
        split.addWidget(left)

        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right)
        rl.setContentsMargins(6, 6, 6, 6)
        bar = QtWidgets.QHBoxLayout()
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.valueChanged.connect(self._show_index)
        self.spin = QtWidgets.QSpinBox()
        self.spin.valueChanged.connect(self.slider.setValue)
        self.slider.valueChanged.connect(self.spin.setValue)
        self.overlay_n = QtWidgets.QSpinBox()
        self.overlay_n.setRange(0, 60)
        self.overlay_n.setValue(0)
        self.overlay_n.setPrefix("Sobrepor ")
        self.overlay_n.setSuffix(" espectros")
        self.overlay_n.setToolTip("Sobrepõe N espectros igualmente espaçados ao longo do ensaio (evolução do dano)")
        self.overlay_n.valueChanged.connect(lambda _: self._show_index(self.slider.value()))
        self.stamp = QtWidgets.QLabel("")
        self.stamp.setObjectName("muted")
        bar.addWidget(QtWidgets.QLabel("Espectro"))
        bar.addWidget(self.slider, 1)
        bar.addWidget(self.spin)
        bar.addWidget(self.overlay_n)
        rl.addLayout(bar)
        rl.addWidget(self.stamp)
        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setBackground(theme["surface"])
        self.p_spec = self.glw.addPlot(row=0, col=0)
        self.p_wf = self.glw.addPlot(row=1, col=0)
        self.p_wf.setXLink(self.p_spec)
        style_plot(self.p_spec, theme, "Comprimento de onda (nm)", "Potência")
        style_plot(self.p_wf, theme, "Comprimento de onda (nm)", "Tempo de ensaio (min)")
        self.curve = self.p_spec.plot([], [], pen=pg.mkPen(theme["series"][0], width=1.8))
        self.family = []
        self.img = pg.ImageItem(axisOrder="row-major")
        self.p_wf.addItem(self.img)
        self.cbar = pg.ColorBarItem(values=(-60, -20), colorMap=sequential_cmap(theme), width=14)
        self.cbar.setImageItem(self.img, insert_in=self.p_wf)
        self.marker = pg.InfiniteLine(angle=0, pen=pg.mkPen(theme["ink"], width=1, style=QtCore.Qt.DashLine))
        self.p_wf.addItem(self.marker)
        Crosshair(self.p_spec, theme["ink2"])
        rl.addWidget(self.glw, 1)
        self.metrics = QtWidgets.QLabel("")
        self.metrics.setWordWrap(True)
        rl.addWidget(self.metrics)
        split.addWidget(right)
        split.setSizes([320, 1080])
        self.setCentralWidget(split)

    # ------------------------------------------------------------ abrir
    def open_dialog(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Pasta do ensaio", self.cfg.get("storage.base_dir"))
        if d:
            self.open_test(d)

    def open_test(self, d: str):
        self.test_dir = Path(d)
        self.path_label.setText("  " + str(self.test_dir))
        self.tree.clear()
        cycles = reader.list_cycles(self.test_dir)
        chans = sorted({c for cy in cycles for c in cy["channels"]})
        root_all = QtWidgets.QTreeWidgetItem(["Ensaio completo"])
        for ch in chans:
            it = QtWidgets.QTreeWidgetItem([f"CH{ch} (todos os ciclos)"])
            it.setData(0, QtCore.Qt.UserRole, (None, ch))
            root_all.addChild(it)
        self.tree.addTopLevelItem(root_all)
        for cy in cycles:
            node = QtWidgets.QTreeWidgetItem([cy["name"]])
            for ch in cy["channels"]:
                it = QtWidgets.QTreeWidgetItem([f"CH{ch}"])
                it.setData(0, QtCore.Qt.UserRole, (cy["name"], ch))
                node.addChild(it)
            self.tree.addTopLevelItem(node)
        root_all.setExpanded(True)
        info = reader.test_info(self.test_dir)
        eq = info.get("equipamento", {})
        txt = [f"Status: {info.get('status', '?')}", f"Início: {info.get('inicio', '?')}",
               f"Fim: {info.get('fim', '—')}", f"Amostras: {info.get('amostras_gravadas', '?')}",
               f"Ciclos: {len(cycles)}", f"Equipamento: {eq.get('tipo', '')} SN {eq.get('sn', '')}",
               f"Canais: {eq.get('canais_gravados', '')}"]
        ev = self.test_dir / "eventos.log"
        if ev.exists():
            lac = [ln for ln in ev.read_text(encoding="utf-8", errors="ignore").splitlines() if "\tLACUNA\t" in ln]
            txt.append(f"Lacunas/reconexões registradas: {len(lac)}")
        self.info.setPlainText("\n".join(txt))

    def _load_item(self, item, _col):
        ud = item.data(0, QtCore.Qt.UserRole)
        if not ud:
            return
        cycle, ch = ud
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            if cycle:
                self.data = reader.load_cycle_channel(self.test_dir / cycle, ch)
            else:
                self.data = reader.load_test_channel(self.test_dir, ch)
            self.data["ch"] = ch
            self.data["label"] = f"CH{ch}" + (f" · {cycle}" if cycle else " · ensaio completo")
        except Exception as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "Visualizador", f"Falha ao carregar: {exc}")
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        n = self.data["P"].shape[0]
        if n == 0:
            QtWidgets.QMessageBox.information(self, "Visualizador", "Sem espectros neste arquivo.")
            return
        col = self.theme["series"][(ch - 1) % len(self.theme["series"])]
        self.curve.setPen(pg.mkPen(col, width=1.8))
        self.slider.setRange(0, n - 1)
        self.spin.setRange(0, n - 1)
        self._render_waterfall()
        self.slider.setValue(n - 1)
        self._show_index(n - 1)

    def _render_waterfall(self):
        d = self.data
        P, L = d["P"], d["L"]
        n = P.shape[0]
        step = max(1, n // 1500)
        idx = np.arange(0, n, step)
        axis = L[0] if L.size else np.arange(P.shape[1], dtype=float)
        rows = []
        for i in idx:
            if L.size and not np.array_equal(L[i], axis):
                rows.append(np.interp(axis, L[i], P[i]))
            else:
                rows.append(P[i])
        img = np.vstack(rows).astype(np.float32)
        t = d["t_ensaio"][idx] / 60.0
        self.img.setImage(img, autoLevels=False)
        h = float(t[-1] - t[0]) if len(t) > 1 else 1.0
        self.img.setRect(QtCore.QRectF(float(axis[0]), float(t[0]), float(axis[-1] - axis[0]), max(h, 1e-6)))
        lo, hi = np.nanpercentile(img, [2, 99.8])
        if hi > lo:
            self.cbar.setLevels((float(lo), float(hi)))

    def _show_index(self, i: int):
        d = self.data
        if d is None or i >= d["P"].shape[0]:
            return
        wav = d["L"][i] if d["L"].size else np.arange(d["P"].shape[1], dtype=float)
        pw = d["P"][i]
        self.curve.setData(wav, pw)
        for c in self.family:
            self.p_spec.removeItem(c)
        self.family = []
        k = self.overlay_n.value()
        if k > 0:
            cmap = sequential_cmap(self.theme)
            n = d["P"].shape[0]
            for j, idx in enumerate(np.linspace(0, n - 1, k).astype(int)):
                c = cmap.map(0.25 + 0.75 * j / max(1, k - 1), mode="qcolor")
                w = d["L"][idx] if d["L"].size else wav
                it = self.p_spec.plot(w, d["P"][idx], pen=pg.mkPen(c, width=1.1))
                it.setZValue(-1)
                self.family.append(it)
        a = analyze_spectrum(wav, pw, self.cfg.get("analysis.power_unit", "auto"),
                             self.cfg.get("analysis.prominence_db", 3.0), self.cfg.get("analysis.min_distance_nm", 0.3),
                             16, self.cfg.get("analysis.bw_db", 10.0))
        self.p_spec.setLabel("left", unit_label(a["unit"]), color=self.theme["ink"])
        t = d["t_unix"][i]
        self.marker.setPos(d["t_ensaio"][i] / 60.0)
        rep = " · repetido" if d["repetido"][i] else ""
        self.stamp.setText(f"{d['label']} · amostra {int(d['amostra'][i])} · "
                           f"{time.strftime('%d/%m/%Y %H:%M:%S', time.localtime(t))} · "
                           f"t = {d['t_ensaio'][i] / 60:.2f} min{rep}")
        bw = self.cfg.get("analysis.bw_db", 10.0)
        parts = [f"<b>Pico {k}</b>: λ={p['lambda_max_nm']:.4f} nm, FWHM={p['fwhm_nm']:.4f} nm, "
                 f"BW−{bw:g}dB={p['bw_x_nm']:.4f} nm, sub-picos={p['sub_picos']}"
                 for k, p in enumerate(a["peaks"][:6], 1)]
        self.metrics.setText(" &nbsp;·&nbsp; ".join(parts) or "Nenhum pico detectado.")

    # ------------------------------------------------------------ exportar
    def export_csv(self):
        if self.data is None:
            return
        i = self.slider.value()
        d = self.data
        default = os.path.join(str(self.test_dir), f"CH{d['ch']}_amostra{int(d['amostra'][i])}.csv")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Exportar espectro", default, "CSV (*.csv)")
        if not path:
            return
        F = CsvFormat(self.cfg.get("storage.csv_format", "intl"))
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(F.sep.join(["lambda_nm", "potencia"]) + "\n")
            wav = d["L"][i] if d["L"].size else np.arange(d["P"].shape[1])
            for a, b in zip(wav, d["P"][i]):
                f.write(F.f(a, "%.10g") + F.sep + F.f(b, "%.8g") + "\n")

    def export_fig(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Exportar figura", str(self.test_dir or Path.home()),
                                                        "PNG (*.png);;SVG (*.svg)")
        if not path:
            return
        import pyqtgraph.exporters as ex
        e = ex.SVGExporter(self.glw.ci) if path.lower().endswith(".svg") else ex.ImageExporter(self.glw.ci)
        if not path.lower().endswith(".svg"):
            e.parameters()["width"] = 2400
        e.export(path)
