@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 电路学习站
where python >nul 2>nul
if errorlevel 1 (
  echo 没有找到 python，请先安装 Python 3 并勾选 "Add to PATH"。
  pause
  exit /b 1
)
set PYTHONIOENCODING=utf-8
echo 正在启动本地服务（关闭本窗口即停止）...
python scripts\serve.py
pause
