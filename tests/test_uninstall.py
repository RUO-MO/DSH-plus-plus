# -*- coding: utf-8 -*-
"""P2-9 完整卸载流程单测"""
import sys, os, tempfile, importlib.util
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

spec = importlib.util.spec_from_file_location('dsh_skin_main', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'dsh-skin.py'))
ts = importlib.util.module_from_spec(spec); spec.loader.exec_module(ts)
import plugin_manager, cdp_skin

tmp = tempfile.mkdtemp()
skin = os.path.join(tmp, '.dsh-skins')
os.makedirs(os.path.join(skin, 'keus'))
with open(os.path.join(skin, 'keus', 'skin.css'), 'w') as f:
    f.write('x{}')
ts.SKIN_ROOT = skin

# mock CDP 移除
removed = {}
def fake_remove(port=None):
    removed['called'] = True
    return {'targets': ['aaaa1111', 'bbbb2222']}
ts.cdp_skin.remove_css = fake_remove
# mock 托管块还原（其内部逻辑已在 test_plugin_restore 覆盖）
plugin_manager.restore_managed = lambda disable_registry=True: {'/x/cordis.patch.yml': 'restored'}

# 1) 非 purge：移除注入 + 还原，但保留数据目录
r = ts.uninstall(purge=False)
assert removed['called']
assert len(r['injection_removed']) == 2
assert r['patch_restored'].get('/x/cordis.patch.yml') == 'restored'
assert r['purged'] is False and os.path.isdir(skin), '非 purge 必须保留数据'
assert any('程序文件' in n for n in r['notes'])
print('1) 非 purge 卸载（移除注入+还原托管块、保留数据）: PASS')

# 2) purge：数据目录被删
r2 = ts.uninstall(purge=True)
assert r2['purged'] is True and not os.path.exists(skin), 'purge 应删除数据目录'
print('2) purge 彻底删除数据目录: PASS')

# 3) DSH 未运行时 remove 抛错也不影响后续还原/不崩
ts.cdp_skin.remove_css = lambda port=None: (_ for _ in ()).throw(RuntimeError('no cdp'))
plugin_manager.restore_managed = lambda disable_registry=True: {}
r3 = ts.uninstall(purge=False)
assert any('移除注入时出错' in n for n in r3['notes'])
print('3) CDP 不可用时优雅降级: PASS')

print('=== 完整卸载流程全部 PASS ===')
