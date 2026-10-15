"""Testes rapidos:  python -m pytest tests  (na pasta fbg_lab)."""

import os
import sys
import threading

import numpy as np
import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))

from fbglab import analise, calib, fisica, fontes  # noqa: E402
from fbglab.sessao import Sessao  # noqa: E402

CALIB = os.path.join(AQUI, "..", "..", "F13424-0019-001.calib")


@pytest.mark.skipif(not os.path.exists(CALIB), reason="arquivo .calib ausente")
def test_calib():
    c = calib.ler_calib(CALIB)
    assert c.serie == "F13424-0019-001"
    assert c.tabela_nm.shape == (851, 512)
    assert c.temps_c[0] == pytest.approx(-15.0) and c.temps_c[-1] == pytest.approx(70.0)
    wl = c.comprimentos_de_onda(25.0)
    assert np.all(np.diff(wl) > 0)
    assert 1500 < wl[0] < 1510 and 1585 < wl[-1] < 1600
    assert c.fsr_etalon_ghz() == pytest.approx(200.0, abs=0.5)


def test_tmm_rede_uniforme():
    lam0, L, dn = 1550.0, 10.0, 5e-5
    lam = np.linspace(1549, 1551, 4001)
    r = fisica.refletancia_tmm(lam, lam0, L, dn, 0.0)
    kl = np.pi * dn / (lam0 * 1e-9) * L * 1e-3
    assert r.max() == pytest.approx(np.tanh(kl) ** 2, rel=1e-3)


def test_tmm_chirp_alarga():
    lam = np.linspace(1548, 1552, 4001)
    z = np.linspace(-5, 5, 60)
    r0 = fisica.refletancia_tmm(lam, 1550, 10, 4e-5, np.zeros(60))
    r1 = fisica.refletancia_tmm(lam, 1550, 10, 4e-5, fisica.perfil_deformacao(z, 0, 50))
    larg = lambda r: np.ptp(lam[r > 0.5 * r.max()])  # noqa: E731
    assert larg(r1) > 2 * larg(r0)


def _media(src, n=10):
    ss = [src.espectro() for _ in range(n)]
    return ss[0].wl, np.mean([s.pw for s in ss], axis=0)


def test_recupera_gradiente():
    src = fontes.FonteSimulada([fontes.FBGSimulada(1550.0, 10.0, 4e-5)], semente=3)
    wl0, p0 = _media(src)
    j = analise.JanelaFBG("F", 1550.0, 1.5, 10.0)
    ref_tmm = analise.ajustar_tmm_referencia(wl0, p0, j)
    ref = analise.PerfilReferencia(wl0, p0, 1550.0, 1.5)
    src.definir_estado(0, eps_media_ue=400, gradiente_ue_mm=60)
    wl, p = _media(src)
    c = analise.metricas_pico(wl, p, 1550.5, 1.5)["lambda_cog"]
    tm = analise.ajustar_tmm_gradiente(wl, p, j, ref_tmm, centro_nm=c)
    assert tm["delta_eps_ue"] == pytest.approx(600, rel=0.1)
    assert tm["eps_media_ue"] == pytest.approx(400, abs=20)
    eps, pp = analise.distribuicao_deformacao(ref, wl, p, c, 1.5, 1550.0)
    st = analise.estatisticas_distribuicao(eps, pp)
    assert st["largura_equiv"] == pytest.approx(600, rel=0.25)
    ch = analise.ajustar_chirp(ref, wl, p, c, 1.5)
    k = fisica.sensibilidade_pm_por_ue(1550) * 1e-3
    assert ch["chirp_nm"] / k == pytest.approx(600, rel=0.3)


def test_leitura_texto_virgula(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("cabecalho\nWavelength\tPower\n1550,10\t1,5\n1550,27\t2,5\n1550,44\t3,5\n", encoding="latin-1")
    e = fontes.ler_texto_espectro(str(f))
    assert len(e) == 1
    assert e[0].wl[1] == pytest.approx(1550.27) and e[0].pw[2] == pytest.approx(3.5)


def test_web_e_sessao(tmp_path):
    from fbglab import servidor_simulado
    srv = servidor_simulado.criar_servidor(porta=0)
    porta = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        f = fontes.FonteWeb("127.0.0.1", porta, canal=1)
        e = f.espectro()
        assert len(e.wl) == len(e.pw) == 512
        pk, pw = f.picos()
        assert len(pk) == 3
        s = Sessao.nova(str(tmp_path), "t")
        k = s.novo_passo("ref")
        for _ in range(3):
            s.adicionar(f.espectro(), k)
        s.salvar()
        s2 = Sessao.abrir(s.pasta)
        assert s2.n == 3 and s2.rotulos() == ["ref"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_gravacao_bruta_recupera_apos_queda(tmp_path):
    """Cada espectro vai para bruto/*.csv na hora; sem salvar() (queda), abrir() recupera tudo."""
    src = fontes.FonteSimulada(semente=1)
    s = Sessao.nova(str(tmp_path), "queda", em_memoria=False)
    k = s.novo_passo("continuo")
    esperado = []
    for _ in range(7):
        e = src.espectro()
        esperado.append(e.pw.copy())
        s.adicionar(e, k)
    s.marcar_evento("fissura 1")
    s.gravador.flush()              # o que o SO ja tem; nao chama salvar() de proposito
    s2 = Sessao.abrir(s.pasta)
    assert s2.n == 7
    assert np.allclose(s2.pw[3], esperado[3])
    assert s2.meta["eventos"][0]["indice"] == 7
    b = s2.em_blocos(3)
    assert b.rotulos()[0].startswith("t=") and len(b.rotulos()) == 3


def test_cog_sem_travamento_no_pixel():
    src = fontes.FonteSimulada([fontes.FBGSimulada(1550.0, 10.0, 4e-5)], ruido=0.0)
    k = fisica.sensibilidade_pm_por_ue(1550) * 1e-3
    ref = None
    erros = []
    for eps in np.arange(0, 300, 13):
        src.definir_estado(0, eps_media_ue=eps)
        e = src.espectro()
        c = analise.metricas_pico(e.wl, e.pw, 1550.1, 1.5)["lambda_cog"]
        ref = c if ref is None else ref
        erros.append((c - ref) / k - eps)
    assert np.max(np.abs(erros)) < 3.0     # microstrain
