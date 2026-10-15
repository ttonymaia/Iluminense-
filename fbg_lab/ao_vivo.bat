@echo off
rem Abre o espectro bruto ao vivo (ILLumiSense precisa estar aberto).
cd /d "%~dp0"
python -m fbglab ao-vivo %*
