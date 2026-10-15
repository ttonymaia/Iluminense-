"""Visualizacao ao vivo do espectro bruto (osciloscopio optico).

Teclas na janela:
    r   captura o espectro atual (media de 10) como REFERENCIA
    n   alterna o zoom entre as FBGs (e o espectro completo)
    s   salva o espectro atual em CSV (pasta de saida)
    g   inicia/para a gravacao continua numa sessao
    q   sai
"""

from __future__ import annotations

import os
import time
from collections import deque

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation

from . import analise, graficos
from .sessao import Sessao


def executar(fonte, janelas: list[analise.JanelaFBG] | None = None, canal=None, n_cascata: int = 200,
             pasta_saida: str = "ensaios", intervalo_ms: int = 100):
    graficos._estilo()
    e0 = fonte.espectro(canal)
    if not janelas:
        picos = analise.detectar_picos(e0.wl, e0.pw)
        janelas = [analise.JanelaFBG(f"FBG{i+1}", float(p), 1.5) for i, p in enumerate(picos)]
    estado = {"ref": None, "zoom": -1, "grav": None, "ultimo": e0, "t0": time.time(), "n": 0}
    cascata = deque(maxlen=n_cascata)

    fig = plt.figure(figsize=(12, 7))
    gs = fig.add_gridspec(2, 3, height_ratios=[1, 1.2])
    ax = fig.add_subplot(gs[0, :2])
    axt = fig.add_subplot(gs[0, 2])
    axc = fig.add_subplot(gs[1, :])
    axt.axis("off")
    (l_atual,) = ax.plot(e0.wl, e0.pw, color=graficos.CATEG[0], lw=1.5, label="atual")
    (l_ref,) = ax.plot([], [], color=graficos.MUDO, lw=1.2, ls="--", label="referência")
    ax.set_xlabel("Comprimento de onda (nm)")
    ax.set_ylabel("Potência")
    ax.legend(loc="upper right", fontsize=8)
    txt = axt.text(0, 1, "", va="top", ha="left", family="monospace", fontsize=8)
    img = axc.imshow(np.zeros((2, 2)), aspect="auto", origin="lower", cmap=graficos.CMAP_SEQ,
                     interpolation="nearest", vmin=0, vmax=1)
    axc.grid(False)
    axc.set_xlabel("Comprimento de onda (nm)")
    axc.set_ylabel("Espectros (mais recente em cima)")
    fig.suptitle("ILLumiSense — espectro bruto ao vivo   [r] referência  [n] zoom  [s] salvar  [g] gravar  [q] sair",
                 fontsize=9)

    def faixa():
        if estado["zoom"] < 0:
            return float(e0.wl.min()), float(e0.wl.max())
        j = janelas[estado["zoom"]]
        c = j.centro_nm
        return c - 2 * j.meia_largura_nm, c + 2 * j.meia_largura_nm

    def tecla(ev):
        if ev.key == "r":
            ss = [fonte.espectro(canal) for _ in range(10)]
            estado["ref"] = (ss[0].wl, np.mean([s.pw for s in ss], axis=0))
            for j in janelas:
                m = analise.metricas_pico(estado["ref"][0], estado["ref"][1], j.centro_nm, j.meia_largura_nm)
                if np.isfinite(m["lambda_centro"]):
                    j.centro_nm = m["lambda_centro"]
            estado["ref_centros"] = {j.nome: j.centro_nm for j in janelas}
        elif ev.key == "n":
            estado["zoom"] = estado["zoom"] + 1 if estado["zoom"] + 1 < len(janelas) else -1
            cascata.clear()
        elif ev.key == "s":
            os.makedirs(pasta_saida, exist_ok=True)
            e = estado["ultimo"]
            p = os.path.join(pasta_saida, f"espectro_{time.strftime('%Y%m%d_%H%M%S')}.csv")
            np.savetxt(p, np.column_stack([e.wl, e.pw]), delimiter=";", header="lambda_nm;potencia",
                       comments="", fmt="%.6f")
            print(f"salvo: {p}")
        elif ev.key == "g":
            if estado["grav"] is None:
                s = Sessao.nova(pasta_saida, "ao_vivo", {"modo": "ao_vivo",
                                                          "protocolo": {"fbgs": [vars(j) for j in janelas]}})
                s.novo_passo("ao vivo")
                estado["grav"] = s
                print(f"gravando em {s.pasta}")
            else:
                estado["grav"].salvar()
                estado["grav"].fechar()
                print(f"{estado['grav'].n} espectros salvos em {estado['grav'].pasta}")
                estado["grav"] = None
        elif ev.key == "q":
            plt.close(fig)

    fig.canvas.mpl_connect("key_press_event", tecla)

    def atualizar(_):
        try:
            e = fonte.espectro(canal)
        except Exception as ex:  # noqa: BLE001
            txt.set_text(f"falha na leitura:\n{ex}")
            return []
        estado["ultimo"] = e
        estado["n"] += 1
        if estado["grav"] is not None:
            estado["grav"].adicionar(e, 0)
        l_atual.set_data(e.wl, e.pw)
        if estado["ref"] is not None:
            l_ref.set_data(*estado["ref"])
        lo, hi = faixa()
        ax.set_xlim(lo, hi)
        sel = (e.wl >= lo) & (e.wl <= hi)
        if np.any(sel):
            ymax = float(np.max(e.pw[sel]))
            ax.set_ylim(min(0.0, float(np.min(e.pw[sel]))), ymax * 1.1 if ymax > 0 else 1)
        grade = np.linspace(lo, hi, 600)
        y = np.interp(grade, e.wl, e.pw)
        y = y - np.percentile(y, 10)
        cascata.append(y / max(float(np.max(y)), 1e-12))
        img.set_data(np.vstack(cascata))
        img.set_extent([lo, hi, -0.5, len(cascata) - 0.5])
        axc.set_xlim(lo, hi)
        axc.set_ylim(-0.5, max(len(cascata), 2) - 0.5)
        linhas = [f"{'FBG':<7}{'λ CoG':>11}{'Δ(pm)':>8}{'FWHM':>7}{'assim':>7}"]
        rc = estado.get("ref_centros", {})
        for j in janelas:
            m = analise.metricas_pico(e.wl, e.pw, j.centro_nm, j.meia_largura_nm)
            if j.rastrear and np.isfinite(m["lambda_centro"]):
                j.centro_nm = m["lambda_centro"]
            d = (m["lambda_cog"] - rc[j.nome]) * 1e3 if j.nome in rc else float("nan")
            linhas.append(f"{j.nome:<7}{m['lambda_cog']:>11.4f}{d:>8.1f}{m['fwhm']*1e3:>7.0f}"
                          f"{m['assim_cog_meio']*1e3:>7.1f}")
        taxa = estado["n"] / max(time.time() - estado["t0"], 1e-9)
        linhas += ["", f"{taxa:.1f} espectros/s", "gravando: " + ("SIM" if estado["grav"] is not None else "não")]
        txt.set_text("\n".join(linhas))
        return [l_atual, l_ref, img, txt]

    anim = FuncAnimation(fig, atualizar, interval=intervalo_ms, cache_frame_data=False)
    plt.show()
    if estado["grav"] is not None:
        estado["grav"].salvar()
        estado["grav"].fechar()
    fonte.fechar()
    return anim
