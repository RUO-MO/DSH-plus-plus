// TraeSkin 用户脚本示例 · 监听界面元素 & 自定义快捷键
//
// 演示：MutationObserver 封装（TraeSkin.on）、定时器、存储、键盘快捷键。
// 这里用的 .solo-lite-chat-panel-container 是已实机核实的稳定锚点；
// 换 TraeWork 版本后可用 `python trae-skin.py probe` 重新确认。

TraeSkin.ready(function () {

  // 1) 等主面板出现后做点事（元素可能晚于脚本就绪）
  TraeSkin.on('.solo-lite-chat-panel-container', function (panel) {
    TraeSkin.log('主面板已就绪', panel.className.slice(0, 60));
    panel.setAttribute('data-traeskin-bound', '1');
  });

  // 2) 自定义快捷键：Alt + K 弹一个提示（示例用途，可改成任何逻辑）
  TraeSkin.ready(function () {
    document.addEventListener('keydown', function (e) {
      if (e.altKey && !e.ctrlKey && (e.key === 'k' || e.key === 'K')) {
        var n = (TraeSkin.storage.get('hits', 0) || 0) + 1;
        TraeSkin.storage.set('hits', n);
        TraeSkin.toast('你按了 Alt+K（第 ' + n + ' 次）');
      }
    });
  });

  // 3) 低频心跳：可用于轮询状态，页面卸载自动清理
  TraeSkin.interval(function () {
    // 示例：把当前时间写进页面标题后的隐藏标记（无害）
    document.documentElement.setAttribute('data-traeskin-alive', String(Date.now()));
  }, 15000);

  TraeSkin.log('示例脚本 02 初始化完成');
});
