"""概念验证：完全脱离 DSH/CDP，纯文件系统枚举 Wallpaper Engine 壁纸。

逻辑逐行对照插件的 lib/index.js：
  steamProbeDirsP / locateWallpaperEngineP / owningLibrariesP
  librariesFromVdfP / readProjectP / enumerateWallpapersAsync / readPlaylistsP
仅用于验证，不修改任何工程文件。
"""
import json
import os
import re
import sys

WE_APPID = '431960'
STEAM_PROBE_DIRS = [
    r'C:\Program Files (x86)\Steam',
    r'C:\Program Files\Steam',
    r'D:\Steam',
    r'D:\SteamLibrary',
    r'E:\SteamLibrary',
]
KINDS = ['scene', 'video', 'web', 'application']


def registry_steam_root():
    """对应 steamPathFromRegistryP()：HKCU\\Software\\Valve\\Steam 的 SteamPath。"""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as k:
            val, _ = winreg.QueryValueEx(k, 'SteamPath')
            return os.path.normpath(val) if val else None
    except OSError:
        return None


def env_steam_roots():
    """对应 steamRootsFromEnv()：DSH_WE_STEAM_ROOT，逗号/分号分隔。"""
    raw = (os.environ.get('DSH_WE_STEAM_ROOT') or '').strip()
    if not raw:
        return []
    return [p.strip() for p in re.split(r'[,;]', raw) if p.strip()]


def steam_probe_dirs():
    """对应 steamProbeDirsP()（Windows 分支，无 WSL）。"""
    out = []
    reg = registry_steam_root()
    if reg:
        out.append(reg)
    out.extend(env_steam_roots())
    out.extend(STEAM_PROBE_DIRS)
    seen, uniq = set(), []
    for d in out:
        k = os.path.normcase(os.path.normpath(d))
        if k not in seen:
            seen.add(k)
            uniq.append(d)
    return uniq


def libraries_from_vdf(vdf_path):
    """对应 librariesFromVdfP()：解析 libraryfolders.vdf，取含 WE_APPID 的库。"""
    try:
        with open(vdf_path, encoding='utf-8', errors='replace') as f:
            text = f.read()
    except OSError:
        return []
    libs, current = [], None
    for line in text.splitlines():
        m = re.match(r'^\s*"path"\s+"([^"]+)"\s*$', line)
        if m:
            current = m.group(1).replace('\\\\', '\\')
            continue
        if current and WE_APPID in line:
            if current not in libs:
                libs.append(current)
    return libs


def locate_wallpaper_engine():
    """对应 locateWallpaperEngineP()：找含 wallpaper32.exe 的目录。"""
    probes = steam_probe_dirs()
    libraries = []
    for probe in probes:
        vdf = os.path.join(probe, 'steamapps', 'libraryfolders.vdf')
        if os.path.exists(vdf):
            libraries.extend(libraries_from_vdf(vdf))
    roots = probes + libraries
    cands = [os.path.join(r, 'steamapps', 'common', 'wallpaper_engine') for r in roots]
    cands.append(r'C:\Program Files (x86)\Wallpaper Engine')
    seen = set()
    for raw in cands:
        d = os.path.normpath(raw)
        if d in seen:
            continue
        seen.add(d)
        if os.path.exists(os.path.join(d, 'wallpaper32.exe')):
            return d
    return None


def owning_libraries():
    """对应 owningLibrariesP()：库列表 + vdf 所在 Steam 根自身。"""
    libs = []
    for probe in steam_probe_dirs():
        vdf = os.path.join(probe, 'steamapps', 'libraryfolders.vdf')
        if os.path.exists(vdf):
            libs.extend(libraries_from_vdf(vdf))
        if os.path.exists(os.path.join(probe, 'steamapps', 'common', 'wallpaper_engine')):
            libs.append(probe)
    seen, uniq = set(), []
    for l in libs:
        k = os.path.normcase(os.path.normpath(l))
        if k not in seen:
            seen.add(k)
            uniq.append(l)
    return uniq


def infer_type(f):
    if re.search(r'\.(mp4|webm|mkv|avi|mov)$', f, re.I):
        return 'video'
    if re.search(r'\.(html?|js)$', f, re.I):
        return 'web'
    return 'scene'


def read_project(d):
    """对应 readProjectP()：读 project.json。"""
    pj = os.path.join(d, 'project.json')
    if not os.path.isfile(pj):
        return None
    try:
        with open(pj, encoding='utf-8', errors='replace') as f:
            o = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(o, dict) or not o.get('file'):
        return None
    t = o.get('type')
    t = t.lower() if isinstance(t, str) else infer_type(o['file'])
    if t not in KINDS:
        t = 'scene'
    return {
        'id': os.path.basename(d),
        'title': o.get('title') if isinstance(o.get('title'), str) else os.path.basename(d),
        'type': t,
        'file': o.get('file'),
        'preview': o.get('preview') if isinstance(o.get('preview'), str) else None,
        'contentrating': o.get('contentrating') if isinstance(o.get('contentrating'), str) else None,
    }


def resolve_scene_main(d, declared):
    """对应 resolveSceneMainFileP()。"""
    for c in [declared, 'scene.pkg', 'scene.json']:
        if c and os.path.isfile(os.path.join(d, c)):
            return c
    try:
        pkgs = [n for n in os.listdir(d) if n.lower().endswith('.pkg')]
    except OSError:
        return None
    return pkgs[0] if len(pkgs) == 1 else None


