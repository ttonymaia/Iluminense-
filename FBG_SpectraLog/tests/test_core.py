"""Testes do núcleo (sem interface gráfica). Rode com:  python -m pytest -q"""
import gzip
import math
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from spectralog.analysis import analyze_spectrum, compare_reference  # noqa: E402
from spectralog.config import Config  # noqa: E402
from spectralog.engine import ChannelFrame, Engine, Sample, Schedule  # noqa: E402
from spectralog.illws_client import (ILLWSClient, ILLWSConnectionError, interpret_active_channels,  # noqa: E402
                                     parse_labview_json)
from spectralog import reader  # noqa: E402
from spectralog.simulator import Simulator  # noqa: E402
from spectralog.storage import TestWriter  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "cfg"))


# ----------------------------------------------------------------- cliente
def test_parse_labview_json_tolerates_inf_nan():
    o = parse_labview_json('{"Error":0,"SpecPow":[-Inf,1.5,NaN,inf],"TypeHW":"info"}')
    assert o["Error"] == 0 and o["TypeHW"] == "info"
    v = o["SpecPow"]
    assert v[0] == -math.inf and v[1] == 1.5 and math.isnan(v[2]) and v[3] == math.inf


@pytest.mark.parametrize("raw,n,base,expected", [
    ([1, 0, 1, 1], 4, 1, [1, 3, 4]),         # vetor de flags
    ([1, 1, 1, 1], 4, 1, [1, 2, 3, 4]),
    ([1, 3], 4, 1, [1, 3]),                   # lista de canais (base 1)
    ([0, 2], 4, 0, [1, 3]),                   # lista de canais (base 0)
    ([1, 2, 3, 4], 4, 1, [1, 2, 3, 4]),
    ([], 4, 1, []),
])
def test_interpret_active_channels(raw, n, base, expected):
    assert interpret_active_channels(raw, n, base) == expected


def test_client_against_simulator():
    sim = Simulator(port=0, n_points=512).start()
    try:
        c = ILLWSClient("127.0.0.1", sim.port)
        hw = c.get_hw_information()
        assert hw["n_channels"] == 4 and "FBG-scan" in hw["type"]
        wav, pw = c.get_spectrum(1)
        assert wav.size == pw.size == 512 and 1509 < wav[0] < wav[-1] < 1591
        pk, pp = c.get_peaks(1)
        assert pk.size == 3
        assert c.get_spectrum(4)[1].size == 0     # CH4 inativo no simulador
    finally:
        sim.stop()
    with pytest.raises(ILLWSConnectionError):
        ILLWSClient("127.0.0.1", sim.port, timeout=0.5).get_hw_information()


# ----------------------------------------------------------------- agenda
def test_schedule_continuous_no_drift():
    s = Schedule(100.0, 4.0)
    assert s.next_tick(100.0) == (100.0, None)
    t, _ = s.next_tick(100.0 + 1000.1)
    assert abs(t - (100.0 + 1000.25)) < 1e-9


def test_schedule_burst():
    s = Schedule(0.0, 2.0, "burst", burst_duration_s=1.0, burst_period_s=10.0)
    assert s.next_tick(0.0) == (0.0, 1)
    assert s.next_tick(0.2) == (0.5, 1)
    assert s.next_tick(0.7) == (10.0, 2)      # fora da janela -> próxima rajada
    assert s.next_tick(10.4) == (10.5, 2)


# ----------------------------------------------------------------- análise
def _gauss_db(w, c, fwhm, peak_db=-25, floor_db=-62):
    lin = 10 ** (peak_db / 10) * np.exp(-0.5 * ((w - c) / (fwhm / 2.3548)) ** 2) + 10 ** (floor_db / 10)
    return 10 * np.log10(lin)


def test_fwhm_and_peak_accuracy():
    w = np.linspace(1540, 1560, 2001)
    pw = _gauss_db(w, 1550.0123, 0.20)
    a = analyze_spectrum(w, pw)
    assert a["unit"] == "dB" and a["n_peaks"] == 1
    p = a["peaks"][0]
    assert abs(p["lambda_max_nm"] - 1550.0123) < 0.002
    assert abs(p["fwhm_nm"] - 0.20) / 0.20 < 0.02
    assert p["sub_picos"] == 1


