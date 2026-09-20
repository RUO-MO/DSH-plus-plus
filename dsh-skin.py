# -*- coding: utf-8 -*-
"""DeepSeek Harness 增强工作台（DSH++）

用法:
  python dsh-skin.py restore                 还原官方样式（移除 CDP 注入的样式与运行时）
  python dsh-skin.py migrate                 清理换肤历史遗留（config 的 themes/active + 主题目录）
  python dsh-skin.py launch                  启动 DeepSeek Harness 桌面版（开发模式，自带 CDP 9222）
  python dsh-skin.py detect                  探测环境（CDP 端口 / 进程 / 启动器）
  python dsh-skin.py deps [--install]        检查（或安装）运行依赖
  python dsh-skin.py doctor                  综合体检：环境/依赖/CDP 通道/选择器
  python dsh-skin.py probe [--apply]         CDP 实测选择器存活，--apply 写入覆盖以适配新版
  python dsh-skin.py selectors [--set key=sel] [--reset]   查看/手工设置/清空选择器覆盖
  python dsh-skin.py enhance [--preset builtin|scripts|full] [--enable k] [--apply]
                                              增强器：用户脚本 / 用户 CSS / 模块开关
  python dsh-skin.py uninstall [--purge]     完整卸载（移除注入 + 还原托管块；--purge 连数据删）
  python dsh-skin.py --version               显示版本

关于换肤（2026-09-20 变更）:
  主题库 / 参数调优 / CSS 模板 / 预览 / 主题包安装已移除，「动态背景」改为
  参考 dsh-wallpaper-engine 的接口契约实现对接适配层（走其 /wallpaper-engine/* 路由）。
  本工具保留：CDP 增强注入（含打标器）、会话管理、供应商配置、插件管理、诊断。
  老用户升级后跑一次 `migrate` 即可清掉旧主题数据。

注入通道：唯一 CDP（渲染进程自带 --remote-debugging-port=9222），
不修改 deepseek-harness 任何文件；增强全部可逆。
"""
import argparse
import os
import shutil

import cdp_skin
import dsh_env
import enhance_engine
import marker_engine

__version__ = '1.0.0'

# ---------------- 路径（统一走 dsh_env） ----------------
ROOT = os.path.dirname(os.path.abspath(__file__))   # .bat 等随附文件所在目录
SKIN_ROOT = dsh_env.SKIN_ROOT
CONFIG = dsh_env.CONFIG

ensure_dirs = dsh_env.ensure_dirs
load_config = dsh_env.load_config
save_config = dsh_env.save_config
update_config = dsh_env.update_config


def _channel(cfg):
    return 'cdp'   # DSH 唯一注入通道


# ---------------- 核心 ----------------
# 换肤（主题库 / 参数 / 模板 / 预览 / 选择器覆盖）已于 2026-09-20 移除，
# 「动态背景」改为参考 dsh-wallpaper-engine 契约实现的适配层（见 wallpaper_engine.py）。
# 保留部分：
#   · restore()    —— 清除历史遗留的 CDP 注入（老用户升级后跑一次即可干净）
#   · migrate_legacy_data() —— 清理 ~/.dsh-skins 下残留的主题目录与 config 字段


def restore():
    """还原官方样式：移除 CDP 注入的样式与运行时（不动任何官方文件）。

    换肤移除后本命令仍保留 —— 用于清理旧版本注入过的皮肤，或关闭全部增强注入。
    """
    ensure_dirs()
    update_config(lambda cfg: cfg.__setitem__('active', None))
    cdp_note = ''
    if cdp_skin.WEBSOCKET_AVAILABLE:
        r = cdp_skin.remove_css()
        if r.get('ok'):
            cdp_note = '，CDP 样式与运行时已移除'
    print('[OK] 已还原官方样式' + cdp_note)


# 旧配置里属于换肤、现已废弃的字段（迁移时清掉，保留 themes 之外的用户数据）
_LEGACY_CONFIG_KEYS = ('active', 'themes')
# 数据根下属于换肤产物的目录名（非主题目录，不能误删）
_NON_THEME_DIRS = ('enhance', 'plugins', 'backups', 'exports', 'cache', 'logs')


