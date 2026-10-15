# vtx_cef_worker 帧服务定长二进制 IPC 协议（#110 CEF-02a）

权威实现：`src/ipc_protocol.hpp`（消息/常量 + 偏移静态断言）、
`src/shm_ring.hpp`（环布局）、`src/serve.cpp`（worker 侧）、
`cef_client.py`（Python 消费端镜像）。测试
`tests/test_cef_worker_shm.py::NativeRingSelfTest` 以
`shm_ring_selftest --dump-ring` 跨语言锁定本表。

## §0 与工单原文的偏差记录（红队必读）

1. **回执 128B 而非 64B**：工单冻结的 `FrameSlotHeader` 字段集共 88 字节
   （`alignas(64)` 后结构体 128B），无法装入「64 字节回执」。本实现以
   **冻结字段集为准**：模式 1 回执 = 完整 `FrameSlotHeader`（128B 原生
   布局）。64B 定长仅 `FrameRequest`（可达）。
2. **`vtx/cef_client.py` 改道**：工单落点 `vtx/` 属生产面，#110 边界
   禁改（本单仍属 experiments 面），消费端随 worker 同目录交付为
   `experiments/gfx_cef_worker/cef_client.py`；接线 `vtx/` 留待后续工单。
3. **槽位状态机位置**：冻结字段集无空闲字承载生命周期状态，EMPTY/
   WRITING/READY 门置于环控制块 `SlotGate`（专享缓存行），槽头保持
   工单原样作帧描述/计时面。
4. **逆预乘为 AVX2 LUT gather**（256×256 精确表 + 不透明快路径），
   与 #109 标量公式逐值一致由 selftest 穷举 (c,a) 全组合保证
   （libspng/fpng 未引入——PNG 仍走 #109 zlib 编码器，K1 帧耗时优化
   属下游工单）。
   **#111 更新**：已升级为 `src/pixel_ops.{h,cpp}` 正式算子
   `unpremultiply_bgra_to_rgba`（65,536 项 LUT + AVX2 单遍混合式 +
   NEON 门控 + 标量兜底，全域穷举零失配）+ libspng/fpng 双编码后端
   （见下方 #111 偏差项）；模式 1 落盘编码默认 spng(L1, SUB|UP)。
5. **#111 libspng v0.7.3 编码 fmt**：工单未指定 fmt；v0.7.3 的
   `spng_encode_image` 仅接受 `SPNG_FMT_PNG`/`SPNG_FMT_RAW`（无
   `SPNG_FMT_RGBA8` 编码支持）。8-bit RGBA 输入布局与 `SPNG_FMT_PNG`
   逐字节一致（行内像素字节、无滤波字节），滤波由 `SPNG_FILTER_CHOICE`
   选项驱动——工单语义（SUB|UP + level 1..3）完整达成。
6. **#111 `--png-filter` 扩展参数**：工单 CLI 面仅定义
   `--png-encoder`/`--png-level`；实测 v0.7.3 的 SUB|UP 行滤波选择使
   编码耗时 3-14×于禁滤波（与工单 DoD「单帧 ≤8ms / 4 线程 ≥300fps」
   在渐变/噪声类内容上冲突，数字见 README「双 PNG 编码后端」），故增
   `--png-filter=none|sub|up|sub_up|all`（默认 `sub_up`=工单口径）作为
   逃生档；协议本身零改动（回执计时字段不变）。
7. **#111 原子落盘临时名**：工单口径 `.f{:04d}.png.tmp.<pid>.<tid>`
   （`vtx/fstxn.py` 语义的 C++ 镜像）已按此实现（#110 的裸 `.tmp` 后缀
   升级）；写者 SIGKILL 崩溃窗口内目标恒完整或不存在，`*.tmp.<pid>.*`
   残件为可辨识可清扫模式。

## §1 传输与字节序

- **控制流**：Unix Domain Socket（worker `--ipc-socket=<path>` 监听，
  单宿主契约；`VTXCEF_SERVE_READY socket=… pid=… run_id=… slots=4`
  stdout 行后可连接）。
