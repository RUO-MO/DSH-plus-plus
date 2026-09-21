#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""we_scanner · 壁纸本地扫描器回归测试

覆盖 2026-09-21 的架构修正：「读取壁纸列表」是纯文件系统操作，不依赖 DSH/CDP。

  1. 类型推断 / project.json 解析 / scene 主容器解析（对照插件同款语义）
  2. 四来源枚举：defaultprojects / myprojects / workshop / uploads
  3. playlists 解析（来自 WE 自己的 config.json）
  4. scan_inventory 的结构契约与 TTL 缓存
  5. media_file 的 id 白名单 —— **路径穿越必须被拒**
  6. _slim_inventory 对本地清单不再拼 CDP origin
  7. fetch_inventory 默认走本地，**DSH 未运行时也必须成功**（旧实现的根因缺陷）

全部用临时目录 + 桩，绝不读真实 Steam / Wallpaper Engine（CI 上没有）。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import we_scanner as S          # noqa: E402
import wallpaper_engine as W    # noqa: E402


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(obj, handle, ensure_ascii=False)


class ScannerBase(unittest.TestCase):
    """构造一个假的 WE 目录树，并桩掉全部探测函数。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='dsh-we-')
        self.we = os.path.join(self.tmp, 'we')
        self.library = os.path.join(self.tmp, 'steamlib')
        # 库结构必须与插件一致：<库>/steamapps/workshop/content/<appid>
        self.workshop = os.path.join(self.library, 'steamapps', 'workshop',
                                     'content', '431960')
        self.uploads = os.path.join(self.tmp, 'uploads')

        os.makedirs(self.uploads, exist_ok=True)
        self._build_we()

        self._orig = {
            'locate': S.locate_wallpaper_engine,
            'owning': S.owning_libraries,
            'probes': S.steam_probe_dirs,
            'upload': S.upload_dir,
        }
        S.locate_wallpaper_engine = lambda: self.we
        S.owning_libraries = lambda: [self.library]
        S.steam_probe_dirs = lambda: []
        S.upload_dir = lambda: self.uploads
        S.clear_cache()

    def tearDown(self):
        S.locate_wallpaper_engine = self._orig['locate']
        S.owning_libraries = self._orig['owning']
        S.steam_probe_dirs = self._orig['probes']
        S.upload_dir = self._orig['upload']
        S.clear_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build_we(self):
        """defaultprojects 两有效 + 一破损；workshop 一有效。"""
        dp = os.path.join(self.we, 'projects', 'defaultprojects')
        # scene：声明 scene.json，文件存在
        _write_json(os.path.join(dp, 'alpha', 'project.json'),
                    {'file': 'scene.json', 'title': 'Alpha', 'type': 'scene',
                     'preview': 'preview.jpg'})
        open(os.path.join(dp, 'alpha', 'scene.json'), 'w').close()
        open(os.path.join(dp, 'alpha', 'preview.jpg'), 'wb').close()
        # 无 type 字段 → 按扩展名推断为 video；无 preview
        _write_json(os.path.join(dp, 'beta', 'project.json'),
                    {'file': 'clip.mp4', 'title': 'Beta'})
        open(os.path.join(dp, 'beta', 'clip.mp4'), 'wb').close()
        # 破损：没有 project.json，应被跳过
        os.makedirs(os.path.join(dp, 'broken'), exist_ok=True)

        # workshop：type 缺省 + 声明 scene.json 但只给了 scene.pkg（真实工坊形态）
        _write_json(os.path.join(self.workshop, 'ws-001', 'project.json'),
                    {'file': 'scene.json', 'title': 'Workshop One',
                     'contentrating': 'Mature'})
        open(os.path.join(self.workshop, 'ws-001', 'scene.pkg'), 'wb').close()

        # 上传壁纸 + meta
        open(os.path.join(self.uploads, 'up-abc123.jpg'), 'wb').close()
        _write_json(os.path.join(self.uploads, '.meta.json'),
                    {'up-abc123': {'title': '我的壁纸', 'contentrating': 'Everyone'}})

        # WE 自己的 config.json（playlists）
        _write_json(os.path.join(self.we, 'config.json'), {
            'profile1': {'general': {'playlists': [
                {'name': '混合', 'items': [
                    os.path.join(dp, 'alpha', 'project.json'),
                    r'X:\steamapps\workshop\content\431960\ws-001\project.json',
                    r'X:\missing\nope\project.json',
                ], 'settings': {'order': 'random', 'delay': 30}},
            ]}},
        })


class TypeInference(ScannerBase):
    def test_infer_by_extension(self):
        self.assertEqual(S._infer_type('a.mp4'), 'video')
        self.assertEqual(S._infer_type('A.WEBM'), 'video')
        self.assertEqual(S._infer_type('index.html'), 'web')
        self.assertEqual(S._infer_type('x.js'), 'web')
        self.assertEqual(S._infer_type('scene.json'), 'scene')
        self.assertEqual(S._infer_type('scene.pkg'), 'scene')


class ProjectParsing(ScannerBase):
    def test_read_valid_project(self):
        p = S._read_project(os.path.join(self.we, 'projects', 'defaultprojects', 'alpha'))
        self.assertEqual(p['id'], 'alpha')
        self.assertEqual(p['title'], 'Alpha')
        self.assertEqual(p['type'], 'scene')
        self.assertEqual(p['contentrating'], None)

    def test_read_project_infers_missing_type(self):
        p = S._read_project(os.path.join(self.we, 'projects', 'defaultprojects', 'beta'))
        self.assertEqual(p['type'], 'video')
        self.assertEqual(p['preview'], None)

    def test_missing_project_json_returns_none(self):
        self.assertIsNone(S._read_project(os.path.join(
            self.we, 'projects', 'defaultprojects', 'broken')))

    def test_garbage_json_returns_none(self):
        d = os.path.join(self.tmp, 'garbage')
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'project.json'), 'w', encoding='utf-8') as f:
            f.write('{not json')
        self.assertIsNone(S._read_project(d))

    def test_project_without_file_field_returns_none(self):
        d = os.path.join(self.tmp, 'nofile')
        _write_json(os.path.join(d, 'project.json'), {'title': 'x'})
        self.assertIsNone(S._read_project(d))

    def test_scene_main_prefers_declared_then_pkg(self):
        alpha = os.path.join(self.we, 'projects', 'defaultprojects', 'alpha')
        self.assertEqual(S._resolve_scene_main(alpha, 'scene.json'), 'scene.json')
        ws = os.path.join(self.workshop, 'ws-001')
        # 声明 scene.json 不存在，回落到目录内唯一 scene.pkg
        self.assertEqual(S._resolve_scene_main(ws, 'scene.json'), 'scene.pkg')


class Enumeration(ScannerBase):
    def test_enumerates_all_sources(self):
        walls, counts = S._enumerate_wallpapers(self.we, [self.library])
        ids = sorted(w['id'] for w in walls)
        self.assertEqual(ids, ['alpha', 'beta', 'ws-001'])
        # 破损目录不计入
        self.assertEqual(counts[os.path.join(self.we, 'projects', 'defaultprojects')], 2)

    def test_scene_main_resolved_to_existing_file(self):
        walls, _ = S._enumerate_wallpapers(self.we, [self.library])
        ws = [w for w in walls if w['id'] == 'ws-001'][0]
        self.assertTrue(ws['file_abs'].endswith('scene.pkg'),
                        'scene 应回落到真实存在的 scene.pkg：%s' % ws['file_abs'])

    def test_upload_dir_null_falls_back_to_default(self):
        """config.json 的 uploadDir 为 null 时要走默认值，不能返回 None。"""
        orig = S._CONFIG_FILE
        S._CONFIG_FILE = os.path.join(self.tmp, 'nonexistent.json')
        try:
            self.assertTrue(S.upload_dir())
        finally:
            S._CONFIG_FILE = orig

    def test_every_entry_carries_source(self):
        """每条壁纸都必须带 source。

        面板与 CLI 的来源统计依赖它；曾因 `_shape()` 漏传该字段，
        导致体检结果显示成「来源: ? 30」。
        """
        inv = S.scan_inventory()
        self.assertTrue(inv['wallpapers'])
        for w in inv['wallpapers']:
            self.assertTrue(w.get('source'),
                            '壁纸 %s 缺 source 字段' % w.get('id'))

    def test_source_distinguishes_roots(self):
        """安装自带 / 创意工坊 / 上传三类来源要能区分开。"""
        inv = S.scan_inventory()
        buckets = {}
        for w in inv['wallpapers']:
            buckets.setdefault(w['source'], set()).add(w['id'])
        self.assertIn('alpha', buckets.get('defaultprojects', set()))
        self.assertIn('ws-001', buckets.get('workshop', set()))
        self.assertIn('up-abc123', buckets.get('uploads', set()))


class Playlists(ScannerBase):
    def test_playlist_items_resolve_to_known_ids(self):
        playlists = S._read_playlists(self.we)
        self.assertEqual(len(playlists), 1)
        row = playlists[0]
        self.assertEqual(row['name'], '混合')
        self.assertEqual(row['order'], 'random')
        self.assertEqual(row['delay'], 30)
        self.assertEqual(len(row['items']), 3)

    def test_inventory_playlist_counts(self):
        inv = S.scan_inventory()
        self.assertEqual(len(inv['playlists']), 1)
        pl = inv['playlists'][0]
        # alpha（安装相对路径）与 ws-001（含 appid 的路径）都应解析出来
        self.assertEqual(pl['total'], 2)
        self.assertEqual(pl['unresolvedCount'], 1)
        self.assertEqual(set(pl['wallpaperIds']), {'alpha', 'ws-001'})


class InventoryShape(ScannerBase):
    def test_required_keys(self):
        inv = S.scan_inventory()
        for key in ('installDir', 'uploadDir', 'total', 'portableCount',
                    'wallpapers', 'playlists', 'source', 'details'):
            self.assertIn(key, inv)
        self.assertEqual(inv['source'], 'local')

    def test_totals_include_uploads(self):
        inv = S.scan_inventory()
        ids = sorted(w['id'] for w in inv['wallpapers'])
        self.assertEqual(ids, ['alpha', 'beta', 'up-abc123', 'ws-001'])
        self.assertEqual(inv['total'], 4)

    def test_upload_meta_title_applied(self):
        inv = S.scan_inventory()
        up = [w for w in inv['wallpapers'] if w['id'] == 'up-abc123'][0]
        self.assertEqual(up['title'], '我的壁纸')
        self.assertEqual(up['contentrating'], 'Everyone')
        self.assertEqual(up['type'], 'image')

    def test_media_urls_point_at_own_asset_route(self):
        inv = S.scan_inventory()
        for w in inv['wallpapers']:
            if w['media']:
                self.assertTrue(w['media'].startswith('/api/wallpaper/asset/media/'))
            if w['preview']:
                self.assertTrue(w['preview'].startswith('/api/wallpaper/asset/preview/'))

    def test_cache_returns_same_object_within_ttl(self):
        a = S.scan_inventory()
        b = S.scan_inventory()
        self.assertIs(a, b)
        S.clear_cache()
        self.assertIsNot(S.scan_inventory(), a)


class MediaSafety(ScannerBase):
    def test_valid_ids_resolve(self):
        path, mime = S.media_file('alpha', 'preview')
        self.assertTrue(path and path.endswith('preview.jpg'))
        self.assertEqual(mime, 'image/jpeg')
        path, mime = S.media_file('ws-001', 'media')
        self.assertTrue(path and path.endswith('scene.pkg'))

    def test_traversal_and_junk_ids_rejected(self):
        for bad in ('../../config.json', '..\\..\\config.json', 'a/b', 'a\\b',
                    '..', '', '.', 'x' * 200, 'alpha\x00', 'notexist-xyz'):
            self.assertEqual(S.media_file(bad, 'preview'), (None, None),
                             '非法 id 应被拒: %r' % (bad,))

    def test_bad_kind_rejected(self):
        self.assertEqual(S.media_file('alpha', 'secrets'), (None, None))
        self.assertEqual(S.media_file('alpha', ''), (None, None))

    def test_find_wallpaper(self):
        self.assertEqual(S.find_wallpaper('alpha')['title'], 'Alpha')
        self.assertIsNone(S.find_wallpaper('../alpha'))
        self.assertIsNone(S.find_wallpaper('nope'))

    def test_mime_lookup(self):
        self.assertEqual(S._mime_for('a.PNG'), 'image/png')
        self.assertEqual(S._mime_for('a.mp4'), 'video/mp4')
        self.assertEqual(S._mime_for('a.unknown'), 'application/octet-stream')
        self.assertEqual(S._mime_for('noext'), 'application/octet-stream')


class SlimInventory(ScannerBase):
    def test_local_source_does_not_prepend_cdp_origin(self):
        """本地清单的 URL 必须原样保留 —— 拼 CDP origin 会让 DSH 未运行时全裂图。"""
        inv = S.scan_inventory()
        slim = W._slim_inventory(inv)
        w = [x for x in slim['wallpapers'] if x['id'] == 'alpha'][0]
        self.assertTrue(w['preview'].startswith('/api/wallpaper/asset/'))
        self.assertNotIn('http://', w['preview'])

    def test_plugin_source_still_normalised(self):
        """插件来源的 token 路由仍需绝对化（保留路径的行为不能被破坏）。"""
        inv = {'wallpapers': [{'id': 'p1', 'playable': True,
                               'preview': '/wallpaper-engine/preview/tok'}],
               'total': 1}
        slim = W._slim_inventory(inv)
        self.assertIn('preview', slim['wallpapers'][0])

    def test_carries_source_and_details(self):
        slim = W._slim_inventory(S.scan_inventory())
        self.assertEqual(slim['source'], 'local')
        self.assertIn('details', slim)


class FetchInventoryIsLocal(ScannerBase):
    """旧实现的核心缺陷：DSH 没开调试端口时清单整个不可用且无降级。"""

    def test_default_path_does_not_touch_cdp(self):
        calls = []
        orig = W._cdp_request
        W._cdp_request = lambda *a, **k: (calls.append(a), {'ok': False, 'err': 'x'})[1]
        try:
            res = W.fetch_inventory()
        finally:
            W._cdp_request = orig
        self.assertTrue(res['ok'], '本地扫描不应因 CDP 不可用而失败')
        self.assertEqual(res['via'], 'local')
        self.assertEqual(calls, [], '默认路径不得调用 CDP')
        self.assertEqual(res['inventory']['total'], 4)

    def test_explicit_plugin_source_still_available(self):
        res = W.fetch_inventory(source='plugin', timeout=0.3)
        self.assertIsInstance(res, dict)
        self.assertIn('ok', res)

    def test_payload_does_not_require_plugin_installed(self):
        """读取壁纸与插件是否安装无关：插件没装也要给清单。"""
        orig = W.plugin_status
        W.plugin_status = lambda profile='desktop': {
            'installed': False, 'version': None, 'profile': profile,
            'plugin_dir': None, 'profiles': [], 'config': '', 'config_exists': False,
            'upload_dir': ''}
        orig_fetch = W.fetch_settings
        W.fetch_settings = lambda timeout=8.0: {'settings': {}, 'betterSidebar': None,
                                                'source': 'file', 'route_err': 'stub'}
        try:
            payload = W.inventory_payload(fetch=True)
        finally:
            W.plugin_status = orig
            W.fetch_settings = orig_fetch
        self.assertFalse(payload['plugin']['installed'])
        self.assertIsNotNone(payload['inventory'], '插件未安装时也应返回本机壁纸清单')
        self.assertEqual(payload['inventory_via'], 'local')
        self.assertFalse(payload.get('inventory_err'), '本地扫描成功时不应有错误信息')


if __name__ == '__main__':
    unittest.main(verbosity=2)
