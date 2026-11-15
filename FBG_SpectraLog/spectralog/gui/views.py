"""Vistas gráficas: espectro ao vivo, cascata (espectrograma) e evolução temporal."""
from __future__ import annotations

import collections

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from .theme import channel_color, sequential_cmap, style_plot
from .widgets import Crosshair

NAN = float("nan")


def unit_label(unit: str) -> str:
    return "Potência (dBm)" if unit == "dB" else "Potência (u.a.)"


# ============================================================================ espectro
class SpectrumView(QtWidgets.QWidget):
    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.unit = "dB"
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        bar = QtWidgets.QHBoxLayout()
        self.mode = QtWidgets.QComboBox()
        self.mode.addItem("Canais sobrepostos", "overlay")
        self.mode.addItem("Grade 2 × 2", "grid")
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.chk_ill = QtWidgets.QCheckBox("Picos do ILLumiSense")
        self.chk_ill.setChecked(True)
        self.chk_det = QtWidgets.QCheckBox("Picos detectados")
        self.chk_ref = QtWidgets.QCheckBox("Referência")
        self.chk_ref.setChecked(True)
        self.chk_ref.toggled.connect(self.refresh_legend)
        self.chk_auto = QtWidgets.QCheckBox("Autoescala")
        self.chk_auto.setChecked(True)
        self.chk_auto.toggled.connect(self._autoscale)
        self.chk_freeze = QtWidgets.QCheckBox("Congelar tela")
        for w in (QtWidgets.QLabel("Exibição"), self.mode, self.chk_ill, self.chk_det, self.chk_ref,
                  self.chk_auto, self.chk_freeze):
            bar.addWidget(w)
        self.info = QtWidgets.QLabel("")
        self.info.setObjectName("muted")
        self.info.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.info.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        bar.addWidget(self.info, 1)
        lay.addLayout(bar)

        self.stack = QtWidgets.QStackedWidget()
        self.glw_over = pg.GraphicsLayoutWidget()
        self.glw_grid = pg.GraphicsLayoutWidget()
        self.stack.addWidget(self.glw_over)
        self.stack.addWidget(self.glw_grid)
        lay.addWidget(self.stack, 1)

        self.p_over = self.glw_over.addPlot()
        self.legend = self.p_over.addLegend(offset=(-10, 10), labelTextSize="9pt")
        self.p_grid = []
        for i in range(4):
            p = self.glw_grid.addPlot(row=i // 2, col=i % 2)
            self.p_grid.append(p)
        for p in self.p_grid[1:]:
            p.setXLink(self.p_grid[0])
        self.cross_over = Crosshair(self.p_over, theme["ink2"])
        self.cross_grid = [Crosshair(p, theme["ink2"]) for p in self.p_grid]
        self.items_over: dict = {}
        self.items_grid: dict = {}
        self._build_items()
        self.apply_theme(theme)

    def _build_items(self):
        for ch in range(1, 5):
            self.items_over[ch] = self._make_items(self.p_over, ch, legend=True)
            self.items_grid[ch] = self._make_items(self.p_grid[ch - 1], ch, legend=False)

    def _make_items(self, plot, ch, legend):
        c = channel_color(self.theme, ch)
        curve = plot.plot([], [], pen=pg.mkPen(c, width=1.6), name=f"CH{ch}" if legend else None)
        curve.setClipToView(True)
        ref = plot.plot([], [], pen=pg.mkPen(c, width=1.2, style=QtCore.Qt.DashLine))
        ref.setZValue(-1)
        ill = pg.ScatterPlotItem(size=9, symbol="t", pen=pg.mkPen(self.theme["surface"], width=1.5),
                                 brush=pg.mkBrush(c))
        det = pg.ScatterPlotItem(size=8, symbol="o", pen=pg.mkPen(c, width=1.5), brush=pg.mkBrush(None))
        plot.addItem(ill)
        plot.addItem(det)
        return {"curve": curve, "ref": ref, "ill": ill, "det": det}

    def apply_theme(self, theme: dict):
        self.theme = theme
        self._legend_chs = None
        self.glw_over.setBackground(theme["surface"])
        self.glw_grid.setBackground(theme["surface"])
        style_plot(self.p_over, theme, "Comprimento de onda (nm)", unit_label(self.unit))
        for i, p in enumerate(self.p_grid):
            style_plot(p, theme, "Comprimento de onda (nm)", unit_label(self.unit), title=f"CH{i + 1}")
        self.legend.setLabelTextColor(theme["ink"])
        self.legend.setBrush(pg.mkBrush(theme["surface"] + "e6"))
        self.legend.setPen(pg.mkPen(theme["border"]))
        for items in (self.items_over, self.items_grid):
            for ch, it in items.items():
                c = channel_color(theme, ch)
                it["curve"].setPen(pg.mkPen(c, width=1.6))
                it["ref"].setPen(pg.mkPen(c, width=1.2, style=QtCore.Qt.DashLine))
                it["ill"].setPen(pg.mkPen(theme["surface"], width=1.5))
                it["ill"].setBrush(pg.mkBrush(c))
                it["det"].setPen(pg.mkPen(c, width=1.5))
        for cr in [self.cross_over] + self.cross_grid:
            cr.set_color(theme["ink2"])

    def refresh_legend(self, *_):
        self._legend_chs = None

    def _mode_changed(self, _):
        self.stack.setCurrentIndex(0 if self.mode.currentData() == "overlay" else 1)

    def _autoscale(self, on):
        for p in [self.p_over] + self.p_grid:
            p.enableAutoRange(enable=on)

    def set_unit(self, unit: str):
        if unit != self.unit:
            self.unit = unit
            lab = {"color": self.theme["ink"], "font-size": "10pt"}
            for p in [self.p_over] + self.p_grid:
                p.setLabel("left", unit_label(unit), **lab)
            for cr in [self.cross_over] + self.cross_grid:
                cr.fmt = "λ = {x:.4f} nm   P = {y:.2f} " + ("dBm" if unit == "dB" else "u.a.")

    def update_data(self, sample, analysis: dict, reference: dict):
        if self.chk_freeze.isChecked() or sample is None:
            return
        overlay = self.mode.currentData() == "overlay"
        items = self.items_over if overlay else self.items_grid
        other = self.items_grid if overlay else self.items_over
        for it in other.values():  # limpa a vista oculta para não gastar memória de GPU
            if it["curve"].xData is not None and len(it["curve"].xData):
                for k in ("curve", "ref"):
                    it[k].setData([], [])
                it["ill"].setData([], [])
                it["det"].setData([], [])
        shown = []
        for ch in range(1, 5):
            it = items[ch]
            fr = sample.frames.get(ch)
            if fr is None or not fr.ok:
                it["curve"].setData([], [])
                it["ill"].setData([], [])
                it["det"].setData([], [])
                it["ref"].setData([], [])
                continue
            shown.append(ch)
            it["curve"].setData(fr.wav, fr.pw)
            if self.chk_ill.isChecked() and fr.peaks_wav is not None and fr.peaks_wav.size:
                m = fr.peaks_wav > 0
                it["ill"].setData(fr.peaks_wav[m], fr.peaks_pow[m])
            else:
                it["ill"].setData([], [])
            a = analysis.get(ch)
            if self.chk_det.isChecked() and a and a.get("peaks"):
                it["det"].setData([p["lambda_max_nm"] for p in a["peaks"]], [p["P_max"] for p in a["peaks"]])
            else:
                it["det"].setData([], [])
            ref = reference.get(ch)
            if self.chk_ref.isChecked() and ref is not None:
                it["ref"].setData(ref[0], ref[1])
            else:
                it["ref"].setData([], [])
            if a:
                self.set_unit(a.get("unit", self.unit))
        if overlay and shown != getattr(self, "_legend_chs", None):
            self._legend_chs = shown
            self.legend.clear()
            for ch in shown:
                self.legend.addItem(self.items_over[ch]["curve"], f"CH{ch}")
            if any(ch in reference for ch in shown) and self.chk_ref.isChecked():
                self.legend.addItem(pg.PlotDataItem(pen=pg.mkPen(self.theme["ink2"], width=1.2,
                                                                 style=QtCore.Qt.DashLine)), "referência")
        npts = max((sample.frames[c].pw.size for c in shown), default=0)
        self.info.setText(f"Espectro nº {sample.seq} · {npts} pontos · aquisição {sample.acq_ms:.0f} ms"
                          + (" · GRAVADO" if sample.recorded else " · prévia"))

    def plot_items(self):
        return [self.p_over] if self.mode.currentData() == "overlay" else self.p_grid

    def export_target(self):
        return self.glw_over.ci if self.mode.currentData() == "overlay" else self.glw_grid.ci


# ============================================================================ cascata
class WaterfallView(QtWidgets.QWidget):
    def __init__(self, theme: dict, maxlen: int = 600, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.buf: collections.deque = collections.deque(maxlen=maxlen)
        self.axis_ref = None
        self.dirty = False
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        bar = QtWidgets.QHBoxLayout()
        self.ch = QtWidgets.QComboBox()
        for c in range(1, 5):
            self.ch.addItem(f"CH{c}", c)
        self.ch.currentIndexChanged.connect(self.clear)
        self.cmap = QtWidgets.QComboBox()
        for name, label in (("azul", "Azul sequencial"), ("viridis", "Viridis"), ("magma", "Magma"),
                            ("cividis", "Cividis")):
            self.cmap.addItem(label, name)
        self.cmap.currentIndexChanged.connect(lambda _: self.apply_theme(self.theme))
        self.nrows = QtWidgets.QSpinBox()
        self.nrows.setRange(20, 5000)
        self.nrows.setValue(maxlen)
        self.nrows.setSuffix(" espectros")
        self.nrows.valueChanged.connect(self._resize)
        self.only_rec = QtWidgets.QCheckBox("Somente espectros gravados")
        btn = QtWidgets.QPushButton("Limpar")
        btn.clicked.connect(self.clear)
        for w in (QtWidgets.QLabel("Canal"), self.ch, QtWidgets.QLabel("Mapa de cores"), self.cmap,
                  QtWidgets.QLabel("Histórico"), self.nrows, self.only_rec, btn):
            bar.addWidget(w)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.glw = pg.GraphicsLayoutWidget()
        lay.addWidget(self.glw, 1)
        self.plot = self.glw.addPlot()
        self.img = pg.ImageItem(axisOrder="row-major")
        self.plot.addItem(self.img)
        self.bar = pg.ColorBarItem(values=(-60, -20), colorMap=sequential_cmap(theme), interactive=True, width=14)
        self.bar.setImageItem(self.img, insert_in=self.plot)
        self.cross = Crosshair(self.plot, theme["ink2"], fmt="λ = {x:.4f} nm   t = {y:.1f} s")
        self.apply_theme(theme)
        self._auto_levels = True

    def apply_theme(self, theme: dict):
        self.theme = theme
        self.glw.setBackground(theme["surface"])
        style_plot(self.plot, theme, "Comprimento de onda (nm)", "Tempo relativo ao espectro mais recente (s)")
        self.bar.setColorMap(sequential_cmap(theme, self.cmap.currentData()))
        try:
            self.bar.axis.setPen(pg.mkPen(theme["axis"]))
            self.bar.axis.setTextPen(pg.mkPen(theme["ink2"]))
        except Exception:
            pass
        self.cross.set_color(theme["ink2"])

    def _resize(self, n):
        self.buf = collections.deque(list(self.buf)[-n:], maxlen=n)
        self.dirty = True

    def clear(self, *_):
        self.buf.clear()
        self.axis_ref = None
        self.img.clear()
        self._auto_levels = True

    def push(self, sample):
        if sample is None:
            return
        if self.only_rec.isChecked() and not sample.recorded:
            return
        fr = sample.frames.get(self.ch.currentData())
        if fr is None or not fr.ok or fr.pw.size < 2:
            return
        if self.axis_ref is None or self.axis_ref.size != fr.wav.size:
            self.axis_ref = fr.wav.copy()
            self.buf.clear()
        pw = fr.pw
        if not np.array_equal(fr.wav, self.axis_ref):
            pw = np.interp(self.axis_ref, fr.wav, fr.pw)
        self.buf.append((sample.t_unix, pw.astype(np.float32)))
        self.dirty = True

    def render(self, unit: str):
        if not self.dirty or not self.buf or self.axis_ref is None:
            return
        self.dirty = False
        t = np.array([b[0] for b in self.buf])
        data = np.vstack([b[1] for b in self.buf])
        x0, x1 = float(self.axis_ref[0]), float(self.axis_ref[-1])
        tr = t - t[-1]
        y0 = float(tr[0]) if len(tr) > 1 else -1.0
        h = max(1e-3, -y0)
        self.img.setImage(data, autoLevels=False)
        self.img.setRect(QtCore.QRectF(x0, y0, x1 - x0, h))
        if self._auto_levels or len(self.buf) < 5:
            lo, hi = np.nanpercentile(data, [2, 99.8])
            if hi > lo:
                self.bar.setLevels((float(lo), float(hi)))
            self._auto_levels = len(self.buf) < 5
        lab = "Potência (dBm)" if unit == "dB" else "Potência (u.a.)"
        if lab != getattr(self, "_bar_label", None):
            self.bar.axis.setLabel(lab)
            self._bar_label = lab


# ============================================================================ evolução
QUANTS = [("pmax", "Potência de pico"), ("shift", "Δλ por correlação c/ referência (pm)"),
          ("corr", "Correlação c/ referência (ρ)"), ("npk", "Nº de picos detectados"),
          ("lam_own", "λ máx. detectado — deslocamento (pm)")]


class EvolutionView(QtWidgets.QWidget):
    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self.theme = theme
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        bar = QtWidgets.QHBoxLayout()
        self.ch = QtWidgets.QComboBox()
        for c in range(1, 5):
            self.ch.addItem(f"CH{c}", c)
        self.q3 = QtWidgets.QComboBox()
        for k, label in QUANTS:
            self.q3.addItem(label, k)
        self.rel = QtWidgets.QCheckBox("Δλ relativo ao início")
        self.rel.setChecked(True)
        self.info = QtWidgets.QLabel("")
        self.info.setObjectName("muted")
        self.info.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.info.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        for w in (QtWidgets.QLabel("Canal"), self.ch, QtWidgets.QLabel("3º gráfico"), self.q3, self.rel):
            bar.addWidget(w)
        bar.addWidget(self.info, 1)
        lay.addLayout(bar)
        self.glw = pg.GraphicsLayoutWidget()
        lay.addWidget(self.glw, 1)
        self.p1 = self.glw.addPlot(row=0, col=0)
        self.p2 = self.glw.addPlot(row=1, col=0)
        self.p3 = self.glw.addPlot(row=2, col=0)
        self.p2.setXLink(self.p1)
        self.p3.setXLink(self.p1)
        self.leg = self.p1.addLegend(offset=(-10, 10), labelTextSize="8pt")
        self.curves: dict = {}
        self.apply_theme(theme)

    def apply_theme(self, theme: dict):
        self.theme = theme
        self.glw.setBackground(theme["surface"])
        self._label_axes("min")
        self.leg.setLabelTextColor(theme["ink"])
        self.leg.setBrush(pg.mkBrush(theme["surface"] + "e6"))
        self.leg.setPen(pg.mkPen(theme["border"]))
        for (plot, k), c in self.curves.items():
            col = theme["series"][k % len(theme["series"])]
            c.setPen(pg.mkPen(col, width=1.6))

    def _label_axes(self, tunit: str):
        q = dict(QUANTS)[self.q3.currentData()]
        style_plot(self.p1, self.theme, "", "Δλ Bragg (pm)" if self.rel.isChecked() else "λ Bragg (nm)",
                   title="Comprimento de onda de Bragg (picos do ILLumiSense)")
        style_plot(self.p2, self.theme, "", "FWHM (nm)", title="Largura a −3 dB")
        style_plot(self.p3, self.theme, f"Tempo de ensaio ({tunit})", q, title=q)

    def _curve(self, plot, k: int, name: str | None):
        key = (id(plot), k)
        c = self.curves.get(key)
        if c is None:
            col = self.theme["series"][k % len(self.theme["series"])]
            c = plot.plot([], [], pen=pg.mkPen(col, width=1.6), name=name)
            self.curves[key] = c
        return c

    def render(self, hist: list, t0: float | None, unit: str):
        ch = self.ch.currentData()
        pts = [p for p in hist if ch in p["ch"]]
        if not pts:
            self.info.setText("Sem dados para este canal ainda.")
            for c in self.curves.values():
                c.setData([], [])
            return
        t = np.array([p["t"] for p in pts])
        t_ref = t0 if t0 else t[0]
        span = t[-1] - t_ref
        tunit, div = ("s", 1.0) if span < 300 else (("min", 60.0) if span < 3 * 3600 else ("h", 3600.0))
        x = (t - t_ref) / div
        self._label_axes(tunit)
        if self.q3.currentData() == "pmax":
            self.p3.setLabel("left", "Potência de pico (dBm)" if unit == "dB" else "Potência de pico (u.a.)",
                             color=self.theme["ink"])

        def matrix(key):
            rows = [p["ch"][ch][key] if p["ch"][ch][key] is not None else np.empty(0) for p in pts]
            n = max((r.size for r in rows), default=0)
            m = np.full((len(rows), n), np.nan)
            for i, r in enumerate(rows):
                m[i, :r.size] = r
            return m

        ill = matrix("ill")
        if ill.shape[1] == 0:
            ill = matrix("lam")
        fw = matrix("fwhm")
        nmax = min(8, max(ill.shape[1], fw.shape[1]))
        used = set()
        for k in range(nmax):
            if k < ill.shape[1]:
                y = ill[:, k]
                if self.rel.isChecked():
                    first = y[np.isfinite(y)]
                    y = (y - first[0]) * 1000.0 if first.size else y
                self._curve(self.p1, k, f"FBG {k + 1}").setData(x, y, connect="finite")
                used.add((id(self.p1), k))
            if k < fw.shape[1]:
                self._curve(self.p2, k, None).setData(x, fw[:, k], connect="finite")
                used.add((id(self.p2), k))
        q = self.q3.currentData()
        if q in ("pmax", "lam_own"):
            m = matrix("pmax" if q == "pmax" else "lam")
            for k in range(min(8, m.shape[1])):
                y = m[:, k]
                if q == "lam_own":
                    first = y[np.isfinite(y)]
                    y = (y - first[0]) * 1000.0 if first.size else y
                self._curve(self.p3, k, None).setData(x, y, connect="finite")
                used.add((id(self.p3), k))
        else:
            if q == "npk":
                y = np.array([float(np.isfinite(p["ch"][ch]["lam"]).sum()) for p in pts])
            else:
                y = np.array([p["ch"][ch][q] for p in pts], dtype=float)
                if q == "shift":
                    y = y * 1000.0
            self._curve(self.p3, 0, None).setData(x, y, connect="finite")
            used.add((id(self.p3), 0))
        for key, c in self.curves.items():
            if key not in used:
                c.setData([], [])
        self.info.setText(f"{len(pts)} pontos · {span / 60:.1f} min")
