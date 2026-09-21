# -*- coding: utf-8 -*-
"""数据根审计（dsh_env.skin_root_audit）回归测试。

为什么需要这套测试（2026-09-21 排查结论）
----------------------------------------
本机曾同时存在三份 `.dsh-skins`：生效根、旧 env 残留导致的孤儿副本、
以及默认根（指针载体）。根分裂的症状隐蔽 —— 插件注册表 / 主题 / 日志各存一份，
表现为「某些设置时好时坏」。`skin_root_audit()` 就是把这种情况显性化。

另外查出一个真实测试卫生缺陷：`import server` 会在模块级解析数据根并创建目录、
写 `server.token`（实测），因此有 4 套测试若不在隔离根下运行就会污染真实 home。
`run_all.py` 现已统一注入隔离根，本套测试锁住该行为。

本套测试完全桩掉文件系统与环境，**不依赖本机 Steam / DSH / 注册表**，CI 可跑。
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dsh_env as E


class AuditBase(unittest.TestCase):
    """构造若干假的「数据根」，并桩掉发现函数与注册表读取。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='dsh-root-')
        # 活跃根：有 config.json + 插件注册表
        self.active = os.path.join(self.tmp, 'active')
        # 有数据的孤儿根：有 config.json，且 10 天未写入
        self.stale = os.path.join(self.tmp, 'stale')
        # 裸目录：只有 server.token（测试副产物的形态），不算数据根
        self.bare = os.path.join(self.tmp, 'bare')

        os.makedirs(os.path.join(self.active, 'plugins'), exist_ok=True)
        os.makedirs(self.stale, exist_ok=True)
        os.makedirs(self.bare, exist_ok=True)

        self._write_json(os.path.join(self.active, 'config.json'),
                         {'dsh_home': r'X:\home\.dsh'})
        self._write_json(os.path.join(self.active, 'plugins', 'registry.json'),
                         {'plugins': {'a': {}, 'b': {}}})
        self._write_json(os.path.join(self.stale, 'config.json'), {'themes': {'t': {}}})
        with open(os.path.join(self.bare, 'server.token'), 'w'):
            pass

        # 把孤儿根的时间戳推到 10 天前
        old = time.time() - 10 * 86400
        os.utime(os.path.join(self.stale, 'config.json'), (old, old))
        os.utime(self.stale, (old, old))

        self._orig = {
            'discover': E.discover_skin_roots,
            'root': E.SKIN_ROOT,
            'config': E.CONFIG,
            'pointer': E.POINTER_CONFIG,
            'registry_env': E._registry_env,
            'env_root': os.environ.get('DSH_SKIN_ROOT'),
            'env_home': os.environ.get('DSH_HOME'),
        }
        E.SKIN_ROOT = self.active
        E.CONFIG = os.path.join(self.active, 'config.json')
        # 指针文件也要桩掉：否则会去读本机真实指针，一旦与桩出来的生效根不同
        # 就会产生「指针与生效根不一致」告警，让测试依赖本机环境。
        E.POINTER_CONFIG = os.path.join(self.tmp, 'pointer.json')
        self._write_json(E.POINTER_CONFIG, {'skin_root': self.active})
        E._registry_env = lambda: {}
        E.discover_skin_roots = lambda: [
            {'dir': self.active, 'origins': ['生效根']},
            {'dir': self.stale, 'origins': ['测试构造']},
            {'dir': self.bare, 'origins': ['测试构造']},
        ]
        os.environ.pop('DSH_SKIN_ROOT', None)
        os.environ.pop('DSH_HOME', None)

    def tearDown(self):
        E.discover_skin_roots = self._orig['discover']
        E.SKIN_ROOT = self._orig['root']
        E.CONFIG = self._orig['config']
        E.POINTER_CONFIG = self._orig['pointer']
        E._registry_env = self._orig['registry_env']
        for key, value in (('DSH_SKIN_ROOT', self._orig['env_root']),
                           ('DSH_HOME', self._orig['env_home'])):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _write_json(path, obj):
        import json
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(obj, handle)


