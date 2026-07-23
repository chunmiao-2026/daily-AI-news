@echo off
REM ============================================
REM  每日 AI 精选 · 计划任务入口
REM  用于 Windows 任务计划程序每天 9:10 自动运行
REM ============================================
REM  配置说明：
REM    1. 打开「任务计划程序」
REM    2. 创建任务 → 触发器：每天 9:10
REM    3. 操作：启动此程序 → 浏览选择本文件
REM    4. 无论用户是否登录都要运行
REM ============================================

cd /d "C:\Users\001\daily-ai-news"

echo [%date% %time%] 每日 AI 精选开始...

REM ---------- Python 路径（3.12，已预装 feedparser）----------
set PYTHON=C:\Users\001\AppData\Local\Programs\Python\Python312\python.exe

if not exist "%PYTHON%" (
    echo [ERROR] Python 3.12 未找到
    pause
    exit /b 1
)

REM ---------- 强制 UTF-8 输出（避免 emoji 乱码）----------
set PYTHONIOENCODING=utf-8

REM ---------- 运行主脚本 ----------
"%PYTHON%" daily_digest.py

if %ERRORLEVEL% neq 0 (
    echo [WARNING] daily_digest.py 返回了非零退出码 (%ERRORLEVEL%)
    echo 日志已保留，请检查异常原因
) else (
    echo [%date% %time%] 每日 AI 精选完成 ✅
)

REM 保持窗口打开 10 秒，方便看输出
timeout /t 10 /nobreak >nul
