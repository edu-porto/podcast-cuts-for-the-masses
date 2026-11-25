@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title Cortes AI - Instalação

echo ============================================================
echo    CORTES AI - INSTALAÇÃO
echo    (só precisa fazer isso uma vez; precisa de internet)
echo ============================================================
echo.

rem ---------------------------------------------------------------
rem 1. Python 3.10 ou mais novo
rem ---------------------------------------------------------------
echo [Passo 1 de 3] Procurando o Python...
set "PY="
py -3 -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto :python_ok
python -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto :python_ok

echo    Python não encontrado. Instalando o Python 3.12 (pode pedir permissão)...
where winget >nul 2>nul
if errorlevel 1 goto :sem_python
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
set "PY312=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist "%PY312%" set PY="%PY312%"
if not defined PY goto :sem_python

:python_ok
echo    Python OK.
echo.

rem ---------------------------------------------------------------
rem 2. Ambiente virtual + bibliotecas
rem ---------------------------------------------------------------
echo [Passo 2 de 3] Instalando as bibliotecas do Cortes AI (alguns minutos)...
if exist ".venv\Scripts\python.exe" goto :venv_ok
%PY% -m venv .venv
if errorlevel 1 goto :erro_venv
:venv_ok
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet --disable-pip-version-check
".venv\Scripts\python.exe" -m pip install --upgrade -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 goto :erro_pip
echo    Bibliotecas OK.
echo.

rem ---------------------------------------------------------------
rem 3. ffmpeg, Ollama e modelo de IA
rem ---------------------------------------------------------------
echo [Passo 3 de 3] Preparando o ffmpeg, o Ollama e a inteligência artificial...
set PYTHONUTF8=1
".venv\Scripts\python.exe" -m cortes_ai.preparar
if errorlevel 1 goto :erro_preparar

echo.
echo ============================================================
echo    INSTALAÇÃO CONCLUÍDA!
echo    Para usar: dê dois cliques em  abrir.bat
echo ============================================================
echo.
pause
exit /b 0

:sem_python
echo.
echo  ERRO: não consegui instalar o Python sozinho.
echo  Faça assim:
echo    1. Abra https://www.python.org/downloads/ no navegador
echo    2. Baixe e instale o Python (marque a caixa "Add python.exe to PATH")
echo    3. Dê dois cliques em instalar.bat de novo
echo.
pause
exit /b 1

:erro_venv
echo.
echo  ERRO: não consegui preparar a pasta do programa (.venv).
echo  Confira se esta pasta não está dentro de um arquivo .zip e se há espaço em disco,
echo  depois rode instalar.bat de novo.
echo.
pause
exit /b 1

:erro_pip
echo.
echo  ERRO: não consegui baixar as bibliotecas.
echo  Verifique sua conexão com a internet e rode instalar.bat de novo.
echo.
pause
exit /b 1

:erro_preparar
echo.
echo  A instalação NÃO terminou. Leia a mensagem de erro acima,
echo  resolva o que ela pede e rode instalar.bat de novo.
echo  (Detalhes técnicos: logs\cortes_ai.log)
echo.
pause
exit /b 1
