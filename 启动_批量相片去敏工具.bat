@echo off
chcp 65001 >nul
title 批量相片去敏工具 · 启动中
cd /d "%~dp0"
where pythonw >nul 2>nul
if errorlevel 1 (
    echo.
    echo  [提示] 没有找到 Python，请先安装 Python 3.7 或更高版本。
    echo         安装时勾选 "Add Python to PATH"。
    echo.
    pause
    exit /b 1
)
start "" pythonw.exe -m app.main
exit