def test_peak_split_detected():
    w = np.linspace(1560, 1580, 4001)
    lin = (10 ** (-25 / 10) * np.exp(-0.5 * ((w - 1569.8) / 0.1) ** 2)
           + 10 ** (-26 / 10) * np.exp(-0.5 * ((w - 1570.2) / 0.1) ** 2) + 1e-6)
    a = analyze_spectrum(w, 10 * np.log10(lin), prominence_db=3, min_distance_nm=0.05)
    assert a["n_peaks"] == 2


def test_reference_shift():
    w = np.linspace(1540, 1560, 4001)
    ref = _gauss_db(w, 1550.0, 0.25)
    cur = _gauss_db(w, 1550.137, 0.25)
    r = compare_reference(w, cur, w, ref, "dB")
    assert abs(r["shift_nm"] - 0.137) < 0.003
    assert r["correlacao"] > 0.3


# ----------------------------------------------------------------- gravação
def _cfg(tmp_path, **storage):
    cfg = Config(tmp_path / "c.json")
    cfg.set("storage.base_dir", str(tmp_path / "ens"))
    for k, v in storage.items():
        cfg.set(f"storage.{k}", v)
    return cfg


def _sample(i, t, chans=(1, 2), n=64, shift=0.0):
    w = np.linspace(1510, 1590, n) + shift
    frames = {}
    for ch in chans:
        pw = -60 + 30 * np.exp(-0.5 * ((w - 1550 - ch) / 0.2) ** 2) + 0.001 * i
        frames[ch] = ChannelFrame(ch, t, w, pw, np.array([1550.0 + ch]), np.array([-30.0]))
    return Sample(i, i, t, 0.0, frames, 5.0, True)


@pytest.mark.parametrize("fmt,lam", [("intl", "always"), ("br", "on_change")])
def test_writer_roundtrip_and_cycles(tmp_path, fmt, lam):
    cfg = _cfg(tmp_path, csv_format=fmt, save_wavelength=lam, cycle_mode="samples", cycle_samples=7,
               flush_interval_s=0.2)
    d = tmp_path / "ensaio"
    t0 = time.time()
    w = TestWriter(d, cfg.snapshot(), {"tipo": "teste"}, lambda *a: None, t0)
    sent = []
    for i in range(1, 21):
        s = _sample(i, t0 + i * 0.1, shift=0.01 if i > 10 else 0.0)
        sent.append(s)
        w.put(s)
    w.close()
    cycles = reader.list_cycles(d)
    assert [c["name"] for c in cycles] == ["ciclo_0001", "ciclo_0002", "ciclo_0003"]
    data = reader.load_test_channel(d, 2)
    assert data["P"].shape == (20, 64)
    assert np.array_equal(data["amostra"], np.arange(1, 21))
    for i, s in enumerate(sent):
        np.testing.assert_allclose(data["P"][i], s.frames[2].pw, rtol=1e-7, atol=1e-6)
        np.testing.assert_allclose(data["L"][i], s.frames[2].wav, rtol=0, atol=1e-6)
    info = reader.test_info(d)
    assert info["status"] == "finalizado" and info["amostras_gravadas"] == 20


def test_writer_resume_and_truncated_line(tmp_path):
    cfg = _cfg(tmp_path, cycle_mode="time", cycle_minutes=60)
    d = tmp_path / "ensaio"
    t0 = time.time()
    w = TestWriter(d, cfg.snapshot(), {}, lambda *a: None, t0)
    for i in range(1, 6):
        w.put(_sample(i, t0 + i))
    w.close("interrompido")
    # simula queda de energia no meio da escrita de uma linha
    p = d / "ciclo_0001" / "CH1_potencia.csv"
    with open(p, "a", encoding="utf-8") as f:
        f.write("6,123.4,5.0,,0,-60.1,-60.2")
    w2 = TestWriter(d, cfg.snapshot(), {}, lambda *a: None, time.time(), resume=True)
    assert w2.last_sample_index == 5 and abs(w2.t0 - t0) < 1e-6
    for i in range(6, 9):
        w2.put(_sample(i, t0 + i))
    w2.close()
    data = reader.load_test_channel(d, 1)
    assert list(data["amostra"]) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert len(reader.list_cycles(d)) == 2