- **数据流（模式 2）**：POSIX SHM 4-Slot 环。**零残留设计**：
  `shm_open(O_CREAT|O_EXCL)` → `ftruncate` → `mmap` → **立即
  `shm_unlink`** —— 对象名仅存在于微秒级窗口，实体由各方 fd 维系，
  任何进程死亡（含 SIGKILL）由内核回收，`/dev/shm` 任何时刻零
  `vtx_shm_*` 残留。消费端 fd 经 Hello 消息 **SCM_RIGHTS** 随带交付
  （工单「消费端映射同一 shm_open 句柄」）。
- **字节序**：小端（目标面 linux64/x86-64）；全部消息定长、无隐式
  填充（`<` packed 语义与 C++ 原生偏移一致，静态断言钉死）。

## §2 SHM 环布局

```
偏移 0        RingControl（384B，alignas(64)）
偏移 384+…    Slot[0..3] = [FrameSlotHeader 128B][frame_bytes 像素]
slot_stride = align64(128 + frame_bytes)；frame_bytes = max_w*max_h*4
```

### RingControl（偏移表 = cef_client.py `_CTRL_*` 镜像）

| 偏移 | 类型 | 字段 | 语义 |
| --- | --- | --- | --- |
| 0 | u32 | layout_ready | 创建者 release-store `0x56545852`（"VTXR"）发布布局；消费端 acquire 自旋 |
| 4 | u32 | magic | `0x56545852` |
| 8 | u16 | protocol_version | 1 |
| 10 | u16 | ring_slots | 4 |
| 12/16 | u32 | max_width/max_height | 环容量（= worker 视口） |
| 20 | u32 | frame_bytes | 每槽数据字节数 |
| 24 | u32 | slot_bytes | = slot_stride |
| 32 | u64 | run_id | 本次 serve 运行标识（陈旧帧判别基准） |
| 40 | i32 | owner_pid | 创建进程 |
| 48/56/64 | u64 | published/released/backpressure_wait_us_total | 环级账目 |
| 128 | SlotGate[4] | 门（各 64B 专享缓存行） | state u32 @+0、seq u32 @+4 |

### 槽位状态机（单写者=worker 捕获路径，单读者=宿主）

```
EMPTY --Claim(写者,CAS)--> WRITING --Publish(写者,release)--> READY
READY --Release(读者,release)--> EMPTY
WRITING --Abandon(写者,错误路径)--> EMPTY
```

同步语义：发布先写槽头+像素数据，后 release-store `READY`；读者
acquire 见 `READY` 即见完整帧（读写同步不依赖信号量/轮询数据区）。
`seq` 为该槽累计发布序号（通知回传，ABA 防御）。

## §3 消息（Socket 控制流）

| 方向 | 消息 | 定长 | magic | 载要 |
| --- | --- | --- | --- | --- |
| W→C | HelloInfo | 64B | `VTXH` 0x…48 | run_id/pid/slots/几何 + **SCM_RIGHTS(SHM fd)** |
| C→W | FrameRequest | 64B | `VTXQ` 0x…51 | flags/request_id/frame_index/期望尺寸/out_png_path[40]（≤39+NUL） |
| W→C | FrameSlotHeader（模式 1 回执） | 128B | `VTXF` 0x…46 | 工单冻结字段集（见 §4） |
| W→C | SlotNotice（模式 2） | 32B | `VTXN` 0x…4E | status/request_id/frame_index/slot_index(0xFF=无)/seq/backpressure_wait_us |

`FrameRequest.flags`：bit0=WANT_SLOT（模式 2）、bit1=DUMP_RAW（诊断：
交付字节同时直写 `<out_png_path>.raw`，供端到端逐字节对照）。
关闭协议：无显式 Shutdown 消息——宿主 `close()` 即 EOF，worker 优雅
收尾（退出码 0）；请求中途断裂/协议错误/空闲超时分别为 4/3/5。

### FrameSlotHeader（工单冻结，偏移=线上规范）

