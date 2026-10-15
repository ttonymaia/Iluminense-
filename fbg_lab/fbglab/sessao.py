"""Gravacao e leitura de ensaios.

Uma sessao e uma pasta com:
    bruto/potencia.csv   UM ESPECTRO POR LINHA, gravado no disco no momento da leitura:
                         indice; t_epoch_s; passo; temp_C; canal; px0 ... px511
    bruto/lambda.csv     mesmo formato, com o comprimento de onda (nm) de cada pixel
    sessao.json          metadados: protocolo, FBGs, passos, eventos marcados; criado no inicio
    sessao.npz           copia binaria compacta (wl, pw, t, passo, temp_c, canal), gerada no fim
                         de cada passo / do ensaio
    espectros/           CSV por passo (lambda + cada espectro + media), prontos para Origin/Excel

Os CSV usam ';' como separador e virgula decimal (abrem direto no Excel em portugues).
Se o programa ou o PC cair no meio do ensaio, tudo o que ja foi lido esta em bruto/*.csv
(no maximo o ultimo espectro se perde) e Sessao.abrir() reconstroi a sessao a partir deles.

O eixo lambda e guardado por espectro porque o ILLumiSense recalcula o eixo
conforme a temperatura interna do espectrometro.
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import os
import re

import numpy as np

from .fontes import Espectro

_COLS_INFO = ["indice", "t_epoch_s", "passo", "temp_C", "canal"]


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", s.strip(), flags=re.UNICODE)
    return s.strip("_") or "ensaio"


def _fmt(v: float) -> str:
    if not np.isfinite(v):
        return ""
    return repr(float(v)).replace(".", ",")


class GravadorBruto:
    """Escreve cada espectro imediatamente em bruto/potencia.csv e bruto/lambda.csv."""

    def __init__(self, pasta: str, fsync_cada: int = 20):
        self.pasta = os.path.join(pasta, "bruto")
        os.makedirs(self.pasta, exist_ok=True)
        self.fsync_cada = max(1, int(fsync_cada))
        self._f = {}
        self.n_pixels = None
        self.n = 0

    def _abrir(self, n_pixels: int):
        self.n_pixels = n_pixels
        cab = ";".join(_COLS_INFO + [f"px{i}" for i in range(n_pixels)]) + "\n"
        for nome in ("potencia", "lambda"):
            caminho = os.path.join(self.pasta, f"{nome}.csv")
            novo = not os.path.exists(caminho)
            f = open(caminho, "a", encoding="utf-8", newline="")
            if novo:
                f.write(cab)
            self._f[nome] = f

    def escrever(self, indice: int, e: Espectro, passo: int):
        if self.n_pixels is None:
            self._abrir(len(e.pw))
        if len(e.pw) != self.n_pixels or len(e.wl) != self.n_pixels:
            raise ValueError(f"espectro com {len(e.pw)} pontos (esperado {self.n_pixels})")
        temp = np.nan if e.temp_c is None else e.temp_c
        info = f"{indice};{_fmt(e.t)};{passo};{_fmt(temp)};{int(e.canal)};"
        self._f["potencia"].write(info + ";".join(_fmt(v) for v in e.pw) + "\n")
        self._f["lambda"].write(info + ";".join(_fmt(v) for v in e.wl) + "\n")
        self.n += 1
        # flush a cada espectro: sobrevive a travamento do programa;
        # fsync periodico: sobrevive tambem a queda de energia (perde no maximo fsync_cada espectros)
        self.flush(fsync=self.n % self.fsync_cada == 0)

    def flush(self, fsync: bool = True):
        for f in self._f.values():
            f.flush()
            if fsync:
                os.fsync(f.fileno())

    def fechar(self):
        for f in self._f.values():
            f.flush()
            f.close()
        self._f = {}


def _ler_csv_bruto(caminho: str) -> tuple[np.ndarray, np.ndarray]:
    with open(caminho, encoding="utf-8") as f:
        f.readline()
        linhas = [ln for ln in f if ln.strip()]
    if not linhas:
        return np.zeros((0, len(_COLS_INFO))), np.zeros((0, 0))
    # descarta uma eventual ultima linha incompleta (queda de energia no meio da escrita)
    ncol = linhas[0].count(";") + 1
    linhas = [ln for ln in linhas if ln.count(";") + 1 == ncol]
    txt = "".join(linhas).replace(",", ".").replace(";;", ";nan;").replace(";;", ";nan;")
    m = np.loadtxt(io.StringIO(txt), delimiter=";", ndmin=2)
    k = len(_COLS_INFO)
    return m[:, :k], m[:, k:]


class Sessao:
    def __init__(self, pasta: str, meta: dict | None = None, em_memoria: bool = True):
        self.pasta = pasta
        self.meta = meta or {}
        self.meta.setdefault("passos", [])
        self.meta.setdefault("eventos", [])
        self.em_memoria = em_memoria
        self.gravador: GravadorBruto | None = None
        self._wl: list[np.ndarray] = []
        self._pw: list[np.ndarray] = []
        self._t: list[float] = []
        self._passo: list[int] = []
        self._temp: list[float] = []
        self._canal: list[int] = []

    # ---------------------------------------------------------------- criacao
    @classmethod
    def nova(cls, raiz: str, nome: str, meta: dict | None = None, gravar_bruto: bool = True,
             em_memoria: bool = True) -> "Sessao":
        agora = _dt.datetime.now()
        pasta = os.path.join(raiz, f"{agora:%Y%m%d_%H%M%S}_{_slug(nome)}")
        os.makedirs(pasta, exist_ok=False)
        m = dict(meta or {})
        m.update({"nome": nome, "inicio": agora.isoformat(timespec="seconds")})
        s = cls(pasta, m, em_memoria=em_memoria)
        if gravar_bruto:
            s.gravador = GravadorBruto(pasta)
        s.salvar_meta()
        return s

    def novo_passo(self, rotulo: str, **info) -> int:
        self.meta["passos"].append({"rotulo": rotulo, **info,
                                    "inicio": _dt.datetime.now().isoformat(timespec="seconds"),
                                    "indice_inicial": self.n})
        self.salvar_meta()
        return len(self.meta["passos"]) - 1

    def marcar_evento(self, texto: str, t: float | None = None) -> dict:
        """Anota um evento (ex.: 'primeira fissura visivel') no instante atual / espectro atual."""
        t = _dt.datetime.now().timestamp() if t is None else t
        ev = {"t_epoch_s": t, "hora": _dt.datetime.fromtimestamp(t).isoformat(timespec="seconds"),
              "indice": self.n, "texto": texto or "marca"}
        self.meta["eventos"].append(ev)
        self.salvar_meta()
        return ev

    def adicionar(self, e: Espectro, passo: int):
        if self.gravador is not None:
            self.gravador.escrever(self.n, e, passo)
        if self.em_memoria:
            self._wl.append(np.asarray(e.wl, float))
            self._pw.append(np.asarray(e.pw, float))
        self._t.append(float(e.t))
        self._passo.append(int(passo))
        self._temp.append(np.nan if e.temp_c is None else float(e.temp_c))
        self._canal.append(int(e.canal))

    # ---------------------------------------------------------------- acesso
    @property
    def n(self) -> int:
        return len(self._t)

    @property
    def wl(self) -> np.ndarray:
        return np.vstack(self._wl)

    @property
    def pw(self) -> np.ndarray:
        return np.vstack(self._pw)

    @property
    def t(self) -> np.ndarray:
        return np.asarray(self._t)

    @property
    def passo(self) -> np.ndarray:
        return np.asarray(self._passo, dtype=int)

    @property
    def temp_c(self) -> np.ndarray:
        return np.asarray(self._temp)

    def rotulos(self) -> list[str]:
        return [p["rotulo"] for p in self.meta["passos"]]

    def espectros_do_passo(self, k: int) -> tuple[np.ndarray, np.ndarray]:
        sel = self.passo == k
        return self.wl[sel], self.pw[sel]

    def media_do_passo(self, k: int) -> tuple[np.ndarray, np.ndarray]:
        """Espectro medio do passo (reamostrado no eixo do primeiro espectro)."""
        wl, pw = self.espectros_do_passo(k)
        if len(wl) == 0:
            raise ValueError(f"passo {k} sem espectros")
        base = wl[0]
        acc = np.zeros_like(base)
        for w, p in zip(wl, pw):
            acc += np.interp(base, w, p) if not np.array_equal(w, base) else p
        return base, acc / len(wl)

    def em_blocos(self, n_por_bloco: int) -> "Sessao":
        """Copia da sessao em que cada bloco de N espectros consecutivos vira um 'passo'
        (para analisar ensaios continuos ao longo do tempo). Nao altera os arquivos."""
        s = Sessao(self.pasta, json.loads(json.dumps(self.meta)), em_memoria=True)
        s._wl, s._pw, s._t, s._temp, s._canal = self._wl, self._pw, self._t, self._temp, self._canal
        n_blocos = int(np.ceil(self.n / n_por_bloco))
        s._passo = [i // n_por_bloco for i in range(self.n)]
        t0 = self._t[0] if self._t else 0.0
        s.meta["passos"] = [{"rotulo": f"t={self._t[min(b * n_por_bloco, self.n - 1)] - t0:.0f}s",
                             "indice_inicial": b * n_por_bloco} for b in range(n_blocos)]
        return s

    # ---------------------------------------------------------------- disco
    def salvar_meta(self):
        os.makedirs(self.pasta, exist_ok=True)
        self.meta["n_espectros"] = self.n
        tmp = os.path.join(self.pasta, "sessao.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.meta, f, indent=2, ensure_ascii=False)
        os.replace(tmp, os.path.join(self.pasta, "sessao.json"))

    def salvar(self, csv_por_passo: bool = True):
        os.makedirs(self.pasta, exist_ok=True)
        if self.gravador is not None:
            self.gravador.flush()
        if self.em_memoria and self.n:
            tmp = os.path.join(self.pasta, "sessao_tmp.npz")
            np.savez_compressed(tmp, wl=self.wl, pw=self.pw, t=self.t, passo=self.passo,
                                temp_c=self.temp_c, canal=np.asarray(self._canal))
            os.replace(tmp, os.path.join(self.pasta, "sessao.npz"))
        self.meta["fim"] = _dt.datetime.now().isoformat(timespec="seconds")
        self.salvar_meta()
        if csv_por_passo and self.em_memoria and self.n:
            self.exportar_csv()

    def fechar(self):
        if self.gravador is not None:
            self.gravador.fechar()
            self.gravador = None

    def exportar_csv(self, decimal: str = ","):
        """Um CSV por passo: coluna lambda (do 1o espectro) + uma coluna por espectro + media."""
        d = os.path.join(self.pasta, "espectros")
        os.makedirs(d, exist_ok=True)
        for k, rot in enumerate(self.rotulos()):
            wl, pw = self.espectros_do_passo(k)
            if len(wl) == 0:
                continue
            base = wl[0]
            cols = [np.interp(base, w, p) for w, p in zip(wl, pw)]
            m = np.column_stack([base] + cols + [np.mean(cols, axis=0)])
            cab = ";".join(["lambda_nm"] + [f"esp{i+1}" for i in range(len(cols))] + ["media"])
            txt = "\n".join(";".join(f"{v:.6f}" for v in linha) for linha in m)
            if decimal == ",":
                txt = txt.replace(".", ",")
            nome = f"passo{k:02d}_{_slug(rot)}.csv"
            with open(os.path.join(d, nome), "w", encoding="utf-8") as f:
                f.write(cab + "\n" + txt + "\n")

    @classmethod
    def abrir(cls, pasta: str) -> "Sessao":
        with open(os.path.join(pasta, "sessao.json"), encoding="utf-8") as f:
            meta = json.load(f)
        s = cls(pasta, meta)
        npz = os.path.join(pasta, "sessao.npz")
        bruto_p = os.path.join(pasta, "bruto", "potencia.csv")
        bruto_l = os.path.join(pasta, "bruto", "lambda.csv")
        n_npz = 0
        if os.path.exists(npz):
            z = np.load(npz)
            n_npz = len(z["t"])
        n_bruto = 0
        if os.path.exists(bruto_p) and os.path.exists(bruto_l):
            with open(bruto_p, "rb") as f:
                n_bruto = max(0, sum(1 for _ in f) - 1)
        usar_bruto = n_bruto > n_npz      # ensaio continuo, ou interrompido depois do ultimo npz
        if usar_bruto:
            # sessao continua (sem npz) ou interrompida antes de gravar o npz
            info, pw = _ler_csv_bruto(bruto_p)
            info_l, wl = _ler_csv_bruto(bruto_l)
            n = min(len(pw), len(wl))
            for i in range(n):
                s._wl.append(wl[i])
                s._pw.append(pw[i])
                s._t.append(float(info[i, 1]))
                s._passo.append(int(info[i, 2]))
                s._temp.append(float(info[i, 3]))
                s._canal.append(int(info[i, 4]))
        elif n_npz:
            canal = z["canal"] if "canal" in z else np.ones(n_npz, int)
            for i in range(n_npz):
                s._wl.append(z["wl"][i])
                s._pw.append(z["pw"][i])
                s._t.append(float(z["t"][i]))
                s._passo.append(int(z["passo"][i]))
                s._temp.append(float(z["temp_c"][i]))
                s._canal.append(int(canal[i]))
        return s
