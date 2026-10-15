"""Rotina de laboratorio guiada por protocolo (JSON).

Fluxo:
  1. verifica a conexao e mostra o espectro/picos encontrados;
  2. passo de REFERENCIA (sem carga) com N espectros;
  3. para cada passo do protocolo: pede ao operador para aplicar a carga
     (modo "manual") ou espera o tempo programado (modo "temporizado"),
     aguarda a estabilizacao, adquire N espectros e mostra as metricas na hora;
  4. salva a sessao (npz + json + CSV por passo), roda a analise completa e
     gera figuras + relatorio.md.

Exemplo de protocolo: exemplos/protocolo_exemplo.json
"""

from __future__ import annotations

import json
import os
import threading
import time

import numpy as np

from . import analise, graficos
from .fontes import criar_fonte, FonteSimulada
from .sessao import Sessao


def carregar_protocolo(caminho: str) -> dict:
    with open(caminho, encoding="utf-8") as f:
        p = json.load(f)
    p.setdefault("espectros_por_passo", 20)
    p.setdefault("intervalo_s", 0.1)
    p.setdefault("espera_estabilizacao_s", 3.0)
    p.setdefault("modo", "manual")
    p.setdefault("pasta_saida", "ensaios")
    p.setdefault("passos", [])
    p.setdefault("referencia", {"rotulo": "referencia (sem carga)"})
    return p


def adquirir_n(fonte, sessao: Sessao, passo: int, n: int, intervalo_s: float, canal=None, eco=True) -> int:
    ok = 0
    falhas = 0
    while ok < n:
        try:
            e = fonte.espectro(canal)
        except Exception as ex:  # noqa: BLE001
            falhas += 1
            print(f"   ! falha na leitura ({falhas}): {ex}")
            if falhas > max(5, n):
                raise
            time.sleep(0.5)
            continue
        sessao.adicionar(e, passo)
        ok += 1
        if eco:
            print(f"\r   espectros: {ok}/{n}", end="", flush=True)
        if intervalo_s > 0:
            time.sleep(intervalo_s)
    if eco:
        print()
    return ok


def _tabela_rapida(sessao: Sessao, passo: int, janelas: list[analise.JanelaFBG], centros: dict):
    """Metricas do espectro medio do passo. centros[nome] = centro atual (rastreado),
    centros[nome + '_ref'] = centro da referencia."""
    wl, pw = sessao.media_do_passo(passo)
    print(f"   {'FBG':<10}{'lambda CoG (nm)':>16}{'desloc. (pm)':>14}{'FWHM (pm)':>11}{'W20 (pm)':>10}"
          f"{'assim. (pm)':>12}{'subpicos':>9}")
    for j in janelas:
        c = centros.get(j.nome, j.centro_nm)
        m = analise.metricas_pico(wl, pw, c, j.meia_largura_nm)
        if j.rastrear and np.isfinite(m["lambda_centro"]):
            centros[j.nome] = m["lambda_centro"]
        d = (m["lambda_cog"] - centros.get(j.nome + "_ref", c)) * 1e3
        print(f"   {j.nome:<10}{m['lambda_cog']:>16.4f}{d:>14.1f}{m['fwhm']*1e3:>11.1f}{m['w20']*1e3:>10.1f}"
              f"{m['assim_cog_meio']*1e3:>12.1f}{m['n_subpicos']:>9d}")


