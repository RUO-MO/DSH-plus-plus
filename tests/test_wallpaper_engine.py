#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""wallpaper_engine · 动态壁纸适配层回归测试

覆盖换肤移除后新增的适配层（原 test_theme_engine.py 删除后的能力补位）：
  1. marker_engine.MARKER_VERSION 存在；换肤时代的 TEMPLATE_VERSION 已清除
  2. plugin_status() 结构契约（installed/version/config 为路径字符串而非 dict）
  3. load_settings() 永远返回 dict
  4. save_settings() 参数白名单：非法键被丢弃、越界值被夹紧、未知键不落盘
  5. **参数区间与插件 sanitizeSettings 的 clamp 一致**（2026-09-20 修的真缺陷：
     区间曾写宽，导致面板滑杆越过插件边界后值被插件重置成默认 —— 「拖了没反应」）
  6. 写操作优先走 PUT 路由，失败才降级改文件
  7. fetch_inventory() 在 DSH 未运行时返回可解释错误而非抛异常

全部使用临时目录 + monkeypatch，不触碰真实 ~/.dsh-wallpaper-engine。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import marker_engine        # noqa: E402
import wallpaper_engine as W  # noqa: E402


class MarkerContract(unittest.TestCase):
    def test_marker_version_is_int(self):
        self.assertIsInstance(marker_engine.MARKER_VERSION, int)
        self.assertGreaterEqual(marker_engine.MARKER_VERSION, 1)

    def test_marker_names_stable(self):
        for name in ('frame', 'sidebar', 'chat', 'composer', 'scroll'):
            self.assertIn(name, marker_engine.MARKER_NAMES)

    def test_no_template_version_left(self):
        """换肤移除后不应再有 TEMPLATE_VERSION。"""
        self.assertFalse(hasattr(marker_engine, 'TEMPLATE_VERSION'))


class WallpaperEngineBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='dsh-wp-')
        self._orig_cfg = W.CONFIG_FILE
        W.CONFIG_FILE = os.path.join(self.tmp, 'config.json')
        # 隔离 CDP：默认让「路由不可达」，save_settings 走文件降级路径，
        # 避免测试真的去连 DSH。需要测路由的用例自己再 patch。
        self._orig_req = W._cdp_request

    def tearDown(self):
        W.CONFIG_FILE = self._orig_cfg
        W._cdp_request = self._orig_req
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, obj):
        with open(W.CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False)

    def _read(self):
        with open(W.CONFIG_FILE, encoding='utf-8') as f:
            return json.load(f)

    def _route_down(self):
        """把 CDP 路由打桩为不可达（模拟 DSH 未运行）。"""
        W._cdp_request = lambda *a, **k: {'ok': False, 'err': 'stub: 路由不可达'}


