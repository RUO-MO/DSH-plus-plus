# -*- coding: utf-8 -*-
"""DSH++ 本地后端（原 DSHSkin）：给 panel.html 提供真实 API，直接驱动 dsh-skin.py

换肤已移除（2026-09-20）：动态壁纸改为参考 dsh-wallpaper-engine 契约实现的适配层；
本后端只保留「增强 / 选择器适配 / 会话与凭证 / 插件管理 / CDP 注入」。

用法:
  python server.py [--port 8765] [--no-open]

API:
  GET  /                     面板页面（静态托管项目目录）
  GET  /api/status           环境状态（目录/CDP/选择器健康/依赖/增强）
  GET  /api/detect           路径探测报告
  GET  /api/deps             依赖状态
  GET  /api/doctor           综合体检（结构化）
  GET  /api/cdp-status       CDP 目标与注入状态
  GET  /api/selectors        选择器覆盖与内置默认
  GET  /api/logs             操作日志
  GET  /api/template         区域定义（换肤参数 schema 已移除，仅保留区域契约）
  GET  /api/wallpaper        动态壁纸插件状态 + 壁纸清单 + 可调参数
  POST /api/wallpaper {id?|settings?}  切换壁纸 / 调参（写插件 config.json）
  POST /api/restore                  清除 CDP 注入
  POST /api/repair                   注入自检与修复（CDP 补注）
  POST /api/probe  {apply?}          CDP 实测选择器，可写入覆盖完成适配
  POST /api/selectors {regions|reset?}  写入/清空选择器覆盖
  POST /api/settings {cdp_port?, dsh_root?, launcher_mode?, desktop_exe?}
  POST /api/cdp-apply            CDP 热注入（CDP 未就绪只返回状态，绝不自动拉起 DSH）
  POST /api/restart-dsh              重启 DeepSeek Harness（需用户确认后调用）

已移除（换肤，2026-09-20）：
  · 状态变更类一律返回 410：/api/switch、/api/remove、/api/install、
    /api/rebuild/<id>、/api/theme-params/<id>
  · 只读类返回 200 空壳（带 legacy_removed 标记，避免旧书签报 500）：
    /api/themes
  · 已删除（404）：/api/theme-image/<id>、/api/preview-css/<id>、/api/export/<id>
"""
import argparse
import base64
import importlib.util
import io
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
import zipfile
try:
    import yaml
except ImportError:      # PyYAML 缺失时服务仍可启动（模型读取走 mini 解析，config 型偏好保存报清晰错误）
    yaml = None
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import cdp_skin
import dsh_env
import enhance_engine
import plugin_manager
import marker_engine
import tray
import updater
import wallpaper_engine

# 打包为 exe（PyInstaller onefile）后，__file__ 指向临时解压目录 _MEIPASS
if getattr(sys, 'frozen', False):
    ROOT = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
else:
    ROOT = os.path.dirname(os.path.abspath(__file__))

PORT = 8765
LOG_FILE = None  # 由 ensure_dirs 后设置


def _app_quit():
    """面板「退出」：延迟后终止本进程（打包版/源码 run 均适用），
    释放 dist\\DSH++.exe 供重新构建。Timer 线程内执行，避免阻塞响应。"""
    time.sleep(0.3)
    try:
        os._exit(0)
    except Exception:
        pass

# dsh-skin.py 带连字符，无法直接 import，按路径加载模块
_spec = importlib.util.spec_from_file_location('dsh_skin_main', os.path.join(ROOT, 'dsh-skin.py'))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)   # 复用其核心逻辑（路径 / 配置 / switch / install ...）
ts.ensure_dirs()
LOG_FILE = os.path.join(ts.SKIN_ROOT, 'logs.json')

# ---------------- 本机 API 随机令牌（P2-5 纵深防御） ----------------
# 面板由本 server 托管，启动时生成随机 token 注入页面；所有状态变更与敏感读取
# 必须带正确 token，拦截「本机其它进程无 Origin 直接 curl 8765」这类越权读取。
TOKEN_FILE = os.path.join(ts.SKIN_ROOT, 'server.token')
SENSITIVE_GET = {'/api/providers', '/api/models', '/api/sessions', '/api/sessions-search',
                 '/api/session-detail', '/api/enhance', '/api/enhance-file',
                 '/api/desktop-activate', '/api/usage-stats', '/api/plugins-task',
                 '/api/token-usage', '/api/wallpaper'}

# 插件导入后台任务表：/api/plugins-install 提交后立即返回 task id，
# 面板轮询 /api/plugins-task 获取状态；解压/校验/npm 安装全程不阻塞界面。
_PLUGIN_TASKS = {}                  # task_id -> {status, msg, info, ts}
_PLUGIN_TASKS_LOCK = threading.Lock()
_PLUGIN_TASK_TTL = 600.0            # 任务结果保留 10 分钟


def _plugin_task_new(name):
    tid = secrets.token_hex(8)
    _PLUGIN_TASKS[tid] = {'status': 'running', 'name': name, 'msg': '提交成功，等待后台执行',
                          'info': None, 'ts': time.time()}
    return tid


def _plugin_task_set(tid, **kw):
    kw['ts'] = time.time()
    with _PLUGIN_TASKS_LOCK:
        if tid in _PLUGIN_TASKS:
            _PLUGIN_TASKS[tid].update(kw)


def _plugin_task_payload(tid):
    now = time.time()
    with _PLUGIN_TASKS_LOCK:
        for k in [k for k, v in list(_PLUGIN_TASKS.items()) if now - v.get('ts', 0) > _PLUGIN_TASK_TTL]:
            _PLUGIN_TASKS.pop(k, None)
        t = _PLUGIN_TASKS.get(tid)
        if not t:
            return {'ok': False, 'msg': '任务不存在或已过期'}
        out = {'ok': True, 'task': tid, 'status': t.get('status'), 'name': t.get('name'),
               'msg': t.get('msg'), 'info': t.get('info')}
    return out

# 桌面应用（desktop_app.py）注册的「激活窗口」回调：第二实例启动时调用它唤起已有窗口。
DESKTOP_ACTIVATE_HOOKS = []


def _load_or_create_token():
    """复用未过期的既有 token（面板热刷新不掉线），否则新生成并落盘（仅当前用户可读）。"""
    try:
        if os.path.isfile(TOKEN_FILE):
            with open(TOKEN_FILE, encoding='utf-8') as f:
                tok = f.read().strip()
            if re.fullmatch(r'[0-9a-f]{32,64}', tok or ''):
                return tok
    except OSError:
        pass
    tok = secrets.token_hex(24)
    try:
        with open(TOKEN_FILE + '.tmp', 'w', encoding='utf-8') as f:
            f.write(tok)
        os.replace(TOKEN_FILE + '.tmp', TOKEN_FILE)
        try:
            os.chmod(TOKEN_FILE, 0o600)   # 仅属主可读写
        except OSError:
            pass
    except OSError:
        pass
    return tok


SERVER_TOKEN = _load_or_create_token()


def _version_tuple(v):
    nums = re.findall(r'\d+', str(v or ''))
    return tuple(int(x) for x in nums[:3]) + (0,) * (3 - min(3, len(nums)))


# 内置主题种子（_zip_theme_meta / _upgrade_builtins / seed_builtin_themes）已随换肤功能
# 于 2026-09-20 整体移除。动态壁纸改为参考 dsh-wallpaper-engine 契约实现的适配层。


MAX_LOG_ERR_REPEAT = 6   # 同一异常文本连续记录上限，防止后台线程刷爆日志
WATCH_INTERVAL = 6       # CDP 补注轮询（秒）
MAX_LOG_LINES = 300      # logs.json 内存条数上限
MAX_LOG_MSG = 2000       # 单条日志字符上限（防超长堆栈撑爆文件）
MAX_LOG_BYTES = 256 * 1024  # logs.json 文件大小上限，超过则轮转成 logs.old.json
_LOG_LOCK = threading.RLock()  # 多线程（HTTP + 多个守护线程）并发写日志的锁

# 凭据/密钥脱敏：任何日志落盘前先过一遍，保证 key 永不落日志（P2-3 审计要求）
_REDACT_RULES = [
    (re.compile(r'sk-[A-Za-z0-9][A-Za-z0-9\-_]{7,}'), 'sk-***'),
    (re.compile(r'(?i)(bearer\s+)[A-Za-z0-9\-._~+/=]{12,}'), r'\1***'),
    (re.compile(r'(?i)((?:api[_-]?key|access[_-]?token|secret|password|passwd)["\']?\s*[:=]\s*["\']?)[A-Za-z0-9\-._]{10,}'), r'\1***'),
    (re.compile(r'(?i)(DEEPSEEK_API_KEY\s*[=:]\s*)\S+'), r'\1***'),
]


def redact_secrets(text):
    """把日志文本里可能的密钥/令牌打码。"""
    if not text:
        return text
    s = str(text)
    for rx, repl in _REDACT_RULES:
        s = rx.sub(repl, s)
    return s


# ---------------- 日志（持久化） ----------------
def add_log(msg, typ='info', level=None, source='system'):
    """追加一条日志到 ~/.dsh-skins/logs.json（线程安全、脱敏、截断、按大小轮转）。
    typ  兼容旧分类（info/success/warn/error，决定既有渲染样式）
    level显示级别 debug/info/warn/error，缺省由 typ 推断
    source 来源分类（system/plugin/session/inject/panel/user-action…）"""
    msg = redact_secrets(msg)
    if len(msg) > MAX_LOG_MSG:
        msg = msg[:MAX_LOG_MSG] + '…（已截断）'
    if level is None:
        level = 'error' if typ == 'error' else ('warn' if typ == 'warn' else 'info')
    entry = {"time": time.strftime('%Y-%m-%d %H:%M:%S'),
             "ts": int(time.time() * 1000), "msg": msg, "type": typ,
             "level": level, "source": source or 'system'}
    with _LOG_LOCK:
        logs = []
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, encoding='utf-8') as f:
                    logs = json.load(f)
                if not isinstance(logs, list):
                    logs = []
            except (json.JSONDecodeError, OSError):
                logs = []
        if typ == 'error' and logs and logs[-1].get('msg') == msg and logs[-1].get('type') == 'error':
            count = logs[-1].get('repeat', 0)
            logs[-1] = dict(logs[-1])
            logs[-1]['repeat'] = count + 1
            if count >= MAX_LOG_ERR_REPEAT:
                return entry
        else:
            logs.append(entry)
        logs = logs[-MAX_LOG_LINES:]
        try:
            # 大小轮转：现有文件超阈值先归档（只保留一份 .old）
            if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > MAX_LOG_BYTES:
                try:
                    os.replace(LOG_FILE, LOG_FILE + '.old.json')
                except OSError:
                    pass
            with open(LOG_FILE + '.tmp', 'w', encoding='utf-8') as f:
                json.dump(logs, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(LOG_FILE + '.tmp', LOG_FILE)
        except OSError:
            pass
    return entry


def load_logs():
    with _LOG_LOCK:
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, encoding='utf-8') as f:
                    data = json.load(f)
                return data if isinstance(data, list) else []
            except (json.JSONDecodeError, OSError):
                return []
    return []


# ---------------- DSH 进程 ----------------
_dsh_running_cache = {'v': None, 'ts': 0.0}


def dsh_running(use_cache=True):
    """DeepSeek Harness 桌面版是否在运行。is_running() 会调 tasklist，故加 3s 短缓存。"""
    now = time.time()
    if use_cache and _dsh_running_cache['v'] is not None \
            and now - _dsh_running_cache['ts'] < 3.0:
        return _dsh_running_cache['v']
    v = dsh_env.is_running()
    _dsh_running_cache['v'] = v
    _dsh_running_cache['ts'] = now
    return v


def restart_dsh():
    """以「注入模式」启动/重启 DSH（调用方需已获用户确认）。返回 (ok, msg)。
    统一覆盖：未运行→直接启动；生产 exe 在跑但无调试端口→关闭后带
    --remote-debugging-port 重启；dev 模式→走 start:desktop 启动器。全程不改 DSH 文件。"""
    _dsh_running_cache['v'] = None
    try:
        ok, msg, action = dsh_env.relaunch_with_debug(force_restart=True)
        return ok, msg
    except Exception as e:
        return False, '以注入模式重启失败: {0}'.format(e)


