@echo off
chcp 65001 >nul
rem ============================================================
rem 启动 DeepSeek Harness 桌面版（开发模式，自带 CDP 调试端口 9222）
rem 用途：让 DSHSkin 能热注入增强（改完立即生效，无需重启，不修改官方文件）
rem 注意：不会擅自结束正在运行的进程；若已在运行请先手动关闭再执行本脚本
rem ============================================================
set "DIR=%~dp0"
call "%DIR%_ensure_deps.bat"
if errorlevel 1 (
  echo [错误] Python 环境不可用，无法继续
  pause
  exit /b 1
)
echo 正在启动 DeepSeek Harness（开发模式）...
"%PY%" "%DIR%dsh-skin.py" launch
if errorlevel 1 (
  echo.
  echo [提示] 若提示找不到根目录/启动命令，请：
  echo        1. 设环境变量 DSH_SKIN_HARNESS_ROOT 指向 deepseek-harness 仓库；
  echo        2. 或打开面板 → 设置 → 填写 Harness 根目录。
)
echo.
pause
