@echo off
setlocal
cd /d "%~dp0\..\.."
python -m tools.robot_cpg_gui.app %*
if errorlevel 1 (
  echo.
  echo โปรแกรมปิดด้วยข้อผิดพลาด กรุณาอ่าน tools\robot_cpg_gui\README_TH.md
  pause
)
endlocal
