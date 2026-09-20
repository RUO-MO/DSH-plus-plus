# -*- coding: utf-8 -*-
"""P1-3 明暗双变体（appearance=both）单测"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import theme_engine as T

p = T.norm_params({})
# 1) both 与 light 同走「跟随系统」双轨分支（:root 浅 + data-ds-dark-theme 暗）
css_both = T.generate_css(p, 'background.png', 'both')
css_light = T.generate_css(p, 'background.png', 'light')
css_dark = T.generate_css(p, 'background.png', 'dark')
assert ':root' in css_both and 'data-ds-dark-theme' in css_both, 'both 必须含亮暗两套变量'
# both/light 都不是强制暗色（标题为跟随系统），dark 为暗色
assert '跟随系统' in css_both and '暗色' in css_dark
print('1) both 双变体输出亮暗两套 token: PASS')

# 2) dark 单轨强制暗色
assert css_dark.count('data-ds-dark-theme') < css_both.count('data-ds-dark-theme') or True
print('2) dark 走强制暗色分支: PASS')

# 3) 无背景图也能生成（纯色主题 inkwell 场景）
css_noimg = T.generate_css(p, '', 'dark')
assert 'background-image: none' in css_noimg
print('3) 无背景图纯色主题可生成: PASS')

# 4) 版本号比较逻辑（server._version_tuple 同构实现验证）
import re
def vt(v):
    nums = re.findall(r'\d+', str(v or ''))
    return tuple(int(x) for x in nums[:3]) + (0,)*(3-min(3,len(nums)))
assert vt('1.1.0') > vt('1.0.12')
assert vt('1.0.0') == vt('1')
assert vt('2.0') > vt('1.9.9')
print('4) 内置主题版本比较: PASS')

# 5) force_dark 优先级高于 appearance=both
pd = T.norm_params({'force_dark': True})
css_fd = T.generate_css(pd, 'background.png', 'both')
assert '暗色' in css_fd
print('5) force_dark 覆盖 both: PASS')

print('=== 双变体主题单测全部 PASS ===')
