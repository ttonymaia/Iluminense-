"""Leitura do arquivo de calibracao do ILLumiSense (ex.: F13424-0019-001.calib).

Formato obtido por engenharia reversa (dados "flattened" do LabVIEW, big-endian):

    u32  n                      tamanho da string
    char[n]                     numero de serie (ex.: "F13424-0019-001")
    u32  64, u32 1              dimensoes de um array 2D de double (64 x 1)
    f64[64]                     [0] = temperatura de referencia do etalon (22,5 C)
                                [1:] = 63 picos do etalon de referencia em nm
                                       (espacamento constante de ~199,97 GHz)
    u32  851, u32 513           dimensoes da tabela pixel -> lambda
    f64[851*513]                cada linha: [temperatura C, lambda(pixel 0..511) nm]
                                temperaturas de -15,0 a 70,0 C em passos de 0,1 C

A tabela e usada pelo ILLumiSense quando nao ha etalon ("Load BaySpec data if
no etalon.vi"). Aqui ela e usada no modo DLL (acesso direto ao espectrometro),
onde a DLL devolve apenas o indice do pixel e a temperatura do termistor.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np


@dataclass
class Calibracao:
    serie: str
    etalon_temp_c: float
    etalon_picos_nm: np.ndarray      # (63,)
    temps_c: np.ndarray              # (851,)
    tabela_nm: np.ndarray            # (851, 512)

    @property
    def n_pixels(self) -> int:
        return self.tabela_nm.shape[1]

    def comprimentos_de_onda(self, temp_c: float) -> np.ndarray:
        """lambda de cada pixel (nm) para a temperatura interna do espectrometro."""
        t = float(np.clip(temp_c, self.temps_c[0], self.temps_c[-1]))
        i = int(np.searchsorted(self.temps_c, t))
        if i <= 0:
            return self.tabela_nm[0].copy()
        if i >= len(self.temps_c):
            return self.tabela_nm[-1].copy()
        t0, t1 = self.temps_c[i - 1], self.temps_c[i]
        f = (t - t0) / (t1 - t0)
        return (1 - f) * self.tabela_nm[i - 1] + f * self.tabela_nm[i]

    def resumo(self) -> str:
        wl = self.comprimentos_de_onda(25.0)
        disp = np.diff(wl)
        drift = (self.tabela_nm[-1] - self.tabela_nm[0]) / (self.temps_c[-1] - self.temps_c[0])
        return (
            f"Serie: {self.serie}\n"
            f"Pixels: {self.n_pixels}\n"
            f"Faixa @25 C: {wl[0]:.3f} - {wl[-1]:.3f} nm\n"
            f"Dispersao: {disp.min()*1000:.1f} - {disp.max()*1000:.1f} pm/pixel (media {disp.mean()*1000:.1f})\n"
            f"Tabela de temperatura: {self.temps_c[0]:.1f} a {self.temps_c[-1]:.1f} C ({len(self.temps_c)} linhas)\n"
            f"Deriva termica do espectrometro: {drift.mean()*1000:.1f} pm/C (media)\n"
            f"Etalon: {len(self.etalon_picos_nm)} picos @ {self.etalon_temp_c:.1f} C, "
            f"{self.etalon_picos_nm[0]:.3f} - {self.etalon_picos_nm[-1]:.3f} nm, "
            f"FSR {self.fsr_etalon_ghz():.2f} GHz"
        )

    def fsr_etalon_ghz(self) -> float:
        f = 299792458.0 / (self.etalon_picos_nm * 1e-9)
        return float(np.mean(np.abs(np.diff(f))) / 1e9)


def _ler_array2d(buf: bytes, off: int) -> tuple[np.ndarray, int]:
    r, c = struct.unpack_from(">II", buf, off)
    off += 8
    n = r * c
    arr = np.frombuffer(buf, dtype=">f8", count=n, offset=off).astype(float).reshape(r, c)
    return arr, off + 8 * n


def ler_calib(caminho: str) -> Calibracao:
    buf = open(caminho, "rb").read()
    (n,) = struct.unpack_from(">I", buf, 0)
    serie = buf[4:4 + n].decode("latin-1")
    off = 4 + n
    et, off = _ler_array2d(buf, off)
    et = et.ravel()
    tab, off = _ler_array2d(buf, off)
    if off != len(buf):
        raise ValueError(f"arquivo .calib com {len(buf) - off} bytes inesperados no final")
    return Calibracao(
        serie=serie,
        etalon_temp_c=float(et[0]),
        etalon_picos_nm=et[1:].copy(),
        temps_c=tab[:, 0].copy(),
        tabela_nm=tab[:, 1:].copy(),
    )


def exportar_csv(cal: Calibracao, caminho: str) -> None:
    """Exporta a tabela pixel x temperatura para CSV (uma linha por temperatura)."""
    cab = "temp_C;" + ";".join(f"px{i}" for i in range(cal.n_pixels))
    dados = np.column_stack([cal.temps_c, cal.tabela_nm])
    np.savetxt(caminho, dados, delimiter=";", header=cab, comments="", fmt="%.6f")