class SettingsReadWrite(WallpaperEngineBase):
    def setUp(self):
        super().setUp()
        self._route_down()

    def test_load_settings_missing_file_returns_dict(self):
        s = W.load_settings()
        self.assertIsInstance(s, dict)

    def test_load_settings_returns_settings_subtree(self):
        self._write({'settings': {'id': 'abc', 'blur': 40}, 'uploadDir': 'X'})
        s = W.load_settings()
        self.assertEqual(s.get('id'), 'abc')
        self.assertEqual(s.get('blur'), 40)
        self.assertNotIn('uploadDir', s)

    def test_save_rejects_unknown_key(self):
        self._write({'settings': {'id': 'keep'}})
        saved, changed, _res = W.save_settings({'evilKey': 'x', 'blur': 20})
        self.assertNotIn('evilKey', saved)
        self.assertNotIn('evilKey', changed)
        self.assertIn('blur', changed)

    def test_save_clamps_out_of_range(self):
        """越界值必须被主动夹紧到 [lo,hi]，而不是原样发给插件。

        插件 clampNum 的语义是「越界→回落 fallback（默认值）」，不是夹紧；
        所以本工具若不自夹，用户拖到 99999 会被插件悄悄重置成默认值。
        """
        self._write({'settings': {}})
        saved, changed, _res = W.save_settings({'blur': 99999})
        lo_hi = None
        for key, _label, lo, hi, _step, _isint in W.TUNABLE:
            if key == 'blur':
                lo_hi = (lo, hi)
        self.assertIsNotNone(lo_hi, 'blur 应在 TUNABLE 白名单里')
        self.assertLessEqual(saved['blur'], lo_hi[1])
        self.assertGreaterEqual(saved['blur'], lo_hi[0])
        self.assertEqual(saved['blur'], lo_hi[1], '上溢应夹到上界')

    def test_save_clamps_below_lower_bound(self):
        self._write({'settings': {}})
        saved, _changed, _res = W.save_settings({'playbackRate': -5})
        self.assertEqual(saved['playbackRate'], 0.5, '下溢应夹到下界')

    def test_save_preserves_other_keys(self):
        self._write({'settings': {'id': 'a', 'blur': 10, 'accent': '#abcdef'}})
        W.save_settings({'id': 'zzz'})
        raw = self._read()
        self.assertEqual(raw['settings'].get('id'), 'zzz')
        # 白名单内但未提交的键原样保留
        self.assertEqual(raw['settings'].get('blur'), 10)
        self.assertEqual(raw['settings'].get('accent'), '#abcdef')

    def test_save_preserves_untouched_complex_keys(self):
        """插件内部维护的复杂结构（轮播组 / 隐藏列表）不得被本工具改动。"""
        self._write({'settings': {
            'id': 'a',
            'rotationGroups': [{'id': 'g1', 'name': 'X', 'interval': 30,
                                'order': 'sequence', 'wallpaperIds': ['p']}],
            'hiddenIds': ['h1', 'h2'],
            'rotationSeeded': True,
            'noticeSeen': '0.7.2',
        }})
        W.save_settings({'blur': 30})
        s = self._read()['settings']
        self.assertEqual(s['rotationGroups'][0]['id'], 'g1')
        self.assertEqual(s['hiddenIds'], ['h1', 'h2'])
        self.assertIs(s['rotationSeeded'], True)
        self.assertEqual(s['noticeSeen'], '0.7.2')

    def test_save_no_valid_keys_reports_unchanged(self):
        self._write({'settings': {'id': 'a'}})
        _saved, changed, res = W.save_settings({'nope': 1})
        self.assertEqual(changed, [])
        self.assertEqual(res['via'], 'none', '无有效键时不应产生任何写入')

    def test_save_noop_when_value_identical(self):
        """值没变时不应触发写入（changed 为空）。"""
        self._write({'settings': {'blur': 30}})
        _saved, changed, res = W.save_settings({'blur': 30})
        self.assertEqual(changed, [])
        self.assertEqual(res['via'], 'none')

    def test_save_reports_file_fallback_when_route_down(self):
        self._write({'settings': {}})
        _saved, changed, res = W.save_settings({'blur': 20})
        self.assertIn('blur', changed)
        self.assertEqual(res['via'], 'file', '路由不可达时应降级改文件')
        self.assertTrue(res['err'])
        self.assertEqual(self._read()['settings']['blur'], 20)

    def test_save_rejects_bad_enum_and_still_saves_valid(self):
        self._write({'settings': {}})
        _saved, changed, _res = W.save_settings(
            {'objectFit': 'nonsense', 'ropeForm': 'whale'})
        self.assertNotIn('objectFit', changed)
        self.assertIn('ropeForm', changed)

    def test_save_rejects_bad_color(self):
        self._write({'settings': {}})
        _saved, changed, _res = W.save_settings(
            {'accent': 'red', 'glassColor': '#AABBCC'})
        self.assertNotIn('accent', changed)
        self.assertIn('glassColor', changed)

    def test_save_accepts_empty_caret_color(self):
        """caretColor 允许空串（表示跟随原生）。"""
        self._write({'settings': {'caretColor': '#000000'}})
        _saved, changed, _res = W.save_settings({'caretColor': ''})
        self.assertIn('caretColor', changed)
        self.assertEqual(self._read()['settings']['caretColor'], '')

    def test_save_coerces_bool_toggle(self):
        self._write({'settings': {'flip': False}})
        _saved, changed, _res = W.save_settings({'flip': True})
        self.assertIn('flip', changed)
        self.assertIs(self._read()['settings']['flip'], True)

    def test_save_writes_integer_params_as_int(self):
        self._write({'settings': {}})
        W.save_settings({'blur': 32.0})
        v = self._read()['settings']['blur']
        self.assertIsInstance(v, int)

    def test_write_is_atomic_no_tmp_left(self):
        self._write({'settings': {}})
        W.save_settings({'blur': 20})
        leftovers = [f for f in os.listdir(self.tmp) if f.endswith('.dshpp.tmp')]
        self.assertEqual(leftovers, [], '原子写不应残留 .tmp 文件')


