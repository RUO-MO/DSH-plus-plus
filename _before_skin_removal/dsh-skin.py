# -*- coding: utf-8 -*-
"""DeepSeek Harness 主题包工具（DSH++，原 DSHSkin）

用法:
  python dsh-skin.py install <主题包.zip> [--dry-run]
  python dsh-skin.py list                    列出已安装主题
  python dsh-skin.py switch <id>             切换主题（CDP 热生效）
  python dsh-skin.py params <id> [--json ...] 查看/更新主题参数
  python dsh-skin.py rebuild <id>            用当前模板+已存参数重生成 skin.css（升级失效后修复）
  python dsh-skin.py export <id> -o out.zip  导出主题包
  python dsh-skin.py restore                 还原官方样式（移除 CDP 皮肤与运行时）
  python dsh-skin.py remove <id>             删除已安装主题
  python dsh-skin.py pack <meta> <bg> <css> -o out.zip   打包主题包
  python dsh-skin.py cleanup                 清理孤儿目录/图片
  python dsh-skin.py launch                  启动 DeepSeek Harness 桌面版（开发模式，自带 CDP 9222）
  python dsh-skin.py detect                  探测环境（CDP 端口 / 进程 / 启动器）
  python dsh-skin.py deps [--install]        检查（或安装）运行依赖
  python dsh-skin.py doctor                  综合体检：环境/依赖/CDP 通道/选择器
  python dsh-skin.py probe [--apply]         CDP 实测选择器存活，--apply 写入覆盖以适配新版
  python dsh-skin.py selectors [--set key=sel] [--reset]   查看/手工设置/清空选择器覆盖
  python dsh-skin.py enhance [--preset builtin|scripts|full] [--enable k] [--apply]
                                              增强器：用户脚本 / 用户 CSS / 模块开关
  python dsh-skin.py template [-o out.css] [--appearance light|dark] [--json ...]
  python dsh-skin.py --version               显示版本

主题包格式（zip）:
  meta.json        {"id":"keus","name":"Keus","version":"1.0.0","appearance":"light","image":"background.png"}
  background.png  背景图（PNG/JPG/WebP，≤20MB，可选：纯 CSS 主题可不带）
  skin.css        追加 CSS；背景图文件名用 {{IMAGE}} 占位，安装时替换为实际名

注入通道：唯一 CDP（渲染进程自带 --remote-debugging-port=9222），
不修改 deepseek-harness 任何文件；皮肤与增强全部可逆。
"""
import argparse
import json
import os
import re
import shutil
import zipfile

import cdp_skin
import dsh_env
import enhance_engine
import theme_engine as te

__version__ = '1.0.0'

# ---------------- 路径（统一走 dsh_env） ----------------
ROOT = os.path.dirname(os.path.abspath(__file__))   # .bat 等随附文件所在目录
SKIN_ROOT = dsh_env.SKIN_ROOT
CONFIG = dsh_env.CONFIG
MAX_ZIP_SIZE = 50 * 1024 * 1024      # zip 包上限 50MB
MAX_IMG_SIZE = 20 * 1024 * 1024      # 背景图上限 20MB

ensure_dirs = dsh_env.ensure_dirs
load_config = dsh_env.load_config
save_config = dsh_env.save_config
update_config = dsh_env.update_config


def _channel(cfg):
    return 'cdp'   # DSH 唯一注入通道


# ---------------- 核心 ----------------
def _migrate_theme(cfg, tid):
    """把过期主题的 skin_css 按当前模板重建（保留用户参数/外观/背景图）。"""
    t = cfg.get('themes', {}).get(tid)
    if not t:
        return False
    css = t.get('skin_css') or ''
    if not te.css_needs_rebuild(css):
        return False
    old_v = te.css_template_version(css)
    t['skin_css'] = te.generate_css(t.get('params') or te.DEFAULT_PARAMS,
                                    t.get('image'), t.get('appearance', 'light'))
    t['params'] = te.norm_params(t.get('params'))
    print('[i] 主题「{0}」样式模板已升级 v{1} → v{2}（按已存参数重建）'.format(
        t.get('name', tid), old_v, te.TEMPLATE_VERSION))
    return True


