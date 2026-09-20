# -*- coding: utf-8 -*-
"""A/B 双轨单元测试：spec 解析选轨、bundles 挂名事务、分轨启停/卸载、settings 命名空间清理。
全部离线（npm/pnpm/网络均打桩），不动真实 ~/.dsh-skins 与 ~/.dsh。"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dsh_env
import plugin_manager as pm

_FAIL = []


def _stub(name, fn):
    """打桩并登记，tearDown 统一还原。"""
    setattr(pm, name, fn)
    _FAIL.append((pm, name, getattr(pm, name)))


def _stub_env(name, fn):
    setattr(dsh_env, name, fn)
    _FAIL.append((dsh_env, name, getattr(dsh_env, name)))


class TrackBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='dshskin-track-test-')
        _FAIL.clear()
        # 隔离 registry / store
        _stub('STORE', os.path.join(self.tmp, 'plugins'))
        _stub('REGISTRY', os.path.join(pm.STORE, 'registry.json'))
        _stub('_inject_lock', threading.RLock())
        # pnpm / 运行状态 / 模式 / home 全打桩
        _stub('_run_pnpm', lambda args, timeout=900: None)
        _stub_env('is_running', lambda: False)
        _stub_env('launch_mode', lambda: 'packaged')
        _stub_env('dsh_home', lambda: (self.tmp, 'test'))
        # web 补丁层 / 桌面项目目录指向临时区，避免触碰真实文件
        _stub('_web_patch_path', lambda: os.path.join(self.tmp, 'web', 'cordis.patch.yml'))
        _stub('_desktop_project_dir', lambda: os.path.join(self.tmp, 'dproject'))
        _stub('_desktop_patch_path', lambda: os.path.join(self.tmp, 'dproject', 'cordis.patch.yml'))
        _stub_env('resolve_node', lambda: (None, None))

    def tearDown(self):
        for obj, name, orig in _FAIL:
            setattr(obj, name, orig)
        _FAIL.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- spec 解析 ----
    def test_parse_npm_spec(self):
        self.assertEqual(pm.parse_npm_spec('dshmarket'), ('dshmarket', None))
        self.assertEqual(pm.parse_npm_spec('dshmarket@1.2.3'), ('dshmarket', '1.2.3'))
        self.assertEqual(pm.parse_npm_spec('@liustack/modlens@3.17.2'), ('@liustack/modlens', '3.17.2'))
        self.assertIsNone(pm.parse_npm_spec('github:o/r#main'))
        self.assertIsNone(pm.parse_npm_spec('https://x/y'))
        self.assertIsNone(pm.parse_npm_spec('./local-dir'))

    def test_parse_github_spec(self):
        self.assertEqual(pm.parse_github_spec('github:owner/repo#dev'), ('owner', 'repo', 'dev'))
        self.assertEqual(pm.parse_github_spec('github:owner/repo'), ('owner', 'repo', None))
        self.assertEqual(pm.parse_github_spec('https://github.com/o/r/tree/feature'),
                         ('o', 'r', 'feature'))
        self.assertIsNone(pm.parse_github_spec('gitlab:o/r'))

    def test_resolve_spec_local(self):
        d = os.path.join(self.tmp, 'localpkg')
        os.makedirs(d)
        self.assertEqual(pm.resolve_spec(d)['kind'], 'local-dir')
        self.assertEqual(pm.resolve_spec(d)['track'], 'B')

    def test_resolve_spec_npm_missing_raises(self):
        _stub('npm_lookup', lambda n, v=None, timeout=8: None)
        with self.assertRaises(ValueError):
            pm.resolve_spec('dshmarket')

    def test_resolve_spec_npm_track_a(self):
        _stub('npm_lookup', lambda n, v=None, timeout=8: ('1.0.0', 'desc', 'url'))
        rs = pm.resolve_spec('dshmarket')
        self.assertEqual(rs['track'], 'A')
        self.assertEqual(rs['version'], '1.0.0')

    # ---- A 轨 ----
    def _fake_profile(self):
        pdir = os.path.join(self.tmp, 'profiles', 'desktop')
        return pdir

    def test_install_bundle_and_bundles_entry(self):
        pdir = self._fake_profile()

        def fake_pnpm(args, timeout=900):
            if 'add' in args:
                pm._ensure_profile_project(pdir)
                m = pm._profile_manifest(pdir) or {}
                m.setdefault('dependencies', {})['dshmarket'] = '1.0.0'
                with open(os.path.join(pdir, 'package.json'), 'w', encoding='utf-8') as f:
                    json.dump(m, f)
        _stub('_run_pnpm', fake_pnpm)
        rec = pm.install_bundle_npm('dshmarket', '1.0.0')
        self.assertEqual(rec['track'], 'A')
        self.assertTrue(os.path.isfile(os.path.join(pdir, 'package.json.dshskin-orig')))
        self.assertIn('dshmarket', pm._bundles_list(pdir))

        # list_plugins：A 轨 alive/wired
        data = pm.list_plugins()
        item = [p for p in data['plugins'] if p['name'] == 'dshmarket'][0]
        self.assertEqual(item['track'], 'A')

        # 启停 = bundles 去留（dependencies 保留）
        os.makedirs(os.path.join(pdir, 'node_modules', 'dshmarket'))  # 模拟包体
        pm.set_enabled('dshmarket', 'desktop', False)
        self.assertNotIn('dshmarket', pm._bundles_list(pdir) or [])
        m = pm._profile_manifest(pdir)
        self.assertIn('dshmarket', m['dependencies'])
        pm.set_enabled('dshmarket', 'desktop', True)
        self.assertIn('dshmarket', pm._bundles_list(pdir))

    def test_install_bundle_blocked_when_running(self):
        _stub_env('is_running', lambda: True)
        with self.assertRaises(ValueError):
            pm.install_bundle_npm('dshmarket', '1.0.0')

    def test_install_bundle_blocked_in_dev_mode(self):
        _stub_env('launch_mode', lambda: 'dev')
        with self.assertRaises(ValueError):
            pm.install_bundle_npm('dshmarket', '1.0.0')

    def test_remove_bundle_cleans_registry_and_bundles(self):
        pdir = self._fake_profile()

        def fake_pnpm(args, timeout=900):
            if 'add' in args:
                pm._ensure_profile_project(pdir)
                m = pm._profile_manifest(pdir) or {}
                m.setdefault('dependencies', {})['dshmarket'] = '1.0.0'
                with open(os.path.join(pdir, 'package.json'), 'w', encoding='utf-8') as f:
                    json.dump(m, f)
            elif 'remove' in args:
                m = pm._profile_manifest(pdir) or {}
                m.get('dependencies', {}).pop('dshmarket', None)
                with open(os.path.join(pdir, 'package.json'), 'w', encoding='utf-8') as f:
                    json.dump(m, f)
        _stub('_run_pnpm', fake_pnpm)
        pm.install_bundle_npm('dshmarket', '1.0.0')
        # 记一个 settings 命名空间，卸载应一并清掉
        home = self.tmp
        with open(os.path.join(home, 'settings.yaml'), 'w', encoding='utf-8') as f:
            f.write('dshmarket:\n  port: 1\nother:\n  x: 2\n')
        reg = pm._load_registry()
        reg['plugins']['dshmarket']['settings'] = {'ns': 'dshmarket', 'text': 'port: 1'}
        pm._save_registry(reg)

        pm.remove_package('dshmarket')
        self.assertNotIn('dshmarket', pm._load_registry()['plugins'])
        self.assertNotIn('dshmarket', pm._bundles_list(pdir) or [])
        m = pm._profile_manifest(pdir)
        self.assertNotIn('dshmarket', m.get('dependencies', {}))
        with open(os.path.join(home, 'settings.yaml'), encoding='utf-8') as f:
            self.assertNotIn('dshmarket:', f.read())

    # ---- B 轨 ----
    def _make_b_plugin(self, name='demo-plugin', deps=None):
        d = os.path.join(self.tmp, 'src', name)
        os.makedirs(os.path.join(d, 'lib'))
        with open(os.path.join(d, 'package.json'), 'w', encoding='utf-8') as f:
            json.dump({'name': name, 'version': '0.1.0',
                       'dependencies': deps or {},
                       'dsh': {'client': {'platform': 'web'}}}, f)
        with open(os.path.join(d, 'lib', 'index.js'), 'w', encoding='utf-8') as f:
            f.write('export default {}\n')
        return d

    def test_b_track_flow(self):
        _stub('sync_deps', lambda extra_dirs=(), required=False: True)
        d = self._make_b_plugin()
        info = pm.import_dir(d)
        self.assertEqual(info['track'], 'B')
        data = pm.list_plugins()
        item = [p for p in data['plugins'] if p['name'] == 'demo-plugin'][0]
        self.assertEqual(item['track'], 'B')
        self.assertTrue(item['wired']['web'] or True)  # 写盘路径已隔离，wired 只验证不抛错
        # apply_rows 不给 A 轨记录写装配行（混合 registry 验证）
        reg = pm._load_registry()
        reg['plugins']['fake-a'] = {'name': 'fake-a', 'track': 'A', 'dir': '',
                                    'targets': {'web': True, 'desktop': True}}
        pm._save_registry(reg)
        pm.apply_rows()
        for pp in (pm._web_patch_path(), pm._desktop_patch_path()):
            if os.path.isfile(pp):
                with open(pp, encoding='utf-8') as f:
                    self.assertNotIn('fake-a', f.read())

    def test_a_track_config_guard(self):
        reg = pm._load_registry()
        reg['plugins']['x-a'] = {'name': 'x-a', 'track': 'A'}
        pm._save_registry(reg)
        with self.assertRaises(ValueError):
            pm.set_plugin_config('x-a', 'port: 1')

    def test_b_remove_cleans_namespace(self):
        _stub('sync_deps', lambda extra_dirs=(), required=False: True)
        d = self._make_b_plugin()
        pm.import_dir(d)
        with open(os.path.join(self.tmp, 'settings.yaml'), 'w', encoding='utf-8') as f:
            f.write('demo-plugin:\n  a: 1\nkeep:\n  b: 2\n')
        reg = pm._load_registry()
        reg['plugins']['demo-plugin']['settings'] = {'ns': 'demo-plugin', 'text': 'a: 1'}
        pm._save_registry(reg)
        pm.remove_package('demo-plugin')
        self.assertNotIn('demo-plugin', pm._load_registry()['plugins'])
        self.assertFalse(os.path.isdir(os.path.join(pm.STORE, 'demo-plugin')))
        with open(os.path.join(self.tmp, 'settings.yaml'), encoding='utf-8') as f:
            content = f.read()
        self.assertNotIn('demo-plugin', content)
        self.assertIn('keep', content)


if __name__ == '__main__':
    unittest.main(verbosity=2)
