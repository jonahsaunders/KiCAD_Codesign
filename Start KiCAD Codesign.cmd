@echo off
setlocal
cd /d "%~dp0"
set "CODESIGN_PYTHON="
for /f "delims=" %%D in ('dir /b /ad /o-n "%ProgramFiles%\KiCad" 2^>nul') do (
    if not defined CODESIGN_PYTHON if exist "%ProgramFiles%\KiCad\%%D\bin\pythonw.exe" set "CODESIGN_PYTHON=%ProgramFiles%\KiCad\%%D\bin\pythonw.exe"
)
if not defined CODESIGN_PYTHON (
    echo KiCad with bundled Python was not found under Program Files.
    echo Install KiCad 10, or run launch.py with Python 3.11+, wxPython and Pillow.
    pause
    exit /b 1
)
start "KiCAD Codesign" "%CODESIGN_PYTHON%" "%~dp0launch.py"
