# OUTPUT_BRIDGE.md — Electron NativeImage 双路径输出桥契约（#113 ELEC-02）

> 范围警告：本件是 #113 实验桥的规格，不是 Alpha 导航适配的验收结论。
> 文中 `tests/test_elec_output_bridge.py` 在本检出不存在，不能据此宣称跨语言门已过。
> 邻行恢复/页面 originals 路径不满足 Alpha 规则，专用适配不调用本桥；
> 共享助手输出在本棒未测且明确不支持。已验证的原生 PNG 路线见 [README](README.md)。
>
> 权威规格（issue #113「本轮就绪审计」要求冻结的调用边界）。实现面：
> `output_bridge.mjs`（Node/Electron 侧）+ `native/`（原生编码助手
> `vtx_elec_output_helper`，复用 #111 `gfx_cef_worker/src/pixel_ops|png_writer`
> 源码只读编译）。协议常量镜像 = `native/src/elec_output_protocol.hpp`（偏移
> 静态断言）；跨语言锁定 = `tests/test_elec_output_bridge.py`。
> canary 判据唯一 home = `vtx/frame_canary.py`（v2 双像素；本桥三端口
> Python/JS/C++ 由测试穷举对拍，公式常数禁改）。

## 0. 同步模型总述

#110 环内用 `SlotGate` CAS（单写者/单读者 mmap 原子）；Electron 侧 Node 无
跨进程 mmap 原子写承诺，因此本桥的**权威流控是 socket credit**：助手每
落盘/拒绝一帧即回一条 `Credit{slot}`，Node 侧 `MAX_INFLIGHT_FRAMES = 4`
（= 环槽数，工单冻结值，`OutputBridge` 构造器拒绝其它槽数）内才有资格提交
下一帧——在途未落盘帧 > 4 时 `writeFrameBitmap` 在 `_acquireCredit()` 上
背压等待（不分配新内存，等待时长计入 `metrics.backpressureWaitMsTotal`）。
环内 `SlotGate.state`（EMPTY/WRITING/READY）与 `released_total` 仅作诊断面
（Node 以 pwrite 发布、助手以 release-store 释放），与 credit 并行维护；
两者偏差不构成流控错误，但计入证据。

## 1. 缓冲所有权与 stride（冻结）

| 面 | 所有者 | 生命周期 | stride |
| --- | --- | --- | --- |
| `NativeImage`（capturePage 产物） | Electron 主进程 | 调用方持有；桥只读 | 引擎内部 |
| `image.toBitmap()` 返回 Buffer | 桥（临时副本） | pwrite 进 SHM 后即可弃 | 紧凑 `width*4` |
| SHM 槽数据 | Node 写 → 助手读 | credit 回收后复用 | 紧凑 `width*4`（槽头 `stride` 字段为准，助手按行拷贝防御任意 stride） |
| 助手内部 `bgra`/`rgba`/`png` 向量 | 助手 | 单请求处理期 | — |
| 落盘 `.tmp.<pid>.<tid>` | 助手 | `rename` 后消失 | — |

**`toBitmap()` 返回像素副本：修改副本不等于修改原 NativeImage**（工单红队
点）。通路 2 因此必须 `nativeImage.createFromBitmap(恢复后像素)` 明确重建并
`toPNG()` 重建实例（`encodeTopngPath2` 从不编码传入的旧 image，重建后回读
前 8 字节自证 `rebuildEchoOk`）。通路 1 只把副本当只读像素源，无此歧义。

## 2. 像素/色彩约定（冻结）

- 槽数据 = **BGRA、行主序、预乘（BGRA_PREMUL）**、sRGB（`force-color-profile=srgb`）。
- 助手流水线：v2 双像素 canary 验证（(0,0)=P1、(0,1)=P2，`tol1` 档默认开）
  → 双像素恢复（宿主上报 `orig1/orig2` 优先；缺省行 1 邻色
  `(1,0)/(1,1)`——**仅当 fixture 证明顶行与行 1 等值时精确**，基准页
  `pages/bench_frame.html` 是该 fixture；生产通路应上报原像素）→
  `unpremultiply_bgra_to_rgba`（#111 AVX2/NEON/标量 LUT 算子，回执报后端）→
  RGBA8 PNG（spng level∈1..3 × filter / fpng / zlib）→ `.tmp+rename` 原子落盘。
