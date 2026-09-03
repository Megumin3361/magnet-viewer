@echo off
rem One-click launcher for Magnet Viewer
cd /d %~dp0
if not exist .venv (
    echo Creating virtual environment ...
    python -m venv .venv
)
call .venv\Scripts\activate.bat
if not exist .venv\.installed (
    echo Installing dependencies, please wait ...
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    if errorlevel 1 (
        echo Dependency install failed. Check your network / Python version.
        pause
        exit /b 1
    )
    type nul > .venv\.installed
)
python main.py
if errorlevel 1 pause
