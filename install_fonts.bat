@echo off
rem ============================================================
rem  Install the Chiron GoRound TC UI font for the current user.
rem  (ASCII-only on purpose: cmd.exe reads .bat with the system
rem   ANSI code page, Chinese text here would break.)
rem
rem  - no admin rights needed
rem  - safe to run repeatedly (skips if already installed)
rem  - requires the fonts\ folder next to this file
rem ============================================================
cd /d "%~dp0"

if not exist install_fonts.ps1 (
    echo ERROR: install_fonts.ps1 not found.
    echo        Run this from the project root folder.
    pause
    exit /b 1
)

if not exist fonts (
    echo ERROR: fonts\ folder not found - cannot install the font.
    echo        Make sure the project was downloaded completely.
    pause
    exit /b 1
)

powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0install_fonts.ps1"
echo.
pause
