# -*- coding: utf-8 -*-
"""Wallpaper Engine 本地扫描器 —— 纯文件系统，零 DSH / 零 CDP / 零 HTTP。

背景（2026-09-21 架构修正）
--------------------------
「读取壁纸列表」与「把壁纸应用到 DSH」是两件独立的事：

  * **读取** = 遍历本机磁盘、解析各壁纸的 project.json。这一步不需要 DSH
    参与，也不需要调试端口 —— 任何有文件系统权限的进程都能做。
  * **应用** = 把选中的 id 写进插件 settings，让 DSH 换背景。这一步才涉及 DSH，
    由 `wallpaper_engine.save_settings()` 负责。

此前把「读取」错接在 CDP 上（借 DSH 页面同源 fetch 插件的 `/inventory` 路由），
导致 DSH 未开启 `--remote-debugging-port` 时壁纸清单整个不可用，且没有任何降级。
本模块用纯 Python 重写读取侧，彻底摆脱该依赖。

为什么插件一定要挂 HTTP 路由，而本模块不需要
--------------------------------------------
插件（v0.7.3）是「宿主端 + 浏览器端」双半结构：宿主端 `lib/index.js` 负责读盘，
浏览器端 `lib/client.js` 负责渲染。浏览器受沙箱限制**读不了本地文件**，所以宿主端
必须把扫描结果和媒体字节经 `/wallpaper-engine/*` 路由喂给它（README 原话：
注册 inventory 路由是「供浏览器端获取」；并明确「插件直接扫描磁盘，不读 WE 的配置」）。
本模块跑在 DSH++ 的 Python 后端里，**直接就有文件系统权限**，故无需复刻那层 HTTP。

契约来源
--------
逐行对照 dsh-plugin-wallpaper-engine v0.7.3 `lib/index.js` 实现（非 README）。
v0.7.5 已复核：下列函数全部仍在，枚举来源与 `WE_APPID='431960'` 均未变，契约一致。

  ====================================  ==========================================
  本模块函数                             对应插件函数（index.js 行号）
  ====================================  ==========================================
  `_registry_steam_root()`              `steamPathFromRegistryP()` (:100)
  `_env_steam_roots()`                  `steamRootsFromEnv()` (:123)
  `steam_probe_dirs()`                  `steamProbeDirsP()` (:177)
  `_libraries_from_vdf()`               `librariesFromVdfP()` (:211)
  `locate_wallpaper_engine()`           `locateWallpaperEngineP()` (:228)
  `owning_libraries()`                  `owningLibrariesP()` (:253)
  `_infer_type()`                       `inferType()` (:270)
  `_read_project()`                     `readProjectP()` (:278)
  `_resolve_scene_main()`               `resolveSceneMainFileP()` (:309)
  `_enumerate_wallpapers()`             `enumerateWallpapersAsync()` (:327)
  `_playlist_item_id()`                 `playlistItemId()` (:423)
  `_read_playlists()`                   `readPlaylistsP()` (:390)
  `upload_dir()`                        `resolveUploadDir()` (:1569)
  `_enumerate_uploads()`                `enumerateUploadsP()` (:1777)
  `scan_inventory()`                    `buildInventory()` (:1957)
  ====================================  ==========================================

零侵入：只读磁盘上的 Wallpaper Engine / Steam 数据，不写入任何文件。
"""

import json
import os
import re
import threading
import time

# ---------------- 常量（与插件一致） ----------------
WE_APPID = '431960'

#: libraryfolders.vdf 缺失时的兜底探测目录（index.js:76 STEAM_PROBE_DIRS）
STEAM_PROBE_DIRS = (
    r'C:\Program Files (x86)\Steam',
    r'C:\Program Files\Steam',
    r'D:\Steam',
    r'D:\SteamLibrary',
    r'E:\SteamLibrary',
)

#: 插件允许的上传文件命名（index.js:1719）
UPLOAD_FILE_RE = re.compile(r'^(up-[a-z0-9-]+)\.(mp4|jpg|jpeg|png)$', re.I)

KINDS = ('scene', 'video', 'web', 'application')

_DATA_DIR = os.path.join(os.path.expanduser('~'), '.dsh-wallpaper-engine')
_CONFIG_FILE = os.path.join(_DATA_DIR, 'config.json')
DEFAULT_UPLOAD_DIR = os.path.join(_DATA_DIR, 'uploads')

