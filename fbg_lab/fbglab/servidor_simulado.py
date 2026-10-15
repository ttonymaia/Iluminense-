"""Servidor HTTP que imita o servico web ILLWS do ILLumiSense (para treino e testes sem o equipamento).

Responde nas mesmas rotas e com o mesmo formato JSON descobertos no ILLWS.lvws:
    /ILLWS/GetSpectrum/<canal>, /ILLWS/GetSpectrumBasic/<canal>, /ILLWS/GetPeaks/<canal>,
    /ILLWS/GetHWInformation/, /ILLWS/GetSerialNumber/, ...
Roteiro simulado (repete a cada 60 s), para treinar a leitura das figuras:
    FBG1: deformacao uniforme em rampa;
    FBG2: rampa + gradiente crescente (chirp) e, a partir de 30 s, uma "fissura" que abre no
          meio da rede (salto de deformacao crescente -> o pico alarga e se divide em dois);
    FBG3: livre (referencia de temperatura).
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from . import analise
from .fontes import FonteSimulada, FBGSimulada


def criar_servidor(porta: int = 8002, caminho_calib: str | None = None, rampa_ue_s: float = 20.0,
                   host: str = "127.0.0.1") -> ThreadingHTTPServer:
    fonte = FonteSimulada([FBGSimulada(1530.0, 10.0, 3e-5), FBGSimulada(1550.0, 10.0, 4e-5),
                           FBGSimulada(1570.0, 10.0, 3e-5)], caminho_calib=caminho_calib, semente=None)
    t0 = time.time()
    trava = threading.Lock()

    def espectro():
        t = time.time() - t0
        tc = t % 60.0
        with trava:
            fonte.definir_estado(0, eps_media_ue=rampa_ue_s * tc)
            fonte.definir_estado(1, eps_media_ue=rampa_ue_s * tc, gradiente_ue_mm=rampa_ue_s * tc / 30,
                                 degrau_ue=max(0.0, tc - 30.0) * 60.0)
            return fonte.espectro()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):  # noqa: N802
            partes = [p for p in self.path.split("/") if p]
            if not partes or partes[0] != "ILLWS":
                return self._json({"erro": "rota desconhecida"}, 404)
            rota = partes[1] if len(partes) > 1 else ""
            if rota == "GetSpectrum":
                e = espectro()
                return self._json({"Error": 0, "SpecWav": e.wl.tolist(), "SpecPow": e.pw.tolist()})
            if rota == "GetSpectrumBasic":
                return self._json({"Error": 0, "SpecPow": espectro().pw.tolist()})
            if rota == "GetPeaks":
                e = espectro()
                pk = analise.detectar_picos(e.wl, e.pw)
                pw = [float(np.interp(p, e.wl, e.pw)) for p in pk]
                return self._json({"Error": 0, "PeakWavelengths": pk.tolist(), "PeakPowers": pw})
            if rota == "GetHWInformation":
                return self._json({"Error": 0, "TypeHW": "SIMULADO", "NumberOfChannels": 1, "ScanRates": [100]})
            if rota == "GetSerialNumber":
                return self._json({"Error": 0, "SN": "SIM-0000"})
            if rota in ("GetFWVersion", "GetSWVersion"):
                return self._json({"Error": 0, rota[3:]: "sim"})
            if rota == "GetScanRate":
                return self._json({"Error": 0, "ScanRate": 100})
            if rota == "GetActiveChannels":
                return self._json({"Error": 0, "ActiveChannels": [1]})
            return self._json({"Error": 1}, 404)

    srv = ThreadingHTTPServer((host, porta), H)
    return srv


def executar(porta: int = 8002, caminho_calib: str | None = None):
    srv = criar_servidor(porta, caminho_calib)
    print(f"Servidor ILLWS simulado em http://127.0.0.1:{porta}/ILLWS/  (Ctrl+C para parar)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    srv.server_close()
