# -*- coding: utf-8 -*-
"""P0-5 漂移检测纯逻辑单测：基线建立 / 区域落空 / 版本变化 / 恢复"""
import sys, tempfile, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server as S
import cdp_skin as C

tmp = tempfile.mkdtemp()
S.DRIFT_BASELINE = os.path.join(tmp, 'dom-baseline.json')
S._drift_cache['v'] = None

REGIONS = ['background','frame','sidebar','chat','scroll','composerbar',
           'composer','rightbar','overlay','sessionhead']

def make_probe(dead=(), version='1.2.0'):
    def _probe(regions=None, port=None):
        regs = {}
        for k in REGIONS:
            matched = k not in dead
            regs[k] = {'matched': matched, 'chosen': 'x' if matched else None,
                       'hits': [1] if matched else [0], 'candidates': ['x']}
        return {'ok': True, 'regions': regs,
                'dead': list(dead), 'alive': [k for k in REGIONS if k not in dead],
                'landmarks': {}, 'counts': {}, 'suggested': {}}
    return _probe

C.cdp_ready = lambda *a, **k: True
S.dsh_env.harness_version = lambda: '1.2.0'

# 1) 无基线
C.probe_regions = make_probe()
r = S.evaluate_drift(force=True)
assert r['available'] and r['has_baseline'] is False and r['drifted'] is False, r
print('1) 无基线时不误报: PASS')

# 2) 建立全绿基线
cur = S.current_dom_fingerprint()
S.save_drift_baseline(cur)
S._drift_cache['v'] = None
r = S.evaluate_drift(force=True)
assert r['has_baseline'] is True and r['drifted'] is False, r
print('2) 全绿基线建立后无漂移: PASS')

# 3) 两个区域落空 → 漂移
C.probe_regions = make_probe(dead=('overlay', 'rightbar'))
S._drift_cache['v'] = None
r = S.evaluate_drift(force=True)
assert r['drifted'] is True and set(r['lost']) == {'overlay', 'rightbar'}, r
print('3) 区域落空被检出:', r['lost'], 'PASS')

# 4) 区域恢复 → 不再漂移
C.probe_regions = make_probe()
S._drift_cache['v'] = None
r = S.evaluate_drift(force=True)
assert r['drifted'] is False and r['lost'] == [], r
print('4) 结构恢复后漂移解除: PASS')

# 5) DSH 版本变化 → 漂移（即使区域都命中）
S.dsh_env.harness_version = lambda: '1.3.0'
C.probe_regions = make_probe()
S._drift_cache['v'] = None
r = S.evaluate_drift(force=True)
assert r['version_changed'] is True and r['drifted'] is True, r
print('5) DSH 版本变化被检出: %s→%s PASS' % (r['baseline_version'], r['current_version']))

print('=== 漂移检测逻辑全部 PASS ===')
