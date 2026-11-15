# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: gera dist/FBG_SpectraLog/ com dois executáveis:
#   FBG_SpectraLog.exe          interface gráfica (uso normal)
#   FBG_SpectraLog_console.exe  linha de comando (--simulador, --headless)
# Uso:  pyinstaller --noconfirm spectralog.spec
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

_SKIP = ("examples", "opengl", "jupyter", "canvas", "flowchart", "console", "multiprocess", "tests")
hidden = collect_submodules("pyqtgraph", filter=lambda n: not any(x in n.lower() for x in _SKIP))
hidden += ["scipy.signal", "scipy.io", "h5py", "psutil", "colorama"]
datas = collect_data_files("pyqtgraph", includes=["colors/maps/*", "icons/**/*"])

a = Analysis(
    ["run_spectralog.py"],
    pathex=["."],
    binaries=[],
    datas=datas + [("README.md", "."), ("ferramentas/*", "ferramentas")],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "jupyter", "PyQt5", "PyQt6", "PySide2",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "pytest", "cryptography",
              "yaml", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe_gui = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="FBG_SpectraLog",
    console=False,
    upx=False,
    version=None,
)
exe_cli = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="FBG_SpectraLog_console",
    console=True,
    upx=False,
)
coll = COLLECT(
    exe_gui, exe_cli,
    a.binaries, a.datas,
    strip=False, upx=False,
    name="FBG_SpectraLog",
)
