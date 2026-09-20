@echo off
rem 一次性「以注入模式重启 DSH Desktop」助手（无 pause，供定时任务/后台触发）
set "EXE=D:\DSH\dsh-desktop\dsh-plugin-desktop\dist\win-unpacked\DSH Desktop.exe"
set "DSH_HOME=D:\DSH\DSHdata\.dsh"
taskkill /IM "DSH Desktop.exe" /T /F >nul 2>&1
taskkill /IM "DeepSeek Harness.exe" /T /F >nul 2>&1
timeout /t 2 /nobreak >nul
start "" "%EXE%" --remote-debugging-port=9222