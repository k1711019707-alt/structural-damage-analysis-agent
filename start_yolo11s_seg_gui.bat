@echo off
setlocal
cd /d "%~dp0"

if exist "YOLO11s\Scripts\python.exe" (
    "YOLO11s\Scripts\python.exe" "scripts\launch_yolo11s_seg_gui.py"
) else (
    python "scripts\launch_yolo11s_seg_gui.py"
)

if errorlevel 1 (
    echo.
    echo GUI failed to start. If dependencies are missing, run:
    echo   YOLO11s\Scripts\python.exe -m pip install PySide6 python-docx
    echo.
    pause
)
