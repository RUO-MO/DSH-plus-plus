#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""启动链健壮性回归测试

覆盖 2026-09-20 修的两个首屏卡死缺陷（表现为「一直卡在 DSH++ 启动中…」）：

  1. 启动遮罩可能永久不退场
     历史写法把兜底 setTimeout 排在 `await Promise.allSettled(...)` 之后，
     而 Promise.allSettled 处理不了「永不 settle」的 promise —— 一旦某个预取
     挂起（keep-alive 连接被回收 / WebView2 网络栈异常 / 后端正重启），
     await 永不返回，兜底计时器根本执行不到 → 无限期白屏。

  2. token-usage 冷缓存阻塞 + warming 标志失效
     _token_usage_warmup() 里赋值 _TOKEN_USAGE_WARMING 却没写 `global`，
     导致模块级标志恒为 False；同时冷缓存请求会再做一次全量会话语料扫描。

静态断言 + 语义等价运行（Node）双重验证，不需要真实浏览器。
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANEL = os.path.join(ROOT, 'panel.html')
NODE = shutil.which('node') or r'C:\Users\mr\.workbuddy\binaries\node\versions\22.22.2-3\node.exe'


def _read(path):
    with io.open(path, encoding='utf-8') as f:
        return f.read()


def _panel_src():
    return _read(PANEL)


def _init_block(src):
    """截出「=== 初始化 ===」那个 IIFE 的正文。"""
    m = re.search(r'// === 初始化 ===(.*?)\n  \}\)\(\);', src, re.S)
    return m.group(1) if m else ''


class BootMaskNeverHangs(unittest.TestCase):
    def setUp(self):
        self.src = _panel_src()
        self.init = _init_block(self.src)
        self.assertTrue(self.init, '未找到 panel.html 的初始化段')

    def test_with_timeout_helper_exists(self):
        self.assertIn('function withTimeout(', self.src)

    def test_hard_timer_registered_before_await(self):
        """兜底计时器必须先于 await 注册 —— 这是原 bug 的核心。"""
        i_hard = self.init.find('setTimeout(dismissBoot, BOOT_HARD_MS)')
        i_await = self.init.find('await Promise.race')
        self.assertGreaterEqual(i_hard, 0, '未找到硬上限定时器')
        self.assertGreaterEqual(i_await, 0, '未找到 await Promise.race')
        self.assertLess(i_hard, i_await,
                        '硬上限定时器必须排在 await 之前，否则卡住时永不执行')

    def test_each_prefetch_has_timeout(self):
        """四项预取都要包 withTimeout，否则单项挂起仍会拖垮首屏。

        注意断言方式：bootSteps 是「声明四行、单点包裹」的写法 —— 四项预取
        在 bootSteps.map(...) 回调里统一过 withTimeout，字面量只出现一次。
        所以不能数 withTimeout 的出现次数（那是 1），而要断言：
          a) bootSteps 至少 4 项（少写一项 = 少一道超时保护）
          b) map 回调确实调了 withTimeout 包裹每项
        """
        m = re.search(r'const bootSteps\s*=\s*\[(.*?)\];', self.init, re.S)
        self.assertIsNotNone(m, '未找到 bootSteps 预取清单')
        rows = re.findall(r"\[\s*'[^']*'\s*,", m.group(1))
        self.assertGreaterEqual(len(rows), 4,
                                '启动预取应至少 4 项，实际 %d 项' % len(rows))
        self.assertIn('.map(', self.init, 'bootSteps 应经 .map 统一包裹')
        self.assertIn('withTimeout(p, BOOT_BUDGET_MS', self.init,
                      'map 回调须用 withTimeout 包裹每项预取')

    def test_race_with_budget(self):
        self.assertIn('Promise.race', self.init)
        self.assertIn('BOOT_BUDGET_MS', self.init)

    def test_failed_prefetch_is_logged_not_silent(self):
        self.assertIn("启动预取部分失败", self.init)

    def test_semantic_stall_still_dismisses(self):
        """语义等价复现：某个预取永不 settle 时，遮罩仍须退场。"""
        if not NODE or not os.path.isfile(NODE):
            self.skipTest('node 不可用，跳过语义验证')
        script = r'''
function withTimeout(p, ms, label) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error((label||'请求')+'超时')), ms);
    Promise.resolve(p).then(v => { clearTimeout(t); resolve(v); },
                           e => { clearTimeout(t); reject(e); });
  });
}
const BOOT_BUDGET_MS = 200, BOOT_HARD_MS = 600;
let dismissed = 0; const t0 = Date.now();
const dismissBoot = () => { dismissed++; };
setTimeout(dismissBoot, BOOT_HARD_MS);
const never = new Promise(() => {});
const prefetch = Promise.allSettled([
  withTimeout(Promise.resolve(1), BOOT_BUDGET_MS, 'a'),
  withTimeout(never, BOOT_BUDGET_MS, 'stalled'),
]);
(async () => {
  await Promise.race([prefetch, new Promise(r => setTimeout(r, BOOT_BUDGET_MS + 100))]);
  dismissBoot();
  console.log(dismissed >= 1 && (Date.now() - t0) < 1500 ? 'PASS' : 'FAIL');
})();
'''
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False,
                                         encoding='utf-8', dir=ROOT) as f:
            f.write(script)
            tmp = f.name
        try:
            out = subprocess.run([NODE, tmp], capture_output=True, text=True, timeout=30)
            self.assertIn('PASS', out.stdout, '卡死路径未能退场: %s%s' % (out.stdout, out.stderr))
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass


