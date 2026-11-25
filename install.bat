@echo off
chcp 65001 >nul
cd /d "%~dp0"
title TG WS Proxy - установка библиотек

where python >nul 2>nul
if errorlevel 1 (
    echo [ОШИБКА] Python не найден. Установите Python 3.10-3.12 с python.org
    echo и отметьте галочку "Add Python to PATH".
    pause
    exit /b 1
)

if not exist venv (
    echo Создаю виртуальное окружение...
    python -m venv venv
    if errorlevel 1 goto :fail
)

call venv\Scripts\activate.bat
python -m pip install --upgrade pip
if errorlevel 1 goto :fail

echo Устанавливаю библиотеки проекта...
pip install -e .
if errorlevel 1 goto :fail

echo Устанавливаю PyInstaller...
pip install pyinstaller
if errorlevel 1 goto :fail

echo.
echo Готово. Библиотеки установлены.
echo Для сборки exe запустите build.bat
pause
exit /b 0

:fail
echo.
echo [ОШИБКА] Установка не удалась, см. сообщения выше.
pause
exit /b 1
