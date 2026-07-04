@echo off
setlocal
cd /d "%~dp0studio"
set "PY=..\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo(
  echo   Python environment not found.  Run setup once ^(see README^):
  echo     python -m venv .venv
  echo     .venv\Scripts\pip install -r requirements.txt
  echo(
  echo   You also need ffmpeg and yt-dlp on PATH.
  echo(
  pause
  exit /b 1
)
rem start the local server only if nothing is already listening on 7860
netstat -ano | findstr ":7860" | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
  start "Halal Duff Studio" /min "%PY%" server.py
  timeout /t 4 /nobreak >nul
)
start "" "http://127.0.0.1:7860"
exit /b 0