class DataRootFlagging(AuditBase):
    def test_active_root_marked(self):
        audit = E.skin_root_audit()
        rows = {r['dir']: r for r in audit['candidates']}
        self.assertTrue(rows[self.active]['active'])
        self.assertFalse(rows[self.stale]['active'])

    def test_bare_dir_is_not_a_data_root(self):
        """只被顺手创建了 server.token 的裸目录不算数据根 —— 否则体检全是噪音。"""
        audit = E.skin_root_audit()
        rows = {r['dir']: r for r in audit['candidates']}
        self.assertFalse(rows[self.bare]['has_data'])
        self.assertTrue(rows[self.stale]['has_data'])
        self.assertTrue(rows[self.active]['has_data'])

    def test_reads_plugin_count_and_missing_dsh_home(self):
        audit = E.skin_root_audit()
        rows = {r['dir']: r for r in audit['candidates']}
        self.assertEqual(rows[self.active]['plugin_count'], 2)
        self.assertEqual(rows[self.stale]['plugin_count'], 0)
        self.assertEqual(rows[self.active]['dsh_home'], r'X:\home\.dsh')
        self.assertIsNone(rows[self.stale]['dsh_home'], '孤儿根应报告缺 dsh_home 指针')


class WarningRules(AuditBase):
    def test_stale_root_warns(self):
        audit = E.skin_root_audit()
        hits = [w for w in audit['warnings'] if self.stale in w]
        self.assertTrue(hits, '10 天未写入且有数据的孤儿根必须告警')

    def test_bare_dir_does_not_warn(self):
        audit = E.skin_root_audit()
        self.assertFalse([w for w in audit['warnings'] if self.bare in w],
                         '裸目录不该产生「其它数据根」告警')

    def test_active_root_never_warns(self):
        audit = E.skin_root_audit()
        self.assertFalse([w for w in audit['warnings'] if self.active in w])

    def test_env_conflict_warns(self):
        os.environ['DSH_SKIN_ROOT'] = self.stale
        audit = E.skin_root_audit()
        self.assertTrue([w for w in audit['warnings'] if 'DSH_SKIN_ROOT' in w],
                        'env 与生效根不一致必须告警 —— 下次启动会切过去')

    def test_pointer_conflict_warns(self):
        """指针指向别处时必须告警（这是根分裂最典型的成因）。"""
        self._write_json(E.POINTER_CONFIG, {'skin_root': self.stale})
        audit = E.skin_root_audit()
        self.assertTrue([w for w in audit['warnings'] if '指针' in w])

    def test_pointer_agrees_no_warning(self):
        self._write_json(E.POINTER_CONFIG, {'skin_root': self.active})
        audit = E.skin_root_audit()
        self.assertFalse([w for w in audit['warnings'] if '指针' in w])

    def test_missing_dsh_home_env_warns(self):
        os.environ['DSH_HOME'] = os.path.join(self.tmp, 'not-created')
        audit = E.skin_root_audit()
        self.assertTrue([w for w in audit['warnings'] if 'DSH_HOME' in w])

    def test_healthy_when_only_active_root_has_data(self):
        E.discover_skin_roots = lambda: [{'dir': self.active, 'origins': ['生效根']}]
        audit = E.skin_root_audit()
        self.assertEqual(audit['warnings'], [], '只有一个数据根时不应有任何告警')


