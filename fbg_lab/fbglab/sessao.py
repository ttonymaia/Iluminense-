"""Gravacao e leitura de ensaios.

Uma sessao e uma pasta com:
    sessao.npz     wl (N x P), pw (N x P), t (N), passo (N), temp_c (N), canal (N)
    sessao.json    metadados: protocolo, FBGs, rotulos dos passos, fonte, observacoes
    espectros/     (opcional) CSV por passo, prontos para Origin/Excel

O eixo lambda e guardado por espectro porque o ILLumiSense recalcula o eixo
conforme a temperatura interna do espectrometro.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re

import numpy as np

from .fontes import Espectro


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", s.strip(), flags=re.UNICODE)
    return s.strip("_") or "ensaio"


class Sessao:
    def __init__(self, pasta: str, meta: dict | None = None):
        self.pasta = pasta
        self.meta = meta or {}
        self.meta.setdefault("passos", [])
        self._wl: list[np.ndarray] = []
        self._pw: list[np.ndarray] = []
        self._t: list[float] = []
        self._passo: list[int] = []
        self._temp: list[float] = []
        self._canal: list[int] = []

    # ---------------------------------------------------------------- criacao
    @classmethod
    def nova(cls, raiz: str, nome: str, meta: dict | None = None) -> "Sessao":
        agora = _dt.datetime.now()
        pasta = os.path.join(raiz, f"{agora:%Y%m%d_%H%M%S}_{_slug(nome)}")
        os.makedirs(pasta, exist_ok=False)
        m = dict(meta or {})
        m.update({"nome": nome, "inicio": agora.isoformat(timespec="seconds")})
        return cls(pasta, m)

    def novo_passo(self, rotulo: str, **info) -> int:
        self.meta["passos"].append({"rotulo": rotulo, **info,
                                    "inicio": _dt.datetime.now().isoformat(timespec="seconds")})
        return len(self.meta["passos"]) - 1

    def adicionar(self, e: Espectro, passo: int):
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

    # ---------------------------------------------------------------- disco
    def salvar(self, csv_por_passo: bool = True):
        os.makedirs(self.pasta, exist_ok=True)
        if self.n:
            np.savez_compressed(os.path.join(self.pasta, "sessao.npz"), wl=self.wl, pw=self.pw, t=self.t,
                                passo=self.passo, temp_c=self.temp_c, canal=np.asarray(self._canal))
        self.meta["n_espectros"] = self.n
        self.meta["fim"] = _dt.datetime.now().isoformat(timespec="seconds")
        with open(os.path.join(self.pasta, "sessao.json"), "w", encoding="utf-8") as f:
            json.dump(self.meta, f, indent=2, ensure_ascii=False)
        if csv_por_passo and self.n:
            self.exportar_csv()

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
        if os.path.exists(npz):
            z = np.load(npz)
            canal = z["canal"] if "canal" in z else np.ones(len(z["t"]), int)
            for i in range(len(z["t"])):
                s._wl.append(z["wl"][i])
                s._pw.append(z["pw"][i])
                s._t.append(float(z["t"][i]))
                s._passo.append(int(z["passo"][i]))
                s._temp.append(float(z["temp_c"][i]))
                s._canal.append(int(canal[i]))
        return s