def _decode(raw):
    """Windows 控制台输出多为 ANSI(中文=GBK)，按 mbcs 解码再退回 utf-8 容错"""
    if isinstance(raw, str):
        return raw
    try:
        return raw.decode('mbcs')
    except (LookupError, UnicodeDecodeError, AttributeError):
        try:
            return raw.decode('utf-8', 'replace')
        except Exception:
            return str(raw)


# ---------------- CDP 皮肤注入（可逆 · 不修改官方文件） ----------------
_cdp_watcher = None


def active_bundle():
    """当前应注入渲染进程的 (css, js)。

    css = 主题样式（背景图 data URI）+ 用户 CSS
    js  = 打标器 + window.DSHSkin 运行时 + 已启用用户脚本
    这是「换肤 + 增强」的统一出口；任何 CDP 注入都走这里。
    """
    try:
        css, js, _meta = ts.enhance_bundle()
        return css or '', js or ''
    except Exception as e:
        add_log('生成注入内容失败: {0}'.format(e), 'error')
        return '', ''


def active_inject_css():
    """兼容旧调用：仅取 CSS 部分"""
    return active_bundle()[0] or None


def cdp_auto_apply():
    """注入增强到正在运行的 DeepSeek Harness。

    按用户约定**绝不自动拉起 / 重启桌面应用**：CDP 未就绪时只返回状态
    （running-without-cdp / stopped），由用户手动启动或「以注入模式重启」。"""
    css, js = active_bundle()
    if not css and not js:
        return {'ok': False, 'err': 'no-active-enhancement'}
    if not cdp_skin.WEBSOCKET_AVAILABLE:
        return {'ok': False, 'err': 'websocket-not-installed'}
    if not cdp_skin.cdp_ready():
        if dsh_running():
            return {'ok': False, 'err': 'running-without-cdp'}
        return {'ok': False, 'err': 'stopped'}
    return cdp_skin.inject_bundle(css, js)


def cdp_status_payload():
    info = {'available': cdp_skin.WEBSOCKET_AVAILABLE, 'port': dsh_env.cdp_port(),
            'target': bool(cdp_skin.find_page_target())}
    if not info['available']:
        info['err'] = 'websocket-not-installed'
    # 精细运行模式：ready / no-debug（生产 exe 未开调试端口）/ stopped
    try:
        info['mode'] = dsh_env.cdp_mode()
    except Exception:
        info['mode'] = 'unknown'
    info['installed_exe'] = dsh_env.find_installed_exe()
    info['launcher_mode'] = dsh_env.launch_mode()
    dexe, dsrc = dsh_env.desktop_exe()
    info['desktop_exe'] = dexe
    info['desktop_exe_source'] = dsrc
    # 降级动作指引：无端口时需要「以注入模式重启」，未运行时「一键启动」，二者都走 restart-dsh
    info['need_relaunch'] = info['mode'] in ('no-debug', 'stopped')
    if info['target']:
        st = cdp_skin.page_state()
        if st.get('ok'):
            info['injected'] = st.get('injected')
            info['runtime'] = st.get('runtime')
            info['hash'] = st.get('hash')
            info['target_count'] = len(cdp_skin.find_page_targets())
    info['active'] = ts.load_config().get('active')
    info['enhance'] = enhance_engine.load_state().get('enabled')
    return info


def start_cdp_watcher():
    """守护线程：页面 reload / target 重建 / 主题或增强变更后自动补注
    （6s 轮询，只补注不重启；比对内容 hash 决定是否过期）"""
    global _cdp_watcher
    if _cdp_watcher is not None:
        return _cdp_watcher
    _cdp_watcher = cdp_skin.SkinWatcher(active_bundle, interval=WATCH_INTERVAL)
    _cdp_watcher.start()
    return _cdp_watcher


# ---------------- 选择器漂移自动探测（P0-5） ----------------
# DSH 升级后稳定 data 契约若变化，皮肤区域会静默失效。这里把「已知良好」的区域命中
# 矩阵存为基线，启动后周期性实测对比，发现「原本命中、现在落空」即主动告警并支持一键重测。
DRIFT_BASELINE = os.path.join(dsh_env.SKIN_ROOT, 'dom-baseline.json')
_drift_cache = {'v': None, 'ts': 0.0}
DRIFT_TTL = 30.0          # 探测结果缓存秒数（CDP 实测有成本）
_drift_alerted = set()    # 同一轮丢失只告警一次，避免刷屏


def load_drift_baseline():
    try:
        with open(DRIFT_BASELINE, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_drift_baseline(data):
    os.makedirs(dsh_env.SKIN_ROOT, exist_ok=True)
    tmp = DRIFT_BASELINE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, DRIFT_BASELINE)


def current_dom_fingerprint():
    """实测一次区域命中矩阵；无 CDP / 探测失败返回 None。"""
    if not (cdp_skin.WEBSOCKET_AVAILABLE and cdp_skin.cdp_ready()):
        return None
    res = cdp_skin.probe_regions()
    if not res.get('ok'):
        return None
    fp = {k: bool(v.get('matched')) for k, v in res.get('regions', {}).items()}
    return {'fingerprint': fp, 'landmarks': res.get('landmarks', {}),
            'dead': res.get('dead', []), 'dsh_version': dsh_env.harness_version(),
            'marker_version': marker_engine.MARKER_VERSION, 'ts': time.time()}


def evaluate_drift(force=False):
    """对比当前指纹与基线，返回漂移结论。force=True 跳过缓存重新实测。"""
    now = time.time()
    if not force and _drift_cache['v'] is not None and now - _drift_cache['ts'] < DRIFT_TTL:
        return _drift_cache['v']
    cur = current_dom_fingerprint()
    base = load_drift_baseline()
    if cur is None:
        out = {'available': False}
    elif not base:
        out = {'available': True, 'has_baseline': False, 'drifted': False, 'current': cur}
    else:
        bf, cf = base.get('fingerprint', {}), cur['fingerprint']
        lost = [k for k, alive in cf.items() if not alive and bf.get(k) is True]
        ver_changed = bool(base.get('dsh_version') and cur.get('dsh_version')
                           and base['dsh_version'] != cur['dsh_version'])
        out = {'available': True, 'has_baseline': True, 'drifted': bool(lost) or ver_changed,
               'lost': lost, 'version_changed': ver_changed,
               'baseline_version': base.get('dsh_version'),
               'current_version': cur.get('dsh_version'),
               'dead': cur.get('dead', []), 'current': cur}
        if out['drifted']:
            key = ','.join(lost) + '|' + str(out['current_version'])
            if key not in _drift_alerted:
                _drift_alerted.add(key)
                why = ('DSH 版本变化 %s→%s' % (out['baseline_version'], out['current_version'])
                       if ver_changed else '')
                add_log('检测到界面结构可能已变化（%s 区域落空:%s），建议在诊断页一键重测/重建主题'
                        % (why or '', ', '.join(lost) or '无'), 'warn')
    _drift_cache['v'] = out
    _drift_cache['ts'] = now
    return out


def _drift_brief():
    """状态接口里的轻量漂移摘要：优先用缓存，不触发新的 CDP 实测。"""
    cached = _drift_cache['v']
    if cached is None:
        return {'available': bool(cdp_skin.cdp_ready()), 'checked': False}
    return {'available': cached.get('available', False), 'checked': True,
            'has_baseline': cached.get('has_baseline', False),
            'drifted': cached.get('drifted', False), 'lost': cached.get('lost', []),
            'version_changed': cached.get('version_changed', False)}


def drift_watch_loop(first_delay=12.0, interval=120.0):
    """后台巡检：DSH 起来后周期性比对基线，漂移只告警不自动改东西。"""
    def _loop():
        time.sleep(first_delay)
        while True:
            try:
                if cdp_skin.cdp_ready():
                    evaluate_drift(force=True)
            except Exception:
                pass
            time.sleep(interval)
    threading.Thread(target=_loop, daemon=True).start()


# ---------------- 页面内增强错误回收（P1-4） ----------------
# 增强模块/用户脚本运行在渲染进程，异常只存在于页面里，server 侧日志看不到。
# 这里通过 CDP 周期回收 window.DSHSkin.logs 与全局错误，error 级落到工具日志。
_err_watermark = {'t': 0}


def page_logs_since(since=0):
    """拉取 since(ms 时间戳) 之后的页面日志（前端轮询用，自带水位）。"""
    try:
        return cdp_skin.collect_page_logs(port=dsh_env.cdp_port(), since=since)
    except Exception:
        return []


def page_error_watch_loop(first_delay=8.0, interval=8.0):
    """后台把页面新出现的 error 级日志转进工具日志（info 级不转，避免刷屏）。"""
    def _loop():
        time.sleep(first_delay)
        while True:
            try:
                if cdp_skin.cdp_ready():
                    logs = cdp_skin.collect_page_logs(
                        port=dsh_env.cdp_port(), since=_err_watermark['t'])
                    for e in logs:
                        _err_watermark['t'] = max(_err_watermark['t'], e.get('t', 0))
                        if e.get('lv') == 'error':
                            add_log('页面错误[%s]: %s' % (e.get('mod') or 'page', e.get('m', '')), 'warn')
            except Exception:
                pass
            time.sleep(interval)
    threading.Thread(target=_loop, daemon=True, name='dshskin-page-err').start()


# ---------------- 预览快照保鲜（P1-8） ----------------
ASSETS_DIR = os.path.join(ROOT, 'assets')
SNAP_MANIFEST = os.path.join(ASSETS_DIR, 'snap-manifest.json')
SNAP_FILES = ('snap-home.html', 'snap-conv.html', 'snap-settings.html',
              'dsh-vendor.css', 'dsh-index.css', 'dsh-inline.css')


def snapshots_status():
    """快照是否随 DSH 升级而漂移：对比抓取时记录的版本与当前版本/文件完整性。"""
    man = None
    if os.path.isfile(SNAP_MANIFEST):
        try:
            with open(SNAP_MANIFEST, encoding='utf-8') as f:
                man = json.load(f)
        except (OSError, json.JSONDecodeError):
            man = None
    missing = [fn for fn in SNAP_FILES if not os.path.isfile(os.path.join(ASSETS_DIR, fn))]
    cur_hv = ''
    try:
        cur_hv = dsh_env.harness_version() or ''
    except Exception:
        cur_hv = ''
    cur_tv = marker_engine.MARKER_VERSION
    reasons = []
    if man is None:
        reasons.append('尚未在本机重抓（使用随包内置快照）')
    else:
        if man.get('harness_version') and cur_hv and man['harness_version'] != cur_hv:
            reasons.append('DSH 版本变化：{0} → {1}'.format(man['harness_version'], cur_hv))
        if int(man.get('marker_version', man.get('template_version', 0))) < cur_tv:
            reasons.append('界面打标器已升级到 v{0}'.format(cur_tv))
    if missing:
        reasons.append('缺少快照文件：{0}'.format(', '.join(missing)))
    return {
        'available': os.path.isdir(ASSETS_DIR),
        'manifest': man,
        'current_harness_version': cur_hv,
        'current_template_version': cur_tv,
        'missing': missing,
        'stale': bool(reasons),
        'reasons': reasons,
        'cdp_ready': bool(cdp_skin.cdp_ready()),
    }


def recapture_snapshots(port=None):
    """调用 tools/capture_dsh_snapshots.py 从运行中的 DSH 重新抓取快照。"""
    script = os.path.join(ROOT, 'tools', 'capture_dsh_snapshots.py')
    if not os.path.isfile(script):
        return {'ok': False, 'err': '抓取脚本不存在: {0}'.format(script)}
    if not cdp_skin.cdp_ready():
        return {'ok': False, 'err': 'DSH 未以调试模式运行（端口未就绪），请先启动并注入模式重启 DSH'}
    # 解释器：源码运行用当前 python；冻结 exe 时尝试 py 启动器/系统 python
    py = sys.executable
    if getattr(sys, 'frozen', False) or not py.lower().endswith('python.exe'):
        import shutil as _sh
        py = _sh.which('py') or _sh.which('python') or ''
        if not py:
            return {'ok': False, 'err': '一键重抓需要本机 Python 3.11+（打包版不含解释器），可在源码目录手动运行抓取脚本'}
    port = port or dsh_env.cdp_port()
    # 打包版 exe 无父控制台：显式 CREATE_NO_WINDOW 防止 py 启动器弹黑窗口
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
    try:
        proc = subprocess.run([py, script, '--port', str(port)],
                              capture_output=True, text=True, timeout=120, cwd=ROOT,
                              creationflags=flags)
    except subprocess.TimeoutExpired:
        return {'ok': False, 'err': '抓取超时（120s）'}
    except Exception as e:
        return {'ok': False, 'err': '抓取失败: {0}'.format(e)}
    out = (proc.stdout or '') + (proc.stderr or '')
    ok = proc.returncode == 0 and os.path.isfile(SNAP_MANIFEST)
    return {'ok': ok, 'err': '' if ok else (out[-800:] or '抓取未产出 manifest'),
            'output': out[-2000:], 'status': snapshots_status()}


