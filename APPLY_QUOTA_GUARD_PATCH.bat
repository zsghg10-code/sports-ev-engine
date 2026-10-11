@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [Sports EV Engine] API-Football quota guard patch
python APPLY_QUOTA_GUARD_PATCH.py
if errorlevel 1 (
  echo.
  echo PATCH FAILED.
  pause
  exit /b 1
)
echo.
echo PATCH COMPLETE. Review git diff, then commit/upload and Reboot Streamlit.
pause
