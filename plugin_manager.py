# -*- coding: utf-8 -*-
"""DSHSkin 插件管理：按 DeepSeek Harness 官方插件协议导入/启停/卸载插件包。

协议要点（与官方一致，全部零侵入）：
  * 插件 = 一个 npm 包：package.json 的 `dsh.bundle`（声明 cordis.patch.yml 组合层）
    和/或 `dsh.client`（声明浏览器半 exports["./client"]）；
  * 安装 = 在 profile 的用户补丁层（cordis.patch.yml）写一条 Loader 行
    `- insert: [{ id, name: 'file:///…', disabled }]`——行字段（含 disabled 开关）
    均为官方 Loader 行 schema；插件依赖由 npm 装进插件仓库目录，随 file URL 解析；
  * web 目标：`$DSH_HOME/profiles/web/cordis.patch.yml`（默认 ~/.dsh/profiles/web）；
  * 桌面端开发模式目标：`<harness>/apps/desktop/.desktop-build/development/project/cordis.patch.yml`，
    该项目每次启动重建，由 launch 时的注入线程按 registry 反复补写；
  * 本模块绝不写 deepseek-harness 仓库的受管源码（project 目录是构建产物）。

存储：~/.dsh-skins/plugins/registry.json + <name>/<version>/（解包后的插件本体）。

双轨安装（面板「安装时自动选轨」）：
  * A 轨 · 官方 bundle 直装：spec 能解析为 npm 包且 registry 上确实存在时走此轨。
    DSH 完全退出后复刻官方事务：`pnpm --dir <profile> add <name>@<ver> --save-exact
    --ignore-scripts` + package.json 的 `dsh.profile.bundles` 挂名；插件进
    <profile>/node_modules，DSH 启动按 bundles 自加载（常驻、与面板无关），
    卸载 = `pnpm remove` + 摘挂名，官方锁文件管依赖，零残留。运行时禁止安装/卸载
    （防 profile 事务锁冲突）；启停 = 挂名去留（仅影响下次启动）。
  * B 轨 · 持久装配行：本地目录 / tgz / GitHub 源码包。插件本体留本目录，装配行
    持久写进 cordis.patch.yml 用户补丁层；依赖统一装进 ~/.dsh-skins/plugins/node_modules
    （pnpm 优先、npm 兜底，替代旧「每插件各自 npm install」）；卸载 = 删装配行 +
    整目录 + 清 settings.yaml 命名空间，零残留。
"""

import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import zipfile

try:
    import yaml
except ImportError:      # PyYAML 缺失时后端仍可启动（配置型偏好读写报清晰错误，见 _yaml_required）
    yaml = None

import dsh_env

BLOCK_BEGIN = '# >>> dshskin:plugins (managed, do not edit inside) >>>'
BLOCK_END = '# <<< dshskin:plugins <<<'
ROW_ID_PREFIX = 'dshskin-'
STORE = os.path.join(dsh_env.SKIN_ROOT, 'plugins')
REGISTRY = os.path.join(STORE, 'registry.json')

_inject_lock = threading.RLock()   # 可重入：set_enabled/_all、import_package、remove_package 持锁后调用 apply_rows()，普通 Lock 会自死锁


class YamlMissing(RuntimeError):
    """PyYAML 缺失时抛出：只影响需要读写 YAML 的插件操作，不影响后端启动。"""


def _yaml_required():
    """需要 YAML 的操作先调这里：缺失时给出可执行的修复指引而非裸 ImportError。"""
    if yaml is None:
        raise YamlMissing('缺少 PyYAML：运行 `python dsh-skin.py deps --install` 后重试')
    return yaml



# ---------------- registry ----------------

def _load_registry():
    try:
        with open(REGISTRY, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get('plugins'), dict):
            data.setdefault('enabled', True)   # 插件注入总开关（缺省开）
            if _heal_registry(data):
                try:
                    _save_registry(data)       # 数据根迁移自愈落盘（失败不影响本次读取）
                except OSError:
                    pass
            return data
    except Exception:
        pass
    return {"plugins": {}, "enabled": True}


def _save_registry(reg):
    os.makedirs(STORE, exist_ok=True)
    tmp = REGISTRY + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, REGISTRY)


def _store_candidate_dir(record):
    """track B 插件在当前数据根的规范落点：<STORE>/<name>/<version>。
    版本子目录可能残留多个（如 0.1.0/0.2.0 并存）：优先精确版本，
    否则仅一个子目录时取之；都不可判定返回 None。"""
    name = record.get('name', '')
    if not name:
        return None
    base = os.path.join(STORE, name.replace('/', '__'))
    if not os.path.isdir(base):
        return None
    ver = record.get('version')
    if ver and os.path.isdir(os.path.join(base, ver)):
        return os.path.join(base, ver)
    try:
        subs = sorted(s for s in os.listdir(base) if os.path.isdir(os.path.join(base, s)))
    except OSError:
        return None
    if len(subs) == 1:
        return os.path.join(base, subs[0])
    return None


def _heal_registry(reg):
    """数据根迁移自愈：B 轨插件 record.dir 可能指向旧数据根
    （如 D:\\DATA\\DSHdata，迁移前写入的 registry/补丁行）。只要规范库
    落点存在就统一指回当前 STORE——补丁行 file:/// URL 由 dir 生成，
    指回后旧根目录可安全弃用。返回是否有变更。"""
    changed = False
    for rec in reg.get('plugins', {}).values():
        if rec.get('track', 'B') == 'A':
            continue
        cand = _store_candidate_dir(rec)
        if cand and rec.get('dir') != cand:
            rec['dir'] = cand
            changed = True
    return changed


# ---------------- 包解包与协议校验 ----------------
# 解压炸弹 / 恶意成员防护阈值（皮肤与插件都很小，阈值留足余量）
MAX_UNCOMPRESSED = 200 * 1024 * 1024   # 展开后总大小上限 200MB
MAX_SINGLE_FILE = 50 * 1024 * 1024     # 单文件上限 50MB
MAX_MEMBERS = 4000                     # 成员数量上限
MAX_COMPRESS_RATIO = 100               # 未压缩/压缩 比率上限（zip bomb 典型特征是超高压缩比）


def _safe_join(dest, name):
    """防路径穿越：返回规范化后的目标路径，越界则抛错。"""
    base = os.path.normpath(dest)
    target = os.path.normpath(os.path.join(base, name))
    if target != base and not target.startswith(base + os.sep):
        raise ValueError('压缩包内含非法路径: %s' % name)
    return target