# ---------------- 源码热重启（防旧代码覆盖新注入） ----------------
# 工具自身 .py 更新后，正在运行的后端仍持有旧模板/旧引擎，SkinWatcher 会把
# 旧 CSS 反复注回页面（「改了模板却不生效」的根源）。这里监视本目录源码
# mtime，变化稳定后自动重启进程加载新代码（exe / 非源码目录运行时跳过）。
_SOURCE_FILES = ('server.py', 'dsh-skin.py', 'dsh_env.py', 'marker_engine.py',
                 'cdp_skin.py', 'enhance_engine.py')


def _source_stamp():
    stamp = []
    for name in _SOURCE_FILES:
        try:
            stamp.append(os.path.getmtime(os.path.join(ROOT, name)))
        except OSError:
            stamp.append(0.0)
    return tuple(stamp)


def _restart_self():
    """用同一命令行重启本进程（固定追加 --no-open 与 --wait 1.0：给旧进程
    让出端口留时间；浏览器里面板已开着，不需要再开标签页）。
    Windows 上 os.execv 行不可靠（静默失败），改用子进程继承控制台后本进程退出。"""
    argv = [sys.executable, os.path.join(ROOT, 'server.py'),
            '--wait', '1.0', '--no-open']
    skip = False
    for a in sys.argv[1:]:
        if skip:            # --wait 的值一并跳过（否则孙进程会多出裸值参数）
            skip = False
            continue
        if a == '--no-open':
            continue
        if a == '--wait':
            skip = True
            continue
        if a.startswith('--wait='):
            continue
        argv.append(a)
    print('[i] 工具源码已更新，面板后端自动重启加载新代码...')
    add_log('工具源码已更新，面板后端自动重启（加载新模板/引擎）', 'info')
    try:
        sys.stdout.flush()
    except Exception:
        pass
    if getattr(sys, 'frozen', False):
        return   # exe 场景交由用户/启动器重启，不自动拉起
    try:
        import subprocess
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
        subprocess.Popen(argv, close_fds=False, creationflags=flags)
    except Exception as e:
        print('[!] 自动重启失败（继续以旧代码运行）:', e)
        return
    os._exit(0)


def start_source_watcher(grace_s=12, interval_s=8):
    """源码 mtime 守护线程：变化连续观察到两轮才重启（容忍编辑器写盘抖动）。"""
    if getattr(sys, 'frozen', False):
        return
    if not os.path.isfile(os.path.join(ROOT, 'server.py')):
        return

    def loop():
        time.sleep(grace_s)
        base = _source_stamp()
        changed_once = False
        while True:
            time.sleep(interval_s)
            try:
                cur = _source_stamp()
            except Exception:
                continue
            if cur == base:
                changed_once = False
                continue
            if not changed_once:
                changed_once = True
                continue
            try:
                _restart_self()
            except Exception as e:
                print('[!] 自动重启失败（继续以旧代码运行）:', e)
                changed_once = False

    threading.Thread(target=loop, daemon=True, name='dshskin-srcwatch').start()


def repair_injection():
    """注入自检与修复（DSH 无文件通道，只做 CDP 补注）。"""
    fixed = []
    cfg = ts.load_config()
    if enhance_engine.load_state().get('enabled'):
        if not cdp_skin.WEBSOCKET_AVAILABLE:
            fixed.append('CDP 不可用（缺 websocket-client）')
        elif not cdp_skin.cdp_ready():
            fixed.append('桌面版未运行或无调试端口，请先启动')
        else:
            css, js = active_bundle()
            if css or js:
                r = cdp_skin.inject_bundle(css, js)
                fixed.append('CDP 补注完成' if r.get('ok') else 'CDP 补注失败: ' + str(r.get('err')))
    return fixed


# 自愈链路（auto_heal_once / heal_loop / cdp_auto_launch）已按用户要求整体移除：
# DSH++ 永不自动拉起 / 重启桌面应用。CDP 未就绪时一律只返回状态，由用户手动
# 「启动 DSH（调试模式）」/「以注入模式重启」决定。被动巡检线程（drift /
# page-error / cdp-watcher）只读 CDP 状态与日志，不含任何拉起逻辑。


# ---------------- 各类 payload ----------------
def api_ok(data=None, msg='ok'):
    return {"ok": True, "msg": msg, **(data or {})}


def api_err(msg):
    return {"ok": False, "msg": str(msg)}


def run_ts(fn, *a, **kw):
    """调用 dsh-skin 核心函数，捕获 SystemExit（脚本用 raise SystemExit 报错）。"""
    try:
        fn(*a, **kw)
        invalidate_status()
        return api_ok()
    except SystemExit as e:
        return api_err(e.code if e.code else e)


def themes_payload():
    """已弃用：换肤功能已于 2026-09-20 移除。

    保留空壳以免旧前端 / 旧书签调用报 500；始终返回空主题列表，
    并带上 legacy_removed 标记，前端据此提示「换肤已由动态壁纸取代」。
    """
    return api_ok({"active": None, "themes": [], "channel": 'cdp',
                   "legacy_removed": True,
                   "note": "换肤已移除，动态背景请用 DSH 插件 dsh-plugin-wallpaper-engine"})


# ---------------- 动态壁纸（参考 dsh-wallpaper-engine 契约实现对接） ----------------
def wallpaper_payload(fetch=True):
    """插件状态 + 壁纸清单（借 CDP 同源拉取）+ 可调参数。"""
    try:
        return api_ok(wallpaper_engine.inventory_payload(fetch=fetch))
    except Exception as e:
        return api_err('读取动态壁纸状态失败: {0}'.format(e))


def wallpaper_set(body):
    """切换壁纸 / 调参。

    写路径（2026-09-20 改）：**优先 PUT 插件自己的 /wallpaper-engine/settings 路由**，
    复用插件的 enqueueConfigWrite 串行队列 + sanitizeSettings 校验；
    路由不可达（DSH 未开调试口 / 插件未加载）时才降级直接改
    ~/.dsh-wallpaper-engine/config.json。

    body:
      {id?: "壁纸 id", settings?: {scrim:0.3, ...}}
    插件客户端轮询 settings 后自动生效（无需重启 DSH）。
    """
    patch = {}
    sid = body.get('id')
    if isinstance(sid, str) and sid:
        patch['id'] = sid
    settings = body.get('settings')
    if isinstance(settings, dict):
        patch.update(settings)
    if not patch:
        return api_err('缺少 id 或 settings')
    try:
        saved, changed, res = wallpaper_engine.save_settings(patch)
    except Exception as e:
        return api_err('写入壁纸设置失败: {0}'.format(e))
    if not changed:
        return api_err('没有可写入的合法参数')
    if 'id' in changed:
        add_log('已切换动态壁纸 → {0}'.format(saved.get('id')), 'success')
    else:
        add_log('已更新动态壁纸参数: {0}'.format(', '.join(changed)), 'success')
    via = res.get('via')
    if via == 'file':
        # 降级写入成功但绕过了插件的串行队列 —— 明确告知，避免用户以为一切正常
        add_log('壁纸参数走文件降级写入（插件路由不可达：{0}）'.format(res.get('err')), 'warn')
    return api_ok({"settings": saved, "changed": changed,
                   "via": via, "route_err": res.get('err')})


# ---------------- v2：脚本市场 / 会话管理 / 供应商配置 ----------------
MARKET_DIR = os.path.join(ROOT, 'market')
_FM_RE = re.compile(r'//\s*@(\w+)\s*:\s*(.*)')


def _market_meta(path):
    """解析脚本头部 front-matter（// @key: value），失败返回 None"""
    try:
        with io.open(path, encoding='utf-8', errors='replace') as f:
            head = f.read(2000)
    except OSError:
        return None
    meta = {}
    for line in head.splitlines()[:20]:
        m = _FM_RE.match(line.strip())
        if m:
            meta[m.group(1).lower()] = m.group(2).strip()
    if not meta.get('name'):
        return None
    return meta


def _market_entry(fn, installed):
    path = os.path.join(MARKET_DIR, fn)
    try:
        with io.open(path, encoding='utf-8', errors='replace') as f:
            content = f.read()
    except OSError:
        return None
    meta = _market_meta(path) or {}
    name = meta.get('name') or fn[:-3]
    analysis = enhance_engine.analyze_script(content)
    return {
        "name": name,
        "title": meta.get('title', name),
        "description": meta.get('description', ''),
        "version": meta.get('version', '1.0.0'),
        "author": meta.get('author', 'DSHSkin'),
        "icon": meta.get('icon', '✦'),
        "source": analysis.get('source', ''),
        "permissions": analysis.get('permissions', ['dom']),
        "uses": analysis.get('uses', []),
        "undeclared": analysis.get('undeclared', []),
        "risk": analysis.get('risk', 'minimal'),
        "file": fn,
        "installed": name in installed,
    }


def market_payload():
    items = []
    installed = {it['name'][:-3] for it in enhance_engine.list_files('.js')
                 if it['name'].endswith('.js')}
    if os.path.isdir(MARKET_DIR):
        for fn in sorted(os.listdir(MARKET_DIR)):
            if not fn.endswith('.js'):
                continue
            entry = _market_entry(fn, installed)
            if entry:
                items.append(entry)
    return api_ok({"items": items, "dir": MARKET_DIR})


def market_inspect(name):
    """安装前检查：返回脚本元信息、权限、实际能力、风险与代码预览（不写盘）。"""
    if not os.path.isdir(MARKET_DIR):
        return None
    for fn in sorted(os.listdir(MARKET_DIR)):
        if not fn.endswith('.js'):
            continue
        meta = _market_meta(os.path.join(MARKET_DIR, fn)) or {}
        if (meta.get('name') or fn[:-3]) != name:
            continue
        with io.open(os.path.join(MARKET_DIR, fn), encoding='utf-8') as f:
            content = f.read()
        analysis = enhance_engine.analyze_script(content)
        analysis['file'] = fn
        analysis['preview'] = content[:1600]   # 前 1600 字符供用户审阅
        return analysis
    return None


def market_install(name, confirmed=False):
    """把市场脚本安装到用户脚本目录。confirmed=False 时只返回风险信息要求二次确认。"""
    if not os.path.isdir(MARKET_DIR):
        return None
    for fn in sorted(os.listdir(MARKET_DIR)):
        if not fn.endswith('.js'):
            continue
        meta = _market_meta(os.path.join(MARKET_DIR, fn)) or {}
        if (meta.get('name') or fn[:-3]) != name:
            continue
        with io.open(os.path.join(MARKET_DIR, fn), encoding='utf-8') as f:
            content = f.read()
        analysis = enhance_engine.analyze_script(content)
        if not confirmed:
            return {'need_confirm': True, 'analysis': analysis}
        target = re.sub(r'[^A-Za-z0-9._-]', '-', name) + '.js'
        enhance_engine.write_file(target, content)
        st = enhance_engine.load_state()
        st.setdefault('scripts', {})[target] = True
        # 来源/权限/风险留档，便于审计「这个脚本哪来的、能干什么」
        st.setdefault('scripts_meta', {})[target] = {
            'source': analysis.get('source') or 'market:' + fn,
            'permissions': analysis.get('permissions'),
            'uses': analysis.get('uses'),
            'risk': analysis.get('risk'),
            'version': analysis.get('version'),
            'installedAt': int(time.time()),
        }
        enhance_engine.save_state(st)
        return {'installed': target, 'analysis': analysis}
    return None


def sessions_payload():
    """列出本机 DSH 会话（只读）。

    通道：桌面壳把会话库放在 host 侧数据目录（session-controller 持久化）。
    这里扫描候选目录下的会话文件，按 mtime 倒序；解析失败给降级提示。
    """
    try:
        import session_store
        return api_ok({"sessions": session_store.list_sessions()})
    except Exception as e:
        return api_err('会话通道不可用: {0}'.format(e))