def executar(protocolo: dict, fonte=None, interativo: bool = True, analisar: bool = True) -> Sessao:
    janelas = [analise.JanelaFBG.de_dict(f) for f in protocolo["fbgs"]]
    src_cfg = dict(protocolo.get("fonte", {"tipo": "web"}))
    fonte = fonte or criar_fonte(src_cfg.pop("tipo", "web"), **src_cfg)
    canal = protocolo.get("fonte", {}).get("canal")
    sessao = Sessao.nova(protocolo["pasta_saida"], protocolo.get("ensaio", "ensaio"), {"protocolo": protocolo})
    print(f"Sessao: {sessao.pasta}")

    # 1. verificacao
    e = fonte.espectro(canal)
    print(f"Conexao OK: {len(e.wl)} pontos, {e.wl.min():.2f}-{e.wl.max():.2f} nm, "
          f"potencia max {np.max(e.pw):.3g}")
    picos = analise.detectar_picos(e.wl, e.pw)
    print("Picos detectados (nm): " + ", ".join(f"{p:.3f}" for p in picos))
    for j in janelas:
        if not np.any(np.abs(picos - j.centro_nm) < j.meia_largura_nm):
            print(f"  AVISO: nenhum pico perto de {j.nome} ({j.centro_nm} nm +- {j.meia_largura_nm})")

    simul = isinstance(fonte, FonteSimulada)
    n = int(protocolo["espectros_por_passo"])
    dt = float(protocolo["intervalo_s"])
    centros: dict = {}

    def _passo(rotulo: str, info: dict, n_esp: int):
        k = sessao.novo_passo(rotulo, **info)
        print(f"\n== Passo {k}: {rotulo}")
        if simul and "simulacao" in info:
            for i, est in enumerate(info["simulacao"]):
                fonte.definir_estado(i, **est)
        if protocolo["modo"] == "manual" and interativo:
            input("   Aplique a condicao e pressione ENTER para adquirir... ")
        elif protocolo["modo"] == "temporizado" and k > 0:
            dur = float(info.get("duracao_s", protocolo.get("duracao_passo_s", 0)))
            if dur > 0:
                print(f"   aguardando {dur:.0f} s (modo temporizado)")
                time.sleep(dur)
        esp = float(info.get("espera_s", protocolo["espera_estabilizacao_s"]))
        if esp > 0 and not simul:
            print(f"   estabilizando {esp:.1f} s")
            time.sleep(esp)
        adquirir_n(fonte, sessao, k, n_esp, dt, canal)
        if k == 0:
            wl, pw = sessao.media_do_passo(0)
            for j in janelas:
                m = analise.metricas_pico(wl, pw, j.centro_nm, j.meia_largura_nm)
                centros[j.nome] = m["lambda_centro"] if np.isfinite(m["lambda_centro"]) else j.centro_nm
                centros[j.nome + "_ref"] = centros[j.nome]
        _tabela_rapida(sessao, k, janelas, centros)
        sessao.salvar(csv_por_passo=False)   # salva a cada passo (seguranca)

    ref = protocolo["referencia"]
    ref_info = {k: v for k, v in ref.items() if k not in ("rotulo", "n_espectros")}
    _passo(ref.get("rotulo", "referencia"), ref_info, int(ref.get("n_espectros", n)))
    try:
        for p in protocolo["passos"]:
            info = {k: v for k, v in p.items() if k != "rotulo"}
            _passo(p["rotulo"], info, int(p.get("n_espectros", n)))
    except KeyboardInterrupt:
        print("\nInterrompido pelo operador: salvando o que foi adquirido.")
    finally:
        sessao.salvar()
        sessao.fechar()
        fonte.fechar()

    if analisar and len(sessao.rotulos()) > 0:
        relatorio(sessao, janelas, protocolo)
    return sessao


