@echo off
chcp 65001 >nul
rem ============================================================
rem DSHSkin 内部脚本：解析可用 Python + 确保 websocket-client 就绪
rem 由其它 .bat 调用（call 后 PY 变量回传给调用方），也可单独运行。
rem ============================================================
set "DIR=%~dp0"
set "PY="
rem 1) 显式覆盖
if not "%DSH_SKIN_PY%"=="" (
  "%DSH_SKIN_PY%" -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PY=%DSH_SKIN_PY%"
)
rem 2) py 启动器（优先 3.11）→ 真实 exe 绝对路径
if "%PY%"=="" (
  where py >nul 2>&1
  if not errorlevel 1 (
    for /f "usebackq delims=" %%i in (`py -3.11 -c "import sys; print(sys.executable)" 2^>nul`) do set "PY=%%i"
  )
)
if "%PY%"=="" (
  where py >nul 2>&1
  if not errorlevel 1 (
    for /f "usebackq delims=" %%i in (`py -3 -c "import sys; print(sys.executable)" 2^>nul`) do set "PY=%%i"
  )
)
rem 3) where python / python3：跳过 Store 占位 stub，实测能跑才用
if "%PY%"=="" for /f "delims=" %%i in ('where python 2^>nul') do call :tryone "%%i"
if "%PY%"=="" for /f "delims=" %%i in ('where python3 2^>nul') do call :tryone "%%i"

if "%PY%"=="" (
  echo [错误] 未找到可用的 Python 3（已自动跳过 Microsoft Store 占位程序）
  echo        可设环境变量 DSH_SKIN_PY 指向 python.exe 后重试
  exit /b 1
)

rem 依赖自检 / 自动安装
"%PY%" -c "import websocket" >nul 2>&1
if %errorlevel%==0 (
  echo [依赖] websocket-client 已就绪（Python: %PY%）
  exit /b 0
)
echo [依赖] 未检测到 websocket-client，尝试自动安装...
"%PY%" -m pip install "websocket-client>=1.6.0" --quiet
if errorlevel 1 (
  echo [警告] websocket-client 安装失败，CDP 热注入将不可用（增强/会话管理不受影响）
  echo        可稍后手动执行："%PY%" -m pip install "websocket-client>=1.6.0"
  exit /b 0
)
echo [依赖] websocket-client 安装完成
exit /b 0

rem 子过程：候选 exe 非 stub 且实测可跑才采用
:tryone
if not "%PY%"=="" exit /b 0
echo "%~1" | findstr /i /c:"WindowsApps" >nul && exit /b 0
"%~1" -c "import sys" >nul 2>&1 || exit /b 0
set "PY=%~1"
exit /b 0
