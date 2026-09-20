# -*- coding: utf-8 -*-
"""P1-6 快捷键注册表/归一化/冲突检测单测"""
import sys, os, tempfile, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import enhance_engine as E

# 1) 归一化
assert E.normalize_combo('ctrl+alt+u') == 'Ctrl+Alt+U'
assert E.normalize_combo('control+shift+enter') == 'Ctrl+Shift+Enter'
assert E.normalize_combo('') == ''
assert E.normalize_combo('ctrl') == ''  # 只有修饰键没有主键 → 非法
print('1) 快捷键归一化: PASS')

# 2) 默认解析
hk = E.resolve_hotkeys({})
assert hk['ui-tweaks.toggle'] == 'Ctrl+Alt+U'
assert hk['input.send'] == 'Ctrl+Alt+Enter'
print('2) 默认快捷键解析: PASS')

# 3) 用户覆盖
hk = E.resolve_hotkeys({'ui-tweaks.toggle': 'ctrl+alt+x'})
assert hk['ui-tweaks.toggle'] == 'Ctrl+Alt+X'
assert hk['session.export'] == 'Ctrl+Alt+E'  # 其他保持默认
print('3) 用户改键覆盖: PASS')

# 4) 冲突检测：把两个动作改成同一组合
conf = E.detect_hotkey_conflicts({'session.export': 'ctrl+alt+u'})  # 与 ui-tweaks.toggle 默认撞
assert len(conf) == 1 and set(conf[0]['actions']) == {'session.export', 'ui-tweaks.toggle'}, conf
assert E.detect_hotkey_conflicts({}) == []
print('4) 冲突检测: PASS')

# 5) state 持久化保留 hotkeys 与 scripts_meta（不被白名单丢弃）
tmp = tempfile.mkdtemp()
E.STATE_FILE = os.path.join(tmp, 'enhance.json')
E.ENHANCE_DIR = os.path.join(tmp, 'enhance')
st = E.load_state()
st['hotkeys'] = {'session.export': 'Ctrl+Alt+X'}
st['scripts_meta'] = {'demo.js': {'source': 'market:demo.js', 'risk': 'low'}}
st['scripts'] = {'demo.js': True}
E.save_state(st)
back = E.load_state()
assert back['hotkeys'] == {'session.export': 'Ctrl+Alt+X'}, back.get('hotkeys')
assert back['scripts_meta']['demo.js']['source'] == 'market:demo.js'
# 非法 action 的改键应被过滤
st2 = E.load_state()
st2['hotkeys'] = {'not.exist': 'Ctrl+Alt+Z', 'session.copy': 'Ctrl+Alt+Y'}
saved = E.save_state(st2)
assert 'not.exist' not in saved['hotkeys'] and saved['hotkeys'].get('session.copy') == 'Ctrl+Alt+Y'
print('5) 状态持久化/过滤: PASS')

# 6) 运行时注入热键表且无占位符残留
st3 = E.load_state(); st3['enabled'] = True; E.save_state(st3)
js, meta = E.build_runtime_js(force=True)
assert '__HOTKEYS__' not in js
assert 'R.isHotkey' in js
assert 'Ctrl+Alt+U' in js
print('6) 运行时热键注入: PASS')

print('=== 快捷键治理全部 PASS ===')
