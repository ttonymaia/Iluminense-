"""Analise do espectro bruto: metricas do pico e estimativa do chirp.

Com apenas o comprimento de onda de pico (o que o ILLumiSense entrega) so se
mede a deformacao MEDIA na rede. Com o espectro completo da para medir tambem:

  * FWHM e largura a 20 %       -> alargamento do pico (chirp / nao uniformidade)
  * lambda_cog - lambda_max     -> assimetria (gradiente nao linear, flexao)
  * assimetria estatistica      -> 3o momento do pico
  * numero de subpicos          -> divisao do pico (deformacao em degrau, descolamento)
  * ajuste "referencia * retangulo":
        S(l) = a * [R_ref (*) rect_W](l - delta) + b
    Aproximacao de rede fraca: um gradiente LINEAR de deformacao espalha os
    lambdas de Bragg uniformemente numa faixa W; o espectro medido e o espectro
    de referencia (sem carga, ja convoluido com a resposta do instrumento)
    convoluido com um retangulo de largura W.
        delta -> deformacao media
        W     -> diferenca de deformacao entre as pontas da rede (Delta eps)
        W/L   -> gradiente (ue/mm)
  * distribuicao de deformacao (deconvolucao NNLS regularizada): histograma de
    quanto da rede esta em cada nivel de deformacao, sem impor perfil linear.

Limitacao: o espectrometro tem ~0,17 nm/pixel e resolucao optica da ordem de
0,2-0,3 nm. Chirps bem menores que isso so aparecem como pequeno aumento de
FWHM; use media de varios espectros e compare sempre com a referencia.
"""

from __future__ import annotations

import time
import warnings
from dataclasses import dataclass, asdict

import numpy as np
from scipy import interpolate, optimize, signal, special

from . import fisica


@dataclass
class JanelaFBG:
    nome: str
    centro_nm: float
    meia_largura_nm: float = 1.5
    comprimento_mm: float = 10.0
    rastrear: bool = True             # recentraliza a janela no pico atual
    referencia_temperatura: bool = False
    apodizacao: str = "uniforme"      # 'uniforme' ou 'gaussiana' (usado no ajuste T-matrix)

    @classmethod
    def de_dict(cls, d: dict) -> "JanelaFBG":
        mapa = {"lambda_nominal": "centro_nm", "janela_nm": None}
        kw = {}
        for k, v in d.items():
            if k == "janela_nm":
                kw["meia_largura_nm"] = float(v) / 2
            elif k in mapa and mapa[k]:
                kw[mapa[k]] = v
            elif k in cls.__dataclass_fields__:
                kw[k] = v
        return cls(**kw)


# ----------------------------------------------------------------------------- utilitarios

def linearizar(pw: np.ndarray, escala: str = "auto") -> np.ndarray:
    """Converte dB(m) -> linear se necessario. O ILLumiSense mostra 'Power [%]' (linear)."""
    pw = np.asarray(pw, float)
    if escala == "linear":
        return pw
    if escala == "db" or (escala == "auto" and np.nanmedian(pw) < 0 and np.nanmax(pw) <= 20):
        return 10 ** (pw / 10)
    return pw


def recortar(wl: np.ndarray, pw: np.ndarray, centro: float, meia: float):
    sel = (wl >= centro - meia) & (wl <= centro + meia)
    return wl[sel], pw[sel]


def linha_base(y: np.ndarray, frac_borda: float = 0.15) -> float:
    n = max(2, int(len(y) * frac_borda))
    return float(np.median(np.concatenate([y[:n], y[-n:]])))


def detectar_picos(wl: np.ndarray, pw: np.ndarray, prominencia_rel: float = 0.15,
                   dist_min_nm: float = 1.0) -> np.ndarray:
    """Picos (lambda, nm) com proeminencia acima de uma fracao do maior pico."""
    y = linearizar(pw)
    y = y - np.percentile(y, 10)
    passo = float(np.median(np.diff(wl)))
    pk, _ = signal.find_peaks(y, prominence=prominencia_rel * np.max(y),
                              distance=max(1, int(dist_min_nm / passo)))
    return wl[pk]


def centro_gravidade(x: np.ndarray, yb: np.ndarray, limiar: float = 0.5, passo_nm: float = 0.001) -> float:
    """CoG da regiao contigua ao maximo acima de limiar*maximo, sobre interpolacao PCHIP."""
    if len(x) < 3:
        return float(x[int(np.argmax(yb))])
    xf = np.arange(x[0], x[-1], passo_nm)
    yf = interpolate.PchipInterpolator(x, yb)(xf)
    i = int(np.argmax(yf))
    nivel = limiar * yf[i]
    e = i
    while e > 0 and yf[e - 1] >= nivel:
        e -= 1
    d = i
    while d < len(yf) - 1 and yf[d + 1] >= nivel:
        d += 1
    w = yf[e:d + 1] - nivel
    return float(np.sum(xf[e:d + 1] * w) / np.sum(w)) if np.sum(w) > 0 else float(xf[i])


