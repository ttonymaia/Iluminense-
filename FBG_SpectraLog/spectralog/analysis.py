"""Métricas espectrais para FBG uniformes e chirped (caracterização de dano).

Para cada pico detectado no espectro bruto:
  * lambda_max_nm        posição do máximo com interpolação parabólica sub-pixel
  * lambda_centroide_nm  centroide (potência linear) na região acima de -3 dB
  * P_max                potência de pico (na unidade do instrumento)
  * fwhm_nm              largura a -3 dB (meia potência)
  * bw_x_nm              largura a -X dB (configurável; útil para FBG chirped/largas)
  * assimetria           (λ_dir - λ_max)/(λ_max - λ_esq) a -3 dB (1 = simétrico)
  * skewness             terceiro momento padronizado do pico (potência linear)
  * sub_picos            nº de máximos locais dentro da banda -X dB (indicador de
                         divisão de pico / deformação não uniforme / dano)
Comparação com espectro de referência: deslocamento por correlação cruzada (nm),
coeficiente de correlação de Pearson e diferença RMS.
"""
from __future__ import annotations

import math

import numpy as np

try:
    from scipy.signal import find_peaks
except ImportError:  # pragma: no cover
    find_peaks = None

NAN = float("nan")


def detect_unit(pw: np.ndarray, configured: str = "auto") -> str:
    if configured in ("dB", "linear"):
        return configured
    if pw is None or pw.size == 0:
        return "dB"
    finite = pw[np.isfinite(pw)]
    if finite.size == 0:
        return "dB"
    # Espectros em dBm de interrogadores FBG são essencialmente negativos.
    return "dB" if np.median(finite) < 0 else "linear"


def to_linear(pw: np.ndarray, unit: str) -> np.ndarray:
    if unit == "dB":
        return np.power(10.0, np.clip(pw, -200, 100) / 10.0)
    return np.clip(pw, 0, None)


def _local_maxima(y: np.ndarray, prominence: float, distance: int) -> np.ndarray:
    if find_peaks is not None:
        idx, _ = find_peaks(y, prominence=prominence, distance=max(1, distance))
        return idx
    # fallback simples sem scipy
    cand = np.where((y[1:-1] > y[:-2]) & (y[1:-1] >= y[2:]))[0] + 1
    out = []
    for i in cand[np.argsort(-y[cand])]:
        if all(abs(i - j) >= distance for j in out) and y[i] - np.median(y) >= prominence:
            out.append(i)
    return np.array(sorted(out), dtype=int)


def _crossing(x: np.ndarray, y: np.ndarray, i0: int, level: float, step: int) -> tuple[float, int]:
    """Caminha a partir de i0 (step=-1 esquerda, +1 direita) até y < level; interpola."""
    n = y.size
    i = i0
    while 0 <= i + step < n:
        j = i + step
        if y[j] < level:
            y1, y2 = y[i], y[j]
            if y1 == y2:
                return float(x[j]), j
            f = (y1 - level) / (y1 - y2)
            return float(x[i] + f * (x[j] - x[i])), j
        i = j
    return NAN, i


def _parabolic(x: np.ndarray, y: np.ndarray, i: int) -> tuple[float, float]:
    if 0 < i < y.size - 1:
        y0, y1, y2 = y[i - 1], y[i], y[i + 1]
        den = y0 - 2 * y1 + y2
        if den < 0:
            d = 0.5 * (y0 - y2) / den
            d = max(-0.5, min(0.5, d))
            xi = float(np.interp(i + d, np.arange(x.size), x))
            return xi, float(y1 - 0.25 * (y0 - y2) * d)
    return float(x[i]), float(y[i])


def peak_metrics(wav: np.ndarray, pw: np.ndarray, i: int, unit: str, bw_db: float = 10.0,
                 floor: float | None = None) -> dict:
    lam, pmax = _parabolic(wav, pw, i)
    if unit == "dB":
        lvl3 = pw[i] - 3.0
        lvlx = pw[i] - bw_db
    else:
        base = floor if floor is not None else float(np.median(pw))
        amp = pw[i] - base
        lvl3 = base + amp * 10 ** (-3.0 / 10)
        lvlx = base + amp * 10 ** (-bw_db / 10)
    l3, il3 = _crossing(wav, pw, i, lvl3, -1)
    r3, ir3 = _crossing(wav, pw, i, lvl3, +1)
    lx, ilx = _crossing(wav, pw, i, lvlx, -1)
    rx, irx = _crossing(wav, pw, i, lvlx, +1)
    fwhm = r3 - l3 if math.isfinite(l3) and math.isfinite(r3) else NAN
    bwx = rx - lx if math.isfinite(lx) and math.isfinite(rx) else NAN
    asym = (r3 - lam) / (lam - l3) if math.isfinite(fwhm) and lam - l3 > 0 else NAN

    a, b = max(0, il3), min(pw.size, ir3 + 1)
    lin = to_linear(pw[a:b], unit)
    w = wav[a:b]
    if unit != "dB":
        lin = np.clip(pw[a:b] - (floor if floor is not None else float(np.median(pw))), 0, None)
    s = float(lin.sum())
    if s > 0 and w.size >= 3:
        cen = float((w * lin).sum() / s)
        var = float(((w - cen) ** 2 * lin).sum() / s)
        skew = float(((w - cen) ** 3 * lin).sum() / s / var ** 1.5) if var > 0 else NAN
    else:
        cen, skew = lam, NAN

    # sub-picos dentro da banda -X dB (divisão de pico / chirp induzido por dano)
    sub = 1
    ax, bx = max(0, ilx), min(pw.size, irx + 1)
    if bx - ax >= 5:
        seg = pw[ax:bx]
        prom = 0.5 if unit == "dB" else 0.05 * max(1e-12, float(seg.max() - seg.min()))
        sub = max(1, int(_local_maxima(seg, prom, 2).size))
    return {
        "lambda_max_nm": lam, "lambda_centroide_nm": cen, "P_max": pmax,
        "fwhm_nm": fwhm, "bw_x_nm": bwx, "assimetria": asym, "skewness": skew,
        "sub_picos": sub, "indice": int(i),
    }