#: 扫描结果缓存 TTL。插件用 3s（index.js:1955 INVENTORY_TTL_MS）；
#: 这里取略长值，因为后端只服务单个面板，不需要插件那样的高频刷新。
SCAN_TTL = 5.0


# ==========================================================================
# 1. Steam 与 Wallpaper Engine 定位
# ==========================================================================
def _registry_steam_root():
    """读 `HKCU\\Software\\Valve\\Steam` 的 `SteamPath`（对应 index.js:100）。

    插件走 `reg.exe query` 子进程；Python 直接用 `winreg`，更快且无子进程开销。
    非 Windows 或键不存在时返回 None。
    """
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
            value, _ = winreg.QueryValueEx(key, 'SteamPath')
    except OSError:
        return None
    return os.path.normpath(value) if value else None


def _env_steam_roots():
    """`DSH_WE_STEAM_ROOT` 环境变量（逗号/分号分隔，对应 index.js:123）。"""
    raw = (os.environ.get('DSH_WE_STEAM_ROOT') or '').strip()
    if not raw:
        return []
    return [p.strip() for p in re.split(r'[,;]', raw) if p.strip()]


# 探测列表缓存：注册表查询在部分环境要几百毫秒，且 locate/owning 都会调用。
# 与插件的 60s TTL 对齐（index.js:172 STEAM_PROBE_TTL_MS）。
_PROBE_CACHE = {'at': 0.0, 'dirs': None}
_PROBE_TTL = 60.0


def steam_probe_dirs():
    """候选 Steam 根目录：注册表 → 环境变量 → 硬编码候选（对应 index.js:177）。

    WSL 的 `/mnt/<盘>` 探测未移植 —— 本工具只跑在 Windows 上。
    """
    now = time.time()
    if _PROBE_CACHE['dirs'] is not None and (now - _PROBE_CACHE['at']) < _PROBE_TTL:
        return _PROBE_CACHE['dirs']

    ordered, seen = [], set()
    root = _registry_steam_root()
    candidates = ([root] if root else []) + _env_steam_roots() + list(STEAM_PROBE_DIRS)
    for path in candidates:
        key = os.path.normcase(os.path.normpath(path))
        if key not in seen:
            seen.add(key)
            ordered.append(path)

    _PROBE_CACHE['at'] = now
    _PROBE_CACHE['dirs'] = ordered
    return ordered


def _libraries_from_vdf(vdf_path):
    """解析 `libraryfolders.vdf`，返回含 WE_APPID 的库路径（对应 index.js:211）。

    只认 `"path" "X:\\..."` 行，随后在其后面的行里找 appid —— 与插件同款
    逐行扫描，不引入完整 VDF 解析器。
    """
    try:
        with open(vdf_path, encoding='utf-8', errors='replace') as handle:
            text = handle.read()
    except OSError:
        return []

    libraries, current = [], None
    for line in text.splitlines():
        match = re.match(r'^\s*"path"\s+"([^"]+)"\s*$', line)
        if match:
            current = match.group(1).replace('\\\\', '\\')
            continue
        if current and WE_APPID in line and current not in libraries:
            libraries.append(current)
    return libraries


def locate_wallpaper_engine():
    """定位 WE 安装目录（判据：存在 `wallpaper32.exe`，对应 index.js:228）。"""
    probes = steam_probe_dirs()
    libraries = []
    for probe in probes:
        vdf = os.path.join(probe, 'steamapps', 'libraryfolders.vdf')
        if os.path.isfile(vdf):
            libraries.extend(_libraries_from_vdf(vdf))

    candidates = [os.path.join(root, 'steamapps', 'common', 'wallpaper_engine')
                  for root in (probes + libraries)]
    candidates.append(r'C:\Program Files (x86)\Wallpaper Engine')

    seen = set()
    for raw in candidates:
        path = os.path.normpath(raw)
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        if os.path.isfile(os.path.join(path, 'wallpaper32.exe')):
            return path
    return None