- 恢复语义：canary 拒绝（陈旧/无签名/不相容/歧义/孪生防御）**绝不落盘、
  绝不恢复**，回执 `status=canary_reject` + `reason` 细粒度归因；接受才恢复
  并编码。归因取自解码结果（`rendered_frame`），绝不转录请求帧号。

## 3. 错误返回（冻结）

`Done.status_code`（`OUT_STATUS`）：`0 ok`｜`1 canary_reject`｜
`2 header_invalid`（槽头 magic/版本/run_id/几何不符）｜`3 encode_error`｜
`4 io_error`（原子落盘失败）｜`5 bad_request`（字段非法）。Node 侧对应
`reject(err)`，`err.code` = 状态名、`err.receipt` = 原始回执（canary 拒绝
也携带计时/归因，供上层显形）。助手死亡/断连/协议断裂 → 桥进入 dead 态，
所有在途 Promise 拒绝、后续写入拒绝（严格实验禁回退：错误不掩盖）。

## 4. 队列容量与背压（冻结）

- `MAX_INFLIGHT_FRAMES = 4`（通路 1 = credit 界；通路 2 `DiskCommitPool`
  同值）。两路独立计数，禁止混算指标。
- 通路 2 的 `toPNG()` 是**主线程同步编码**：`tPngEncodeMs` 如实计入主线程
  成本（工单口径「worker_threads 只搬写盘」）；worker 仅 `.tmp+rename`。

## 5. 退出回收（冻结）

- SHM：Node 以 `/dev/shm/vtx_elec_out_*` tmpfs 文件创建；助手 `open` 成功后
  **立即 unlink**——实体仅由双方 fd/mmap 维系，任一方死亡（含 SIGKILL）由
  内核回收，`/dev/shm` 零 `vtx_elec_out_*` 残留。
- Unix socket / 运行目录：`OutputBridge.stop()` 关 server、unlink socket、
  等助手排空退出（超时 SIGKILL 兜底）、`rm -rf` 临时运行目录。
- 助手：socket EOF = 优雅关闭（排空在途后 exit 0）；空闲超时（默认 120s）
  exit 5；协议错误 exit 4。
- Node 侧持有 `shmFd` 至 stop；进程退出前未 stop 由内核 tmpfs 回收兜底。

## 6. 消息布局（小端；`elec_output_protocol.hpp` 静态断言钉死）

| 消息 | 大小 | 方向 | 要点 |
| --- | --- | --- | --- |
| Hello | 64B | 助手→Node | pid/slots/几何/shm_bytes/run_id/unpremul_backend；Node 全字段核验收口 |
| Credit | 16B | 助手→Node | slot + seq（诊断）；权威流控 |
| Request | 296B | Node→助手 | id/slot/frame/几何/run_id/encoder(u8)/level/filter(u8)/tol1/orig 像素/计时透传/`out_path[224]`（≤223B+NUL） |
| Done | 144B | 助手→Node | status/reason/归因/分段计时（decode/restore/alpha_norm/png_encode/disk_commit）/png_bytes/crc32 |

环布局 = #110 `shm_ring.hpp` 同构：`[RingControl 384B][Slot×4]`、
`Slot = [FrameSlotHeader 128B][frame_bytes]`、`slot_stride = align64(128+frame_bytes)`；
`buildSlotHeader`（Node 镜像）↔ 助手 `--check-header` 跨语言锁定。

## 7. 构建与产物

```bash
bash experiments/gfx_electron_worker/native/build_output_helper.sh
# → experiments/gfx_electron_worker/native/build/bin/vtx_elec_output_helper
#   （含 --selftest 全域穷举、--bench 编码器选型基准、--check-header）
```

零网络、零 CEF 依赖；`gfx_cef_worker/` 源码**只读引用**（并行 #114 不受
影响）。`native/build/` 不入库（本目录 `.gitignore`）。

## 8. 用法

```js
// 通路 1（bitmap_shared_encoder）
const bridge = new OutputBridge({ encoder: 'spng', level: 1, filter: 'sub_up' });
await bridge.start();
const receipt = await bridge.writeFrameFromImage(image, { frame: f, outPath });
await bridge.stop();

// 通路 2（electron_native_topng）
const pool = new DiskCommitPool();
const r = await encodeTopngPath2(image, { frame: f, outPath, pool });
await pool.stop();
```

基准/证据入口：`bench_output_bridge.mjs`（真实 Electron OSR + 本页
`pages/bench_frame.html`，1000 帧 1080p 双路独立跑）；纯 Node 冒烟 =
`selfcheck_bridge.mjs`（`tests/test_elec_output_bridge.py` 消费）。