def analyze_spectrum(wav: np.ndarray, pw: np.ndarray, unit_cfg: str = "auto", prominence_db: float = 3.0,
                     min_distance_nm: float = 0.3, max_peaks: int = 16, bw_db: float = 10.0) -> dict:
    res = {"unit": "dB", "peaks": [], "n_peaks": 0, "centroide_global_nm": NAN,
           "largura_rms_nm": NAN, "potencia_integrada": NAN}
    if wav is None or pw is None or wav.size < 5 or wav.size != pw.size:
        return res
    ok = np.isfinite(wav) & np.isfinite(pw)
    if ok.sum() < 5:
        return res
    wav, pw = wav[ok], pw[ok]
    if wav[0] > wav[-1]:
        wav, pw = wav[::-1], pw[::-1]
    unit = detect_unit(pw, unit_cfg)
    res["unit"] = unit
    step = float(np.median(np.diff(wav))) if wav.size > 1 else 1.0
    dist = int(max(1, round(min_distance_nm / step))) if step > 0 else 1
    floor = float(np.percentile(pw, 20))
    if unit == "dB":
        prom = prominence_db
    else:
        prom = max(1e-12, 0.1 * float(pw.max() - floor))
    idx = _local_maxima(pw, prom, dist)
    if idx.size > max_peaks:
        idx = np.sort(idx[np.argsort(-pw[idx])][:max_peaks])
    res["peaks"] = [peak_metrics(wav, pw, int(i), unit, bw_db, floor) for i in idx]
    res["n_peaks"] = len(res["peaks"])
    lin = to_linear(pw, unit) if unit == "dB" else np.clip(pw - floor, 0, None)
    tot = float(np.trapezoid(lin, wav)) if hasattr(np, "trapezoid") else float(np.trapz(lin, wav))
    if lin.sum() > 0:
        c = float((wav * lin).sum() / lin.sum())
        res["centroide_global_nm"] = c
        res["largura_rms_nm"] = float(np.sqrt(((wav - c) ** 2 * lin).sum() / lin.sum()))
    res["potencia_integrada"] = tot
    return res


def compare_reference(wav: np.ndarray, pw: np.ndarray, ref_wav: np.ndarray, ref_pw: np.ndarray,
                      unit: str = "dB") -> dict:
    """Compara o espectro atual com a referência (ex.: espectro antes do carregamento)."""
    out = {"shift_nm": NAN, "correlacao": NAN, "rms_diff": NAN}
    try:
        if wav.size < 8 or ref_wav.size < 8:
            return out
        lo = max(np.nanmin(wav), np.nanmin(ref_wav))
        hi = min(np.nanmax(wav), np.nanmax(ref_wav))
        if hi <= lo:
            return out
        step = float(np.median(np.abs(np.diff(wav))))
        n = int(min(20000, max(16, (hi - lo) / step)))
        grid = np.linspace(lo, hi, n)
        o1 = np.argsort(wav)
        o2 = np.argsort(ref_wav)
        a = np.interp(grid, wav[o1], pw[o1])
        b = np.interp(grid, ref_wav[o2], ref_pw[o2])
        out["rms_diff"] = float(np.sqrt(np.mean((a - b) ** 2)))
        if np.std(a) > 0 and np.std(b) > 0:
            out["correlacao"] = float(np.corrcoef(a, b)[0, 1])
        la = to_linear(a, unit)
        lb = to_linear(b, unit)
        la = la - la.mean()
        lb = lb - lb.mean()
        nfft = 1 << int(math.ceil(math.log2(2 * n)))
        xc = np.fft.irfft(np.fft.rfft(la, nfft) * np.conj(np.fft.rfft(lb, nfft)), nfft)
        xc = np.concatenate((xc[-(n - 1):], xc[:n]))
        k = int(np.argmax(xc))
        d = 0.0
        if 0 < k < xc.size - 1:
            y0, y1, y2 = xc[k - 1], xc[k], xc[k + 1]
            den = y0 - 2 * y1 + y2
            if den != 0:
                d = 0.5 * (y0 - y2) / den
        lag = (k - (n - 1)) + d
        out["shift_nm"] = float(lag * (grid[1] - grid[0]))
    except Exception:
        pass
    return out
