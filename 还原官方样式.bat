@echo off
chcp 65001 >nul
rem ============================================================
rem DeepSeek Harness 还原官方样式（移除 CDP 注入与运行时）
rem ============================================================
set "DIR=%~dp0"
call "%DIR%_ensure_deps.bat"
if errorlevel 1 (
  echo [错误] Python 环境不可用，无法继续
  pause
  exit /b 1
)
"%PY%" "%DIR%dsh-skin.py" restore
echo.
pause
