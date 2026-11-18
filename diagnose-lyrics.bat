@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
set "SONG_TITLE=%~1"
set "SONG_ARTIST=%~2"
set "SONG_DURATION=%~3"
if not defined SONG_TITLE set /p "SONG_TITLE=Song title: "
if not defined SONG_ARTIST set /p "SONG_ARTIST=Artist: "
if not defined SONG_TITLE goto missing
if not defined SONG_ARTIST goto missing
if not defined SONG_DURATION set "SONG_DURATION=0"
echo Spotify Overlay Lyrics - Lyrics Provider Diagnostic
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launcher.ps1" -Mode Install
if errorlevel 1 goto error
set "VENV_PY=%LOCALAPPDATA%\SpotifyOverlayLyrics\runtime-venv\Scripts\python.exe"
if not exist "%VENV_PY%" goto error
"%VENV_PY%" "%~dp0scripts\diagnose_lyrics.py" --title "%SONG_TITLE%" --artist "%SONG_ARTIST%" --duration %SONG_DURATION%
set "RESULT=%ERRORLEVEL%"
echo.
echo The test displays provider status, not copyrighted lyric text.
echo If the lookup failed, copy the diagnostic messages above.
pause
exit /b %RESULT%
:missing
echo Song title and artist are required.
pause
exit /b 2
:error
echo Runtime is not ready. See %%LOCALAPPDATA%%\SpotifyOverlayLyrics\setup.log
pause
exit /b 1