def owning_libraries():
    """拥有 WE 的 Steam 库列表（对应 index.js:253）。

    **关键**：vdf 所在的 Steam 根自身也是一个库，但 vdf 不会把它列为 path 条目。
    若 WE 装在默认库，漏掉它会导致工坊内容全部消失、playlists 无法解析。
    """
    libraries = []
    for probe in steam_probe_dirs():
        vdf = os.path.join(probe, 'steamapps', 'libraryfolders.vdf')
        if os.path.isfile(vdf):
            libraries.extend(_libraries_from_vdf(vdf))
        if os.path.isfile(os.path.join(probe, 'steamapps', 'common',
                                       'wallpaper_engine', 'wallpaper32.exe')):
            libraries.append(probe)

    ordered, seen = [], set()
    for path in libraries:
        key = os.path.normcase(os.path.normpath(path))
        if key not in seen:
            seen.add(key)
            ordered.append(path)
    return ordered


# ==========================================================================
# 2. 壁纸枚举
# ==========================================================================
def _infer_type(filename):
    """按主文件扩展名推断类型（对应 index.js:270）。"""
    if re.search(r'\.(mp4|webm|mkv|avi|mov)$', filename, re.I):
        return 'video'
    if re.search(r'\.(html?|js)$', filename, re.I):
        return 'web'
    return 'scene'


def _read_project(directory):
    """读单个壁纸目录的 project.json（对应 index.js:278）。"""
    project_file = os.path.join(directory, 'project.json')
    if not os.path.isfile(project_file):
        return None
    try:
        with open(project_file, encoding='utf-8', errors='replace') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get('file'):
        return None

    declared = data.get('type')
    wp_type = declared.lower() if isinstance(declared, str) else _infer_type(data['file'])
    if wp_type not in KINDS:
        wp_type = 'scene'

    title = data.get('title')
    preview = data.get('preview')
    rating = data.get('contentrating')
    return {
        'id': os.path.basename(directory),
        'title': title if isinstance(title, str) and title else os.path.basename(directory),
        'type': wp_type,
        'file': data['file'],
        'preview': preview if isinstance(preview, str) else None,
        # WE 的 G / PG13 / R 分级直接透传，浏览器端据此复现 WE 的过滤。
        'contentrating': rating if isinstance(rating, str) else None,
    }


def _resolve_scene_main(directory, declared):
    """解析 scene 项目真正的主容器（对应 index.js:309）。

    工坊项目常声明 `scene.json` 却只打包了 `scene.pkg`（反之亦然），
    故按 声明值 → scene.pkg → scene.json → 目录内唯一 *.pkg 依次探测。
    """
    for candidate in (declared, 'scene.pkg', 'scene.json'):
        if candidate and os.path.isfile(os.path.join(directory, candidate)):
            return candidate
    try:
        pkgs = [n for n in os.listdir(directory) if n.lower().endswith('.pkg')]
    except OSError:
        return None
    return pkgs[0] if len(pkgs) == 1 else None


def _enumerate_wallpapers(install_dir, library_dirs):
    """枚举四个来源的全部壁纸（对应 index.js:327）。

    来源：`<installDir>/projects/{defaultprojects,myprojects}` +
    `<各库>/steamapps/workshop/content/431960`；上传目录单独处理。

    返回 (wallpapers, roots)，roots 记录各来源目录命中数量，便于诊断。
    """
    roots = []
    if install_dir:
        for sub in ('defaultprojects', 'myprojects'):
            path = os.path.join(install_dir, 'projects', sub)
            if os.path.isdir(path):
                roots.append(path)
    for library in library_dirs:
        workshop = os.path.join(library, 'steamapps', 'workshop', 'content', WE_APPID)
        if os.path.isdir(workshop):
            roots.append(workshop)

    found = {}
    counts = {}
    for root in roots:
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        hit = 0
        for entry in entries:
            directory = os.path.join(root, entry)
            if not os.path.isdir(directory):
                continue
            project = _read_project(directory)
            if not project or project['id'] in found:
                continue
            if project['type'] == 'scene':
                main = _resolve_scene_main(directory, project['file']) or project['file']
                project['file_abs'] = os.path.join(directory, main)
            else:
                project['file_abs'] = os.path.join(directory, project['file'])
            project['preview_abs'] = (os.path.join(directory, project['preview'])
                                      if project['preview'] else None)
            project['source'] = ('workshop' if 'workshop' in root
                                 else os.path.basename(root))
            found[project['id']] = project
            hit += 1
        counts[root] = hit

    ordered = sorted(found.values(), key=lambda w: (w['title'] or '').lower())
    return ordered, counts


def _path_key(path):
    """路径归一化键（对应 index.js:371 pathKey）。"""
    return os.path.normcase(os.path.normpath(str(path).replace('/', '\\')))


