"""Figuras do ensaio (PNG, prontas para relatorio).

  visao_geral.png             espectro completo: referencia x ultimo passo, janelas das FBGs
  espectrograma_<FBG>.png     cascata (lambda x espectro, cor = intensidade): deslocamento + alargamento
  sobreposicao_<FBG>.png      espectros medios de cada passo sobrepostos (rampa clara -> escura = carga)
  metricas_<FBG>.png          deslocamento, larguras, Delta eps (chirp) e assimetria por passo
  distribuicao_<FBG>.png      mapa da distribuicao de deformacao ao longo da rede por passo
"""

from __future__ import annotations

import os

import matplotlib

if os.environ.get("MPLBACKEND") is None and not os.environ.get("DISPLAY") and os.name != "nt":
    matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

# paleta de referencia (validada para daltonismo na ordem abaixo)
CATEG = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
AZUL = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6",
        "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
SUPERFICIE = "#fcfcfb"
TINTA = "#0b0b0b"
TINTA2 = "#52514e"
MUDO = "#898781"
GRADE = "#e1e0d9"
EIXO = "#c3c2b7"

CMAP_SEQ = LinearSegmentedColormap.from_list("fbg_seq", [SUPERFICIE] + AZUL)


def _estilo():
    plt.rcParams.update({
        "figure.facecolor": SUPERFICIE, "axes.facecolor": SUPERFICIE, "savefig.facecolor": SUPERFICIE,
        "axes.edgecolor": EIXO, "axes.labelcolor": TINTA2, "xtick.color": MUDO, "ytick.color": MUDO,
        "text.color": TINTA, "axes.grid": True, "grid.color": GRADE, "grid.linewidth": 0.6,
        "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 2.0,
        "font.size": 9, "axes.titlesize": 10, "axes.titleweight": "bold", "legend.frameon": False,
        "font.family": ["DejaVu Sans"],
    })


def _cores_ordinais(n: int) -> list[str]:
    """n tons da rampa azul (passo 250 -> 700) para series ordenadas (passos de carga)."""
    rampa = AZUL[3:]
    if n == 1:
        return [rampa[-1]]
    idx = np.linspace(0, len(rampa) - 1, n).round().astype(int)
    return [rampa[i] for i in idx]


def _poucos(n: int, maximo: int = 15) -> np.ndarray:
    """Indices de ate 'maximo' itens igualmente espacados (sempre inclui o primeiro e o ultimo)."""
    if n <= maximo:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, maximo).round().astype(int))


def _grade_comum(wl: np.ndarray, lo: float, hi: float) -> np.ndarray:
    passo = float(np.median(np.abs(np.diff(wl[0])))) / 2
    return np.arange(lo, hi, passo)


