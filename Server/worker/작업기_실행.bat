@echo off
rem TRELLIS.2 worker (GPU PC): runs trellis_worker.py in WSL Ubuntu-24.04 ~/TRELLIS.2/.venv
rem Token = Windows env var TRELLIS_WORKER_TOKEN (same value as the server PC). Once: setx TRELLIS_WORKER_TOKEN "<secret>"
rem Keep this file ASCII + CRLF: cmd misreads UTF-8 Korean lines. Stop: Ctrl+C or close the window.

if "%TRELLIS_WORKER_TOKEN%"=="" (
    echo [worker] TRELLIS_WORKER_TOKEN is not set.
    echo          Run: setx TRELLIS_WORKER_TOKEN "secret"  then open this file again.
    pause
    exit /b 1
)

rem pass the token to WSL through the environment, not the command line
rem -e: run bash directly (without -e the default shell expands $w first and it becomes empty)
set WSLENV=TRELLIS_WORKER_TOKEN/u:%WSLENV%
title TRELLIS worker (port 8090)
echo [worker] starting on port 8090. Check from the server PC: http://^<this PC Tailscale IP^>:8090/health
echo [worker] stop with Ctrl+C

wsl -d Ubuntu-24.04 --cd "%~dp0." -e bash -lc "w=$PWD; cd ~/TRELLIS.2 && source .venv/bin/activate && exec python \"$w/trellis_worker.py\" --host 0.0.0.0 --port 8090 --token \"$TRELLIS_WORKER_TOKEN\""

echo [worker] exited (code %errorlevel%)
pause