| 偏移 | 类型 | 字段 | 语义 |
| --- | --- | --- | --- |
| 0 | u32 | magic | `0x56545846`（"VTXF"） |
| 4 | u16 | protocol_version | 1 |
| 6 | u16 | status_code | 0=OK 1=TIMEOUT 2=SIZE_MISMATCH 3=JS_ERR |
| 8 | u64 | run_id | 与环 run_id 一致（不符=陈旧残帧，消费端拒收） |
| 16 | u32 | document_gen | 每次导航自增 |
| 20 | u32 | request_id | 请求序号（回显） |
| 24 | i32 | target_frame_index | 请求帧号 f |
| 28 | i32 | canary_frame_index | (0,0) 像素解码核实的帧号（-1=未核实） |
| 32/36 | u32 | width/height | 实测帧尺寸 |
| 40 | u32 | stride | 槽内行步长（紧凑=width*4） |
| 44 | u8 | pixel_format | 1=BGRA_PREMUL（OnPaint 原始面）2=RGBA_STRAIGHT |
| 45 | u8 | color_space | 1=sRGB |
| 46 | u16 | rejected_paints | 本请求拒绝的陈旧 OnPaint 次数 |
| 48 | u64 | t_ready_wait_us | 导航→加载落定 |
| 56 | u64 | t_capture_raw_us | OnPaint→槽/暂存紧凑拷贝 |
| 64 | u64 | t_alpha_norm_us | SIMD 逆预乘（模式 1） |
| 72 | u64 | t_png_encode_us | PNG 编码（模式 1） |
| 80 | u64 | t_disk_commit_us | .tmp+rename 落盘（模式 1） |
| 88-127 | — | 对齐填充 | 零填充 |

## §4 请求/交付时序

**模式 1（Worker 原地落盘，首选零 IPC 像素传输）**：
请求(64B) → 导航(`--url-template` 的 `{f}` 代入) → external begin frame
驱动 OnPaint（尺寸卫兵→紧凑拷贝→1px 金丝雀核实；陈旧帧拒绝计数）→
编码线程池：SIMD 逆预乘（pixel_ops）→ PNG（#111 双后端
`--png-encoder=spng|fpng|zlib` × `--png-level` × `--png-filter`，默认
spng L1 SUB|UP）→ `.tmp.<pid>.<tid>`+`rename` 原子落盘 → 回执(128B)。
回执计时字段语义不变：`t_png_encode_us` 为所选后端编码段耗时
（跨后端对比时注意口径一致）。

**模式 2（SHM 环跨进程传输，为 #68 直通 FFmpeg 预研）**：
请求(64B) → **先取空槽**（4 槽全满=背压：暂停发起下一帧 BeginFrame，
分片等待并计时 `backpressure_wait_us`，超时→TIMEOUT 通知，绝不死等）
→ OnPaint 紧凑拷入槽数据 → 填槽头 → Publish(READY) → 通知(32B: 槽索引
+seq+背压计时) → 消费端 mmap 同 fd 零拷贝读 → 写门 EMPTY 释放。

## §5 金丝雀约定（pages/frame_canary.html）

像素 (0,0) 不透明：R=0x43('C')、G=f&0xFF、B=(f>>8)&0xFF、A=255
（不透明像素预乘==直通，BGRA 面直接解码）。解码失败/不符 → 该次
OnPaint 按陈旧帧拒绝（`rejected_paints`++），不占交付。

## §6 故障注入面（`--fault-inject`）

| 值 | 语义 | DoD |
| --- | --- | --- |
| `size_mismatch` | GetViewRect 返回 height-1（1920x1079），捕获卫兵仍按期望 1080 → SIZE_MISMATCH 回执（实测尺寸随 detail 显形） | ① |
| `abort_after_frame` | 成功交付 `--fault-abort-after-frames`（默认 1）帧后 `abort()`（serve 模式注入 `--disable-crash-reporter` 保原生 SIGABRT） | ③ |
| 消费端阻塞 | 无 worker 侧开关：宿主持槽不释放即触发环满背压 | ② |

消费端任何 worker 死亡（abort/kill -9）经 socket EOF/RST 显形为
`CefWorkerError`——明确错误而非死等。
