# -*- coding: utf-8 -*-
"""
DSHSkin · 常驻形态（P1-9）
==========================
- 系统托盘图标：颜色/提示反映 CDP 注入状态，菜单提供「打开面板 / 立即注入 /
  还原样式 / 开机自启 / 退出」等快捷操作。
- 开机自启：写入当前用户注册表 Run 键（无需管理员、可随时撤销），不依赖托盘库。

托盘依赖 pystray + Pillow，均为**可选**：缺 pystray 时本模块不报错，
server 正常以控制台形态运行（run_tray 返回 False）。
"""
import os
import sys
import json
import threading
import urllib.request

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_VALUE = 'DSHSkin'


# ---------------- 开机自启（Windows 注册表 Run 键） ----------------
def autostart_command():
    """开机自启要执行的命令行：冻结版启动 exe（不自动弹浏览器，托盘为入口），源码版用 pythonw。"""
    if getattr(sys, 'frozen', False):
        return '"{0}" --no-open'.format(sys.executable)
    pyw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
    if not os.path.isfile(pyw):
        pyw = sys.executable
    server_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'server.py')
    return '"{0}" "{1}" --no-open'.format(pyw, server_py)


def is_autostart():
    if sys.platform != 'win32':
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_VALUE)
        return True
    except OSError:
        return False
    except Exception:
        return False


def set_autostart(enable):
    """True=写入开机自启，False=移除。返回 (ok, 当前状态/错误信息)。"""
    if sys.platform != 'win32':
        return False, '仅支持 Windows 开机自启'
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            if enable:
                winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, RUN_VALUE)
                except FileNotFoundError:
                    pass
        return True, is_autostart()
    except Exception as e:
        return False, str(e)


# ---------------- 本地 API 调用 ----------------
def _api(port, token, path, method='GET', timeout=8):
    url = 'http://127.0.0.1:{0}{1}'.format(port, path)
    req = urllib.request.Request(url, method=method,
                                 headers={'X-DSHSkin-Token': token or ''})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


# ---------------- 托盘图标 ----------------
def _draw_icon(color):
    from PIL import Image, ImageDraw
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([6, 6, 58, 58], radius=16, fill=color)
    d.rounded_rectangle([6, 6, 58, 58], radius=16, outline=(255, 255, 255, 90), width=2)
    # 字母 D
    d.rounded_rectangle([22, 18, 30, 46], radius=4, fill=(255, 255, 255, 235))
    d.arc([22, 18, 44, 46], start=-90, end=90, fill=(255, 255, 255, 235), width=8)
    return img


_STATE_COLOR = {
    'ready': (52, 199, 123, 255),       # 绿：已注入
    'injecting': (64, 150, 255, 255),  # 蓝：注入中
    'no-debug': (230, 170, 60, 255),   # 黄：DSH 在但非调试模式
    'stopped': (150, 150, 150, 255),   # 灰：DSH 未运行
}


def run_tray(port, get_token, open_panel, on_quit=None):
    """启动托盘（阻塞，调用方应放独立线程）。pystray 缺失返回 False。"""
    try:
        import pystray
        from PIL import Image
    except Exception:
        return False

    state = {'mode': 'stopped', 'injected': 0, 'targets': 0}

    def refresh():
        try:
            d = _api(port, get_token(), '/api/cdp-status')
            state.update(d)
        except Exception:
            state['mode'] = 'stopped'
        mode = state.get('mode', 'stopped')
        color = _STATE_COLOR.get(mode, _STATE_COLOR['stopped'])
        if state.get('injected'):
            color = _STATE_COLOR['ready']
        tip = 'DSH++ · {0}{1}'.format(
            {'ready': '已连接', 'no-debug': '需调试模式重启', 'stopped': 'DSH 未运行'}.get(mode, mode),
            '（已注入 {0}/{1}）'.format(state.get('injected', 0), state.get('targets', 0))
            if state.get('injected') else '')
        return color, tip

    def do_open(icon=None, item=None):
        open_panel()

    def do_apply(icon=None, item=None):
        try:
            _api(port, get_token(), '/api/cdp-apply', 'POST')
        except Exception:
            pass

    def do_restore(icon=None, item=None):
        try:
            _api(port, get_token(), '/api/cdp-restore', 'POST')
        except Exception:
            pass

    def toggle_autostart(icon, item):
        ok, _ = set_autostart(not bool(item.checked))
        if ok:
            icon.update_menu()

    def do_quit(icon, item):
        if on_quit:
            try:
                on_quit()
            except Exception:
                pass
        icon.stop()

    color, tip = refresh()
    menu = pystray.Menu(
        pystray.MenuItem('打开面板', do_open, default=True),
        pystray.MenuItem('立即注入主题', do_apply),
        pystray.MenuItem('还原官方样式', do_restore),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('开机自启', toggle_autostart,
                         checked=lambda item: is_autostart()),
        pystray.MenuItem('退出', do_quit),
    )
    icon = pystray.Icon('DSH++', _draw_icon(color), tip, menu)

    def _poll():
        import time
        while icon.running:
            try:
                c, t = refresh()
                icon.icon = _draw_icon(c)
                icon.title = t[:63]   # Windows tooltip 长度限制
            except Exception:
                pass
            time.sleep(5)

    threading.Thread(target=_poll, daemon=True, name='dshskin-tray-poll').start()
    icon.run()
    return True


def start_tray_thread(port, get_token, open_panel, on_quit=None):
    """非阻塞启动托盘；缺 pystray 时返回 False 且不影响主程序。"""
    def _worker():
        try:
            run_tray(port, get_token, open_panel, on_quit)
        except Exception as e:
            print('     托盘已停用（{0}），以控制台形态运行'.format(e))
    t = threading.Thread(target=_worker, daemon=True, name='dshskin-tray')
    t.start()
    return True
