"""Inicia o FBG SpectraLog (interface gráfica). Veja README.md."""
import multiprocessing
import sys

from spectralog.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
