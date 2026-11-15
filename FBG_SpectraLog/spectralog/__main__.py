"""Ponto de entrada.

    python -m spectralog                      interface gráfica
    python -m spectralog --simulador          simula o ILLumiSense (porta 8002)
    python -m spectralog --headless --taxa 2  grava sem interface (Ctrl+C para parar)
"""
from __future__ import annotations

import argparse
import signal
import sys
import time


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="FBG_SpectraLog", description="Gravação de espectro bruto do ILLumiSense")
    ap.add_argument("--simulador", action="store_true", help="executa o simulador do ILLumiSense")
    ap.add_argument("--porta", type=int, default=None, help="porta do web service (padrão 8002)")
    ap.add_argument("--pontos", type=int, default=1024, help="[simulador] pontos por espectro")
    ap.add_argument("--varredura", type=float, default=20.0, help="[simulador] taxa de varredura (Hz)")
    ap.add_argument("--dano-min", type=float, default=10.0, help="[simulador] minutos até dano máximo")
    ap.add_argument("--queda-a-cada", type=float, default=0.0, help="[simulador] simula queda a cada N s")
    ap.add_argument("--queda-por", type=float, default=5.0, help="[simulador] duração da queda (s)")
    ap.add_argument("--headless", action="store_true", help="grava sem interface gráfica")
    ap.add_argument("--config", default=None, help="arquivo de configuração JSON")
    ap.add_argument("--taxa", type=float, default=None, help="[headless] frequência de gravação (Hz)")
    ap.add_argument("--nome", default=None, help="[headless] nome do ensaio")
    ap.add_argument("--pasta", default=None, help="[headless] pasta base dos ensaios")
    ap.add_argument("--duracao", type=float, default=0.0, help="[headless] duração em s (0 = até Ctrl+C)")
    ap.add_argument("--retomar", default=None, help="[headless] pasta de ensaio a continuar")
    args = ap.parse_args(argv)

    if args.simulador:
        from .simulator import run_forever
        run_forever(port=args.porta or 8002, outage_every=args.queda_a_cada, outage_for=args.queda_por,
                    n_points=args.pontos, scan_rate=args.varredura, damage_minutes=args.dano_min)
        return 0

    from .config import Config
    cfg = Config(args.config).load()
    if args.porta:
        cfg.set("connection.port", args.porta)
        cfg.set("connection.port_from_illumisense", False)

    if not args.headless:
        from .gui.app import run_gui
        return run_gui(cfg)

    from .engine import Engine
    if args.taxa:
        cfg.set("acquisition.rate_hz", args.taxa)
    if args.pasta:
        cfg.set("storage.base_dir", args.pasta)
    eng = Engine(cfg)
    stop = {"flag": False}

    def _sig(*_):
        stop["flag"] = True

    signal.signal(signal.SIGINT, _sig)
    try:
        signal.signal(signal.SIGTERM, _sig)
    except Exception:
        pass
    eng.events.listeners.append(lambda t, lvl, msg: print(f"{time.strftime('%H:%M:%S')} [{lvl}] {msg}", flush=True))
    eng.start()
    t_start = time.monotonic()
    started = False
    last_print = 0.0
    while not stop["flag"]:
        time.sleep(0.2)
        if not started and eng.connected:
            eng.start_recording(name=args.nome, resume_dir=args.retomar)
            started = True
        if started and time.monotonic() - last_print > 10:
            st = eng.status()
            print(f"  amostras={st['rec_index']} ciclo={st['cycle']} taxa={st['eff_rate']:.3f} Hz "
                  f"pendentes={st['pending']} conectado={st['connected']}", flush=True)
            last_print = time.monotonic()
        if args.duracao and started and time.monotonic() - t_start > args.duracao:
            break
    eng.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
