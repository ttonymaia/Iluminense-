"""Linha de comando:  python -m fbglab <comando> [opcoes]

Comandos
  sondar           procura o servico web do ILLumiSense e testa os canais
  calib            mostra/exporta a tabela pixel -> lambda do arquivo .calib
  ao-vivo          janela com espectro bruto + cascata em tempo real
  adquirir         gravacao continua de espectros (ensaio dinamico)
  rotina           rotina de laboratorio guiada por protocolo JSON (passos de carga)
  analisar         analisa uma sessao gravada (metricas, chirp, figuras, relatorio)
  importar         cria uma sessao a partir de arquivos de texto (ex.: "Save spectrum")
  servidor-simulado  imita o ILLumiSense (porta 8002) para treinar sem o equipamento
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

from . import analise, calib, fontes, rotina
from .sessao import Sessao

_AQUI = os.path.dirname(os.path.abspath(__file__))
_RAIZ_REPO = os.path.abspath(os.path.join(_AQUI, "..", ".."))


def _calib_padrao() -> str | None:
    achados = glob.glob(os.path.join(_RAIZ_REPO, "*.calib"))
    return achados[0] if achados else None


def _args_fonte(p: argparse.ArgumentParser):
    g = p.add_argument_group("fonte do espectro")
    g.add_argument("--fonte", default="web", choices=["web", "dll", "sim"],
                   help="web = servico do ILLumiSense (padrao); dll = Sense2020Dll direto; sim = simulador")
    g.add_argument("--host", default="localhost")
    g.add_argument("--porta", type=int, default=8002)
    g.add_argument("--canal", type=int, default=1)
    g.add_argument("--basico", action="store_true", help="usa GetSpectrumBasic (mais rapido, eixo fixo)")
    g.add_argument("--dll", default=os.path.join(_RAIZ_REPO, "Sense2020Dll.dll"))
    g.add_argument("--calib", default=None, help="arquivo .calib (padrao: o da pasta do ILLumiSense)")
    g.add_argument("--t-int", type=int, default=None, help="(dll) tempo de integracao em us")


def _fonte(a):
    cal = a.calib or _calib_padrao()
    if a.fonte == "web":
        return fontes.FonteWeb(a.host, a.porta, canal=a.canal, basico=a.basico)
    if a.fonte == "dll":
        return fontes.FonteDLL(a.dll, cal, a.t_int)
    return fontes.FonteSimulada(caminho_calib=cal)


def _janelas(lista: list[str] | None, protocolo: dict | None = None) -> list[analise.JanelaFBG] | None:
    """--fbg NOME:CENTRO[:JANELA_TOTAL[:COMPRIMENTO_MM]]  (ex.: --fbg FBG1:1550.1:3:10)"""
    if lista:
        out = []
        for s in lista:
            p = s.split(":")
            out.append(analise.JanelaFBG(p[0], float(p[1]), float(p[2]) / 2 if len(p) > 2 else 1.5,
                                         float(p[3]) if len(p) > 3 else 10.0))
        return out
    if protocolo and protocolo.get("fbgs"):
        return [analise.JanelaFBG.de_dict(f) for f in protocolo["fbgs"]]
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fbglab", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sondar", help="procura o servico web do ILLumiSense")
    p.add_argument("--host", default="localhost")
    p.add_argument("--porta", type=int, nargs="*", default=[8002, 8001, 8080, 80])

    p = sub.add_parser("calib", help="le o arquivo .calib")
    p.add_argument("arquivo", nargs="?", default=None)
    p.add_argument("--csv", default=None, help="exporta a tabela para CSV")

    p = sub.add_parser("ao-vivo", help="espectro em tempo real")
    _args_fonte(p)
    p.add_argument("--fbg", action="append", help="NOME:CENTRO[:JANELA[:L_mm]] (repetir para varias)")
    p.add_argument("--saida", default="ensaios")

    p = sub.add_parser("adquirir", help="gravacao continua")
    _args_fonte(p)
    p.add_argument("--nome", default="continuo")
    p.add_argument("--duracao", type=float, default=None, help="segundos (padrao: ate Ctrl+C)")
    p.add_argument("--intervalo", type=float, default=0.0, help="pausa entre espectros (s)")
    p.add_argument("--saida", default="ensaios")

    p = sub.add_parser("rotina", help="rotina de laboratorio por protocolo")
    p.add_argument("protocolo")
    p.add_argument("--fonte", default=None, choices=["web", "dll", "sim"], help="sobrescreve a fonte do protocolo")
    p.add_argument("--nao-interativo", action="store_true", help="nao pede ENTER entre passos")
    p.add_argument("--sem-analise", action="store_true")

    p = sub.add_parser("analisar", help="analisa uma sessao gravada")
    p.add_argument("sessao", help="pasta da sessao")
    p.add_argument("--fbg", action="append", help="NOME:CENTRO[:JANELA[:L_mm]]")
    p.add_argument("--ref", type=int, default=0, help="indice do passo de referencia")
    p.add_argument("--sem-tmm", action="store_true", help="pula o ajuste T-matrix (mais rapido)")

    p = sub.add_parser("importar", help="cria sessao a partir de arquivos de texto (um passo por arquivo)")
    p.add_argument("arquivos", nargs="+")
    p.add_argument("--nome", default="importado")
    p.add_argument("--saida", default="ensaios")

    p = sub.add_parser("servidor-simulado", help="imita o servico web do ILLumiSense")
    p.add_argument("--porta", type=int, default=8002)
    p.add_argument("--calib", default=None)

    a = ap.parse_args(argv)

    if a.cmd == "sondar":
        for linha in fontes.sondar_web(a.host, a.porta):
            print(linha)
        return 0

    if a.cmd == "calib":
        arq = a.arquivo or _calib_padrao()
        if not arq:
            print("informe o arquivo .calib")
            return 1
        c = calib.ler_calib(arq)
        print(c.resumo())
        if a.csv:
            calib.exportar_csv(c, a.csv)
            print(f"tabela exportada: {a.csv}")
        return 0

    if a.cmd == "ao-vivo":
        from . import ao_vivo
        ao_vivo.executar(_fonte(a), _janelas(a.fbg), canal=a.canal, pasta_saida=a.saida)
        return 0

    if a.cmd == "adquirir":
        rotina.aquisicao_continua(_fonte(a), a.saida, a.nome, a.duracao, a.intervalo, canal=a.canal)
        return 0

    if a.cmd == "rotina":
        prot = rotina.carregar_protocolo(a.protocolo)
        if a.fonte:
            prot.setdefault("fonte", {})["tipo"] = a.fonte
        if prot.get("fonte", {}).get("tipo") in ("sim", "dll") and not prot["fonte"].get("caminho_calib"):
            prot["fonte"]["caminho_calib"] = _calib_padrao()
        if prot.get("fonte", {}).get("tipo") == "dll":
            prot["fonte"].setdefault("caminho_dll", os.path.join(_RAIZ_REPO, "Sense2020Dll.dll"))
        rotina.executar(prot, interativo=not a.nao_interativo, analisar=not a.sem_analise)
        return 0

    if a.cmd == "analisar":
        s = Sessao.abrir(a.sessao)
        prot = s.meta.get("protocolo")
        jan = _janelas(a.fbg, prot)
        if not jan:
            wl, pw = s.media_do_passo(a.ref)
            jan = [analise.JanelaFBG(f"FBG{i+1}", float(c), 1.5) for i, c in enumerate(analise.detectar_picos(wl, pw))]
            print("FBGs detectadas automaticamente: " + ", ".join(f"{j.nome}={j.centro_nm:.3f}" for j in jan))
        rotina.relatorio(s, jan, prot, com_tmm=False if a.sem_tmm else None, passo_ref=a.ref)
        return 0

    if a.cmd == "importar":
        s = Sessao.nova(a.saida, a.nome, {"modo": "importado"})
        for arq in a.arquivos:
            esp = fontes.ler_texto_espectro(arq)
            k = s.novo_passo(os.path.splitext(os.path.basename(arq))[0], arquivo=arq)
            for e in esp:
                s.adicionar(e, k)
            print(f"{arq}: {len(esp)} espectro(s), {len(esp[0].wl)} pontos")
        s.salvar()
        print(f"Sessao criada: {s.pasta}\nAgora rode: python -m fbglab analisar \"{s.pasta}\" --fbg NOME:CENTRO")
        return 0

    if a.cmd == "servidor-simulado":
        from . import servidor_simulado
        servidor_simulado.executar(a.porta, a.calib or _calib_padrao())
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
