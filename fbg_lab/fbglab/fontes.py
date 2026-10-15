"""Fontes de espectro.

FonteWeb       servico web REST embutido no ILLumiSense.exe (ILLWS, porta 8002).
               Endpoints descobertos no pacote ILLWS.lvws:
                   GET /ILLWS/GetSpectrum/<canal>      -> {"Error", "SpecWav"[], "SpecPow"[]}
                   GET /ILLWS/GetSpectrumBasic/<canal> -> {"Error", "SpecPow"[]}
                   GET /ILLWS/GetPeaks/<canal>         -> {"Error", "PeakWavelengths"[], "PeakPowers"[]}
                   GET /ILLWS/GetHWInformation/        -> {"Error", "TypeHW", "NumberOfChannels", "ScanRates"[]}
                   (ver docs/engenharia_reversa.md para a lista completa)
               O ILLumiSense continua rodando normalmente: e o caminho recomendado.

FonteDLL       acesso direto ao espectrometro pela Sense2020Dll.dll (BaySpec).
               Exige Windows + Python 32 bits e o ILLumiSense FECHADO (o USB e
               exclusivo). Devolve contagens brutas por pixel; o eixo lambda vem
               da tabela do arquivo .calib (sem a correcao por etalon).

FonteSimulada  gera espectros realistas (T-matrix + resposta do instrumento) para
               treinar a rotina e validar a analise sem o equipamento.

FonteArquivo   le espectros salvos em texto (botao "Save spectrum" do ILLumiSense,
               CSV/TXT com virgula ou ponto decimal).
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import numpy as np

from . import fisica


@dataclass
class Espectro:
    t: float                  # instante (epoch, s)
    wl: np.ndarray            # comprimento de onda de cada pixel (nm)
    pw: np.ndarray            # potencia/intensidade de cada pixel (unidade da fonte)
    canal: int = 1
    temp_c: float | None = None
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------------------
# Servico web do ILLumiSense
# --------------------------------------------------------------------------------------

def _chave(d: dict, nome: str):
    """Busca uma chave do JSON do LabVIEW sem diferenciar maiusculas/minusculas."""
    for k, v in d.items():
        if k.lower() == nome.lower():
            return v
    raise KeyError(nome)


class FonteWeb:
    def __init__(self, host: str = "localhost", porta: int = 8002, servico: str = "ILLWS",
                 canal: int = 1, timeout: float = 5.0, basico: bool = False):
        self.host, self.porta, self.servico = host, porta, servico
        self.canal, self.timeout, self.basico = canal, timeout, basico
        self._wl_cache: np.ndarray | None = None

    @property
    def base(self) -> str:
        pre = f"/{self.servico}" if self.servico else ""
        return f"http://{self.host}:{self.porta}{pre}"

    def _get(self, caminho: str) -> dict:
        url = self.base + caminho
        with urllib.request.urlopen(url, timeout=self.timeout) as r:
            txt = r.read().decode("utf-8", errors="replace")
        try:
            return json.loads(txt)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"resposta nao-JSON de {url}: {txt[:200]!r}") from e

    def info(self) -> dict:
        out = {}
        for ep in ("GetHWInformation/", "GetSerialNumber/", "GetFWVersion/", "GetSWVersion/",
                   "GetScanRate/", "GetActiveChannels/"):
            try:
                out[ep.rstrip("/")] = self._get("/" + ep)
            except Exception as e:  # noqa: BLE001 - so diagnostico
                out[ep.rstrip("/")] = f"erro: {e}"
        return out

    def espectro(self, canal: int | None = None) -> Espectro:
        c = self.canal if canal is None else canal
        t = time.time()
        if self.basico and self._wl_cache is not None:
            d = self._get(f"/GetSpectrumBasic/{c}")
            pw = np.asarray(_chave(d, "SpecPow"), dtype=float)
            wl = self._wl_cache
        else:
            d = self._get(f"/GetSpectrum/{c}")
            wl = np.asarray(_chave(d, "SpecWav"), dtype=float)
            pw = np.asarray(_chave(d, "SpecPow"), dtype=float)
            self._wl_cache = wl
        err = d.get("Error", d.get("error", 0))
        if len(wl) == 0 or len(pw) == 0:
            raise RuntimeError(f"espectro vazio (Error={err}). O canal {c} esta ativo?")
        if len(wl) != len(pw):
            raise RuntimeError(f"tamanhos diferentes: SpecWav={len(wl)} SpecPow={len(pw)}")
        return Espectro(t=t, wl=wl, pw=pw, canal=c, extra={"erro_ill": err})

    def picos(self, canal: int | None = None) -> tuple[np.ndarray, np.ndarray]:
        c = self.canal if canal is None else canal
        d = self._get(f"/GetPeaks/{c}")
        return (np.asarray(_chave(d, "PeakWavelengths"), dtype=float),
                np.asarray(_chave(d, "PeakPowers"), dtype=float))

    def fechar(self):
        pass


def sondar_web(host: str = "localhost", portas=(8002, 8001, 8080, 80), timeout: float = 2.0) -> list[str]:
    """Testa combinacoes de porta / prefixo / canal e relata o que respondeu."""
    rel = []
    for porta in portas:
        for serv in ("ILLWS", ""):
            f = FonteWeb(host, porta, serv, timeout=timeout)
            try:
                hw = f._get("/GetHWInformation/")
                rel.append(f"[OK] {f.base}/GetHWInformation/ -> {json.dumps(hw)[:300]}")
            except urllib.error.HTTPError as e:
                rel.append(f"[--] {f.base}/GetHWInformation/ -> HTTP {e.code}")
                continue
            except Exception as e:  # noqa: BLE001
                rel.append(f"[--] {f.base}/GetHWInformation/ -> {type(e).__name__}: {e}")
                continue
            for canal in (0, 1, 2):
                try:
                    s = f.espectro(canal)
                    rel.append(f"[OK]   canal {canal}: {len(s.wl)} pontos, "
                               f"{s.wl.min():.3f}-{s.wl.max():.3f} nm, "
                               f"potencia {s.pw.min():.3g}..{s.pw.max():.3g}")
                except Exception as e:  # noqa: BLE001
                    rel.append(f"[--]   canal {canal}: {type(e).__name__}: {e}")
            return rel
    return rel


# --------------------------------------------------------------------------------------
# Acesso direto pela Sense2020Dll.dll
# --------------------------------------------------------------------------------------

class FonteDLL:
    """Prototipos (stdcall) obtidos por desmontagem da Sense2020Dll.dll:

        WORD DLL_Open_Device(const char* pasta_calib_LT)      # 0 = OK; 1 = aquisicao continua ativa;
                                                              # 3 = nenhum/mais de um espectrometro;
                                                              # 4 = falha ao abrir; 5 = serie invalida;
                                                              # 8 = Calib_LT_<serie>.dat invalido
        int  DLL_Get_Pixel_Count(void)                        # 512
        BOOL DLL_Get_Device_SN(char* buf64)
        BOOL DLL_Set_IntegrationTime_SampleRate(DWORD t_int_us (>=20), DWORD taxa_Hz (1..8000))
        BOOL DLL_Set_Sensor_Mode(WORD modo)                   # sensibilidade (ganho)
        BOOL DLL_Snapshot_Acquisition(double* lambda, double* intensidade, double* temp_C)
        void DLL_Close_Device(void)

    Sem o arquivo Calib_LT_<serie>.dat da BaySpec a DLL devolve lambda = indice do
    pixel; por isso o eixo e recalculado pela tabela do .calib do ILLumiSense.
    """

    def __init__(self, caminho_dll: str, caminho_calib: str | None = None,
                 tempo_integracao_us: int | None = None, taxa_hz: int | None = None,
                 modo_sensor: int | None = None):
        if sys.platform != "win32":
            raise RuntimeError("FonteDLL so funciona no Windows")
        if ctypes.sizeof(ctypes.c_void_p) != 4:
            raise RuntimeError("A Sense2020Dll.dll e 32 bits: use um Python 32 bits (x86)")
        pasta = os.path.dirname(os.path.abspath(caminho_dll))
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(pasta)
        self.dll = ctypes.WinDLL(os.path.abspath(caminho_dll))
        d = self.dll
        d.DLL_Open_Device.argtypes = [ctypes.c_char_p]
        d.DLL_Open_Device.restype = ctypes.c_uint16
        d.DLL_Get_Pixel_Count.argtypes = []
        d.DLL_Get_Pixel_Count.restype = ctypes.c_int
        d.DLL_Get_Device_SN.argtypes = [ctypes.c_char_p]
        d.DLL_Get_Device_SN.restype = ctypes.c_int
        d.DLL_Set_IntegrationTime_SampleRate.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        d.DLL_Set_IntegrationTime_SampleRate.restype = ctypes.c_int
        d.DLL_Set_Sensor_Mode.argtypes = [ctypes.c_uint16]
        d.DLL_Set_Sensor_Mode.restype = ctypes.c_int
        d.DLL_Snapshot_Acquisition.argtypes = [ctypes.POINTER(ctypes.c_double)] * 3
        d.DLL_Snapshot_Acquisition.restype = ctypes.c_int
        d.DLL_Close_Device.argtypes = []
        d.DLL_Close_Device.restype = None

        rc = d.DLL_Open_Device(None)
        if rc != 0 or not d.DLL_Get_Pixel_Count() > 0:
            raise RuntimeError(f"DLL_Open_Device falhou (codigo {rc}). O ILLumiSense esta fechado? "
                               "O driver USB (bayu2L/BSUSB) esta instalado?")
        self.n = d.DLL_Get_Pixel_Count()
        sn = ctypes.create_string_buffer(64)
        d.DLL_Get_Device_SN(sn)
        self.serie = sn.value.decode("latin-1", errors="replace")
        if tempo_integracao_us is not None:
            d.DLL_Set_IntegrationTime_SampleRate(int(tempo_integracao_us), int(taxa_hz or 100))
        if modo_sensor is not None:
            d.DLL_Set_Sensor_Mode(int(modo_sensor))
        self.cal = None
        if caminho_calib:
            from .calib import ler_calib
            self.cal = ler_calib(caminho_calib)
        self._wl = (ctypes.c_double * self.n)()
        self._pw = (ctypes.c_double * self.n)()
        self._tc = ctypes.c_double(0.0)

    def espectro(self, canal: int | None = None) -> Espectro:
        t = time.time()
        ok = self.dll.DLL_Snapshot_Acquisition(self._wl, self._pw, ctypes.byref(self._tc))
        if not ok:
            raise RuntimeError("DLL_Snapshot_Acquisition falhou")
        pw = np.frombuffer(self._pw, dtype=float).copy()
        wl_dll = np.frombuffer(self._wl, dtype=float).copy()
        temp = float(self._tc.value)
        if self.cal is not None and self.cal.n_pixels == self.n:
            wl = self.cal.comprimentos_de_onda(temp)
        else:
            wl = wl_dll
        return Espectro(t=t, wl=wl, pw=pw, canal=canal or 1, temp_c=temp,
                        extra={"serie": self.serie})

    def fechar(self):
        try:
            self.dll.DLL_Close_Device()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------------------
# Simulador
# --------------------------------------------------------------------------------------

@dataclass
class FBGSimulada:
    lambda0_nm: float
    comprimento_mm: float = 10.0
    dn_ac: float = 4e-5
    eps_media_ue: float = 0.0
    gradiente_ue_mm: float = 0.0
    curvatura_ue_mm2: float = 0.0
    apodizacao: str = "uniforme"
    degrau_ue: float = 0.0           # salto de deformacao numa posicao da rede (fissura cruzando a FBG)
    pos_degrau_mm: float = 0.0       # posicao do salto, medida a partir do centro da rede


class FonteSimulada:
    """Espectro sintetico: envelope da fonte SLED x refletancia T-matrix + ruido."""

    def __init__(self, fbgs: list[FBGSimulada] | None = None, caminho_calib: str | None = None,
                 fwhm_instr_nm: float = 0.25, ruido: float = 0.15, escala: float = 80.0,
                 temp_c: float = 25.0, semente: int | None = 0, n_secoes: int = 60):
        self.fbgs = fbgs or [FBGSimulada(1530.0), FBGSimulada(1550.0), FBGSimulada(1570.0)]
        if caminho_calib:
            from .calib import ler_calib
            self.cal = ler_calib(caminho_calib)
        else:
            self.cal = None
        self.fwhm, self.ruido, self.escala, self.temp_c = fwhm_instr_nm, ruido, escala, temp_c
        self.rng = np.random.default_rng(semente)
        self.n_secoes = n_secoes

    def eixo(self) -> np.ndarray:
        if self.cal is not None:
            return self.cal.comprimentos_de_onda(self.temp_c)
        # aproximacao do eixo do F13424-0019-001 @25 C (512 pixels, ~0,17 nm/pixel)
        p = np.arange(512)
        return 1505.503 + 0.18354 * p - 1.27e-5 * p ** 2

    def definir_estado(self, idx: int, eps_media_ue: float | None = None, gradiente_ue_mm: float | None = None,
                       curvatura_ue_mm2: float | None = None, degrau_ue: float | None = None,
                       pos_degrau_mm: float | None = None):
        f = self.fbgs[idx]
        if degrau_ue is not None:
            f.degrau_ue = degrau_ue
        if pos_degrau_mm is not None:
            f.pos_degrau_mm = pos_degrau_mm
        if eps_media_ue is not None:
            f.eps_media_ue = eps_media_ue
        if gradiente_ue_mm is not None:
            f.gradiente_ue_mm = gradiente_ue_mm
        if curvatura_ue_mm2 is not None:
            f.curvatura_ue_mm2 = curvatura_ue_mm2

    def espectro(self, canal: int | None = None) -> Espectro:
        wl = self.eixo()
        env = np.exp(-0.5 * ((wl - 1550.0) / 30.0) ** 2) * 0.9 + 0.1
        r = np.zeros_like(wl)
        for f in self.fbgs:
            z = (np.arange(self.n_secoes) + 0.5) / self.n_secoes * f.comprimento_mm - f.comprimento_mm / 2
            eps = fisica.perfil_deformacao(z, f.eps_media_ue, f.gradiente_ue_mm, f.curvatura_ue_mm2)
            eps = eps + np.where(z > f.pos_degrau_mm, f.degrau_ue, 0.0)
            apod = fisica.perfil_apodizacao(self.n_secoes, f.apodizacao)
            r += fisica.espectro_medido(wl, f.lambda0_nm, f.comprimento_mm, f.dn_ac, eps, self.fwhm, apod=apod)
        pw = self.escala * env * r + 0.5 + self.ruido * self.rng.standard_normal(len(wl))
        return Espectro(t=time.time(), wl=wl, pw=pw, canal=canal or 1, temp_c=self.temp_c,
                        extra={"simulado": True})

    def fechar(self):
        pass


# --------------------------------------------------------------------------------------
# Arquivos de texto
# --------------------------------------------------------------------------------------

_NUM = re.compile(r"^[\s;,\t]*[-+]?\d")


def ler_texto_espectro(caminho: str) -> list[Espectro]:
    """Le espectros em texto. Aceita:
       - 2 colunas (lambda, potencia), uma linha por pixel;
       - N colunas: lambda + varias colunas de potencia (um espectro por coluna);
       - separador tab, ';' ou ',' e virgula decimal (Windows em portugues).
    Linhas de cabecalho nao numericas sao ignoradas.
    """
    linhas = open(caminho, encoding="latin-1").read().splitlines()
    dados = []
    for ln in linhas:
        if not _NUM.match(ln):
            continue
        s = ln.strip()
        if "\t" in s:
            campos = s.split("\t")
        elif ";" in s:
            campos = s.split(";")
        elif re.search(r"\d\s+[-+]?\.?\d", s):
            campos = s.split()          # separado por espacos (virgula = decimal)
        else:
            campos = s.split(",")       # CSV com ponto decimal
        try:
            dados.append([float(c.strip().replace(",", ".")) for c in campos if c.strip() != ""])
        except ValueError:
            continue
    if not dados:
        raise ValueError(f"nenhum dado numerico em {caminho}")
    ncol = max(set(len(d) for d in dados), key=[len(d) for d in dados].count)
    m = np.array([d for d in dados if len(d) == ncol], dtype=float)
    if ncol < 2:
        raise ValueError("esperado ao menos 2 colunas (lambda, potencia)")
    t = os.path.getmtime(caminho)
    return [Espectro(t=t, wl=m[:, 0].copy(), pw=m[:, j].copy(), extra={"arquivo": caminho, "coluna": j})
            for j in range(1, ncol)]


def criar_fonte(tipo: str, **kw):
    tipo = tipo.lower()
    if tipo == "web":
        return FonteWeb(**{k: v for k, v in kw.items() if k in
                           ("host", "porta", "servico", "canal", "timeout", "basico")})
    if tipo == "dll":
        return FonteDLL(kw["caminho_dll"], kw.get("caminho_calib"), kw.get("tempo_integracao_us"),
                        kw.get("taxa_hz"), kw.get("modo_sensor"))
    if tipo in ("sim", "simulada", "simulador"):
        fbgs = [FBGSimulada(**f) for f in kw.get("fbgs", [])] or None
        return FonteSimulada(fbgs=fbgs, caminho_calib=kw.get("caminho_calib"),
                             fwhm_instr_nm=kw.get("fwhm_instr_nm", 0.25), ruido=kw.get("ruido", 0.15),
                             semente=kw.get("semente", 0))
    raise ValueError(f"fonte desconhecida: {tipo}")
