# -*- coding: utf-8 -*-
"""P2-4 解压炸弹/路径穿越防护单测"""
import sys, os, tempfile, zipfile, io
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import plugin_manager as P

tmp = tempfile.mkdtemp()

# 1) 正常插件包通过
good = os.path.join(tmp, 'good.zip')
with zipfile.ZipFile(good, 'w') as z:
    z.writestr('package.json', '{"name":"ok","dsh":{"client":"lib/index.js"}}')
    z.writestr('lib/index.js', 'module.exports={}')
dest = os.path.join(tmp, 'good_out')
root = P.extract_package(good, dest)
assert os.path.isfile(os.path.join(root, 'package.json'))
print('1) 正常插件包解压通过: PASS')

# 2) 高压缩比炸弹被拒（5MB 全零，压缩后极小，比率远超 100）
bomb = os.path.join(tmp, 'bomb.zip')
with zipfile.ZipFile(bomb, 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('package.json', '{"name":"b"}')
    z.writestr('big.bin', b'0' * (5 * 1024 * 1024))
try:
    P.extract_package(bomb, os.path.join(tmp, 'bomb_out'))
    raise AssertionError('炸弹包应被拒绝')
except ValueError as e:
    assert '压缩比' in str(e) or '炸弹' in str(e), str(e)
    print('2) 高压缩比炸弹被拒: PASS（%s）' % e)

# 3) 路径穿越被拒
evil = os.path.join(tmp, 'evil.zip')
with zipfile.ZipFile(evil, 'w') as z:
    z.writestr('package.json', '{"name":"e"}')
    z.writestr('../../escape.txt', 'x')
try:
    P.extract_package(evil, os.path.join(tmp, 'evil_out'))
    raise AssertionError('穿越路径应被拒绝')
except ValueError as e:
    assert '非法路径' in str(e)
    print('3) 路径穿越被拒: PASS')

# 4) 成员数过多被拒
many = os.path.join(tmp, 'many.zip')
with zipfile.ZipFile(many, 'w') as z:
    for i in range(P.MAX_MEMBERS + 50):
        z.writestr('f%d.txt' % i, 'x')
try:
    P.extract_package(many, os.path.join(tmp, 'many_out'))
    raise AssertionError('超多成员应被拒')
except ValueError as e:
    assert '成员数' in str(e)
    print('4) 成员数超限被拒: PASS')

# 确认没有文件逃逸到 tmp 之外
assert not os.path.exists(os.path.join(os.path.dirname(tmp), 'escape.txt'))
print('=== 解压防护全部 PASS ===')
