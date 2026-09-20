// TraeSkin 用户脚本示例 · 最简
//
// 用法：把本文件复制到 %USERPROFILE%\.trae-skins\enhance\ 下，
//       然后在面板「增强器」里打开总开关 + 用户脚本模块，点「应用增强（CDP）」。
// 前提：TraeWork 以调试模式启动（调试模式启动TraeWork.bat）。

TraeSkin.ready(function () {
  TraeSkin.log('hello.js 已加载，运行时 v' + TraeSkin.__v);
  TraeSkin.toast('TraeSkin 增强已生效');
});