def _playlist_item_id(item, by_path, by_id):
    """把 playlist 里的一条路径解析成壁纸 id（对应 index.js:423）。"""
    exact = by_path.get(_path_key(item))
    if exact:
        return exact
    match = re.search(r'[\\/]431960[\\/]([^\\/]+)(?:[\\/]|$)', item, re.I)
    if match:
        project = by_id.get(match.group(1))
        if project:
            return project['id']
    # 兜底：用倒数第二段目录名匹配（覆盖 projects\defaultprojects\<name>\project.json
    # 这类安装相对路径 —— 它们不含工坊 appid）。
    folder = re.search(r'[\\/]([^\\/]+)[\\/][^\\/]+$', item)
    if folder and folder.group(1) in by_id:
        return folder.group(1)
    return None


def _read_playlists(install_dir):
    """读 WE 自己的 `<installDir>/config.json` 里的轮播列表（对应 index.js:390）。"""
    if not install_dir:
        return []
    config_file = os.path.join(install_dir, 'config.json')
    if not os.path.isfile(config_file):
        return []
    try:
        with open(config_file, encoding='utf-8', errors='replace') as handle:
            config = json.load(handle)
    except (OSError, ValueError):
        return []

    rows, seen = [], set()
    for profile_name, profile in (config or {}).items():
        general = profile.get('general') if isinstance(profile, dict) else None
        if not isinstance(general, dict):
            continue
        playlists = general.get('playlists') if isinstance(general.get('playlists'), list) else []
        if not playlists:
            selected = (general.get('wallpaperconfig') or {}).get('selectedwallpapers')
            if isinstance(selected, dict):
                playlists = [row.get('playlist') for row in selected.values()
                             if isinstance(row, dict) and isinstance(row.get('playlist'), dict)]

        for index, row in enumerate(playlists):
            if not isinstance(row, dict):
                continue
            items = [x for x in (row.get('items') or [])
                     if isinstance(x, str) and x.strip()]
            if not items:
                continue
            name = (row.get('name') or '').strip() or 'Playlist {0}'.format(index + 1)
            signature = '{0}\0{1}'.format(name, '\0'.join(items))
            if signature in seen:
                continue
            seen.add(signature)
            settings = row.get('settings') if isinstance(row.get('settings'), dict) else {}
            delay = settings.get('delay')
            # bool 是 int 子类，显式排除，避免 True 被当成延迟秒数。
            if isinstance(delay, bool) or not isinstance(delay, (int, float)):
                delay = None
            rows.append({
                'name': name,
                'items': items,
                'order': 'random' if settings.get('order') == 'random' else 'sequence',
                'delay': delay,
                '_profile': profile_name,
                '_index': index,
            })
    return rows


# ==========================================================================
# 3. 上传壁纸
# ==========================================================================
def upload_dir():
    """上传目录：环境变量 → config.json 的 uploadDir → 默认值（index.js:1569）。"""
    env = (os.environ.get('DSH_WE_UPLOAD_DIR') or '').strip()
    if env:
        return env
    try:
        with open(_CONFIG_FILE, encoding='utf-8', errors='replace') as handle:
            config = json.load(handle)
        configured = config.get('uploadDir') if isinstance(config, dict) else None
        if isinstance(configured, str) and configured.strip():
            return configured.strip()
    except (OSError, ValueError):
        pass
    return DEFAULT_UPLOAD_DIR


def _read_upload_meta(directory):
    """读上传目录的 `.meta.json`（标题 / 分级），对应 index.js:1728 + 1743。"""
    path = os.path.join(directory, '.meta.json')
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding='utf-8', errors='replace') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _meta_entry(meta, uid):
    """归一化一条 meta（兼容旧式 `{id: title}` 与新式 `{id: {...}}`）。"""
    value = meta.get(uid)
    if isinstance(value, str):
        return {'title': value, 'contentrating': None}
    if isinstance(value, dict):
        title = value.get('title')
        rating = value.get('contentrating')
        return {
            'title': title if isinstance(title, str) and title.strip() else uid,
            'contentrating': rating.strip() if isinstance(rating, str) and rating.strip() else None,
        }
    return {'title': uid, 'contentrating': None}


