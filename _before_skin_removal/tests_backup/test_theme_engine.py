# -*- coding: utf-8 -*-
"""P2-1 theme_engine 参数校验/模板重建/区域解析 单测"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import theme_engine as T

# 1) 默认参数齐全（9 项，含 font_scale/force_dark）
d = T.norm_params({})
assert set(d) == {p['key'] for p in T.PARAM_SCHEMA}
assert d['font_scale'] == 100 and d['force_dark'] is False
assert len(T.PARAM_SCHEMA) == 9, T.PARAM_SCHEMA
print('1) 默认参数完整（含 font_scale/force_dark）: PASS')

# 2) range 钳制到 [min,max]
n = T.norm_params({'glow_strength': 999, 'glass_opacity': 0, 'blur_strength': 25})
assert n['glow_strength'] == 100 and n['glass_opacity'] == 20 and n['blur_strength'] == 25
print('2) range 越界钳制: PASS')

# 3) 非法数值回落默认
n = T.norm_params({'radius': 'abc', 'glow_strength': None})
assert n['radius'] == 14 and n['glow_strength'] == 60
print('3) 非法值回落默认: PASS')

# 4) color 合法/非法
n = T.norm_params({'glow_color': 'aabbcc', 'overlay_color': 'xyz'})
assert n['glow_color'] == '#aabbcc' and n['overlay_color'] == '#0a0c10'
print('4) 颜色校验: PASS')

# 5) bool 与未知键丢弃
n = T.norm_params({'force_dark': 1, 'unknown_key': 1})
assert n['force_dark'] is True and 'unknown_key' not in n
print('5) bool 转换/未知键丢弃: PASS')

# 6) 当前模板生成的 CSS 不需要重建；空/旧版需要
fresh = T.generate_css(T.DEFAULT_PARAMS, 'bg.png', 'dark')
assert 'dsh-skin-template: v{0}'.format(T.TEMPLATE_VERSION) in fresh
assert T.css_needs_rebuild(fresh) is False
assert T.css_needs_rebuild('') is True
assert T.css_needs_rebuild('/* dsh-skin-template: v1 */ .x{}') is True
print('6) 模板重建判定: PASS')

# 7) 旧 trae 类名识别为 stale
stale = T.theme_staleness('.solo-lite{}.chat-input-v2{}')
assert 'solo-lite' in stale and 'chat-input-v2' in stale
assert T.css_needs_rebuild('.solo-lite{}') is True
print('7) 旧类名 stale 识别: PASS')

# 8) REGIONS 全部 reliable 且 OVERRIDABLE 对齐
assert T.OVERRIDABLE == set(T.REGIONS)
assert all(r.get('reliable') for r in T.REGIONS.values()), '不应再依赖随机哈希类名'
assert len(T.REGIONS) == 10
print('8) 区域契约稳定（10 区全 reliable）: PASS')

# 9) 区域覆盖：override 只接受 OVERRIDABLE 键（值为选择器字符串）
ov = T.resolve_regions({'sidebar': '[data-x]'})
assert ov['sidebar']['selector'] == '[data-x]' and ov['sidebar'].get('overridden')
base = T.resolve_regions({'nonsense': 'x'})
assert 'overridden' not in base['sidebar']
print('9) 区域覆盖白名单: PASS')

# 10) 打标器：force_dark 开/关
js_dark = T.generate_marker_js(force_dark=True)
js_light = T.generate_marker_js(force_dark=False)
assert 'FORCE_DARK' in js_dark and 'data-ds-dark-theme' in js_dark
print('10) 打标器 force_dark 分支: PASS')

print('=== theme_engine 单测全部 PASS ===')
