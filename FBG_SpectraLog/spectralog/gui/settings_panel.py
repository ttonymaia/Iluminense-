"""Painel lateral de configurações (ligado diretamente ao Config)."""
from __future__ import annotations

import math
import os

from PySide6 import QtCore, QtWidgets

from .theme import channel_color
from .widgets import ColorSwatch, hint

RATE_UNITS = [("Hz", "Hz"), ("s", "s (intervalo)"), ("min", "min (intervalo)")]


def rate_to_value(rate_hz: float, unit: str) -> float:
    if unit == "s":
        return 1.0 / rate_hz
    if unit == "min":
        return 1.0 / rate_hz / 60.0
    return rate_hz


def value_to_rate(value: float, unit: str) -> float:
    if unit == "s":
        return 1.0 / max(value, 1e-6)
    if unit == "min":
        return 1.0 / max(value * 60.0, 1e-6)
    return value


def fmt_period(rate_hz: float) -> str:
    p = 1.0 / rate_hz
    if p < 1:
        return f"{p * 1000:.0f} ms"
    if p < 120:
        return f"{p:.3g} s"
    if p < 7200:
        return f"{p / 60:.3g} min"
    return f"{p / 3600:.3g} h"


class SettingsPanel(QtWidgets.QScrollArea):
    changed = QtCore.Signal(str)
    reconnect_clicked = QtCore.Signal()
    optimize_power_clicked = QtCore.Signal()

    def __init__(self, cfg, theme: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.theme = theme
        self.setWidgetResizable(True)
        self.setMinimumWidth(410)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        body = QtWidgets.QWidget()
        self.lay = QtWidgets.QVBoxLayout(body)
        self.lay.setContentsMargins(8, 4, 8, 8)
        self.lay.setSpacing(4)
        self.setWidget(body)
        self._building = True
        self._build_connection()
        self._build_channels()
        self._build_acquisition()
        self._build_storage()
        self._build_analysis()
        self._build_power()
        self.lay.addStretch(1)
        self._building = False
        self._update_rate_labels()
        self._update_mode_widgets()

    # ------------------------------------------------------------ binding
    def _set(self, key: str, value) -> None:
        if self._building:
            return
        self.cfg.set(key, value)
        self.changed.emit(key)

    def _check(self, text: str, key: str, tip: str = "") -> QtWidgets.QCheckBox:
        w = QtWidgets.QCheckBox(text)
        w.setChecked(bool(self.cfg.get(key)))
        w.setToolTip(tip)
        w.toggled.connect(lambda v, k=key: self._set(k, bool(v)))
        return w

    def _dspin(self, key: str, lo: float, hi: float, dec: int = 2, step: float = 1.0, suffix: str = "",
               scale: float = 1.0) -> QtWidgets.QDoubleSpinBox:
        w = QtWidgets.QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setDecimals(dec)
        w.setSingleStep(step)
        w.setSuffix(suffix)
        w.setKeyboardTracking(False)
        w.setValue(float(self.cfg.get(key)) / scale)
        w.valueChanged.connect(lambda v, k=key, s=scale: self._set(k, float(v) * s))
        return w

    def _ispin(self, key: str, lo: int, hi: int, suffix: str = "") -> QtWidgets.QSpinBox:
        w = QtWidgets.QSpinBox()
        w.setRange(lo, hi)
        w.setSuffix(suffix)
        w.setKeyboardTracking(False)
        w.setValue(int(self.cfg.get(key)))
        w.valueChanged.connect(lambda v, k=key: self._set(k, int(v)))
        return w

    def _combo(self, key: str, options: list[tuple]) -> QtWidgets.QComboBox:
        w = QtWidgets.QComboBox()
        cur = self.cfg.get(key)
        for val, label in options:
            w.addItem(label, val)
        for i, (val, _) in enumerate(options):
            if str(val) == str(cur):
                w.setCurrentIndex(i)
        w.currentIndexChanged.connect(lambda i, k=key, ww=w: self._set(k, ww.itemData(i)))
        return w

    def _group(self, title: str) -> QtWidgets.QFormLayout:
        g = QtWidgets.QGroupBox(title)
        f = QtWidgets.QFormLayout(g)
        f.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        f.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        f.setHorizontalSpacing(8)
        f.setVerticalSpacing(6)
        self.lay.addWidget(g)
        return f

    # ------------------------------------------------------------ seções
    def _build_connection(self):
        f = self._group("Conexão com o ILLumiSense")
        self.conn_group = f.parentWidget()
        self.host = QtWidgets.QLineEdit(self.cfg.get("connection.host"))
        self.host.editingFinished.connect(lambda: self._set("connection.host", self.host.text().strip() or "127.0.0.1"))
        f.addRow("Endereço", self.host)
        self.port = self._ispin("connection.port", 1, 65535)
        f.addRow("Porta", self.port)
        f.addRow(self._check("Usar a porta configurada no ILLumiSense", "connection.port_from_illumisense",
                                 "Usa a porta configurada no servidor web embutido do ILLumiSense"))
        svc = QtWidgets.QLineEdit(self.cfg.get("connection.service"))
        svc.editingFinished.connect(lambda: self._set("connection.service", svc.text().strip() or "ILLWS"))
        f.addRow("Serviço", svc)
        f.addRow("Numeração", self._combo("connection.channel_base",
                                          [("auto", "Automática"), (1, "Canais 1–4"), (0, "Canais 0–3")]))
        f.addRow("Tempo limite", self._dspin("connection.timeout_s", 0.2, 30, 1, 0.5, " s"))
        f.addRow(self._check("Conectar automaticamente ao detectar o ILLumiSense", "connection.auto_connect"))
        btn = QtWidgets.QPushButton("Reconectar agora")
        btn.clicked.connect(self.reconnect_clicked)
        f.addRow(btn)

    def _build_channels(self):
        f = self._group("Canais (até 4)")
        self.follow = self._check("Seguir os canais ligados no ILLumiSense", "connection.follow_active_channels",
                                  "Grava exatamente os canais que estão ativos no ILLumiSense")
        self.follow.toggled.connect(lambda _: self._update_channel_enable())
        f.addRow(self.follow)
        self.ch_checks, self.ch_state, self.ch_swatch = [], [], []
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(8)
        enabled = self.cfg.get("channels.enabled")
        for i in range(4):
            sw = ColorSwatch(channel_color(self.theme, i + 1))
            cb = QtWidgets.QCheckBox(f"CH{i + 1}")
            cb.setChecked(bool(enabled[i]))
            cb.toggled.connect(lambda v, k=i: self._set_channel(k, v))
            st = QtWidgets.QLabel("—")
            st.setObjectName("muted")
            grid.addWidget(sw, i, 0)
            grid.addWidget(cb, i, 1)
            grid.addWidget(st, i, 2)
            grid.setColumnStretch(2, 1)
            self.ch_checks.append(cb)
            self.ch_state.append(st)
            self.ch_swatch.append(sw)
        w = QtWidgets.QWidget()
        w.setLayout(grid)
        f.addRow(w)
        self._update_channel_enable()

    def _set_channel(self, i: int, v: bool):
        en = list(self.cfg.get("channels.enabled"))
        en[i] = bool(v)
        self._set("channels.enabled", en)

    def _update_channel_enable(self):
        follow = self.follow.isChecked()
        for cb in self.ch_checks:
            cb.setEnabled(not follow)

    def _build_acquisition(self):
        f = self._group("Aquisição")
        self.acq_group = f.parentWidget()
        row = QtWidgets.QHBoxLayout()
        self.rate_val = QtWidgets.QDoubleSpinBox()
        self.rate_val.setDecimals(4)
        self.rate_val.setKeyboardTracking(False)
        self.rate_unit = QtWidgets.QComboBox()
        for val, label in RATE_UNITS:
            self.rate_unit.addItem(label, val)
        unit = self.cfg.get("acquisition.rate_unit", "Hz")
        self.rate_unit.setCurrentIndex(max(0, [u for u, _ in RATE_UNITS].index(unit) if unit in
                                           [u for u, _ in RATE_UNITS] else 0))
        self._apply_rate_range()
        self.rate_val.setValue(rate_to_value(float(self.cfg.get("acquisition.rate_hz")), unit))
        self.rate_val.valueChanged.connect(self._rate_changed)
        self.rate_unit.currentIndexChanged.connect(self._unit_changed)
        self.rate_unit.setMinimumWidth(130)
        row.addWidget(self.rate_val, 1)
        row.addWidget(self.rate_unit)
        w = QtWidgets.QWidget()
        w.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        f.addRow("Frequência", w)
        self.period_label = hint("")
        f.addRow(self.period_label)
        self.mode = self._combo("acquisition.mode", [("continuous", "Contínuo"),
                                                     ("burst", "Intermitente (rajadas periódicas)")])
        self.mode.currentIndexChanged.connect(lambda _: self._update_mode_widgets())
        f.addRow("Modo", self.mode)
        self.burst_dur = self._dspin("acquisition.burst_duration_s", 0.1, 86400, 1, 1, " s")
        self.burst_per = self._dspin("acquisition.burst_period_s", 0.05, 100000, 2, 1, " min", scale=60.0)
        self.burst_dur_l = QtWidgets.QLabel("Duração da rajada")
        self.burst_per_l = QtWidgets.QLabel("Repetir a cada")
        f.addRow(self.burst_dur_l, self.burst_dur)
        f.addRow(self.burst_per_l, self.burst_per)
        self.save_peaks = self._check("Gravar também os picos do ILLumiSense", "acquisition.save_peaks")
        f.addRow(self.save_peaks)
        self.skip_dup = self._check("Não gravar espectros repetidos", "acquisition.skip_duplicates",
                                    "Se a frequência pedida for maior que a taxa de varredura do ILLumiSense, "
                                    "espectros idênticos ao anterior não são gravados (ficam marcados como repetidos).")
        f.addRow(self.skip_dup)
        f.addRow(self._check("Retomar a gravação sozinho ao reconectar", "acquisition.auto_resume"))
        f.addRow(self._check("Iniciar a gravação assim que conectar", "acquisition.auto_start_on_connect"))
        self.maxrate_label = hint("")
        f.addRow(self.maxrate_label)

    def _apply_rate_range(self):
        u = self.rate_unit.currentData()
        self.rate_val.blockSignals(True)
        if u == "Hz":
            self.rate_val.setRange(0.0001, 200.0)
            self.rate_val.setSingleStep(0.5)
        elif u == "s":
            self.rate_val.setRange(0.005, 86400.0)
            self.rate_val.setSingleStep(1.0)
        else:
            self.rate_val.setRange(0.001, 1440.0)
            self.rate_val.setSingleStep(1.0)
        self.rate_val.blockSignals(False)

    def _unit_changed(self, _):
        rate = float(self.cfg.get("acquisition.rate_hz"))
        u = self.rate_unit.currentData()
        self._apply_rate_range()
        self.rate_val.blockSignals(True)
        self.rate_val.setValue(rate_to_value(rate, u))
        self.rate_val.blockSignals(False)
        self._set("acquisition.rate_unit", u)
        self._update_rate_labels()

    def _rate_changed(self, v):
        rate = value_to_rate(float(v), self.rate_unit.currentData())
        self._set("acquisition.rate_hz", rate)
        self._update_rate_labels()

    def _update_rate_labels(self):
        rate = float(self.cfg.get("acquisition.rate_hz"))
        self.period_label.setText(f"= {rate:.4g} espectros/s por canal · período {fmt_period(rate)}")

    def _update_mode_widgets(self):
        burst = self.mode.currentData() == "burst"
        for w in (self.burst_dur, self.burst_per, self.burst_dur_l, self.burst_per_l):
            w.setVisible(burst)

    def _build_storage(self):
        f = self._group("Gravação em ciclos")
        self.storage_group = f.parentWidget()
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.folder = QtWidgets.QLineEdit(self.cfg.get("storage.base_dir"))
        self.folder.editingFinished.connect(lambda: self._set("storage.base_dir", self.folder.text().strip()))
        b = QtWidgets.QToolButton()
        b.setText("…")
        b.clicked.connect(self._pick_folder)
        row.addWidget(self.folder, 1)
        row.addWidget(b)
        w = QtWidgets.QWidget()
        w.setLayout(row)
        f.addRow("Pasta", w)
        name = QtWidgets.QLineEdit(self.cfg.get("storage.test_name"))
        name.editingFinished.connect(lambda: self._set("storage.test_name", name.text().strip() or "ensaio"))
        self.test_name = name
        f.addRow("Nome do ensaio", name)
        self.cycle_mode = self._combo("storage.cycle_mode", [("time", "Novo arquivo por tempo"),
                                                             ("samples", "Novo arquivo por nº de espectros"),
                                                             ("burst", "Novo arquivo a cada rajada")])
        self.cycle_mode.currentIndexChanged.connect(lambda _: self._update_cycle_widgets())
        f.addRow("Ciclo", self.cycle_mode)
        self.cycle_min = self._dspin("storage.cycle_minutes", 0.1, 100000, 1, 5, " min")
        self.cycle_n = self._ispin("storage.cycle_samples", 1, 100000000, " espectros")
        self.cycle_min_l = QtWidgets.QLabel("Duração do ciclo")
        self.cycle_n_l = QtWidgets.QLabel("Tamanho do ciclo")
        f.addRow(self.cycle_min_l, self.cycle_min)
        f.addRow(self.cycle_n_l, self.cycle_n)
        f.addRow("Gravar no disco", self._dspin("storage.flush_interval_s", 0.2, 60, 1, 0.5, " s"))
        f.addRow("Eixo λ", self._combo("storage.save_wavelength",
                                       [("always", "Em todo espectro (bruto completo)"),
                                        ("on_change", "Somente quando mudar")]))
        f.addRow("Formato CSV", self._combo("storage.csv_format",
                                            [("intl", "Internacional ( , e . )"), ("br", "Excel Brasil ( ; e , )")]))
        f.addRow(self._check("Gravar métricas espectrais (FWHM, centroide…)", "storage.save_metrics"))
        f.addRow(self._check("Compactar ciclos fechados (.csv.gz)", "storage.compress_closed",
                                 "Reduz o espaço ~4x. O ciclo em gravação nunca é compactado."))
        f.addRow("Avisar abaixo de", self._dspin("storage.min_free_gb", 0.1, 10000, 1, 1, " GB livres"))
        f.addRow(hint("Cada ciclo fica em uma pasta própria (ciclo_0001, ciclo_0002, …). Os dados são "
                      "forçados ao disco no intervalo acima: uma queda de energia perde no máximo esse tempo."))
        self._update_cycle_widgets()

    def _update_cycle_widgets(self):
        m = self.cycle_mode.currentData()
        self.cycle_min.setVisible(m == "time")
        self.cycle_min_l.setVisible(m == "time")
        self.cycle_n.setVisible(m == "samples")
        self.cycle_n_l.setVisible(m == "samples")

    def _pick_folder(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Pasta dos ensaios", self.folder.text())
        if d:
            self.folder.setText(os.path.normpath(d))
            self._set("storage.base_dir", self.folder.text())

    def _build_analysis(self):
        f = self._group("Análise espectral")
        f.addRow("Unidade", self._combo("analysis.power_unit", [("auto", "Automática"), ("dB", "dBm (log)"),
                                                                ("linear", "Linear (u.a.)")]))
        f.addRow("Largura extra a", self._dspin("analysis.bw_db", 0.5, 40, 1, 1, " dB"))
        f.addRow("Proeminência mín.", self._dspin("analysis.prominence_db", 0.1, 60, 1, 0.5, " dB"))
        f.addRow("Separação mín.", self._dspin("analysis.min_distance_nm", 0.01, 20, 2, 0.05, " nm"))
        f.addRow(hint("FWHM = largura a −3 dB. A largura extra (ex.: −10 dB) caracteriza FBG chirped. "
                      "Sub-picos > 1 indicam divisão de pico (deformação não uniforme/dano)."))

    def _build_power(self):
        f = self._group("Energia e segurança do ensaio")
        self.keep_awake = self._check("Impedir suspensão do Windows (a tela pode desligar)", "power.keep_awake")
        f.addRow(self.keep_awake)
        f.addRow(self._check("Proteger o ILLumiSense contra economia de energia", "power.protect_illumisense"))
        f.addRow(self._check("Prioridade elevada para a aquisição", "power.high_priority"))
        f.addRow(self._check("Retomar o ensaio se o programa/PC reiniciar", "recovery.auto_resume_after_crash"))
        self.startup = QtWidgets.QCheckBox("Abrir o FBG SpectraLog ao iniciar o Windows")
        self.startup.setVisible(os.name == "nt")
        f.addRow(self.startup)
        b = QtWidgets.QPushButton("Otimizar plano de energia do Windows…")
        b.clicked.connect(self.optimize_power_clicked)
        f.addRow(b)
        self.power_label = hint("")
        f.addRow(self.power_label)

    # ------------------------------------------------------------ estado
    def set_recording(self, rec: bool) -> None:
        self.storage_group.setEnabled(not rec)
        self.save_peaks.setEnabled(not rec)
        self.skip_dup.setEnabled(not rec)

    def update_status(self, st: dict, theme: dict) -> None:
        n = int(st["device"].get("n_canais") or 4) if st["connected"] else 4
        active = st["active"]
        enabled = set(st["enabled"])
        for i in range(4):
            ch = i + 1
            if ch > n:
                txt = "não existe neste equipamento"
            elif not st["connected"]:
                txt = "aguardando conexão"
            elif active is None:
                txt = "estado desconhecido"
            else:
                txt = "ligado no ILLumiSense" if ch in active else "desligado no ILLumiSense"
            if ch in enabled and st["connected"]:
                txt += " · gravando" if st["recording"] else " · selecionado"
            self.ch_state[i].setText(txt)
            if self.follow.isChecked():
                self.ch_checks[i].blockSignals(True)
                self.ch_checks[i].setChecked(ch in enabled)
                self.ch_checks[i].blockSignals(False)
        fetch = st.get("fetch_ms", float("nan"))
        nch = max(1, len(st["enabled"]))
        if st["connected"] and math.isfinite(fetch) and fetch > 0:
            self.maxrate_label.setText(f"Tempo para ler {nch} canal(is): {fetch:.0f} ms → frequência máxima "
                                       f"≈ {1000.0 / fetch:.1f} Hz. Varredura do ILLumiSense: "
                                       f"{st['device'].get('taxa_varredura_hz', '—')} Hz.")
        else:
            self.maxrate_label.setText("")
        self.power_label.setText("Proteção ativa: o sistema não suspende; desligar a tela não afeta o ensaio."
                                 if st["power"] else
                                 ("Proteção desativada." if os.name == "nt" else
                                  "Proteção de energia só é necessária no Windows."))

    def apply_theme(self, theme: dict) -> None:
        self.theme = theme
        for i, sw in enumerate(self.ch_swatch):
            sw.set_color(channel_color(theme, i + 1))
