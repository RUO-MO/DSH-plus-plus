@echo off
chcp 65001 >nul
rem ============================================================
rem 构建 DSH++ 桌面应用（PyInstaller onefile）
rem 产物：dist\DSH++.exe —— 双击即用，独立窗口 + 托盘 + 单实例
rem 前置：python dsh-skin.py deps --install   （含 pywebview）
rem ============================================================
set "PY="
for %%P in (
  "%LocalAppData%\Programs\Python\Python311\python.exe"
  "%LocalAppData%\Programs\Python\Python312\python.exe"
  "%LocalAppData%\Programs\Python\Python313\python.exe"
  "%ProgramFiles%\Python311\python.exe"
  "%ProgramFiles%\Python312\python.exe"
  "%ProgramFiles%\Python313\python.exe"
) do if not defined PY if exist %%P set "PY=%%P"
if not defined PY for /f "delims=" %%P in ('where python 2^>nul') do if not defined PY set "PY=%%P"
if not defined PY (
  echo [错误] 未找到 Python。请先安装 Python 3.11+ 后重试。
  pause
  exit /b 1
)
echo 使用 Python: %PY%
"%PY%" -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
  echo [i] 安装 PyInstaller...
  "%PY%" -m pip install pyinstaller
)
"%PY%" -m pip show pywebview >nul 2>&1
if errorlevel 1 (
  echo [i] 安装 pywebview（桌面窗口内核，可选）...
  "%PY%" -m pip install pywebview
)
cd /d "%~dp0"

rem 终止运行中的 DSH++ 进程，释放 dist\DSH++.exe 占用（否则 PyInstaller 覆盖失败）
taskkill /IM "DSH++.exe" /F >nul 2>&1
if errorlevel 1 (
  echo [i] 未检测到运行中的 DSH++ 进程，跳过终止。
) else (
  echo [OK] 已终止 DSH++ 进程，构建产物可覆盖。
)

echo 构建中（onefile，约 1-3 分钟）...
"%PY%" -m PyInstaller --noconfirm --clean build_desktop.spec
if errorlevel 1 (
  echo [错误] 构建失败，见上方日志。
  pause
  exit /b 1
)
echo.
echo [OK] 构建完成：dist\DSH++.exe
pause