class TokenUsageWarmFlag(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, ROOT)
        import server
        self.server = server
        self._saved = dict(server._TOKEN_USAGE_CACHE)
        self._saved_warm = server._TOKEN_USAGE_WARMING

    def tearDown(self):
        s = self.server
        s._TOKEN_USAGE_CACHE.update(self._saved)
        s._TOKEN_USAGE_WARMING = self._saved_warm

    def test_warmup_declares_global(self):
        """_token_usage_warmup 必须声明 global，否则模块级 warming 永不更新。"""
        src = _read(os.path.join(ROOT, 'server.py'))
        m = re.search(r'def _token_usage_warmup\(\):(.*?)\ndef ', src, re.S)
        self.assertIsNotNone(m, '未找到 _token_usage_warmup')
        self.assertIn('global _TOKEN_USAGE_WARMING', m.group(1),
                      '缺少 global 声明 → warming 标志失效')

    def test_cold_cache_with_warmup_does_not_block(self):
        """冷缓存 + 预热进行中：应立即返回 pending 占位，不做全量扫描。"""
        import time
        s = self.server
        s._TOKEN_USAGE_CACHE.update({'key': None, 'value': None, 'ts': 0.0})
        s._TOKEN_USAGE_WARMING = True
        t = time.time()
        v = s._token_usage_payload()
        dt = time.time() - t
        self.assertLess(dt, 0.5, '冷缓存请求发生阻塞：%.2fs' % dt)
        self.assertTrue(v.get('pending'), '应带 pending 标记供前端继续轮询')
        self.assertTrue(v.get('warming'))

    def test_blocking_true_preserves_sync_semantics(self):
        """显式 blocking=True 时保留旧的同步语义（CLI / 测试索取最终值用）。"""
        s = self.server
        s._TOKEN_USAGE_CACHE.update({'key': None, 'value': None, 'ts': 0.0})
        s._TOKEN_USAGE_WARMING = False
        v = s._token_usage_payload(blocking=True)
        self.assertFalse(v.get('pending'))
        # 必须包含聚合器的全部字段（前端 tuStatsFromHost 依赖它们）
        for k in ('totalTokens', 'turns', 'days', 'hours', 'models'):
            self.assertIn(k, v, '阻塞路径缺少字段 %s' % k)

    def test_pending_payload_shape_matches_contract(self):
        """pending 占位也必须带全字段，避免前端解构 undefined。"""
        s = self.server
        s._TOKEN_USAGE_CACHE.update({'key': None, 'value': None, 'ts': 0.0})
        s._TOKEN_USAGE_WARMING = True
        v = s._token_usage_payload()
        for k in ('totalTokens', 'turns', 'days', 'hours', 'models',
                  'dayModels', 'modelTokens', 'hoursToday'):
            self.assertIn(k, v, 'pending 占位缺少字段 %s' % k)


class PanelPendingHandling(unittest.TestCase):
    def test_panel_honours_pending_flag(self):
        src = _panel_src()
        m = re.search(r'async function loadTokenUsage\(\)(.*?)\n  \}', src, re.S)
        self.assertIsNotNone(m, '未找到 loadTokenUsage')
        body = m.group(1)
        self.assertIn('data.pending', body, '前端未处理 pending 占位')
        self.assertIn('startTuPoll', body, '遇到 pending 时必须启动轮询')


if __name__ == '__main__':
    unittest.main(verbosity=2)
