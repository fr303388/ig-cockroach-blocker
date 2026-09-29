@echo off
rem ============================================================
rem  Build the EXE.  (This file is intentionally ASCII-only:
rem  cmd.exe reads .bat files with the system ANSI code page, so
rem  putting Chinese text in here corrupts the output / syntax.)
rem ============================================================
cd /d "%~dp0"

echo [1/6] Installing Python dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller
if errorlevel 1 (
    echo     Trying py launcher...
    py -m pip install -r requirements.txt
    py -m pip install pyinstaller
)

echo [2/6] Installing Chromium browser (one-time, ~150MB)...
python -m playwright install chromium
if errorlevel 1 py -m playwright install chromium

echo [3/6] Installing UI font (Chiron GoRound TC) for current user...
rem Per-user install, no admin rights needed.
rem If already installed it skips. A failure here never stops the build:
rem the app falls back to the system font automatically.
if exist install_fonts.ps1 (
    powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0install_fonts.ps1"
) else (
    echo     install_fonts.ps1 not found - skipping. App will use system font.
)

echo [4/6] Cleaning old build...
if exist build rmdir /s /q build
if exist dist (
    echo     NOTE: if you see "Access is denied", the old EXE is still running.
    echo           Close it first, then run this again.
    rmdir /s /q dist
)

echo [5/6] Building EXE (onedir mode, more reliable with Playwright)...
pyinstaller --noconfirm --windowed --name CockroachBlocker --collect-all playwright main.py

echo [6/6] Carrying over login state, settings and the font installer...
if exist ig_state.json copy /y ig_state.json "dist\CockroachBlocker\_internal\ig_state.json" >nul
if exist threads_state.json copy /y threads_state.json "dist\CockroachBlocker\_internal\threads_state.json" >nul
if exist config.json copy /y config.json "dist\CockroachBlocker\_internal\config.json" >nul

rem Ship the font + installer inside dist so whoever receives the folder can
rem run install_fonts.bat and get the same look. Delete the next 3 lines to
rem keep dist ~93 MB smaller.
if exist fonts xcopy /y /i /q "fonts" "dist\CockroachBlocker\fonts" >nul
if exist install_fonts.ps1 copy /y install_fonts.ps1 "dist\CockroachBlocker\" >nul
if exist install_fonts.bat copy /y install_fonts.bat "dist\CockroachBlocker\" >nul

echo.
echo ============================================================
echo  BUILD DONE
echo  Launch:  dist\CockroachBlocker\CockroachBlocker.exe
echo  Ship the whole dist\CockroachBlocker\ folder, not just the exe.
echo.
echo  NOTE 1: First run defaults to DRY RUN - it only lists who it WOULD
echo          block and never actually blocks. Turn it off in the
echo          settings tab when you are ready.
echo.
echo  NOTE 2: The font is installed per-user on THIS machine. Anyone you
echo          give the dist folder to should run install_fonts.bat once
echo          (it ships inside dist\CockroachBlocker\).
echo          Without it the app still works, just with the system font.
echo ============================================================
pause
