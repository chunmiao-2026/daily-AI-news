@echo off
REM ============================================
REM  Daily AI Digest - Manual entry point
REM  (Scheduled task now calls Python directly)
REM ============================================

cd /d "C:\Users\001\daily-ai-news"

echo [%date% %time%] Daily AI Digest start...

REM ---------- Python 3.12 ----------
set PYTHON=C:\Users\001\AppData\Local\Programs\Python\Python312\python.exe

if not exist "%PYTHON%" (
    echo [ERROR] Python 3.12 not found at %PYTHON%
    pause
    exit /b 1
)

REM ---------- Force UTF-8 output ----------
set PYTHONIOENCODING=utf-8

REM ---------- Run main script ----------
"%PYTHON%" daily_digest.py

if %ERRORLEVEL% neq 0 (
    echo [WARNING] daily_digest.py returned exit code %ERRORLEVEL%
    echo Check logs above for details.
) else (
    echo [%date% %time%] Daily AI Digest completed OK
)

REM Keep window open if double-clicked
if /i "%1"=="--nopause" goto :eof
pause
