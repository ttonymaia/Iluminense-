"""Exemplo de pós-processamento em Python de um ensaio do FBG SpectraLog.

Uso:  python exemplo_analise.py "C:\\...\\2026-11-15_101500_ensaio_FBG" 2
Gera metricas_CHn.csv (FWHM, largura a -10 dB, λ de pico, sub-picos por espectro) e, se o
matplotlib estiver instalado, um gráfico em cascata e a evolução da FWHM.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spectralog.analysis import analyze_spectrum  # noqa: E402
from spectralog.reader import load_test_channel  # noqa: E402

pasta, canal = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 1
d = load_test_channel(pasta, canal)
print(f"{d['P'].shape[0]} espectros x {d['P'].shape[1]} pontos")

linhas = []
for i in range(d["P"].shape[0]):
    a = analyze_spectrum(d["L"][i], d["P"][i], bw_db=10.0)
    for k, p in enumerate(a["peaks"], 1):
        linhas.append((d["amostra"][i], d["t_ensaio"][i], k, p["lambda_max_nm"], p["fwhm_nm"], p["bw_x_nm"],
                       p["sub_picos"]))
saida = Path(pasta) / f"metricas_CH{canal}.csv"
np.savetxt(saida, np.array(linhas), delimiter=",", fmt="%.10g",
           header="amostra,t_ensaio_s,pico,lambda_max_nm,fwhm_nm,bw_10dB_nm,sub_picos", comments="")
print("salvo:", saida)

try:
    import matplotlib.pyplot as plt
except ImportError:
    sys.exit(0)
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 8), constrained_layout=True)
im = ax1.imshow(d["P"], aspect="auto", origin="lower", cmap="Blues",
                extent=[d["L"][0, 0], d["L"][0, -1], d["t_ensaio"][0] / 60, d["t_ensaio"][-1] / 60])
ax1.set(xlabel="Comprimento de onda (nm)", ylabel="Tempo (min)", title=f"CH{canal} — cascata")
fig.colorbar(im, ax=ax1, label="Potência")
m = np.array(linhas)
for k in np.unique(m[:, 2])[:6]:
    s = m[m[:, 2] == k]
    ax2.plot(s[:, 1] / 60, s[:, 4], lw=1.2, label=f"pico {int(k)}")
ax2.set(xlabel="Tempo (min)", ylabel="FWHM (nm)")
ax2.legend()
plt.show()
