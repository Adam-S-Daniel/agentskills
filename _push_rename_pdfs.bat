@echo off
setlocal EnableDelayedExpansion
title Push rename-pdfs skill to origin/main

cd /d D:\repos\adam-s-daniel\agentskills || (echo Could not cd to repo & pause & exit /b 1)

echo === Repo state before ===
git rev-parse --abbrev-ref HEAD
git status --short
echo.

for /f "tokens=*" %%i in ('git rev-parse --abbrev-ref HEAD') do set "ORIG_BRANCH=%%i"
echo Original branch: !ORIG_BRANCH!

echo.
echo === Stashing tracked modifications (untracked rename-pdfs stays in place) ===
git stash push -m "auto-stash before rename-pdfs commit" 2>&1
set "STASHED=0"
git stash list | findstr /C:"auto-stash before rename-pdfs commit" >nul && set "STASHED=1"

echo.
echo === Fetching, checking out main, pulling ===
git fetch origin main || goto :fail
git checkout main || goto :fail
git pull --ff-only origin main || goto :fail

echo.
echo === Staging and committing rename-pdfs ===
git add skills/rename-pdfs || goto :fail
git diff --cached --stat
git commit -m "add(skills): rename-pdfs - interactive PDF renaming with per-file confirmation" || goto :fail

echo.
echo === Pushing to origin/main ===
git push origin main || goto :fail

echo.
echo === Restoring original branch !ORIG_BRANCH! ===
git checkout !ORIG_BRANCH! || goto :fail
if "!STASHED!"=="1" (
  echo Popping stash...
  git stash pop
)

echo.
echo === Updating WSL/Ubuntu copy at ~/repos/agentskills ===
wsl bash -c "if [ -d ~/repos/agentskills/.git ]; then cd ~/repos/agentskills && git fetch origin && cur=$(git symbolic-ref --short HEAD 2>/dev/null); if [ \"$cur\" = main ]; then git pull --ff-only origin main; else git fetch origin main:main 2>/dev/null || echo 'WSL: local main has diverged from origin/main; skipping fast-forward'; fi; else echo 'WSL: ~/repos/agentskills not found, skipping'; fi"

echo.
echo === DONE ===
echo Repo state after:
git status --short
echo.
pause
exit /b 0

:fail
echo.
echo === FAILED ===
echo Inspect git state manually. New skill files are at:
echo   D:\repos\adam-s-daniel\agentskills\skills\rename-pdfs\
echo.
pause
exit /b 1
