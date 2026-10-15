"""Modelo fisico da FBG: matriz de transferencia (T-matrix) com perfil de deformacao.

Referencia: T. Erdogan, "Fiber grating spectra", J. Lightwave Technol. 15(8), 1997.
A rede e dividida em M secoes uniformes; em cada secao o comprimento de onda de
Bragg local e

    lambda_B(z) = lambda_B0 * [1 + (1 - p_e) * eps(z)]

onde p_e ~ 0,22 e o coeficiente fotoelastico efetivo da silica. Uma deformacao
que varia ao longo da rede (gradiente) produz um chirp: cada trecho reflete em
um lambda diferente e o pico alarga, fica assimetrico ou se divide.

Observacao importante: a refletancia |r|^2 de uma estrutura sem perdas e igual
quando iluminada pelos dois lados, logo o SINAL do gradiente (qual extremidade
esta mais tracionada) nao pode ser obtido so pelo espectro de reflexao.
"""

from __future__ import annotations

import numpy as np

P_E = 0.22          # coeficiente fotoelastico efetivo (silica)
N_EFF = 1.447       # indice efetivo tipico (SMF-28 @ 1550 nm)


def sensibilidade_pm_por_ue(lambda_nm: float, p_e: float = P_E) -> float:
    """Sensibilidade a deformacao em pm/microstrain (aprox. 1,2 pm/ue em 1550 nm)."""
    return lambda_nm * (1.0 - p_e) * 1e-3


def perfil_apodizacao(n_secoes: int, tipo: str = "uniforme", fwhm_frac: float = 0.5) -> np.ndarray | None:
    """Apodizacao da modulacao de indice: 'uniforme' ou 'gaussiana' (FWHM = fwhm_frac * L)."""
    if tipo in (None, "", "uniforme"):
        return None
    z = (np.arange(n_secoes) + 0.5) / n_secoes - 0.5
    if tipo == "gaussiana":
        return np.exp(-4 * np.log(2) * (z / fwhm_frac) ** 2)
    raise ValueError(f"apodizacao desconhecida: {tipo}")


def perfil_deformacao(z: np.ndarray, eps_media_ue: float, gradiente_ue_mm: float = 0.0,
                      curvatura_ue_mm2: float = 0.0) -> np.ndarray:
    """eps(z) em microstrain; z em mm medido a partir do centro da rede."""
    return eps_media_ue + gradiente_ue_mm * z + curvatura_ue_mm2 * (z ** 2 - np.mean(z ** 2))


