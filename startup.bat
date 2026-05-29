@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo  AI Tutor — Startup
echo ============================================================
echo.

:: ----------------------------------------------------------------
:: 1. Check Python 3.10+
:: ----------------------------------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo        Install Python 3.10 or later from https://python.org
    exit /b 1
)

for /f "tokens=2 delims= " %%V in ('python --version 2^>^&1') do set PY_VER=%%V
for /f "tokens=1,2 delims=." %%A in ("!PY_VER!") do (
    set PY_MAJOR=%%A
    set PY_MINOR=%%B
)

if !PY_MAJOR! LSS 3 (
    echo ERROR: Python 3.10+ required. Found !PY_VER!
    exit /b 1
)
if !PY_MAJOR! EQU 3 (
    if !PY_MINOR! LSS 10 (
        echo ERROR: Python 3.10+ required. Found !PY_VER!
        exit /b 1
    )
)

echo [OK] Python !PY_VER! detected.

:: ----------------------------------------------------------------
:: 2. Set environment variables
:: ----------------------------------------------------------------
set PYTHONPATH=E:\ai-tutor
set PROJECT_ROOT=E:\ai-tutor

echo [OK] PYTHONPATH=!PYTHONPATH!
echo [OK] PROJECT_ROOT=!PROJECT_ROOT!
echo.

:: ----------------------------------------------------------------
:: 3. Start overlay first (needs Win32 window time)
:: ----------------------------------------------------------------
echo Starting MCP server: overlay (port 9100)...
start /min "mcp-overlay" cmd /c "python E:\ai-tutor\mcp-servers\overlay\server.py"

echo Waiting 3 seconds for overlay window to initialise...
timeout /t 3 /nobreak >nul

:: ----------------------------------------------------------------
:: 4. Start remaining MCP servers
:: ----------------------------------------------------------------
echo Starting MCP server: desktop-control (port 9101)...
start /min "mcp-desktop-control" cmd /c "python E:\ai-tutor\mcp-servers\desktop-control\server.py"

echo Starting MCP server: browser-control (port 9102)...
start /min "mcp-browser-control" cmd /c "python E:\ai-tutor\mcp-servers\browser-control\server.py"

echo Starting MCP server: vision-fallback (port 9103)...
start /min "mcp-vision-fallback" cmd /c "python E:\ai-tutor\mcp-servers\vision-fallback\server.py"

echo Starting MCP server: voice (port 9104)...
start /min "mcp-voice" cmd /c "python E:\ai-tutor\mcp-servers\voice\server.py"

echo Starting MCP server: lesson-manager (port 9105)...
start /min "mcp-lesson-manager" cmd /c "python E:\ai-tutor\mcp-servers\lesson-manager\server.py"

echo.
echo All server processes launched. Polling health endpoints...
echo.

:: ----------------------------------------------------------------
:: 5. Health-check helper — polls GET /health up to 30 seconds
::    Usage: call :wait_healthy <name> <port>
:: ----------------------------------------------------------------
set ALL_HEALTHY=1

call :wait_healthy overlay          9100
call :wait_healthy desktop-control  9101
call :wait_healthy browser-control  9102
call :wait_healthy vision-fallback  9103
call :wait_healthy voice            9104
call :wait_healthy lesson-manager   9105

:: ----------------------------------------------------------------
:: 6. Launch Claude Code if all healthy, otherwise exit with error
:: ----------------------------------------------------------------
echo.
if !ALL_HEALTHY! EQU 1 (
    echo ============================================================
    echo  All servers healthy — launching Claude Code
    echo ============================================================
    claude --project E:\ai-tutor
) else (
    echo ============================================================
    echo  One or more servers failed health check. See errors above.
    echo  Claude Code will NOT be launched.
    echo ============================================================
    exit /b 1
)

endlocal
exit /b 0


:: ================================================================
:: Subroutine: wait_healthy <server-name> <port>
:: Polls http://127.0.0.1:<port>/health up to 30 seconds (1s interval)
:: Sets ALL_HEALTHY=0 and prints failure if server never responds
:: ================================================================
:wait_healthy
set _NAME=%1
set _PORT=%2
set _TRIES=0
set _MAX=30

:poll_loop
curl -s -o nul -w "%%{http_code}" http://127.0.0.1:%_PORT%/health 2>nul | findstr /r "^2" >nul 2>&1
if not errorlevel 1 (
    echo [OK] %_NAME% is healthy on port %_PORT%
    goto :eof
)

set /a _TRIES+=1
if !_TRIES! GEQ !_MAX! (
    echo [FAIL] %_NAME% did not become healthy within %_MAX% seconds ^(port %_PORT%^)
    set ALL_HEALTHY=0
    goto :eof
)

timeout /t 1 /nobreak >nul
goto poll_loop
