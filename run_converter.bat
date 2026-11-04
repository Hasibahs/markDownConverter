@echo off
title MarkItDown Batch Converter
cd /d "%~dp0"

echo =======================================================
echo         MarkItDown Batch File Converter
echo =======================================================
echo.
echo Launching server and opening your web browser...
echo.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" app.py
) else (
    echo [ERROR] Virtual environment (.venv) was not found in this folder.
    echo Please make sure the virtual environment is set up.
    echo.
    pause
)
