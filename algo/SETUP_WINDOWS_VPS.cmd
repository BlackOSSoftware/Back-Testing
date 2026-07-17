@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo [Algo Desk] Windows VPS setup
echo [Algo Desk] Folder: %CD%
echo.

where node >nul 2>&1
if errorlevel 1 (
    echo [Algo Desk] ERROR: Node.js not found. Install LTS from https://nodejs.org
    pause
    exit /b 1
)

echo [Algo Desk] Step 1/2: npm install ...
call npm install --no-audit --no-fund
if errorlevel 1 (
    echo [Algo Desk] ERROR: npm install failed.
    pause
    exit /b 1
)

if not exist "node_modules\next\" (
    echo [Algo Desk] ERROR: package 'next' still missing after npm install.
    pause
    exit /b 1
)

echo.
echo [Algo Desk] Step 2/2: npm run build (can take 5-15 minutes on VPS) ...
echo [Algo Desk] Do NOT close this window.
echo.

call npm run build
if errorlevel 1 (
    echo.
    echo [Algo Desk] Normal build failed. Trying low-memory build ...
    call npm run build:vps
    if errorlevel 1 (
        echo [Algo Desk] ERROR: build failed.
        pause
        exit /b 1
    )
)

if not exist ".next\" (
    echo [Algo Desk] ERROR: .next folder missing after build.
    pause
    exit /b 1
)

echo.
echo [Algo Desk] Setup complete.
echo [Algo Desk] Start app with: START_ALGO_DESK.cmd
echo [Algo Desk] Or manually: npm run start
echo.
pause
endlocal
