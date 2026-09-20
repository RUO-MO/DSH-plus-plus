# -*- coding: utf-8 -*-
"""生成 DSHSkin 全套 .bat 启动器（CRLF + chcp 65001）。

Python 解析约定（集中在 _ensure_deps.bat；call 返回后 PY 变量回传给调用方）：
  1. 环境变量 DSH_SKIN_PY（显式覆盖，仍实测能跑才用）
  2. py 启动器：优先 3.11，退回默认 3.x → 解析出真实 python.exe 绝对路径
     （解析成绝对路径是为了 `start "" "%PY%"` 不会把 "py -3" 当成文件）
  3. where python / python3 逐候选：跳过 Microsoft Store 占位 stub
     （路径含 WindowsApps），且 `-c "import sys"` 实测通过才采用
背景：本机 PATH 的 `python` 命中 Store stub（exit 49、无输出），
旧 `where python` 逻辑拿到假解释器导致全部 .bat 静默失败。

用法：python tools/gen_launchers.py
"""
import io
import os

D = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bat(name, lines):
    p = os.path.join(D, name)
    io.open(p, 'w', encoding='utf-8', newline='').write('\r\n'.join(lines) + '\r\n')
    print('[OK]', name)


# 调用方通用头：解析 Python（_ensure_deps.bat 内完成）+ 依赖自检
PYCALL = [
    'set "DIR=%~dp0"',
    'call "%DIR%_ensure_deps.bat"',
    'if errorlevel 1 (',
    '  echo [错误] Python 环境不可用，无法继续',
    '  pause',
    '  exit /b 1',
    ')',
]

ENSURE = [
    '@echo off',
    'chcp 65001 >nul',
    'rem ============================================================',
    'rem DSHSkin 内部脚本：解析可用 Python + 确保 websocket-client 就绪',
    'rem 由其它 .bat 调用（call 后 PY 变量回传给调用方），也可单独运行。',
    'rem ============================================================',
    'set "DIR=%~dp0"',
    'set "PY="',
    'rem 1) 显式覆盖',
    'if not "%DSH_SKIN_PY%"=="" (',
    '  "%DSH_SKIN_PY%" -c "import sys" >nul 2>&1',
    '  if not errorlevel 1 set "PY=%DSH_SKIN_PY%"',
    ')',
    'rem 2) py 启动器（优先 3.11）→ 真实 exe 绝对路径',
    'if "%PY%"=="" (',
    '  where py >nul 2>&1',
    '  if not errorlevel 1 (',
    '    for /f "usebackq delims=" %%i in (`py -3.11 -c "import sys; print(sys.executable)" 2^>nul`) do set "PY=%%i"',
    '  )',
    ')',
    'if "%PY%"=="" (',
    '  where py >nul 2>&1',
    '  if not errorlevel 1 (',
    '    for /f "usebackq delims=" %%i in (`py -3 -c "import sys; print(sys.executable)" 2^>nul`) do set "PY=%%i"',
    '  )',
    ')',
    'rem 3) where python / python3：跳过 Store 占位 stub，实测能跑才用',
    'if "%PY%"=="" for /f "delims=" %%i in (\'where python 2^>nul\') do call :tryone "%%i"',
    'if "%PY%"=="" for /f "delims=" %%i in (\'where python3 2^>nul\') do call :tryone "%%i"',
    '',
    'if "%PY%"=="" (',
    '  echo [错误] 未找到可用的 Python 3（已自动跳过 Microsoft Store 占位程序）',
    '  echo        可设环境变量 DSH_SKIN_PY 指向 python.exe 后重试',
    '  exit /b 1',
    ')',
    '',
    'rem 依赖自检 / 自动安装',
    '"%PY%" -c "import websocket" >nul 2>&1',
    'if %errorlevel%==0 (',
    '  echo [依赖] websocket-client 已就绪（Python: %PY%）',
    '  exit /b 0',
    ')',
    'echo [依赖] 未检测到 websocket-client，尝试自动安装...',
    '"%PY%" -m pip install "websocket-client>=1.6.0" --quiet',
    'if errorlevel 1 (',
    '  echo [警告] websocket-client 安装失败，CDP 热注入将不可用（增强/会话管理不受影响）',
    '  echo        可稍后手动执行："%PY%" -m pip install "websocket-client>=1.6.0"',
    '  exit /b 0',
    ')',
    'echo [依赖] websocket-client 安装完成',
    'exit /b 0',
    '',
    'rem 子过程：候选 exe 非 stub 且实测可跑才采用',
    ':tryone',
    'if not "%PY%"=="" exit /b 0',
    'echo "%~1" | findstr /i /c:"WindowsApps" >nul && exit /b 0',
    '"%~1" -c "import sys" >nul 2>&1 || exit /b 0',
    'set "PY=%~1"',
    'exit /b 0',
]

