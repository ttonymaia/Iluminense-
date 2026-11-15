"""Leitura dos ensaios gravados e conversão para HDF5 / MATLAB (.mat).

Exemplo (Python):
    from spectralog.reader import load_test_channel
    d = load_test_channel(r"C:\\...\\2026-11-15_101500_ensaio_FBG", ch=2)
    d["P"]        # matriz (n_espectros x n_pontos) de potência
    d["L"]        # matriz (n_espectros x n_pontos) de comprimento de onda (nm)
    d["t_ensaio"] # tempo de cada espectro (s desde o início)
"""
from __future__ import annotations

import gzip
import io
import json
import re
from pathlib import Path

import numpy as np


def _open_text(path: Path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return open(path, "r", encoding="utf-8", newline="")


def _find(cycle_dir: Path, name: str) -> Path | None:
    for cand in (cycle_dir / name, cycle_dir / (name + ".gz")):
        if cand.exists():
            return cand
    return None


def load_matrix(path: Path) -> tuple[list[str], np.ndarray]:
    """Lê um CSV numérico do SpectraLog (formato internacional ou brasileiro)."""
    with _open_text(path) as f:
        header = f.readline().rstrip("\r\n")
        sep = ";" if header.count(";") > header.count(",") else ","
        cols = header.split(sep)
        body = f.read()
    if not body.strip():
        return cols, np.empty((0, len(cols)))
    if sep == ";":
        body = body.replace(",", ".")
    # linha final incompleta (queda de energia durante a escrita) é descartada
    if not body.endswith("\n"):
        body = body[: body.rfind("\n") + 1]
    rows = []
    ncols = len(cols)
    try:
        arr = np.loadtxt(io.StringIO(body), delimiter=sep, ndmin=2)
        return cols, arr
    except ValueError:
        for line in body.splitlines():
            parts = line.split(sep)
            if len(parts) != ncols:
                continue
            try:
                rows.append([float(x) if x not in ("", "nan") else np.nan for x in parts])
            except ValueError:
                continue
        return cols, np.asarray(rows, dtype=float).reshape(-1, ncols)


def list_cycles(test_dir: str | Path) -> list[dict]:
    test_dir = Path(test_dir)
    out = []
    for d in sorted(test_dir.glob("ciclo_*")):
        if not re.match(r"ciclo_\d+$", d.name):
            continue
        chans = sorted({int(m.group(1)) for p in d.iterdir()
                        if (m := re.match(r"CH(\d)_potencia\.csv(\.gz)?$", p.name))})
        out.append({"name": d.name, "path": d, "channels": chans})
    return out


def test_info(test_dir: str | Path) -> dict:
    p = Path(test_dir) / "ensaio_info.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_cycle_channel(cycle_dir: str | Path, ch: int) -> dict:
    cycle_dir = Path(cycle_dir)
    pf = _find(cycle_dir, f"CH{ch}_potencia.csv")
    if pf is None:
        raise FileNotFoundError(f"CH{ch}_potencia.csv não encontrado em {cycle_dir}")
    _, P = load_matrix(pf)
    lf = _find(cycle_dir, f"CH{ch}_lambda.csv")
    Lraw = np.empty((0, 0))
    if lf is not None:
        _, Lraw = load_matrix(lf)
    meta_p = 5
    meta_l = 4
    amostra = P[:, 0].astype(np.int64) if P.size else np.empty(0, dtype=np.int64)
    res = {
        "amostra": amostra,
        "t_unix": P[:, 1] if P.size else np.empty(0),
        "t_ensaio": P[:, 2] if P.size else np.empty(0),
        "rajada": P[:, 3] if P.size else np.empty(0),
        "repetido": P[:, 4] if P.size else np.empty(0),
        "P": P[:, meta_p:] if P.size else np.empty((0, 0)),
    }
    # eixo λ: uma linha por espectro (modo "always") ou apenas quando muda ("on_change")
    if Lraw.size:
        la = Lraw[:, 0].astype(np.int64)
        Lv = Lraw[:, meta_l:]
        if la.size == amostra.size and np.array_equal(la, amostra):
            res["L"] = Lv
        else:
            idx = np.searchsorted(la, amostra, side="right") - 1
            idx = np.clip(idx, 0, la.size - 1)
            res["L"] = Lv[idx]
    else:
        res["L"] = np.empty((0, 0))
    return res


def load_test_channel(test_dir: str | Path, ch: int, cycles: list[str] | None = None) -> dict:
    parts = []
    for c in list_cycles(test_dir):
        if cycles and c["name"] not in cycles:
            continue
        if ch in c["channels"]:
            parts.append(load_cycle_channel(c["path"], ch))
    if not parts:
        raise FileNotFoundError(f"Nenhum dado do CH{ch} em {test_dir}")
    n = {p["P"].shape[1] for p in parts if p["P"].size}
    if len(n) > 1:
        raise ValueError("O número de pontos do espectro mudou entre ciclos; carregue os ciclos separadamente.")
    out = {}
    for k in parts[0]:
        arrs = [p[k] for p in parts if p[k].size]
        out[k] = np.concatenate(arrs, axis=0) if arrs else parts[0][k]
    return out


def load_peaks(cycle_dir: str | Path) -> tuple[list[str], np.ndarray]:
    f = _find(Path(cycle_dir), "picos.csv")
    return load_matrix(f) if f else ([], np.empty((0, 7)))


def export_hdf5(test_dir: str | Path, out_path: str | Path | None = None, progress=None) -> Path:
    import h5py

    test_dir = Path(test_dir)
    out_path = Path(out_path) if out_path else test_dir / (test_dir.name + ".h5")
    cycles = list_cycles(test_dir)
    with h5py.File(out_path, "w") as h:
        h.attrs["origem"] = "FBG SpectraLog"
        h.attrs["ensaio_info"] = json.dumps(test_info(test_dir), ensure_ascii=False, default=str)
        for i, c in enumerate(cycles):
            g = h.create_group(c["name"])
            for ch in c["channels"]:
                d = load_cycle_channel(c["path"], ch)
                gc = g.create_group(f"CH{ch}")
                for k, v in d.items():
                    if v.size:
                        gc.create_dataset(k, data=v, compression="gzip", compression_opts=4, shuffle=True)
                gc.attrs["descricao"] = "P: potência (n_espectros x n_pontos); L: comprimento de onda em nm"
            cols, pk = load_peaks(c["path"])
            if pk.size:
                ds = g.create_dataset("picos_illumisense", data=pk, compression="gzip")
                ds.attrs["colunas"] = ",".join(cols)
            if progress:
                progress(i + 1, len(cycles))
    return out_path


def export_mat(test_dir: str | Path, out_dir: str | Path | None = None, progress=None) -> list[Path]:
    from scipy.io import savemat

    test_dir = Path(test_dir)
    out_dir = Path(out_dir) if out_dir else test_dir / "matlab"
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    cycles = list_cycles(test_dir)
    for i, c in enumerate(cycles):
        m = {}
        for ch in c["channels"]:
            d = load_cycle_channel(c["path"], ch)
            m[f"CH{ch}"] = {k: v for k, v in d.items() if v.size}
        cols, pk = load_peaks(c["path"])
        if pk.size:
            m["picos_illumisense"] = pk
            m["picos_colunas"] = np.array(cols, dtype=object)
        p = out_dir / f"{c['name']}.mat"
        savemat(p, m, do_compression=True, long_field_names=True)
        files.append(p)
        if progress:
            progress(i + 1, len(cycles))
    return files