def relatorio(sessao: Sessao, janelas: list[analise.JanelaFBG], protocolo: dict | None = None,
              com_tmm: bool | None = None, passo_ref: int = 0) -> str:
    print("\nAnalisando...")
    if com_tmm is None:
        com_tmm = bool((protocolo or {}).get("ajuste_tmm", True))
    n_ajustes = len(set(sessao.passo.tolist())) * len(janelas)
    if com_tmm and n_ajustes > 60:
        print(f"   {n_ajustes} ajustes T-matrix (~{n_ajustes / 60:.0f} min). Para ir mais rapido: --sem-tmm "
              "ou blocos maiores.")
    res = analise.analisar_sessao(sessao, janelas, passo_ref=passo_ref, com_tmm=com_tmm)
    analise.salvar_tabela_csv(res["por_espectro"], os.path.join(sessao.pasta, "metricas_por_espectro.csv"))
    analise.salvar_tabela_csv(res["por_passo"], os.path.join(sessao.pasta, "metricas_por_passo.csv"))
    figs = graficos.gerar_todos(sessao, res)
    linhas = [f"# Relatorio - {sessao.meta.get('nome', '')}", "",
              f"- Inicio: {sessao.meta.get('inicio', '')}", f"- Espectros: {sessao.n}",
              f"- Passos: {len(sessao.rotulos())}", ""]
    if protocolo and protocolo.get("observacoes"):
        linhas += ["## Observacoes", "", protocolo["observacoes"], ""]
    for j in janelas:
        linhas += [f"## {j.nome} (L = {j.comprimento_mm} mm, lambda ref = {res['centros_ref'][j.nome]:.4f} nm)", ""]
        tr = res["tmm_ref"].get(j.nome)
        if tr and "erro" not in tr:
            linhas += [f"Ajuste T-matrix da referencia: dn_ac = {tr['dn_ac']:.2e}, "
                       f"refletividade de pico = {tr['refletividade_pico']*100:.1f} %, "
                       f"FWHM do instrumento = {tr['fwhm_instr']*1e3:.0f} pm "
                       "(dn_ac, refletividade e FWHM sao parcialmente degenerados entre si: use-os apenas como "
                       "parametros do modelo, nao como medida da rede)", ""]
        linhas += ["| passo | desloc. (pm) | eps medio (ue) | FWHM (pm) | W20 (pm) | assim. (pm) | "
                   "Delta eps modelo (ue) | Delta eps T-matrix (ue) | Delta eps dist. (ue) | subpicos | salto entre subpicos (ue) |",
                   "|---|---|---|---|---|---|---|---|---|---|---|"]
        for l in res["por_passo"]:
            if l["fbg"] != j.nome:
                continue

            def f(k, fat=1.0, d=1):
                v = l.get(k, np.nan)
                return "-" if v is None or not np.isfinite(v) else f"{v*fat:.{d}f}"
            linhas.append(f"| {l['rotulo']} | {f('deslocamento_pm')} | {f('eps_cog_ue')} | {f('fwhm',1e3)} | "
                          f"{f('w20',1e3)} | {f('assim_cog_meio',1e3)} | "
                          f"{f('delta_eps_modelo_ue') if l.get('n_subpicos', 1) <= 1 else '(pico dividido)'} | "
                          f"{f('tmm_delta_eps_ue')} | {f('dist_largura_equiv')} | {l.get('n_subpicos', '-')} | "
                          f"{f('salto_subpicos_ue')} |")
        linhas.append("")
    linhas += ["## Figuras", ""] + [f"![{os.path.basename(p)}](figuras/{os.path.basename(p)})" for p in figs]
    linhas += ["", "## Como ler", "",
               "- **deslocamento / eps medio**: o que o ILLumiSense ja entrega (pico).",
               "- **FWHM e W20** crescendo em relacao a referencia: o pico alargou -> deformacao nao uniforme (chirp).",
               "- **assimetria** diferente de zero: perfil de deformacao nao linear ou parte da rede descolada.",
               "- **Delta eps T-matrix**: diferenca de deformacao entre as pontas da rede supondo gradiente linear "
               "(estimativa quantitativa; o sinal do gradiente nao e observavel pela reflexao).",
               "- **Delta eps modelo**: ajuste gaussiana (*) retangulo integrado em cada pixel (rapido, nao 'trava' na "
               "grade de pixels; tende a subestimar ~5-15 %). Tambem calculado espectro a espectro "
               "(metricas_por_espectro.csv) para ensaios continuos.",
               "- **subpicos / salto entre subpicos**: o pico se dividiu. A separacao convertida em deformacao e o "
               "salto de deformacao entre trechos da rede: assinatura de fissura cruzando a FBG (ou descolamento). "
               "Nesse caso o lambda 'cravado' (CoG) fica ambiguo e pode pular entre os subpicos.",
               "- **FWHM e W20** oscilam ~+-40 pm quando o pico atravessa os pixels (amostragem): so leve a serio "
               "variacoes bem maiores, ou use os estimadores por modelo.",
               "- **Delta eps dist.**: sqrt(12) x desvio-padrao da distribuicao de deformacao obtida por deconvolucao "
               "(sem impor perfil; para gradiente linear equivale a Delta eps). Na referencia ele mostra o piso de "
               "resolucao do sistema (tipicamente 50-100 ue).",
               ""]
    p = os.path.join(sessao.pasta, "relatorio.md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(linhas))
    print(f"Relatorio: {p}")
    for fg in figs:
        print(f"Figura:    {fg}")
    return p


def _leitor_de_eventos(sessao: Sessao, trava: threading.Lock):
    """Thread: cada linha digitada (ENTER) vira um evento com hora e indice do espectro."""
    while True:
        try:
            txt = input()
        except (EOFError, KeyboardInterrupt, OSError):
            return
        with trava:
            ev = sessao.marcar_evento(txt.strip())
        print(f"\n   >> evento marcado no espectro {ev['indice']} ({ev['hora']}): {ev['texto']}")


def aquisicao_continua(fonte, pasta_saida: str, nome: str, duracao_s: float | None, intervalo_s: float,
                       canal=None, rotulo: str = "continuo", marcar_eventos: bool = True,
                       meta: dict | None = None) -> Sessao:
    """Grava espectros continuamente (ensaio de fissuracao, fluencia, ciclagem). Ctrl+C encerra.

    Cada espectro bruto vai direto para bruto/potencia.csv e bruto/lambda.csv (nao fica so na
    memoria). Durante a gravacao, digite um texto e ENTER para marcar um evento
    (ex.: "fissura 1 visivel", "carga 12 kN"); os eventos ficam no sessao.json e aparecem nas figuras.
    """
    m = {"modo": "continuo", "intervalo_s": intervalo_s}
    m.update(meta or {})
    sessao = Sessao.nova(pasta_saida, nome, m, gravar_bruto=True, em_memoria=False)
    k = sessao.novo_passo(rotulo)
    trava = threading.Lock()
    print(f"Sessao: {sessao.pasta}")
    print("Gravando. Digite um texto + ENTER para marcar um evento; Ctrl+C para encerrar.")
    if marcar_eventos:
        threading.Thread(target=_leitor_de_eventos, args=(sessao, trava), daemon=True).start()
    t0 = time.time()
    falhas_seguidas = 0
    try:
        while duracao_s is None or time.time() - t0 < duracao_s:
            try:
                e = fonte.espectro(canal)
            except Exception as ex:  # noqa: BLE001
                falhas_seguidas += 1
                print(f"\n! falha na leitura ({falhas_seguidas}): {ex}")
                time.sleep(0.5)
                continue
            falhas_seguidas = 0
            with trava:
                sessao.adicionar(e, k)
            if sessao.n % 10 == 0:
                taxa = sessao.n / max(time.time() - t0, 1e-9)
                print(f"\r{sessao.n} espectros  ({taxa:.1f}/s)  {time.time() - t0:7.0f} s", end="", flush=True)
            if intervalo_s > 0:
                time.sleep(intervalo_s)
    except KeyboardInterrupt:
        pass
    print()
    with trava:
        sessao.salvar(csv_por_passo=False)
        sessao.fechar()
    fonte.fechar()
    print(f"{sessao.n} espectros brutos salvos em {os.path.join(sessao.pasta, 'bruto')}")
    print(f"Para analisar ao longo do tempo: python -m fbglab analisar \"{sessao.pasta}\" --blocos 20")
    return sessao
