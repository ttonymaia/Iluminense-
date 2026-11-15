@echo off
chcp 65001 >nul
title FBG SpectraLog - instalacao
cd /d "%~dp0"
echo.
echo  Instalando o FBG SpectraLog (somente na primeira vez)...
echo.
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% --version >nul 2>nul || (echo  ERRO: Python nao encontrado. Instale o Python 3.12 de https://www.python.org/downloads/ marcando "Add python.exe to PATH". & pause & exit /b 1)
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo  ERRO na instalacao das dependencias. & pause & exit /b 1)
echo.
echo  Pronto! Use INICIAR.bat para abrir o programa.
pause