def providers_payload():
    """读取 DSH 凭证状态（打码展示，不回传完整密钥）。"""
    try:
        import session_store
        return api_ok(session_store.providers_info())
    except Exception as e:
        return api_err('凭证通道不可用: {0}'.format(e))


def _channel(cfg):
    return 'cdp'   # DSH 唯一注入通道


_STATUS_CACHE = {'key': None, 'value': None, 'ts': 0.0}
_STATUS_TTL = 3.0       # 秒：面板常在一次操作后连打多次 status，短 TTL 合并掉重复诊断


def status_payload(use_cache=True):
    now = time.time()
    if use_cache and _STATUS_CACHE['key'] is not None \
            and now - _STATUS_CACHE['ts'] < _STATUS_TTL:
        return _STATUS_CACHE['value']
    value = _status_payload_uncached()
    _STATUS_CACHE['key'] = True
    _STATUS_CACHE['value'] = value
    _STATUS_CACHE['ts'] = now
    return value


def invalidate_status():
    _STATUS_CACHE['key'] = None
    _STATUS_CACHE['ts'] = 0.0
    try:
        cdp_skin.invalidate_probe_cache()
    except Exception:
        pass
    try:
        dsh_env.invalidate_cdp_cache()
    except Exception:
        pass


# ---------------- Token 用量聚合缓存（内置版 token-usage 数据源） ----------------
# 全量聚合随会话量线性增长，用 TTL 缓存 + 互斥锁避免面板轮询/并发请求触发重复全量扫描。
# 预热线程（见 serve()）在启动时后台算一次并写入缓存，用户点进「令牌用量」首屏即有数据。
_TOKEN_USAGE_LOCK = threading.Lock()
_TOKEN_USAGE_CACHE = {'key': None, 'value': None, 'ts': 0.0}
_TOKEN_USAGE_TTL = 15.0      # 秒：与面板轮询间隔对齐
_TOKEN_USAGE_WARMING = False  # 预热尚未完成标志（面板据此显示「统计中…」）


def _token_usage_payload(blocking=None):
    """GET /api/token-usage 数据：缓存命中直接返回；冷缓存时默认不阻塞。

    空结果（无任何用量数据）不落地缓存：视为启动初期语料未就绪，
    保持 warming=True 让面板显示「统计中…」并靠轮询重算，
    避免预热/首查时刻将 0 值写入缓存导致误报「暂无 Token 用量数据」。

    阻塞策略（2026-09-20 修）：
      全量扫描随会话量线性增长（本机实测 ~1.6s，语料更大时更久），而启动预热
      线程已在后台算同一件事。若请求方在冷缓存时也同步扫描，就会「重复算一遍」，
      并把首屏卡在这 1 个请求上。故冷缓存且预热仍在跑时立即返回 warming 占位，
      由面板轮询（15s）在预热完成后自然拿到数据。
      blocking=True 时保留旧的同步语义（供 CLI / 测试显式索取最终值）。
    """
    import session_store
    now = time.time()
    with _TOKEN_USAGE_LOCK:
        fresh = (_TOKEN_USAGE_CACHE['key'] is not None
                 and now - _TOKEN_USAGE_CACHE['ts'] < _TOKEN_USAGE_TTL)
        if fresh:
            value = dict(_TOKEN_USAGE_CACHE['value'])
        else:
            # 冷缓存：预热进行中则先给占位，避免与预热线程重复全量扫描、拖慢首屏
            if blocking is None:
                blocking = not _TOKEN_USAGE_WARMING
            if not blocking:
                return {'warming': True, 'pending': True,
                        'computedAt': int(now * 1000),
                        'totalTokens': 0, 'turns': 0, 'days': {}, 'hours': {},
                        'models': [], 'dayModels': {}, 'modelTokens': {},
                        'hoursToday': {}, 'firstUsedAt': None, 'lastUsedAt': None,
                        'maxTurnMs': 0}
            try:
                value = session_store.aggregate_token_usage()
            except Exception as e:
                # 无 DSH 会话目录（未安装 / 尚未产生会话 / home 未解析）时
                # 聚合器会抛 RuntimeError。这里必须兜住 —— 否则 /api/token-usage
                # 会对调用方返回 500，且阻塞路径（CLI / 测试）直接崩。
                # 降级为「零用量 + warming」，让面板显示「统计中…」而非报错。
                add_log('Token 用量聚合失败，降级为零值：{0}'.format(e), 'warn')
                return {'warming': True, 'degraded': True, 'pending': False,
                        'computedAt': int(now * 1000),
                        'totalTokens': 0, 'turns': 0, 'days': {}, 'hours': {},
                        'models': [], 'dayModels': {}, 'modelTokens': {},
                        'hoursToday': {}, 'firstUsedAt': None, 'lastUsedAt': None,
                        'maxTurnMs': 0}
            value['computedAt'] = int(now * 1000)
            if value.get('totalTokens') or (value.get('days') or {}):
                _TOKEN_USAGE_CACHE['key'] = True
                _TOKEN_USAGE_CACHE['value'] = value
                _TOKEN_USAGE_CACHE['ts'] = now
        # 无数据且非预热线程主导时仍算「统计中」，由前端轮询持续重算
        if not value.get('totalTokens') and not (value.get('days') or {}):
            value['warming'] = True
        else:
            value['warming'] = _TOKEN_USAGE_WARMING
        return value


def _token_usage_warmup():
    """启动预热：后台聚合一次写入缓存，成败经日志记录，绝不阻塞启动/其他请求。

    空结果（语料未就绪）不写缓存并保持 warming=True，等前端轮询/后续请求
    重算到真实数据后再生效，避免首屏误报「暂无数据」。
    """
    try:
        import session_store
        global _TOKEN_USAGE_WARMING   # 无此声明会在函数内建同名局部变量，模块级标志永不更新
        with _TOKEN_USAGE_LOCK:
            _TOKEN_USAGE_WARMING = True
            value = session_store.aggregate_token_usage()
            if value.get('totalTokens') or (value.get('days') or {}):
                _TOKEN_USAGE_CACHE['key'] = True
                _TOKEN_USAGE_CACHE['value'] = value
                _TOKEN_USAGE_CACHE['ts'] = time.time()
                _TOKEN_USAGE_WARMING = False
                add_log('Token 用量预热完成（回合 {0}，Token {1}）'.format(
                    value.get('turns', 0), value.get('totalTokens', 0)), 'info', source='system')
            else:
                # 暂无数据：保持 warming，通知前端继续轮询，不写空缓存
                add_log('Token 用量预热：会话语料暂无用量数据，保持统计中等待重算', 'info', source='system')
    except Exception as e:
        add_log('Token 用量预热失败（不影响启动）: {0}'.format(e), 'warn', source='system')
        _TOKEN_USAGE_WARMING = False


def _status_payload_uncached():
    det = dsh_env.detect_report()
    # 选择器健康：有 CDP target 时实测；否则为 None（未知）。
    # 换肤移除后，选择器只服务于增强注入（打标器 + 增强脚本定位），
    # 增强关闭时视为健康（没有需要生效的内容，打标器本就不运行）。
    selectors = []
    enh_on = enhance_engine.load_state().get('enabled')
    if enh_on:
        if cdp_skin.WEBSOCKET_AVAILABLE and cdp_skin.cdp_ready():
            res = cdp_skin.probe_regions()
            if res.get('ok'):
                counts = res.get('counts', {})
                for key, reg in marker_engine.resolve_regions().items():
                    sels = [s.strip() for s in reg['selector'].split(',') if s.strip()]
                    selectors.append({"key": key, "label": reg["label"],
                                      "alive": any(counts.get(s, 0) > 0 for s in sels),
                                      "overridden": bool(reg.get("overridden"))})
    selector_healthy = all(s['alive'] for s in selectors) if selectors else True
    return api_ok({
        "app": "dsh-skin",
        "version": ts.__version__,
        "product": det.get('product') or 'DeepSeek Harness',
        "harness_root": det['harness_root'],
        "skin_root": ts.SKIN_ROOT,
        "cdp_port": dsh_env.cdp_port(),
        "cdp_ready": bool(cdp_skin.cdp_ready()),
        "healthy": selector_healthy is not False,
        "selector_healthy": selector_healthy,
        "selectors": selectors,
        "config_exists": os.path.exists(ts.CONFIG),
        "dsh_running": dsh_running(),
        "launcher": det['launcher'],
        "launcher_mode": det['launcher_mode'],
        "desktop_exe": det['desktop_exe'],
        "desktop_exe_source": det['desktop_exe_source'],
        "dsh_home": det['dsh_home'],
        "dsh_home_source": det['dsh_home_source'],
        "channel": 'cdp',
        "deps": {"websocket": cdp_skin.WEBSOCKET_AVAILABLE},
        "cdp": cdp_status_payload(),
        "drift": _drift_brief(),
        "tool_version": ts.__version__,
        "enhance": enhance_engine.summary(),
    })


