@echo off
:: War Room — Show status and board
cd /d "%~dp0.."
echo.
echo === WAR ROOM STATUS ===
uv run python war_room/war_room.py status
echo.
echo === PAPERCLIP BOARD ===
uv run python war_room/war_room.py board
pause
