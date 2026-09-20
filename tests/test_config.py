# -*- coding: utf-8 -*-
"""P0-1/P2-2 配置并发锁与版本迁移单测"""
import sys, os, tempfile, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dsh_env as E

tmp = tempfile.mkdtemp()
E.SKIN_ROOT = tmp
E.CONFIG = os.path.join(tmp, 'config.json')

# 1) 无配置时给默认且带版本
cfg = E.load_config()
assert cfg['config_version'] == E.CONFIG_VERSION and cfg['themes'] == {}
print('1) 默认配置带版本号: PASS')

# 2) 老配置（无版本/缺字段）迁移补齐，保留用户数据
with open(E.CONFIG, 'w', encoding='utf-8') as f:
    f.write('{"active":"keus","themes":{"keus":{"builtin":true}}}')
cfg = E.load_config()
assert cfg['config_version'] == E.CONFIG_VERSION
assert cfg['active'] == 'keus' and cfg['themes']['keus']['builtin']
assert cfg['channel'] == 'cdp'  # 缺的字段补上
print('2) 老配置迁移补字段且保留用户数据: PASS')

# 3) 损坏 JSON 回落默认
with open(E.CONFIG, 'w', encoding='utf-8') as f:
    f.write('{broken')
assert E.load_config()['config_version'] == E.CONFIG_VERSION
print('3) 损坏配置自愈: PASS')

# 4) update_config 并发不丢更新（20 线程 × 50 次自增）
E.save_config(E._default_config())
def bump():
    for _ in range(50):
        E.update_config(lambda c: c.__setitem__('counter', c.get('counter', 0) + 1))
ts = [threading.Thread(target=bump) for _ in range(20)]
[t.start() for t in ts]; [t.join() for t in ts]
final = E.load_config()
assert final['counter'] == 20 * 50, final['counter']
print('4) 1000 次并发自增零丢失（=%d）: PASS' % final['counter'])

# 5) save 必落版本号
E.update_config(lambda c: c.update({'x': 1}))
import json
with open(E.CONFIG, encoding='utf-8') as f:
    on_disk = json.load(f)
assert on_disk['config_version'] == E.CONFIG_VERSION
print('5) 落盘带版本号: PASS')

print('=== 配置锁/迁移单测全部 PASS ===')