class SettingsRoutePreference(WallpaperEngineBase):
    """写操作应优先走插件 PUT 路由（复用其串行队列），失败才降级改文件。"""

    def test_put_route_used_when_available(self):
        calls = []

        def fake(method, path, body=None, **kw):
            calls.append((method, path, body))
            if method == 'GET':
                return {'ok': True, 'status': 200,
                        'data': {'settings': {'id': 'a', 'blur': 10},
                                 'betterSidebar': False}}
            if method == 'PUT':
                return {'ok': True, 'status': 200,
                        'data': {'ok': True, 'settings': body}}
            return {'ok': False, 'err': 'unexpected'}

        W._cdp_request = fake
        _saved, changed, res = W.save_settings({'blur': 40})
        self.assertIn('blur', changed)
        self.assertEqual(res['via'], 'route')
        self.assertIsNone(res['err'])
        # 必须真的发出了 PUT，且目标路径正确
        puts = [c for c in calls if c[0] == 'PUT']
        self.assertEqual(len(puts), 1, '应恰好发一次 PUT')
        self.assertEqual(puts[0][1], '/wallpaper-engine/settings')
        # body 必须是**完整对象**（含未改动的 id），因为插件 PUT 是整对象替换
        self.assertEqual(puts[0][2].get('id'), 'a')
        self.assertEqual(puts[0][2].get('blur'), 40)
        # 且不得因此产生本地文件写入
        self.assertFalse(os.path.exists(W.CONFIG_FILE))

    def test_put_body_is_full_object_not_patch(self):
        """插件 PUT 走 sanitizeSettings 整对象落盘 —— 漏字段等于重置为默认。"""
        seen = {}

        def fake(method, path, body=None, **kw):
            if method == 'GET':
                return {'ok': True, 'status': 200,
                        'data': {'settings': {'id': 'a', 'blur': 10, 'scrim': 0.5,
                                              'accent': '#123456'}, 'betterSidebar': None}}
            seen['body'] = body
            return {'ok': True, 'status': 200, 'data': {'settings': body}}

        W._cdp_request = fake
        W.save_settings({'blur': 40})
        for k in ('id', 'blur', 'scrim', 'accent'):
            self.assertIn(k, seen['body'], 'PUT body 必须携带原有键 %s' % k)
        self.assertEqual(seen['body']['scrim'], 0.5)

    def test_route_500_falls_back_to_file(self):
        def fake(method, path, body=None, **kw):
            if method == 'GET':
                return {'ok': True, 'status': 200,
                        'data': {'settings': {'id': 'a'}}}
            return {'ok': False, 'status': 500, 'err': 'HTTP 500 boom'}

        W._cdp_request = fake
        _saved, changed, res = W.save_settings({'blur': 20})
        self.assertIn('blur', changed)
        self.assertEqual(res['via'], 'file')
        self.assertIn('500', res['err'])

    def test_route_response_settings_echo_wins(self):
        """插件可能因校验调整值，以它回显的为准。"""
        def fake(method, path, body=None, **kw):
            if method == 'GET':
                return {'ok': True, 'status': 200,
                        'data': {'settings': {'id': 'a'}, 'betterSidebar': None}}
            return {'ok': True, 'status': 200,
                    'data': {'settings': {'id': 'a', 'blur': 60}}}

        W._cdp_request = fake
        saved, _changed, res = W.save_settings({'blur': 999})
        self.assertEqual(res['via'], 'route')
        self.assertEqual(saved.get('blur'), 60, '应采用插件回显值')

    def test_fetch_settings_falls_back_to_file_on_route_error(self):
        self._write({'settings': {'id': 'fromfile'}})
        W._cdp_request = lambda *a, **k: {'ok': False, 'err': 'down'}
        r = W.fetch_settings()
        self.assertEqual(r['source'], 'file')
        self.assertTrue(r.get('route_err'))
        self.assertEqual(r['settings'].get('id'), 'fromfile')

    def test_fetch_settings_prefers_route(self):
        self._write({'settings': {'id': 'fromfile'}})
        W._cdp_request = lambda *a, **k: {
            'ok': True, 'status': 200,
            'data': {'settings': {'id': 'fromroute'}, 'betterSidebar': True}}
        r = W.fetch_settings()
        self.assertEqual(r['source'], 'route')
        self.assertEqual(r['settings'].get('id'), 'fromroute')
        self.assertIs(r['betterSidebar'], True)


