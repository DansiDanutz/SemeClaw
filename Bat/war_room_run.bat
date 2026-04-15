@echo off
:: War Room — Run a task
:: Usage: war_room_run.bat "Research AI agent marketplaces"
cd /d "%~dp0.."
if "%~1"=="" (
    set /p TASK="Enter task: "
) else (
    set TASK=%~1
)
echo.
echo ===========================================
echo  War Room starting task:
echo  %TASK%
echo ===========================================
echo.
uv run python war_room/war_room.py run "%TASK%"
pause
