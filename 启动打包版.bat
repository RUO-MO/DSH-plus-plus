@echo off
chcp 65001 >nul
rem ============================================================
rem 启动打包版 DSH（带 CDP 调试端口 9222）
rem exe 解析顺序（与 dsh_env 一致）：
rem   1. 环境变量 DSH_SKIN_DESKTOP_EXE
rem   2. 新社区壳 D:\DSH\dsh-desktop\dsh-plugin-desktop\dist\win-unpacked\DSH Desktop.exe
rem   3. 官方打包产出 D:\DSH\deepseek-harness\apps\desktop\.desktop-build\targets\win-x64\unsigned-artifacts\win-unpacked\DeepSeek Harness.exe
rem 先结束旧实例（打包版有单实例锁：第二实例会自动退出），再以
rem --remote-debugging-port=9222 拉起，让 DSH++ 面板能热注入皮肤与增强。
rem 注意：这是「手动启动」脚本——DSH++ 本身永不自动拉起/重启 DSH。
rem ============================================================
set "EXE=%DSH_SKIN_DESKTOP_EXE%"
if "%EXE%"=="" if exist "D:\DSH\dsh-desktop\dsh-plugin-desktop\dist\win-unpacked\DSH Desktop.exe" set "EXE=D:\DSH\dsh-desktop\dsh-plugin-desktop\dist\win-unpacked\DSH Desktop.exe"
if "%EXE%"=="" if exist "D:\DSH\deepseek-harness\apps\desktop\.desktop-build\targets\win-x64\unsigned-artifacts\win-unpacked\DeepSeek Harness.exe" set "EXE=D:\DSH\deepseek-harness\apps\desktop\.desktop-build\targets\win-x64\unsigned-artifacts\win-unpacked\DeepSeek Harness.exe"
set "DSH_HOME=D:\DSH\DSHdata\.dsh"
if not defined EXE (
  echo [错误] 未找到打包版 exe：
  echo.
  echo 请任选其一：
  echo   1. 先完成打包（新壳：yarn workspace dsh-plugin-desktop package:dir；官方：.desktop-build\run-package-win-dir.cmd）；
  echo   2. 设环境变量 DSH_SKIN_DESKTOP_EXE 指向实际 exe；
  echo   3. 编辑本脚本，把 EXE 变量改为实际路径。
  pause
  exit /b 1
)
echo 正在结束旧实例（若在运行）...
taskkill /IM "DSH Desktop.exe" /T /F >nul 2>&1
taskkill /IM "DeepSeek Harness.exe" /T /F >nul 2>&1
timeout /t 1 /nobreak >nul
echo 正在以调试端口 9222 启动打包版...
start "" "%EXE%" --remote-debugging-port=9222
echo 已启动。打开 DSH++ 面板后点「立即注入（CDP）」即可换肤/增强。
echo 数据根：%DSH_HOME%
pause