def migrate_legacy_data(purge_dirs=True):
    """清理换肤功能的历史遗留：config 里的 themes/active 字段 + 数据根下的主题目录。

    幂等、可逆（只删换肤产物，不动 enhance/plugins/backups/exports 等）。
    purge_dirs=False 时只清 config 字段、保留磁盘目录（用户想留个纪念时用）。
    返回 {config_cleaned: bool, dirs_removed: [..], dirs_kept: [..]}
    """
    result = {'config_cleaned': False, 'dirs_removed': [], 'dirs_kept': []}

    def mut(cfg):
        changed = False
        for k in _LEGACY_CONFIG_KEYS:
            if k in cfg:
                # themes 里可能有 builtin 记录，一并清掉；active 置空
                del cfg[k]
                changed = True
        # 兼容：老配置可能把主题根写成 themes 列表
        if 'theme_config' in cfg:
            del cfg['theme_config']
            changed = True
        return changed

    try:
        result['config_cleaned'] = bool(update_config(mut))
    except Exception as e:
        print('[!] 清理 config 失败（可忽略）: {0}'.format(e))

    if purge_dirs and os.path.isdir(SKIN_ROOT):
        known = set(_NON_THEME_DIRS)
        for name in sorted(os.listdir(SKIN_ROOT)):
            p = os.path.join(SKIN_ROOT, name)
            if not os.path.isdir(p) or name in known:
                continue
            # 主题目录特征：内含 skin.css 或 background.png 或 meta 衍生文件
            looks_like_theme = any(
                os.path.isfile(os.path.join(p, f))
                for f in ('skin.css', 'background.png', 'meta.json'))
            if looks_like_theme:
                shutil.rmtree(p, ignore_errors=True)
                result['dirs_removed'].append(name)
            else:
                result['dirs_kept'].append(name)

    return result