class Handler(BaseHTTPRequestHandler):
    server_version = 'DSH++/1.0'

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers',
                         'Content-Type, X-Filename, X-DSHSkin-Token')

    def _origin_ok(self):
        """本地防 CSRF：状态变更请求若带 Origin/Referer，来源必须是本机。"""
        for h in ('Origin', 'Referer'):
            v = self.headers.get(h)
            if not v:
                continue
            try:
                host = urlparse(v).hostname or ''
            except Exception:
                return False
            return host in ('127.0.0.1', 'localhost', '::1')
        return True

    def _presented_token(self):
        """从请求头或 query 取出调用方携带的 token。"""
        tok = self.headers.get('X-DSHSkin-Token') or self.headers.get('X-Dshskin-Token')
        if not tok:
            try:
                tok = (parse_qs(urlparse(self.path).query).get('token') or [None])[0]
            except Exception:
                tok = None
        return tok or ''

    def _token_ok(self):
        return secrets.compare_digest(self._presented_token(), SERVER_TOKEN)

    def _deny(self):
        return self._json(api_err('未授权：缺少有效本机访问令牌'), 401)

    def _safe_write(self, data):
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self._cors()
        self.end_headers()
        self._safe_write(body)

    def _bin(self, body, ctype, fname=None):
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        if fname:
            self.send_header('Content-Disposition', 'attachment; filename="{0}"'.format(fname))
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self._cors()
        self.end_headers()
        self._safe_write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    # ---------------- 壁纸媒体供给 ----------------
    def _wallpaper_asset(self, path):
        """`GET /api/wallpaper/asset/<kind>/<id>` —— 供壁纸媒体字节。

        为什么由本工具供给而不是走插件路由：插件把媒体挂在它自己的
        `/wallpaper-engine/media/<token>` 上，token 只存在于插件进程内存，
        且那条路由受 DSH 的 Host/Origin 栅栏保护，外部进程无法直连。
        本工具的后端本来就有文件系统权限，直接读文件返回即可。

        **免 token**：`<img src>` / `<video src>` 无法携带自定义请求头，
        若要求 `X-DSHSkin-Token`，所有缩略图与视频都会裂图。

        安全边界：`id` 必须过 `we_scanner` 的白名单正则，且路径只从**已扫描
        到的壁纸路径表**中取 —— 调用方无法传入任意路径，免疫路径穿越。
        暴露面与本机同用户的文件读取权限相同，未新增越权能力。
        """
        rest = path[len('/api/wallpaper/asset/'):]
        parts = [seg for seg in rest.split('/') if seg]
        if len(parts) != 2:
            return self._json(api_err('asset 路径应形如 /api/wallpaper/asset/<kind>/<id>'), 404)
        kind, wid = parts
        if kind not in ('media', 'preview'):
            return self._json(api_err('kind 只能是 media 或 preview'), 404)
        try:
            import we_scanner
            source, ctype = we_scanner.media_file(wid, kind)
        except Exception as exc:
            return self._json(api_err('解析壁纸媒体失败：{0}'.format(exc)), 500)
        if not source:
            label = '预览图' if kind == 'preview' else '媒体文件'
            return self._json(api_err('未找到壁纸 {0} 的{1}'.format(wid, label)), 404)
        self._send_local_file(source, ctype)

    def _send_local_file(self, source, ctype):
        """流式发送本地文件，支持单段 Range（视频拖动进度需要 206）。"""
        import email.utils
        try:
            info = os.stat(source)
        except OSError:
            return self._json(api_err('媒体文件已不可读'), 404)

        etag = '"{0:x}-{1:x}"'.format(int(info.st_mtime), info.st_size)
        if self.headers.get('If-None-Match') == etag:
            self.send_response(304)
            self.send_header('ETag', etag)
            self._cors()
            self.end_headers()
            return

        start, end, status = 0, info.st_size - 1, 200
        rng = self.headers.get('Range')
        if rng:
            m = re.match(r'^bytes=(\d*)-(\d*)$', rng.strip())
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), info.st_size - 1)
                else:
                    start = max(0, info.st_size - int(m.group(2)))
                if start >= info.st_size or start > end:
                    self.send_response(416)
                    self.send_header('Content-Range', 'bytes */{0}'.format(info.st_size))
                    self._cors()
                    self.end_headers()
                    return
                status = 206

        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('ETag', etag)
        self.send_header('Last-Modified', email.utils.formatdate(info.st_mtime, usegmt=True))
        # 壁纸媒体按 mtime 失效即可：允许浏览器缓存，避免每次重传几十 MB 视频
        self.send_header('Cache-Control', 'private, max-age=300')
        if status == 206:
            self.send_header('Content-Range', 'bytes {0}-{1}/{2}'.format(start, end, info.st_size))
        self._cors()
        self.end_headers()

        remaining = end - start + 1
        try:
            with open(source, 'rb') as handle:
                handle.seek(start)
                while remaining > 0:
                    chunk = handle.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            pass

    def _head_probe(self):
        path = urlparse(self.path).path
        status, ctype, length = 200, 'text/html; charset=utf-8', 0

        # 换肤已移除：旧前端 HEAD 探针不应误落到静态托管
        if path.startswith(('/api/theme-image/', '/api/theme-params/',
                            '/api/preview-css/', '/api/export/')):
            status = 410
        elif path == '/favicon.ico':
            status = 204
        else:
            rel = path.lstrip('/') or 'panel.html'
            fp = os.path.normpath(os.path.join(ROOT, rel))
            if not fp.startswith(ROOT) or not os.path.isfile(fp):
                status = 404
            else:
                ctype = 'text/html; charset=utf-8' if fp.endswith('.html') else 'application/octet-stream'
                length = os.path.getsize(fp)

        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(length))
        self.send_header('Cache-Control', 'no-store')
        self._cors()
        self.end_headers()

    def do_HEAD(self):
        try:
            self._head_probe()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            pass

    # ---------------- GET ----------------
    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        qs = parse_qs(u.query)
        if path == '/api/ping':
            return self._json(api_ok({"app": "dsh-skin", "version": ts.__version__,
                                      "pid": os.getpid()}))
        # 桌面应用第二实例：唤起已有窗口（带 token，防本机无令牌进程触发）
        if path == '/api/desktop-activate':
            for h in list(DESKTOP_ACTIVATE_HOOKS):
                try:
                    h()
                except Exception:
                    pass
            return self._json(api_ok({"activated": True}))
        # 敏感读取：必须带正确 token（浏览器面板由托管页注入），拦截本机无令牌进程
        if path in SENSITIVE_GET and not self._token_ok():
            return self._deny()
        if path == '/api/themes':
            return self._json(themes_payload())
        if path == '/api/wallpaper':
            fetch = (qs.get('fetch') or ['1'])[0] not in ('0', 'false', 'no')
            return self._json(wallpaper_payload(fetch=fetch))
        # 壁纸媒体字节（预览图 / 视频）。**必须在敏感路径检查之外**：
        # <img src> / <video src> 无法携带自定义请求头，加 token 会整片裂图。
        if path.startswith('/api/wallpaper/asset/'):
            return self._wallpaper_asset(path)
        if path == '/api/status':
            return self._json(status_payload())
        if path == '/api/detect':
            return self._json(api_ok(dsh_env.detect_report()))
        if path == '/api/deps':
            return self._json(api_ok({"websocket": cdp_skin.WEBSOCKET_AVAILABLE,
                                      "version": getattr(getattr(cdp_skin, 'websocket', None),
                                                         '__version__', None),
                                      "hint": "python dsh-skin.py deps --install"}))
        if path == '/api/doctor':
            return self._json(api_ok({"report": ts.doctor_report()}))
        if path == '/api/cdp-status':
            return self._json(api_ok(cdp_status_payload()))
        if path == '/api/drift':
            # force=1 立即重新实测，否则用 30s 缓存
            return self._json(api_ok(evaluate_drift(force=(qs.get('force', ['0'])[0] == '1'))))
        if path == '/api/page-logs':
            try:
                since = int(qs.get('since', ['0'])[0])
            except ValueError:
                since = 0
            logs = page_logs_since(since)
            return self._json(api_ok({'logs': logs,
                                      'max_t': max([x.get('t', 0) for x in logs] + [since])}))
        if path == '/api/snapshots-status':
            return self._json(api_ok(snapshots_status()))
        if path == '/api/autostart':
            return self._json(api_ok({'enabled': tray.is_autostart(),
                                      'command': tray.autostart_command()}))
        if path == '/api/update-check':
            return self._json(api_ok(updater.check_for_update(ts.__version__)))
        if path == '/api/selectors':
            regions = marker_engine.resolve_regions()
            return self._json(api_ok({
                "overrides": marker_engine.load_overrides(),
                "file": marker_engine.OVERRIDES_FILE,
                "regions": [{"key": k, "label": v["label"], "selector": v["selector"],
                             "overridden": bool(v.get("overridden"))} for k, v in regions.items()],
            }))
        if path == '/api/logs':
            return self._json(api_ok({"logs": load_logs()}))
        if path == '/api/market':
            return self._json(market_payload())
        if path == '/api/sessions':
            return self._json(sessions_payload())
        if path == '/api/usage-stats':
            try:
                import session_store
                sessions = session_store.list_sessions()
                acc = {'sessions': 0, 'input_tokens': 0, 'output_tokens': 0,
                       'cache_read_tokens': 0, 'total_tokens': 0}
                for m in sessions:
                    u = m.get('usage')
                    if not u:
                        continue
                    acc['sessions'] += 1
                    acc['input_tokens'] += int(u.get('input_tokens') or 0)
                    acc['output_tokens'] += int(u.get('output_tokens') or 0)
                    acc['cache_read_tokens'] += int(u.get('cache_read_tokens') or 0)
                    acc['total_tokens'] += int(u.get('total_tokens') or 0)
                return self._json(api_ok(acc))
            except Exception as e:
                return self._json(api_err('用量统计失败: {0}'.format(e)))
        if path == '/api/token-usage':
            try:
                return self._json(api_ok(_token_usage_payload()))
            except Exception as e:
                return self._json(api_err('Token 用量统计失败: {0}'.format(e)))
        if path == '/api/plugins-task':
            tid = (qs.get('task') or [''])[0]
            if not tid:
                return self._json(api_err('缺少任务 id'))
            return self._json(_plugin_task_payload(tid))
        if path == '/api/sessions-search':
            q = (qs.get('q') or [''])[0]
            try:
                import session_store
                return self._json(api_ok(session_store.search_sessions(q)))
            except Exception as e:
                return self._json(api_err('搜索失败: {0}'.format(e)))
        if path == '/api/session-detail':
            sid = (qs.get('id') or [''])[0]
            try:
                import session_store
                detail = session_store.session_detail(sid)
            except Exception as e:
                return self._json(api_err('读取失败: {0}'.format(e)))
            if not detail:
                return self._json(api_err('会话不存在'), 404)
            return self._json(api_ok(detail))
        if path == '/api/providers':
            return self._json(providers_payload())
        if path == '/api/models':
            # 模型管理：读取 settings.yaml 模型表明细 + 默认模型（含 reasoningEffort）
            try:
                import session_store
                info = session_store.providers_info()
                return self._json(api_ok({
                    'providers': info.get('providers') or [],
                    'default_model': info.get('default_model') or {},
                }))
            except Exception as e:
                return self._json(api_err('读取模型配置失败: {0}'.format(e)))
        if path == '/api/settings':
            cfg = ts.load_config()
            return self._json(api_ok({"settings": {k: cfg.get(k) for k in
                                                   ('cdp_port', 'dsh_root',
                                                    'launcher_mode', 'desktop_exe')}}))
        if path == '/api/plugins':
            return self._json(api_ok(plugin_manager.list_plugins()))
        if path == '/api/plugins-resolve':
            # 安装前预检：spec → 自动选轨（A=npm 官方直装 / B=github·本地装配行），
            # 同步返回（含 npm registry 查询），面板据此给出确认文案
            spec = (qs.get('spec') or [''])[0]
            try:
                return self._json(api_ok(plugin_manager.resolve_spec(spec)))
            except ValueError as e:
                return self._json(api_err(str(e)))
        if path == '/api/enhance':
            return self._json(api_ok(enhance_engine.summary()))
        if path == '/api/enhance-file':
            name = (qs.get('name') or [''])[0]
            content = enhance_engine.read_file(name)
            if content is None:
                return self._json(api_err('文件不存在: ' + str(name)), 404)
            return self._json(api_ok({"name": os.path.basename(name), "content": content}))
        if path == '/api/template':
            # 换肤已移除：仅保留区域契约（增强/选择器适配仍需要）
            return self._json(api_ok({
                "schema": {},
                "defaults": {},
                "templates": {"light": "", "dark": ""},
                "regions": [{"key": k, "label": v["label"], "reliable": v.get("reliable", False),
                             "note": v.get("note", "")}
                            for k, v in marker_engine.resolve_regions().items()],
                "legacy_removed": True,
                "note": "换肤已移除，动态背景请用 DSH 插件 dsh-plugin-wallpaper-engine",
            }))
        if path.startswith('/api/theme-params/'):
            return self._json(api_err('换肤功能已移除'), 410)
        if path.startswith('/api/preview-css/'):
            return self._json(api_err('换肤功能已移除'), 410)
        if path.startswith('/api/theme-image/'):
            return self._json(api_err('换肤功能已移除'), 410)
        if path.startswith('/api/export/'):
            return self._json(api_err('换肤功能已移除'), 410)
        if path == '/favicon.ico':
            self.send_response(204)
            self._cors()
            self.end_headers()
            return
        # 静态托管
        rel = path.lstrip('/') or 'panel.html'
        fp = os.path.normpath(os.path.join(ROOT, rel))
        if not fp.startswith(ROOT) or not os.path.isfile(fp):
            return self._json(api_err('文件不存在: ' + rel), 404)
        _mime = {'html': 'text/html; charset=utf-8', 'css': 'text/css; charset=utf-8',
                 'js': 'application/javascript', 'png': 'image/png',
                 'jpg': 'image/jpeg', 'svg': 'image/svg+xml',
                 'json': 'application/json', 'woff2': 'font/woff2'}
        _ext = fp.rsplit('.', 1)[-1].lower() if '.' in fp else ''
        ct = _mime.get(_ext, 'application/octet-stream')
        with open(fp, 'rb') as f:
            data = f.read()
        # 托管的 HTML 页面注入本机令牌引导：页面内所有 /api 调用自动带头
        if _ext == 'html':
            bootstrap = ('<script>window.__DSHSKIN_TOKEN__=%s;'
                         '(function(){var of=window.fetch;if(of){window.fetch=function(i,n){'
                         'n=n||{};n.headers=new Headers(n.headers||{});'
                         'try{var u=(typeof i==="string")?i:(i&&i.url)||"";'
                         'if(u.indexOf("/api/")>=0||u.indexOf("127.0.0.1")>=0||u.indexOf("localhost")>=0){'
                         'n.headers.set("X-DSHSkin-Token",window.__DSHSKIN_TOKEN__);}}catch(e){}'
                         'return of.call(this,i,n);};}})();</script>'
                         % json.dumps(SERVER_TOKEN)).encode('utf-8')
            text = data.decode('utf-8', 'replace')
            if '<head' in text.lower():
                text = re.sub(r'(<head[^>]*>)', r'\1' + bootstrap.decode('utf-8'), text, count=1, flags=re.I)
            else:
                text = bootstrap.decode('utf-8') + text
            data = text.encode('utf-8')
        return self._bin(data, ct)

    # ---------------- POST ----------------
    def _read_body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return self.rfile.read(n) if n > 0 else b''

    def _body_json(self):
        return json.loads(self._read_body() or b'{}')

    def do_POST(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        path = u.path

        if not self._origin_ok():
            add_log('已拒绝跨站请求: {0}'.format(path), 'error')
            return self._json(api_err('跨站请求被拒绝（仅允许本机来源）'), 403)

        # 所有状态变更必须携带本机令牌（面板由 server 托管并注入 token）
        if not self._token_ok():
            add_log('已拒绝无令牌的变更请求: {0}'.format(path), 'error')
            return self._deny()

        if path == '/api/switch':
            return self._json(api_err('换肤功能已移除'), 410)

        if path == '/api/restore':
            res = run_ts(ts.restore)
            if res['ok']:
                add_log('已清除 CDP 注入', 'success')
            return self._json(res)

        if path.startswith('/api/rebuild/'):
            return self._json(api_err('换肤功能已移除'), 410)

        if path == '/api/probe':
            if not cdp_skin.WEBSOCKET_AVAILABLE:
                return self._json(api_err('需要 websocket-client：python dsh-skin.py deps --install'))
            if not cdp_skin.cdp_ready():
                return self._json(api_err('未发现 DSH 调试端口 {0}，请先启动桌面版'.format(dsh_env.cdp_port())))
            body = self._body_json()
            res = cdp_skin.probe_regions()
            if not res.get('ok'):
                return self._json(api_err(res.get('err')))
            applied = None
            if body.get('apply'):
                applied = marker_engine.save_overrides(res.get('suggested', {}))
                add_log('选择器实测并已写入覆盖 {0} 项'.format(len(applied)), 'success')
            return self._json(api_ok({"counts": res.get('counts'), "alive": res.get('alive'),
                                      "dead": res.get('dead'), "suggested": res.get('suggested'),
                                      "applied": applied}))

        if path == '/api/drift-baseline':
            body = self._body_json()
            if body.get('reset'):
                try:
                    os.remove(DRIFT_BASELINE)
                except OSError:
                    pass
                _drift_cache['v'] = None
                add_log('已清除界面结构基线', 'info')
                return self._json(api_ok({"baseline": None}))
            cur = current_dom_fingerprint()
            if cur is None:
                return self._json(api_err('DSH 调试端口未就绪，无法实测建立基线'))
            if cur.get('dead'):
                return self._json(api_err('当前仍有区域未命中（%s），请确认 DSH 已进入主界面后重试'
                                          % ', '.join(cur['dead'])))
            save_drift_baseline(cur)
            _drift_alerted.clear()
            _drift_cache['v'] = None
            add_log('已更新界面结构基线（%d 个区域，DSH %s）'
                    % (len(cur['fingerprint']), cur.get('dsh_version') or '?'), 'success')
            invalidate_status()
            return self._json(api_ok({"baseline": cur, "rebuilt": []}))

        if path == '/api/selectors':
            body = self._body_json()
            if body.get('reset'):
                marker_engine.save_overrides({})
                add_log('已清空选择器覆盖', 'info')
                return self._json(api_ok({"overrides": {}}))
            regions = body.get('regions')
            if not isinstance(regions, dict):
                return self._json(api_err('缺少 regions'))
            saved = marker_engine.save_overrides(regions)
            add_log('已更新选择器覆盖: {0}'.format(', '.join(saved) or '空'), 'success')
            invalidate_status()
            return self._json(api_ok({"overrides": saved}))

        if path == '/api/settings':
            body = self._body_json()
            cfg = ts.load_config()
            changed = []
            for key in ('cdp_port', 'dsh_root', 'launcher_mode', 'desktop_exe'):
                if key in body:
                    cfg[key] = body[key]
                    changed.append(key)
            if changed:
                ts.save_config(cfg)
                dsh_env.invalidate()
                add_log('已更新设置: {0}'.format(', '.join(changed)), 'success')
                invalidate_status()
            return self._json(api_ok({"settings": {k: cfg.get(k) for k in
                                                   ('cdp_port', 'dsh_root',
                                                    'launcher_mode', 'desktop_exe')}}))

        if path == '/api/cdp-apply':
            self._body_json()  # 兼容旧面板可能携带的 body；不再读取 allow_kill/force_launch
            r = cdp_auto_apply()
            if r.get('ok'):
                add_log('CDP 主题已注入（可逆，不修改官方文件）', 'success')
                invalidate_status()
                return self._json(api_ok({'landmarks': r.get('landmarks')}))
            msg = r.get('err', '注入失败')
            if msg == 'running-without-cdp':
                msg = 'DeepSeek Harness 正在运行但未开启调试端口；请在面板点「以注入模式重启」（不会自动替你重启）'
            elif msg == 'stopped':
                msg = 'DeepSeek Harness 未运行；请在面板点「启动 DSH（调试模式）」或运行 启动注入模式.bat 手动启动'
            elif msg == 'websocket-not-installed':
                msg = '缺少依赖 websocket-client，请先执行 deps --install'
            return self._json(api_err(msg))

        if path == '/api/cdp-restore':
            r = cdp_skin.remove_css()
            if r.get('ok'):
                add_log('已移除 CDP 皮肤', 'info')
                invalidate_status()
                return self._json(api_ok({}))
            return self._json(api_err(r.get('err', '移除失败')))

        if path == '/api/remove':
            return self._json(api_err('换肤功能已移除'), 410)

        if path == '/api/install':
            return self._json(api_err('换肤功能已移除；插件请用「插件管理」或 DSH 插件市场'), 410)

        if path == '/api/wallpaper':
            body = self._body_json()
            res = wallpaper_set(body)
            if res.get('ok'):
                self._try_cdp(log_prefix='壁纸参数更新后')
            return self._json(res)

        if path == '/api/repair':
            fixed = repair_injection()
            add_log('执行注入自检' + ('，修复: ' + '；'.join(fixed) if fixed else '，一切正常'),
                    'success' if not fixed else 'info')
            invalidate_status()
            return self._json(api_ok({"fixed": fixed}))

        if path == '/api/restart-dsh':
            add_log('用户确认后重启 DeepSeek Harness', 'info')
            try:
                ok, msg = restart_dsh()
                add_log(msg, 'success' if ok else 'error')
                return self._json(api_ok({"restarted": ok, "msg": msg}) if ok else api_err(msg))
            except Exception as e:
                add_log('重启失败: {0}'.format(e), 'error')
                return self._json(api_err('重启失败: {0}'.format(e)))

        if path.startswith('/api/theme-params/'):
            return self._json(api_err('换肤功能已移除'), 410)

        if path == '/api/enhance':
            body = self._body_json()
            if body.get('preset'):
                try:
                    st = enhance_engine.apply_preset(body['preset'])
                except ValueError as e:
                    return self._json(api_err(str(e)))
                add_log('增强预设 → {0}'.format(body['preset']), 'success')
                self._try_cdp(log_prefix='增强预设后')
                return self._json(api_ok({"state": st, "summary": enhance_engine.summary()}))
            st = enhance_engine.load_state()
            changed = []
            if 'enabled' in body:
                st['enabled'] = bool(body['enabled'])
                changed.append('总开关')
            if isinstance(body.get('modules'), dict):
                keys = {m['key'] for m in enhance_engine.MODULES}
                for k, v in body['modules'].items():
                    if k in keys:
                        st['modules'][k] = bool(v)
                        changed.append(k)
            if body.get('script'):
                st['scripts'][os.path.basename(body['script'])] = bool(body.get('script_enabled', True))
                changed.append(body['script'])
            if isinstance(body.get('hotkeys'), dict):
                # 改键：只接受注册表里的动作，空串=清除（回退默认），并做冲突检测
                merged = dict(st.get('hotkeys', {}))
                for action, combo in body['hotkeys'].items():
                    if action not in enhance_engine.HOTKEYS:
                        continue
                    combo = (combo or '').strip()
                    if combo:
                        norm = enhance_engine.normalize_combo(combo)
                        if not norm:
                            return self._json(api_err('快捷键格式无效: {0} = {1}'.format(action, combo)))
                        merged[action] = norm
                    else:
                        merged.pop(action, None)
                conflicts = enhance_engine.detect_hotkey_conflicts(merged)
                if conflicts:
                    c = conflicts[0]
                    return self._json(api_err('快捷键冲突：{0} 同时绑定到 {1}'.format(
                        c['combo'], '、'.join(c['actions']))))
                st['hotkeys'] = merged
                changed.append('快捷键')
            st = enhance_engine.save_state(st)
            if changed:
                add_log('增强设置已更新: {0}'.format('、'.join(changed)), 'success')
                self._try_cdp(log_prefix='增强设置后')
            return self._json(api_ok({"state": st, "summary": enhance_engine.summary()}))

        if path == '/api/enhance-save':
            body = self._body_json()
            content = body.get('content') or ''
            try:
                name = enhance_engine.write_file(body.get('name'), content)
            except ValueError as e:
                return self._json(api_err(str(e)))
            # 用户自写脚本：返回静态风险扫描供编辑器提示，不阻断保存
            scan = enhance_engine.analyze_script(content) if str(name).endswith('.js') else None
            add_log('已保存增强文件 {0}'.format(name), 'success')
            self._try_cdp(log_prefix='保存脚本后')
            return self._json(api_ok({"name": name, "summary": enhance_engine.summary(),
                                      "scan": scan}))

        if path == '/api/enhance-delete':
            name = self._body_json().get('name')
            if not enhance_engine.delete_file(name):
                return self._json(api_err('删除失败: ' + str(name)))
            add_log('已删除增强文件 {0}'.format(name), 'info')
            self._try_cdp(log_prefix='删除脚本后')
            return self._json(api_ok({"summary": enhance_engine.summary()}))

        if path == '/api/enhance-apply':
            self._body_json()  # 兼容旧面板 body；不再读取 allow_kill/force_launch
            r = cdp_auto_apply()
            if r.get('ok'):
                add_log('已注入主题 + 增强（可逆）', 'success')
                return self._json(api_ok({'landmarks': r.get('landmarks'), 'jsLen': r.get('jsLen')}))
            msg = r.get('err', '注入失败')
            if msg == 'running-without-cdp':
                msg = 'DeepSeek Harness 正在运行但未开启调试端口；请在面板点「以注入模式重启」（不会自动替你重启）'
            elif msg == 'stopped':
                msg = 'DeepSeek Harness 未运行；请在面板点「启动 DSH（调试模式）」或运行 启动注入模式.bat 手动启动'
            elif msg == 'websocket-not-installed':
                msg = '缺少依赖 websocket-client，请先执行 deps --install'
            return self._json(api_err(msg))

        # ---------------- v2：市场安装 / 会话导出备份 / 凭证备份 / 启动 ----------------
        if path == '/api/launch':
            plugin_manager.start_injector()
            r = run_ts(dsh_env.launch_dsh)
            if r.get('ok'):
                add_log('DeepSeek Harness 启动指令已下发', 'info')
                return self._json(api_ok({"state": r.get('state', 'launched')}))
            return self._json(api_err(r.get('err', '启动失败')))

        if path == '/api/market-inspect':
            body = self._body_json()
            name = str(body.get('name') or '')
            if not re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$', name):
                return self._json(api_err('非法脚本名'))
            info = market_inspect(name)
            if not info:
                return self._json(api_err('市场里没有这个脚本: ' + name))
            return self._json(api_ok(info))

        if path == '/api/market-install':
            body = self._body_json()
            name = str(body.get('name') or '')
            if not re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$', name):
                return self._json(api_err('非法脚本名'))
            confirmed = bool(body.get('confirmed'))
            result = market_install(name, confirmed=confirmed)
            if not result:
                return self._json(api_err('市场里没有这个脚本: ' + name))
            # 第一步：未确认 → 返回权限/风险，要求前端弹确认框
            if result.get('need_confirm'):
                return self._json(api_ok({'need_confirm': True, 'analysis': result['analysis']}))
            target = result['installed']
            st = enhance_engine.load_state()
            st.setdefault('modules', {})
            if not st['modules'].get('user-scripts'):
                st['modules']['user-scripts'] = True   # 装了脚本就开用户脚本通道
                enhance_engine.save_state(st)
            ana = result.get('analysis', {})
            add_log('已从市场安装脚本「{0}」→ {1}（风险 {2}，权限 {3}）'.format(
                name, target, ana.get('risk'), ','.join(ana.get('permissions', []))), 'success')
            invalidate_status()
            return self._json(api_ok({"name": target, "analysis": ana}))

        if path == '/api/sessions-compare':
            body = self._body_json()
            ids = body.get('ids') or []
            if not isinstance(ids, list) or not ids:
                return self._json(api_err('请选择要对比的会话'))
            if len(ids) > 10:
                return self._json(api_err('一次最多对比 10 个会话'))
            try:
                import session_store
                return self._json(api_ok(session_store.compare_sessions(ids)))
            except Exception as e:
                return self._json(api_err('对比失败: {0}'.format(e)))

        if path == '/api/sessions-export':
            body = self._body_json()
            try:
                import session_store
                out = session_store.export_session(str(body.get('id') or ''),
                                                   fmt=str(body.get('format') or 'md'))
            except Exception as e:
                return self._json(api_err('导出失败: {0}'.format(e)))
            if not out:
                return self._json(api_err('会话不存在或为空'))
            add_log('会话已导出: {0}'.format(out), 'success')
            return self._json(api_ok({"path": out}))

        if path == '/api/sessions-backup':
            try:
                import session_store
                out = session_store.backup_all()
            except Exception as e:
                return self._json(api_err('备份失败: {0}'.format(e)))
            add_log('会话库已备份: {0}'.format(out), 'success')
            return self._json(api_ok({"path": out}))

        if path == '/api/sessions-delete':
            body = self._body_json()
            ids = body.get('ids') or []
            if not isinstance(ids, list) or not ids:
                return self._json(api_err('请选择要删除的会话'))
            if len(ids) > 50:
                return self._json(api_err('单次最多删除 50 个会话'))
            try:
                import session_store
                out = session_store.delete_sessions(ids)
            except Exception as e:
                return self._json(api_err('删除失败: {0}'.format(e)))
            add_log('已物理删除会话 {0} 个（备份至 {1}）'.format(len(out['deleted']), out['backup']), 'success')
            return self._json(api_ok(out))

        if path == '/api/plugins-install-spec':
            # 便捷安装器（双轨自动）：能解析 npm 版本 → A 轨官方 bundle 直装
            # （pnpm profile 事务，DSH 须退出）；否则 B 轨（github 拉源码 / 本地导入）。
            body = self._body_json()
            spec = str(body.get('spec') or '').strip()
            web = bool(body.get('web', True))
            desktop = bool(body.get('desktop', True))
            try:
                rs = plugin_manager.resolve_spec(spec)
            except ValueError as e:
                return self._json(api_err(str(e)))
            if rs['track'] == 'A' and dsh_env.is_running():
                return self._json(api_err('DeepSeek Harness 正在运行，请先完全退出再安装'
                                          '（A 轨需写 desktop profile，避免事务锁冲突）'))
            tid = _plugin_task_new(spec)
            add_log('插件安装已提交（%s 轨 · %s）: %s' % (
                rs['track'], rs.get('kind') or '', spec), 'info')

            def _run():
                try:
                    if rs['track'] == 'A':
                        info = plugin_manager.install_bundle_npm(
                            rs['name'], rs.get('version'), {'web': web, 'desktop': desktop},
                            description=rs.get('description') or '')
                        _plugin_task_set(tid, status='done', name=spec,
                                         msg='已官方直装 {0}@{1}（A 轨 bundle → profiles/desktop，重启 DSH 生效）'.format(
                                             info['name'], info['version']), info=info)
                        add_log('已官方直装插件 {0}@{1}（A 轨）'.format(info['name'], info['version']), 'success')
                    else:
                        tg = {'web': web, 'desktop': desktop}
                        if rs.get('kind') == 'github':
                            info = plugin_manager.install_from_github(spec, tg)
                        elif rs.get('kind') == 'local-dir':
                            info = plugin_manager.import_dir(rs['spec'], tg)
                        elif rs.get('kind') == 'local-pkg':
                            info = plugin_manager.import_package(rs['spec'], tg)
                        else:
                            raise ValueError('未知的 B 轨来源: %s' % rs.get('kind'))
                        _plugin_task_set(tid, status='done', name=spec,
                                         msg='已导入 {0}@{1}（B 轨装配行，bundle={2} client={3}）'.format(
                                             info['name'], info['version'],
                                             info['hasBundle'], info['hasClient']), info=info)
                        add_log('已导入插件 {0}@{1}（B 轨装配行）'.format(info['name'], info['version']), 'success')
                except Exception as e:
                    _plugin_task_set(tid, status='error', name=spec, msg=str(e) or '安装失败')
                    add_log('插件安装失败: {0}'.format(e), 'error')

            threading.Thread(target=_run, daemon=True, name='dshskin-plugin-spec-install').start()
            return self._json(api_ok({'task': tid, 'track': rs['track'], 'kind': rs.get('kind'),
                                      'name': rs.get('name'), 'version': rs.get('version')}))

        if path == '/api/plugins-install':
            data = self._read_body()
            if not data:
                return self._json(api_err('未收到插件包数据'))
            name = (qs.get('name') or ['plugin.zip'])[0]
            web = (qs.get('web') or ['1'])[0] not in ('0', 'false', '')
            desktop = (qs.get('desktop') or ['1'])[0] not in ('0', 'false', '')
            if not name.lower().endswith(('.zip', '.tgz', '.tar.gz')):
                return self._json(api_err('仅支持 .zip / .tgz / .tar.gz 插件包'))
            if name.lower().endswith('.zip') and data[:2] != b'PK':
                return self._json(api_err('文件不是有效的 zip'))
            tmp = tempfile.NamedTemporaryFile(suffix=os.path.splitext(name)[1], delete=False)
            tmp.write(data)
            tmp.close()
            tid = _plugin_task_new(name)
            add_log('插件导入已提交（后台执行）: {0}'.format(name), 'info')

            def _run():
                try:
                    info = plugin_manager.import_package(tmp.name, {'web': web, 'desktop': desktop})
                    _plugin_task_set(tid, status='done', name=name,
                                     msg='已导入 {0}@{1}（bundle={2} client={3}）'.format(
                                         info['name'], info['version'],
                                         info['hasBundle'], info['hasClient']), info=info)
                    add_log('已导入插件 {0}@{1}（bundle={2} client={3}）'.format(
                        info['name'], info['version'], info['hasBundle'], info['hasClient']), 'success')
                except Exception as e:
                    _plugin_task_set(tid, status='error', name=name, msg=str(e) or '导入失败')
                    add_log('插件导入失败: {0}'.format(e), 'error')
                finally:
                    if os.path.exists(tmp.name):
                        try:
                            os.remove(tmp.name)
                        except OSError:
                            pass

            threading.Thread(target=_run, daemon=True, name='dshskin-plugin-install').start()
            return self._json(api_ok({'task': tid}))

        if path == '/api/plugins-install-dir':
            # 目录导入：前端 webkitdirectory 选择目录后，把文件树（相对路径 + base64 内容）
            # 一次性 POST 到此接口，后端在临时目录重建后再走 plugin_manager.import_dir（校验/入库/接线）。
            try:
                body = self._body_json()
            except Exception as e:
                return self._json(api_err('请求体不是合法 JSON: {0}'.format(e)))
            files = body.get('files')
            if not isinstance(files, list) or not files:
                return self._json(api_err('未收到目录文件清单'))
            web = bool(body.get('web', True))
            desktop = bool(body.get('desktop', True))
            tmp = tempfile.mkdtemp(prefix='dshskin-plugin-dir-')
            try:
                for f in files:
                    rel = str(f.get('path') or '')
                    data64 = f.get('data')
                    if not rel or rel.startswith(('/', '\\')) or '\\' in rel:
                        raise ValueError('非法相对路径: {0}'.format(rel))
                    rel = rel.replace('\\', '/')
                    if rel.startswith('..') or '/..' in rel or rel.endswith('/..'):
                        raise ValueError('非法相对路径: {0}'.format(rel))
                    if data64 is None:
                        raise ValueError('文件 {0} 缺少内容'.format(rel))
                    try:
                        data = base64.b64decode(data64)
                    except Exception:
                        raise ValueError('文件 {0} 内容不是合法 base64'.format(rel))
                    if len(data) > 50 * 1024 * 1024:
                        raise ValueError('文件 {0} 超过 50MB 上限'.format(rel))
                    dest = os.path.normpath(os.path.join(tmp, rel))
                    if not dest.startswith(os.path.normpath(tmp) + os.sep):
                        raise ValueError('非法路径逃逸: {0}'.format(rel))
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, 'wb') as fh:
                        fh.write(data)
                info = plugin_manager.import_dir(tmp, {'web': web, 'desktop': desktop})
                add_log('已从目录导入插件 {0}@{1}（bundle={2} client={3}）'.format(
                    info['name'], info['version'], info['hasBundle'], info['hasClient']), 'success')
                return self._json(api_ok({'info': info}))
            except ValueError as e:
                return self._json(api_err(str(e)))
            except Exception as e:
                add_log('目录导入失败: {0}'.format(e), 'error')
                return self._json(api_err('目录导入失败: {0}'.format(e)))
            finally:
                try:
                    import shutil
                    shutil.rmtree(tmp, ignore_errors=True)
                except Exception:
                    pass

        if path == '/api/plugins-toggle':
            body = self._body_json()
            try:
                plugin_manager.set_enabled(str(body.get('name') or ''), str(body.get('target') or ''),
                                           bool(body.get('enabled')))
            except ValueError as e:
                return self._json(api_err(str(e)))
            add_log('插件「{0}」{1} 端已{2}'.format(body.get('name'), body.get('target'),
                                                      '启用' if body.get('enabled') else '停用'), 'info')
            return self._json(api_ok(plugin_manager.list_plugins()))

        if path == '/api/plugins-toggle-all':
            body = self._body_json()
            plugin_manager.set_enabled_all(bool(body.get('enabled')))
            add_log('插件管理已全局{0}'.format('启用' if body.get('enabled') else '停用'), 'info')
            return self._json(api_ok(plugin_manager.list_plugins()))

        if path == '/api/plugins-config':
            # 插件偏好（外部「按偏好工作」的两个官方落点）：
            #   kind='settings'（默认）：写入 $DSH_HOME/settings.yaml 命名空间
            #     —— settings 型插件（官方 settings-file 提供者挂载在 base bundle，
            #     热重载生效，无需重启）；
            #   kind='config'：写入装配层 Loader 行 config: 段
            #     —— config 型插件（偏好随装配层 mount，重启生效）。
            # 留空 = 删除对应偏好（恢复插件默认）。
            body = self._body_json()
            name = str(body.get('name') or '')
            kind = str(body.get('kind') or 'settings')
            ns = str(body.get('ns') or body.get('namespace') or '')
            config_text = body.get('config')
            if isinstance(config_text, dict):
                if yaml is None:
                    return self._json(api_err('PyYAML 未安装，无法序列化 config 型偏好；请 pip install pyyaml'))
                config_text = yaml.safe_dump(config_text, allow_unicode=True, sort_keys=False)
            text = config_text if isinstance(config_text, str) else ''
            try:
                if kind == 'config':
                    plugin_manager.set_plugin_config(name, text)
                    plugin_manager.remember_settings(name, '')
                else:
                    if not ns:
                        ns = plugin_manager.default_namespace(name)
                    plugin_manager.set_settings_namespace(ns, text)
                    plugin_manager.remember_settings(name, text, ns)
            except ValueError as e:
                return self._json(api_err(str(e)))
            add_log('插件「{0}」偏好已更新（{1}）'.format(
                name, '装配行 config' if kind == 'config' else 'settings.yaml:{0}'.format(ns)), 'info')
            return self._json(api_ok(plugin_manager.list_plugins()))

        if path == '/api/plugins-remove':
            body = self._body_json()
            try:
                plugin_manager.remove_package(str(body.get('name') or ''))
            except ValueError as e:
                return self._json(api_err(str(e)))
            add_log('已卸载插件「{0}」'.format(body.get('name')), 'success')
            return self._json(api_ok(plugin_manager.list_plugins()))

        if path == '/api/plugin-reload':
            # 从记录的源目录重载目录导入插件；未记录/失效时弹原生对话框选一次并记住
            body = self._body_json()
            name = str(body.get('name') or '').strip()
            if not name:
                return self._json(api_err('缺少插件名'))
            try:
                result = plugin_manager.reload_from_dir(name)
                if not result.get('ok'):
                    return self._json(api_ok({'cancelled': True}))
                info = result['info']
                add_log('已从源目录重载插件 {0}@{1}'.format(info['name'], info['version']), 'success')
                return self._json(api_ok({'src': result.get('src'), 'info': info,
                                          'plugins': plugin_manager.list_plugins()}))
            except ValueError as e:
                return self._json(api_err(str(e)))
            except Exception as e:
                add_log('插件重载失败: {0}'.format(e), 'error')
                return self._json(api_err('插件重载失败: {0}'.format(e)))

        if path == '/api/plugins-restore':
            # 移除 DSHSkin 托管块并把 cordis.patch.yml 还原到介入前（完全可逆）
            try:
                report = plugin_manager.restore_managed(disable_registry=True)
                add_log('已移除插件托管块并还原补丁层: {0}'.format(
                        '；'.join('{0}={1}'.format(os.path.basename(os.path.dirname(k)), v)
                                  for k, v in report.items())), 'success')
                return self._json(api_ok({'report': report, 'plugins': plugin_manager.list_plugins()}))
            except Exception as e:
                return self._json(api_err('还原托管块失败: {0}'.format(e)))

        if path == '/api/uninstall':
            # 面板侧完整卸载：移除注入 + 还原托管块（不运行中自删数据目录，purge 走 CLI）
            try:
                report = ts.uninstall(purge=False)
                add_log('已执行卸载：移除注入 {0} 处，还原托管块 {1}'.format(
                    len(report['injection_removed']), report['patch_restored']), 'success')
                invalidate_status()
                return self._json(api_ok(report))
            except Exception as e:
                return self._json(api_err('卸载失败: {0}'.format(e)))

        if path == '/api/snapshots-recapture':
            r = recapture_snapshots()
            if r.get('ok'):
                add_log('预览快照已从当前 DSH 重新抓取', 'success')
                return self._json(api_ok(r))
            add_log('快照重抓失败: {0}'.format(r.get('err')), 'error')
            return self._json(api_err(r.get('err', '重抓失败')))

        if path == '/api/autostart':
            enable = bool(body.get('enable'))
            ok, info = tray.set_autostart(enable)
            if ok:
                add_log('开机自启已{0}'.format('开启' if enable else '关闭'), 'success')
                return self._json(api_ok({'enabled': bool(info)}))
            return self._json(api_err('设置开机自启失败: {0}'.format(info)))

        if path == '/api/providers-backup':
            try:
                import session_store
                out = session_store.backup_credentials()
            except Exception as e:
                return self._json(api_err('备份失败: {0}'.format(e)))
            add_log('凭证已备份: {0}'.format(out), 'success')
            return self._json(api_ok({"path": out}))

        if path == '/api/models-save':
            # 模型管理：行级手术写 settings.yaml（contextWindow/maxTokens/input + reasoningEffort）
            try:
                import session_store
            except Exception as e:
                return self._json(api_err('session_store 不可用: {0}'.format(e)))
            body = self._body_json()
            provider = str(body.get('provider') or '')
            model = str(body.get('model') or '')
            if not provider or not model:
                return self._json(api_err('缺少 provider/model'))
            context_window = body.get('contextWindow')
            max_tokens = body.get('maxTokens')
            vision = body.get('vision')
            reasoning = body.get('reasoning')
            model_reasoning = body.get('modelReasoning')
            enabled = body.get('enabled')
            try:
                if context_window is not None:
                    context_window = int(context_window)
                    if context_window <= 0:
                        raise ValueError()
                if max_tokens is not None:
                    max_tokens = int(max_tokens)
                    if max_tokens <= 0:
                        raise ValueError()
            except (TypeError, ValueError):
                return self._json(api_err('contextWindow/maxTokens 必须是正整数'))
            if vision is not None:
                vision = bool(vision)
            if reasoning is not None and not isinstance(reasoning, str):
                return self._json(api_err('reasoning 参数非法'))
            if model_reasoning is not None and not isinstance(model_reasoning, str):
                return self._json(api_err('modelReasoning 参数非法'))
            if enabled is not None and not isinstance(enabled, bool):
                return self._json(api_err('enabled 参数非法'))
            try:
                ok, msg = session_store.save_model_config(
                    provider=provider, model=model, context_window=context_window,
                    max_tokens=max_tokens, vision=vision, reasoning=reasoning,
                    model_reasoning=model_reasoning, enabled=enabled)
            except Exception as e:
                return self._json(api_err('保存失败: {0}'.format(e)))
            if ok:
                add_log('模型配置已更新: {0}/{1} — {2}'.format(provider, model, msg), 'success')
                return self._json(api_ok({'msg': msg}))
            return self._json(api_err(msg))

        if path == '/api/app-quit':
            # 面板「退出」：显式终止本进程（释放 dist\DSH++.exe，便于重新构建）。
            # 延迟 0.3s 让响应先落回前端，再由线程执行退出。
            threading.Timer(0.3, _app_quit).start()
            return self._json(api_ok({}))
        if path == '/api/logs-clear':
            # 面板「清空日志」：清空持久化日志（保留已轮转的 .old.json）
            with _LOG_LOCK:
                try:
                    if os.path.exists(LOG_FILE):
                        os.remove(LOG_FILE)
                except OSError as e:
                    return self._json(api_err('清空失败: {0}'.format(e)))
            add_log('已清空操作日志', 'info', source='user-action')
            return self._json(api_ok({}))
        if path == '/api/open-path':
            body = self._body_json()
            target = str(body.get('path') or '')
            real = os.path.realpath(target)
            root_real = os.path.realpath(ts.SKIN_ROOT)
            if not real.lower().startswith(root_real.lower()):
                return self._json(api_err('只允许打开 ~/.dsh-skins 内的路径'))
            if not os.path.exists(real):
                return self._json(api_err('路径不存在: ' + target))
            try:
                if os.name == 'nt':
                    flag = '/select,' if os.path.isfile(real) else ''
                    subprocess.Popen(['explorer', flag, real])
                else:
                    subprocess.Popen(['xdg-open', os.path.dirname(real) if os.path.isfile(real) else real])
                return self._json(api_ok({"opened": real}))
            except Exception as e:
                return self._json(api_err('打开失败: {0}'.format(e)))

        return self._json(api_err('未知接口: ' + path), 404)

    def _live_state(self):
        """参数/主题变更后，判断「是否已在渲染进程里热生效」。"""
        info = {'live': False, 'channel': 'cdp', 'cdp_available': False, 'cdp_target': False}
        try:
            info['cdp_available'] = cdp_skin.WEBSOCKET_AVAILABLE
            info['cdp_target'] = bool(cdp_skin.find_page_target())
            if info['cdp_target']:
                st = cdp_skin.page_state()
                css, js = active_bundle()
                cur = cdp_skin.bundle_hash(css, js)
                info['live'] = bool(st.get('ok') and st.get('injected') and st.get('hash') == cur)
        except Exception:
            pass
        return info

    def _try_cdp(self, log_prefix=''):
        """切换/安装/调参后尝试热注入；失败只记录，不影响主流程"""
        if not cdp_skin.WEBSOCKET_AVAILABLE:
            return
        try:
            r = cdp_auto_apply()
            # 良性未就绪状态不刷日志：没运行 / 运行但无调试端口 / 无激活主题
            if not r.get('ok') and r.get('err') not in ('running-without-cdp', 'stopped', 'no-active-enhancement'):
                add_log('{0} CDP 注入未完成: {1}'.format(log_prefix, r.get('err')), 'info')
        except Exception as e:
            add_log('{0} CDP 注入异常: {1}'.format(log_prefix, e), 'error')

    def log_message(self, fmt, *args):
        sys.stderr.write('  [server] ' + (fmt % args) + '\n')


