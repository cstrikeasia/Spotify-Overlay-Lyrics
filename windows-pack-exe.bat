@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
echo Spotify Overlay Lyrics - Windows launcher v0.0.2
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launcher.ps1" -Mode Build
set "APP_EXIT=%ERRORLEVEL%"
echo.
if not "%APP_EXIT%"=="0" echo [ERROR] See details above. You can also run the diagnostics BAT.
echo Press any key to close this window...
pause >nul
exit /b %APP_EXIT%
