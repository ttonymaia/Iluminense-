@echo off
chcp 65001 >nul
title Simulador do ILLumiSense (porta 8002)
cd /d "%~dp0"
echo  Simulador do ILLumiSense - use somente SEM o ILLumiSense real aberto.
echo  Feche esta janela para parar.
".venv\Scripts\python.exe" -m spectralog --simulador %*
pause
