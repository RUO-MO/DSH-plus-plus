# -*- coding: utf-8 -*-
"""壁纸媒体 asset 路由（`/api/wallpaper/asset/<kind>/<id>`）回归测试。

为什么需要这套
--------------
这条路由是**鉴权面上唯一的免 token 口子**（`<img src>` / `<video src>` 无法携带
自定义请求头，若要求令牌则所有缩略图与视频都会裂图），而且带 Range / ETag /
304 这些很容易在重构中被悄悄弄坏的细节。此前只有手工端到端验证，没有测试锁住，
所以任何一次改动都可能让「缩略图全裂」而不被 CI 发现。

本套测试真起 ThreadingHTTPServer，但**不依赖本机 Steam / DSH**：
`we_scanner` 的扫描被替换成固定路径表，主文件与预览图是临时生成的假文件。
"""
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 数据根隔离：import server 会在模块级解析数据根、建目录并写 server.token
_ISO_ROOT = tempfile.mkdtemp(prefix='dshpp-asset-root-')
os.environ['DSH_SKIN_ROOT'] = _ISO_ROOT

import server as SV          # noqa: E402
import we_scanner            # noqa: E402
from http.server import ThreadingHTTPServer   # noqa: E402

# handler 线程随主线程退出，避免退出时被 keep-alive 连接 join 拖住
ThreadingHTTPServer.daemon_threads = True

WID = 'unitwp'
MEDIA_BYTES = bytes(range(256)) * 40        # 10240 B，内容可逐字节校验
PREVIEW_BYTES = b'GIF89a' + b'\x00' * 26    # 32 B

_media_dir = tempfile.mkdtemp(prefix='dshpp-asset-media-')
MEDIA_PATH = os.path.join(_media_dir, 'media.mp4')
PREVIEW_PATH = os.path.join(_media_dir, 'preview.gif')

_srv = None
BASE = ''
_orig_scan = None


def setUpModule():
    global _srv, BASE, _orig_scan

    with open(MEDIA_PATH, 'wb') as handle:
        handle.write(MEDIA_BYTES)
    with open(PREVIEW_PATH, 'wb') as handle:
        handle.write(PREVIEW_BYTES)

    # 桩掉扫描：填一张固定路径表，让**真实的** media_file 跑白名单校验与查表
    _orig_scan = we_scanner.scan_inventory
    we_scanner.scan_inventory = lambda use_cache=True: {'wallpapers': [], 'total': 0}
    we_scanner._PATH_INDEX.clear()
    we_scanner._PATH_INDEX[WID] = {'media': MEDIA_PATH, 'preview': PREVIEW_PATH}

    SV.SERVER_TOKEN = 't' * 48
    _srv = ThreadingHTTPServer(('127.0.0.1', 0), SV.Handler)
    BASE = 'http://127.0.0.1:%d' % _srv.server_address[1]
    threading.Thread(target=_srv.serve_forever, daemon=True).start()


def tearDownModule():
    if _srv is not None:
        _srv.shutdown()
        _srv.server_close()   # shutdown() 不关监听 socket，必须显式关闭
    if _orig_scan is not None:
        we_scanner.scan_inventory = _orig_scan
    we_scanner._PATH_INDEX.clear()
    shutil.rmtree(_media_dir, ignore_errors=True)
    shutil.rmtree(_ISO_ROOT, ignore_errors=True)


