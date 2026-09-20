# -*- coding: utf-8 -*-
"""P1-7 会话 token 统计/全文搜索/对比单测（合成事件流，不依赖真实 DSH 数据）"""
import sys, os, tempfile, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import session_store as S

tmp = tempfile.mkdtemp()
now = int(time.time() * 1000)

def write_session(proj, sid, events):
    d = os.path.join(tmp, 'sessions', proj, sid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'session.v3.jsonl'), 'w', encoding='utf-8') as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')

def ev(t, data, dt=0):
    return {'type': t, 'time': now + dt, 'data': data}

# 会话 A：含真实 usage、工具调用、插件注入
A = [
    ev('session', {'createdAt': now, 'cwd': 'D:/proj', 'agentPreset': 'code'}),
    ev('session/title', {'title': '解析整个项目'}),
    ev('turn/start', {'turn': 1}),
    ev('user/message', {'source': {'kind': 'user'}, 'content': [{'type': 'text', 'text': '帮我解析整个项目结构'}]}),
    ev('system/message', {'message': {'content': [{'type': 'text', 'text': 'sys'}]}}),
    ev('user/message', {'source': {'kind': 'plugin', 'plugin': 'x'}, 'content': [{'type': 'text', 'text': '插件注入内容不应被搜索到'}]}),
    ev('tool/call', {'name': 'ls'}),
    ev('assistant/message', {'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': '项目包含 src 目录和配置文件'}]},
                             'usage': {'inputTokens': 1000, 'outputTokens': 500, 'cacheReadTokens': 200, 'totalTokens': 1700}}),
    ev('turn/end', {'turn': 1}),
]
write_session('proj-a', 'session-aaa', A)

# 会话 B
B = [
    ev('session', {'createdAt': now}),
    ev('session/title', {'title': '另一个任务'}),
    ev('user/message', {'source': {'kind': 'user'}, 'content': [{'type': 'text', 'text': '重构配置模块'}]}),
    ev('assistant/message', {'message': {'content': [{'type': 'text', 'text': '已重构配置加载逻辑'}]},
                             'usage': {'inputTokens': 300, 'outputTokens': 200, 'totalTokens': 500}}),
    ev('turn/end', {'turn': 1}),
]
write_session('proj-b', 'session-bbb', B)

cfg = {'dsh_home': tmp}

# 1) 真实 usage 统计
meta, events = S._parse_session(os.path.join(tmp, 'sessions', 'proj-a', 'session-aaa'))
u = meta['usage']
assert u['input_tokens'] == 1000 and u['output_tokens'] == 500 and u['cache_read_tokens'] == 200
assert u['total_tokens'] == 1700 and u['assistant_messages'] == 1
assert meta['turns'] == 1 and meta['tool_calls'] == 1 and meta['user_msgs'] == 1
print('1) 真实 token/轮次/工具统计: PASS', u)

# 2) 对话序列过滤插件/系统
turns = S.conversation_turns(events)
assert len(turns) == 2, turns
assert turns[0]['role'] == 'user' and turns[1]['role'] == 'assistant'
print('2) 对话序列（过滤插件/系统）: PASS')

# 3) 全文搜索：命中正文，不命中插件注入
r = S.search_sessions('项目', cfg)
assert r['total'] >= 1
hit_ids = [x['id'] for x in r['results']]
assert 'session-aaa' in hit_ids
assert r['results'][0]['matches'][0]['snippet']
r2 = S.search_sessions('插件注入内容不应被搜索到', cfg)
assert r2['total'] == 0, '插件注入不应被搜到'
print('3) 跨会话全文搜索 + 插件隔离: PASS')

# 4) totalTokens 缺失时用 input+output+cache 兜底
C = [ev('assistant/message', {'message': {'content': [{'type': 'text', 'text': 'x'}]},
                              'usage': {'inputTokens': 10, 'outputTokens': 5, 'cacheReadTokens': 2}})]
d = os.path.join(tmp, 'sessions', 'p', 'session-ccc'); os.makedirs(d, exist_ok=True)
with open(os.path.join(d, 'session.v3.jsonl'), 'w', encoding='utf-8') as f:
    for e in C: f.write(json.dumps(e, ensure_ascii=False) + '\n')
m, _ = S._parse_session(d)
assert m['usage']['total_tokens'] == 17, m['usage']
print('4) totalTokens 兜底求和: PASS')

# 5) 会话对比 + 合计
cmp = S.compare_sessions(['session-aaa', 'session-bbb', 'session-missing'], cfg)
assert len(cmp['rows']) == 3
assert cmp['rows'][2].get('missing')
assert cmp['summary']['total_tokens'] == 2200, cmp['summary']
assert cmp['summary']['tool_calls'] == 1
print('5) 会话对比/合计/缺失标记: PASS')

print('=== 会话深度功能全部 PASS ===')