class TunableRangesMatchPlugin(WallpaperEngineBase):
    """面板滑杆区间必须与插件 sanitizeSettings 的 clamp 一致。

    2026-09-20 实测发现 8 项里 7 项区间写错（如 blur 放行到 120 而插件上限 60），
    后果：用户拖过插件边界 → 插件 clampNum 回落到默认值 → 「拖了没反应」。
    这里把插件的真实区间固化下来，防止再次写宽。
    """

    # 摘自插件 lib/index.js sanitizeSettings()（v0.7.3，2026-09-20 核对）
    PLUGIN_CLAMPS = {
        'scrim': (0, 1),
        'border': (0, 1),
        'blur': (0, 60),
        'wallpaperBlur': (0, 60),
        'backgroundBrightness': (40, 160),
        'backgroundContrast': (40, 200),
        'backgroundSaturate': (0, 200),
        'playbackRate': (0.5, 2),
        'glassAlpha': (0, 60),
        'sidebarBlur': (0, 200),
        'sidebarAlpha': (0, 200),
        'sidebarContentAlpha': (0, 80),
        'ropeScale': (0.5, 2.5),
        'fontWeight': (100, 900),
        'wallpaperOpacity': (0, 90),
    }

    def test_every_tunable_range_matches_plugin(self):
        for key, _label, lo, hi, _step, _pct in W.TUNABLE:
            self.assertIn(key, self.PLUGIN_CLAMPS,
                          'TUNABLE 出现未核对区间的键: %s' % key)
            plo, phi = self.PLUGIN_CLAMPS[key]
            self.assertEqual(
                (lo, hi), (plo, phi),
                '%s 区间 [%g,%g] 与插件 clamp [%g,%g] 不一致 —— '
                '越界值会被插件重置为默认，表现为「拖了没反应」'
                % (key, lo, hi, plo, phi))

    def test_no_tunable_exceeds_plugin_bounds(self):
        """兜底：任何一项都不得超出插件边界（宁可窄，不可宽）。"""
        for key, _label, lo, hi, _step, _pct in W.TUNABLE:
            plo, phi = self.PLUGIN_CLAMPS[key]
            self.assertGreaterEqual(lo, plo, '%s 下界超出插件范围' % key)
            self.assertLessEqual(hi, phi, '%s 上界超出插件范围' % key)

    def test_enum_values_match_plugin(self):
        """枚举取值必须落在插件白名单内，否则被 clampStr 回落默认。"""
        plugin_enums = {
            'objectFit': ['cover', 'contain', 'center', 'fill'],
            'typeFilter': ['all', 'video', 'web', 'image', 'scene'],
            'contentRatingFilter': ['all', 'everyone', 'pg13', 'mature', 'unrated'],
            'pickerLayout': ['fixed', 'classic'],
            'ropeForm': ['maid', 'whale'],
            'fontFamily': ['inherit', 'Microsoft YaHei', 'KaiTi', 'SimSun',
                           'SimHei', 'STXingkai', 'monospace'],
            'fpsCap': [0, 60, 48, 30, 24],
        }
        for key, _label, vals, _dflt in W.ENUMS:
            self.assertIn(key, plugin_enums, '未核对的枚举键: %s' % key)
            self.assertEqual(list(vals), plugin_enums[key],
                             '%s 取值表与插件不一致' % key)

    def test_enum_defaults_in_whitelist(self):
        for key, _label, vals, dflt in W.ENUMS:
            self.assertIn(dflt, vals, '%s 默认值不在白名单内' % key)

    def test_coerce_clamps_to_bounds(self):
        for key in self.PLUGIN_CLAMPS:
            if key not in W._TUNABLE_MAP:
                continue
            lo, hi, _s, _p = W._TUNABLE_MAP[key]
            ok_hi, v_hi = W._coerce(key, 1e9)
            self.assertTrue(ok_hi)
            self.assertEqual(v_hi, int(hi) if float(hi).is_integer() and
                             float(lo).is_integer() else hi)
            ok_lo, v_lo = W._coerce(key, -1e9)
            self.assertTrue(ok_lo)
            self.assertEqual(v_lo, int(lo) if float(lo).is_integer() and
                             float(hi).is_integer() else lo)

    def test_coerce_rejects_non_numeric(self):
        ok, _v = W._coerce('blur', 'not-a-number')
        self.assertFalse(ok)

    def test_coerce_rejects_nan(self):
        ok, _v = W._coerce('blur', float('nan'))
        self.assertFalse(ok)

    def test_writable_covers_all_param_groups(self):
        """面板声明的四类参数都必须落在可写白名单里。"""
        for k, *_ in W.TUNABLE:
            self.assertIn(k, W._WRITABLE)
        for k, *_ in W.ENUMS:
            self.assertIn(k, W._WRITABLE)
        for k, *_ in W.TOGGLES:
            self.assertIn(k, W._WRITABLE)
        for k, *_ in W.COLORS:
            self.assertIn(k, W._WRITABLE)

    def test_complex_plugin_keys_are_not_writable(self):
        """插件内部结构不得被面板改写。"""
        for k in ('rotationGroups', 'hiddenIds', 'rotationSeeded', 'noticeSeen'):
            self.assertNotIn(k, W._WRITABLE,
                             '%s 由插件内部维护，不应列为可写' % k)


