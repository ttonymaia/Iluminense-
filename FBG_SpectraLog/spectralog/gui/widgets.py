"""Widgets reutilizáveis."""
from __future__ import annotations

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from .theme import STATUS


class Led(QtWidgets.QWidget):
    def __init__(self, parent=None, size: int = 10):
        super().__init__(parent)
        self._color = QtGui.QColor(STATUS["idle"])
        self.setFixedSize(size, size)

    def set_color(self, color: str) -> None:
        self._color = QtGui.QColor(color)
        self.update()

    def paintEvent(self, _):  # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(self._color)
        r = self.rect().adjusted(1, 1, -1, -1)
        p.drawEllipse(r)


class StatusPill(QtWidgets.QFrame):
    """Indicador de estado: LED + título + valor (o estado nunca é comunicado só pela cor)."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("pill")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 12, 4)
        lay.setSpacing(7)
        self.led = Led(self)
        self.title = QtWidgets.QLabel(title)
        self.title.setObjectName("muted")
        self.value = QtWidgets.QLabel("—")
        f = self.value.font()
        f.setBold(True)
        self.value.setFont(f)
        lay.addWidget(self.led)
        lay.addWidget(self.title)
        lay.addWidget(self.value)

    def set(self, state: str, text: str, tooltip: str = "") -> None:
        self.led.set_color(STATUS.get(state, STATUS["idle"]))
        self.value.setText(text)
        self.setToolTip(tooltip)


class ColorSwatch(QtWidgets.QWidget):
    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.color = QtGui.QColor(color)
        self.setFixedSize(22, 10)

    def set_color(self, color: str) -> None:
        self.color = QtGui.QColor(color)
        self.update()

    def paintEvent(self, _):  # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(self.color)
        p.drawRoundedRect(QtCore.QRectF(0, 3, 22, 4), 2, 2)


def hline() -> QtWidgets.QFrame:
    f = QtWidgets.QFrame()
    f.setFrameShape(QtWidgets.QFrame.HLine)
    f.setFrameShadow(QtWidgets.QFrame.Plain)
    f.setStyleSheet("color: rgba(128,128,128,60);")
    return f


def hint(text: str) -> QtWidgets.QLabel:
    lab = QtWidgets.QLabel(text)
    lab.setObjectName("hint")
    lab.setWordWrap(True)
    return lab


class Crosshair:
    """Cursor em cruz com leitura (λ, P) para gráficos de espectro."""

    def __init__(self, plot: pg.PlotItem, color: str, fmt="λ = {x:.4f} nm   P = {y:.2f}"):
        self.plot = plot
        self.fmt = fmt
        pen = pg.mkPen(color, width=1, style=QtCore.Qt.DashLine)
        self.v = pg.InfiniteLine(angle=90, movable=False, pen=pen)
        self.h = pg.InfiniteLine(angle=0, movable=False, pen=pen)
        self.label = pg.TextItem("", anchor=(0, 1), color=color)
        for it in (self.v, self.h, self.label):
            it.setZValue(50)
            plot.addItem(it, ignoreBounds=True)
            it.hide()
        self.proxy = pg.SignalProxy(plot.scene().sigMouseMoved, rateLimit=40, slot=self._moved)

    def set_color(self, color: str) -> None:
        pen = pg.mkPen(color, width=1, style=QtCore.Qt.DashLine)
        self.v.setPen(pen)
        self.h.setPen(pen)
        self.label.setColor(color)

    def _moved(self, evt):
        pos = evt[0]
        vb = self.plot.getViewBox()
        if self.plot.sceneBoundingRect().contains(pos) and vb.sceneBoundingRect().contains(pos):
            p = vb.mapSceneToView(pos)
            self.v.setPos(p.x())
            self.h.setPos(p.y())
            self.label.setText(self.fmt.format(x=p.x(), y=p.y()))
            self.label.setPos(p.x(), p.y())
            for it in (self.v, self.h, self.label):
                it.show()
        else:
            for it in (self.v, self.h, self.label):
                it.hide()


def fmt_duration(seconds: float) -> str:
    if seconds != seconds or seconds < 0:
        return "—"
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return (f"{d}d " if d else "") + f"{h:02d}:{m:02d}:{s:02d}"


def fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", ".")


class FlowLayout(QtWidgets.QLayout):
    """Layout que quebra linha quando falta largura (indicadores de estado em telas pequenas)."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items: list = []
        self._sp = spacing

    def addItem(self, item):  # noqa: N802
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return QtCore.Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, w):  # noqa: N802
        return self._do(QtCore.QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):  # noqa: N802
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        s = QtCore.QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QtCore.QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do(self, rect, test):
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = r.x(), r.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            nx = x + hint.width() + self._sp
            if nx - self._sp > r.right() and line_h > 0:
                x = r.x()
                y += line_h + self._sp
                nx = x + hint.width() + self._sp
                line_h = 0
            if not test:
                it.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), hint))
            x = nx
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()
