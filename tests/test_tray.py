# -*- coding: utf-8 -*-
"""P1-9 常驻形态：开机自启注册表读写（可逆）+ 缺托盘库降级"""
import sys, os, importlib.util
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tray

# 1) 自启命令行：冻结/源码两种形态都应非空且含 --no-open
cmd = tray.autostart_command()
assert '--no-open' in cmd and cmd.strip()
print('1) 自启命令行生成: PASS ->', cmd[:70])

# 2) 注册表写入→读取→删除（HKCU，可逆，测完恢复原状）
before = tray.is_autostart()
ok, st = tray.set_autostart(True)
if not ok:
    print('SKIP: 注册表不可写（受限环境）: {0}'.format(st))
    sys.exit(0)
assert ok and tray.is_autostart(), '写入后应能读到'
print('2) 写入开机自启并读回: PASS')
ok, st = tray.set_autostart(False)
assert ok and not tray.is_autostart(), '删除后应读不到'
print('3) 移除开机自启: PASS')
# 恢复测试前状态
tray.set_autostart(before)

# 4) 缺 pystray 时 run_tray 返回 False（降级，不抛异常）
if importlib.util.find_spec('pystray') is None:
    assert tray.run_tray(8765, lambda: '', lambda: None) is False
    print('4) 缺 pystray 优雅降级: PASS')
else:
    print('4) pystray 已安装，跳过降级分支')

# 5) start_tray_thread 非阻塞返回（worker 内部自行兜底）
t = tray.start_tray_thread(8765, lambda: '', lambda: None)
assert t is True
print('5) 托盘线程非阻塞启动: PASS')

print('=== 常驻形态单测全部 PASS ===')
