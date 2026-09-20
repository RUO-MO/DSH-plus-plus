# -*- coding: utf-8 -*-
"""纯逻辑单测：mock CDP 会话，验证多 target 编排/聚合/回收/守护补注/自举注册。"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cdp_skin as C

class FakeCDP:
    def __init__(self, tid):
        self.tid=tid; self.closed=False; self.style=False; self.rt=False
        self.cache=None; self.script_ids=[]
    def cmd(self, m, p=None):
        if m=='Page.addScriptToEvaluateOnNewDocument':
            i='boot-%s'%self.tid; self.script_ids.append(i); return {'result':{'identifier':i}}
        return {}
    def eval(self, expr, await_promise=False):
        e=expr.replace(' ','')
        if 'localStorage.setItem' in e: self.cache='set'; return {'ok':True,'value':None}
        if 'localStorage.removeItem' in e:
            self.cache=None
            if 's.remove()' in e: self.style=False; self.rt=False
            return {'ok':True,'value':0}
        if e.startswith('JSON.stringify({style'):
            return {'ok':True,'value':json.dumps({'style':self.style,'rt':self.rt,'body':self.style})}
        if "createElement('style')" in e:
            self.style=True; return {'ok':True,'value':123}
        if 's.remove()' in e:
            self.style=False; self.rt=False; return {'ok':True,'value':123}
        if e.startswith('(function(){vars=document.getElementById'):  # cur_attr 探测
            return {'ok':True,'value':'h12'}
        if 'slots' in e:
            return {'ok':True,'value':json.dumps({'slots':5,'phase':2})}
        if 'removeAttribute' in e: return {'ok':True,'value':None}
        self.rt=True; return {'ok':True,'value':None}  # js 运行时
    def prepare_for_new_document(self):
        self.cmd('Page.enable'); self.cmd('Runtime.enable')
        return self.cmd('Page.addScriptToEvaluateOnNewDocument',{'source':'x'})['result']['identifier']
    def alive(self): return not self.closed
    def close(self): self.closed=True

def mk(tid): return {'id':tid,'type':'page','webSocketDebuggerUrl':'ws/%s'%tid,'url':'dsh-app://m'}
opened=[]
def _fake_open(self, t):
    c=FakeCDP(t['id']); c.prepare_for_new_document(); opened.append(t['id']); return c
C.TargetSessions._open=_fake_open
C.port_open=lambda *a,**k:True

# 1) 双 target 注入
CURRENT=[mk('A'),mk('B')]
C.find_page_targets=lambda port=None:list(CURRENT)
r=C.inject_bundle('css-body','js-body',9222)
assert r['ok'] and r['injected_count']==2 and r['target_count']==2, r
sess=C._SESSIONS.all()
assert set(sess)=={'A','B'}
for tid,c in sess.items():
    assert c.style and c.cache=='set' and len(c.script_ids)==1 and c.rt, (tid,'注入/缓存/自举/运行时缺失')
print('1) 双 target 注入 + 缓存 + 自举注册 + 运行时: PASS')

# 2) A 关 C 开
CURRENT=[mk('B'),mk('C')]
sess2=C._SESSIONS.sync(C.find_page_targets())
assert set(sess2)=={'B','C'}
assert C._SESSIONS.all()['B'] is sess['B'], '存活 target 复用长连'
assert sess['A'].closed, '消失 target 回收连接'
assert 'C' in opened, '新窗口建连'
print('2) target 变更：回收/复用/接入: PASS')

# 3) 守护补注
sess2['B'].style=False; sess2['B'].rt=False
w=C.SkinWatcher(lambda:('css-body','js-body'))
w._tick()
assert w.last_report['ok'] and w.last_report['target_count']==2, w.last_report
assert sess2['B'].style and sess2['B'].rt, '丢失窗口补注'
print('3) 守护线程多窗口补注: PASS report=', w.last_report)

# 4) 全量移除
rr=C.remove_css(9222)
assert rr['removed_count']==2, rr
for c in C._SESSIONS.all().values():
    assert not c.style and c.cache is None
print('4) 全 target 移除 + 清缓存: PASS')

C._SESSIONS.close_all()
print('=== 多 target / 预注入编排逻辑全部 PASS ===')
