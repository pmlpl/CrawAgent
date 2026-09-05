@echo off
chcp 65001 >nul
title anything-analyzer MCP Server
echo.
echo === anything-analyzer ===
cd /d "C:\Users\MOM\Tools\anything-analyzer"
call "C:\Users\MOM\AppData\Local\pnpm\bin\pnpm.CMD" dev
echo.
pause >nul
