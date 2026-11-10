# dsh-image-isolation

DSH 插件（cordis service）：用**进程隔离的图像工作子进程**替换官方
`@deepseek-ai/dsh-attachment-local` 的图像处理路径。sharp/libvips 只在 fork 子进程
（常驻池，每 worker 同时一任务，空闲复用、故障即换新）里加载；子进程 native 崩溃
（SIGSEGV 等）只杀死该子进程，宿主进程继续运行，受影响的那个操作干净失败
（绝不自动重试），下一个操作使用全新 worker。

## 动机与边界（如实说明）

- 本机曾观测到宿主进程在 libvips（`vips_sink_disc`）内 SIGSEGV。本插件**没有复现该特定
  崩溃**；测试证明的是 **containment**：native 图像工作被限制在子进程内，父进程
  `/proc/self/maps` 始终无 libvips/sharp（有测试断言）。
- 只覆盖图像入口：`validateImage` / `saveImage` / `saveImages` / `readImage` /
  `readImageRequest`。文件入口（`saveFile`/`saveFileStream`/`readFileStream` 等）零
  sharp，直接继承官方实现。
- **未覆盖官方未来新增的图像入口**：本插件 pin 了 peer 依赖版本（0.2.1-rc.2）。升级
  DSH 前请先核对官方 store 是否新增图像 API；如有，先更新本插件再升级。
- core dump 治理：本插件只负责不把 native 崩溃带进宿主；如需限制 core 大小，可在
  systemd 单元加 `LimitCORE=0`（文档建议，本插件不改任何服务配置）。
- **不隔离 cgroup OOM**：worker 子进程与宿主同属一个 cgroup，系统级内存压力仍可能
  触发 OOM killer 杀死宿主或子进程；本插件只隔离 native 崩溃（SIGSEGV 等），不改变
  内存总量约束。

## 结构

- `src/index.mjs` — `IsolatedAttachmentStore extends LocalAttachmentStore`。Config 继承
  官方全部字段与默认值，另加 `workerTimeoutMs`（1000–300000，默认 30000）与
  `workerMaxQueue`（0–1024，默认 64）。池大小 = `imageCompressionConcurrency`
  （官方默认 2，上限 8）。Config 由宿主注入 `dshHome`（官方 schema 无 default，直连
  构造必须显式提供）；本插件对直连 `new IsolatedAttachmentStore(ctx, config)` 的
  config 不再二次兜底——请走 `IsolatedAttachmentStore.Config(...)` resolved 值或提供
  等价完整配置。
- `src/protocol.mjs` — 父子两侧共享的 wire 协议助手（防两侧算法漂移）：字节预算
  常量、`auditWireBytes`（fail-loud 审计）、`checkTransferBounds`、错误信封校验
  `validateWireError`、按 op 的结果 schema `validateOpResult`。