def refletancia_tmm(lam_nm: np.ndarray, lambda_b0_nm: float, comprimento_mm: float,
                    dn_ac: float, eps_ue: np.ndarray | float = 0.0, apod: np.ndarray | None = None,
                    n_eff: float = N_EFF, p_e: float = P_E, n_secoes: int | None = None) -> np.ndarray:
    """Refletancia |r|^2 da FBG pela matriz de transferencia.

    lam_nm         : vetor de comprimentos de onda (nm)
    lambda_b0_nm   : lambda de Bragg sem deformacao
    comprimento_mm : comprimento da rede
    dn_ac          : modulacao do indice (amplitude; kappa = pi*dn_ac/lambda)
    eps_ue         : deformacao em cada secao (microstrain); escalar = uniforme
    apod           : perfil de apodizacao (0..1) por secao; None = uniforme
    """
    lam = np.asarray(lam_nm, dtype=float) * 1e-9
    eps = np.atleast_1d(np.asarray(eps_ue, dtype=float))
    if n_secoes is None:
        n_secoes = len(eps) if eps.size > 1 else 1
    if eps.size == 1:
        eps = np.full(n_secoes, eps[0])
    if apod is None:
        apod = np.ones(n_secoes)
    dz = comprimento_mm * 1e-3 / n_secoes
    lam_b = lambda_b0_nm * 1e-9 * (1.0 + (1.0 - p_e) * eps * 1e-6)

    t11 = np.ones_like(lam, dtype=complex)
    t12 = np.zeros_like(lam, dtype=complex)
    t21 = np.zeros_like(lam, dtype=complex)
    t22 = np.ones_like(lam, dtype=complex)
    # percorre da extremidade distante (secao M) ate a de entrada (secao 1)
    for k in range(n_secoes - 1, -1, -1):
        sig = 2 * np.pi * n_eff * (1.0 / lam - 1.0 / lam_b[k])
        kap = np.pi * dn_ac * apod[k] / lam
        gam = np.sqrt(kap ** 2 - sig ** 2 + 0j)
        ch = np.cosh(gam * dz)
        # sinh(g dz)/g com limite seguro quando g -> 0
        with np.errstate(invalid="ignore", divide="ignore"):
            shg = np.where(np.abs(gam) > 1e-12, np.sinh(gam * dz) / gam, dz)
        f11 = ch - 1j * sig * shg
        f12 = -1j * kap * shg
        f21 = 1j * kap * shg
        f22 = ch + 1j * sig * shg
        n11 = f11 * t11 + f12 * t21
        n12 = f11 * t12 + f12 * t22
        n21 = f21 * t11 + f22 * t21
        n22 = f21 * t12 + f22 * t22
        t11, t12, t21, t22 = n11, n12, n21, n22
    r = t21 / t11
    return np.abs(r) ** 2


def convolucao_gaussiana(lam_nm: np.ndarray, y: np.ndarray, fwhm_nm: float) -> np.ndarray:
    """Convolui y (em grade uniforme) com uma gaussiana de largura FWHM (resposta do instrumento)."""
    if fwhm_nm <= 0:
        return y
    dl = lam_nm[1] - lam_nm[0]
    s = fwhm_nm / 2.354820045 / dl
    half = int(np.ceil(4 * s))
    k = np.exp(-0.5 * (np.arange(-half, half + 1) / s) ** 2)
    k /= k.sum()
    ypad = np.pad(y, half, mode="edge")
    return np.convolve(ypad, k, mode="valid")


def espectro_medido(pix_nm: np.ndarray, lambda_b0_nm: float, comprimento_mm: float, dn_ac: float,
                    eps_ue: np.ndarray | float, fwhm_instr_nm: float, passo_fino_nm: float = 0.002,
                    margem_nm: float = 3.0, **kw) -> np.ndarray:
    """Refletancia vista pelo espectrometro: TMM -> convolucao (PSF + pixel) -> amostragem nos pixels."""
    eps_arr = np.atleast_1d(eps_ue)
    lam_c = lambda_b0_nm * (1 + (1 - P_E) * float(np.mean(eps_arr)) * 1e-6)
    larg = (1 - P_E) * lambda_b0_nm * 1e-6 * (float(np.ptp(eps_arr)) if eps_arr.size > 1 else 0.0)
    lo = max(pix_nm.min(), lam_c - margem_nm - larg)
    hi = min(pix_nm.max(), lam_c + margem_nm + larg)
    out = np.zeros_like(pix_nm, dtype=float)
    if hi <= lo:
        return out
    fino = np.arange(lo - 1.0, hi + 1.0, passo_fino_nm)
    r = refletancia_tmm(fino, lambda_b0_nm, comprimento_mm, dn_ac, eps_ue, **kw)
    pix_larg = float(np.median(np.abs(np.diff(pix_nm))))
    # resposta do pixel (janela retangular) aproximada como gaussiana equivalente
    fwhm_tot = np.sqrt(fwhm_instr_nm ** 2 + (0.89 * pix_larg) ** 2)
    rc = convolucao_gaussiana(fino, r, fwhm_tot)
    sel = (pix_nm >= fino[0]) & (pix_nm <= fino[-1])
    out[sel] = np.interp(pix_nm[sel], fino, rc)
    return out
