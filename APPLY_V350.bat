@echo off
chcp 65001 >nul
python APPLY_V350_PATCH.py
if errorlevel 1 pause & exit /b 1
python -m pytest -q tests\test_v350_nhl_nfl.py
pause
