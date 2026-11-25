@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title Cortes AI

if exist ".venv\Scripts\python.exe" goto :abrir
echo.
echo  O Cortes AI ainda não foi instalado.
echo  Dê dois cliques em  instalar.bat  primeiro.
echo.
pause
exit /b 1

:abrir
echo ============================================================
echo    CORTES AI
echo    A tela vai abrir no seu navegador em alguns segundos.
echo    Deixe esta janela aberta enquanto usa o programa.
echo    Para fechar o Cortes AI, feche esta janela.
echo ============================================================
echo.
set PYTHONUTF8=1
".venv\Scripts\python.exe" -m cortes_ai
if not errorlevel 1 exit /b 0
echo.
echo  O Cortes AI fechou por causa de um erro.
echo  Tente abrir de novo. Se continuar, rode instalar.bat mais uma vez.
echo  (Detalhes técnicos: logs\cortes_ai.log)
echo.
pause
exit /b 1
