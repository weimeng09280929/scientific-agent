@echo off
setlocal
cd /d "%~dp0"

echo [1/3] Checking port 8001...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8001 .*LISTENING"') do (
  taskkill /PID %%P /F >nul 2>nul
)

echo [2/3] Starting research Agent system...
if not exist "venv\Scripts\python.exe" (
  echo ERROR: venv\Scripts\python.exe was not found.
  pause
  exit /b 1
)

start "Scientific Research Agent" /min cmd /c ""%CD%\venv\Scripts\python.exe" -m uvicorn web.api:app --host 127.0.0.1 --port 8001 1>"%CD%\web_server_8001.out.log" 2>"%CD%\web_server_8001.err.log""

echo [3/3] Waiting for startup...
timeout /t 6 /nobreak >nul
start "" "http://127.0.0.1:8001/"

echo If the page cannot be opened, check web_server_8001.err.log.
pause
endlocal