def _cruzamento(x0, x1, y0, y1, nivel):
    if y1 == y0:
        return 0.5 * (x0 + x1)
    return x0 + (nivel - y0) * (x1 - x0) / (y1 - y0)


def largura_no_nivel(x: np.ndarray, y: np.ndarray, i_max: int, frac: float):
    """Largura do pico na fracao 'frac' da altura (interpolacao linear); retorna (larg, esq, dir)."""
    nivel = frac * y[i_max]
    i = i_max
    while i > 0 and y[i] > nivel:
        i -= 1
    if y[i] > nivel:
        return np.nan, np.nan, np.nan
    xe = _cruzamento(x[i], x[i + 1], y[i], y[i + 1], nivel)
    j = i_max
    while j < len(y) - 1 and y[j] > nivel:
        j += 1
    if y[j] > nivel:
        return np.nan, np.nan, np.nan
    xd = _cruzamento(x[j - 1], x[j], y[j - 1], y[j], nivel)
    return xd - xe, xe, xd


# ----------------------------------------------------------------------------- metricas

def metricas_pico(wl: np.ndarray, pw: np.ndarray, centro: float, meia: float,
                  limiar_cog: float = 0.2, escala: str = "auto") -> dict:
    """Metricas de um pico de FBG dentro da janela [centro - meia, centro + meia]."""
    nan = float("nan")
    out = dict(lambda_max=nan, lambda_cog=nan, lambda_centro=nan, lambda_gauss=nan, pot_max=nan, base=nan, fwhm=nan,
               w20=nan, assim_cog_meio=nan, razao_lados=nan, assimetria=nan, n_subpicos=0,
               separacao_subpicos_nm=nan, razao_subpicos=nan, area=nan,
               snr=nan)
    x, y = recortar(wl, linearizar(pw, escala), centro, meia)
    if len(x) < 5:
        return out
    b = linha_base(y)
    yb = y - b
    i = int(np.argmax(yb))
    if yb[i] <= 0:
        return out
    ruido = float(np.std(np.concatenate([yb[:max(2, len(yb) // 7)], yb[-max(2, len(yb) // 7):]])))
    out["base"], out["pot_max"] = b, float(yb[i])
    out["snr"] = float(yb[i] / ruido) if ruido > 0 else np.inf
    # maximo com interpolacao parabolica de 3 pontos
    if 0 < i < len(x) - 1:
        y0, y1, y2 = yb[i - 1], yb[i], yb[i + 1]
        den = y0 - 2 * y1 + y2
        d = 0.5 * (y0 - y2) / den if den != 0 else 0.0
        d = float(np.clip(d, -0.5, 0.5))
        out["lambda_max"] = float(np.interp(i + d, np.arange(len(x)), x))
    else:
        out["lambda_max"] = float(x[i])
    # centro de gravidade acima do limiar, calculado sobre o perfil interpolado (passo de 1 pm).
    # Com o pico ocupando ~2 pixels, o CoG direto nos pixels "trava" no centro do pixel.
    out["lambda_cog"] = centro_gravidade(x, yb, limiar_cog)
    # centro de TODA a feicao (todos os pixels acima de 10 %, mesmo separados): quando o pico se
    # divide (fissura), o CoG pula entre os subpicos; este valor fica no meio e e usado para
    # rastrear a janela
    mt = yb >= 0.1 * yb[i]
    out["lambda_centro"] = float(np.sum(x[mt] * yb[mt]) / np.sum(yb[mt]))
    # larguras
    fwhm, xe, xd = largura_no_nivel(x, yb, i, 0.5)
    out["fwhm"] = fwhm
    out["w20"] = largura_no_nivel(x, yb, i, 0.2)[0]
    if np.isfinite(fwhm):
        lm = out["lambda_cog"]
        esq, dir_ = lm - xe, xd - lm
        out["razao_lados"] = float(dir_ / esq) if esq > 0 else nan
    # assimetria: CoG (limiar baixo, pega a cauda) - ponto medio da FWHM. Menos sensivel ao "travamento"
    # no pixel do que usar o maximo do pico.
    if np.isfinite(fwhm):
        out["assim_cog_meio"] = out["lambda_cog"] - 0.5 * (xe + xd)
    # momentos (regiao acima de 10 %)
    m10 = yb >= 0.1 * yb[i]
    pesos = np.clip(yb[m10], 0, None)
    if pesos.sum() > 0:
        mu = np.sum(x[m10] * pesos) / pesos.sum()
        var = np.sum((x[m10] - mu) ** 2 * pesos) / pesos.sum()
        if var > 0:
            out["assimetria"] = float(np.sum((x[m10] - mu) ** 3 * pesos) / pesos.sum() / var ** 1.5)
    out["area"] = float(np.trapezoid(np.clip(yb, 0, None), x)) if hasattr(np, "trapezoid") \
        else float(np.trapz(np.clip(yb, 0, None), x))
    # subpicos
    pk, _ = signal.find_peaks(yb, height=0.3 * yb[i], prominence=0.1 * yb[i])
    out["n_subpicos"] = int(len(pk))
    if len(pk) >= 2:
        # dois maiores subpicos: a separacao e o salto de deformacao entre os trechos da rede
        # (assinatura tipica de fissura cruzando a FBG ou de descolamento parcial)
        dois = pk[np.argsort(yb[pk])[-2:]]
        pos = []
        for j in dois:
            if 0 < j < len(x) - 1:
                y0, y1, y2 = yb[j - 1], yb[j], yb[j + 1]
                den = y0 - 2 * y1 + y2
                d = float(np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5)) if den != 0 else 0.0
                pos.append(float(np.interp(j + d, np.arange(len(x)), x)))
            else:
                pos.append(float(x[j]))
        out["separacao_subpicos_nm"] = abs(pos[1] - pos[0])
        out["razao_subpicos"] = float(yb[dois[0]] / yb[dois[1]])
    # ajuste gaussiano (referencia de comparacao)
    try:
        sel = yb >= 0.2 * yb[i]
        p0 = [yb[i], out["lambda_max"], (fwhm if np.isfinite(fwhm) else 0.3) / 2.3548]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", optimize.OptimizeWarning)
            popt, _ = optimize.curve_fit(lambda xx, a, mu, s: a * np.exp(-0.5 * ((xx - mu) / s) ** 2),
                                         x[sel], yb[sel], p0=p0, maxfev=2000)
        out["lambda_gauss"] = float(popt[1])
    except Exception:  # noqa: BLE001
        pass
    return out


# ----------------------------------------------------------------------------- chirp

class PerfilReferencia:
    """Perfil do pico sem carga, em grade fina, com integral acumulada para convolucao rapida."""

    def __init__(self, wl: np.ndarray, pw: np.ndarray, centro: float, meia: float,
                 passo_nm: float = 0.002, escala: str = "auto"):
        x, y = recortar(wl, linearizar(pw, escala), centro, meia)
        if len(x) < 5:
            raise ValueError("janela de referencia sem pontos suficientes")
        b = linha_base(y)
        yb = np.clip(y - b, 0, None)
        i = int(np.argmax(yb))
        # zera fora do pico principal (evita levar ruido de borda para o modelo)
        lim = 0.02 * yb[i]
        e = i
        while e > 0 and yb[e] > lim:
            e -= 1
        d = i
        while d < len(yb) - 1 and yb[d] > lim:
            d += 1
        yb[:e] = 0
        yb[d + 1:] = 0
        self.altura = float(yb[i])
        self.centro = float(np.sum(x * yb) / np.sum(yb))
        self.x = np.arange(x[0] - 5.0, x[-1] + 5.0, passo_nm)
        self.y = np.interp(self.x, x, yb, left=0.0, right=0.0)
        self.c = np.concatenate([[0.0], np.cumsum(0.5 * (self.y[1:] + self.y[:-1]) * passo_nm)])
        self.passo = passo_nm

    def convoluido(self, lam: np.ndarray, delta: float, largura: float) -> np.ndarray:
        """[R_ref (*) rect_W](lam - delta), normalizado para W -> 0 dar R_ref."""
        u = lam - delta
        if largura < 2 * self.passo:
            return np.interp(u, self.x, self.y, left=0.0, right=0.0)
        c1 = np.interp(u + largura / 2, self.x, self.c, left=0.0, right=self.c[-1])
        c0 = np.interp(u - largura / 2, self.x, self.c, left=0.0, right=self.c[-1])
        return (c1 - c0) / largura


def ajustar_chirp(ref: PerfilReferencia, wl: np.ndarray, pw: np.ndarray, centro: float, meia: float,
                  escala: str = "auto") -> dict:
    """Ajusta S = a*[R_ref (*) rect_W](l - delta) + b. Retorna delta, W e incertezas (1 sigma)."""
    nan = float("nan")
    res = dict(delta_nm=nan, chirp_nm=nan, ganho=nan, offset=nan, rms=nan,
               delta_nm_sd=nan, chirp_nm_sd=nan, ok=False)
    x, y = recortar(wl, linearizar(pw, escala), centro, meia)
    if len(x) < 6:
        return res
    b0 = linha_base(y)
    yb = y - b0
    i = int(np.argmax(yb))
    m = yb > 0.3 * yb[i]
    cog = np.sum(x[m] * yb[m]) / np.sum(yb[m])
    fw = largura_no_nivel(x, yb, i, 0.5)[0]
    fw_ref = largura_no_nivel(ref.x, ref.y, int(np.argmax(ref.y)), 0.5)[0]
    w0 = np.sqrt(max((fw if np.isfinite(fw) else fw_ref) ** 2 - fw_ref ** 2, 0.0)) * 1.2 + 0.01
    area_ref = ref.c[-1]
    area = np.trapezoid(np.clip(yb, 0, None), x) if hasattr(np, "trapezoid") else np.trapz(np.clip(yb, 0, None), x)
    a0 = max(area / area_ref, 1e-6) if area_ref > 0 else 1.0
    p0 = [cog - ref.centro, w0, a0, 0.0]
    lim_inf = [-meia, 0.0, 0.0, -np.inf]
    lim_sup = [meia, 2 * meia, np.inf, np.inf]

    def resid(p):
        return p[2] * ref.convoluido(x, p[0], p[1]) + p[3] - yb

    melhor = None
    for w_ini in (p0[1], 0.02, max(0.3, 2 * p0[1])):   # varias partidas: W tem minimos locais
        try:
            r = optimize.least_squares(resid, [p0[0], w_ini, p0[2], 0.0], bounds=(lim_inf, lim_sup),
                                       x_scale=[0.05, 0.05, a0, 0.05 * yb[i]], diff_step=1e-3)
        except Exception:  # noqa: BLE001
            continue
        if melhor is None or r.cost < melhor.cost:
            melhor = r
    if melhor is None:
        return res
    p = melhor.x
    dof = max(1, len(x) - 4)
    s2 = 2 * melhor.cost / dof
    try:
        cov = np.linalg.pinv(melhor.jac.T @ melhor.jac) * s2
        sd = np.sqrt(np.clip(np.diag(cov), 0, None))
    except Exception:  # noqa: BLE001
        sd = np.full(4, nan)
    if p[1] < 2.5 * ref.passo:      # W encostado no limite zero: incerteza do jacobiano nao vale
        sd[1] = nan
    res.update(delta_nm=float(p[0]), chirp_nm=float(p[1]), ganho=float(p[2]), offset=float(p[3] + b0),
               rms=float(np.sqrt(s2)), delta_nm_sd=float(sd[0]), chirp_nm_sd=float(sd[1]), ok=bool(melhor.success))
    return res


def distribuicao_deformacao(ref: PerfilReferencia, wl: np.ndarray, pw: np.ndarray, centro: float, meia: float,
                            lambda0_nm: float, faixa_ue: tuple[float, float] | None = None,
                            passo_ue: float = 10.0, reg: float = 0.2, escala: str = "auto",
                            p_e: float = fisica.P_E) -> tuple[np.ndarray, np.ndarray]:
    """Histograma de deformacao ao longo da rede (fracao do comprimento por nivel de eps).

    Modelo: S(l) = sum_j p_j * R_ref(l - k*eps_j) + b, p_j >= 0, com penalidade de
    suavidade (segunda diferenca). Resolve por NNLS. Retorna (eps_ue, p) com sum(p)=1.
    """
    k = fisica.sensibilidade_pm_por_ue(lambda0_nm, p_e) * 1e-3   # nm/ue
    x, y = recortar(wl, linearizar(pw, escala), centro, meia)
    yb = y - linha_base(y)
    if faixa_ue is None:
        # suporte possivel: onde o pico medido esta acima de 5 % do maximo (evita que o ajuste
        # use copias deslocadas para as bordas modelar ruido de linha de base)
        acima = np.flatnonzero(yb > 0.05 * np.max(yb))
        lo, hi = x[acima[0]], x[acima[-1]]
        faixa_ue = ((lo - ref.centro) / k, (hi - ref.centro) / k)
    eps = np.arange(faixa_ue[0], faixa_ue[1] + passo_ue, passo_ue)
    A = np.column_stack([ref.convoluido(x, k * e, 0.0) for e in eps] + [np.ones_like(x)])
    escala_a = np.max(A[:, :-1]) if A[:, :-1].size else 1.0
    A[:, :-1] /= escala_a
    n = len(eps)
    D = np.zeros((max(n - 2, 0), n + 1))
    for i in range(n - 2):
        D[i, i:i + 3] = [1, -2, 1]
    lam = reg * np.sqrt(len(x) / max(n, 1)) * np.max(np.abs(yb))
    Aa = np.vstack([A, lam * D])
    ya = np.concatenate([yb - np.min(yb), np.zeros(D.shape[0])])
    sol, _ = optimize.nnls(Aa, ya, maxiter=50 * (n + 1))
    p = sol[:-1]
    if p.sum() > 0:
        p = p / p.sum()
    return eps, p


def estatisticas_distribuicao(eps: np.ndarray, p: np.ndarray) -> dict:
    """Media, desvio, percentis e 'largura equivalente' = sqrt(12)*desvio (para uma distribuicao
    uniforme, i.e. gradiente linear, e exatamente a diferenca de deformacao entre as pontas)."""
    if p.sum() <= 0:
        return dict(eps_media=np.nan, eps_desvio=np.nan, eps_p05=np.nan, eps_p95=np.nan, faixa_90=np.nan,
                    largura_equiv=np.nan)
    mu = float(np.sum(eps * p))
    sd = float(np.sqrt(np.sum((eps - mu) ** 2 * p)))
    cdf = np.cumsum(p)
    p05 = float(np.interp(0.05, cdf, eps))
    p95 = float(np.interp(0.95, cdf, eps))
    return dict(eps_media=mu, eps_desvio=sd, eps_p05=p05, eps_p95=p95, faixa_90=p95 - p05,
                largura_equiv=float(np.sqrt(12) * sd))


# ----------------------------------------------------------------------------- gaussiana (*) retangulo por pixel

_SQ2 = np.sqrt(2.0)


def _int_phi(u):
    """Antiderivada da normal acumulada: int Phi(u) du = u*Phi(u) + phi(u)."""
    return u * 0.5 * (1 + special.erf(u / _SQ2)) + np.exp(-0.5 * u * u) / np.sqrt(2 * np.pi)


def modelo_gauss_ret(lam, passo, delta, largura, sigma, amp, base):
    """Pico = amp * [gaussiana(sigma) (*) retangulo(largura)] centrado em delta, INTEGRADO na largura de
    cada pixel (como o detector mede). Por ser um modelo continuo, nao 'trava' na grade de pixels."""
    a = lam - passo / 2 - delta
    c = lam + passo / 2 - delta
    if largura < 1e-4:
        phi = 0.5 * (special.erf(c / sigma / _SQ2) - special.erf(a / sigma / _SQ2))
        return amp * phi / passo + base
    h = largura / 2
    v = sigma * (_int_phi((c + h) / sigma) - _int_phi((a + h) / sigma)
                 - _int_phi((c - h) / sigma) + _int_phi((a - h) / sigma)) / (largura * passo)
    return amp * v + base


def ajustar_gauss_ret(wl, pw, centro: float, meia: float, sigma: float | None = None,
                      escala: str = "auto") -> dict:
    """Sem sigma: ajusta a referencia (largura=0) e devolve sigma (resposta do instrumento + rede).
    Com sigma fixo: ajusta deslocamento e largura do retangulo (= chirp, em nm)."""
    nan = float("nan")
    out = dict(centro_nm=nan, largura_nm=nan, sigma_nm=nan, amp=nan, rms=nan, ok=False)
    x, y = recortar(wl, linearizar(pw, escala), centro, meia)
    if len(x) < 6:
        return out
    passo = np.gradient(x)
    b0 = linha_base(y)
    yb = y - b0
    i = int(np.argmax(yb))
    area = np.trapezoid(np.clip(yb, 0, None), x) if hasattr(np, "trapezoid") else np.trapz(np.clip(yb, 0, None), x)
    c0 = centro_gravidade(x, yb, 0.2)
    if sigma is None:
        c0 = float(np.clip(c0, x[0] + 1e-6, x[-1] - 1e-6))
        r = optimize.least_squares(lambda p: modelo_gauss_ret(x, passo, p[0], 0.0, p[1], p[2], p[3]) - yb,
                                   [c0, 0.12, max(area, 1e-9), 0.0],
                                   bounds=([x[0], 0.01, 0, -np.inf], [x[-1], 2.0, np.inf, np.inf]),
                                   x_scale=[0.05, 0.05, max(area, 1e-9), max(0.05 * yb[i], 1e-9)])
        out.update(centro_nm=float(r.x[0]), largura_nm=0.0, sigma_nm=float(r.x[1]), amp=float(r.x[2]),
                   rms=float(np.sqrt(np.mean(r.fun ** 2))), ok=bool(r.success))
        return out
    # chute inicial da largura pela variancia (convolucao soma variancias: var = sigma^2 + W^2/12)
    m = yb > 0.1 * yb[i]
    w_ = np.clip(yb[m], 0, None)
    mu = np.sum(x[m] * w_) / w_.sum()
    var = np.sum((x[m] - mu) ** 2 * w_) / w_.sum()
    w0 = float(np.sqrt(max(12 * (var - sigma ** 2), 0.0004)))
    melhor = None
    c0 = float(np.clip(c0, x[0] + 1e-6, x[-1] - 1e-6))
    acima = x[yb > 0.1 * yb[i]]
    w_ext = float(np.ptp(acima)) - 2 * sigma if len(acima) > 1 else 0.05   # extensao total da feicao
    c_ext = float(0.5 * (acima.min() + acima.max())) if len(acima) > 1 else c0
    for c_ini, wi in ((c0, w0), (c0, 0.05), (c_ext, w_ext)):
        wi = float(np.clip(wi, 0.0, 1.98 * meia))
        c_ini = float(np.clip(c_ini, x[0] + 1e-6, x[-1] - 1e-6))
        try:
            r = optimize.least_squares(lambda p: modelo_gauss_ret(x, passo, p[0], p[1], sigma, p[2], p[3]) - yb,
                                       [c_ini, wi, max(area, 1e-9), 0.0],
                                       bounds=([x[0], 0.0, 0, -np.inf], [x[-1], 2 * meia, np.inf, np.inf]),
                                       x_scale=[0.05, 0.05, max(area, 1e-9), max(0.05 * yb[i], 1e-9)])
        except ValueError:
            continue
        if melhor is None or r.cost < melhor.cost:
            melhor = r
    if melhor is None:
        return out
    out.update(centro_nm=float(melhor.x[0]), largura_nm=float(melhor.x[1]), sigma_nm=float(sigma),
               amp=float(melhor.x[2]), rms=float(np.sqrt(np.mean(melhor.fun ** 2))), ok=bool(melhor.success))
    return out


# ----------------------------------------------------------------------------- ajuste T-matrix

def ajustar_tmm_referencia(wl, pw, janela: JanelaFBG, escala: str = "auto", n_secoes: int = 30) -> dict:
    """Ajusta lambda_B0, dn_ac e a resposta do instrumento (FWHM) ao espectro sem carga."""
    x, y = recortar(wl, linearizar(pw, escala), janela.centro_nm, janela.meia_largura_nm)
    b = linha_base(y)
    yb = y - b
    i = int(np.argmax(yb))
    L = janela.comprimento_mm
    apod = fisica.perfil_apodizacao(n_secoes, janela.apodizacao)
    eps0 = np.zeros(n_secoes) if apod is not None else 0.0

    def modelo(p, xx):
        lam0, dn, fw, a = p
        return a * fisica.espectro_medido(xx, lam0, L, dn, eps0, fw, apod=apod,
                                          n_secoes=n_secoes if apod is not None else 1)

    melhor = None
    for dn0 in (1e-5, 4e-5, 1.5e-4):    # refletividade baixa / media / alta
        p0 = [x[i], dn0, 0.2, 1.0]
        p0[3] = yb[i] / max(float(np.max(modelo(p0, x))), 1e-9)
        r = optimize.least_squares(lambda p: modelo(p, x) - yb, p0,
                                   bounds=([x[0], 1e-6, 0.01, 0], [x[-1], 1e-3, 1.5, np.inf]),
                                   x_scale=[0.01, 1e-5, 0.05, p0[3]])
        if melhor is None or r.cost < melhor.cost:
            melhor = r
    r = melhor
    lam0, dn, fw, a = r.x
    rmax = float(np.max(fisica.refletancia_tmm(np.linspace(lam0 - 1, lam0 + 1, 2001), lam0, L, dn, eps0,
                                               apod=apod, n_secoes=n_secoes if apod is not None else 1)))
    return dict(lambda_b0=float(lam0), dn_ac=float(dn), fwhm_instr=float(fw), ganho=float(a), base=float(b),
                refletividade_pico=rmax, rms=float(np.sqrt(np.mean(r.fun ** 2))))


def ajustar_tmm_gradiente(wl, pw, janela: JanelaFBG, ref_tmm: dict, quadratico: bool = False,
                          escala: str = "auto", n_secoes: int = 40, centro_nm: float | None = None) -> dict:
    """Com dn_ac e FWHM do instrumento fixos (da referencia), ajusta eps medio e gradiente (e curvatura)."""
    c = janela.centro_nm if centro_nm is None else centro_nm
    x, y = recortar(wl, linearizar(pw, escala), c, janela.meia_largura_nm)
    b = linha_base(y)
    yb = y - b
    L = janela.comprimento_mm
    z = (np.arange(n_secoes) + 0.5) / n_secoes * L - L / 2
    apod = fisica.perfil_apodizacao(n_secoes, janela.apodizacao)
    lam0 = ref_tmm["lambda_b0"]
    k = fisica.sensibilidade_pm_por_ue(lam0) * 1e-3
    i = int(np.argmax(yb))
    m = yb > 0.3 * yb[i]
    eps0 = (np.sum(x[m] * yb[m]) / np.sum(yb[m]) - lam0) / k

    def modelo(p):
        eps = fisica.perfil_deformacao(z, p[0], p[1], p[3] if quadratico else 0.0)
        return p[2] * fisica.espectro_medido(x, lam0, L, ref_tmm["dn_ac"], eps, ref_tmm["fwhm_instr"], apod=apod)

    p0 = [eps0, 5.0, ref_tmm["ganho"]] + ([0.0] if quadratico else [])
    lo = [eps0 - 2000, 0.0, 0.0] + ([-500.0] if quadratico else [])
    hi = [eps0 + 2000, 2000.0, np.inf] + ([500.0] if quadratico else [])
    melhor = None
    for g0 in (1.0, 20.0, 80.0):
        p0[1] = g0
        r = optimize.least_squares(lambda p: modelo(p) - yb, p0, bounds=(lo, hi),
                                   x_scale=[10.0, 5.0, ref_tmm["ganho"]] + ([1.0] if quadratico else []))
        if melhor is None or r.cost < melhor.cost:
            melhor = r
    p = melhor.x
    out = dict(eps_media_ue=float(p[0]), gradiente_ue_mm=float(p[1]), ganho=float(p[2]),
               delta_eps_ue=float(p[1] * L), rms=float(np.sqrt(np.mean(melhor.fun ** 2))))
    if quadratico:
        out["curvatura_ue_mm2"] = float(p[3])
    return out


# ----------------------------------------------------------------------------- sessao inteira

def analisar_sessao(sessao, janelas: list[JanelaFBG], passo_ref: int = 0, escala: str = "auto",
                    p_e: float = fisica.P_E, com_distribuicao: bool = True, com_tmm: bool = True,
                    limiar_cog: float = 0.2, reg_dist: float = 0.2, chirp_por_espectro: bool = True) -> dict:
    """Calcula metricas por espectro e por passo (espectro medio) para cada FBG."""
    wl_all, pw_all, passo_all, t_all = sessao.wl, sessao.pw, sessao.passo, sessao.t
    rot = sessao.rotulos()
    wl_ref, pw_ref = sessao.media_do_passo(passo_ref)

    refs, centros_ref, tmm_ref, sig_ref, cog_ref = {}, {}, {}, {}, {}
    for jf in janelas:
        c = jf.centro_nm
        m0 = metricas_pico(wl_ref, pw_ref, c, jf.meia_largura_nm, limiar_cog, escala)
        if np.isfinite(m0["lambda_centro"]) and jf.rastrear:
            c = m0["lambda_centro"]
        centros_ref[jf.nome] = c          # centro da janela (feicao inteira)
        cog_ref[jf.nome] = m0["lambda_cog"] if np.isfinite(m0["lambda_cog"]) else c   # base do deslocamento
        refs[jf.nome] = PerfilReferencia(wl_ref, pw_ref, c, jf.meia_largura_nm, escala=escala)
        sig_ref[jf.nome] = ajustar_gauss_ret(wl_ref, pw_ref, c, jf.meia_largura_nm, escala=escala)["sigma_nm"]
        if com_tmm:
            jtmp = JanelaFBG(jf.nome, c, jf.meia_largura_nm, jf.comprimento_mm, apodizacao=jf.apodizacao)
            try:
                tmm_ref[jf.nome] = ajustar_tmm_referencia(wl_ref, pw_ref, jtmp, escala)
            except Exception as e:  # noqa: BLE001
                tmm_ref[jf.nome] = {"erro": str(e)}

    # ---- por espectro
    linhas = []
    ultimo = dict(centros_ref)
    t_ini = time.time()
    for n in range(len(t_all)):
        if n and n % 500 == 0:
            dt = time.time() - t_ini
            print(f"   espectro {n}/{len(t_all)}  (faltam ~{dt / n * (len(t_all) - n) / 60:.1f} min)")
        for jf in janelas:
            c = ultimo[jf.nome] if jf.rastrear else jf.centro_nm
            m = metricas_pico(wl_all[n], pw_all[n], c, jf.meia_largura_nm, limiar_cog, escala)
            if jf.rastrear and np.isfinite(m["lambda_centro"]):
                ultimo[jf.nome] = m["lambda_centro"]
            k = fisica.sensibilidade_pm_por_ue(centros_ref[jf.nome], p_e) * 1e-3
            lin = dict(indice=n, t=t_all[n] - t_all[0], passo=int(passo_all[n]),
                       rotulo=rot[passo_all[n]] if passo_all[n] < len(rot) else "",
                       fbg=jf.nome, **m,
                       deslocamento_pm=(m["lambda_cog"] - cog_ref[jf.nome]) * 1e3,
                       eps_cog_ue=(m["lambda_cog"] - cog_ref[jf.nome]) / k,
                       eps_centro_ue=(m["lambda_centro"] - centros_ref[jf.nome]) / k,
                       salto_subpicos_ue=m["separacao_subpicos_nm"] / k)
            if chirp_por_espectro and np.isfinite(sig_ref[jf.nome]) and np.isfinite(m["lambda_cog"]):
                gr = ajustar_gauss_ret(wl_all[n], pw_all[n], m["lambda_centro"], jf.meia_largura_nm,
                                       sig_ref[jf.nome], escala)
                # pico dividido: o modelo de gradiente suave nao se aplica (use salto_subpicos_ue)
                lin["delta_eps_modelo_ue"] = gr["largura_nm"] / k if m["n_subpicos"] <= 1 else float("nan")
            linhas.append(lin)

    # ---- por passo (espectro medio: mais SNR para medir forma)
    resumo, distribs = [], {}
    ultimo = dict(centros_ref)
    for kp in sorted(set(passo_all.tolist())):
        wl_m, pw_m = sessao.media_do_passo(kp)
        for jf in janelas:
            c = ultimo[jf.nome] if jf.rastrear else jf.centro_nm
            m = metricas_pico(wl_m, pw_m, c, jf.meia_largura_nm, limiar_cog, escala)
            if jf.rastrear and np.isfinite(m["lambda_centro"]):
                c = m["lambda_centro"]
                ultimo[jf.nome] = c
            ref = refs[jf.nome]
            meia = jf.meia_largura_nm
            ch = ajustar_chirp(ref, wl_m, pw_m, c, meia, escala)
            lam0 = centros_ref[jf.nome]
            k = fisica.sensibilidade_pm_por_ue(lam0, p_e) * 1e-3
            lin = dict(passo=kp, rotulo=rot[kp] if kp < len(rot) else str(kp), fbg=jf.nome,
                       n_espectros=int(np.sum(passo_all == kp)), **m,
                       deslocamento_pm=(m["lambda_cog"] - cog_ref[jf.nome]) * 1e3,
                       eps_cog_ue=(m["lambda_cog"] - cog_ref[jf.nome]) / k,
                       eps_centro_ue=(m["lambda_centro"] - lam0) / k,
                       salto_subpicos_ue=m["separacao_subpicos_nm"] / k,
                       delta_ajuste_pm=ch["delta_nm"] * 1e3, chirp_pm=ch["chirp_nm"] * 1e3,
                       chirp_pm_sd=ch["chirp_nm_sd"] * 1e3, ajuste_rms=ch["rms"],
                       eps_media_ue=ch["delta_nm"] / k, delta_eps_ue=ch["chirp_nm"] / k,
                       delta_eps_ue_sd=ch["chirp_nm_sd"] / k,
                       gradiente_ue_mm=ch["chirp_nm"] / k / jf.comprimento_mm)
            if np.isfinite(sig_ref[jf.nome]):
                gr = ajustar_gauss_ret(wl_m, pw_m, c, meia, sig_ref[jf.nome], escala)
                ok = m["n_subpicos"] <= 1
                lin.update(delta_eps_modelo_ue=gr["largura_nm"] / k if ok else float("nan"),
                           gradiente_modelo_ue_mm=gr["largura_nm"] / k / jf.comprimento_mm if ok else float("nan"),
                           sigma_ref_pm=sig_ref[jf.nome] * 1e3)
            if com_distribuicao:
                try:
                    eps, p = distribuicao_deformacao(ref, wl_m, pw_m, c, meia, lam0, escala=escala, p_e=p_e,
                                                     reg=reg_dist)
                    distribs.setdefault(jf.nome, []).append((kp, eps, p))
                    lin.update({f"dist_{k_}": v for k_, v in estatisticas_distribuicao(eps, p).items()})
                except Exception as e:  # noqa: BLE001
                    lin["dist_erro"] = str(e)
            if com_tmm and "erro" not in tmm_ref.get(jf.nome, {"erro": 1}):
                try:
                    tm = ajustar_tmm_gradiente(wl_m, pw_m, jf, tmm_ref[jf.nome], escala=escala, centro_nm=c)
                    lin.update({f"tmm_{k_}": v for k_, v in tm.items()})
                except Exception as e:  # noqa: BLE001
                    lin["tmm_erro"] = str(e)
            resumo.append(lin)

    # compensacao de temperatura com FBG livre de deformacao
    refs_t = [j.nome for j in janelas if j.referencia_temperatura]
    if refs_t:
        for tabela, chave_passo in ((linhas, "indice"), (resumo, "passo")):
            desl_t = {}
            for l in tabela:
                if l["fbg"] in refs_t:
                    desl_t.setdefault(l[chave_passo], []).append(l["deslocamento_pm"])
            for l in tabela:
                dt = np.mean(desl_t.get(l[chave_passo], [np.nan]))
                k = fisica.sensibilidade_pm_por_ue(centros_ref[l["fbg"]], p_e)
                l["deslocamento_comp_pm"] = l["deslocamento_pm"] - dt
                l["eps_comp_ue"] = l["deslocamento_comp_pm"] / k

    return dict(por_espectro=linhas, por_passo=resumo, distribuicoes=distribs,
                centros_ref=centros_ref, tmm_ref=tmm_ref,
                janelas=[asdict(j) for j in janelas], passo_ref=passo_ref)


def salvar_tabela_csv(linhas: list[dict], caminho: str, decimal: str = ","):
    if not linhas:
        return
    chaves = []
    for l in linhas:
        for k in l:
            if k not in chaves:
                chaves.append(k)

    def fmt(v):
        if isinstance(v, (float, np.floating)):
            s = "" if not np.isfinite(v) else f"{v:.10g}"
            return s.replace(".", decimal)
        return str(v)

    with open(caminho, "w", encoding="utf-8") as f:
        f.write(";".join(chaves) + "\n")
        for l in linhas:
            f.write(";".join(fmt(l.get(k, "")) for k in chaves) + "\n")


def limite_deteccao_chirp(fwhm_instr_nm: float, fwhm_rede_nm: float, snr: float,
                          lambda_nm: float = 1550.0, n_medias: int = 1) -> dict:
    """Estimativa grosseira do menor Delta eps detectavel pelo alargamento do pico.

    Considera FWHM_tot^2 ~ FWHM_ref^2 + (k*Delta_eps)^2 / (alfa) e incerteza da
    FWHM ~ FWHM_ref / (SNR * sqrt(N)). Use o simulador para um numero confiavel.
    """
    fw_ref = np.hypot(fwhm_instr_nm, fwhm_rede_nm)
    sig_fw = fw_ref / (snr * np.sqrt(n_medias))
    # retangulo de largura W tem desvio W/sqrt(12); gaussiana: FWHM = 2.355 sigma
    w_min = np.sqrt(2 * fw_ref * 3 * sig_fw) * np.sqrt(12) / 2.355
    k = fisica.sensibilidade_pm_por_ue(lambda_nm) * 1e-3
    return dict(fwhm_ref_nm=fw_ref, sigma_fwhm_nm=sig_fw, chirp_min_nm=w_min, delta_eps_min_ue=w_min / k)
