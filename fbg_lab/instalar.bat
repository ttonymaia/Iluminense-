@echo off
rem Instala as dependencias do fbg_lab e testa a conexao com o ILLumiSense.
cd /d "%~dp0"
python -m pip install -r requirements.txt
echo.
echo Testando o servico web do ILLumiSense (deixe o ILLumiSense aberto)...
python -m fbglab sondar
pause
