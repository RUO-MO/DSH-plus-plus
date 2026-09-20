# -*- coding: utf-8 -*-
"""P0-6 托管块首写备份 + 还原入口 单测（临时目录，不碰真实 harness）"""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import plugin_manager as P

tmp = tempfile.mkdtemp()
web = os.path.join(tmp, 'profiles', 'web', 'cordis.patch.yml')
desk = os.path.join(tmp, 'project', 'cordis.patch.yml')
os.makedirs(os.path.dirname(web), exist_ok=True)
os.makedirs(os.path.dirname(desk), exist_ok=True)
P._web_patch_path = lambda: web
P._desktop_project_dir = lambda: os.path.join(tmp, 'project')
P._desktop_patch_path = lambda: desk

# 用户原始内容（介入前就有的自定义行）
ORIGINAL = "- id: user-row\n  name: 'user-plugin'\n"
with open(web, 'w', encoding='utf-8') as f:
    f.write(ORIGINAL)

rec = [{'name': 'demo', 'dir': tmp, 'entryRel': 'lib/index.js',
        'enabled': {'web': True, 'desktop': True}, 'targets': {'web': True, 'desktop': True}}]
os.makedirs(os.path.join(tmp, 'lib'), exist_ok=True)
with open(os.path.join(tmp, 'lib', 'index.js'), 'w', encoding='utf-8') as f:
    f.write('module.exports={}')

# 1) 首次写入：应生成 .orig，且内容等于原始
P._write_patch_file(web, rec, 'web')
assert os.path.isfile(web + '.orig'), '首写应生成 .orig'
assert open(web + '.orig', encoding='utf-8').read() == ORIGINAL, '.orig 必须等于介入前原貌'
assert P.BLOCK_BEGIN in open(web, encoding='utf-8').read(), '当前文件应含托管块'
print('1) 首写生成 .orig 且保存原貌: PASS')

# 2) 再次写入（模拟增删插件）：.orig 不被覆盖
rec2 = [dict(rec[0], name='demo2')]
P._write_patch_file(web, rec2, 'web')
assert open(web + '.orig', encoding='utf-8').read() == ORIGINAL, '.orig 必须一次性、永不覆盖'
print('2) .orig 一次性不被后续写入覆盖: PASS')

# 3) restore：精确还原到介入前，托管块消失，备份清理
report = P.restore_managed(disable_registry=False)
assert P.BLOCK_BEGIN not in open(web, encoding='utf-8').read(), '还原后不应含托管块'
assert open(web, encoding='utf-8').read() == ORIGINAL, '应精确还原原始用户内容'
assert not os.path.exists(web + '.orig') and not os.path.exists(web + '.bak'), '备份应清理'
print('3) restore 精确还原 + 清理备份: PASS  report=', report.get(web))

# 4) 介入前文件不存在：首写后 .orig 为空，restore 删除文件
assert not os.path.exists(desk)
P._write_patch_file(desk, rec, 'desktop')
assert os.path.isfile(desk) and os.path.isfile(desk + '.orig')
P.restore_managed(disable_registry=False)
assert not os.path.exists(desk), '介入前不存在的文件还原后应删除'
print('4) 原本不存在 → 首写 → 还原后删除: PASS')

# 5) 幂等：无 .orig 且无托管块时 restore 报 already-clean，不破坏用户内容
with open(web, 'w', encoding='utf-8') as f:
    f.write(ORIGINAL)
rep = P.restore_managed(disable_registry=False)
assert rep[web] == 'already-clean'
assert open(web, encoding='utf-8').read() == ORIGINAL
print('5) 干净文件 restore 幂等不误伤: PASS')

print('=== 托管块备份/还原全部 PASS ===')