class PluginStatusContract(WallpaperEngineBase):
    def test_plugin_status_shape(self):
        st = W.plugin_status()
        self.assertIsInstance(st, dict)
        for k in ('installed', 'version', 'profile', 'config', 'config_exists'):
            self.assertIn(k, st)
        # config 必须是路径字符串（历史坑：曾误当作 dict 用 .get()）
        self.assertIsInstance(st['config'], str)

    def test_settings_isolated_to_tmp(self):
        """确认测试确实没有读写真实配置。"""
        self.assertEqual(os.path.dirname(W.CONFIG_FILE), self.tmp)


class FetcherRobustness(unittest.TestCase):
    def test_fetch_inventory_returns_dict_or_throws_nothing(self):
        """DSH 未运行时应返回 {ok: False, err: ...} 而不抛异常。"""
        try:
            r = W.fetch_inventory(timeout=0.5)
        except Exception as e:      # 任何异常都算失败
            self.fail('fetch_inventory 不应抛异常: %r' % (e,))
        self.assertIsInstance(r, dict)
        self.assertIn('ok', r)
        if not r['ok']:
            # 降级路径必须带可读原因
            self.assertTrue(r.get('err'), '失败时应给出 err 说明')

    def test_cdp_request_never_throws(self):
        try:
            r = W._cdp_request('GET', '/wallpaper-engine/settings', timeout=0.5)
        except Exception as e:
            self.fail('_cdp_request 不应抛异常: %r' % (e,))
        self.assertIsInstance(r, dict)
        self.assertIn('ok', r)

    def test_inventory_payload_never_throws(self):
        """即使拉不到清单，payload 也必须是可 JSON 化的 dict。"""
        try:
            p = W.inventory_payload(fetch=False)
        except Exception as e:
            self.fail('inventory_payload 不应抛异常: %r' % (e,))
        self.assertIsInstance(p, dict)
        # 必须能 JSON 序列化（server 会直接 _json 出去）
        json.dumps(p, ensure_ascii=False, default=str)

    def test_inventory_payload_exposes_param_groups(self):
        p = W.inventory_payload(fetch=False)
        for k in ('plugin', 'settings', 'tunables', 'enums', 'toggles', 'colors'):
            self.assertIn(k, p, 'payload 应含 %s' % k)
        self.assertIsInstance(p['tunables'], list)
        self.assertIsInstance(p['enums'], list)
        self.assertIsInstance(p['toggles'], list)
        self.assertIsInstance(p['colors'], list)

    def test_tunable_schema_wellformed(self):
        for row in W.TUNABLE:
            self.assertEqual(len(row), 6, 'TUNABLE 每项应为 6 元组: %r' % (row,))
            key, label, lo, hi, step, is_int = row
            self.assertIsInstance(key, str)
            self.assertIsInstance(label, str)
            self.assertLess(lo, hi)
            self.assertGreater(step, 0)
            self.assertIsInstance(is_int, bool)

    def test_enum_schema_wellformed(self):
        for row in W.ENUMS:
            self.assertEqual(len(row), 4, 'ENUMS 每项应为 4 元组: %r' % (row,))
            key, label, vals, dflt = row
            self.assertIsInstance(key, str)
            self.assertIsInstance(label, str)
            self.assertIsInstance(vals, list)
            self.assertGreaterEqual(len(vals), 2)

    def test_toggle_schema_wellformed(self):
        for row in W.TOGGLES:
            self.assertEqual(len(row), 3, 'TOGGLES 每项应为 3 元组: %r' % (row,))

    def test_color_schema_wellformed(self):
        for row in W.COLORS:
            self.assertEqual(len(row), 3, 'COLORS 每项应为 3 元组: %r' % (row,))

    def test_slim_inventory_keeps_scene_fields(self):
        """Scene 类型壁纸要带 sceneUrl / sceneVideo（否则面板无法播场景）。"""
        inv = {'wallpapers': [{'id': 's1', 'title': 'S', 'type': 'scene',
                               'playable': True,
                               'sceneUrl': '/wallpaper-engine/scene-runtime/t1',
                               'sceneVideo': '/wallpaper-engine/scene-video/t2'}],
               'total': 1}
        slim = W._slim_inventory(inv)
        w = slim['wallpapers'][0]
        self.assertIn('sceneUrl', w)
        self.assertIn('sceneVideo', w)

    def test_slim_inventory_tolerates_garbage(self):
        for bad in (None, [], 'x', {}, {'wallpapers': 'nope'}):
            out = W._slim_inventory(bad)
            if bad is None or not isinstance(bad, dict):
                self.assertIsNone(out)
            else:
                self.assertIsInstance(out, dict)


if __name__ == '__main__':
    unittest.main(verbosity=2)
