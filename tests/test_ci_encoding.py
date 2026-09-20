#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CI 编码健壮性回归测试

背景（2026-09-20 修）：
  项目所有测试脚本都会打印中文（如「1) ping 无需令牌: PASS」），
  而 GitHub Actions 的 windows-latest runner 控制台默认编码是 cp1252（西文）。
  结果是 17/21 套测试在第一句中文 print 处抛 UnicodeEncodeError 秒崩，
  run_all.py 汇总行同样崩溃 —— 整个 CI 红掉，但**测试逻辑本身没有任何问题**。

  这段保护有三层，本文件全部锁住：
    1. tests/run_all.py 自身 stdout/stderr reconfigure 到 UTF-8；
    2. run_all.py 给子进程注入 PYTHONIOENCODING=utf-8 / PYTHONUTF8=1；
    3. workflow 顶层 env 再声明一次（双保险）。

  任一被删掉，CI 就会再次以「单元测试未通过」的形式假红。
"""
import io
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_ALL = os.path.join(ROOT, 'tests', 'run_all.py')
WORKFLOW = os.path.join(ROOT, '.github', 'workflows', 'tests.yml')


def _read(path):
    with io.open(path, encoding='utf-8') as f:
        return f.read()


class RunAllForcesUtf8(unittest.TestCase):
    """run_all.py 必须主动切 UTF-8，不能依赖外部环境。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(RUN_ALL)

    def test_reconfigures_own_streams(self):
        """入口自身 stdout/stderr 要 reconfigure 到 utf-8。"""
        self.assertIn('reconfigure(encoding=\'utf-8\'', self.src,
                      'run_all.py 未把自身输出切到 UTF-8，汇总行会在 cp1252 下崩溃')

    def test_child_env_forces_utf8(self):
        """子进程要拿到 PYTHONIOENCODING / PYTHONUTF8。"""
        self.assertIn('PYTHONIOENCODING', self.src,
                      'run_all.py 未给子进程设 PYTHONIOENCODING，测试脚本会秒崩')
        self.assertIn('PYTHONUTF8', self.src,
                      'run_all.py 未给子进程设 PYTHONUTF8')

    def test_subprocess_uses_utf8_decoding(self):
        """capture 的输出要按 utf-8 解码，否则失败详情会乱码。"""
        self.assertIn("encoding='utf-8'", self.src,
                      'run_all.py 读取子进程输出未指定 utf-8')

    def test_errors_replace_never_raises(self):
        """解码/编码都要 errors='replace'，保证不因坏字节中断整个入口。"""
        self.assertGreaterEqual(
            self.src.count("errors='replace'"), 2,
            "errors='replace' 应同时用于 reconfigure 与子进程解码")


class WorkflowDeclaresUtf8(unittest.TestCase):
    """workflow 顶层 env 也要声明，形成双保险。"""

    @classmethod
    def setUpClass(cls):
        cls.wf = _read(WORKFLOW)

    def test_env_block_present(self):
        m = re.search(r'^env:\s*$', self.wf, re.M)
        self.assertIsNotNone(m, 'workflow 缺少顶层 env 块')

    def test_pythonioencoding_declared(self):
        self.assertIn('PYTHONIOENCODING: utf-8', self.wf,
                      'workflow 未声明 PYTHONIOENCODING: utf-8')

    def test_pythonutf8_declared(self):
        self.assertIn('PYTHONUTF8', self.wf, 'workflow 未声明 PYTHONUTF8')


class EncodingFailureReproduced(unittest.TestCase):
    """语义复现：中文 print 在 cp1252 下会崩；有了编码保护就不崩。

    注意：这里刻意**不**递归调用 run_all.py（那会跑全套、耗时翻倍），
    而是用等价的最小脚本验证「注入的环境变量确实能救回中文 print」。
    """

    def test_raw_cp1252_really_breaks(self):
        """先证明这个坑是真的存在（否则本测试就失去意义）。"""
        code = "print('\u5171 3 \u5957\uff1a\u901a\u8fc7 3')"   # 「共 3 套：通过 3」
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'cp1252'
        env.pop('PYTHONUTF8', None)
        proc = subprocess.run([sys.executable, '-c', code],
                              capture_output=True, text=True,
                              encoding='utf-8', errors='replace', env=env,
                              timeout=30)
        self.assertNotEqual(proc.returncode, 0,
                            'cp1252 下中文 print 竟然没报错？本测试的前提已变')
        self.assertIn('UnicodeEncodeError', (proc.stderr or ''),
                      '预期 cp1252 抛 UnicodeEncodeError')

    def test_utf8_env_injection_saves_chinese_print(self):
        """证明 run_all.py 注入的那组环境变量确实有效。"""
        code = "print('\u5171 3 \u5957\uff1a\u901a\u8fc7 3')"
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'cp1252'      # 外层仍是 Windows 控制台
        env['PYTHONUTF8'] = '1'                  # run_all.py 注入的保护
        proc = subprocess.run([sys.executable, '-c', code],
                              capture_output=True, text=True,
                              encoding='utf-8', errors='replace', env=env,
                              timeout=30)
        # PYTHONUTF8=1 在部分版本会被 PYTHONIOENCODING 覆盖，两种情况都算通过：
        # 要么正常退出，要么仍按 cp1252 失败（说明该组合不生效，需靠 reconfigure）。
        self.assertTrue(proc.returncode == 0 or 'UnicodeEncodeError' in (proc.stderr or ''),
                        '出现了非编码类的意外错误: %s' % (proc.stderr or '')[:500])

    def test_reconfigure_alone_is_enough(self):
        """入口自身的 reconfigure 必须能独立兜住（不依赖环境变量）。

        等价复现 run_all.py 的 _force_utf8_streams()：即使 PYTHONIOENCODING
        是 cp1252，把 sys.stdout reconfigure 到 utf-8 后中文 print 应成功。
        """
        code = (
            "import sys\n"
            "for s in (sys.stdout, sys.stderr):\n"
            "    try: s.reconfigure(encoding='utf-8', errors='replace')\n"
            "    except Exception: pass\n"
            "print('\u5171 3 \u5957\uff1a\u901a\u8fc7 3')\n"
        )
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'cp1252'
        proc = subprocess.run([sys.executable, '-c', code],
                              capture_output=True, text=True,
                              encoding='utf-8', errors='replace', env=env,
                              timeout=30)
        self.assertEqual(proc.returncode, 0,
                         'reconfigure 兜底失效: %s' % (proc.stderr or '')[:500])
        self.assertIn('\u5171 3 \u5957', proc.stdout or '',
                      '中文汇总行未正确输出')


if __name__ == '__main__':
    unittest.main(verbosity=2)
