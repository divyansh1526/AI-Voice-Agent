@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM  start_mcp_server.bat
REM
REM  YOU DO NOT NEED TO RUN THIS MANUALLY.
REM  main.py auto-spawns mcp_db_server.py as a subprocess the first time a
REM  voice session connects. The MCP server runs silently in the background.
REM
REM  Use this script ONLY for manual debugging of the MCP server in isolation:
REM      1. Open this terminal
REM      2. Run: start_mcp_server.bat
REM      3. The server logs all MongoDB calls to this terminal's stderr
REM      4. Press Ctrl+C to stop
REM ─────────────────────────────────────────────────────────────────────────────

title VoiceBridge - MCP Database Server (Debug Mode)
color 0A

echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║   VoiceBridge MCP Database Server               ║
echo  ║   Debug / standalone mode                       ║
echo  ╚══════════════════════════════════════════════════╝
echo.
echo  NOTE: In production, main.py spawns this automatically.
echo  This script is for manual debugging only.
echo.

REM Activate virtual environment if present
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
    echo  [OK] Virtual environment activated
) else (
    echo  [WARN] No venv found - using system Python
)

echo.
echo  Connecting to MongoDB Atlas...
echo  Starting MCP server on stdio transport...
echo  Press Ctrl+C to stop.
echo  ────────────────────────────────────────────────────
echo.

python mcp_db_server.py

echo.
echo  ────────────────────────────────────────────────────
echo  MCP server stopped.
pause