def enumerate_wallpapers(install_dir, library_dirs):
    """对应 enumerateWallpapersAsync()。"""
    found, roots = {}, []
    if install_dir:
        for sub in ('defaultprojects', 'myprojects'):
            p = os.path.join(install_dir, 'projects', sub)
            if os.path.isdir(p):
                roots.append(p)
    for lib in library_dirs:
        ws = os.path.join(lib, 'steamapps', 'workshop', 'content', WE_APPID)
        if os.path.isdir(ws):
            roots.append(ws)

    project_dirs, per_root = [], {}
    for root in roots:
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        n = 0
        for e in entries:
            d = os.path.join(root, e)
            if os.path.isdir(d):
                project_dirs.append(d)
                n += 1
        per_root[root] = n

    for d in project_dirs:
        p = read_project(d)
        if not p or p['id'] in found:
            continue
        if p['type'] == 'scene':
            main = resolve_scene_main(d, p['file']) or p['file']
            p['file_abs'] = os.path.join(d, main)
        else:
            p['file_abs'] = os.path.join(d, p['file'])
        p['preview_abs'] = os.path.join(d, p['preview']) if p['preview'] else None
        p['source_root'] = next((r for r in roots if d.startswith(r)), '?')
        found[p['id']] = p
    return [found[k] for k in sorted(found, key=lambda i: (found[i]['title'] or ''))], per_root


def read_playlists(install_dir):
    """对应 readPlaylistsP()：读 WE 自己的 config.json。"""
    if not install_dir:
        return []
    cp = os.path.join(install_dir, 'config.json')
    if not os.path.isfile(cp):
        return []
    try:
        with open(cp, encoding='utf-8', errors='replace') as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return []
    rows = []
    for prof_name, prof in (cfg or {}).items():
        gen = prof.get('general') if isinstance(prof, dict) else None
        if not isinstance(gen, dict):
            continue
        playlists = gen.get('playlists') if isinstance(gen.get('playlists'), list) else []
        if not playlists:
            sel = (gen.get('wallpaperconfig') or {}).get('selectedwallpapers')
            if isinstance(sel, dict):
                playlists = [m.get('playlist') for m in sel.values()
                             if isinstance(m, dict) and isinstance(m.get('playlist'), dict)]
        for i, row in enumerate(playlists):
            if not isinstance(row, dict):
                continue
            items = [x for x in (row.get('items') or []) if isinstance(x, str) and x.strip()]
            if not items:
                continue
            rows.append({
                'name': (row.get('name') or '').strip() or 'Playlist %d' % (i + 1),
                'count': len(items),
                'order': ((row.get('settings') or {}).get('order') or 'sequence'),
            })
    return rows


def main():
    print('=' * 78)
    print('纯文件系统枚举（零 DSH / 零 CDP / 零 HTTP）')
    print('=' * 78)

    probes = steam_probe_dirs()
    print('\n[1] Steam 探测目录 (%d)' % len(probes))
    print('    注册表 HKCU\\Software\\Valve\\Steam\\SteamPath :', registry_steam_root())
    print('    环境变量 DSH_WE_STEAM_ROOT                    :', env_steam_roots() or '(未设置)')
    for p in probes:
        print('      -', p, '(存在)' if os.path.isdir(p) else '(无)')

    install = locate_wallpaper_engine()
    print('\n[2] Wallpaper Engine 安装目录')
    print('    ->', install or '(未找到)')

    libs = owning_libraries()
    print('\n[3] owned Steam 库 (%d)' % len(libs))
    for l in libs:
        print('      -', l)

    print('\n[4] 枚举壁纸')
    walls, per_root = enumerate_wallpapers(install, libs)
    for root, n in per_root.items():
        print('      %-72s %d 项' % (root, n))
    print('    合计唯一壁纸: %d' % len(walls))

    by_type, by_source = {}, {}
    for w in walls:
        by_type[w['type']] = by_type.get(w['type'], 0) + 1
        src = 'workshop' if 'workshop' in w['source_root'] else (
            'defaultprojects' if 'defaultprojects' in w['source_root'] else 'myprojects')
        by_source[src] = by_source.get(src, 0) + 1
    print('    按类型:', by_type)
    print('    按来源:', by_source)

    print('\n[5] 前 12 项（id | title | type | rating | 主文件在)')
    for w in walls[:12]:
        print('      %-12s %-34s %-7s %-10s %s' % (
            w['id'], (w['title'] or '')[:32], w['type'],
            w['contentrating'] or '-', 'Y' if os.path.isfile(w['file_abs']) else 'N'))

    print('\n[6] 当前在用壁纸 id 是否在列表中')
    try:
        with open(os.path.join(os.path.expanduser('~'), '.dsh-wallpaper-engine', 'config.json'),
                  encoding='utf-8') as f:
            cur = (json.load(f).get('settings') or {}).get('id')
        hit = next((w for w in walls if w['id'] == cur), None)
        print('      settings.id = %s -> %s' % (cur, ('命中: ' + hit['title']) if hit else '未命中'))
    except Exception as e:
        print('      读取失败:', e)

    print('\n[7] playlists（来自 WE 自身 config.json）')
    pl = read_playlists(install)
    if pl:
        for r in pl:
            print('      -', r)
    else:
        print('      (无)')


if __name__ == '__main__':
    main()
