@echo off
chcp 65001 >nul
rem ============================================================
rem DeepSeek Harness 主题包导入（把 .zip 拖到这个 bat 上即可）
rem ============================================================
set "DIR=%~dp0"
call "%DIR%_ensure_deps.bat"
if errorlevel 1 (
  echo [错误] Python 环境不可用，无法继续
  pause
  exit /b 1
)
if "%~1"=="" (
  echo 用法: 将主题包 .zip 文件拖放到本文件图标上
  pause
  exit /b 0
)
"%PY%" "%DIR%dsh-skin.py" install "%~1"
echo.
pause