class Discovery(unittest.TestCase):
    """真实 discover_skin_roots 的去重 / 排序行为（仅桩掉 SKIN_ROOT 与注册表）。"""

    def test_dedup_merges_same_path(self):
        orig_root, orig_reg = E.SKIN_ROOT, E._registry_env
        home_default = os.path.join(os.path.expanduser('~'), '.dsh-skins')
        E.SKIN_ROOT = home_default
        E._registry_env = lambda: {}
        try:
            rows = E.discover_skin_roots()
        finally:
            E.SKIN_ROOT, E._registry_env = orig_root, orig_reg
        hit = [r for r in rows
               if os.path.normcase(r['dir']) == os.path.normcase(home_default)]
        self.assertEqual(len(hit), 1, '同一路径只能出现一次')
        self.assertGreaterEqual(len(hit[0]['origins']), 2,
                                '生效根与默认根同路径时应合并来源标注')

    def test_env_root_is_a_candidate(self):
        orig_root, orig_reg = E.SKIN_ROOT, E._registry_env
        orig_env = os.environ.get('DSH_SKIN_ROOT')
        probe = os.path.join(tempfile.gettempdir(), 'dshpp-audit-probe')
        E.SKIN_ROOT = os.path.join(tempfile.gettempdir(), 'dshpp-audit-active')
        E._registry_env = lambda: {}
        os.environ['DSH_SKIN_ROOT'] = probe
        try:
            dirs = [os.path.normcase(r['dir']) for r in E.discover_skin_roots()]
        finally:
            E.SKIN_ROOT, E._registry_env = orig_root, orig_reg
            if orig_env is None:
                os.environ.pop('DSH_SKIN_ROOT', None)
            else:
                os.environ['DSH_SKIN_ROOT'] = orig_env
        self.assertIn(os.path.normcase(probe), dirs)


class RunAllIsolatesDataRoot(unittest.TestCase):
    """run_all.py 必须给子进程注入隔离数据根，否则测试会污染真实数据目录。

    实证：`import server` 会在模块级解析数据根、`ensure_dirs()` 并写 `server.token`。
    """

    def _run_all_source(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'run_all.py')
        with open(path, encoding='utf-8') as handle:
            return handle.read()

    def test_injects_isolated_skin_root(self):
        src = self._run_all_source()
        self.assertIn('DSH_SKIN_ROOT', src,
                      'run_all.py 必须设置 DSH_SKIN_ROOT 以隔离数据根')
        self.assertIn('tempfile', src)
        self.assertIn('dshpp-tests', src, '隔离沙箱目录名应可辨识')

    def test_isolates_per_case(self):
        src = self._run_all_source()
        # 每套测试一个独立子目录，避免互相干扰
        self.assertIn('case_root', src)

    def test_sandbox_cleared_before_run(self):
        src = self._run_all_source()
        self.assertIn('rmtree', src, '开跑前应清空隔离沙箱，保证不依赖历史状态')

    def test_api_auth_self_isolates(self):
        """单独跑 test_api_auth.py（不经 run_all）时也必须自隔离。"""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_api_auth.py')
        with open(path, encoding='utf-8') as handle:
            src = handle.read()
        self.assertIn('DSH_SKIN_ROOT', src)
        head = src.split('import server as SV')[0]
        self.assertIn('DSH_SKIN_ROOT', head,
                      '隔离必须在 import server 之前生效 —— 否则已经在真实根下建目录了')


class ApiAuthServerCleanup(unittest.TestCase):
    """锁住 test_api_auth 的服务器清理 —— 漏掉会变成偶发 FAIL。

    症状很有迷惑性：9 步断言全部打印 PASS，退出码却是 1。
    根因是 `ThreadingHTTPServer.daemon_threads` 默认 False（退出时逐一 join
    handler 线程，而 urllib 的连接可能仍 keep-alive），且 `shutdown()` 只停
    accept 循环、不关监听 socket。修复前实测复现率约 3/10，修复后 0/30。
    """

    def _src(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_api_auth.py')
        with open(path, encoding='utf-8') as handle:
            return handle.read()

    def test_daemon_threads_enabled(self):
        self.assertIn('daemon_threads = True', self._src(),
                      'handler 线程必须设为守护，否则退出时被 join 拖住')

    def test_server_close_called(self):
        self.assertIn('server_close()', self._src(),
                      'shutdown() 不关监听 socket，必须显式 server_close()')


if __name__ == '__main__':
    unittest.main(verbosity=2)
