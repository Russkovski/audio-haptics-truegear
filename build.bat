@echo off
REM Baut AudioHaptics.exe und - falls Inno Setup installiert ist - AudioHaptics-Setup.exe
REM Voraussetzungen: Python installiert. Fuer das Setup zusaetzlich Inno Setup (kostenlos):
REM   https://jrsoftware.org/isdl.php
cd /d "%~dp0"

echo [1/3] Installiere Werkzeuge...
pip install --upgrade pyinstaller customtkinter pyaudiowpatch numpy scipy websocket-client
if errorlevel 1 goto fail

echo [2/3] Baue AudioHaptics.exe ...
python -m PyInstaller --noconfirm --clean --onefile --windowed --name AudioHaptics --icon icon.ico ^
  --collect-all customtkinter --add-data "icon.ico;." ^
  --hidden-import pyaudiowpatch --hidden-import websocket ^
  audio_haptics_gui.py
if not exist dist\AudioHaptics.exe goto fail

echo [3/3] Baue Setup ...
set ISCC=
for %%V in (6 7) do (
  if exist "%ProgramFiles(x86)%\Inno Setup %%V\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup %%V\ISCC.exe"
  if exist "%ProgramFiles%\Inno Setup %%V\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup %%V\ISCC.exe"
  if exist "%LocalAppData%\Programs\Inno Setup %%V\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup %%V\ISCC.exe"
)
if "%ISCC%"=="" (
  echo Inno Setup nicht gefunden - nur dist\AudioHaptics.exe wurde erstellt.
  echo Fuer ein richtiges Setup: Inno Setup installieren und build.bat erneut starten.
  goto end
)
"%ISCC%" installer.iss
if exist dist\AudioHaptics-Setup.exe (
  echo.
  echo Fertig: dist\AudioHaptics-Setup.exe
) else (
  goto fail
)
goto end

:fail
echo.
echo Fehler beim Bauen - bitte die Meldungen oben an Claude schicken.

:end
echo.
pause