def get(path, headers=None):
    """返回 (status, headers, body)。303/304 也会被 urllib 当异常抛出，一并接住。"""
    req = urllib.request.Request(BASE + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def url_for(kind):
    return '/api/wallpaper/asset/{0}/{1}'.format(kind, WID)


class ServesBytesWithoutToken(unittest.TestCase):
    """免 token 是设计要求：带令牌才能看图的 `<img>` 是无法工作的。"""

    def test_preview_served_without_any_token(self):
        status, headers, body = get(url_for('preview'))
        self.assertEqual(status, 200)
        self.assertEqual(body, PREVIEW_BYTES)
        self.assertEqual(headers.get('Content-Type'), 'image/gif')

    def test_media_served_without_any_token(self):
        status, headers, body = get(url_for('media'))
        self.assertEqual(status, 200)
        self.assertEqual(body, MEDIA_BYTES)
        self.assertEqual(headers.get('Content-Length'), str(len(MEDIA_BYTES)))

    def test_advertises_range_and_cache_validators(self):
        _, headers, _ = get(url_for('media'))
        self.assertEqual(headers.get('Accept-Ranges'), 'bytes')
        self.assertTrue(headers.get('ETag'), '缺 ETag 会让缩略图每次都重传')
        self.assertIn('max-age', headers.get('Cache-Control', ''))

    def test_unknown_id_is_404(self):
        status, _, _ = get('/api/wallpaper/asset/media/no-such-wallpaper')
        self.assertEqual(status, 404)


class RangeRequests(unittest.TestCase):
    """scene 预览视频需要拖动进度条，Range 必须真能用。"""

    def test_range_returns_206_and_exact_slice(self):
        status, headers, body = get(url_for('media'), {'Range': 'bytes=100-199'})
        self.assertEqual(status, 206)
        self.assertEqual(headers.get('Content-Range'),
                         'bytes 100-199/{0}'.format(len(MEDIA_BYTES)))
        self.assertEqual(body, MEDIA_BYTES[100:200])

    def test_open_ended_range_reads_to_eof(self):
        status, headers, body = get(url_for('media'),
                                    {'Range': 'bytes={0}-'.format(len(MEDIA_BYTES) - 10)})
        self.assertEqual(status, 206)
        self.assertEqual(body, MEDIA_BYTES[-10:])

    def test_suffix_range_returns_tail(self):
        status, _, body = get(url_for('media'), {'Range': 'bytes=-64'})
        self.assertEqual(status, 206)
        self.assertEqual(body, MEDIA_BYTES[-64:])

    def test_range_beyond_eof_is_416(self):
        status, headers, body = get(url_for('media'), {'Range': 'bytes=999999-1000000'})
        self.assertEqual(status, 416)
        self.assertEqual(headers.get('Content-Range'),
                         'bytes */{0}'.format(len(MEDIA_BYTES)))
        self.assertEqual(body, b'', '416 不应带响应体')

    def test_end_beyond_eof_is_clamped_not_416(self):
        """`bytes=0-999999` 是合法请求，服务端应夹到 EOF 而不是报错。"""
        status, _, body = get(url_for('media'), {'Range': 'bytes=0-999999'})
        self.assertEqual(status, 206)
        self.assertEqual(body, MEDIA_BYTES)


class ConditionalRequests(unittest.TestCase):

    def test_if_none_match_hits_304(self):
        _, headers, _ = get(url_for('media'))
        etag = headers.get('ETag')
        status, out_headers, body = get(url_for('media'), {'If-None-Match': etag})
        self.assertEqual(status, 304)
        self.assertEqual(body, b'')
        self.assertEqual(out_headers.get('ETag'), etag)

    def test_stale_etag_still_serves_200(self):
        status, _, body = get(url_for('media'), {'If-None-Match': '"stale-etag"'})
        self.assertEqual(status, 200)
        self.assertEqual(body, MEDIA_BYTES)


class PathTraversalRejected(unittest.TestCase):
    """id 必须过白名单正则 —— 这是免 token 之后唯一的安全边界。"""

    def test_traversal_and_junk_ids_all_404(self):
        probes = [
            '/api/wallpaper/asset/media/..%2F..%2F..%2Fconfig.json',
            '/api/wallpaper/asset/media/....//config.json',
            '/api/wallpaper/asset/media/%2e%2e%2fconfig.json',
            '/api/wallpaper/asset/media/..',
            '/api/wallpaper/asset/media/.',
            '/api/wallpaper/asset/media/a%2Fb',
            '/api/wallpaper/asset/media/C%3A%5CWindows%5Cwin.ini',
        ]
        for probe in probes:
            with self.subTest(probe=probe):
                status, _, _ = get(probe)
                self.assertEqual(status, 404, '必须拒绝 {0}'.format(probe))

    def test_real_config_json_is_never_exposed(self):
        """把真实数据根的 config.json 当攻击目标 —— 它是绝对不该被读到的。"""
        secret = os.path.join(_ISO_ROOT, 'config.json')
        with open(secret, 'w', encoding='utf-8') as handle:
            handle.write('{"secret": "must-not-leak"}')
        for probe in ('/api/wallpaper/asset/media/..%2Fconfig.json',
                      '/api/wallpaper/asset/media/../config.json'):
            status, _, body = get(probe)
            self.assertNotEqual(status, 200)
            self.assertNotIn(b'must-not-leak', body)


class RouteShapeValidation(unittest.TestCase):

    def test_wrong_kind_is_404(self):
        for kind in ('bogus', 'frame', 'thumb', 'MEDIA'):
            with self.subTest(kind=kind):
                status, _, _ = get('/api/wallpaper/asset/{0}/{1}'.format(kind, WID))
                self.assertEqual(status, 404)

    def test_missing_or_extra_segments_are_404(self):
        for path in ('/api/wallpaper/asset/media',
                     '/api/wallpaper/asset/',
                     '/api/wallpaper/asset/media/%s/extra' % WID):
            with self.subTest(path=path):
                status, _, _ = get(path)
                self.assertEqual(status, 404)


class ExemptionDoesNotLeak(unittest.TestCase):
    """asset 免 token，但豁免不能外溢到清单接口。"""

    def test_listing_still_requires_token(self):
        status, _, body = get('/api/wallpaper')
        self.assertEqual(status, 401, '清单接口必须仍然要令牌')
        self.assertNotIn(WID, body.decode('utf-8', 'replace'))

    def test_listing_works_with_token(self):
        status, _, _ = get('/api/wallpaper', {'X-DSHSkin-Token': SV.SERVER_TOKEN})
        self.assertEqual(status, 200)


if __name__ == '__main__':
    unittest.main(verbosity=2)
