@echo off
cd /d "%~dp0"

set PY=
py -3 --version >nul 2>&1 && set PY=py -3
if not defined PY (
    python --version >nul 2>&1 && set PY=python
)
if not defined PY (
    echo [ERROR] Python not found. Install Python from python.org and check "Add python.exe to PATH".
    pause
    exit /b 1
)
echo Using: %PY%
echo Building from: %cd%

rem remove previous build output so old code is never reused
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist LevelUpTool.spec del /q LevelUpTool.spec

%PY% -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo [ERROR] pyinstaller install failed. See the messages above.
    pause
    exit /b 1
)

%PY% -m PyInstaller --noconfirm --clean --onefile --windowed --name LevelUpTool main.py
if errorlevel 1 (
    echo [ERROR] build failed. See the messages above.
    pause
    exit /b 1
)

if exist "dist\LevelUpTool.exe" (
    echo.
    echo [OK] %cd%\dist\LevelUpTool.exe
) else (
    echo [ERROR] build finished but dist\LevelUpTool.exe was not found.
)
pause
