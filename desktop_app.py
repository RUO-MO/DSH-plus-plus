# -*- coding: utf-8 -*-
"""DSH++ 桌面应用入口（PyInstaller 打包入口，双击 exe 运行）

形态：独立窗口 + 系统托盘 + 单实例；后端复用 server.py 的 HTTP Handler 与守护线程。
窗口后端优先级：pywebview（WebView2 内核，原生窗口）→ msedge --app（独立应用窗口）
→ 默认浏览器。窗口关闭 = 最小化到托盘；托盘「打开面板」唤回窗口，「退出」结束进程。

python desktop_app.py 同样可用（开发预览）；正式使用请用打包出的 DSH++.exe。
"""
import ctypes
import importlib.util
import os
import subprocess
import sys
import threading
import time

ROOT = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import dsh_env          # noqa: E402
import server as SV     # noqa: E402

APP_TITLE = 'DSH++ · DeepSeek Harness 增强工作台'
MUTEX_NAME = 'Local\\DSHPP_SingleInstance'
_WINDOW = None          # pywebview 窗口引用
LOG_FILE = None         # 延迟到数据目录初始化后
_HIDE_ON_START = False  # 托盘可用时启动默认隐藏窗口，托盘「打开面板」唤回


def _log(msg):
    """窗口化 exe 无控制台：关键信息写 ~/.dsh-skins/desktop.log 并尝试 stdout。"""
    global LOG_FILE
    try:
        if LOG_FILE is None:
            LOG_FILE = os.path.join(dsh_env.SKIN_ROOT, 'desktop.log')
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write('[%s] %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg))
    except Exception:
        pass
    try:
        print(msg)
    except Exception:
        pass


def _single_instance_lock():
    """Windows 命名互斥：成功持有返回句柄；已存在（ERROR_ALREADY_EXISTS）返回 None。"""
    try:
        k32 = ctypes.windll.kernel32
        handle = k32.CreateMutexW(None, False, MUTEX_NAME)
        if handle and k32.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
            return None
        return handle
    except Exception:
        return 'ok'   # 非 Windows / 失败时不阻止启动（允许并行，后端端口探测兜底）


def _activate_existing(port):
    """第二实例：请求已有实例唤起窗口（带 token，由后端鉴权）。"""
    try:
        tok = SV.SERVER_TOKEN
        url = 'http://127.0.0.1:{0}/api/desktop-activate?token={1}'.format(port, tok)
        import urllib.request
        with urllib.request.urlopen(url, timeout=3) as r:
            r.read()
    except Exception:
        try:
            SV.open_browser('http://127.0.0.1:{0}/'.format(port))
        except Exception:
            pass


def _find_msedge():
    cands = [
        os.path.join(os.environ.get('ProgramFiles(x86)', ''), 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
        os.path.join(os.environ.get('ProgramFiles', ''), 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def _show_window():
    """唤起（隐藏中的）pywebview 窗口——托盘「打开面板」与第二实例激活共用。"""
    w = _WINDOW
    if w is None:
        return
    try:
        w.show()
        w.restore()
    except Exception:
        pass


def _start_backend(port):
    """启动 HTTP 服务与全部守护线程（与 server.main 一致，frozen 下源码守护自动禁用）。"""
    srv = SV.ThreadingHTTPServer(('127.0.0.1', port), SV.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True, name='dshpp-http').start()
    # 自愈已按用户要求关闭：不再周期性 auto_heal_once / heal_loop（其 launch_dsh 会拉起 DSH 弹窗）
    try:
        SV.start_cdp_watcher()
    except Exception as e:
        _log('     CDP 守护启动失败: {0}'.format(e))
    try:
        SV.drift_watch_loop()
    except Exception:
        pass
    try:
        SV.page_error_watch_loop()
    except Exception:
        pass
    try:
        SV.plugin_manager.start_injector()
    except Exception:
        pass
    return srv


def _run_window(url):
    """独立窗口：pywebview → msedge --app → 浏览器。返回 True（阻塞直到退出或转入托盘常驻）。"""
    if importlib.util.find_spec('webview') is not None:
        try:
            import webview
            global _WINDOW
            window = webview.create_window(
                APP_TITLE, url, width=1380, height=920,
                background_color='#101423', min_size=(980, 640),
                visible=(not _HIDE_ON_START))   # 托盘可用时启动默认隐藏到托盘
            _WINDOW = window
            SV.DESKTOP_ACTIVATE_HOOKS.append(_show_window)

            def _on_closing():
                try:
                    window.hide()      # 关窗 → 最小化到托盘
                except Exception:
                    pass
                return False
            window.events.closing += _on_closing
            webview.start(gui='edgechromium')
            return True               # 仅当窗口被托盘「退出」真正销毁后返回
        except Exception as e:
            _log('     pywebview 窗口不可用，降级独立应用窗口: {0}'.format(e))
    edge = _find_msedge()
    if edge and not _HIDE_ON_START:
        try:
            subprocess.Popen([edge, '--app={0}'.format(url),
                              '--window-size=1380,920', '--new-window'])
            return True               # 进程保持（托盘 + 后端），Edge 窗口关闭后可从托盘再开
        except Exception as e:
            _log('     Edge 应用窗口失败，降级浏览器: {0}'.format(e))
    if not _HIDE_ON_START:
        SV.open_browser(url)
    return _HIDE_ON_START


def main():
    lock = _single_instance_lock()
    if lock is None:
        port, _st = SV.pick_port(SV.PORT)
        _activate_existing(port)
        return 0

    port, state = SV.pick_port(SV.PORT)
    url = 'http://127.0.0.1:{0}/'.format(port)
    srv = None
    if state != 'exists':
        _log('[OK] DSH++ 桌面后端已启动: {0}'.format(url))
        _log('     静态目录: {0}'.format(ROOT))
        srv = _start_backend(port)
    else:
        _log('[!] DSH++ 后端已在 {0} 运行，直接打开面板'.format(url))

    def _quit():
        try:
            if srv is not None:
                srv.shutdown()
        except Exception:
            pass
        try:
            os._exit(0)
        except Exception:
            pass

    def _open_panel():
        _show_window()
        if _WINDOW is None:
            # 非 pywebview 形态：重新拉起窗口/浏览器
            if _find_msedge() and importlib.util.find_spec('webview') is None:
                try:
                    subprocess.Popen([_find_msedge(), '--app={0}'.format(url), '--new-window'])
                    return
                except Exception:
                    pass
            SV.open_browser(url)

    if importlib.util.find_spec('pystray') is not None:
        try:
            SV.tray.start_tray_thread(port, lambda: SV.SERVER_TOKEN, _open_panel, _quit)
            global _HIDE_ON_START
            _HIDE_ON_START = True       # 托盘可用：启动默认隐藏窗口
            _log('     系统托盘已启动（右键图标可快速注入/退出）')
        except Exception as e:
            _log('     托盘启动失败: {0}'.format(e))

    try:
        _run_window(url)
    except Exception as e:
        _log('     窗口启动异常: {0}'.format(e))
        SV.open_browser(url)
    # 窗口（真正）关闭后保持托盘与后端，直到托盘「退出」
    while True:
        time.sleep(1.0)


if __name__ == '__main__':
    main()
