# DSH++（dsh-plugin-dshpp）

DSH 桌面的自安装运行时：面板 + 插件/皮肤管理。按用户 2026-09-17 的设计决定，
DSH++ **不**作为裸依赖直接装进 DSH 桌面 profile，而是自带内嵌安装器/卸载器，
出问题时可以一键干净卸载。

## 目录

```
dshpp\
  bundle\            Cordis 插件本体（host 半 lib/index.js + client 半 lib/client.js）
  install.mjs        内嵌安装器（standalone，幂等）
  uninstall.mjs      内嵌卸载器（standalone，幂等，也是 GUI 坏掉时的应急路径）
  installer-core.mjs 两者共享的文件手术逻辑
  snapshots\         每次操作前的状态快照（<时间戳>-install|uninstall\）
  README.md          本文件
```

## 安装机制（dshskin file:// 模式，为什么不是 pnpm 依赖）

2026-09-17 的 P0 直接安装（profile 依赖 + pnpm）被证伪，两个实证：

1. **桌面自己的 boot 期 pnpm 协调会清掉它**：13:55:01 profile 的
   package.json / pnpm-lock.yaml / node_modules 被同一秒整体重写，
   dshpp 与 hindsight 的依赖、lockfile 条目、node_modules 目录全部消失，
   market 日志里没有任何 uninstall 事件——是桌面 boot 流程自己做的。
2. **market UI 处理不了自己没装的包**：对 dshpp 的 toggle 反复报
   "no loader entry matched"。

因此改用 dshskin 已生产验证的模式（本机 4 条 dshskin 行长期稳定存活，
且 13:55:01 的清理**没有**触碰 cordis.patch.yml）：

- bundle 复制到 `D:\DATA\DSHdata\.dsh-skins\plugins\dsh-plugin-dshpp\<version>\`
- 桌面 profile 的 `cordis.patch.yml` 写入受管块（file:// 行）：

  ```yaml
  # >>> dshpp:plugins (managed, do not edit inside) >>>
  - insert:
      - id: dsh-plugin-dshpp
        name: 'file:///D:/DATA/DSHdata/.dsh-skins/plugins/dsh-plugin-dshpp/0.1.0/lib/index.js'
  # <<< dshpp:plugins <<<
  ```

- `registry.json`（DSH++ A-track 视图）登记条目。

file:// 行同时喂两个半：host 半由 Loader 加载（`inject: ['webServer']`
硬依赖，等 webServer 就绪后才注册路由——P0 探针曾因软守卫竞态丢掉全部
路由）；client 半由 bundle package.json 的 `dsh.client` 声明进入浏览器
boot graph（与 dshskin 行同一机制）。

零 pnpm、零 lockfile、零 node_modules、零 market 账本。

## 操作

```powershell
# 安装（幂等，可重复执行）
node D:\DSH\DSH++\dshpp\install.mjs

# 卸载（幂等；只动 DSH++ 自己写的文件）
node D:\DSH\DSH++\dshpp\uninstall.mjs
```

可选参数：`--profile <dir>`（默认 `D:\DATA\DSHdata\.dsh\profiles\desktop`）、
`--skin-root <dir>`（默认 `D:\DATA\DSHdata\.dsh-skins\plugins`）。

**生效需要重启 DSH Desktop**（打包版不监听 profile patch 文件，
`patchReload: live` 在打包 host 无 watch 接线；实测 2026-09-17 14:52
写入后 live tree 无变化、日志无 reload 事件）。两个脚本都**从不**
启动或重启 DSH Desktop——重启只能由用户自己发起。

## 验证

重启后（GUI 浏览器内，web 端口有 403 cookie 栅栏，外部请求一律 403）：

- 浮动按钮「DSH++」→ 打开 `/dshpp` 控制台（HOST ALIVE = 成功）
- `/dshpp/health`、`/dshpp/capabilities`、`/dshpp/tree`
- Loader tree 中应有 `dsh-plugin-dshpp` 行（fiber active）

## 硬约束

- 永不自动启动/自愈 DSH Desktop。
- 卸载只动 DSH++ 自己写的三处：受管 patch 块、skin 树 bundle 副本、
  registry A-track 条目。不碰 pnpm 状态、market 状态、dshskin 受管块、
  其他 profile。
