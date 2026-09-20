#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DSHSkin · 测试统一入口
======================
自动发现并以独立子进程运行 tests/test_*.py（隔离模块级 monkeypatch），
汇总通过/失败，全部通过退出码 0，否则 1。

用法：
    python tests/run_all.py            # 运行全部
    python tests/run_all.py -v         # 显示每个用例输出
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _force_utf8_streams():
    """把自身 stdout/stderr 切到 UTF-8。

    Windows 上控制台默认编码可能是 cp1252（如 GitHub Actions 的
    windows-latest runner）或 gbk，此时打印中文汇总行会抛
    UnicodeEncodeError 并让整个入口以非零码退出 —— 看起来像"测试失败"，
    实为编码问题。errors='replace' 保证即使切换失败也不会中断。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError, OSError):
            pass


_force_utf8_streams()


def main():
    verbose = '-v' in sys.argv or '--verbose' in sys.argv
    tests = sorted(f for f in os.listdir(HERE)
                   if f.startswith('test_') and f.endswith('.py'))
    if not tests:
        print('未发现测试')
        return 0
    py = sys.executable
    # 子进程同样强制 UTF-8：测试脚本大量打印中文，若继承 cp1252 会秒崩
    child_env = os.environ.copy()
    child_env['PYTHONIOENCODING'] = 'utf-8'
    child_env['PYTHONUTF8'] = '1'
    passed, failed = [], []
    t0 = time.time()
    for name in tests:
        path = os.path.join(HERE, name)
        t = time.time()
        proc = subprocess.run([py, path], cwd=ROOT,
                              capture_output=not verbose, text=True,
                              encoding='utf-8', errors='replace',
                              env=child_env)
        dt = time.time() - t
        if proc.returncode == 0:
            passed.append(name)
            print('[PASS] {0:<28} {1:5.1f}s'.format(name, dt))
        else:
            failed.append((name, (proc.stdout or '') + (proc.stderr or '')))
            print('[FAIL] {0:<28} {1:5.1f}s'.format(name, dt))
    print('-' * 48)
    print('共 {0} 套：通过 {1}，失败 {2}，用时 {3:.1f}s'.format(
        len(tests), len(passed), len(failed), time.time() - t0))
    if failed:
        print('\n===== 失败详情 =====')
        for name, out in failed:
            print('\n--- {0} ---'.format(name))
            print(out[-1500:])
        return 1
    print('全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
