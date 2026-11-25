@echo off
chcp 65001 >nul
cd /d "%~dp0"
title TG WS Proxy - сборка exe

if not exist venv\Scripts\activate.bat (
    echo Сначала запустите install.bat
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
pyinstaller packaging\windows.spec --noconfirm
if errorlevel 1 (
    echo [ОШИБКА] Сборка не удалась.
    pause
    exit /b 1
)

echo.
echo Готово: dist\TgWsProxy.exe
pause
