@echo off
:: Push War Room build to GitHub
cd /d "%~dp0.."

:: Remove stale git lock if present
if exist ".git\index.lock" del ".git\index.lock"

echo.
echo === Staging War Room files ===
git add war_room/
git add Bat/
git add .vscode/tasks.json

echo.
echo === Files to commit ===
git diff --cached --stat

echo.
git commit -m "Build War Room: Paperclip agent fleet orchestrator + dashboard

- Phase 1: task_queue.json, shared_state.json (persistent state)
- Phase 2: paperclip_bridge.py (mock + live Paperclip API, issue creation)
- Phase 3: war_room.py (Research->Strategist->Writer pipeline, Telegram notify)
- Phase 4: dashboard/server.py + index.html (Flask at port 8765, dark UI)
- Phase 5: VS Code tasks + bat files (war_room_run, war_room_dashboard, war_room_status)
- Agents: Research (Dexter), Architect (David), Strategist (Memo), Writer (Hermes)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"

echo.
echo === Pushing to GitHub ===
git push origin main

echo.
echo ======================================
echo  War Room pushed to GitHub!
echo ======================================
pause
