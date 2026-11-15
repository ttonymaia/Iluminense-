"""Tema visual: interface sóbria de software científico e gráficos em estilo de publicação."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg

LIGHT = {
    "name": "light",
    "page": "#f3f3f1", "panel": "#fbfbfa", "surface": "#fcfcfb", "raised": "#ffffff",
    "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
    "grid": "#e1e0d9", "axis": "#52514e", "border": "#d6d5ce", "hover": "#ebeae5",
    "accent": "#2a78d6", "accent_ink": "#ffffff", "select": "#cde2fb",
    "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
    "ref": "#52514e",
}
DARK = {
    "name": "dark",
    "page": "#121211", "panel": "#1a1a19", "surface": "#1a1a19", "raised": "#232322",
    "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781",
    "grid": "#2c2c2a", "axis": "#c3c2b7", "border": "#383835", "hover": "#2a2a28",
    "accent": "#3987e5", "accent_ink": "#ffffff", "select": "#184f95",
    "series": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#0ca30c", "#9085e9", "#e66767"],
    "ref": "#c3c2b7",
}
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b",
          "idle": "#898781"}

BLUE_RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
             "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]

FONT_FAMILY = "Segoe UI"


def palette(name: str) -> dict:
    return DARK if name == "dark" else LIGHT


def channel_color(theme: dict, ch: int) -> str:
    return theme["series"][(ch - 1) % len(theme["series"])]


def sequential_cmap(theme: dict, name: str = "azul") -> pg.ColorMap:
    if name in ("viridis", "magma", "inferno", "cividis"):
        try:
            return pg.colormap.get(name)
        except Exception:
            pass
    ramp = BLUE_RAMP if theme["name"] == "light" else list(reversed(BLUE_RAMP))
    # o ponto mais baixo recua para a cor da superfície
    colors = [theme["surface"]] + ramp
    pos = np.linspace(0.0, 1.0, len(colors))
    return pg.ColorMap(pos, [pg.mkColor(c) for c in colors])


def qss(t: dict) -> str:
    return f"""
    QWidget {{ font-family: "{FONT_FAMILY}", "Segoe UI", system-ui, sans-serif; font-size: 9pt;
              color: {t['ink']}; }}
    QMainWindow, QDialog {{ background: {t['page']}; }}
    QDockWidget {{ color: {t['ink']}; }}
    QDockWidget::title {{ background: {t['panel']}; padding: 6px 8px; border-bottom: 1px solid {t['border']};
                         font-weight: 600; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background: {t['panel']}; border: none; }}
    QGroupBox {{ background: {t['raised']}; border: 1px solid {t['border']}; border-radius: 6px;
                margin-top: 14px; padding: 10px 8px 8px 8px; font-weight: 600; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {t['ink2']}; }}
    QGroupBox QLabel, QGroupBox QCheckBox {{ font-weight: 400; }}
    QLabel#muted {{ color: {t['muted']}; }}
    QLabel#hint {{ color: {t['ink2']}; font-size: 8pt; }}
    QLabel#h1 {{ font-size: 11pt; font-weight: 600; }}
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QTableWidget, QListWidget,
    QTreeWidget {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 4px;
                  padding: 3px 5px; selection-background-color: {t['select']}; selection-color: {t['ink']}; }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{ background: {t['raised']}; border: 1px solid {t['border']}; }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 1px solid {t['accent']}; }}
    QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ color: {t['muted']}; }}
    QPushButton, QToolButton {{ background: {t['raised']}; border: 1px solid {t['border']}; border-radius: 4px;
                               padding: 5px 10px; }}
    QPushButton:hover, QToolButton:hover {{ background: {t['hover']}; }}
    QPushButton:pressed, QToolButton:pressed {{ background: {t['select']}; }}
    QPushButton:disabled, QToolButton:disabled {{ color: {t['muted']}; }}
    QPushButton#primary, QToolButton#primary {{ background: {t['accent']}; color: {t['accent_ink']};
                                               border: 1px solid {t['accent']}; font-weight: 600; }}
    QPushButton#primary:disabled, QToolButton#primary:disabled {{ background: {t['border']};
                                               border-color: {t['border']}; color: {t['muted']}; }}
    QPushButton#danger, QToolButton#danger {{ color: {STATUS['critical']}; font-weight: 600; }}
    QToolBar {{ background: {t['panel']}; border: none; border-bottom: 1px solid {t['border']}; spacing: 4px;
               padding: 4px; }}
    QTabWidget::pane {{ border: 1px solid {t['border']}; border-radius: 4px; background: {t['surface']};
                       top: -1px; }}
    QTabBar::tab {{ background: {t['panel']}; border: 1px solid {t['border']}; border-bottom: none;
                   padding: 6px 14px; margin-right: 2px; border-top-left-radius: 4px;
                   border-top-right-radius: 4px; color: {t['ink2']}; }}
    QTabBar::tab:selected {{ background: {t['surface']}; color: {t['ink']}; font-weight: 600; }}
    QHeaderView::section {{ background: {t['panel']}; border: none; border-bottom: 1px solid {t['border']};
                           border-right: 1px solid {t['grid']}; padding: 4px; color: {t['ink2']};
                           font-weight: 600; }}
    QTableWidget {{ gridline-color: {t['grid']}; }}
    QStatusBar {{ background: {t['panel']}; border-top: 1px solid {t['border']}; color: {t['ink2']}; }}
    QMenuBar {{ background: {t['panel']}; }}
    QMenuBar::item:selected, QMenu::item:selected {{ background: {t['hover']}; color: {t['ink']}; }}
    QMenu {{ background: {t['raised']}; border: 1px solid {t['border']}; }}
    QSplitter::handle {{ background: {t['border']}; }}
    QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; }}
    QFrame#statusbar {{ background: {t['raised']}; border-bottom: 1px solid {t['border']}; }}
    QFrame#pill {{ background: {t['panel']}; border: 1px solid {t['border']}; border-radius: 12px; }}
    QSlider::groove:horizontal {{ height: 4px; background: {t['border']}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ width: 14px; margin: -6px 0; border-radius: 7px; background: {t['accent']}; }}
    """


def style_plot(plot: pg.PlotItem, t: dict, xlabel: str = "", ylabel: str = "", title: str | None = None) -> None:
    """Estilo de figura científica: moldura completa, ticks para dentro, grade discreta."""
    plot.getViewBox().setBackgroundColor(t["surface"])
    axis_pen = pg.mkPen(t["axis"], width=1)
    font = pg.QtGui.QFont(FONT_FAMILY, 9)
    for name in ("left", "bottom", "top", "right"):
        plot.showAxis(name)
        ax = plot.getAxis(name)
        ax.setPen(axis_pen)
        ax.setTextPen(pg.mkPen(t["ink2"]))
        ax.setStyle(tickLength=-6, tickFont=font, autoExpandTextSpace=True)  # negativo = para dentro
        ax.enableAutoSIPrefix(False)  # nunca "(x0.001)" nos eixos: unidades explícitas
        if name in ("top", "right"):
            ax.setStyle(showValues=False)
    plot.showGrid(x=True, y=True, alpha=0.18 if t["name"] == "light" else 0.25)
    lab = {"color": t["ink"], "font-size": "10pt"}
    if xlabel:
        plot.setLabel("bottom", xlabel, **lab)
    if ylabel:
        plot.setLabel("left", ylabel, **lab)
    if title is not None:
        plot.setTitle(title, color=t["ink"], size="10pt")


def apply_pg_defaults(t: dict) -> None:
    pg.setConfigOptions(antialias=True, background=t["surface"], foreground=t["ink2"], useOpenGL=False)
