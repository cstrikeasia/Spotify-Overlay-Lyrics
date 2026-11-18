@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
echo Spotify Overlay Lyrics - Real Audio Meter Diagnostic
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launcher.ps1" -Mode Install
if errorlevel 1 goto error
set "VENV_PY=%LOCALAPPDATA%\SpotifyOverlayLyrics\runtime-venv\Scripts\python.exe"
if not exist "%VENV_PY%" goto error
"%VENV_PY%" "%~dp0scripts\diagnose_audio.py" --seconds 8
set "EXITCODE=%ERRORLEVEL%"
echo.
echo Copy the results above if the bars are still not moving.
pause
exit /b %EXITCODE%
:error
echo Runtime not ready. See setup.log in %%LOCALAPPDATA%%\SpotifyOverlayLyrics
pause
exit /b 1
