@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Ambiente Python del progetto non trovato. Segui le istruzioni Setup nel README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" main.py --dashboard
pause
