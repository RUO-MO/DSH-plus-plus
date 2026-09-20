@echo off
chcp 65001 >nul
rem ============================================================
rem 启动 DSH Desktop（新社区壳，带 CDP 调试端口 9222）
rem 先结束旧实例（含 DeepSeek Harness.exe / DSH Desktop.exe），再以
rem --remote-debugging-port=9222 拉起，并设置 DSH_HOME=D:\DSH\DSHdata\.dsh，
rem 让 DSH++ 面板能热注入皮肤与增强（零侵入，不改任何官方文件）。
rem 数据根沿用 DSH++ 正式根 D:\DSH\DSHdata\.dsh-skins。
rem ============================================================
set "EXE=%DSH_SKIN_DESKTOP_EXE%"
if "%EXE%"=="" set "EXE=D:\DSH\dsh-desktop\dsh-plugin-desktop\dist\win-unpacked\DSH Desktop.exe"
set "DSH_HOME=D:\DSH\DSHdata\.dsh"
if not exist "%EXE%" (
  echo [错误] 未找到新社区壳 exe：
  echo   %EXE%
  echo.
  echo 请任选其一：
  echo   1. 设环境变量 DSH_SKIN_DESKTOP_EXE 指向实际 exe；
  echo   2. 编辑本脚本，把 EXE 变量改为实际路径。
  pause
  exit /b 1
)
echo 正在结束旧实例（若在运行）...
taskkill /IM "DSH Desktop.exe" /T /F >nul 2>&1
taskkill /IM "DeepSeek Harness.exe" /T /F >nul 2>&1
timeout /t 1 /nobreak >nul
echo 正在以调试端口 9222 启动 DSH Desktop...
start "" "%EXE%" --remote-debugging-port=9222
echo 已启动。打开 DSH++ 面板后点「立即注入（CDP）」即可换肤/增强。
echo 数据根：%DSH_HOME%
pause