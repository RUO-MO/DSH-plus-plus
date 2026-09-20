# -*- coding: utf-8 -*-
"""P2-10 更新检查器单测（mock 网络，不外联）"""
import sys, os, io, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import updater as U

# 1) 版本号比较（含 v 前缀）
assert U.version_tuple('v1.2.0') > U.version_tuple('1.1.9')
assert U.version_tuple('1.10') > U.version_tuple('1.9.9')
assert U.version_tuple('1.0.0') == U.version_tuple('v1.0')
print('1) 版本比较: PASS')

# 2) 未配置更新源：不抛异常、configured=False
r = U.check_for_update('1.0.0', repo='')
assert r['ok'] is False and r['configured'] is False
print('2) 未配置更新源安全返回: PASS')

# 3) mock GitHub release：有新版本
class FakeResp:
    def __init__(self, obj): self.b = json.dumps(obj).encode()
    def read(self): return self.b
    def __enter__(self): return self
    def __exit__(self, *a): return False

def fake_urlopen(req, timeout=8):
    return FakeResp({'tag_name': 'v1.3.0', 'html_url': 'https://x/r',
                     'body': 'fix', 'prerelease': False,
                     'assets': [{'name': 'DSHSkin.exe', 'browser_download_url': 'http://x/e', 'size': 123}]})
U.urllib.request.urlopen = fake_urlopen
r = U.check_for_update('1.0.0', repo='owner/repo')
assert r['ok'] and r['has_update'] and r['latest'] == 'v1.3.0' and r['assets']
print('3) 检测到新版本并返回资产: PASS')

# 4) 当前已是最新
r = U.check_for_update('1.3.0', repo='owner/repo')
assert r['ok'] and not r['has_update']
print('4) 已是最新: PASS')

# 5) 网络异常被吞，返回 ok=False
def boom(req, timeout=8): raise OSError('network down')
U.urllib.request.urlopen = boom
r = U.check_for_update('1.0.0', repo='owner/repo')
assert r['ok'] is False and 'network down' in r['reason']
print('5) 网络异常安全降级: PASS')

print('=== 更新检查器单测全部 PASS ===')
