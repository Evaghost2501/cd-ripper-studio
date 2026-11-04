@echo off
REM ==== CD Ripper Studio - Windows exe build ====
REM Requires Python 3.11+ (https://www.python.org/downloads/) with "Add to PATH" checked.
cd /d %~dp0
if not exist .venv (
    python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller || goto :error
pyinstaller --noconfirm --clean CDRipperStudio.spec || goto :error
echo.
echo ==== Done: dist\CDRipperStudio.exe ====
explorer dist
goto :eof
:error
echo BUILD FAILED
pause
