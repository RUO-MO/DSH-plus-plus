@echo off
chcp 65001 >nul
rem ============================================================
rem DSHSkin 增强工作台（后端 + 桌面窗口）
rem 优先用 Edge --app 模式开独立窗口（无地址栏，类原生）；无 Edge 回退默认浏览器
rem ============================================================
set "DIR=%~dp0"
call "%DIR%_ensure_deps.bat"
if errorlevel 1 (
  echo [错误] Python 环境不可用，无法继续
  pause
  exit /b 1
)
echo 正在启动 DSHSkin 后端...
start "DSHSkin 工作台" /min "%PY%" "%DIR%server.py" --port 8765
timeout /t 2 /nobreak >nul
set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if exist "%EDGE%" (
  start "" "%EDGE%" --app=http://127.0.0.1:8765/?app=1 --window-size=1180,780
) else (
  start "" "http://127.0.0.1:8765/"
)
echo 工作台已打开：http://127.0.0.1:8765/  （后端在独立最小化窗口运行）
pause
