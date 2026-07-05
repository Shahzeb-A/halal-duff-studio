@echo off
setlocal
cd /d "%~dp0studio"
set "PY=..\.venv\Scripts\python.exe"
set "LOG=%TEMP%\duffstudio.log"
if not exist "%PY%" (
  echo(
  echo   Python environment not found.  Run setup once ^(see README^):
  echo     python -m venv .venv
  echo     .venv\Scripts\pip install -r requirements.txt
  echo(
  echo   You also need ffmpeg and yt-dlp on PATH ^(winget install Gyan.FFmpeg yt-dlp.yt-dlp^).
  echo(
  pause
  exit /b 1
)
rem start the local server only if nothing is already listening on 7860
netstat -ano | findstr ":7860" | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
  start "Halal Duff Studio" /min cmd /c ""%PY%" server.py > "%LOG%" 2>&1"
)
rem wait until the server actually answers (it can take 10-30s to import torch), up to ~40s
powershell -NoProfile -Command "for($i=0;$i -lt 40;$i++){try{Invoke-WebRequest -UseBasicParsing http://127.0.0.1:7860/api/config -TimeoutSec 2 ^| Out-Null; exit 0}catch{Start-Sleep 1}}; exit 1"
if errorlevel 1 (
  echo   The server did not start.  Opening the log...
  start "" notepad "%LOG%"
  exit /b 1
)
start "" "http://127.0.0.1:7860/?t=%RANDOM%"
exit /b 0
