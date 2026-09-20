# -*- coding: utf-8 -*-
"""P1-8 快照保鲜状态判定单测

换肤已移除（2026-09-20）：原「主题模板版本」改为「界面打标器版本」(marker_engine.MARKER_VERSION)。
"""
import sys, os, tempfile, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server as SV
import marker_engine as M

tmp = tempfile.mkdtemp()
SV.ASSETS_DIR = tmp
SV.SNAP_MANIFEST = os.path.join(tmp, 'snap-manifest.json')
SV.dsh_env.harness_version = lambda: '2.0.0'   # 固定当前 DSH 版本

# 造齐快照文件
for fn in SV.SNAP_FILES:
    open(os.path.join(tmp, fn), 'w').write('x')

# 1) 无 manifest：stale（用的是随包内置快照，未在本机重抓）
st = SV.snapshots_status()
assert st['stale'] and any('尚未在本机重抓' in r for r in st['reasons'])
print('1) 无 manifest 判定为待重抓: PASS')

# 2) 版本完全一致：不 stale
json.dump({'capturedAt': 1, 'harness_version': '2.0.0',
           'marker_version': M.MARKER_VERSION, 'files': {}},
          open(SV.SNAP_MANIFEST, 'w'))
st = SV.snapshots_status()
assert not st['stale'], st['reasons']
print('2) 版本一致不 stale: PASS')

# 2b) 旧 manifest（只有 template_version 键）向后兼容：同版本号时不 stale
json.dump({'capturedAt': 1, 'harness_version': '2.0.0',
           'template_version': M.MARKER_VERSION, 'files': {}},
          open(SV.SNAP_MANIFEST, 'w'))
st = SV.snapshots_status()
assert not st['stale'], st['reasons']
print('2b) 旧 template_version 键向后兼容: PASS')

# 3) DSH 升级 → stale
json.dump({'capturedAt': 1, 'harness_version': '1.5.0',
           'marker_version': M.MARKER_VERSION}, open(SV.SNAP_MANIFEST, 'w'))
st = SV.snapshots_status()
assert st['stale'] and any('DSH 版本变化' in r for r in st['reasons'])
print('3) DSH 版本变化判 stale: PASS')

# 4) 打标器升级 → stale + 缺文件
json.dump({'capturedAt': 1, 'harness_version': '2.0.0',
           'marker_version': M.MARKER_VERSION - 1}, open(SV.SNAP_MANIFEST, 'w'))
os.remove(os.path.join(tmp, SV.SNAP_FILES[0]))
st = SV.snapshots_status()
assert st['stale'] and st['missing'] and any('打标器' in r for r in st['reasons'])
print('4) 打标器升级+缺文件判 stale: PASS')

# 5) 未就绪时重抓直接报友好错误（不崩）
SV.cdp_skin.cdp_ready = lambda: False
r = SV.recapture_snapshots()
assert r['ok'] is False and '调试模式' in r['err']
print('5) DSH 未运行时重抓友好报错: PASS')

print('=== 快照保鲜单测全部 PASS ===')
