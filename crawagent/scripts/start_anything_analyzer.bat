@echo off
chcp 65001 >nul
title anything-analyzer MCP Server
echo.
echo [CrawAgent] 正在启动 anything-analyzer ...
cd /d "C:\Users\MOM\Tools\anything-analyzer"
echo. 当前目录: %cd%
call "C:\Users\MOM\AppData\Local\pnpm\bin\pnpm.CMD" dev"
echo.
echo [CrawAgent] 进程已退出。按任意键关闭。
pause >nul