def open_browser(url):
    try:
        if sys.platform == 'win32':
            os.startfile(url)
        else:
            webbrowser.open(url)
        print('     已请求打开浏览器:', url)
    except Exception as e:
        print('     自动打开浏览器失败，请手动访问:', url, e)


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(('127.0.0.1', port)) == 0


def probe_dshskin(port, timeout=2.0):
    """探测端口上是否已有 DSHSkin 后端（避免误把别的程序当自己）。
    返回版本字符串或 None。"""
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(b'GET /api/ping HTTP/1.0\r\nHost: 127.0.0.1\r\n'
                      b'Connection: close\r\nAccept: application/json\r\n\r\n')
            buf = b''
            while b'\r\n\r\n' not in buf and len(buf) < 65536:
                b = s.recv(4096)
                if not b:
                    break
                buf += b
            if b'\r\n\r\n' not in buf:
                return None
            head, body = buf.split(b'\r\n\r\n', 1)
            m = re.search(rb'Content-Length:\s*(\d+)', head, re.I)
            need = int(m.group(1)) if m else None
            while need is not None and len(body) < need:
                b = s.recv(4096)
                if not b:
                    break
                body += b
        d = json.loads(body.decode('utf-8', 'replace'))
        if isinstance(d, dict) and d.get('app') == 'dsh-skin':
            return d.get('version') or 'dsh-skin'
    except Exception:
        return None
    return None