- `src/pool.mjs` — `WorkerPool`：`child_process.fork`（非 worker_threads），
  `serialization: 'advanced'`，env 最小白名单（TMPDIR/TZ/LANG/LC_ALL + 标记变量），
  `execArgv: []`。每 worker 同时只跑一个任务，状态机 `busy → idle` 复用、
  `busy/idle → closing → closed` 终结：**容量只在子进程真实 `close` 事件后释放**，
  被杀 worker 在退出被观测前一直占槽、绝不从注册表消失；dispose 只在所有 child
  真实 close 后 resolve（**没有 5s 宽限冒充回收**），且宿主 dispose 回调返回该
  Promise。任务失败不重试；超时/abort/协议违规 → SIGKILL，job 在真实 close 时按
  挂起原因 settle 且仅一次。子进程 `error` 事件（send/kill/spawn 失败）**不等价于
  退出**：只记录错误名安全事实并 beginClose，终态仍只认 `close`。队列满
  （busy+closing 满 且 queue ≥ maxQueue）直接拒绝；单字段 256MiB 传输预算之外另有
  **全队列总 pending 字节预算**；worker 回复在 resolve 前完整校验——形状/id/ok/
  错误信封（code 必须 UPPER_SNAKE ≤64、message ≤4096）/结果总字节/**按 op 的严格
  结果 schema**（`validate` 恒 `{}`；`prepare`/`readImage` 恰 `{data,ref}` 且 data
  必须是 `Uint8Array`（Buffer 是其子类故通过）、ref 为 `sha256:` 摘要/尺寸/bytes
  正整数且 `ref.bytes === data.byteLength`；`requestImage` 恰 10 键且各字段类型
  匹配——`depth` 为字符串如 `"uchar"`、data 为 Uint8Array 且 `bytes ===
  data.byteLength`），违例杀 worker 拒 job 且不毒化池。字节审计（两侧共享
  `protocol.mjs` 的 `auditWireBytes`）为 fail-loud：字符串计 UTF-8、真实 typed view
  计真长度、深度 >6 或宽度 >64 键或非法声明返回 NaN 拒绝；**原生隐藏容器
  （Map/Set/WeakMap/WeakSet/RegExp/Date/NativeError/装箱原始值）按内部槽位判定、
  一律 NaN 拒绝**——其载荷不在自有可枚举键里，改原型也绕不过 `util.types` 的内部
  槽位判断；**plain object 的声明 `byteLength` 只贡献自身数值，同对象的其他兄弟字段
  照常累计**——`{byteLength: 0, data: 巨大buffer}` 无法绕过预算。排队中 abort：移出队列、退还字节预算、以调用方 reason 拒绝，绝不发
  IPC；入口预 abort 同样直接拒绝；dequeue→start 交接时移除排队期 abort 监听，调用
  方 signal 在成功/失败/中止后不残留监听。worker stdout/stderr 持续排空并丢弃，
  内容不进日志不进错误。构造函数校验全部选项及上界（concurrency ≤ 8、
  timeoutMs ≤ 300000、maxQueue ≤ 1024，TypeError；warn 必须为函数，其抛错被吞且
  不影响生命周期）。
- `src/worker.mjs` — IPC 协议 `{id,op,payload} → {id,ok,result}|{id,ok:false,error:{code,message}}`。
  ops：`validate` / `prepare` / `readImage` / `requestImage`，内部直接调用官方
  `validateImageFile` / `prepareImageFile` / `readImageFile` / `readRequestImageFile`。
  `AttachmentError` 的 code/message 原样透传（message 截 4096）；其余异常折叠为只含
  Error 名称的安全消息（无堆栈、无路径、无 stderr 内容）。入口与出口双向字节预算
  （共享 `protocol.mjs` 实现）：payload 字段发送前检查，result 发送前按
  `auditWireBytes` 汇总（字符串计 UTF-8、深度/宽度超限即拒），超限以拒绝 envelope
  回复（只含字节数事实）。所有 `process.send` 均有守卫：channel 断开不产生
  unhandled rejection；worker 监听 IPC `disconnect`，父进程硬退出时自终止
  （exit 75），不会在 native 长任务里变孤儿。

### 语义要点

- `saveImage`：子进程 `prepare`（纯内存解码/规范化 + digest，官方语义原样），父进程
  用官方 `commitPreparedImageFile` 提交（重新校验 digest）。
- `saveImages`：先整体 `validateImageBatch`，再全部 `prepare` 成功，最后按序 commit——
  validation/prepare 阶段任何一个失败则不产生任何新引用（无部分提交）。commit 阶段
  若存储失败，与官方一致：可能留下不可达对象，但**不返回部分引用**——这不是磁盘
  事务，语义与官方 `saveImages` 相同。
- `readImageRequest`：变体 id（`requestImageVariantId`）在父进程计算，缓存写入官方
  `dshCachePath(dshHome,'attachments')`，与官方缓存布局兼容；并发同变体请求共享一次
  worker 操作，单生命周期语义：单个 waiter abort 只拒自己并移除监听，最后一个
  waiter 离开才取消底层操作；**已取消的共享操作绝不让新 caller 加入**（同官方
  `controller.signal.aborted → 重建`）；settle 后晚到 joiner 直接取已存结果，不挂
  死监听。
- 错误：worker native 崩溃只上报 exit/signal 事实（worker id、退出码/信号名、op、
  字节数），不携带图像数据、原始输出或路径。

## 测试

```bash
npm test        # node --test tests/isolation.test.mjs tests/patch.test.mjs，43 个用例
```

覆盖：不透明/透明 PNG 往返（alpha 保真）、JPEG 准入、坏图拒绝、超尺寸拒绝、batch
一坏零提交 / 全好按序、request 变体稳定性与复用、busy worker 被 SIGKILL 只失败当前
任务且下一个任务成功、超时、abort（抛调用方 reason，含排队中 abort 不发 IPC）、
dispose 拒绝并等待真实退出、队列饱和拒绝、共享请求单 waiter abort、
TOO_MANY_IMAGES、超传输预算在 IPC 前拒绝、父进程 `/proc/self/maps` 无 libvips/sharp；
故障注入（`tests/fixtures/evil-worker.mjs`，小 payload）：wire 噪声/畸形回复被忽略或
杀 worker、超预算 result 被拒且不毒化池、**schema 违例的成功回复（`{}`）被拒且不毒
化池**、**畸形错误信封（坏 code 形状/超长 message）杀 worker**、全队列字节预算拒绝、
构造参数校验与上界 TypeError、抛错的 warn 不影响生命周期、SharedRequest 单生命周期
与 B1 回归（同变体在末 waiter 取消后的新请求必须得到全新操作）；协议审计单元
（UTF-8 字符串计数、深度 9 声明 768MiB → NaN 而非 0、负声明 NaN、>64 键 NaN）；
**合成 child `error` 事件**（idle 与 busy 两种）只触发 beginClose、终态仍等真实
`close`、池可续用；**调用方 signal 监听清零回归**（成功/失败/中止/排队中止四路径
`getEventListeners(signal,'abort') === 0`）；**`readImageRequest` 预 abort 不触发任何
worker run**；**worker 在 IPC disconnect 后自终止（exit 75）**；`requestImage` 真实
形状过严格 schema（Uint8Array data、字符串 depth）并含篡改负例；声明 `byteLength`
不能掩盖兄弟字段或绕过容器宽度限制；结果元数据（`ref.bytes`/`bytes`）必须与
`data.byteLength` 一致且拒绝非 Uint8Array 视图（如 Uint16Array）；隐藏原生容器
（Map/Set/RegExp/Error/Date/装箱值，含嵌套与换原型伪装）审计 NaN 拒绝、正常容器
（plain/null-proto 对象、数组、Uint8Array、裸 ArrayBuffer）不回归；真实 `ctx.plugin` 服务代理调用全部五个图像 API，并通过 `await fork.dispose()` 验证 worker 已退出（断言前不手动清池）。内部方法不使用与 Cordis 服务代理不兼容的 JS 私有品牌；清理通过 fiber effect disposer 注册，而非不会触发的 `dispose` 事件。

测试固定件全部为 8x8/16x16 小图、池并发 ≤2；`tests/fixtures/silent-worker.mjs` 为
测试专用不回话 worker，用于确定性测试超时/取消/回收路径。

## 安装（维护窗口内操作，勿在运行中的会话宿主上执行）

源码在仓内 `integrations/dsh-image-isolation/`，尚未发布 npm。启用思路：

1. 将本目录打包/链接安装进 web profile（`~/.dsh/profiles/web/`），peer 依赖
   cordis 4.0.5、@deepseek-ai/dsh-attachment(-local) 0.2.1-rc.2、dsh-home-paths、
   schemastery 3.18.5 由宿主环境提供。
2. 在 `~/.dsh/profiles/web/cordis.patch.yml` 追加两行 patch（该文件是 loader 的
   **patch 列表**：`id` 定位的覆盖/禁用行 + 无 `id` 的 `insert` 追加行；`name` 在
   覆盖行中是守卫，不匹配则整行跳过。`tests/patch.test.mjs` 用本机真实 loader
   `applyEntryPatches` + 真实 dsh-base 条目表组合验证过下例）：

   <!-- wiring-patch-begin -->
   ```yaml
   # 禁用官方本地图像处理行（name 守卫防止误伤其它条目）
   - id: attachment-local
     name: '@deepseek-ai/dsh-attachment-local'
     disabled: true

   # 顶层插入本插件（name 支持绝对/相对路径，loader 自动转 file: URL）
   - insert:
       - id: image-isolation
         name: dsh-image-isolation        # 或本地绝对路径
         config:
           workerTimeoutMs: 30000
           workerMaxQueue: 64
   ```
   <!-- wiring-patch-end -->

3. 重启宿主（需要在独立外部终端/维护窗口执行，会中断当前会话）。

## 回退

从 `cordis.patch.yml` 删除上述改动（恢复 `attachment-local` 行、移除 insert），重启宿主。
附件数据、缓存布局均与官方一致，切换不产生数据迁移。

## 版本与升级

- `engines.dsh` 与 peer 依赖均 pin `0.2.1-rc.2`。升级 DSH 时必须重新核对官方
  `LocalAttachmentStore` 的图像 API 面；未核对前不要带病升级。
