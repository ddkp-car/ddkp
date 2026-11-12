@echo off
setlocal
cd /d "%~dp0\..\.."
python -m tools.robot_cpg_gui.app --mock
if errorlevel 1 pause
endlocal