def migrate_all_themes(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    migrated = [tid for tid in list(cfg.get('themes', {})) if _migrate_theme(cfg, tid)]
    if migrated:
        save_config(cfg)
    return migrated


def migrate_cmd():
    cfg = load_config()
    migrated = migrate_all_themes(cfg)
    if not migrated:
        print('[OK] 所有主题均为当前模板 v{0}，无需迁移'.format(te.TEMPLATE_VERSION))
        return
    print('[OK] 已升级 {0} 个主题: {1}'.format(len(migrated), ', '.join(migrated)))
    print('     运行 `python dsh-skin.py switch <id>` 或点面板「立即注入」使新样式生效')


def apply_theme(cfg, tid, channel=None):
    """把主题 tid 设为激活（写入 config；实际注入走 CDP，由 enhance_bundle 统一生成）。"""
    cfg['active'] = tid
    if _migrate_theme(cfg, tid):
        save_config(cfg)
    save_config(cfg)
    return 'cdp'


def _check_image_magic(b):
    """返回图片格式名（png/jpg/webp），非法返回 None"""
    if b[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if b[:2] == b'\xff\xd8':
        return 'jpg'
    if b[:4] == b'RIFF' and b[8:12] == b'WEBP':
        return 'webp'
    return None


def _validate_meta(meta):
    tid = meta.get('id')
    if not tid or not re.match(r'^[a-z0-9_-]+$', tid):
        raise SystemExit('meta.json 的 id 必须为小写字母/数字/-/_')
    name = meta.get('name')
    if not name or not str(name).strip():
        raise SystemExit('meta.json 缺少 name 字段')
    version = str(meta.get('version') or '1.0.0')
    appearance = meta.get('appearance') or 'both'
    if appearance not in ('light', 'dark', 'both'):
        raise SystemExit('meta.json 的 appearance 只能为 light / dark / both（both=明暗双变体跟随系统）')
    params = meta.get('params')
    if params is not None and not isinstance(params, dict):
        raise SystemExit('meta.json 的 params 必须为对象')
    return tid, str(name).strip(), version, appearance, params


def _load_json_bytes(b):
    return json.loads(b.decode('utf-8-sig'))


def install(zip_path, dry_run=False):
    zsize = os.path.getsize(zip_path) if os.path.isfile(zip_path) else 0
    if zsize > MAX_ZIP_SIZE:
        raise SystemExit('主题包超过 {0}MB 上限'.format(MAX_ZIP_SIZE // 1024 // 1024))
    with zipfile.ZipFile(zip_path) as z:
        infos = z.infolist()
        if len(infos) > 4000:
            raise SystemExit('主题包成员数超过 4000 上限（疑似解压炸弹）')
        total = 0
        for i in infos:
            if i.file_size > MAX_ZIP_SIZE:
                raise SystemExit('主题包内单文件超过 {0}MB 上限'.format(MAX_ZIP_SIZE // 1024 // 1024))
            total += i.file_size
            if i.compress_size > 0 and i.file_size > 4096 and i.file_size / i.compress_size > 100:
                raise SystemExit('主题包压缩比异常（>100），疑似解压炸弹，已拒绝')
        if total > MAX_ZIP_SIZE:
            raise SystemExit('主题包解压后超过 50MB 上限')
        names = set(z.namelist())
        for need in ('meta.json', 'skin.css'):
            if need not in names:
                raise SystemExit('主题包缺少 {0}'.format(need))
        meta = _load_json_bytes(z.read('meta.json'))
        tid, name, version, appearance, params = _validate_meta(meta)
        builtin = bool(meta.get('builtin', False))
        img_bytes = z.read('background.png') if 'background.png' in names else None
        skin_css = z.read('skin.css').decode('utf-8')
        if img_bytes is not None:
            if len(img_bytes) > MAX_IMG_SIZE:
                raise SystemExit('background.png 超过 20MB 上限')
            if _check_image_magic(img_bytes) is None:
                raise SystemExit('background.png 不是有效图片（仅支持 PNG/JPG/WebP）')
        if not skin_css.strip():
            raise SystemExit('skin.css 内容为空')
    stale = te.theme_staleness(skin_css)
    if dry_run:
        print('[OK] 主题包校验通过: 「{0}」 v{1} ({2})'.format(name, version, appearance)
              + ('，含 params 参数' if params else ''))
        if stale:
            print('[!] 该主题含已失效类名: {0}'.format(', '.join(stale)))
            print('    安装后可用 `python dsh-skin.py rebuild {0}` 按新版界面重生成样式'.format(tid))
        return
    ensure_dirs()
    tdir = os.path.join(SKIN_ROOT, tid)
    os.makedirs(tdir, exist_ok=True)
    img_path = os.path.join(tdir, 'background.png')
    if img_bytes is not None:
        with open(img_path, 'wb') as f:
            f.write(img_bytes)
    elif os.path.exists(img_path):
        os.remove(img_path)
    with open(os.path.join(tdir, 'skin.css'), 'w', encoding='utf-8') as f:
        f.write(skin_css)
    # CDP 注入时背景图会内嵌 data URI，这里只需记录 image 名并替换 {{IMAGE}} 占位
    target_img = 'background.png' if img_bytes is not None else None
    skin_css_final = re.sub(
        r'url\(\s*["\']?\{\{IMAGE\}\}["\']?\s*\)|\{\{IMAGE\}\}',
        lambda m: 'url("background.png")' if 'url' in m.group(0) else (target_img or 'none'),
        skin_css)
    cfg = load_config()
    existed = tid in cfg.get('themes', {})
    final_params = te.norm_params(
        params if params is not None else (cfg['themes'][tid].get('params') if existed else None))
    cfg['themes'][tid] = {
        "name": name, "version": version, "appearance": appearance,
        "image": target_img, "skin_css": skin_css_final, "params": final_params,
    }
    if builtin:
        cfg['themes'][tid]['builtin'] = True
    apply_theme(cfg, tid)
    print('[OK] 主题「{0}」已{1}启用 (v{2}, {3})'.format(name, '更新并' if existed else '安装并', version, appearance))
    if stale:
        print('[!] 检测到失效类名: {0}'.format(', '.join(stale)))
        print('    建议: python dsh-skin.py rebuild {0}'.format(tid))
    _post_apply_note()


def _post_apply_note():
    if cdp_skin.WEBSOCKET_AVAILABLE:
        print('     CDP 通道：面板点「立即注入」或 `python dsh-skin.py enhance --apply` 可热生效，无需重启')
    else:
        print('     CDP 通道不可用（缺 websocket-client）: python dsh-skin.py deps --install')
    print('     还原: python dsh-skin.py restore | 列表: python dsh-skin.py list')


def switch(tid, channel=None):
    def mut(cfg):
        if tid not in cfg.get('themes', {}):
            raise SystemExit('未安装主题 {0}（可用: {1}）'.format(tid, ', '.join(cfg.get('themes', {})) or '无'))
        if 'params' not in cfg['themes'][tid]:
            cfg['themes'][tid]['params'] = dict(te.DEFAULT_PARAMS)
        apply_theme(cfg, tid, channel)
        return cfg['themes'][tid]['name']
    name = update_config(mut)
    print('[OK] 已切换到「{0}」'.format(name))
    _post_apply_note()


def update_params(tid, params):
    """更新主题参数 → 重新生成 skin.css → 重新激活（版本小 bump）。原子读改写。"""
    def mut(cfg):
        if tid not in cfg.get('themes', {}):
            raise SystemExit('未安装主题 {0}'.format(tid))
        t = cfg['themes'][tid]
        t['skin_css'] = te.generate_css(params, t['image'], t.get('appearance', 'light'))
        t['params'] = te.norm_params(params)
        ver = t.get('version', '1.0.0')
        parts = ver.split('.')
        try:
            parts[-1] = str(int(parts[-1]) + 1)
            t['version'] = '.'.join(parts)
        except (IndexError, ValueError):
            t['version'] = ver + '.1'
        apply_theme(cfg, tid)
        return (t['name'], t['version'])
    name, ver = update_config(mut)
    print('[OK] 已更新「{0}」参数并重新激活 (v{1})'.format(name, ver))


def rebuild(tid, channel=None):
    """用当前模板 + 该主题已存参数/外观 重新生成 skin.css —— 用于 DSH 升级后适配。原子读改写。"""
    def mut(cfg):
        if tid not in cfg.get('themes', {}):
            raise SystemExit('未安装主题 {0}'.format(tid))
        t = cfg['themes'][tid]
        stale = te.theme_staleness(t.get('skin_css', ''))
        t['skin_css'] = te.generate_css(t.get('params') or te.DEFAULT_PARAMS,
                                        t['image'], t.get('appearance', 'light'))
        t['params'] = te.norm_params(t.get('params'))
        apply_theme(cfg, tid, channel)
        return (t['name'], stale)
    name, old_stale = update_config(mut)
    if old_stale:
        print('[OK] 已按新版界面重建「{0}」，移除失效类名: {1}'.format(name, ', '.join(old_stale)))
    else:
        print('[OK] 已按当前模板重建「{0}」（原样式不含已知失效类名）'.format(name))
    _post_apply_note()


def restore():
    """还原官方样式：移除 CDP 皮肤、运行时与强制暗色标记（不动任何官方文件）。"""
    ensure_dirs()
    update_config(lambda cfg: cfg.__setitem__('active', None))
    cdp_note = ''
    if cdp_skin.WEBSOCKET_AVAILABLE:
        r = cdp_skin.remove_css()
        if r.get('ok'):
            cdp_note = '，CDP 皮肤与运行时已移除'
    print('[OK] 已还原官方样式' + cdp_note)


def remove(tid):
    def mut(cfg):
        if tid not in cfg.get('themes', {}):
            raise SystemExit('未安装主题 {0}'.format(tid))
        was_active = cfg.get('active') == tid
        del cfg['themes'][tid]
        if was_active:
            cfg['active'] = None
        return was_active
    was_active = update_config(mut)
    if was_active:
        # active 已清空；再移除页面上的 CDP 皮肤（restore 内部不会重复清 active）
        if cdp_skin.WEBSOCKET_AVAILABLE:
            cdp_skin.remove_css()
    shutil.rmtree(os.path.join(SKIN_ROOT, tid), ignore_errors=True)
    print('[OK] 已删除主题 {0}'.format(tid))


def list_themes():
    cfg = load_config()
    if not cfg.get('themes'):
        print('未安装任何主题')
        return
    for tid, t in cfg['themes'].items():
        mark = ' *' if cfg.get('active') == tid else '  '
        stale = te.theme_staleness(t.get('skin_css', ''))
        flag = '  [!] 选择器失效: {0}'.format(', '.join(stale)) if stale else ''
        print('{0}{1}  {2}  v{3}  ({4}){5}'.format(
            mark, tid, t['name'], t['version'], t.get('appearance', 'light'), flag))


def export_theme(tid, out):
    cfg = load_config()
    if tid not in cfg.get('themes', {}):
        raise SystemExit('未安装主题 {0}（可用: {1}）'.format(tid, ', '.join(cfg.get('themes', {})) or '无'))
    t = cfg['themes'][tid]
    img = os.path.join(SKIN_ROOT, tid, 'background.png')
    has_img = bool(t.get('image')) and os.path.isfile(img)
    css = t['skin_css']
    if t.get('image'):
        css = css.replace(t['image'], '{{IMAGE}}')
    meta = {"id": tid, "name": t['name'], "version": t['version'],
            "appearance": t.get('appearance', 'light')}
    if has_img:
        meta['image'] = 'background.png'
    if t.get('params'):
        meta['params'] = t['params']
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('meta.json', json.dumps(meta, ensure_ascii=False, indent=2))
        if has_img:
            with open(img, 'rb') as f:
                z.writestr('background.png', f.read())
        z.writestr('skin.css', css)
    print('[OK] 已导出主题包: {0}'.format(out))


def cleanup():
    """清理孤儿数据：config 之外的主题目录、无主题引用的图片"""
    cfg = load_config()
    removed = []
    if os.path.isdir(SKIN_ROOT):
        for name in os.listdir(SKIN_ROOT):
            p = os.path.join(SKIN_ROOT, name)
            if os.path.isdir(p) and name not in cfg.get('themes', {}) and name not in ('enhance',):
                shutil.rmtree(p, ignore_errors=True)
                removed.append('孤儿目录 {0}'.format(name))
    print('[OK] ' + ('清理完成: ' + '、'.join(removed) if removed else '无需清理，环境干净'))


def uninstall(purge=False, port=None):
    """完整卸载（可逆优先）：
    1) CDP 移除页面皮肤/增强注入；2) 还原 cordis.patch.yml 托管块；
    purge=True 时再删除整个 ~/.dsh-skins 数据目录（主题/脚本/备份/凭据备份一并清掉）。
    返回结构化结果，供 CLI 与面板共用。
    """
    result = {'injection_removed': [], 'patch_restored': {}, 'purged': False, 'notes': []}
    # 1) 移除所有 DSH 窗口里的注入
    try:
        r = cdp_skin.remove_css(port=port or dsh_env.cdp_port())
        result['injection_removed'] = r.get('targets', []) if isinstance(r, dict) else []
    except Exception as e:
        result['notes'].append('移除注入时出错（DSH 未运行可忽略）: {0}'.format(e))
    # 2) 还原 cordis 托管块（.orig 精确还原 / 无备份则剥离托管段）
    try:
        import plugin_manager
        result['patch_restored'] = plugin_manager.restore_managed(disable_registry=True)
    except Exception as e:
        result['notes'].append('还原托管块出错: {0}'.format(e))
    # 3) 可选：清除全部用户数据
    if purge:
        try:
            if os.path.isdir(SKIN_ROOT):
                shutil.rmtree(SKIN_ROOT, ignore_errors=False)
            result['purged'] = not os.path.isdir(SKIN_ROOT)
        except Exception as e:
            result['notes'].append('删除数据目录失败（请先关闭面板/退出程序后重试）: {0}'.format(e))
    result['notes'].append('程序文件本身不会自动删除，请手动移除 DSH++ 程序目录与快捷方式')
    return result


def detect():
    """打印环境探测报告（模式 / exe / CDP / 进程 / 启动器 / 数据根）"""
    r = dsh_env.detect_report()
    print('数据目录: {0}   （config 存在: {1}）'.format(r['skin_root'], r['config_exists']))
    print('启动模式: {0}'.format(r['launcher_mode']))
    if r['launcher_mode'] == 'packaged':
        print('打包版 exe: {0}   （来源: {1}）'.format(r['desktop_exe'] or '未找到！',
                                                r['desktop_exe_source'] or '—'))
        if not r['desktop_exe']:
            print('          提示: 设 DSH_SKIN_DESKTOP_EXE / config.desktop_exe，'
                  '或先运行打包脚本 .desktop-build/run-package-win-dir.cmd')
    else:
        print('Harness 根目录: {0}'.format(r['harness_root'] or '未找到！设 DSH_SKIN_HARNESS_ROOT 或 config.dsh_root'))
    print('DSH 数据根: {0}   （来源: {1}）'.format(r['dsh_home'] or '未解析', r['dsh_home_source'] or '—'))
    print('CDP 端口: {0}   监听: {1}   target: {2}'.format(
        r['cdp_port'], r['cdp_ready'], r['cdp_target']))
    print('桌面版运行中: {0}'.format(r['running']))
    print('Node: {0}  {1}'.format(r['node'] or '未找到', r['node_version'] or ''))
    print('pnpm: {0}'.format(r['pnpm'] or '未找到'))
    print('yarn: {0}'.format(r['yarn'] or '未找到'))
    print('仓库包管理器: {0}'.format(r.get('repo_pm') or '未识别'))
    print('启动方式: {0}'.format(r['launcher'] or '无法解析'))
    print()
    print('提示: 可设环境变量 DSH_SKIN_CDP_PORT / DSH_SKIN_HARNESS_ROOT / DSH_SKIN_NODE / DSH_SKIN_PNPM /')
    print('      DSH_SKIN_DESKTOP_EXE，或在 config.json 写 cdp_port / dsh_root / desktop_exe / launcher_mode 字段手动指定')


def launch():
    """手动启动 DeepSeek Harness 桌面版（按当前模式：dev 按仓库 packageManager 走
    yarn dev / pnpm start:desktop（自带 CDP）；packaged=打包 exe+调试端口）"""
    try:
        import plugin_manager
        plugin_manager.start_injector()
    except Exception:
        pass  # 插件注入线程不可用不阻断启动
    ok, msg = dsh_env.launch_dsh()
    print(('[OK] ' if ok else '[!] ') + msg)


def deps(do_install=False):
    """检查（或安装）运行依赖"""
    import subprocess
    import sys
    ok = cdp_skin.WEBSOCKET_AVAILABLE
    ver = getattr(getattr(cdp_skin, 'websocket', None), '__version__', '?')
    print('解释器: {0}'.format(sys.executable))
    print('websocket-client: {0}'.format('已安装 v{0}'.format(ver) if ok else '缺失（CDP 通道不可用）'))
    if ok:
        print('[OK] 依赖齐备，CDP 通道可用')
        return
    if not do_install:
        print('[!] 缺少依赖。执行 `python dsh-skin.py deps --install` 自动安装')
        return
    print('正在安装 websocket-client ...')
    r = subprocess.run([sys.executable, '-m', 'pip', 'install', 'websocket-client'],
                       capture_output=True, text=True)
    print(r.stdout.strip()[-800:] or r.stderr.strip()[-800:])
    print('[OK] 安装完成，请重新运行命令' if r.returncode == 0 else '[X] 安装失败，请手动 pip install websocket-client')


def _check_bat_files():
    """校验 .bat 行尾：cmd.exe 只认 CRLF。返回问题列表。"""
    import glob as _glob
    problems = []
    for p in sorted(_glob.glob(os.path.join(ROOT, '*.bat'))):
        try:
            b = open(p, 'rb').read()
        except OSError:
            continue
        name = os.path.basename(p)
        cr, lf, crlf = b.count(b'\r'), b.count(b'\n'), b.count(b'\r\n')
        if cr != lf or crlf != lf:
            problems.append({"file": name, "issue": "行尾非 CRLF（cmd.exe 会解析失败）",
                             "detail": "CR={0} LF={1} CRLF={2}".format(cr, lf, crlf)})
        if b.startswith(b'\xef\xbb\xbf'):
            problems.append({"file": name, "issue": "含 UTF-8 BOM（会显示为乱码）", "detail": "BOM"})
        head = b.replace(b'\r\n', b'\n').split(b'\n')[:4]
        if any(ch > 127 for ch in b) and not any(b'chcp' in h for h in head):
            problems.append({"file": name, "issue": "含中文但前几行缺少 chcp 65001", "detail": "chcp"})
    return problems


def doctor_report():
    """综合体检（结构化），供 CLI doctor 与面板 /api/doctor 共用"""
    env = dsh_env.detect_report()
    cfg = load_config()
    cdp = {"available": cdp_skin.WEBSOCKET_AVAILABLE, "port": dsh_env.cdp_port(),
           "target": cdp_skin.cdp_ready(), "injected": None, "runtime": None}
    if cdp['available'] and cdp['target']:
        st = cdp_skin.page_state()
        if st.get('ok'):
            cdp['injected'] = st.get('injected')
            cdp['runtime'] = st.get('runtime')
    # 选择器健康：以 CDP 实测为准（无 target 时置 None）
    selectors = []
    if cdp['available'] and cdp['target']:
        res = cdp_skin.probe_regions()
        if res.get('ok'):
            counts = res.get('counts', {})
            for key, reg in te.resolve_regions().items():
                sels = [s.strip() for s in reg['selector'].split(',') if s.strip()]
                selectors.append({"key": key, "label": reg["label"],
                                  "alive": any(counts.get(s, 0) > 0 for s in sels),
                                  "overridden": bool(reg.get("overridden"))})
    themes = [{"id": tid, "name": t.get('name', tid), "stale": te.theme_staleness(t.get('skin_css', ''))}
              for tid, t in cfg.get('themes', {}).items()]
    return {
        "version": __version__, "env": env,
        "deps": {"websocket": cdp_skin.WEBSOCKET_AVAILABLE},
        "cdp": cdp, "selectors": selectors,
        "selectors_ok": all(s['alive'] for s in selectors) if selectors else None,
        "active": cfg.get('active'), "themes": themes,
        "overrides": te.load_overrides(),
        "enhance": enhance_engine.summary(),
        "bat_problems": _check_bat_files(),
    }


def doctor():
    """综合体检（人类可读）"""
    r = doctor_report()
    env, cdp = r['env'], r['cdp']
    print('=== DSH++ {0} 体检 ==='.format(r['version']))
    prod = env.get('product') or 'DeepSeek Harness'

    print('\n[1/6] 环境')
    print('  目标壳: {0}'.format(prod))
    print('  启动模式: {0}'.format(env['launcher_mode']))
    if env['launcher_mode'] == 'packaged':
        print('  打包版 exe: {0}（来源: {1}）'.format(env['desktop_exe'] or '未找到!',
                                                env['desktop_exe_source'] or '—'))
        if not env['desktop_exe']:
            print('    [!] 设 DSH_SKIN_DESKTOP_EXE / config.desktop_exe，'
                  '或先运行打包脚本 .desktop-build/run-package-win-dir.cmd')
    else:
        print('  Harness 根目录: {0}'.format(env['harness_root'] or '未找到!'))
    print('  DSH 数据根: {0}（来源: {1}）'.format(env['dsh_home'] or '未解析', env['dsh_home_source'] or '—'))
    print('  CDP 端口: {0}   监听={1}   target={2}'.format(env['cdp_port'], env['cdp_ready'], env['cdp_target']))
    print('  桌面版运行中: {0}'.format(env['running']))
    print('  Node: {0} {1}'.format(env['node'] or '未找到!', env['node_version'] or ''))
    print('  pnpm: {0}'.format(env['pnpm'] or '未找到!'))
    print('  yarn: {0}'.format(env['yarn'] or '未找到!'))
    bps = r.get('bat_problems') or []
    if bps:
        print('  启动脚本: {0} 个有问题 → 双击可能报「不是内部或外部命令」'.format(len(bps)))
        for b in bps:
            print('    [!] {0}: {1} ({2})'.format(b['file'], b['issue'], b['detail']))
    else:
        print('  启动脚本: 全部 OK（CRLF 行尾）')

    print('\n[2/6] 依赖')
    print('  websocket-client: {0}'.format(
        'OK' if r['deps']['websocket'] else '缺失 → CDP 通道不可用（dsh-skin.py deps --install）'))

    print('\n[3/6] CDP 通道')
    print('  依赖: {0}   端口 {1}: {2}'.format(
        '有' if cdp['available'] else '缺失', cdp['port'],
        '有 target' if cdp['target'] else '无 target'))
    if cdp['injected'] is not None:
        print('  皮肤已注入: {0}   运行时: {1}'.format(cdp['injected'], cdp['runtime']))
    if not cdp['target'] and env['running']:
        if env['launcher_mode'] == 'packaged':
            print('  [!] 打包版在运行但无调试端口 → 面板「重启 {0}（注入模式）」会先关旧实例再以'
                  ' --remote-debugging-port 拉起（或双击 启动打包版.bat）'.format(prod))
        else:
            print('  [!] 桌面版在运行但无调试端口 → 用「启动DeepSeekHarness.bat」或 `dsh-skin.py launch` 重启')
    if not env['running'] and not env['cdp_ready']:
        print('  桌面版未运行 → 先双击「启动DeepSeekHarness.bat」')

    print('\n[4/6] 选择器健康（CDP 实测）')
    if not r['selectors']:
        print('  （无 CDP target，无法实测；启动桌面版后重试）')
    for s in r['selectors']:
        print('  {0:<14} {1}{2}'.format(s['label'], 'OK' if s['alive'] else '失效!',
                                        '（已覆盖）' if s['overridden'] else ''))
    if r['selectors_ok'] is False:
        print('  [!] 有区域选择器失效，建议运行 `python dsh-skin.py probe --apply` 重新适配')

    stale = [t for t in r['themes'] if t['stale']]
    if stale:
        print('\n[!] 主题样式与新版界面脱节（可 rebuild 修复）:')
        for t in stale:
            print('  {0} ({1}): {2}'.format(t['name'], t['id'], ', '.join(t['stale'])))

    enh = r.get('enhance') or {}
    print('\n[5/6] 增强器')
    print('  总开关: {0}   运行时 v{1}'.format(
        '开' if enh.get('enabled') else '关', enh.get('runtime_version', '?')))
    on = [m['label'] for m in enh.get('modules', []) if m.get('enabled')]
    print('  启用模块: {0}'.format('、'.join(on) or '无'))
    print('  用户脚本: {0} 个 / 用户 CSS: {1} 个'.format(
        len(enh.get('scripts', [])), len(enh.get('styles', []))))

    print('\n[6/6] 已安装主题')
    if not r['themes']:
        print('  （无）')
    for t in r['themes']:
        mark = ' *' if t['id'] == r.get('active') else '  '
        print('  {0}{1}'.format(mark, t['name']))

    print('\n=== 体检结束 ===')
    return r


def probe(apply=False):
    """CDP 实测选择器存活并（可选）写入覆盖，实现对新版 DSH 的适配"""
    if not cdp_skin.WEBSOCKET_AVAILABLE:
        raise SystemExit('需要 websocket-client：python dsh-skin.py deps --install')
    if not cdp_skin.cdp_ready():
        raise SystemExit('未发现 DSH 调试端口 {0}。\n'
                         '  请用「启动DeepSeekHarness.bat」或 `python dsh-skin.py launch` 启动桌面版'.format(cdp_skin.PORT))
    res = cdp_skin.probe_regions()
    if not res.get('ok'):
        raise SystemExit('探测失败: {0}'.format(res.get('err')))
    counts = res.get('counts', {})
    print('=== 选择器实测（DeepSeek Harness 真实 DOM） ===')
    for key, reg in te.resolve_regions().items():
        sels = [s.strip() for s in reg['selector'].split(',') if s.strip()]
        print('  [{0}] {1}'.format(key, reg['label']))
        for s in sels:
            n = counts.get(s, 0)
            print('      {0:<6} {1}'.format(('存活' if n > 0 else '失效'), s))
    dead = res.get('dead', {})
    if dead:
        print('\n[!] 以下区域有候选失效: {0}'.format(', '.join(dead)))
    suggested = res.get('suggested', {})
    if apply:
        saved = te.save_overrides(suggested)
        print('\n[OK] 已写入选择器覆盖 {0} 项 → {1}'.format(len(saved), te.OVERRIDES_FILE))
        print('     已装主题可用 `python dsh-skin.py rebuild <id>` 重生成样式')
    else:
        print('\n[提示] 加 --apply 可把实测结果写入覆盖文件，完成自动适配')


def selectors_cmd(set_pairs=None, reset=False):
    """查看/设置/清空选择器覆盖"""
    if reset:
        te.save_overrides({})
        print('[OK] 已清空选择器覆盖 → {0}'.format(te.OVERRIDES_FILE))
        return
    if set_pairs:
        cur = te.load_overrides()
        for pair in set_pairs:
            if '=' not in pair:
                raise SystemExit('格式应为 key=selector，例如 sidebar=.my-panel')
            k, v = pair.split('=', 1)
            if k not in te.OVERRIDABLE:
                raise SystemExit('未知区域 {0}，可选: {1}'.format(k, ', '.join(sorted(te.OVERRIDABLE))))
            cur[k] = v
        saved = te.save_overrides(cur)
        print('[OK] 已更新选择器覆盖: {0}'.format(', '.join(saved)))
        print('     已装主题请执行 rebuild 生效')
        return
    ov = te.load_overrides()
    print('覆盖文件: {0}'.format(te.OVERRIDES_FILE))
    if not ov:
        print('（空，全部使用内置默认选择器）')
    else:
        for k, v in ov.items():
            print('  {0} = {1}'.format(k, v))
    print('\n内置默认:')
    for k, reg in te.resolve_regions().items():
        mark = ' *覆盖*' if reg.get('overridden') else ''
        print('  [{0}] {1}{2}'.format(k, reg['label'], mark))
        print('      {0}'.format(reg['selector']))


# ---------------- 增强器（Enhance） ----------------
def enhance_bundle():
    """生成要注入渲染进程的 (css, js, meta)。

    css = 当前主题样式（背景图 data URI）+ 用户 CSS
    js  = 打标器（为 CSS 选择器提供 data-dsh-skin 标记）+ window.DSHSkin 运行时 + 用户脚本
    打标器必须与 CSS 同捆：即使增强总开关关闭，换肤选择器也依赖打标。
    """
    cfg = load_config()
    active = cfg.get('active')
    user_css = enhance_engine.custom_css_text()
    css = ''
    force_dark = True
    if active and active in cfg.get('themes', {}):
        t = cfg['themes'][active]
        img = os.path.join(SKIN_ROOT, active, 'background.png')
        params = t.get('params') or te.DEFAULT_PARAMS
        force_dark = bool(te.norm_params(params).get('force_dark', True))
        css = te.generate_inject_css(params, img,
                                     t.get('appearance', 'light'), extra_css=user_css)
    elif user_css:
        css = '/* DSHSkin 增强（未启用主题） */\n' + user_css
    js, meta = enhance_engine.build_runtime_js()
    marker = te.generate_marker_js(force_dark)
    js = (marker + '\n' + js) if js else marker
    meta['force_dark'] = force_dark
    return css, js, meta


def enhance_apply():
    """通过 CDP 立即应用增强（主题 + 打标器 + 用户脚本），无需重启"""
    if not cdp_skin.WEBSOCKET_AVAILABLE:
        raise SystemExit('需要 websocket-client：python dsh-skin.py deps --install')
    if not cdp_skin.cdp_ready():
        raise SystemExit('未发现 DSH 调试端口 {0}。\n'
                         '  请先启动桌面版（「启动DeepSeekHarness.bat」或 `python dsh-skin.py launch`）'.format(cdp_skin.PORT))
    css, js, meta = enhance_bundle()
    if not css and not js:
        raise SystemExit('没有可注入的内容（既无启用主题，也无用户脚本/CSS）')
    r = cdp_skin.inject_bundle(css, js)
    if not r.get('ok'):
        raise SystemExit('注入失败: {0}'.format(r.get('err')))
    print('[OK] 增强已注入：CSS {0} 字符 / JS {1} 字符 / 用户脚本 {2} 个'.format(
        r.get('cssLen'), r.get('jsLen') or 0, len(meta.get('scripts', []))))
    if not meta.get('enabled'):
        print('[提示] 增强总开关是关的，仅注入了主题样式；可在面板「增强」里开启')


def enhance_cmd(name=None, enable=None, preset=None, apply_now=False):
    """增强器管理：查看状态 / 开关模块 / 开关脚本 / 切换预设 / 立即应用"""
    ee = enhance_engine
    ee.ensure_dirs()
    if preset:
        st = ee.apply_preset(preset)
        print('[OK] 已应用增强预设「{0}」（总开关={1}）'.format(preset, st['enabled']))
        print('     生效方式: 面板「应用增强」，或 python dsh-skin.py enhance --apply')
        return
    if name is not None and enable is not None:
        if name.endswith('.js'):
            ee.set_script_enabled(name, enable)
            print('[OK] 用户脚本 {0} → {1}'.format(name, '启用' if enable else '停用'))
        elif name in {m['key'] for m in ee.MODULES}:
            st = ee.load_state()
            st['modules'][name] = bool(enable)
            ee.save_state(st)
            print('[OK] 模块 {0} → {1}'.format(name, '启用' if enable else '停用'))
        else:
            raise SystemExit('未知模块或脚本: {0}'.format(name))
        return
    if apply_now:
        enhance_apply()
        return
    s = ee.summary()
    print('=== DSH++ 增强器 {0} ==='.format(__version__))
    print('总开关: {0}    运行时: v{1}'.format('开' if s['enabled'] else '关', s['runtime_version']))
    print('脚本目录: {0}'.format(s['dir']))
    print('\n模块:')
    for m in s['modules']:
        print('  [{0}] {1:<22} {2}'.format('x' if m['enabled'] else ' ', m['label'], m['desc']))
    print('\n用户脚本 ({0}):'.format(len(s['scripts'])))
    for it in s['scripts']:
        print('  [{0}] {1}  {2}  {3}B'.format('x' if it['enabled'] else ' ', it['name'], it['mtime'], it['size']))
    if not s['scripts']:
        print('  （空 —— 把 .js 放进脚本目录即自动加载）')
    print('\n用户 CSS ({0}):'.format(len(s['styles'])))
    for it in s['styles']:
        print('  {0}  {1}  {2}B'.format(it['name'], it['mtime'], it['size']))


def pack(meta_path, bg_path, css_path, out):
    with open(meta_path, encoding='utf-8-sig') as f:
        meta = json.load(f)
    _validate_meta(meta)
    if not os.path.isfile(bg_path):
        raise SystemExit('背景图不存在: {0}'.format(bg_path))
    with open(bg_path, 'rb') as f:
        img_bytes = f.read()
    if _check_image_magic(img_bytes) is None:
        raise SystemExit('背景图不是有效图片（仅支持 PNG/JPG/WebP）')
    with open(css_path, encoding='utf-8-sig') as f:
        css = f.read()
    if not css.strip():
        raise SystemExit('skin.css 内容为空')
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('meta.json', json.dumps(meta, ensure_ascii=False, indent=2))
        z.writestr('background.png', img_bytes)
        z.writestr('skin.css', css)
    print('[OK] 已打包: {0}'.format(out))


def template(out=None, appearance='light', params=None):
    """生成一份空主题包的 skin.css 模板（可选直接写出）"""
    p = te.norm_params(params or te.DEFAULT_PARAMS)
    css = te.generate_css(p, '{{IMAGE}}', appearance)
    if out:
        with open(out, 'w', encoding='utf-8') as f:
            f.write(css)
        print('[OK] 已写出模板: {0}'.format(out))
    else:
        print(css)
    return css


def main():
    ap = argparse.ArgumentParser(description='DeepSeek Harness 主题包工具 DSH++（原 DSHSkin）')
    ap.add_argument('--version', action='version', version='dsh-skin {0}'.format(__version__))
    sub = ap.add_subparsers(dest='cmd', required=True)

    p1 = sub.add_parser('install')
    p1.add_argument('zip_path')
    p1.add_argument('--dry-run', action='store_true', help='仅校验主题包，不落盘')
    sub.add_parser('list')
    p2 = sub.add_parser('switch')
    p2.add_argument('id')
    sub.add_parser('restore')
    p3 = sub.add_parser('remove')
    p3.add_argument('id')
    p4 = sub.add_parser('export')
    p4.add_argument('id')
    p4.add_argument('-o', required=True, help='输出 zip 路径')
    p5 = sub.add_parser('params')
    p5.add_argument('id')
    p5.add_argument('--json', help='参数 JSON 字符串，省略则打印当前参数')
    p6 = sub.add_parser('rebuild', help='用当前模板重生成样式（升级适配）')
    p6.add_argument('id')
    sub.add_parser('migrate', help='把所有过期主题升级到当前模板')
    sub.add_parser('cleanup')
    p_un = sub.add_parser('uninstall', help='完整卸载：移除注入+还原托管块，--purge 连数据一起删')
    p_un.add_argument('--purge', action='store_true', help='同时删除 ~/.dsh-skins 全部数据')
    sub.add_parser('launch', help='启动 DeepSeek Harness 桌面版（开发模式，自带 CDP 端口）')
    sub.add_parser('detect')
    p7 = sub.add_parser('deps')
    p7.add_argument('--install', action='store_true', help='缺失时自动安装')
    sub.add_parser('doctor')
    p8 = sub.add_parser('probe', help='CDP 实测选择器')
    p8.add_argument('--apply', action='store_true', help='写入选择器覆盖完成适配')
    p9 = sub.add_parser('selectors')
    p9.add_argument('--set', action='append', dest='set_pairs', metavar='key=sel', help='覆盖某区域选择器')
    p9.add_argument('--reset', action='store_true', help='清空覆盖')
    p10 = sub.add_parser('pack')
    p10.add_argument('meta')
    p10.add_argument('bg')
    p10.add_argument('css')
    p10.add_argument('-o', required=True)
    p11 = sub.add_parser('template', help='生成皮肤 CSS 模板')
    p11.add_argument('-o', help='输出文件；省略则打印到 stdout')
    p11.add_argument('--appearance', choices=['light', 'dark', 'both'], default='both')
    p11.add_argument('--json', help='参数 JSON 字符串')
    p12 = sub.add_parser('enhance', help='增强器：用户脚本 / 用户 CSS / 模块开关')
    p12.add_argument('--preset', choices=['off', 'builtin', 'scripts', 'full'], help='一键切换预设（builtin=内置增强 / scripts=仅用户脚本 / full=全部）')
    p12.add_argument('--enable', metavar='KEY', help='启用某模块或脚本（模块键 或 xxx.js）')
    p12.add_argument('--disable', metavar='KEY', help='停用某模块或脚本')
    p12.add_argument('--apply', action='store_true', help='通过 CDP 立即应用增强（无需重启）')

    sub.add_parser('update', help='检查 GitHub Release 是否有新版本（只检查不自动替换）')

    args = ap.parse_args()
    if args.cmd == 'install':
        install(args.zip_path, dry_run=args.dry_run)
    elif args.cmd == 'list':
        list_themes()
    elif args.cmd == 'switch':
        switch(args.id)
    elif args.cmd == 'restore':
        restore()
    elif args.cmd == 'remove':
        remove(args.id)
    elif args.cmd == 'export':
        export_theme(args.id, args.o)
    elif args.cmd == 'rebuild':
        rebuild(args.id)
    elif args.cmd == 'migrate':
        migrate_cmd()
    elif args.cmd == 'cleanup':
        cleanup()
    elif args.cmd == 'uninstall':
        r = uninstall(purge=getattr(args, 'purge', False))
        print('[OK] 已移除注入窗口: {0}'.format(r['injection_removed'] or '无（DSH 未运行）'))
        print('[OK] cordis 托管块还原: {0}'.format(r['patch_restored'] or '无托管块'))
        if r['purged']:
            print('[OK] 已删除数据目录: {0}'.format(SKIN_ROOT))
        for n in r['notes']:
            print('[i] ' + n)
        print('[完成] 卸载流程结束' + ('（数据目录已保留，加 --purge 可彻底清除）' if not r['purged'] else ''))
    elif args.cmd == 'launch':
        launch()
    elif args.cmd == 'detect':
        detect()
    elif args.cmd == 'deps':
        deps(do_install=args.install)
    elif args.cmd == 'doctor':
        doctor()
    elif args.cmd == 'probe':
        probe(apply=args.apply)
    elif args.cmd == 'selectors':
        selectors_cmd(set_pairs=args.set_pairs, reset=args.reset)
    elif args.cmd == 'pack':
        pack(args.meta, args.bg, args.css, args.o)
    elif args.cmd == 'template':
        template(args.o, args.appearance, json.loads(args.json) if args.json else None)
    elif args.cmd == 'enhance':
        if args.enable:
            enhance_cmd(name=args.enable, enable=True)
        elif args.disable:
            enhance_cmd(name=args.disable, enable=False)
        else:
            enhance_cmd(preset=args.preset, apply_now=args.apply)
    elif args.cmd == 'update':
        import updater
        _r = updater.check_for_update(__version__)
        if not _r.get('configured'):
            print('未配置更新源（设置环境变量 DSH_SKIN_REPO=owner/repo，或改 updater.UPDATER_REPO）')
        elif not _r.get('ok'):
            print(_r.get('reason'))
        else:
            print('当前版本 {0} / 最新版本 {1}'.format(_r['current'], _r['latest'] or '未知'))
            if _r['has_update']:
                print('[!] 发现新版本，请前往下载替换（不会自动覆盖运行中的程序）：')
                print('    ' + _r['url'])
                for a in _r.get('assets', []):
                    print('    - {0}: {1}'.format(a['name'], a['url']))
            else:
                print('[OK] 已是最新版本')
    elif args.cmd == 'params':
        cfg = load_config()
        if args.json is None:
            t = cfg['themes'].get(args.id)
            if not t:
                raise SystemExit('未安装主题 {0}'.format(args.id))
            print(json.dumps(t.get('params', te.DEFAULT_PARAMS), ensure_ascii=False, indent=2))
        else:
            update_params(args.id, json.loads(args.json))


if __name__ == '__main__':
    main()
