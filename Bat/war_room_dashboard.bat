@echo off
:: War Room Dashboard — opens at http://127.0.0.1:8765
cd /d "%~dp0.."

:: Install flask if needed
uv run python -c "import flask" 2>nul || uv pip install flask flask-cors

echo.
echo ===========================================
echo  War Room Dashboard
echo  http://127.0.0.1:8765
echo ===========================================
echo.
start "" "http://127.0.0.1:8765"
uv run python war_room/dashboard/server.py
pause