def migrate_cmd():
    """CLI: 清理换肤历史遗留"""
    r = migrate_legacy_data()
    if r['config_cleaned']:
        print('[OK] 已清理 config.json 中的换肤字段（themes / active）')
    else:
        print('[OK] config.json 无需清理')
    if r['dirs_removed']:
        print('[OK] 已移除 {0} 个主题目录: {1}'.format(
            len(r['dirs_removed']), ', '.join(r['dirs_removed'])))
    else:
        print('[OK] 数据根下无主题目录残留')
    if r['dirs_kept']:
        print('[i] 已保留非主题目录: {0}'.format(', '.join(r['dirs_kept'])))
    print('[i] 换肤已由 dsh-plugin-wallpaper-engine 取代；本工具保留增强/会话/插件等能力')



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
            with open(p, 'rb') as fh:
                b = fh.read()
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
            for key, reg in marker_engine.resolve_regions().items():
                sels = [s.strip() for s in reg['selector'].split(',') if s.strip()]
                selectors.append({"key": key, "label": reg["label"],
                                  "alive": any(counts.get(s, 0) > 0 for s in sels),
                                  "overridden": bool(reg.get("overridden"))})
    return {
        "version": __version__, "env": env,
        "deps": {"websocket": cdp_skin.WEBSOCKET_AVAILABLE},
        "cdp": cdp, "selectors": selectors,
        "selectors_ok": all(s['alive'] for s in selectors) if selectors else None,
        "overrides": marker_engine.load_overrides(),
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
        print('  增强已注入: {0}   运行时: {1}'.format(cdp['injected'], cdp['runtime']))
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

    enh = r.get('enhance') or {}
    print('\n[5/6] 增强器')
    print('  总开关: {0}   运行时 v{1}'.format(
        '开' if enh.get('enabled') else '关', enh.get('runtime_version', '?')))
    on = [m['label'] for m in enh.get('modules', []) if m.get('enabled')]
    print('  启用模块: {0}'.format('、'.join(on) or '无'))
    print('  用户脚本: {0} 个 / 用户 CSS: {1} 个'.format(
        len(enh.get('scripts', [])), len(enh.get('styles', []))))

    print('\n[6/6] 动态壁纸（dsh-plugin-wallpaper-engine）')
    try:
        import wallpaper_engine
        wp = wallpaper_engine.plugin_status()
        if wp.get('installed'):
            print('  插件: 已安装 v{0}（profile={1}）'.format(wp.get('version') or '?', wp.get('profile')))
            print('  配置: {0}'.format(wp.get('config') or '（无）'))
            if wp.get('config_exists'):
                sets = wallpaper_engine.load_settings()
                print('  当前壁纸 id: {0}'.format(sets.get('id') or '（未选择）'))
            else:
                print('  [!] 配置文件不存在 → 在 DSH 里打开一次动态壁纸面板即可生成')
        else:
            print('  [i] 未检测到该插件；换肤功能已移除，「动态背景」请装 dsh-plugin-wallpaper-engine')
    except Exception as e:
        print('  [!] 读取壁纸插件状态失败: {0}'.format(e))

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
    for key, reg in marker_engine.resolve_regions().items():
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
        saved = marker_engine.save_overrides(suggested)
        print('\n[OK] 已写入选择器覆盖 {0} 项 → {1}'.format(len(saved), marker_engine.OVERRIDES_FILE))
        print('     已装主题可用 `python dsh-skin.py rebuild <id>` 重生成样式')
    else:
        print('\n[提示] 加 --apply 可把实测结果写入覆盖文件，完成自动适配')


def selectors_cmd(set_pairs=None, reset=False):
    """查看/设置/清空选择器覆盖"""
    if reset:
        marker_engine.save_overrides({})
        print('[OK] 已清空选择器覆盖 → {0}'.format(marker_engine.OVERRIDES_FILE))
        return
    if set_pairs:
        cur = marker_engine.load_overrides()
        for pair in set_pairs:
            if '=' not in pair:
                raise SystemExit('格式应为 key=selector，例如 sidebar=.my-panel')
            k, v = pair.split('=', 1)
            if k not in marker_engine.OVERRIDABLE:
                raise SystemExit('未知区域 {0}，可选: {1}'.format(k, ', '.join(sorted(marker_engine.OVERRIDABLE))))
            cur[k] = v
        saved = marker_engine.save_overrides(cur)
        print('[OK] 已更新选择器覆盖: {0}'.format(', '.join(saved)))
        print('     已装主题请执行 rebuild 生效')
        return
    ov = marker_engine.load_overrides()
    print('覆盖文件: {0}'.format(marker_engine.OVERRIDES_FILE))
    if not ov:
        print('（空，全部使用内置默认选择器）')
    else:
        for k, v in ov.items():
            print('  {0} = {1}'.format(k, v))
    print('\n内置默认:')
    for k, reg in marker_engine.resolve_regions().items():
        mark = ' *覆盖*' if reg.get('overridden') else ''
        print('  [{0}] {1}{2}'.format(k, reg['label'], mark))
        print('      {0}'.format(reg['selector']))


# ---------------- 增强器（Enhance） ----------------
def enhance_bundle():
    """生成要注入渲染进程的 (css, js, meta)。

    css = 用户 CSS（~/.dsh-skins/enhance/*.css）
    js  = 打标器（为增强脚本提供 data-dsh-skin 标记）+ window.DSHSkin 运行时 + 用户脚本

    换肤已移除：不再装配主题 CSS / 背景图。打标器仍必须保留——增强模块与市场脚本
    用 [data-dsh-skin=*] 定位区域（见 marker_engine），去掉会让「界面微调 / 宽屏 /
    专注模式 / 代码复制」等全部失效。
    """
    user_css = enhance_engine.custom_css_text()
    css = ''
    if user_css:
        css = '/* DSH++ 增强 · 用户 CSS */\n' + user_css
    js, meta = enhance_engine.build_runtime_js()
    marker = marker_engine.generate_marker_js(False)
    js = (marker + '\n' + js) if js else marker
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


def main():
    ap = argparse.ArgumentParser(description='DeepSeek Harness 增强工作台 DSH++')
    ap.add_argument('--version', action='version', version='dsh-skin {0}'.format(__version__))
    sub = ap.add_subparsers(dest='cmd', required=True)

    sub.add_parser('restore', help='还原官方样式（移除 CDP 注入的样式与运行时）')
    sub.add_parser('migrate', help='清理换肤历史遗留（config 的 themes/active + 主题目录）')
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
    p12 = sub.add_parser('enhance', help='增强器：用户脚本 / 用户 CSS / 模块开关')
    p12.add_argument('--preset', choices=['off', 'builtin', 'scripts', 'full'], help='一键切换预设（builtin=内置增强 / scripts=仅用户脚本 / full=全部）')
    p12.add_argument('--enable', metavar='KEY', help='启用某模块或脚本（模块键 或 xxx.js）')
    p12.add_argument('--disable', metavar='KEY', help='停用某模块或脚本')
    p12.add_argument('--apply', action='store_true', help='通过 CDP 立即应用增强（无需重启）')

    sub.add_parser('update', help='检查 GitHub Release 是否有新版本（只检查不自动替换）')

    args = ap.parse_args()
    if args.cmd == 'restore':
        restore()
    elif args.cmd == 'migrate':
        migrate_cmd()
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


if __name__ == '__main__':
    main()