def _enumerate_uploads(directory):
    """扫描上传目录（对应 index.js:1777）。上传壁纸没有 project.json。"""
    if not os.path.isdir(directory):
        return []
    try:
        entries = os.listdir(directory)
    except OSError:
        return []

    out = []
    for entry in entries:
        full = os.path.join(directory, entry)
        if not os.path.isfile(full):
            continue
        match = UPLOAD_FILE_RE.match(entry)
        if not match:
            continue
        uid = match.group(1)
        ext = match.group(2).lower()
        kind = 'video' if ext == 'mp4' else 'image'
        out.append({
            'id': uid,
            'type': kind,
            'file_abs': full,
            'preview_abs': full if kind == 'image' else None,
        })
    out.sort(key=lambda w: w['id'])
    return out


# ==========================================================================
# 4. 对外主入口
# ==========================================================================
_SCAN_CACHE = {'at': 0.0, 'payload': None}
_SCAN_LOCK = threading.Lock()

#: id → {'media': abs|None, 'preview': abs|None}。随 scan_inventory 一起重建，
#: 供 media_file() 直接查表 —— 媒体请求远多于清单请求，不能每次重扫磁盘。
_PATH_INDEX = {}


def clear_cache():
    """丢弃扫描缓存（安装/删除壁纸后调用）。"""
    with _SCAN_LOCK:
        _SCAN_CACHE['at'] = 0.0
        _SCAN_CACHE['payload'] = None
        _PATH_INDEX.clear()
    _PROBE_CACHE['at'] = 0.0
    _PROBE_CACHE['dirs'] = None


def scan_inventory(use_cache=True):
    """扫描本机全部壁纸，返回与插件 `/inventory` 同构的 dict。

    结构（与插件 buildInventory 一致）::

        {
          'installDir': str|None,
          'uploadDir': str,
          'total': int,               # 壁纸总数
          'portableCount': int,       # 主文件存在的（可播放）数量
          'wallpapers': [ {id, title, type, contentrating, playable,
                           media, preview, frameUrl, sceneUrl, sceneVideo}, ... ],
          'playlists':  [ {id, name, order, delay, wallpaperIds, total,
                           portableCount, unresolvedCount}, ... ],
          'source': 'local',          # 本模块产出，区别于经 CDP 取得
          'scannedAt': float,
          'details': { 'roots': {...}, 'libraries': [...], 'uploads': int },
        }

    注意 `media` / `preview` 等字段返回的是 **本工具自己的 asset 路径**
    （`/api/wallpaper/asset/<kind>/<id>`），不是插件的 token 路由 ——
    媒体字节由 DSH++ 后端直接供给，不经过 DSH。
    """
    with _SCAN_LOCK:
        now = time.time()
        if use_cache and _SCAN_CACHE['payload'] is not None \
                and (now - _SCAN_CACHE['at']) < SCAN_TTL:
            return _SCAN_CACHE['payload']

    install_dir = locate_wallpaper_engine()
    libraries = owning_libraries()
    wallpapers, root_counts = _enumerate_wallpapers(install_dir, libraries)

    entries = []
    index = {}
    for item in wallpapers:
        has_media = bool(item['file_abs']) and os.path.isfile(item['file_abs'])
        has_preview = bool(item['preview_abs']) and os.path.isfile(item['preview_abs'])
        entries.append(_shape(item['id'], item['title'], item['type'],
                              item['contentrating'], has_media, has_preview,
                              has_frame=item['type'] == 'scene' and has_media,
                              source=item.get('source')))
        index[item['id']] = {
            'media': item['file_abs'] if has_media else None,
            'preview': item['preview_abs'] if has_preview else None,
        }

    up_dir = upload_dir()
    uploads = _enumerate_uploads(up_dir)
    meta = _read_upload_meta(up_dir)
    for item in uploads:
        info = _meta_entry(meta, item['id'])
        has_preview = bool(item['preview_abs']) and os.path.isfile(item['preview_abs'])
        entries.append(_shape(item['id'], info['title'], item['type'],
                              info['contentrating'], True,
                              has_preview, has_frame=False,
                              source='uploads'))
        index[item['id']] = {
            'media': item['file_abs'],
            'preview': item['preview_abs'] if has_preview else None,
        }

    by_path = {_path_key(w['file_abs']): w['id'] for w in wallpapers}
    by_id = {w['id']: w for w in wallpapers}
    playable = {w['id'] for w in entries if w['playable']}

    playlists = []
    for row in _read_playlists(install_dir):
        ids, seen_ids = [], set()
        for raw in row['items']:
            resolved = _playlist_item_id(raw, by_path, by_id)
            if resolved and resolved not in seen_ids:
                seen_ids.add(resolved)
                ids.append(resolved)
        playlists.append({
            'id': '{0}:{1}:{2}'.format(row['_profile'], row['_index'], row['name']),
            'name': row['name'],
            'order': row['order'],
            'delay': row['delay'],
            'wallpaperIds': ids,
            'total': len(ids),
            'portableCount': len([i for i in ids if i in playable]),
            'unresolvedCount': max(0, len(row['items']) - len(ids)),
        })

    payload = {
        'installDir': install_dir,
        'uploadDir': up_dir,
        'total': len(entries),
        'portableCount': len(playable),
        'wallpapers': entries,
        'playlists': playlists,
        'source': 'local',
        'scannedAt': time.time(),
        'details': {
            'roots': root_counts,
            'libraries': libraries,
            'uploads': len(uploads),
        },
    }

    with _SCAN_LOCK:
        _SCAN_CACHE['at'] = time.time()
        _SCAN_CACHE['payload'] = payload
        _PATH_INDEX.clear()
        _PATH_INDEX.update(index)
    return payload