def _check_zip_bomb(zf):
    total = 0
    members = zf.infolist()
    if len(members) > MAX_MEMBERS:
        raise ValueError('压缩包成员数 %d 超过上限 %d（疑似解压炸弹）' % (len(members), MAX_MEMBERS))
    for m in members:
        if m.is_dir():
            continue
        if m.file_size > MAX_SINGLE_FILE:
            raise ValueError('压缩包内单文件 %s 过大（%d 字节，上限 %d）'
                             % (m.filename, m.file_size, MAX_SINGLE_FILE))
        total += m.file_size
        if total > MAX_UNCOMPRESSED:
            raise ValueError('压缩包展开后总体积超过上限 %d MB（疑似解压炸弹）'
                             % (MAX_UNCOMPRESSED // 1024 // 1024))
        # 压缩比：高压缩比 + 非零内容是 zip bomb 典型特征
        if m.compress_size > 0 and m.file_size > 4096:
            ratio = m.file_size / m.compress_size
            if ratio > MAX_COMPRESS_RATIO:
                raise ValueError('压缩包 %s 压缩比达 %.0f，超过安全上限（疑似解压炸弹）'
                                 % (m.filename, ratio))
    return total


def extract_package(src, dest):
    """把 zip/tgz 解包到 dest（路径穿越 + 解压炸弹 + 危险成员防护），返回包根目录。"""
    lower = (src or '').lower()
    if lower.endswith('.zip'):
        with zipfile.ZipFile(src) as zf:
            for member in zf.namelist():
                _safe_join(dest, member)
            _check_zip_bomb(zf)
            zf.extractall(dest)
    elif lower.endswith(('.tgz', '.tar.gz')):
        with tarfile.open(src, 'r:gz') as tf:
            total = 0
            members = tf.getmembers()
            if len(members) > MAX_MEMBERS:
                raise ValueError('压缩包成员数超过上限（疑似解压炸弹）')
            for m in members:
                _safe_join(dest, m.name)
                # 拒绝设备/管道等特殊成员与指向包外的链接
                if m.isdev() or m.ischr() or m.isblk() or m.isfifo():
                    raise ValueError('压缩包含特殊设备成员，已拒绝: %s' % m.name)
                if m.issym() or m.islnk():
                    _safe_join(dest, os.path.join(os.path.dirname(m.name), m.linkname))
                if m.isfile():
                    if m.size > MAX_SINGLE_FILE:
                        raise ValueError('压缩包内单文件 %s 过大' % m.name)
                    total += m.size
                    if total > MAX_UNCOMPRESSED:
                        raise ValueError('压缩包展开后总体积超过安全上限（疑似解压炸弹）')
            tf.extractall(dest)
    else:
        raise ValueError('仅支持 .zip / .tgz / .tar.gz 插件包')

    if os.path.isfile(os.path.join(dest, 'package.json')):
        return dest
    entries = [e for e in os.listdir(dest) if os.path.isdir(os.path.join(dest, e))]
    for entry in sorted(entries):
        if os.path.isfile(os.path.join(dest, entry, 'package.json')):
            return os.path.join(dest, entry)
    raise ValueError('压缩包里找不到 package.json（不是插件包）')


def _entry_file(pkg_root, manifest):
    """定位 host 半入口（Loader 行 name 要指到可 import 的模块）。"""
    exports = manifest.get('exports')
    rel = None
    if isinstance(exports, dict):
        dot = exports.get('.')
        if isinstance(dot, str):
            rel = dot
        elif isinstance(dot, dict):
            rel = dot.get('default') or dot.get('import')
    if not rel:
        rel = manifest.get('main')
    if not rel and os.path.isfile(os.path.join(pkg_root, 'lib', 'index.js')):
        rel = './lib/index.js'
    if not rel and os.path.isfile(os.path.join(pkg_root, 'index.js')):
        rel = './index.js'
    if not rel:
        return None
    path = os.path.normpath(os.path.join(pkg_root, rel))
    if not path.startswith(os.path.normpath(pkg_root)) or not os.path.isfile(path):
        return None
    return path


def validate_protocol(pkg_root):
    """按官方协议校验包：返回 info dict，不合规则抛 ValueError。"""
    with open(os.path.join(pkg_root, 'package.json'), 'r', encoding='utf-8') as f:
        manifest = json.load(f)
    name = manifest.get('name')
    if not isinstance(name, str) or not re.match(r'^(@[a-z0-9-]+/)?[a-z0-9][a-z0-9._-]*$', name):
        raise ValueError('package.json 缺少合法的 npm name')
    dsh = manifest.get('dsh') if isinstance(manifest.get('dsh'), dict) else {}
    bundle = dsh.get('bundle')
    client = dsh.get('client')
    if not bundle and not client:
        raise ValueError('不符合官方插件协议：package.json 缺少 dsh.bundle / dsh.client 声明')

    patch_file = None
    if bundle:
        patch_rel = bundle.get('patch') if isinstance(bundle, dict) else None
        if not isinstance(patch_rel, str):
            raise ValueError('dsh.bundle.patch 缺失（官方协议要求指向组合 yml）')
        _yaml_required()
        patch_path = os.path.normpath(os.path.join(pkg_root, patch_rel))
        if not patch_path.startswith(os.path.normpath(pkg_root)) or not os.path.isfile(patch_path):
            raise ValueError('dsh.bundle.patch 指向的文件不存在: %s' % patch_rel)
        try:
            with open(patch_path, 'r', encoding='utf-8') as f:
                parsed = yaml.safe_load(f)
        except Exception as e:
            raise ValueError('组合 yml 不是合法 YAML: %s' % e)
        if parsed is not None and not isinstance(parsed, list):
            raise ValueError('组合 yml 必须是 Loader 行数组')
        patch_file = patch_rel

    client_file = None
    if client is not None:
        platform = client.get('platform') if isinstance(client, dict) else None
        if platform is not None and platform != 'web':
            raise ValueError('dsh.client.platform 仅支持 web（当前: %s）' % platform)
        exports = manifest.get('exports')
        rel = exports.get('./client') if isinstance(exports, dict) else None
        if isinstance(rel, dict):
            rel = rel.get('default')
        if isinstance(rel, str):
            path = os.path.normpath(os.path.join(pkg_root, rel))
            if not path.startswith(os.path.normpath(pkg_root)) or not os.path.isfile(path):
                raise ValueError('exports["./client"] 指向的文件不存在: %s' % rel)
            client_file = rel

    entry = _entry_file(pkg_root, manifest)
    if entry is None and not client_file:
        raise ValueError('无法定位插件入口（main/exports["."]），且未声明 dsh.client')

    return {
        'name': name,
        'version': str(manifest.get('version') or '0.0.0'),
        'description': str(manifest.get('description') or ''),
        'hasBundle': bundle is not None,
        'hasClient': client is not None,
        'engines': (manifest.get('engines') or {}).get('dsh'),
        'deps': sorted((manifest.get('dependencies') or {}).keys()),
        'patchFile': patch_file,
        'clientFile': client_file,
        'entryRel': os.path.relpath(entry, pkg_root).replace('\\', '/') if entry else None,
    }


# ---------------- 依赖安装（B 轨共享依赖目录） ----------------

def _store_pkg_dirs():
    """B 轨已入库插件目录清单（~/.dsh-skins/plugins/<slug>/<ver>/ 含 package.json）。"""
    out = []
    if os.path.isdir(STORE):
        for slug in sorted(os.listdir(STORE)):
            d1 = os.path.join(STORE, slug)
            if not os.path.isdir(d1):
                continue
            for ver in sorted(os.listdir(d1)):
                d2 = os.path.join(d1, ver)
                if os.path.isfile(os.path.join(d2, 'package.json')):
                    out.append(d2)
    return out


def sync_deps(extra_dirs=(), required=False):
    """B 轨依赖统一：所有入库插件（+ 待入库 extra_dirs）的 dependencies 合并后
    一次性装进 ~/.dsh-skins/plugins/node_modules（pnpm 优先、npm 兜底）。

    替代旧「每插件各自 npm install」：Node 解析插件入口时沿目录链
    …/<ver>/node_modules → …/<slug>/node_modules → plugins/node_modules 命中共享层。
    required=True（安装期）失败抛错；False（卸载期收敛）失败只忽略。"""
    union = {}
    for d in list(_store_pkg_dirs()) + list(extra_dirs):
        try:
            with open(os.path.join(d, 'package.json'), 'r', encoding='utf-8') as f:
                deps = json.load(f).get('dependencies') or {}
        except Exception:
            continue
        for k, v in deps.items():
            if isinstance(k, str) and isinstance(v, str):
                union.setdefault(k, v)
    os.makedirs(STORE, exist_ok=True)
    manifest = {'name': 'dshskin-plugin-deps', 'private': True, 'dependencies': union}
    tmp = os.path.join(STORE, 'package.json.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    os.replace(tmp, os.path.join(STORE, 'package.json'))
    if not union:
        return True
    node, _v = dsh_env.resolve_node()
    if not node:
        raise ValueError('插件声明了依赖 %s，但找不到 Node.js（无法安装依赖）' % ', '.join(sorted(union)))
    npm = None
    pnpm = dsh_env.resolve_pnpm()
    if pnpm:
        cmd = [node, pnpm, '--dir', STORE, 'install', '--ignore-scripts']
    else:
        npm = shutil.which('npm') or os.path.join(os.path.dirname(node), 'npm.cmd' if os.name == 'nt' else 'npm')
        if not os.path.isfile(npm):
            raise ValueError('插件声明了依赖，但找不到 npm / pnpm')
        cmd = [npm, '--prefix', STORE, 'install', '--omit=dev', '--no-audit', '--no-fund']
    env = dict(os.environ)
    env['PATH'] = os.path.dirname(node) + os.pathsep + env.get('PATH', '')
    # 打包版 exe（console=False）无父控制台：子进程需 CREATE_NO_WINDOW 防黑窗闪烁
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
    try:
        if os.name == 'nt' and npm and npm.lower().endswith('.cmd'):
            r = subprocess.run(['cmd', '/c'] + cmd, cwd=STORE,
                               capture_output=True, timeout=900, env=env, creationflags=flags)
        else:
            r = subprocess.run(cmd, cwd=STORE,
                               capture_output=True, timeout=900, env=env, creationflags=flags)
    except subprocess.TimeoutExpired:
        if required:
            raise ValueError('依赖安装超时（15 分钟）')
        return False
    if r.returncode != 0:
        err = ((r.stderr or r.stdout or b'')[-800:]).decode('utf-8', 'replace')
        if required:
            raise ValueError('依赖安装失败:\n%s' % err)
        return False
    return True


# ---------------- spec 解析与自动选轨 ----------------
# A 轨 = npm 包名（registry 上确实存在）；B 轨 = github 引用 / 本地目录 / 压缩包路径

_GH_URL_RE = re.compile(r'^(?:https?://)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:/tree/([^/?#]+))?/?$')
_NPM_NAME_RE = re.compile(r'^(@[a-z0-9-~][a-z0-9-._~]*/)?[a-z0-9-~][a-z0-9-._~]*$')


def parse_npm_spec(spec):
    """'name' / 'name@1.2.3' / '@scope/name' / '@scope/name@1.2.3' → (name, version|None)。
    github 引用、本地路径、压缩包路径等非 npm 形态返回 None。"""
    s = (spec or '').strip().strip('"\'')
    if not s or s.lower().startswith('github:') or '://' in s or '\\' in s or s.startswith('/'):
        return None
    if s.lower().endswith(('.tgz', '.tar.gz', '.zip')) or os.path.exists(s):
        return None
    m = re.match(r'^(@[^@\s/]+/[^@\s/]+|[^@\s/][^@\s/]*)(?:@([^@\s]+))?$', s)
    if not m:
        return None
    name, ver = m.group(1), m.group(2)
    if not _NPM_NAME_RE.match(name):
        return None
    return name, ver


def npm_lookup(name, version=None, timeout=8):
    """查 npm registry：返回 (解析版本号, 描述, tarball URL)；包不存在/网络不可达返回 None。"""
    import urllib.request
    try:
        with urllib.request.urlopen('https://registry.npmjs.org/' + name, timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8', 'replace'))
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get('versions'), dict) or not data['versions']:
        return None
    if version:
        resolved = version if version in data['versions'] else (data.get('dist-tags') or {}).get(version)
        if not resolved:
            return None
    else:
        resolved = (data.get('dist-tags') or {}).get('latest')
    meta = data['versions'].get(resolved) or {}
    return resolved, str(meta.get('description') or ''), (meta.get('dist') or {}).get('tarball')


def parse_github_spec(spec):
    """github:owner/repo[#分支|#标签] 或 GitHub URL（含 /tree/分支）→ (owner, repo, ref|None)。"""
    s = (spec or '').strip().strip('"\'')
    if not s:
        return None
    if s.lower().startswith('github:'):
        rest = s[len('github:'):]
        owner, sep, tail = rest.partition('/')
        if not sep or not owner or '/' in owner:
            return None
        repo, sep2, ref = tail.partition('#')
        if not repo or not sep2 and '/' in repo:
            return None
        return owner, repo, ref or None
    m = _GH_URL_RE.match(s)
    if m:
        return m.group(1), m.group(2), m.group(3) or None
    return None


def resolve_spec(spec):
    """spec → 安装轨判定（面板「安装时自动选轨」）。
    A 轨：npm 包名形态且 registry 上确实存在（拿到精确版本才算）；
    B 轨：github 引用 / 本地目录 / 本地 tgz·zip 路径。
    形如 npm 包名但 registry 上不存在 → 抛错（宁报错不误装）。"""
    s = (spec or '').strip()
    if not s:
        raise ValueError('请输入插件标识：npm 包名 / @scope/包名@版本 / github:owner/repo#分支 / 本地目录或 tgz 路径')
    gh = parse_github_spec(s)
    if gh:
        return {'track': 'B', 'kind': 'github', 'owner': gh[0], 'repo': gh[1], 'ref': gh[2], 'spec': s}
    if os.path.isdir(s):
        return {'track': 'B', 'kind': 'local-dir', 'spec': s}
    if os.path.isfile(s) and s.lower().endswith(('.tgz', '.tar.gz', '.zip')):
        return {'track': 'B', 'kind': 'local-pkg', 'spec': s}
    npm = parse_npm_spec(s)
    if npm:
        name, ver = npm
        hit = npm_lookup(name, ver)
        if not hit:
            raise ValueError('npm registry 上找不到 %s（或网络不可达）——已发布 npm 的插件走官方直装；'
                             '源码包请用 github:owner/repo#分支 或本地导入' % ('%s@%s' % (name, ver) if ver else name))
        resolved, desc, _tarball = hit
        return {'track': 'A', 'kind': 'npm', 'name': name, 'version': resolved,
                'description': desc, 'spec': s}
    raise ValueError('无法识别的插件标识: %s' % s)


def install_from_github(spec, targets=None):
    """B 轨 GitHub 源码包：拉 codeload tar.gz（默认分支自动解析，分支/标签都支持）
    后走 import_package，与本地包全程等价（校验 → 入库 → 统一依赖 → 装配行）。"""
    gh = parse_github_spec(spec)
    if not gh:
        raise ValueError('不是合法的 GitHub 引用: %s' % spec)
    owner, repo, ref = gh
    import urllib.request
    if not ref:
        try:
            with urllib.request.urlopen('https://api.github.com/repos/%s/%s' % (owner, repo), timeout=8) as r:
                ref = (json.load(r) or {}).get('default_branch') or 'main'
        except Exception:
            ref = 'main'
    urls = ['https://codeload.github.com/%s/%s/tar.gz/refs/heads/%s' % (owner, repo, ref),
            'https://codeload.github.com/%s/%s/tar.gz/refs/tags/%s' % (owner, repo, ref)]
    tmp = tempfile.mkdtemp(prefix='dshskin-gh-')
    try:
        tgz = os.path.join(tmp, '%s-%s.tar.gz' % (repo, ref))
        last_err = None
        for url in urls:
            try:
                with urllib.request.urlopen(url, timeout=60) as resp, open(tgz, 'wb') as f:
                    total = 0
                    while True:
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_UNCOMPRESSED:
                            raise ValueError('GitHub 仓库包超过 %d MB 上限' % (MAX_UNCOMPRESSED // 1024 // 1024))
                        f.write(chunk)
                last_err = None
                break
            except Exception as e:
                last_err = e
        if last_err is not None:
            raise ValueError('GitHub 拉取失败（%s@%s）: %s' % (owner + '/' + repo, ref, last_err))
        return import_package(tgz, targets)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------- A 轨：官方 bundle 直装（npm → profile 工程） ----------------

def _profile_dir(target):
    """A 轨官方 profile 工程目录：<dsh_home>/profiles/<target>（与官方
    `dsh plugin --profile <target> add` 同一落点，home 解析与 session/凭证同根）。"""
    home, _src = dsh_env.dsh_home()
    if not home:
        return None
    return os.path.join(home, 'profiles', target)


def _ensure_profile_project(pdir):
    """profile 工程缺失时按官方字段形状补最小 package.json（name/private/dsh.profile.bundles）；
    官方已建工程（Plugin Market 装过）则原样保留。"""
    os.makedirs(pdir, exist_ok=True)
    path = os.path.join(pdir, 'package.json')
    if not os.path.isfile(path):
        minimal = {'name': 'dsh-profile', 'private': True, 'dsh': {'profile': {'bundles': []}}}
        with open(path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(minimal, f, ensure_ascii=False, indent=2)
        os.replace(path + '.tmp', path)


def _profile_manifest(pdir):
    try:
        with open(os.path.join(pdir, 'package.json'), 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _bundles_list(pdir):
    m = _profile_manifest(pdir) or {}
    try:
        lst = ((m.get('dsh') or {}).get('profile') or {}).get('bundles')
    except Exception:
        lst = None
    return lst if isinstance(lst, list) else None


def _set_bundles_entry(pdir, name, present):
    """dsh.profile.bundles 挂名去留（A 轨启停/安装/卸载的最小写入）。
    首次改动前留 package.json.dshskin-orig 一次性备份（还原依据）。"""
    m = _profile_manifest(pdir)
    if m is None:
        if not present:
            return False
        _ensure_profile_project(pdir)
        m = _profile_manifest(pdir)
        if m is None:
            return False
    dsh = m.setdefault('dsh', {})
    prof = dsh.setdefault('profile', {})
    lst = prof.get('bundles')
    if not isinstance(lst, list):
        lst = []
        prof['bundles'] = lst
    changed = False
    if present and name not in lst:
        lst.append(name)
        changed = True
    if not present and name in lst:
        lst.remove(name)
        changed = True
    if changed:
        path = os.path.join(pdir, 'package.json')
        if not os.path.isfile(path + '.dshskin-orig') and os.path.isfile(path):
            try:
                shutil.copyfile(path, path + '.dshskin-orig')
            except OSError:
                pass
        with open(path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
        os.replace(path + '.tmp', path)
    return changed


def _run_pnpm(args, timeout=900):
    """以 corepack 管理的 pnpm.cjs + 显式 Node 执行 pnpm（与启动器同解析链）。"""
    node, _v = dsh_env.resolve_node()
    if not node:
        raise ValueError('找不到 Node.js（>=22），无法执行 pnpm')
    pnpm = dsh_env.resolve_pnpm()
    if not pnpm:
        raise ValueError('找不到 pnpm（corepack 管理版优先），无法复刻官方 profile 事务')
    env = dict(os.environ)
    env['PATH'] = os.path.dirname(node) + os.pathsep + env.get('PATH', '')
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
    try:
        r = subprocess.run([node, pnpm] + args, capture_output=True, timeout=timeout,
                           env=env, creationflags=flags)
    except subprocess.TimeoutExpired:
        raise ValueError('pnpm 执行超时')
    if r.returncode != 0:
        err = ((r.stderr or r.stdout or b'')[-1200:]).decode('utf-8', 'replace')
        raise ValueError('pnpm 执行失败:\n%s' % err)


def install_bundle_npm(name, version=None, targets=None, description=''):
    """A 轨安装（desktop 为主战场，不触碰 web profile 的 CLI 通道）。
    DSH 完全退出后复刻官方事务：
      pnpm --dir <profile> add <name>@<ver> --save-exact --ignore-scripts
      + package.json 的 dsh.profile.bundles 挂名
    profile = <dsh_home>/profiles/desktop（打包模式；dev 工程每次启动重建，直装会被
    冲掉，故 dev 模式拒绝 A 轨）。半途失败回滚已装部分，保持零残留。"""
    targets = targets or {}
    if dsh_env.is_running():
        raise ValueError('DeepSeek Harness 正在运行，请先完全退出再安装（避免 profile 事务锁冲突）')
    if dsh_env.launch_mode() != 'packaged':
        raise ValueError('A 轨桌面直装仅支持打包版（launcher_mode=packaged）；'
                         '开发模式请用「导入插件包 / 目录」走 B 轨装配行')
    pdir = _profile_dir('desktop')
    if not pdir:
        raise ValueError('桌面 profile 未解析（检查 config.dsh_home / DSH_HOME）')
    try:
        _ensure_profile_project(pdir)
        spec = '%s@%s' % (name, version) if version else name
        _run_pnpm(['--dir', pdir, 'add', spec, '--save-exact', '--ignore-scripts'])
        m = _profile_manifest(pdir) or {}
        ver = (m.get('dependencies') or {}).get(name)
        if not ver:
            raise ValueError('pnpm add 后 package.json 未出现 %s 依赖（官方事务字段异常）' % name)
        _set_bundles_entry(pdir, name, True)
    except Exception:
        try:
            _set_bundles_entry(pdir, name, False)
            _run_pnpm(['--dir', pdir, 'remove', name, '--config.ignore-scripts=true'], timeout=300)
        except Exception:
            pass
        _strip_dependency(pdir, name)
        raise
    with _inject_lock:
        reg = _load_registry()
        old = reg['plugins'].get(name, {})
        reg['plugins'][name] = {
            'name': name,
            'version': ver,
            'track': 'A',
            'description': description or old.get('description') or '',
            'origin': 'dshpp',
            'hasBundle': True,
            'hasClient': False,
            'engines': old.get('engines'),
            'deps': old.get('deps', []),
            'profiles': {'desktop': pdir},
            'dir': os.path.join(pdir, 'node_modules', *name.split('/')),
            'installedAt': old.get('installedAt') or time.strftime('%Y-%m-%d %H:%M:%S'),
            'targets': {'web': False, 'desktop': True},
            'enabled': old.get('enabled') or {'web': True, 'desktop': True},
            'config': old.get('config') or {},
            'settings': old.get('settings') or None,
        }
        _save_registry(reg)
        return dict(reg['plugins'][name])


# 收养扫描时永不纳入管理的官方/宿主包（与桌面壳 IMMUTABLE_BUNDLES 对齐 +
# 市场 INBOX_BUNDLES + 市场/社区运行时名）
ADOPT_SKIP_BUNDLES = {
    '@deepseek-ai/dsh-base',
    '@deepseek-ai/dsh-web-app',
    '@deepseek-ai/dsh-headless',
    'dshmarket',
    'dsh-community-market',
    'dsh-plugin-desktop',
    'dsh-plugin-desktop-beta',
    '@deepseek-ai/dsh-desktop-app',
}


def _bundle_meta(pdir, name):
    """从 <pdir>/node_modules/<name>/package.json 读 bundle 元数据
    → (version, description)；包缺失返回 (None, '')。"""
    try:
        with open(os.path.join(pdir, 'node_modules', *name.split('/'), 'package.json'),
                  'r', encoding='utf-8') as f:
            m = json.load(f)
        if not isinstance(m, dict):
            return None, ''
        v = m.get('version')
        d = m.get('description')
        return ((v if isinstance(v, str) and v else None),
                (d if isinstance(d, str) and d.strip() else ''))
    except Exception:
        return None, ''


# ---------------- 内置插件市场（dshmarket）状态对齐 ----------------
# 市场的持久状态在 <profile>/.dsh-market/：state.json（disabled 选择 + 分组）、
# log.ndjson（install/update 等事件）、hot-*.yml（会话级热挂载输入，每次启动清空）。
# 市场的「已安装」= profile package.json 的 dependencies（readInstalled，剔除
# INBOX_BUNDLES）；「启用」= dsh.profile.bundles。DSH++ 作为外部操作者，
# 只做最小对齐：state.json 的 disabled 列表增删 + 清理市场写在用户补丁层的
# 顶层 `- id: X / disabled: …` 行（市场文档化的外部开关形态，packagePatchFlags
# 会归因到对应包）。DSH++ 不追加市场形态的行（避免与市场自身写入重复）。

def _market_dir(pdir):
    return os.path.join(pdir, '.dsh-market')


def _market_log_installed_names(pdir):
    """从 .dsh-market/log.ndjson 的 install/update 事件提取包名（市场安装证据）。

    detail 形态：
      'desktop install boundary needs an exact version: <name>[@tag] -> <name>@<ver>'
      '<name> exit=0 hot=true' / '<name> -> <name>@latest exit=0'
    """
    names = set()
    path = os.path.join(_market_dir(pdir), 'log.ndjson')
    if not os.path.isfile(path):
        return names
    pat = re.compile(
        r'^(?:desktop install boundary needs an exact version:\s*)?'
        r'(@[^@\s]+/[^@\s]+|[^@\s]+?)(?:@[^\s]+)?(?:\s*->|\s+exit=)')
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if not isinstance(ev, dict) or ev.get('event') not in ('install', 'update'):
                    continue
                detail = ev.get('detail')
                if not isinstance(detail, str):
                    continue
                m = pat.match(detail)
                if m:
                    names.add(m.group(1))
    except OSError:
        pass
    return names


def _market_state_sync_disabled(pdir, name, disabled):
    """把 name 在 .dsh-market/state.json 的 disabled 列表中对齐到 DSH++ A 轨状态
    （停用→加入，启用/卸载→移除）。只动 disabled 数组，其余字段原样；
    市场从未运行（无 state.json）则跳过。best-effort，失败不阻断主操作。"""
    path = os.path.join(_market_dir(pdir), 'state.json')
    if not os.path.isfile(path):
        return
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return
        cur = data.get('disabled')
        cur = [x for x in cur if isinstance(x, str)] if isinstance(cur, list) else []
        if (name in cur) == bool(disabled):
            return
        if disabled:
            cur.append(name)
        else:
            cur.remove(name)
        data['disabled'] = cur
        with open(path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(path + '.tmp', path)
    except Exception:
        pass


def _parse_patch_inserted_ids(text):
    """dshmarket parsePatchRows 的 line-wise 移植：收集 patch 文本中
    `insert:` 块内嵌套的 loader entry id（#147 语义：顶层 `- id:` 行
    是别人家的行，不算插入）。"""
    inserted = set()
    insert_indent = None
    for raw in text.split('\n'):
        line = re.sub(r'#.*$', '', raw.replace('\r', ''))
        if line.strip() == '':
            continue
        indent = len(line) - len(line.lstrip(' '))
        if insert_indent is not None and indent <= insert_indent \
                and not re.match(r'^\s*-?\s*(id|name|config):', line):
            insert_indent = None
        if re.match(r'^\s*-?\s*insert:\s*$', line):
            insert_indent = indent
            continue
        m = re.match(r"^\s*-?\s*id:\s*['\"]?([^'\"\s]+)", line)
        if m:
            if insert_indent is not None and indent > insert_indent:
                inserted.add(m.group(1))
            else:
                insert_indent = None
    return inserted


def _patch_inserted_ids_for_package(pdir, name):
    """包自己「插入」的 loader entry id 集合（市场 bundlePatchInsertedIds 口径）：
    声明的 dsh.bundle.patch + 约定根 cordis.patch.yml，两源取并。
    包目录缺失（已卸载）返回空集。"""
    pkg = os.path.join(pdir, 'node_modules', *name.split('/'))
    paths = []
    try:
        with open(os.path.join(pkg, 'package.json'), 'r', encoding='utf-8') as f:
            m = json.load(f)
        declared = ((m.get('dsh') or {}).get('bundle') or {}).get('patch') if isinstance(m, dict) else None
        if isinstance(declared, str) and declared:
            paths.append(os.path.join(pkg, declared))
    except Exception:
        pass
    paths.append(os.path.join(pkg, 'cordis.patch.yml'))
    ids = set()
    for path in paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                ids |= _parse_patch_inserted_ids(f.read())
        except Exception:
            continue
    return ids


def _clear_market_disable_rows(patch_path, ids):
    """移除用户补丁层中市场形态的顶层 `- id: X` + `disabled: …` 行
    （X ∈ ids）——DSH++ 启用/卸载后的陈旧行清理（市场启用时可能留
    disabled:false 强使能行，停用留 disabled:true 行，两者在包重新挂名/移除后
    都应清掉，避免下次启动意外强制使能/禁用）。
    外科手术式按行删除，注释与其余内容原样保留；无可删返回 False。"""
    if not ids or not os.path.isfile(patch_path):
        return False
    try:
        with open(patch_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.read().split('\n')
    except Exception:
        return False
    keep = []
    i = 0
    changed = False
    while i < len(lines):
        line = lines[i]
        # 只匹配顶层行（市场 rowBlock 形态，indent 0）；嵌套 insert 块的行永不触碰
        m = re.match(r"^-\s+id:\s*['\"]?([^'\"\s]+)", line.replace('\r', ''))
        if m and m.group(1) in ids:
            j = i + 1
            if j < len(lines) and re.match(r"^\s+disabled:\s*(true|false)\s*$", lines[j].replace('\r', '')):
                changed = True
                i = j + 1
                continue
        keep.append(line)
        i += 1
    if not changed:
        return False
    try:
        with open(patch_path, 'w', encoding='utf-8', newline='') as f:
            f.write('\n'.join(keep))
        return True
    except Exception:
        return False


def adopt_profile_bundles():
    """把 profile 的第三方依赖收养为 A 轨记录（幂等）。

    扫描源 = profile package.json 的 dependencies（内置市场 dshmarket 的
    「已安装」口径 readInstalled），不是 bundles——市场里「停用」的插件
    （依赖保留、bundles 摘名）同样要可见可管。enabled 取 name 是否在
    bundles。元数据（版本/描述）取 node_modules 下真实包的 package.json；
    origin：log.ndjson 有 install/update 证据 → 'market'（内置市场安装），
    否则 'profile'（DSH++ 直装/其他来源）。

    收养后走既有 A 轨语义：启停 = bundles 挂名去留（依赖保留）+ 市场
    state.json 对齐，卸载 = pnpm remove + 摘名（零残留）。
    返回新收养/补接线的包名列表。"""
    added = []
    with _inject_lock:
        reg = _load_registry()
        for target in ('desktop', 'web'):
            pdir = _profile_dir(target)
            if not pdir or not os.path.isfile(os.path.join(pdir, 'package.json')):
                continue
            m = _profile_manifest(pdir) or {}
            deps = m.get('dependencies')
            deps = deps if isinstance(deps, dict) else {}
            bundles = _bundles_list(pdir) or []
            market_names = _market_log_installed_names(pdir)
            for name in list(deps.keys()):
                if not isinstance(name, str) or name in ADOPT_SKIP_BUNDLES:
                    continue
                other = 'web' if target == 'desktop' else 'desktop'
                ver, desc = _bundle_meta(pdir, name)
                if ver is None:
                    spec = deps.get(name)
                    if isinstance(spec, str) and not spec.startswith(('file:', 'link:', 'http:', 'git')):
                        ver = spec.lstrip('^~>=< ')
                rec = reg['plugins'].get(name)
                if rec is None:
                    origin = 'market' if name in market_names else 'profile'
                    reg['plugins'][name] = {
                        'name': name,
                        'version': ver,
                        'track': 'A',
                        'description': desc or 'Profile bundle（自 profile 依赖自动收养）',
                        'origin': origin,
                        'profiles': {target: pdir},
                        'installedAt': time.strftime('%Y-%m-%d %H:%M:%S'),
                        'targets': {target: True, other: False},
                        'enabled': {target: bool(name in bundles), other: False},
                        'config': {},
                        'settings': None,
                    }
                    added.append(name)
                elif rec.get('track', 'B') == 'A':
                    # 已在册（DSH++ A 轨装过 / 之前收养过）：补 profile 接线，
                    # 用磁盘上的真实包刷新版本/描述，不覆盖用户已设的 enabled
                    profs = rec.setdefault('profiles', {})
                    tg = rec.setdefault('targets', {})
                    en = rec.setdefault('enabled', {})
                    ch = False
                    if not profs.get(target):
                        profs[target] = pdir
                        ch = True
                    if not tg.get(target):
                        tg[target] = True
                        ch = True
                    if target not in en:
                        en[target] = bool(name in bundles)
                        ch = True
                    if ver and rec.get('version') != ver:
                        rec['version'] = ver
                        ch = True
                    # 真实描述优先：空值或收养占位文案（'Profile bundle（自…'）都替换
                    if desc and (not rec.get('description')
                                 or str(rec.get('description', '')).startswith('Profile bundle（自')):
                        rec['description'] = desc
                        ch = True
                    if 'origin' not in rec:
                        rec['origin'] = 'market' if name in market_names else 'profile'
                        ch = True
                    if ch:
                        added.append(name)
        if added:
            _save_registry(reg)
    return added


# ---------------- profile 补丁层路径 ----------------

def _web_patch_path():
    # home 与桌面端同根解析（config.dsh_home → env DSH_HOME → ~/.dsh）：
    # 数据根迁移后若仍按 env/默认家目录拼接，会把 web 补丁写到错误位置
    home, _src = dsh_env.dsh_home()
    if not home:
        return None
    profile = os.environ.get('DSH_PROFILE') or 'web'
    return os.path.join(home, 'profiles', profile, 'cordis.patch.yml')


def _desktop_project_dir():
    """桌面端 cordis 工程目录（按启动模式）。

    dev（现状）：<harness>/apps/desktop/.desktop-build/development/project（源码开发构建产物）；
    packaged（打包版）：<dsh_home>/profiles/desktop（打包版工程，home 跟随
    config.dsh_home / env DSH_HOME / 默认 ~/.dsh，与 session/凭证同一数据根）。
    """
    if dsh_env.launch_mode() == 'packaged':
        home, _src = dsh_env.dsh_home()
        if not home:
            return None
        return os.path.join(home, 'profiles', 'desktop')
    root = dsh_env.harness_root()
    if not root:
        return None
    return os.path.join(root, 'apps', 'desktop', '.desktop-build', 'development', 'project')


def _desktop_patch_path():
    """桌面端装配文件路径（= 该桌面 profile 的用户补丁层 cordis.patch.yml）。

    dev：<harness>/apps/desktop/.desktop-build/development/project/cordis.patch.yml
        （源码开发构建产物目录，每次启动重建，由注入线程按 registry 反复补写）；
    packaged：<dsh_home>/profiles/desktop/cordis.patch.yml —— 官方 desktop-host
        loadProfileDirectory 的 PROFILE_PATCH_FILENAME 用户层；组合根 desktop.cordis.yml
        是包私有文件（"package transactions own this file"）每次启动重置为官方默认，
        外来插件的正规落点就是该 profile 用户层，写它启动后仍保留。
    """
    project = _desktop_project_dir()
    if not project:
        return None
    return os.path.join(project, 'cordis.patch.yml')


def _file_url(path):
    return 'file:///' + os.path.abspath(path).replace('\\', '/')


# ---------------- 托管块构建与写盘 ----------------

def _row_entry(record, target):
    _yaml_required()
    entry_rel = record.get('entryRel') or 'lib/index.js'
    entry = os.path.join(record.get('dir') or '', entry_rel)
    if not os.path.isfile(entry):
        cand = _store_candidate_dir(record)   # dir 指向已失效位置时回退到当前 STORE
        if cand:
            entry = os.path.join(cand, entry_rel)
    if not os.path.isfile(entry):
        raise ValueError('插件 %s 缺少入口文件 %s' % (record['name'], entry_rel))
    row = {
        'id': ROW_ID_PREFIX + record['name'],
        'name': _file_url(entry),
    }
    cfg = record.get('config')
    if isinstance(cfg, dict) and cfg:
        row['config'] = cfg
    if not record.get('enabled', {}).get(target, True):
        row['disabled'] = True
    return row


def _build_block(records, target):
    lines = [BLOCK_BEGIN]
    for record in records:
        row = _row_entry(record, target)
        lines.append('- insert:')
        lines.append('    - id: %s' % row['id'])
        lines.append("      name: '%s'" % row['name'])
        if row.get('config'):
            lines.append('      config:')
            cfg_lines = yaml.safe_dump(row['config'], allow_unicode=True, sort_keys=False).strip().splitlines()
            for ln in cfg_lines:
                lines.append('      ' + ln)
        if row.get('disabled'):
            lines.append('      disabled: true')
    lines.append(BLOCK_END)
    return '\n'.join(lines)


def _strip_managed(text):
    return re.sub(re.escape(BLOCK_BEGIN) + r'.*?' + re.escape(BLOCK_END) + r'\n?', '', text, flags=re.S)


def _orig_path(path):
    return path + '.orig'


def _ensure_original_backup(path, existing):
    """首次介入前把原始 patch 文件另存为 .orig（一次性，后续永不覆盖）。

    判定「首次」：.orig 不存在，且当前内容里没有我们的托管块。
    这样 .orig 永远代表 DSHSkin 介入之前的原貌，是「完全还原」的依据。
    """
    orig = _orig_path(path)
    if os.path.isfile(orig):
        return False
    if BLOCK_BEGIN in existing:
        return False  # 已含托管块却没 .orig（异常/外部清理过），不把含托管块的内容当原始备份
    try:
        with open(orig, 'w', encoding='utf-8') as f:
            f.write(existing)
        return True
    except OSError:
        return False


def _write_patch_file(path, records, target):
    """把托管块写进 cordis.patch.yml；托管块之外的用户内容尽量原样保留。

    规则：剩余原文为空/空数组 → 托管块（或 `[]`）自成文档；块式行数组 → 直接
    追加（同一序列延续，合法）；flow 式数组 → 转块式再追加；解析失败 → 拒写。
    """
    _yaml_required()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = ''
    if os.path.isfile(path):
        with open(path, 'r', encoding='utf-8') as f:
            existing = f.read()
    _ensure_original_backup(path, existing)
    base = _strip_managed(existing).strip()

    if not records:
        if not base:
            # 无插件且无用户内容：不留空文件，避免与 dev.ts 的目录重建竞争
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
                if os.path.isfile(path + '.bak'):
                    try:
                        os.remove(path + '.bak')
                    except OSError:
                        pass
            return
        new_text = base + '\n'
    elif not base:
        new_text = _build_block(records, target) + '\n'
    else:
        try:
            head = yaml.safe_load(base)
        except Exception as e:
            raise ValueError('cordis.patch.yml 不是合法 YAML，拒绝写入 %s: %s' % (path, e))
        if head is None:
            new_text = base + '\n\n' + _build_block(records, target) + '\n'
        elif isinstance(head, list) and len(head) == 0:
            # 空数组：保留原注释头（如 desktop.cordis.yml 的官方说明注释），托管块跟随
            keep = [ln for ln in base.splitlines()
                    if ln.strip() and ln.strip() != '[]' and not ln.lstrip().startswith('-')]
            if keep:
                new_text = '\n'.join(keep) + '\n\n' + _build_block(records, target) + '\n'
            else:
                new_text = _build_block(records, target) + '\n'
        elif isinstance(head, list):
            if base.startswith('['):
                base = yaml.safe_dump(head, allow_unicode=True, sort_keys=False)
            new_text = base + '\n\n' + _build_block(records, target) + '\n'
        else:
            raise ValueError('cordis.patch.yml 不是 Loader 行数组，拒绝写入: %s' % path)

    if os.path.isfile(path):
        shutil.copyfile(path, path + '.bak')
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(new_text)
    os.replace(tmp, path)


def _read_managed_ids(path):
    if not os.path.isfile(path):
        return set()
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    m = re.search(re.escape(BLOCK_BEGIN) + r'(.*?)' + re.escape(BLOCK_END), text, flags=re.S)
    if not m:
        return set()
    try:
        parsed = yaml.safe_load(m.group(1)) or []
    except Exception:
        return set()
    ids = set()
    for entry in parsed:
        if isinstance(entry, dict) and isinstance(entry.get('insert'), list):
            for row in entry['insert']:
                if isinstance(row, dict) and isinstance(row.get('id'), str):
                    ids.add(row['id'])
    return ids


# ---------------- 对外 API ----------------

def list_plugins():
    # 幂等：profile 依赖（内置市场「已安装」口径）的第三方包自动纳入 A 轨管理
    adopt_profile_bundles()
    reg = _load_registry()
    web_patch = _web_patch_path()
    wired_web = _read_managed_ids(web_patch) if web_patch else set()
    desktop_project = _desktop_project_dir()
    desktop_patch = _desktop_patch_path()
    wired_desktop = _read_managed_ids(desktop_patch) if desktop_patch else set()
    plugins = []
    for name, record in reg['plugins'].items():
        track = record.get('track', 'B')
        profiles = record.get('profiles') or {}
        if track == 'A':
            # A 轨接线 = dsh.profile.bundles 挂名存在；存活 = profile node_modules 有包体
            alive = any(os.path.isdir(os.path.join(p, 'node_modules', *name.split('/')))
                        for p in profiles.values())
            wired = {t: bool(profiles.get(t)) and name in (_bundles_list(profiles[t]) or [])
                     for t in ('web', 'desktop')}
        else:
            alive = os.path.isdir(record.get('dir', ''))
            wired = {
                'web': (ROW_ID_PREFIX + name) in wired_web,
                'desktop': (ROW_ID_PREFIX + name) in wired_desktop,
            }
        plugins.append({
            'name': name,
            'track': track,
            'origin': record.get('origin'),
            'profiles': {t: p for t, p in profiles.items()} or None,
            'version': record.get('version'),
            'description': record.get('description'),
            'hasBundle': record.get('hasBundle'),
            'hasClient': record.get('hasClient'),
            'engines': record.get('engines'),
            'deps': record.get('deps', []),
            'installedAt': record.get('installedAt'),
            'targets': record.get('targets', {}),
            'enabled': record.get('enabled', {}),
            'config': record.get('config') or {},
            'settings': record.get('settings') or None,
            'alive': alive,
            'wired': wired,
            'hasSrcDir': bool(record.get('src')) and os.path.isdir(record.get('src')),
        })
    plugins.sort(key=lambda p: p['name'])
    home, _src = dsh_env.dsh_home()
    return {
        'enabled': reg.get('enabled', True),
        'plugins': plugins,
        'webPatch': web_patch,
        'desktopProject': desktop_project,
        'desktopPatch': desktop_patch,
        'desktopProfile': _profile_dir('desktop') if dsh_env.launch_mode() == 'packaged' else None,
        'dshRunning': dsh_env.is_running(),
        'settingsFile': os.path.join(home, 'settings.yaml') if home else None,
        'store': STORE,
    }


def import_dir(pkg_dir, targets=None, src=None):
    """从本地目录导入插件（前端 webkitdirectory 选择后重建的临时目录，或 reload 时直接给定源目录）。
    与 import_package 全程等价：校验 → 拷贝入库 → 装依赖 → 重写补丁层。
    src：可选的源目录绝对路径；目录导入 / reload 时记录，供「从源目录重载」复用（非目录来源为 None）。
    """
    with _inject_lock:
        targets = targets or {}
        want_web = bool(targets.get('web', True))
        want_desktop = bool(targets.get('desktop', True))
        info = validate_protocol(pkg_dir)
        name = info['name']
        store_dir = os.path.join(STORE, name.replace('/', '__'), info['version'])
        if os.path.isdir(store_dir):
            shutil.rmtree(store_dir)
        shutil.copytree(pkg_dir, store_dir,
                        ignore=shutil.ignore_patterns('node_modules', '.git'))
        sync_deps(required=True)
        reg = _load_registry()
        old = reg['plugins'].get(name, {})
        reg['plugins'][name] = {
            'name': name,
            'version': info['version'],
            'track': 'B',
            'description': info['description'],
            'hasBundle': info['hasBundle'],
            'hasClient': info['hasClient'],
            'engines': info['engines'],
            'deps': info['deps'],
            'entryRel': info['entryRel'],
            'dir': store_dir,
            'src': src or old.get('src'),
            'installedAt': old.get('installedAt') or time.strftime('%Y-%m-%d %H:%M:%S'),
            'targets': {'web': want_web, 'desktop': want_desktop},
            'enabled': old.get('enabled') or {'web': True, 'desktop': True},
            'config': old.get('config') or {},
            'settings': old.get('settings') or None,
        }
        _save_registry(reg)
        apply_rows()
        return dict(info, track='B', targets=reg['plugins'][name]['targets'], store=store_dir)


def _pick_srcdir():
    """弹出原生「选择插件源目录」对话框，返回绝对路径；取消返回 None。
    用 tkinter（标准库，打包与浏览器降级路径皆可用）。"""
    try:
        import tkinter as _tk
        from tkinter import filedialog as _fd
        _root = _tk.Tk()
        _root.withdraw()
        _root.attributes('-topmost', True)
        try:
            path = _fd.askdirectory(parent=_root, title='选择插件源目录（用于重载）')
        finally:
            try:
                _root.destroy()
            except Exception:
                pass
        return path or None
    except Exception as e:
        raise ValueError('无法弹出目录选择对话框: {0}（请确认本机可用图形界面）'.format(e))


def reload_from_dir(name):
    """按记录的源目录重新读取并同步插件（免去重复上传）。
    - 已记录 src 且目录存在 → 直接重载；
    - 未记录或目录失效 → 弹原生对话框选一次并记住；
    - 取消 → 返回 cancelled。
    复刻 import_dir（校验 → 重拷入库 → 装依赖 → 重写补丁层），并保留 enabled/config/settings。
    """
    with _inject_lock:
        reg = _load_registry()
        rec = reg['plugins'].get(name)
        if not rec:
            raise ValueError('插件不存在: %s' % name)
        if rec.get('track') == 'A':
            raise ValueError('A 轨官方 bundle 插件不支持从目录重载（非目录来源）')
        src = rec.get('src') or ''
        if not src or not os.path.isdir(src):
            src = _pick_srcdir()
            if not src:
                return {'ok': False, 'cancelled': True}
            if not os.path.isdir(src):
                raise ValueError('选择的源目录不存在或不可读: %s' % src)
        targets = rec.get('targets') or {'web': True, 'desktop': True}
        info = import_dir(src, {k: bool(v) for k, v in targets.items()}, src=src)
        return {'ok': True, 'src': src, 'info': info}


def import_package(src_path, targets=None):
    """导入 zip/tgz 插件包：校验 → 入库 → 装依赖 → 重写两处补丁层。"""
    with _inject_lock:
        targets = targets or {}
        want_web = bool(targets.get('web', True))
        want_desktop = bool(targets.get('desktop', True))
        tmp = tempfile.mkdtemp(prefix='dshskin-plugin-')
        try:
            pkg_root = extract_package(src_path, tmp)
            info = validate_protocol(pkg_root)
            name = info['name']
            store_dir = os.path.join(STORE, name.replace('/', '__'), info['version'])
            if os.path.isdir(store_dir):
                shutil.rmtree(store_dir)
            shutil.copytree(pkg_root, store_dir)
            sync_deps(required=True)
            reg = _load_registry()
            old = reg['plugins'].get(name, {})
            reg['plugins'][name] = {
                'name': name,
                'version': info['version'],
                'track': 'B',
                'description': info['description'],
                'hasBundle': info['hasBundle'],
                'hasClient': info['hasClient'],
                'engines': info['engines'],
                'deps': info['deps'],
                'entryRel': info['entryRel'],
                'dir': store_dir,
                'installedAt': old.get('installedAt') or time.strftime('%Y-%m-%d %H:%M:%S'),
                'targets': {'web': want_web, 'desktop': want_desktop},
                'enabled': old.get('enabled') or {'web': True, 'desktop': True},
                'config': old.get('config') or {},
                'settings': old.get('settings') or None,
            }
            _save_registry(reg)
            apply_rows()
            return dict(info, track='B', targets=reg['plugins'][name]['targets'], store=store_dir)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def apply_rows():
    """按 registry 当前状态重写 web 与桌面两处补丁层（幂等）。
    总开关 enabled=False 时清空插件行（各插件 enabled 状态保留显示，注入整体失效）。"""
    with _inject_lock:
        reg = _load_registry()
        on = bool(reg.get('enabled', True))
        web_patch = _web_patch_path()
        if web_patch:
            records = [r for r in reg['plugins'].values()
                       if r.get('targets', {}).get('web') and r.get('track', 'B') != 'A'] if on else []
            _write_patch_file(web_patch, records, 'web')
        project = _desktop_project_dir()
        desktop_patch = _desktop_patch_path()
        if project and desktop_patch:
            # dev：项目目录未建（dev.ts 重建期）不写，避免与目录重建竞争；
            # packaged：profile 目录由桌面版启动后自建，先写 cordis.patch.yml 无害且必达
            if dsh_env.launch_mode() == 'packaged' or os.path.isfile(os.path.join(project, 'package.json')):
                records = [r for r in reg['plugins'].values()
                           if r.get('targets', {}).get('desktop') and r.get('track', 'B') != 'A'] if on else []
                _write_patch_file(desktop_patch, records, 'desktop')


def _all_patch_paths():
    """DSHSkin 托管的两处 cordis.patch.yml（web profile + 桌面 profile/项目）。"""
    paths = []
    web = _web_patch_path()
    if web:
        paths.append(web)
    project = _desktop_project_dir()
    desktop_patch = _desktop_patch_path()
    if project and desktop_patch:
        paths.append(desktop_patch)
    return paths


def restore_managed(disable_registry=True):
    """移除托管块、把 cordis.patch.yml 还原到 DSHSkin 介入前（完全可逆）。

    - 有 .orig：用首写前原始备份精确还原（原始为空/不存在则删除当前文件）；
    - 无 .orig：剥离托管块，保留用户自己的其他行；
    - disable_registry=True 时同时关闭 registry 中所有插件的 web/desktop 启用，
      避免 start_injector 后台线程把托管块又补回来。
    返回 {path: action} 报告。
    """
    report = {}
    with _inject_lock:
        if disable_registry:
            reg = _load_registry()
            changed = False
            for rec in reg.get('plugins', {}).values():
                en = rec.setdefault('enabled', {})
                if en.get('web') or en.get('desktop'):
                    en['web'] = False
                    en['desktop'] = False
                    changed = True
                # A 轨：还原 = bundles 摘名（package.json 有 .dshskin-orig 备份可回溯）
                if rec.get('track') == 'A':
                    for _target, pdir in (rec.get('profiles') or {}).items():
                        try:
                            if pdir and os.path.isdir(pdir):
                                _set_bundles_entry(pdir, rec['name'], False)
                        except Exception:
                            pass
            if changed:
                _save_registry(reg)

        for path in _all_patch_paths():
            if not os.path.isfile(path):
                for suf in ('.orig', '.bak'):
                    try:
                        os.remove(path + suf)
                    except OSError:
                        pass
                report[path] = 'absent'
                continue
            with open(path, 'r', encoding='utf-8') as f:
                cur = f.read()
            orig = _orig_path(path)
            if os.path.isfile(orig):
                with open(orig, 'r', encoding='utf-8') as f:
                    original = f.read()
                if original.strip():
                    tmp = path + '.tmp'
                    with open(tmp, 'w', encoding='utf-8') as f:
                        f.write(original)
                    os.replace(tmp, path)
                    action = 'restored-from-orig'
                else:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    action = 'removed-originally-absent'
            elif BLOCK_BEGIN in cur:
                stripped = _strip_managed(cur).strip()
                if stripped:
                    tmp = path + '.tmp'
                    with open(tmp, 'w', encoding='utf-8') as f:
                        f.write(stripped + '\n')
                    os.replace(tmp, path)
                    action = 'stripped-block-kept-user'
                else:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    action = 'removed-empty'
            else:
                action = 'already-clean'
            for suf in ('.orig', '.bak'):
                try:
                    os.remove(path + suf)
                except OSError:
                    pass
            report[path] = action
    return report


def _strip_dependency(pdir, name):
    """卸载兜底：pnpm remove 后 dependencies 仍含 name 时（版本怪癖/手工残留）
    摘除该键，保持官方字段形状，确保零残留成立。"""
    m = _profile_manifest(pdir)
    if m is None:
        return
    deps = m.get('dependencies')
    if not isinstance(deps, dict) or name not in deps:
        return
    deps.pop(name, None)
    if not deps:
        m['dependencies'] = {}
    path = os.path.join(pdir, 'package.json')
    with open(path + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    os.replace(path + '.tmp', path)


def remove_bundle_npm(name):
    """A 轨卸载：bundles 摘名 + pnpm remove（profiles/desktop 零残留）。
    DSH 运行时拒绝（pnpm remove 需要 profile 事务锁空闲）。"""
    if dsh_env.is_running():
        raise ValueError('DeepSeek Harness 正在运行，请先完全退出再卸载（A 轨需执行 pnpm remove）')
    with _inject_lock:
        reg = _load_registry()
        record = reg['plugins'].get(name)
        if not record or record.get('track') != 'A':
            raise ValueError('未安装 A 轨插件: %s' % name)
        for target, pdir in (record.get('profiles') or {}).items():
            if not pdir or not os.path.isdir(pdir):
                continue
            # 先收集本包插入的 loader entry id（包目录删除后就读不到了），
            # 用于卸载后清理用户补丁层的市场 disabled 行
            row_ids = _patch_inserted_ids_for_package(pdir, name)
            try:
                _set_bundles_entry(pdir, name, False)
            except Exception:
                pass
            try:
                _run_pnpm(['--dir', pdir, 'remove', name, '--config.ignore-scripts=true'], timeout=600)
            except ValueError:
                # 依赖可能已不在（手工清理过）：挂名已摘，继续卸载
                pass
            _strip_dependency(pdir, name)
            # 对齐内置市场状态（best-effort）：disabled 列表摘名 + 清陈旧行
            _market_state_sync_disabled(pdir, name, False)
            _clear_market_disable_rows(os.path.join(pdir, 'cordis.patch.yml'), row_ids)
        reg['plugins'].pop(name, None)
        _save_registry(reg)
        return record


def set_enabled(name, target, enabled):
    with _inject_lock:
        reg = _load_registry()
        record = reg['plugins'].get(name)
        if not record:
            raise ValueError('未安装插件: %s' % name)
        if target not in ('web', 'desktop'):
            raise ValueError('未知目标: %s' % target)
        record.setdefault('enabled', {})[target] = bool(enabled)
        _save_registry(reg)
        if record.get('track') == 'A':
            # A 轨启停 = bundles 挂名去留（依赖保留，官方语义）；只影响下次启动
            pdir = (record.get('profiles') or {}).get(target)
            if pdir and os.path.isdir(pdir):
                _set_bundles_entry(pdir, name, bool(enabled))
                if bool(enabled):
                    # 启用：清掉市场/外部写在用户补丁层的本包 disabled 行
                    # （disabled:true 会让下次启动仍禁用；disabled:false 强使能行
                    # 会挡住未来的合法停用），并对齐市场 disabled 列表
                    ids = _patch_inserted_ids_for_package(pdir, name)
                    _clear_market_disable_rows(os.path.join(pdir, 'cordis.patch.yml'), ids)
                    _market_state_sync_disabled(pdir, name, False)
                else:
                    # 停用：对齐市场 disabled 列表（市场 UI 的开关状态来源之一）；
                    # 不写市场形态的补丁行（那是市场自己的 HMR 通道，避免重复写入）
                    _market_state_sync_disabled(pdir, name, True)
        else:
            apply_rows()


def set_enabled_all(enabled):
    """插件管理总开关：关闭时清空所有补丁行与 A 轨 bundles 挂名（注入整体失效），
    单个插件的 enabled 状态保留在 registry，重新开启后按各自状态恢复。"""
    with _inject_lock:
        reg = _load_registry()
        reg['enabled'] = bool(enabled)
        _save_registry(reg)
        for rec in reg['plugins'].values():
            if rec.get('track') != 'A':
                continue
            for target, pdir in (rec.get('profiles') or {}).items():
                try:
                    if pdir and os.path.isdir(pdir):
                        want = bool(enabled) and rec.get('enabled', {}).get(target, True)
                        _set_bundles_entry(pdir, rec['name'], want)
                        # 对齐内置市场 disabled 列表（best-effort）
                        _market_state_sync_disabled(pdir, rec['name'], not want)
                        if want:
                            ids = _patch_inserted_ids_for_package(pdir, rec['name'])
                            _clear_market_disable_rows(os.path.join(pdir, 'cordis.patch.yml'), ids)
                except Exception:
                    pass
        apply_rows()


def set_plugin_config(name, text):
    """写入插件偏好（装配行 config 段）。text 为空则清空。
    按官方协议，装配 config 型插件（无 installSection settings）的偏好
    就落在装配层 Loader 行的 config: 段；settings 型插件仍走 settings.yaml。"""
    _yaml_required()
    with _inject_lock:
        reg = _load_registry()
        record = reg['plugins'].get(name)
        if not record:
            raise ValueError('未安装插件: %s' % name)
        if record.get('track') == 'A':
            raise ValueError('官方直装（A 轨）插件没有装配行 config，偏好请选 settings.yaml 命名空间落点')
        text = (text or '').strip()
        if not text:
            data = {}
        else:
            try:
                data = yaml.safe_load(text)
            except Exception as e:
                raise ValueError('偏好不是合法 YAML: %s' % e)
            if data is None:
                data = {}
            if not isinstance(data, dict):
                raise ValueError('偏好必须是 YAML 映射（key: value 形式）')
        record['config'] = data
        _save_registry(reg)
        apply_rows()


_NS_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]*$')


def default_namespace(name):
    """settings.yaml 命名空间默认值 = 插件短名（去 @scope/，只留 [a-z0-9-]，小写）。"""
    short = (name or '').rsplit('/', 1)[-1].lower()
    ns = re.sub(r'[^a-z0-9-]+', '-', short).strip('-')
    return ns or 'plugin'


def _ns_block(text, ns_re):
    """定位命名空间块的文本区间，返回 (start_line, end_line_exclusive) 或 None。

    块 = `^ns:` 行起，到下一个顶格行（首个字符非空白，含列 0 的注释行）止；
    无后续顶格行则到文档末。列 0 注释视为块的边界（归后一个键），宁留勿删。
    """
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if re.match(ns_re + r'\s*:', ln):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        if ln and not ln[0].isspace():
            end = j
            break
    return start, end


def set_settings_namespace(ns, text):
    """把插件偏好写进 $DSH_HOME/settings.yaml 的命名空间（settings 型插件）。

    零侵入、保格式：只替换/新增/删除该命名空间自身的块（行级文本手术），
    同文档里其他命名空间、注释、锚点原样保留；仅当原文档是非块式映射
    （flow 式单行 / JSON）无法行级定位时才整体重序列化。
    text 为空 = 删除该命名空间（恢复启用该插件的默认）。
    """
    ns = (ns or '').strip().lower()
    if not _NS_NAME_RE.match(ns):
        raise ValueError('命名空间只能是小写字母/数字/连字符（如 deepseek-web）: %s' % ns)
    home, _src = dsh_env.dsh_home()
    if not home:
        raise ValueError('无法解析 DSH 数据根（dsh_home）')
    sfile = os.path.join(home, 'settings.yaml')
    existing = ''
    if os.path.isfile(sfile):
        try:
            with open(sfile, 'r', encoding='utf-8') as f:
                existing = f.read()
        except OSError as e:
            raise ValueError('无法读取 settings.yaml: %s' % e)
    raw = existing.lstrip('\ufeff')
    if raw.strip():
        try:
            parsed = yaml.safe_load(raw)
        except Exception as e:
            raise ValueError('settings.yaml 不是合法 YAML: %s' % e)
        if not isinstance(parsed, dict):
            raise ValueError('settings.yaml 顶层必须是 YAML 映射（namespace: 键值）')
    else:
        parsed = {}
        raw = ''

    text = (text or '').strip()
    if text:
        try:
            val = yaml.safe_load(text)
        except Exception as e:
            raise ValueError('偏好不是合法 YAML: %s' % e)
        if not isinstance(val, dict):
            raise ValueError('命名空间内容必须是 YAML 映射')
    else:
        val = None

    block = None
    if val is not None:
        block = yaml.safe_dump({ns: val}, allow_unicode=True, sort_keys=False,
                               default_flow_style=False).rstrip('\n')
    span = _ns_block(raw, re.escape(ns)) if raw.strip() else None
    flow_style = raw.strip()[:1] in ('{', '[')   # 单行 flow/JSON 文档：行级插入不可行

    if flow_style or (span is None and parsed and ns in parsed):
        # 兼容兜底：文档是 flow/JSON 单行，或该命名空间以非块式存在 → 整体重序列化
        # （其余键语义保留；仅此类文档会丢注释/格式）
        data = dict(parsed)
        if val is None:
            data.pop(ns, None)
        else:
            data[ns] = val
        new_text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                                  default_flow_style=False)
    elif val is None and span is None:
        new_text = raw  # 命名空间本就不存在，删除 = 无操作
    else:
        lines = raw.splitlines() if raw.strip() else []
        if span is not None:
            new_lines = lines[:span[0]] + ([block] if block is not None else []) + lines[span[1]:]
            if block is None:
                # 删除：吞掉紧随块后的一个空行，避免残留双空行
                if len(new_lines) > span[0] and new_lines[span[0]] == '':
                    del new_lines[span[0]]
        else:
            if lines and lines[-1] != '':
                lines.append('')
            new_lines = lines + [block]
        new_text = '\n'.join(new_lines)
        if new_text and not new_text.endswith('\n'):
            new_text += '\n'

    # 写盘前校验成稿仍是合法 YAML，且命名空间命中目标值（不命中则取消，绝不破坏原文件）
    try:
        chk = yaml.safe_load(new_text) or {}
    except Exception as e:
        raise ValueError('写入失败：成稿不是合法 YAML: %s' % e)
    if not isinstance(chk, dict):
        raise ValueError('写入失败：成稿顶层不是 YAML 映射')
    if val is not None:
        if chk.get(ns) != val:
            raise ValueError('写入失败：命名空间未正确落盘，已取消')
    elif ns in chk:
        raise ValueError('写入失败：命名空间未删除，已取消')

    os.makedirs(home, exist_ok=True)
    tmp = sfile + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(new_text)
    os.replace(tmp, sfile)


def remember_settings(name, text, ns=None):
    """在 registry 里记录某插件最近保存的 settings.yaml 偏好（面板回显用）。
    text 为空则清除记录；ns 为空时按默认命名空间补全。"""
    with _inject_lock:
        reg = _load_registry()
        record = reg['plugins'].get(name)
        if not record:
            raise ValueError('未安装插件: %s' % name)
        text = (text or '').strip()
        if text and not ns:
            ns = default_namespace(name)
        record['settings'] = {'ns': ns, 'text': text} if text and ns else None
        _save_registry(reg)


def _cleanup_settings_namespace(record):
    """卸载零残留：该插件在 settings.yaml 的命名空间一并清掉（尽力而为，不中断卸载）。"""
    st = record.get('settings') or {}
    if st.get('ns') and st.get('text'):
        try:
            set_settings_namespace(st['ns'], '')
        except Exception:
            pass


def remove_package(name):
    with _inject_lock:
        reg = _load_registry()
        record = reg['plugins'].get(name)
        if not record:
            raise ValueError('未安装插件: %s' % name)
        if record.get('track') == 'A':
            # A 轨：pnpm remove + bundles 摘名 + settings 命名空间清理（运行中会抛错提示先退出）
            remove_bundle_npm(name)
            _cleanup_settings_namespace(record)
            return record
        reg['plugins'].pop(name, None)
        _save_registry(reg)
        apply_rows()
        shutil.rmtree(record.get('dir', ''), ignore_errors=True)
        # 零残留：删整 <slug>/ 目录（版本子目录删除后可能残留空父目录）
        shutil.rmtree(os.path.join(STORE, name.replace('/', '__')), ignore_errors=True)
        _cleanup_settings_namespace(record)
        # B 轨零残留收尾：共享依赖目录按剩余插件收敛（pnpm/npm install 会清掉多余包）
        try:
            sync_deps(required=False)
        except Exception:
            pass
        return record


# ---------------- 桌面端启动注入线程 ----------------

_injector_started = False


def start_injector():
    """launch 桌面端时调用：registry 驱动的反复补写线程（项目每次启动被重建）。"""
    global _injector_started
    if _injector_started:
        return
    _injector_started = True

    def loop():
        while True:
            try:
                project = _desktop_project_dir()
                desktop_patch = _desktop_patch_path()
                if project and desktop_patch and os.path.isfile(os.path.join(project, 'package.json')):
                    reg = _load_registry()
                    records = [r for r in reg['plugins'].values()
                               if r.get('targets', {}).get('desktop') and r.get('track', 'B') != 'A']
                    path = desktop_patch
                    if not records:
                        continue  # 无桌面插件时不触碰 patch 文件，避免与 dev.ts 目录重建竞争
                    if not dsh_env.is_running():
                        continue  # dev.ts 启动重建期不碰目录；DSH 运行后才补写
                    if not os.path.isfile(path):
                        # 文件缺失（dev.ts 重建目录后被清）才补写；存在时绝不打开读，
                        # 消除与 dev.ts 删除非空目录的竞争（ENOTEMPTY/EPERM）
                        with _inject_lock:
                            _write_patch_file(path, records, 'desktop')
            except Exception:
                pass
            time.sleep(0.2)

    threading.Thread(target=loop, daemon=True, name='dshskin-plugin-inject').start()
