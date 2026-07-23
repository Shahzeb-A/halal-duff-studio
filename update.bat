@echo off
rem One command to pull all the latest changes (from Mac or anywhere) + refresh dependencies.
setlocal
cd /d "%~dp0"
echo Pulling latest from GitHub...
git pull --ff-only origin main
if errorlevel 1 (
  echo(
  echo   Pull failed ^(you may have local changes^). Run: git status
  pause
  exit /b 1
)
if exist ".venv\Scripts\pip.exe" (
  echo Refreshing dependencies...
  ".venv\Scripts\pip" install -r requirements.txt -q
)
echo(
echo   Up to date. Launch with "Halal Duff Studio.bat".
pause
