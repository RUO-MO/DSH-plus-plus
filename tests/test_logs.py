# -*- coding: utf-8 -*-
"""P2-3 日志治理：脱敏/截断/并发写/轮转 单测"""
import sys, os, tempfile, threading, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server as SV

tmp = tempfile.mkdtemp()
SV.LOG_FILE = os.path.join(tmp, 'logs.json')

# 1) 凭据脱敏
cases = [
    ('使用 sk-abcdefgh12345678 调用', 'sk-***'),
    ('Authorization: Bearer abcdefghijklmnop1234', 'Bearer ***'),
    ('api_key=AKIAIOSFODHH7EXAMPLE', '***'),
    ('DEEPSEEK_API_KEY=sk-zzzzzzzz12345678 ok', '***'),
    ('普通日志不含密钥', '普通日志不含密钥'),
]
for raw, frag in cases:
    out = SV.redact_secrets(raw)
    assert frag in out, (raw, out)
    assert 'abcdefgh12345678' not in out and 'AKIAIOSFODHH7EXAMPLE' not in out
print('1) 密钥脱敏: PASS')

# 2) add_log 落盘的内容也已脱敏 + 带结构化时间戳
SV.add_log('token is sk-secret1234567890 now', 'info')
logs = SV.load_logs()
assert logs and 'sk-***' in logs[-1]['msg'] and 'ts' in logs[-1]
print('2) 落盘脱敏+结构化字段: PASS')

# 3) 超长截断
SV.add_log('x' * 5000)
assert len(SV.load_logs()[-1]['msg']) <= SV.MAX_LOG_MSG + 10
print('3) 单条超长截断: PASS')

# 4) 并发写不丢（锁）
SV.LOG_FILE = os.path.join(tmp, 'c.json')
def worker(n):
    for i in range(40):
        SV.add_log('w%d-%d' % (n, i))
ts = [threading.Thread(target=worker, args=(n,)) for n in range(10)]
[t.start() for t in ts]; [t.join() for t in ts]
got = SV.load_logs()
# 去重计数：应写入 400 条不同日志（受 MAX_LOG_LINES=300 截断，至少不损坏 JSON 且条数==上限）
assert isinstance(got, list) and len(got) == SV.MAX_LOG_LINES, len(got)
print('4) 400 条并发写无损坏（截断到 %d）: PASS' % SV.MAX_LOG_LINES)

# 5) 大小轮转：把阈值临时调小，写一条后应产生 .old.json
SV.LOG_FILE = os.path.join(tmp, 'r.json')
SV.add_log('seed ' * 100)
SV.MAX_LOG_BYTES = 10  # 极小阈值
SV.add_log('rotate ' * 100)
assert os.path.exists(SV.LOG_FILE + '.old.json'), '应轮转归档'
print('5) 超大小轮转归档: PASS')

# 6) error 连续重复折叠
SV.LOG_FILE = os.path.join(tmp, 'e.json')
for _ in range(10):
    SV.add_log('同样的错误', 'error')
errs = [x for x in SV.load_logs() if x['msg'] == '同样的错误']
assert len(errs) == 1 and errs[0].get('repeat', 0) >= 1
print('6) 连续相同错误折叠: PASS')

print('=== 日志治理全部 PASS ===')
