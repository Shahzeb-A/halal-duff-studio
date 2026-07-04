@echo off
setlocal
set "ROOT=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$w = New-Object -ComObject WScript.Shell;" ^
  "$lnk = $w.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Halal Duff Studio.lnk');" ^
  "$lnk.TargetPath = '%ROOT%Halal Duff Studio.bat';" ^
  "$lnk.WorkingDirectory = '%ROOT%';" ^
  "$lnk.IconLocation = '%ROOT%studio\web\icon.ico';" ^
  "$lnk.Description = 'Halal Duff Studio';" ^
  "$lnk.Save()"
echo(
echo   Done. "Halal Duff Studio" is now on your Desktop - double-click it to open the app.
echo(
pause
