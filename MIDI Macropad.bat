@echo off
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo First run — setting up environment...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install --upgrade pip --quiet
    pip install -r requirements.txt --quiet 2>nul
    pip install mido python-rtmidi pynput pycaw dearpygui comtypes toml obs-websocket-py sounddevice soundfile numpy --quiet
    echo Setup complete!
)

set PYTHONUTF8=1
start "" .venv\Scripts\pythonw.exe main.py