BATS = {
    '_ensure_deps.bat': ENSURE,

    '启动面板.bat': [
        '@echo off',
        'chcp 65001 >nul',
        'rem ============================================================',
        'rem DSHSkin 增强工作台（后端 + 桌面窗口）',
        'rem 优先用 Edge --app 模式开独立窗口（无地址栏，类原生）；无 Edge 回退默认浏览器',
        'rem ============================================================',
        *PYCALL,
        'echo 正在启动 DSHSkin 后端...',
        'start "DSHSkin 工作台" /min "%PY%" "%DIR%server.py" --port 8765',
        'timeout /t 2 /nobreak >nul',
        'set "EDGE=%ProgramFiles(x86)%\\Microsoft\\Edge\\Application\\msedge.exe"',
        'if not exist "%EDGE%" set "EDGE=%ProgramFiles%\\Microsoft\\Edge\\Application\\msedge.exe"',
        'if exist "%EDGE%" (',
        '  start "" "%EDGE%" --app=http://127.0.0.1:8765/?app=1 --window-size=1180,780',
        ') else (',
        '  start "" "http://127.0.0.1:8765/"',
        ')',
        'echo 工作台已打开：http://127.0.0.1:8765/  （后端在独立最小化窗口运行）',
        'pause',
    ],

    '还原官方样式.bat': [
        '@echo off',
        'chcp 65001 >nul',
        'rem ============================================================',
        'rem DeepSeek Harness 还原官方样式（移除 CDP 注入与运行时）',
        'rem ============================================================',
        *PYCALL,
        '"%PY%" "%DIR%dsh-skin.py" restore',
        'echo.',
        'pause',
    ],

    '启动DeepSeekHarness.bat': [
        '@echo off',
        'chcp 65001 >nul',
        'rem ============================================================',
        'rem 启动 DeepSeek Harness 桌面版（开发模式，自带 CDP 调试端口 9222）',
        'rem 用途：让 DSHSkin 能热注入增强（改完立即生效，无需重启，不修改官方文件）',
        'rem 注意：不会擅自结束正在运行的进程；若已在运行请先手动关闭再执行本脚本',
        'rem ============================================================',
        *PYCALL,
        'echo 正在启动 DeepSeek Harness（开发模式）...',
        '"%PY%" "%DIR%dsh-skin.py" launch',
        'if errorlevel 1 (',
        '  echo.',
        '  echo [提示] 若提示找不到根目录/启动命令，请：',
        '  echo        1. 设环境变量 DSH_SKIN_HARNESS_ROOT 指向 deepseek-harness 仓库；',
        '  echo        2. 或打开面板 → 设置 → 填写 Harness 根目录。',
        ')',
        'echo.',
        'pause',
    ],
}

for name, lines in BATS.items():
    bat(name, lines)

for name in BATS:
    b = io.open(os.path.join(D, name), 'rb').read()
    assert b.count(b'\r\n') == b.count(b'\n') and b.count(b'\r') == b.count(b'\r\n'), name + ' 行尾异常'
    assert not b.startswith(b'\xef\xbb\xbf'), name + ' 含 BOM'
print('--- 行尾/BOM 全部通过 ---')