def test_compressed_cycles_readable(tmp_path):
    cfg = _cfg(tmp_path, cycle_mode="samples", cycle_samples=3, compress_closed=True)
    d = tmp_path / "ensaio"
    t0 = time.time()
    w = TestWriter(d, cfg.snapshot(), {}, lambda *a: None, t0)
    for i in range(1, 8):
        w.put(_sample(i, t0 + i))
    w.close()
    deadline = time.time() + 10
    while time.time() < deadline and not list((d / "ciclo_0001").glob("*.csv.gz")):
        time.sleep(0.1)
    time.sleep(0.5)
    assert list((d / "ciclo_0001").glob("CH1_potencia.csv.gz"))
    with gzip.open(d / "ciclo_0001" / "CH1_potencia.csv.gz", "rt") as f:
        assert f.readline().startswith("amostra,")
    data = reader.load_test_channel(d, 1)
    assert data["P"].shape[0] == 7


# ----------------------------------------------------------------- ponta a ponta
def test_engine_end_to_end_with_outage(tmp_path):
    sim = Simulator(port=0, n_points=256, scan_rate=50).start()
    port = sim.port
    cfg = _cfg(tmp_path, cycle_mode="samples", cycle_samples=15, flush_interval_s=0.2)
    cfg.set("connection.port", port)
    cfg.set("connection.port_from_illumisense", False)
    cfg.set("connection.detect_process", False)
    cfg.set("connection.monitor_interval_s", 0.3)
    cfg.set("connection.timeout_s", 1.0)
    cfg.set("acquisition.rate_hz", 10.0)
    cfg.set("power.keep_awake", False)
    eng = Engine(cfg)
    eng.start()
    try:
        t = time.time()
        while not eng.connected and time.time() - t < 5:
            time.sleep(0.05)
        assert eng.connected and eng.enabled_channels() == [1, 2, 3]
        d = eng.start_recording(name="e2e")
        time.sleep(2.0)
        sim.stop()                                  # ILLumiSense "fecha"
        time.sleep(1.5)
        assert not eng.connected
        n_during = eng.rec_index
        sim = Simulator(port=port, n_points=256, scan_rate=50).start()   # e reabre
        t = time.time()
        while not eng.connected and time.time() - t < 6:
            time.sleep(0.05)
        assert eng.connected
        time.sleep(1.5)
        assert eng.rec_index > n_during + 5         # gravação retomada sozinha
        eng.stop_recording()
    finally:
        eng.shutdown()
        sim.stop()
    data = reader.load_test_channel(d, 2)
    assert data["P"].shape[0] == eng.rec_index and data["P"].shape[1] == 256
    log = (Path(d) / "eventos.log").read_text(encoding="utf-8")
    assert "LACUNA" in log and "retomada" in log
    dt = np.diff(data["t_ensaio"])
    assert np.median(dt) == pytest.approx(0.1, abs=0.01)


def test_engine_burst_mode(tmp_path):
    sim = Simulator(port=0, n_points=128).start()
    cfg = _cfg(tmp_path, cycle_mode="burst")
    cfg.set("connection.port", sim.port)
    cfg.set("connection.port_from_illumisense", False)
    cfg.set("connection.detect_process", False)
    cfg.set("acquisition.mode", "burst")
    cfg.set("acquisition.rate_hz", 10.0)
    cfg.set("acquisition.burst_duration_s", 0.5)
    cfg.set("acquisition.burst_period_s", 1.5)
    cfg.set("power.keep_awake", False)
    eng = Engine(cfg)
    eng.start()
    try:
        t = time.time()
        while not eng.connected and time.time() - t < 5:
            time.sleep(0.05)
        d = eng.start_recording(name="rajadas")
        time.sleep(3.6)
        eng.stop_recording()
    finally:
        eng.shutdown()
        sim.stop()
    cyc = reader.list_cycles(d)
    assert len(cyc) == 3                              # uma pasta por rajada
    data = reader.load_cycle_channel(cyc[0]["path"], 1)
    assert 4 <= data["P"].shape[0] <= 5               # 0.5 s a 10 Hz
    assert set(np.unique(data["rajada"])) == {1.0}