def visao_geral(sessao, resultado: dict, pasta: str) -> str:
    _estilo()
    fig, ax = plt.subplots(figsize=(10, 3.6))
    rot = sessao.rotulos()
    pr = resultado["passo_ref"]
    passos = sorted(set(sessao.passo.tolist()))
    wl0, pw0 = sessao.media_do_passo(pr)
    ax.plot(wl0, pw0, color=MUDO, lw=1.5, ls="--", label=f"referência ({rot[pr]})")
    ult = passos[-1]
    wl1, pw1 = sessao.media_do_passo(ult)
    ax.plot(wl1, pw1, color=CATEG[0], lw=1.5, label=f"último passo ({rot[ult]})")
    for i, j in enumerate(resultado["janelas"]):
        c = resultado["centros_ref"][j["nome"]]
        ax.axvspan(c - j["meia_largura_nm"], c + j["meia_largura_nm"], color=CATEG[i % 8], alpha=0.10, lw=0)
        ax.text(c, ax.get_ylim()[1] * 0.97, j["nome"], ha="center", va="top", color=TINTA2, fontsize=8)
    ax.set_xlabel("Comprimento de onda (nm)")
    ax.set_ylabel("Potência (unid. ILLumiSense)")
    ax.set_title("Espectro completo")
    ax.legend(loc="upper right")
    fig.tight_layout()
    p = os.path.join(pasta, "visao_geral.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def espectrograma(sessao, resultado: dict, nome_fbg: str, pasta: str, normalizar: bool = True) -> str:
    """Cascata: cada linha e um espectro; a cor e a intensidade (normalizada pelo maximo da linha)."""
    _estilo()
    j = next(x for x in resultado["janelas"] if x["nome"] == nome_fbg)
    linhas = [l for l in resultado["por_espectro"] if l["fbg"] == nome_fbg]
    cogs = np.array([l["lambda_cog"] for l in linhas], float)
    c0 = resultado["centros_ref"][nome_fbg]
    meia = j["meia_largura_nm"]
    lo = np.nanmin(np.append(cogs, c0)) - meia
    hi = np.nanmax(np.append(cogs, c0)) + meia
    wl, pw = sessao.wl, sessao.pw
    grade = _grade_comum(wl, lo, hi)
    img = np.vstack([np.interp(grade, w, p) for w, p in zip(wl, pw)])
    img = img - np.median(img[:, :max(3, img.shape[1] // 10)], axis=1, keepdims=True)
    if normalizar:
        img = img / np.maximum(img.max(axis=1, keepdims=True), 1e-12)
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(10, 6), gridspec_kw={"width_ratios": [3, 1]}, sharey=True)
    n = img.shape[0]
    ax.imshow(img, aspect="auto", origin="lower", cmap=CMAP_SEQ, vmin=0, vmax=1 if normalizar else None,
              extent=[grade[0], grade[-1], -0.5, n - 0.5], interpolation="nearest")
    ax.plot(cogs, np.arange(n), color=CATEG[1], lw=1.0, label="λ CoG (equivale ao valor 'cravado')")
    ax.axvline(c0, color=MUDO, lw=1, ls="--")
    passo = sessao.passo
    rot = sessao.rotulos()
    trocas = np.flatnonzero(np.diff(passo)) + 1
    for tr in (trocas if len(trocas) <= 40 else []):    # muitos blocos: linhas de divisao poluiriam
        ax.axhline(tr - 0.5, color=TINTA2, lw=0.6, alpha=0.6)
        ax2.axhline(tr - 0.5, color=TINTA2, lw=0.6, alpha=0.6)
    meio = []
    for k in sorted(set(passo.tolist())):
        idx = np.flatnonzero(passo == k)
        meio.append((idx.mean(), rot[k] if k < len(rot) else str(k)))
    sel = _poucos(len(meio))
    ax.set_yticks([meio[i][0] for i in sel])
    ax.set_yticklabels([meio[i][1] for i in sel])
    for ev in sessao.meta.get("eventos", []):
        y = ev["indice"] - 0.5
        for a_ in (ax, ax2):
            a_.axhline(y, color=TINTA, lw=1.2, ls="--")
        ax.annotate(ev["texto"], (grade[-1], y), textcoords="offset points", xytext=(-4, 3), ha="right",
                    fontsize=7, color=TINTA, bbox=dict(boxstyle="round,pad=0.2", fc=SUPERFICIE, ec="none", alpha=0.8))
    ax.grid(False)
    ax.set_xlabel("Comprimento de onda (nm)")
    ax.set_ylabel("Passo / espectro")
    ax.set_title(f"{nome_fbg} — espectrograma (intensidade normalizada)")
    ax.legend(loc="lower right", fontsize=8)
    if "delta_eps_modelo_ue" in linhas[0]:
        de = np.array([l.get("delta_eps_modelo_ue", np.nan) for l in linhas], float)
        sp = np.array([l.get("salto_subpicos_ue", np.nan) for l in linhas], float)
        ax2.plot(de, np.arange(n), color=CATEG[0], lw=1.0, label="chirp (modelo)")
        if np.any(np.isfinite(sp)):
            ax2.plot(sp, np.arange(n), ".", color=CATEG[1], ms=2.5, label="salto entre subpicos")
            ax2.legend(fontsize=7, loc="lower right")
        ax2.set_xlabel("Δε na rede (µε)")
        ax2.set_title("Não uniformidade")
    else:
        fw = np.array([l["fwhm"] for l in linhas], float) * 1e3
        ax2.plot(fw, np.arange(n), color=CATEG[0], lw=1.0)
        ax2.set_xlabel("FWHM (pm)")
        ax2.set_title("Largura")
    fig.tight_layout()
    p = os.path.join(pasta, f"espectrograma_{nome_fbg}.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def sobreposicao(sessao, resultado: dict, nome_fbg: str, pasta: str) -> str:
    _estilo()
    j = next(x for x in resultado["janelas"] if x["nome"] == nome_fbg)
    res = [l for l in resultado["por_passo"] if l["fbg"] == nome_fbg]
    cogs = [l["lambda_cog"] for l in res]
    c0 = resultado["centros_ref"][nome_fbg]
    lo = np.nanmin(cogs + [c0]) - j["meia_largura_nm"]
    hi = np.nanmax(cogs + [c0]) + j["meia_largura_nm"]
    fig, (ax, axn) = plt.subplots(1, 2, figsize=(11, 4.2))
    res = [res[i] for i in _poucos(len(res), 12)]     # no maximo 12 curvas (blocos de ensaio continuo)
    cores = _cores_ordinais(len(res))
    for l, cor in zip(res, cores):
        wl, pw = sessao.media_do_passo(l["passo"])
        sel = (wl >= lo) & (wl <= hi)
        y = pw[sel] - l["base"]
        ax.plot(wl[sel], y, color=cor, lw=1.6, label=l["rotulo"])
        # versao normalizada e centrada: so a FORMA (chirp, assimetria)
        axn.plot(wl[sel] - l["lambda_cog"], y / max(l["pot_max"], 1e-12), color=cor, lw=1.6, label=l["rotulo"])
    ax.set_xlabel("Comprimento de onda (nm)")
    ax.set_ylabel("Potência − linha de base")
    ax.set_title(f"{nome_fbg} — espectro médio por passo")
    axn.set_xlabel("λ − λ CoG (nm)")
    axn.set_ylabel("Normalizado")
    axn.set_title("Forma do pico (centrado e normalizado)")
    axn.set_xlim(-j["meia_largura_nm"], j["meia_largura_nm"])
    leg_ax = axn if len(res) <= 12 else None
    if leg_ax is not None:
        leg_ax.legend(fontsize=7, loc="upper right", title="passo", title_fontsize=7)
    fig.tight_layout()
    p = os.path.join(pasta, f"sobreposicao_{nome_fbg}.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def metricas(resultado: dict, nome_fbg: str, pasta: str) -> str:
    _estilo()
    res = [l for l in resultado["por_passo"] if l["fbg"] == nome_fbg]
    x = np.arange(len(res))
    rot = [l["rotulo"] for l in res]

    def col(k, fator=1.0):
        return np.array([l.get(k, np.nan) for l in res], float) * fator

    fig, axs = plt.subplots(2, 2, figsize=(11, 7), sharex=True)
    ms = 5 if len(res) <= 30 else 2
    a = axs[0, 0]
    chave = "eps_comp_ue" if "eps_comp_ue" in res[0] else "eps_cog_ue"
    a.plot(x, col(chave), "o-", color=CATEG[0], ms=ms, label="ε médio pelo CoG")
    if "tmm_eps_media_ue" in res[0]:
        a.plot(x, col("tmm_eps_media_ue"), "s--", color=CATEG[1], ms=ms, lw=1.5, label="ε médio (T-matrix)")
    a.set_ylabel("Deformação média (µε)")
    a.set_title("Deformação média (o que o pico informa)")
    a.legend(fontsize=8)

    a = axs[0, 1]
    a.plot(x, col("fwhm", 1e3), "o-", color=CATEG[0], ms=ms, label="FWHM (50 %)")
    a.plot(x, col("w20", 1e3), "s-", color=CATEG[1], ms=ms, label="largura a 20 %")
    a.set_ylabel("Largura (pm)")
    a.set_title("Alargamento do pico")
    a.legend(fontsize=8)

    a = axs[1, 0]
    if "delta_eps_modelo_ue" in res[0]:
        a.plot(x, col("delta_eps_modelo_ue"), "o-", color=CATEG[0], ms=ms, label="gaussiana ⊛ retângulo (por pixel)")
    if "tmm_delta_eps_ue" in res[0]:
        a.plot(x, col("tmm_delta_eps_ue"), "s-", color=CATEG[1], ms=ms, label="T-matrix (gradiente linear)")
    if np.any(np.isfinite(col("salto_subpicos_ue"))):
        a.plot(x, col("salto_subpicos_ue"), "D", color=CATEG[3], ms=ms + 1, label="salto entre subpicos (fissura)")
    if "dist_largura_equiv" in res[0]:
        a.plot(x, col("dist_largura_equiv"), "^-", color=CATEG[2], ms=ms + 1, lw=1.5,
               label="distribuição: √12·σ (deconvolução)")
        piso = col("dist_largura_equiv")[0]
        if np.isfinite(piso):
            a.axhline(piso, color=MUDO, lw=1, ls=":")
            a.annotate("piso (referência)", (x[-1], piso), textcoords="offset points", xytext=(0, 4),
                       ha="right", fontsize=7, color=TINTA2)
    a.set_ylabel("Δε entre as pontas da rede (µε)")
    a.set_title("Chirp: não uniformidade da deformação")
    a.legend(fontsize=8)

    a = axs[1, 1]
    a.plot(x, col("assim_cog_meio", 1e3), "o-", color=CATEG[0], ms=ms, label="λ CoG − centro da FWHM")
    a.axhline(0, color=EIXO, lw=1)
    a.set_ylabel("Assimetria (pm)")
    a.set_title("Assimetria do pico")
    a.legend(fontsize=8)

    sel = _poucos(len(rot))
    for a in axs[1]:
        a.set_xticks(x[sel])
        a.set_xticklabels([rot[i] for i in sel], rotation=45, ha="right")
        a.set_xlabel("Passo")
    fig.suptitle(f"{nome_fbg} — métricas por passo", fontweight="bold")
    fig.tight_layout()
    p = os.path.join(pasta, f"metricas_{nome_fbg}.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def distribuicao(resultado: dict, nome_fbg: str, pasta: str) -> str | None:
    dist = resultado["distribuicoes"].get(nome_fbg)
    if not dist:
        return None
    _estilo()
    rot = {l["passo"]: l["rotulo"] for l in resultado["por_passo"] if l["fbg"] == nome_fbg}
    lo = min(e.min() for _, e, _ in dist)
    hi = max(e.max() for _, e, _ in dist)
    grade = np.linspace(lo, hi, 400)
    img = np.vstack([np.interp(grade, e, p / max(p.max(), 1e-12), left=0.0, right=0.0) for _, e, p in dist])
    fig, ax = plt.subplots(figsize=(9, min(0.45 * len(dist) + 2.2, 9.0)))
    ax.imshow(img, aspect="auto", origin="lower", cmap=CMAP_SEQ, vmin=0, vmax=1,
              extent=[grade[0], grade[-1], -0.5, len(dist) - 0.5], interpolation="nearest")
    medias = [np.sum(e * p) for _, e, p in dist]
    ax.plot(medias, np.arange(len(dist)), "o" if len(dist) <= 40 else "-", color=CATEG[1], ms=4, label="média")
    sel = _poucos(len(dist))
    ax.set_yticks(sel)
    ax.set_yticklabels([rot.get(dist[i][0], str(dist[i][0])) for i in sel])
    ax.grid(False)
    ax.set_xlabel("Deformação local (µε) relativa à referência")
    ax.set_ylabel("Passo")
    ax.set_title(f"{nome_fbg} — distribuição de deformação ao longo da rede (deconvolução)")
    ax.legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    p = os.path.join(pasta, f"distribuicao_{nome_fbg}.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def gerar_todos(sessao, resultado: dict, pasta: str | None = None) -> list[str]:
    pasta = pasta or os.path.join(sessao.pasta, "figuras")
    os.makedirs(pasta, exist_ok=True)
    arqs = [visao_geral(sessao, resultado, pasta)]
    for j in resultado["janelas"]:
        n = j["nome"]
        arqs.append(espectrograma(sessao, resultado, n, pasta))
        arqs.append(sobreposicao(sessao, resultado, n, pasta))
        arqs.append(metricas(resultado, n, pasta))
        d = distribuicao(resultado, n, pasta)
        if d:
            arqs.append(d)
    return arqs