def pick_port(preferred):
    """选定监听端口：若首选被占用且是别的程序，向后找可用端口。"""
    if not port_in_use(preferred):
        return preferred, None
    if probe_dshskin(preferred):
        return preferred, 'exists'          # 已有 DSHSkin 在跑 → 复用
    for p in range(preferred + 1, preferred + 20):
        if not port_in_use(p):
            return p, 'shifted'
    return preferred, None


def main():
    ap = argparse.ArgumentParser(description='DSH++ 本地后端（原 DSHSkin）')
    ap.add_argument('--port', type=int, default=PORT)
    ap.add_argument('--no-open', action='store_true', help='不自动打开浏览器')
    ap.add_argument('--wait', type=float, default=0.0, help='绑定端口前等待秒数（自重启端口交接用）')
    ap.add_argument('--no-tray', action='store_true', help='不启动系统托盘图标（缺 pystray 时自动降级）')
    args = ap.parse_args()
    if args.wait > 0:
        time.sleep(args.wait)
    port, state = pick_port(args.port)
    url = 'http://127.0.0.1:{0}/'.format(port)
    if state == 'exists':
        print('[!] DSH++ 后端已在 {0} 运行，直接打开面板'.format(url))
        if getattr(sys, 'frozen', False) and not args.no_open:
            threading.Timer(0.6, lambda: open_browser(url)).start()
        else:
            open_browser(url)
        return
    if state == 'shifted':
        print('[!] 端口 {0} 被其它程序占用，改用 {1}'.format(args.port, port))
    srv = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print('[OK] DSH++ 面板已启动: {0}'.format(url))
    print('     静态目录: {0}'.format(ROOT))
    print('     Harness 根目录: {0}'.format(dsh_env.harness_root() or '未找到'))
    if not cdp_skin.WEBSOCKET_AVAILABLE:
        print('     [!] 缺少 websocket-client，CDP 通道不可用：python dsh-skin.py deps --install')
    print('     按 Ctrl+C 停止')
    # 自愈已按用户要求关闭：不再启动时 auto_heal_once / periodic heal_loop
    try:
        start_cdp_watcher()
        print('     CDP 注入守护已启动（端口 {0}）'.format(dsh_env.cdp_port()))
    except Exception as e:
        print('     CDP 守护启动失败:', e)
    try:
        drift_watch_loop()
    except Exception as e:
        print('     界面漂移巡检启动失败:', e)
    try:
        page_error_watch_loop()
    except Exception as e:
        print('     页面错误回收启动失败:', e)
    try:
        start_source_watcher()
    except Exception as e:
        print('     源码守护启动失败:', e)
    try:
        plugin_manager.start_injector()
        print('     插件注入守护已启动（registry: {0}）'.format(plugin_manager.REGISTRY))
    except Exception as e:
        print('     插件注入守护启动失败:', e)
    try:
        threading.Thread(target=_token_usage_warmup, daemon=True,
                         name='dshskin-token-usage-warmup').start()
        print('     Token 用量预热已启动')
        add_log('Token 用量预热线程已启动', 'info', source='system')
    except Exception as e:
        add_log('Token 用量预热启动失败: {0}'.format(e), 'warn', source='system')
        print('     Token 用量预热启动失败:', e)
    # 系统托盘（可选依赖 pystray；缺失则控制台形态，不影响功能）
    if not args.no_tray and importlib.util.find_spec('pystray') is not None:
        def _quit_srv():
            try:
                srv.shutdown()
            except Exception:
                pass
        tray.start_tray_thread(port, lambda: SERVER_TOKEN, lambda: open_browser(url), _quit_srv)
        print('     系统托盘已启动（右键图标可快速注入/退出）')
    elif not args.no_tray:
        print('     未安装 pystray，以控制台形态运行（pip install pystray 可启用托盘）')
    if getattr(sys, 'frozen', False) and not args.no_open:
        threading.Timer(1.2, lambda: open_browser(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')


if __name__ == '__main__':
    main()
