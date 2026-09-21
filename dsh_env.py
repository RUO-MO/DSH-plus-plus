# -*- coding: utf-8 -*-
"""DSH++ 环境解析：DeepSeek Harness 桌面版定位、启动器解析、CDP 端口探测。

设计要点（相对原 TraeSkin 的 trae_env.py）：
  - 数据目录从 ~/.trae-skins 迁到 ~/.dsh-skins（全新隔离，互不影响）
  - 没有本地 CSS 文件通道：不解析 IDE 安装目录，只关心 CDP 调试端口与进程
  - DeepSeek Harness 桌面版是 Electron 壳：开发模式（start:desktop）渲染进程自带
    --remote-debugging-port=9222 —— 这就是「天然调试后门」；win-x64 打包版默认
    无调试端口，需以 --remote-debugging-port=9222 参数启动（launcher_mode=packaged）
  - 启动命令解析：全局 pnpm.cmd 可能落在 Node <22 上（pnpm 11 需要 node:sqlite），
    因此优先用 corepack 管理的 pnpm.cjs + 显式 Node（>=22）拉起 start:desktop；
    打包版则直接定位 exe（DSH_SKIN_DESKTOP_EXE > config.desktop_exe > 产出目录扫描）

环境变量前缀：DSH_SKIN_*（config.json 可覆盖默认值）
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

APP_NAME = 'DSH++'
APP_TAG = 'dsh-skin'
APP_TITLE = 'DeepSeek Harness 增强工作台（DSH++）'

# config.json 结构版本：load 时据此向前迁移老配置（主题自身另有模板版本迁移）
CONFIG_VERSION = 1

# 进程内配置锁：ThreadingHTTPServer 多线程下保证「读-改-写」不交错丢更新。
# RLock 允许同一线程在 update_config 内嵌套调用 load/save。
_CONFIG_LOCK = threading.RLock()

def _default_skin_root():
    """约定俗成的默认数据根：`~/.dsh-skins`。"""
    return os.path.join(os.path.expanduser('~'), '.dsh-skins')


# 皮肤/插件/运行态数据根（dsh++ 沿用旧名 dsh-skin 至今）。
# 解析优先级：
#   1) 环境变量 DSH_SKIN_ROOT
#   2) 默认根 ~/.dsh-skins/config.json 里的 "skin_root" 指针（把整套 DSH++ 数据搬到
#      指定目录时只需写一次；对桌面应用 DSH++.exe / 面板 / CLI 统一生效，无需共 env）
#   3) 回退 ~/.dsh-skins（向后兼容未迁移机器）
# 注意：为避免循环，指针放在「默认根」里读取，而不是放在被指向的根里。
def _resolve_skin_root():
    env = os.environ.get('DSH_SKIN_ROOT')
    if env:
        return env
    default = _default_skin_root()
    try:
        with open(os.path.join(default, 'config.json'), encoding='utf-8') as f:
            pointer = (json.load(f) or {}).get('skin_root')
        if pointer and os.path.isdir(pointer):
            return pointer
    except (OSError, ValueError):
        pass
    return default


#: 皮肤根指针文件的位置。提成模块级名字有两个好处：① 测试可桩掉，以便验证
#: 「指针与生效根不一致」这条告警（否则它会去读本机真实的指针文件）；
#: ② 排查时可临时覆盖。
POINTER_CONFIG = os.path.join(_default_skin_root(), 'config.json')


def _registry_env():
    """读 Windows 用户作用域环境变量（HKCU\\Environment）。

    为什么不直接用 os.environ：进程继承的值可能落后于注册表 —— 改过环境变量但
    当前会话是旧的时候两者不一致，而**注册表的值才是下次启动真正生效的**。
    非 Windows 或读取失败时返回 {}。
    """
    if os.name != 'nt':
        return {}
    try:
        import winreg
    except ImportError:
        return {}
    out = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                out[str(name).upper()] = value
                index += 1
    except OSError:
        pass
    return out


#: 判断数据根「最后写入时间」时探测的文件 —— 只看顶层与已知关键文件，不做递归
#: walk：当前根含 plugins/node_modules（上千文件），递归会明显拖慢体检。
_ROOT_FRESHNESS_PROBES = ('config.json', 'logs.json', 'desktop.log', 'server.token',
                          os.path.join('plugins', 'registry.json'))


def _dir_freshness(path):
    """目录最后写入时间（取关键文件 mtime 的最大值）；全不可读返回 0.0。"""
    best = 0.0
    for rel in ('',) + _ROOT_FRESHNESS_PROBES:
        try:
            best = max(best, os.path.getmtime(os.path.join(path, rel) if rel else path))
        except OSError:
            continue
    return best


def discover_skin_roots():
    """列出本机全部可能的 SKIN_ROOT 候选（去重）。

    来源：生效根 / 默认根（指针载体）/ env DSH_SKIN_ROOT / DSH_HOME 兄弟目录 /
    盘符浅扫（≤2 层，足以覆盖 `<盘>:\\<父>\\<子>\\.dsh-skins` 这类布局）。

    为什么要这个：「多根并存」是本项目踩过的真实坑（2026-09-21 排查）——
    根分裂后插件注册表、主题、日志各存一份，症状隐蔽，表现为「某些设置时好时坏」。
    """
    found = []

    def add(candidate, origin):
        if not candidate:
            return
        norm = os.path.normpath(candidate)
        for row in found:
            if os.path.normcase(row['dir']) == os.path.normcase(norm):
                row['origins'].append(origin)
                return
        found.append({'dir': norm, 'origins': [origin]})

    add(SKIN_ROOT, '生效根')
    add(_default_skin_root(), '默认根（指针载体）')
    add(os.environ.get('DSH_SKIN_ROOT'), 'env DSH_SKIN_ROOT')
    home = _registry_env().get('DSH_HOME') or os.environ.get('DSH_HOME')
    if home:
        add(os.path.join(os.path.dirname(os.path.normpath(home)), '.dsh-skins'),
            'DSH_HOME 兄弟目录')
    if os.name == 'nt':
        for drive in 'CDEFG':
            base = drive + ':\\'
            if not os.path.isdir(base):
                continue
            for suffix in ('*\\.dsh-skins', '*\\*\\.dsh-skins'):
                for hit in glob.glob(base + suffix):
                    add(hit, '{0} 盘浅扫'.format(drive))
    found.sort(key=lambda r: (not os.path.isdir(r['dir']), r['dir'].lower()))
    return found


def skin_root_audit():
    """数据根一致性审计：生效根 / 指针 / env / 全部候选根及各自活跃度。

    `warnings` 为空即健康。由 `dsh-skin.py doctor` 输出；根分裂时据此立刻发现。
    """
    registry_env = _registry_env()
    pointer_file = POINTER_CONFIG
    pointer_value = None
    try:
        with open(pointer_file, encoding='utf-8') as handle:
            pointer_value = (json.load(handle) or {}).get('skin_root')
    except (OSError, ValueError):
        pointer_value = None

    rows = []
    for row in discover_skin_roots():
        path = row['dir']
        info = dict(row)
        info['exists'] = os.path.isdir(path)
        info['active'] = (os.path.normcase(path)
                          == os.path.normcase(os.path.normpath(SKIN_ROOT)))
        info['last_write'] = _dir_freshness(path) if info['exists'] else 0.0
        info['age_days'] = ((time.time() - info['last_write']) / 86400.0
                            if info['last_write'] else None)
        cfg = {}
        if info['exists']:
            try:
                with open(os.path.join(path, 'config.json'), encoding='utf-8') as handle:
                    cfg = json.load(handle) or {}
            except (OSError, ValueError):
                cfg = {}
        info['dsh_home'] = cfg.get('dsh_home')
        info['themes'] = len(cfg.get('themes') or {})
        try:
            with open(os.path.join(path, 'plugins', 'registry.json'),
                      encoding='utf-8') as handle:
                info['plugin_count'] = len((json.load(handle) or {}).get('plugins') or {})
        except (OSError, ValueError):
            info['plugin_count'] = 0
        # 「有数据」= 存在 config.json 或插件注册表。只被顺手创建的裸目录
        # （例如测试留下的 server.token）不算数据根，不参与告警，否则体检全是噪音。
        info['has_config'] = os.path.isfile(os.path.join(path, 'config.json'))
        info['has_data'] = bool(info['has_config'] or info['plugin_count'])
        rows.append(info)

    env_skin = os.environ.get('DSH_SKIN_ROOT') or registry_env.get('DSH_SKIN_ROOT')
    dsh_home = registry_env.get('DSH_HOME') or os.environ.get('DSH_HOME')

    def _same(a, b):
        return (a and b
                and os.path.normcase(os.path.normpath(a))
                == os.path.normcase(os.path.normpath(b)))

    warnings = []
    if env_skin and not _same(env_skin, SKIN_ROOT):
        warnings.append('env DSH_SKIN_ROOT（{0}）与生效根不一致 —— 下次启动会切过去'
                        .format(env_skin))
    if pointer_value and not _same(pointer_value, SKIN_ROOT):
        warnings.append('指针 {0} 与生效根不一致'.format(pointer_value))
    if dsh_home and not os.path.isdir(dsh_home):
        warnings.append('DSH_HOME（{0}）不存在'.format(dsh_home))
    try:
        configured_home = (load_config() or {}).get('dsh_home')
    except Exception:
        configured_home = None
    if configured_home and dsh_home and not _same(configured_home, dsh_home):
        warnings.append('config.dsh_home（{0}）与 env DSH_HOME（{1}）不一致'
                        .format(configured_home, dsh_home))
    for row in rows:
        if row['active'] or not row['exists'] or not row['has_data']:
            continue
        if row['age_days'] is not None and row['age_days'] > 2:
            warnings.append('存在长期未写入的其它数据根：{0}（{1:.0f} 天前最后写入，'
                            '插件记录 {2} 条）'.format(row['dir'], row['age_days'],
                                                     row['plugin_count']))

    return {
        'active': SKIN_ROOT,
        'pointer': {'file': pointer_file, 'value': pointer_value},
        'env': {'DSH_SKIN_ROOT': env_skin, 'DSH_HOME': dsh_home},
        'config_dsh_home': configured_home,
        'candidates': rows,
        'warnings': warnings,
    }


SKIN_ROOT = _resolve_skin_root()
CONFIG = os.path.join(SKIN_ROOT, 'config.json')

DEFAULT_HARNESS_ROOT = r'D:\DSH\deepseek-harness'
# 新社区桌面壳（dsh-plugin-desktop）monorepo 根，作为 DEFAULT_HARNESS_ROOT 的并列候选
NEW_SHELL_MONOREPO = r'D:\DSH\dsh-desktop'
DEFAULT_CDP_PORT = 9222

# ---- 产品身份：兼顾官方打包版（DeepSeek Harness.exe）与新社区壳（DSH Desktop.exe）----
PRODUCT_NAME = 'DeepSeek Harness'
PRODUCT_NAME_NEW = 'DSH Desktop'
PRODUCT_EXE = 'DeepSeek Harness.exe'
PRODUCT_EXE_NEW = 'DSH Desktop.exe'
# 具名产品 exe（terminate / app_process_running 共用）；开发版 electron.exe 走
# CommandLine 特征过滤（_electron_dsh_pids），不在此列以免误杀其它 Electron 应用
COMMON_EXE_IMAGES = ('DSH Desktop.exe', 'DeepSeek Harness.exe')

# win-x64 官方打包版 exe 在仓库内的产出路径（targets/<platform>/unsigned-artifacts/win-unpacked/）
PACKAGED_TARGET_GLOB = os.path.join(
    'apps', 'desktop', '.desktop-build', 'targets', '*',
    'unsigned-artifacts', 'win-unpacked', PRODUCT_EXE)
# 新社区壳（dsh-plugin-desktop）的产出路径形态：<root>/dist/win-unpacked/DSH Desktop.exe
NEW_SHELL_EXE_NAME = PRODUCT_EXE_NEW
NEW_SHELL_LAYOUT_GLOBS = (
    # 仓库内（dsh-desktop 或 dsh-plugin-desktop 根）的构建产物
    os.path.join('dist', 'win-unpacked', NEW_SHELL_EXE_NAME),
    os.path.join('*', 'dist', 'win-unpacked', NEW_SHELL_EXE_NAME),
    os.path.join('*', '*', 'dist', 'win-unpacked', NEW_SHELL_EXE_NAME),
)

# Electron 开发模式下渲染进程调试端口
ELECTRON_PROCS = ('electron.exe', 'electron')

# 启动器默认（用户桌面快捷方式 -> start-desktop.cmd，与之一致的 cmd 模板）
# 注意：cmd 的 %PATH% 在整行执行前统一展开，两次 set 会互相覆盖，只能拼一次。
# cd 不带引号：cmd /c 对整串的首尾引号有特殊剥离规则，带引号路径会解析错乱
# （"文件名、目录名或卷标语法不正确"）。harness 路径无空格，安全。
LAUNCHER_CMD_TEMPLATE = (
    'cd /d {harness} && '
    'set "PATH=D:\\program;%APPDATA%\\npm;%PATH%" && '
    'set "pnpm_config_verify_deps_before_run=false" && '
    'call pnpm.cmd run start:desktop'
)
# 新社区壳 monorepo（dsh-desktop）是 yarn berry workspace（packageManager: yarn@4.x）：
# 根脚本 dev = 构建社区市场 + 起 dsh-plugin-desktop 开发壳（渲染进程自带 CDP）。
# 注意 PATH 上可能没有 yarn（或只有经典 yarn 1.x，跑不了 berry workspace），
# cmd 兜底必须走 corepack（按 packageManager 字段激活对应版本）。
LAUNCHER_CMD_TEMPLATE_YARN = (
    'cd /d {harness} && '
    'set "PATH=D:\\program;%APPDATA%\\npm;%PATH%" && '
    'call corepack yarn dev'
)

# ---------------- 基础工具 ----------------
def _decode(raw):
    if isinstance(raw, str):
        return raw
    try:
        return raw.decode('mbcs')
    except (LookupError, UnicodeDecodeError, AttributeError):
        try:
            return raw.decode('utf-8', 'replace')
        except Exception:
            return str(raw)


def ensure_dirs():
    os.makedirs(SKIN_ROOT, exist_ok=True)


def _default_config():
    return {'config_version': CONFIG_VERSION, 'themes': {}, 'active': None, 'channel': 'cdp'}


def _migrate_to_1(cfg):
    """v0（无版本号/TraeSkin 遗留）→ v1：补齐 DSHSkin 首版字段。"""
    cfg.setdefault('channel', 'cdp')


# 逐级迁移表：未来新增结构版本时，在此登记 {目标版本: 迁移函数}，
# migrate_config 会从用户当前版本逐级升到 CONFIG_VERSION，不跳步、不丢用户数据。
_MIGRATIONS = {1: _migrate_to_1}


def migrate_config(cfg):
    """把老结构 config 向前迁移到当前 CONFIG_VERSION（只补不改用户数据）。"""
    if not isinstance(cfg, dict):
        return _default_config()
    from_ver = int(cfg.get('config_version', 0) or 0)
    ver = from_ver
    while ver < CONFIG_VERSION:
        ver += 1
        fn = _MIGRATIONS.get(ver)
        if fn:
            fn(cfg)
        cfg['config_version'] = ver
    cfg.setdefault('themes', {})
    cfg.setdefault('active', None)
    cfg.setdefault('channel', 'cdp')
    cfg['config_version'] = CONFIG_VERSION
    return cfg


def load_config():
    with _CONFIG_LOCK:
        try:
            with open(CONFIG, encoding='utf-8') as f:
                cfg = json.load(f)
            if isinstance(cfg, dict):
                return migrate_config(cfg)
        except (OSError, json.JSONDecodeError):
            pass
        return _default_config()


def save_config(cfg):
    with _CONFIG_LOCK:
        ensure_dirs()
        cfg['config_version'] = CONFIG_VERSION
        with open(CONFIG + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(CONFIG + '.tmp', CONFIG)


def update_config(mutator):
    """原子「读-改-写」：在同一把锁内 load → mutator(cfg) → save，返回 mutator 的结果。

    ThreadingHTTPServer 多线程并发切换/保存时，避免「各自读到旧副本再覆盖」丢更新。
    mutator 直接原地修改传入的 cfg 并可返回一个值（默认返回 cfg）。
    """
    with _CONFIG_LOCK:
        cfg = load_config()
        ret = mutator(cfg)
        save_config(cfg)
        return ret if ret is not None else cfg


# 打包版 exe 解析短缓存：状态轮询每几秒调一次 desktop_exe()，避免反复 glob 扫描仓库树。
_EXE_CACHE = {'ts': 0.0, 'value': None}
_EXE_TTL = 15.0


def invalidate():
    """兼容钩子：设置（exe 路径 / 根目录 / 模式）变更后调用，清掉 exe 解析缓存。"""
    _EXE_CACHE['ts'] = 0.0
    _EXE_CACHE['value'] = None


# ---------------- CDP 端口与进程 ----------------
def cdp_port():
    v = os.environ.get('DSH_SKIN_CDP_PORT')
    if v and v.isdigit():
        return int(v)
    cfg = load_config()
    p = cfg.get('cdp_port')
    if isinstance(p, int) and 1 <= p <= 65535:
        return p
    return DEFAULT_CDP_PORT


_PORT_CACHE = {'ts': 0.0, 'port': None, 'value': None}
_PORT_TTL = 3.0
# 进程存在性短缓存：一次状态计算会多次 tasklist 查同一映像名
_PROC_CACHE = {'ts': 0.0, 'value': {}}
_PROC_TTL = 3.0


def _port_listening(port, timeout=0.2):
    import socket
    now = time.time()
    key = port if port is not None else cdp_port()
    if (_PORT_CACHE['port'] == key and now - _PORT_CACHE['ts'] < _PORT_TTL
            and _PORT_CACHE['value'] is not None):
        return _PORT_CACHE['value']
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        value = s.connect_ex(('127.0.0.1', key)) == 0
    _PORT_CACHE['ts'] = now
    _PORT_CACHE['port'] = key
    _PORT_CACHE['value'] = value
    return value


_CDP_HTTP_CACHE = {'ts': 0.0, 'key': None, 'value': None}
_CDP_HTTP_TTL = 3.0


def invalidate_cdp_cache():
    """状态变更（注入/重启）后强制下一次重新探测。"""
    _CDP_HTTP_CACHE['ts'] = 0.0
    _CDP_HTTP_CACHE['key'] = None
    _CDP_HTTP_CACHE['value'] = None
    _PORT_CACHE['ts'] = 0.0
    _PORT_CACHE['port'] = None
    _PORT_CACHE['value'] = None
    _PROC_CACHE['ts'] = 0.0
    _PROC_CACHE['value'] = {}


def _cdp_http(path='/json', port=None, timeout=0.8):
    """GET http://127.0.0.1:<port>/json，返回 JSON 列表或 None。
    1.5s 短缓存（含失败结果）：cdp_ready/cdp_page_target 在一次状态计算中会连续调用，
    避免对无响应端口重复等待 HTTP 超时。"""
    import urllib.request
    key = (path, port or cdp_port())
    now = time.time()
    if (_CDP_HTTP_CACHE['key'] == key and now - _CDP_HTTP_CACHE['ts'] < _CDP_HTTP_TTL):
        return _CDP_HTTP_CACHE['value']
    # 端口快速闸门：未监听时先判活，避免 HTTP 连接被 DROP 后硬等超时
    if not _port_listening(port or cdp_port()):
        value = None
        _CDP_HTTP_CACHE['ts'] = now
        _CDP_HTTP_CACHE['key'] = key
        _CDP_HTTP_CACHE['value'] = value
        return value
    try:
        with urllib.request.urlopen(
                'http://127.0.0.1:{0}{1}'.format(port or cdp_port(), path),
                timeout=timeout) as r:
            value = json.loads(r.read().decode('utf-8', 'replace'))
    except Exception:
        value = None
    _CDP_HTTP_CACHE['ts'] = now
    _CDP_HTTP_CACHE['key'] = key
    _CDP_HTTP_CACHE['value'] = value
    return value


def cdp_ready():
    """调试端口可访问且返回 JSON 数组（Electron CDP 已在监听）。"""
    targets = _cdp_http()
    return isinstance(targets, list)


def _is_dsh_page_url(url):
    """判断 URL 是否标识 DSH 主界面页面。

    - dsh-app://         官方渲染页协议（开发/老打包版）
    - file:///…native-ui 新社区壳的原生对话框（profile-selector/setup-wizard/recovery/dialog），
      只认路径不落到 app 的页面，不作为注入目标
    - http://127.0.0.1:<port>/ 或 http://localhost:<port>/  新社区壳的 loopback 同源 Web 页面
    """
    if url.startswith('dsh-app://'):
        return True
    if url.startswith('file://'):
        # 新社区壳的宿主窗口是 file://…native-ui/…；native-ui 页面不作为主界面
        return 'native-ui' not in url
    if url.startswith('http://127.0.0.1:') or url.startswith('http://localhost:'):
        return True
    return False


def cdp_page_target():
    """找到 DSH 主页面 target（dsh-app://、file:// 或新壳 loopback http 的 page），无则 None。

    新社区壳不再用 dsh-app:// 协议，而是把 Web 界面跑在 127.0.0.1 的临时端口上
    （同源 http）。这里按优先级挑选：明确标记的页面 > 非 native-ui 的 loopback http 页面。
    尽量匹配单一主界面，避免误选 devtools / 原生对话框。"""
    targets = _cdp_http()
    if not isinstance(targets, list):
        return None
    pages = [t for t in targets if t.get('type') == 'page']
    # 第一优先：明确的主界面标记（dsh-app:// 或非 native-ui 的 file://，如官方 dev file 页）
    for t in pages:
        url = str(t.get('url') or '')
        if url.startswith('dsh-app://') or (url.startswith('file://') and 'native-ui' not in url):
            return t
    # 新社区壳：loopback http 同源页面。若多个，取路径最长者作为主界面
    loop = [t for t in pages if _is_dsh_page_url(str(t.get('url') or ''))]
    if loop:
        loop.sort(key=lambda t: len(str(t.get('url') or '')), reverse=True)
        return loop[0]
    # 兜底：唯一 page（排除 devtools）
    for t in pages:
        if not str(t.get('url') or '').startswith('devtools://'):
            return t
    return None


def is_running():
    """桌面版是否在运行：CDP 端口在监听，或 electron/打包版 exe 进程存在。"""
    if cdp_ready():
        return True
    return app_process_running()


_PROC_CHECK_MODE = {'use': 'tasklist'}  # tasklist | powershell | broken

# 打包 exe 无父控制台：所有探测型 run/popen 必须带 CREATE_NO_WINDOW，
# 否则每次调用都会新建可见 cmd 窗口（周期性轮询 → 不断弹窗）。
_NO_WINDOW_FLAGS = (getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                    if os.name == 'nt' else 0)


def _task_has_image(image_name):
    """是否存在指定映像名进程（3s 短缓存，合并一次状态内的重复查询）。

    首选用 tasklist；若 tasklist 被拒绝/无输出（例如 DSH 以管理员运行而当前进程非
    管理员，tasklist 会「Access denied」返回空），自动降级用 PowerShell Get-Process
    （Get-Process 不受该权限差影响，稳定可见）。检测手段形成后缓存，避免反复空转。"""
    now = time.time()
    key = image_name.lower()
    if now - _PROC_CACHE['ts'] < _PROC_TTL and key in _PROC_CACHE['value']:
        return _PROC_CACHE['value'][key]

    result = False
    use = _PROC_CHECK_MODE['use']

    if use == 'powershell':
        result = _task_has_image_pwsh(image_name)
    else:
        # tasklist（首用）或 fallback 后仍优先 tasklist（用户已手动换成可信方式）
        try:
            out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq {0}'.format(image_name), '/NH'],
                                 capture_output=True, timeout=8, creationflags=_NO_WINDOW_FLAGS)
            text = (_decode(out.stdout or b'') + _decode(out.stderr or b'')).lower()
            found = image_name.lower() in text
            denied = 'access denied' in text
            if denied:
                # tasklist 权限受限（常见于 DSH 以管理员运行而本进程非管理员）：
                # 整站切到 PowerShell Get-Process，它不受该权限差影响。
                _PROC_CHECK_MODE['use'] = 'powershell'
                result = _task_has_image_pwsh(image_name)
            else:
                result = found
        except Exception:
            try:
                result = _task_has_image_pwsh(image_name)
            except Exception:
                result = False
            _PROC_CHECK_MODE['use'] = 'powershell'

    if now - _PROC_CACHE['ts'] >= _PROC_TTL:
        _PROC_CACHE['ts'] = now
        _PROC_CACHE['value'] = {}
    _PROC_CACHE['value'][key] = result
    return result


def _task_has_image_pwsh(image_name):
    """用 PowerShell Get-Process 探测（tasklist 权限受限时替代，稳定且不受提权差影响）。"""
    base = image_name.replace('.exe', '')
    cmd = ('powershell -NoProfile -NonInteractive -Command '
           '"Get-Process -Name {0} -ErrorAction SilentlyContinue | Select-Object -First 1"'
           .format(_pwsh_quote(base)))
    out = subprocess.run(cmd, capture_output=True, timeout=8, shell=True,
                         creationflags=_NO_WINDOW_FLAGS)
    return base.lower() in _decode(out.stdout or b'').lower()


def _pwsh_quote(s):
    return "'{0}'".format(s.replace("'", "''"))


def _electron_dsh_pids():
    """返回 DSH 相关 electron.exe 开发进程的 PID 列表。

    用 PowerShell Get-CimInstance 一次取回所有 electron.exe 的 ProcessId/CommandLine，
    CommandLine 小写含 deepseek / dsh-desktop / --remote-debugging-port 任一特征才算
    DSH（VS Code 等其它 Electron 应用不含这些特征，避免误判/误杀）。异常/空输出返回空列表。"""
    cmd = ('powershell -NoProfile -NonInteractive -Command '
           '"Get-CimInstance Win32_Process -Filter {0} | '
           'ForEach-Object {{ $_.ProcessId.ToString() + [char]9 + $_.CommandLine }}"'
           .format(_pwsh_quote("Name='electron.exe'")))
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=8, shell=True,
                         creationflags=_NO_WINDOW_FLAGS)
    except Exception:
        return []
    pids = []
    for line in _decode(out.stdout or b'').splitlines():
        pid_s, sep, cmdline = line.partition('\t')
        if not sep:
            continue
        low = cmdline.lower()
        if 'deepseek' in low or 'dsh-desktop' in low or '--remote-debugging-port' in low:
            try:
                pids.append(int(pid_s))
            except ValueError:
                continue
    return pids


def _dsh_electron_running():
    """electron.exe 是否有 DSH 开发进程在跑（复用 _PROC_CACHE 3s 短缓存防轮询风暴）。"""
    now = time.time()
    key = 'electron-dsh'
    if now - _PROC_CACHE['ts'] < _PROC_TTL and key in _PROC_CACHE['value']:
        return _PROC_CACHE['value'][key]
    result = bool(_electron_dsh_pids())
    if now - _PROC_CACHE['ts'] >= _PROC_TTL:
        _PROC_CACHE['ts'] = now
        _PROC_CACHE['value'] = {}
    _PROC_CACHE['value'][key] = result
    return result


def app_process_running():
    """生产版（DeepSeek Harness.exe / DSH Desktop.exe）或开发版（electron.exe）是否有进程在跑。"""
    for img in COMMON_EXE_IMAGES:
        if _task_has_image(img):
            return True
    return _dsh_electron_running()


def find_installed_exe():
    """定位生产安装版 exe。config.dsh_exe 显式指定优先，其次检索安装目录与新社区壳常见位置。

    覆盖两类产品名：DeepSeek Harness（官方）与 DSH Desktop（新社区壳）。"""
    cfg = load_config()
    explicit = cfg.get('dsh_exe')
    candidates = []
    if explicit:
        candidates.append(explicit)
    for name in (PRODUCT_NAME_NEW, PRODUCT_NAME):
        candidates += [
            os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', name, PRODUCT_EXE_NEW),
            os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', name, PRODUCT_EXE),
            os.path.join(os.environ.get('ProgramFiles', ''), name, PRODUCT_EXE_NEW),
            os.path.join(os.environ.get('ProgramFiles', ''), name, PRODUCT_EXE),
            os.path.join(os.environ.get('ProgramFiles(x86)', ''), name, PRODUCT_EXE),
        ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


# ---------------- 启动模式（dev / packaged） ----------------
def launch_mode():
    """启动目标模式：'dev'（源码开发模式）| 'packaged'（win-x64 / 新社区壳打包版）。

    config.launcher_mode 显式值永远优先（'packaged'/'dev'）。
    未设置时自动探测：能解析到打包版 exe（官方 targets/... 或新壳 dist/win-unpacked）
    就用 packaged，否则 dev——新壳机器（只跑 win-unpacked 产物）无需手工配置。
    旧值（'auto'/'cmd'/'direct'）仍按 dev 处理，向后兼容。
    """
    cfg = load_config()
    lm = cfg.get('launcher_mode')
    if lm in ('packaged', 'dev'):
        return lm
    if lm is not None:
        return 'dev'
    exe, _src = desktop_exe()
    return 'packaged' if exe else 'dev'


def _desktop_exe_scan():
    """打包版 exe 路径解析（launcher_mode=packaged 的启动目标）。返回 (path, source)。

    优先级：env DSH_SKIN_DESKTOP_EXE > config.json.desktop_exe >
    扫描官方仓库打包产出 <root>/apps/desktop/.desktop-build/targets/*/unsigned-artifacts/win-unpacked/
    扫描新社区壳产出 <root>/dist/win-unpacked/DSH Desktop.exe
    （roots 含 harness_root、默认 D:\\DSH\\deepseek-harness、新壳 monorepo D:\\DSH\\dsh-desktop）。
    找不到返回 (None, None)。
    """
    env = os.environ.get('DSH_SKIN_DESKTOP_EXE')
    if env and os.path.isfile(env):
        return env, 'env DSH_SKIN_DESKTOP_EXE'
    cfg = load_config()
    c = cfg.get('desktop_exe')
    if c and os.path.isfile(c):
        return c, 'config.desktop_exe'
    roots = []
    hr = harness_root()
    for r in (hr, DEFAULT_HARNESS_ROOT, NEW_SHELL_MONOREPO):
        if r and r not in roots:
            roots.append(r)
    # 官方打包产出（老 win-x64 布局）
    for r in roots:
        hits = sorted(glob.glob(os.path.join(r, PACKAGED_TARGET_GLOB)))
        if hits:
            return hits[0], '官方打包产出目录扫描'
    # 新社区壳 dist/win-unpacked 布局（含 dsh-desktop 的直接子包、兄弟目录等形态）
    for r in roots:
        for g in NEW_SHELL_LAYOUT_GLOBS:
            hits = sorted(glob.glob(os.path.join(r, g)))
            if hits:
                return hits[0], '新壳 dist/win-unpacked 扫描'
    return None, None


def desktop_exe():
    """带短缓存（15s）的打包版 exe 解析——launch_mode() 自动探测与状态轮询都会走到。"""
    now = time.time()
    if _EXE_CACHE['value'] is not None and now - _EXE_CACHE['ts'] < _EXE_TTL:
        return _EXE_CACHE['value']
    val = _desktop_exe_scan()
    _EXE_CACHE['ts'] = now
    _EXE_CACHE['value'] = val
    return val


def _root_has_dsh_features(path):
    """判断 path 是否为有效 DSH 数据根：settings.yaml 存在且含 DSH 关键命名空间。
    用于拦截历史残留根（如旧 D:\\DATA\\DSHdata）误路由到空配置。"""
    if not path or not os.path.isdir(path):
        return False
    sf = os.path.join(path, 'settings.yaml')
    if not os.path.isfile(sf):
        return False
    try:
        with open(sf, encoding='utf-8', errors='replace') as f:
            head = f.read(4096)
    except Exception:
        return False
    return ('llm-pi-ai' in head or 'agent-default-model' in head
            or 'dsh-desktop' in head or 'ui-onboarding' in head)


def _registry_dsh_home():
    """读取注册表 DSH_HOME（User 优先，Machine 兜底）。当 exe 从旧会话/残留进程
    继承了过期 env 时，注册表里的现值是更可信的正确根；非 Windows / 无值 → None。"""
    if os.name != 'nt':
        return None
    try:
        import winreg
    except Exception:
        return None
    keys = [
        (winreg.HKEY_CURRENT_USER, r'Environment'),
        (winreg.HKEY_LOCAL_MACHINE,
         r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'),
    ]
    for hive, sub in keys:
        try:
            with winreg.OpenKey(hive, sub) as k:
                v, _ = winreg.QueryValueEx(k, 'DSH_HOME')
            if v and os.path.isdir(v):
                return v
        except OSError:
            continue
    return None


def dsh_home():
    """解析 DSH host 数据根（会话/凭证存放目录）。返回 (path, source)。

    dev 模式：config.dsh_home > env DSH_HOME > 仓库 development/home > ~/.dsh（须存在）
    packaged 模式：config.dsh_home > env DSH_HOME > ~/.dsh（默认，目录未建也返回，
    供诊断提示「打包版尚未产生数据」）。显式覆盖始终最高优先，~/.dsh 只作默认。
    """
    cfg = load_config()
    cand = cfg.get('dsh_home')
    if cand and os.path.isdir(cand):
        return cand, 'config.dsh_home'
    env = os.environ.get('DSH_HOME')
    if env and os.path.isdir(env):
        if _root_has_dsh_features(env):
            return env, 'env DSH_HOME'
        # 残留旧根拦截（如机器残留的 D:\DATA\DSHdata\.dsh）：env 根缺 DSH 特征，
        # 优先回退注册表现值；两者都不可用才保留 env 原值（保守兜底）。
        alt = _registry_dsh_home()
        if alt is not None and alt != env and _root_has_dsh_features(alt):
            return alt, '注册表 DSH_HOME（env 残留根已拦截）'
        return env, 'env DSH_HOME（无 DSH 特征，保留原值）'
    if launch_mode() == 'packaged':
        fb = os.path.join(os.path.expanduser('~'), '.dsh')
        alt = _registry_dsh_home()
        if alt and alt != fb and not _root_has_dsh_features(fb) and _root_has_dsh_features(alt):
            return alt, '注册表 DSH_HOME（~/.dsh 无 DSH 数据）'
        return fb, 'packaged 默认 ~/.dsh'
    root = harness_root()
    if root:
        dev = os.path.join(root, 'apps', 'desktop', '.desktop-build', 'development', 'home')
        if os.path.isdir(dev):
            return dev, 'dev 仓库 development/home'
    fb = os.path.join(os.path.expanduser('~'), '.dsh')
    if os.path.isdir(fb):
        return fb, '回退 ~/.dsh'
    return None, '未解析'


def cdp_mode():
    """精细运行状态：
    - 'ready'    : 调试端口可用且能看到 DSH 页面（换肤/增强通道就绪）
    - 'no-debug' : DSH 进程在跑但没有调试端口（典型：生产 exe 直接启动）→ 需以调试端口重启
    - 'stopped'  : DSH 没在运行 → 直接启动即可
    """
    if cdp_ready() and cdp_page_target():
        return 'ready'
    if app_process_running():
        return 'no-debug'
    return 'stopped'


def _terminate_app():
    """结束运行中的 DSH 进程树。Electron 对温和 WM_CLOSE 会挂起，故直接 /T /F 强杀
    （DSH 会话持久化落盘，强杀不丢历史）。返回是否已全部退出。"""
    import time as _t
    to_kill = sorted(set(COMMON_EXE_IMAGES))
    for img in to_kill:
        if not _task_has_image(img):
            continue
        subprocess.run(['taskkill', '/IM', img, '/T', '/F'], capture_output=True)
    for pid in _electron_dsh_pids():
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True)
    for _ in range(20):
        if not app_process_running():
            return True
        _t.sleep(0.5)
    return not app_process_running()


def _launch_env_and_cwd(exe):
    """构造启动打包版/新壳 exe 时的 (env, cwd)。

    - 透传 DSH_HOME（来自 config.dsh_home → env DSH_HOME），保证新社区壳 / 官方打包版
      把数据写到 DSH++ 解析到的同一 home（新壳的 @deepseek-ai/dsh-home-paths 解析 DSH_HOME）。
    - cwd 设为 exe 所在目录，与用户快捷方式（launch-dsh.cmd）的工作目录一致，规避相对路径坑。
    """
    import copy
    env = copy.copy(os.environ)
    home = None
    cfg = load_config()
    if cfg.get('dsh_home'):
        home = cfg['dsh_home']
    elif os.environ.get('DSH_HOME'):
        home = os.environ['DSH_HOME']
    if home:
        env['DSH_HOME'] = home
    cwd = os.path.dirname(exe) if exe else None
    return env, cwd


def relaunch_with_debug(force_restart=False, port=None):
    """以渲染调试端口启动 DSH，让无端口的生产版也具备 CDP 注入通道（不改任何 DSH 文件）。

    - 已就绪：直接返回；
    - 未运行：dev 用启动器、生产用 exe + --remote-debugging-port；
    - 在跑但无端口（force_restart=True 时）：礼貌关闭后带端口重启。
    返回 (ok, msg, action)。
    """
    port = port or cdp_port()
    mode = cdp_mode()
    if mode == 'ready':
        return True, '调试端口 {0} 已就绪'.format(port), 'none'
    if mode == 'no-debug' and not force_restart:
        return False, 'DSH 正在运行但未开调试端口；需以注入模式重启（会先关闭当前窗口再重开）', 'need-restart'

    if mode == 'no-debug':
        if not _terminate_app():
            return False, '旧进程未能关闭，已取消重启（请手动退出 DSH 后重试）', 'blocked'

    if launch_mode() == 'packaged':
        # 打包版：杀旧实例 → exe --remote-debugging-port（沿用单实例锁语义，allow_kill 不变）
        exe, _src = desktop_exe()
        if not exe:
            return False, ('未找到打包版 exe：设 DSH_SKIN_DESKTOP_EXE / config.desktop_exe，'
                           '或先运行打包脚本 .desktop-build/run-package-win-dir.cmd'), 'blocked'
        try:
            env, cwd = _launch_env_and_cwd(exe)
            subprocess.Popen([exe, '--remote-debugging-port={0}'.format(port)],
                             env=env, cwd=cwd,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                                      | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
        except OSError as e:
            return False, '打包版启动失败: {0}'.format(e), 'blocked'
    else:
        # 优先生产 exe（显式带 chromium 调试开关，Electron 会透传）
        exe = find_installed_exe()
        if exe:
            try:
                env, cwd = _launch_env_and_cwd(exe)
                subprocess.Popen([exe, '--remote-debugging-port={0}'.format(port)],
                                 env=env, cwd=cwd,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                                      | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
            except OSError as e:
                return False, '生产版启动失败: {0}'.format(e), 'blocked'
        else:
            ok, msg = launch_dsh()
            if not ok:
                return False, msg, 'blocked'
    # 等待端口就绪
    import time as _t
    for _ in range(40):
        if cdp_ready() and cdp_page_target():
            return True, '已以注入模式启动（调试端口 {0}）'.format(port), 'launched'
        _t.sleep(0.5)
    return False, '已发起启动，但 20s 内调试端口未就绪', 'blocked'


# ---------------- 启动器解析（Node / pnpm / harness） ----------------
def harness_root():
    """deepseek-harness 源码根目录（只读，绝不写入）。

    旧默认 D:\\DSH\\deepseek-harness 已迁移为 D:\\DSH\\dsh-desktop\\deepseek-harness
    （新社区壳 monorepo）。按 环境变量 > config > 旧默认 > 新 monorepo 顺序探测。"""
    for v in (os.environ.get('DSH_SKIN_HARNESS_ROOT'), load_config().get('dsh_root')):
        if v and os.path.isfile(os.path.join(v, 'package.json')):
            return v
    for root in (DEFAULT_HARNESS_ROOT,
                 os.path.join(NEW_SHELL_MONOREPO, 'deepseek-harness'),
                 NEW_SHELL_MONOREPO):
        if os.path.isfile(os.path.join(root, 'package.json')):
            return root
    return None


def harness_version():
    """读取源码包 version（DOM 指纹基线的维度之一）。

    新社区壳 monorepo 的根 package.json 无 version，故按序回退到
    dsh-plugin-desktop / deepseek-harness 子包（谁有取谁）。"""
    root = harness_root()
    if not root:
        return None
    cands = [
        os.path.join(root, 'package.json'),
        os.path.join(root, 'dsh-plugin-desktop', 'package.json'),
        os.path.join(root, 'deepseek-harness', 'package.json'),
    ]
    for pj in cands:
        try:
            if not os.path.isfile(pj):
                continue
            with open(pj, encoding='utf-8') as f:
                v = (json.load(f) or {}).get('version')
            if v:
                return v
        except (OSError, ValueError):
            continue
    return None


def _node_version(node):
    try:
        r = subprocess.run([node, '--version'], capture_output=True, timeout=8)
        return _decode(r.stdout or b'').strip()
    except Exception:
        return ''


_RESOLVE_CACHE = {}


def resolve_node():
    """找一个 >= 22 的 node。返回 (路径, 版本) 或 (None, None)。进程内缓存（运行期不变）。"""
    if 'node' in _RESOLVE_CACHE:
        return _RESOLVE_CACHE['node']
    env = os.environ.get('DSH_SKIN_NODE')
    candidates = []
    if env:
        candidates.append(env)
    candidates += [
        r'D:\program\node.exe',
        os.path.join(os.environ.get('ProgramFiles', ''), 'nodejs', 'node.exe'),
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'node', 'node.exe'),
    ]
    try:
        candidates.append(shutil.which('node'))
    except Exception:
        pass
    seen = set()
    for c in candidates:
        if not c or c in seen or not os.path.isfile(c):
            continue
        seen.add(c)
        ver = _node_version(c)
        m = re.match(r'v?(\d+)\.', ver or '')
        if m and int(m.group(1)) >= 22:
            _RESOLVE_CACHE['node'] = (c, ver)
            return c, ver
    # 最后兜底：能跑通 pnpm 的 node 也可用（版本未知不拦）
    for c in seen:
        if os.path.isfile(c):
            r = (c, _node_version(c))
            _RESOLVE_CACHE['node'] = r
            return r
    _RESOLVE_CACHE['node'] = (None, None)
    return None, None


def resolve_pnpm():
    """返回可执行的 pnpm.cjs 路径（corepack 管理版优先）。进程内缓存。"""
    if 'pnpm' in _RESOLVE_CACHE:
        return _RESOLVE_CACHE['pnpm']
    env = os.environ.get('DSH_SKIN_PNPM')
    if env and os.path.isfile(env):
        _RESOLVE_CACHE['pnpm'] = env
        return env
    roots = [
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'node', 'corepack', 'v1', 'pnpm'),
        os.path.join(os.environ.get('APPDATA', ''), 'node', 'corepack', 'v1', 'pnpm'),
    ]
    for root in roots:
        try:
            for ver in sorted(os.listdir(root), reverse=True):
                p = os.path.join(root, ver, 'bin', 'pnpm.cjs')
                if os.path.isfile(p):
                    _RESOLVE_CACHE['pnpm'] = p
                    return p
        except OSError:
            continue
    try:
        c = shutil.which('pnpm.cjs')
        if c:
            _RESOLVE_CACHE['pnpm'] = c
            return c
    except Exception:
        pass
    _RESOLVE_CACHE['pnpm'] = None
    return None


def resolve_yarn():
    """返回可执行的 yarn（corepack 管理版优先，单文件 yarn.js / bin/yarn.cjs 两种布局）。
    刻意不认 PATH 上的经典 yarn 1.x（跑不了 berry workspace）。进程内缓存。"""
    if 'yarn' in _RESOLVE_CACHE:
        return _RESOLVE_CACHE['yarn']
    env = os.environ.get('DSH_SKIN_YARN')
    if env and os.path.isfile(env):
        _RESOLVE_CACHE['yarn'] = env
        return env
    roots = [
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'node', 'corepack', 'v1', 'yarn'),
        os.path.join(os.environ.get('APPDATA', ''), 'node', 'corepack', 'v1', 'yarn'),
    ]
    for root in roots:
        try:
            vers = [v for v in sorted(os.listdir(root), reverse=True)
                    if re.match(r'^\d+\.', v or '')]
        except OSError:
            continue
        for v in vers:
            for sub in ('yarn.js', os.path.join('bin', 'yarn.cjs')):
                p = os.path.join(root, v, sub)
                if os.path.isfile(p):
                    _RESOLVE_CACHE['yarn'] = p
                    return p
    try:
        c = shutil.which('yarn.js') or shutil.which('yarn.cjs')
        if c:
            _RESOLVE_CACHE['yarn'] = c
            return c
    except Exception:
        pass
    _RESOLVE_CACHE['yarn'] = None
    return None


def _repo_package_manager(root):
    """读仓库的包管理器（package.json 的 packageManager 字段，自 workspace 成员向上回溯）。
    返回 'yarn' / 'pnpm' / None（旧仓库无该字段 → None，走 pnpm 旧模板）。"""
    if not root:
        return None
    d = os.path.abspath(root)
    seen = set()
    while d and d.lower() not in seen:
        seen.add(d.lower())
        pj = os.path.join(d, 'package.json')
        if os.path.isfile(pj):
            try:
                with open(pj, encoding='utf-8') as f:
                    pm = (json.load(f) or {}).get('packageManager') or ''
                if pm:
                    return pm.split('@', 1)[0].strip().lower()
            except (OSError, ValueError):
                pass
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def _dev_launch_target():
    """解析 dev 模式的启动目标，返回 (pm, cwd, script)。

    新社区壳 monorepo（dsh-desktop）是 yarn workspace；用户实际运行的产品是社区壳
    （dsh-plugin-desktop），其 dev 入口在 monorepo 根的 `dev` 脚本（构建社区市场 +
    插件开发壳，渲染进程自带 CDP）。嵌套的官方仓库 deepseek-harness（pnpm，
    packageManager 独立声明）是源码依赖而非产品——harness_root 落在它内部时，
    提升到 monorepo 根用 yarn dev 启动社区壳。其余情况用仓库自身 packageManager：
    yarn → `dev`，pnpm/旧仓库 → `start:desktop`。"""
    root = harness_root()
    if not root:
        return None, None, None
    mono = os.path.abspath(NEW_SHELL_MONOREPO)
    r = os.path.abspath(root).lower()
    m = mono.lower()
    if r == m or r.startswith(m + os.sep):
        pm = _repo_package_manager(mono)
        if pm in ('yarn', 'pnpm'):
            script = 'dev' if pm == 'yarn' else ('dev' if _has_script(mono, 'dev') else 'start:desktop')
            return pm, mono, script
    pm = _repo_package_manager(root)
    if pm == 'yarn':
        return 'yarn', root, 'dev'
    return 'pnpm', root, 'start:desktop'


def _has_script(root, name):
    try:
        with open(os.path.join(root, 'package.json'), encoding='utf-8') as f:
            return bool((json.load(f) or {}).get('scripts', {}).get(name))
    except (OSError, ValueError):
        return False


def resolve_launcher():
    """启动命令形态：'packaged'（打包版 exe + --remote-debugging-port）、
    'cmd'（bat 模板，与用户快捷方式一致）或 'direct'（node+pnpm/yarn 直启）。

    dev 模式按仓库 packageManager 自适应：yarn berry（新社区壳 dsh-desktop monorepo）
    → node + corepack yarn.js + `dev`；pnpm/旧仓库 → node + pnpm.cjs + start:desktop。"""
    if launch_mode() == 'packaged':
        exe, _src = desktop_exe()
        if exe:
            return 'packaged', exe
        return None, None
    cfg = load_config()
    mode = cfg.get('launcher_mode', 'dev')
    pm, cwd, script = _dev_launch_target()
    if pm == 'yarn':
        node, _v = resolve_node()
        yarn = resolve_yarn()
        if node and yarn:
            return 'direct', [node, yarn, script]
        if cwd:
            return 'cmd', LAUNCHER_CMD_TEMPLATE_YARN.format(harness=cwd)
        return None, None
    if pm:
        if mode == 'direct':
            node, _v = resolve_node()
            pnpm = resolve_pnpm()
            if node and pnpm:
                return 'direct', [node, pnpm, 'run', script]
        if cwd:
            return 'cmd', LAUNCHER_CMD_TEMPLATE.format(harness=cwd)
    return None, None


def launch_command():
    """返回可用于 subprocess 的完整启动命令列表。"""
    kind, val = resolve_launcher()
    if kind == 'direct':
        return val
    if kind == 'cmd':
        return ['cmd.exe', '/c', val]
    if kind == 'packaged':
        return [val, '--remote-debugging-port={0}'.format(cdp_port())]
    return None


def launch_dsh():
    """手动启动 DeepSeek Harness 桌面版（按当前模式）：
    dev = 按 packageManager 走 yarn dev / pnpm start:desktop（自带 CDP）；
    packaged = 打包版 exe + --remote-debugging-port。返回 (ok, msg)。
    注意：本函数只应由用户显式动作（面板按钮 / CLI / bat）调用。"""
    kind, val = resolve_launcher()
    if kind == 'packaged':
        if is_running():
            return True, 'DeepSeek Harness 已在运行（CDP 端口 {0}）'.format(cdp_port())
        try:
            env, cwd = _launch_env_and_cwd(val)
            subprocess.Popen([val, '--remote-debugging-port={0}'.format(cdp_port())],
                             env=env, cwd=cwd,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                                      | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
            return True, '已启动打包版 DeepSeek Harness（调试端口 {0}）'.format(cdp_port())
        except Exception as e:
            return False, '打包版启动失败: {0}'.format(e)
    if launch_mode() == 'packaged':
        return False, ('未找到打包版 exe：设 DSH_SKIN_DESKTOP_EXE / config.desktop_exe，'
                       '或先运行打包脚本 .desktop-build/run-package-win-dir.cmd')
    root = harness_root()
    if not root:
        return False, '未找到 deepseek-harness 根目录（设 DSH_SKIN_HARNESS_ROOT 或 config.dsh_root）'
    # direct 模式的 cwd 必须是解析出的 dev 目标目录（新壳=monorepo 根，旧仓库=自身根）
    _pm, dev_cwd, _script = _dev_launch_target()
    cwd = dev_cwd or root
    cmd = launch_command()
    if not cmd:
        return False, '无法解析启动命令：需要 Node>=22 与 pnpm/yarn（corepack 优先）'
    if is_running():
        return True, 'DeepSeek Harness 已在运行（CDP 端口 {0}）'.format(cdp_port())
    try:
        subprocess.Popen(cmd, cwd=cwd,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                         | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
        return True, '已发送启动指令（渲染进程调试端口 {0}）'.format(cdp_port())
    except Exception as e:
        return False, '启动失败: {0}'.format(e)


def product_label(exe=None):
    """根据解析到的 exe 返回面向用户的产品名（新社区壳 DSH Desktop / 官方 DeepSeek Harness）。

    供面板/CLI 显示实际正在打交道的壳子，避免在新壳机器上仍显示旧名造成误导。"""
    if exe is None:
        exe, _ = desktop_exe()
    if exe and PRODUCT_EXE_NEW.lower() in str(exe).lower():
        return PRODUCT_NAME_NEW
    return PRODUCT_NAME


# ---------------- 探测报告 ----------------
def detect_report():
    node, nv = resolve_node()
    pnpm = resolve_pnpm()
    yarn = resolve_yarn()
    root = harness_root()
    kind, val = resolve_launcher()
    mode = launch_mode()
    exe, exe_src = desktop_exe()
    home, home_src = dsh_home()
    return {
        'app': APP_TAG,
        'product': product_label(exe),
        'skin_root': SKIN_ROOT,
        'config_exists': os.path.exists(CONFIG),
        'harness_root': root,
        'harness_ok': bool(root),
        'launcher_mode': mode,
        'desktop_exe': exe,
        'desktop_exe_source': exe_src,
        'dsh_home': home,
        'dsh_home_source': home_src,
        'cdp_port': cdp_port(),
        'cdp_ready': cdp_ready(),
        'cdp_target': bool(cdp_page_target()),
        'cdp_mode': cdp_mode(),
        'installed_exe': find_installed_exe(),
        'app_running': app_process_running(),
        'running': is_running(),
        'node': node,
        'node_version': nv,
        'pnpm': pnpm,
        'yarn': yarn,
        'repo_pm': _repo_package_manager(root),
        'launcher': kind,
        'launcher_detail': val if kind == 'direct' else (val[:120] if val else None),
    }


if __name__ == '__main__':
    import pprint
    pprint.pprint(detect_report())