def _shape(wid, title, wp_type, rating, has_media, has_preview, has_frame,
           source=None):
    """组装单条壁纸，媒体指向本工具的 asset 路由。

    `source` 标注该条目来自哪个来源目录（插件侧叫 `workshop` / `defaultprojects`
    / `myprojects`），面板与 CLI 体检都靠它分类展示，故必须透传。
    """
    asset = '/api/wallpaper/asset/'
    return {
        'id': wid,
        'title': title or wid,
        'type': wp_type,
        'contentrating': rating,
        'source': source,
        'playable': bool(has_media),
        'media': (asset + 'media/' + wid) if has_media else None,
        'preview': (asset + 'preview/' + wid) if has_preview else None,
        # scene 的静态抽帧 / 实时播放器由插件侧渲染，本工具不实现 —— 留 None，
        # 前端回落到 preview。
        'frameUrl': None,
        'sceneUrl': None,
        'sceneVideo': None,
    }


#: asset 路由的 id 白名单：壁纸 id 是目录名或 up-xxx，不含路径分隔符。
_ID_RE = re.compile(r'^[A-Za-z0-9._-]{1,128}$')


def media_file(wid, kind='media'):
    """把壁纸 id 解析成本地文件绝对路径，供 server 供字节。

    :param wid: 壁纸 id（`wallpapers[].id`）
    :param kind: `'media'` 主文件 / `'preview'` 预览图
    :returns: `(abs_path, mime)`；查不到或 id 非法时 `(None, None)`

    安全性：id 必须过白名单正则（不含路径分隔符 / `..`），且只从**扫描到的
    壁纸路径表**里取 —— 不接受调用方传入的任意路径，天然免疫路径穿越。
    """
    if not isinstance(wid, str) or not _ID_RE.match(wid):
        return None, None
    if kind not in ('media', 'preview'):
        return None, None

    scan_inventory()  # 保证索引新鲜（TTL 内直接复用）
    with _SCAN_LOCK:
        entry = _PATH_INDEX.get(wid)
        path = entry.get(kind) if entry else None

    if not path or not os.path.isfile(path):
        # 壁纸可能在两次扫描之间被删除，按不存在处理。
        return None, None
    return path, _mime_for(path)


_MIME = {
    'mp4': 'video/mp4', 'webm': 'video/webm', 'mkv': 'video/x-matroska',
    'avi': 'video/x-msvideo', 'mov': 'video/quicktime',
    'html': 'text/html', 'htm': 'text/html', 'js': 'text/javascript',
    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'gif': 'image/gif',
    'png': 'image/png', 'webp': 'image/webp', 'apng': 'image/apng',
}


def _mime_for(path):
    """按扩展名给 MIME（对应 index.js:437 mimeFor）。"""
    ext = path.rsplit('.', 1)[-1].lower() if '.' in path else ''
    return _MIME.get(ext, 'application/octet-stream')


def find_wallpaper(wid):
    """按 id 取单条壁纸（已扫描结果内查找）。"""
    if not isinstance(wid, str) or not _ID_RE.match(wid):
        return None
    for item in scan_inventory()['wallpapers']:
        if item['id'] == wid:
            return item
    return None
