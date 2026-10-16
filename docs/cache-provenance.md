# 缓存与回执：输入变化必须使旧结果失效

> 本文描述 PR#1（Windows 适配）合入 main 后的实际语义。PR 原始方案（项目级全量
> 指纹 + `cache/<input-fingerprint>/` 整仓布局）在合并中被 main 13bed7a 的
> per-产物内容寻址缓存取代，回执侧价值全部保留——合并裁定见 merge commit
> f0c98dd 与 `vtx/cache_state.py` 模块头。

## 修复的问题

原缓存按文件存在、帧数或 HTML 时间戳判断命中。同路径替换同长素材、修改字幕，
或两个目录使用同名 HTML 时，旧声音/画面可能继续被输出。失败的重跑也可能留下
旧 QC 成功回执。

合入后的机制（main 13bed7a 架构 + PR#1 补强）：

- **gfx 页缓存** `cache/gfx/<key>/`：键 = sha1(html 字节 + 显式依赖内容 + fps +
  视口 + 时长)。同内容跨路径复用；同名页不同内容、同页换时长自然分键。
- **Electron Alpha 身份**：显式 Electron 的可执行文件内容、导航 worker、错误门、像素
  codec、Python 适配/生产门/PNG 校验/监管器代码及关键输出配置进入 gfx 键；缓存根为
  `cache/gfx_exp/<ns>/<key>/`。namespace 同样入键（包括 CDP/CLI 的显式 namespace），
  因而现有 vseg、range、multipass 消费 gfx 目录内容键时一并失效，不会仅换 gfx 却吃旧合成。
  身份与 namespace 进入输入指纹和各阶段回执；运行中改变身份拒绝成功发布。
- **键轴边界（诚实注记）**：CDP/CLI 的无 namespace 历史共享键保留；Chrome 升级与
  NAVFREE/STATIC 不入此键轴。Electron 运行时旁置资源须保持锁定分发包完整；系统字体、
  未声明依赖和网络资源不由二进制摘要完整追踪。段中间物 `vseg_pass` 已入 gc 白名单。
- **seg 预编码** `cache/seg/<key>.mov|mp4` 与 **抽音频** `cache/audio/<key>.wav`：
  键 = sha1(源 stat 身份 + 切片/窗口/fade/编码参数)。源 stat 身份 = path + size +
  mtime_ns + **首/尾 64KB 采样哈希**（PR#1 补强：同尺寸 + 恢复时间戳的同路径替换
  不再误命中——媒体重编码首尾字节必变）。
- **分段渲染段缓存** `cache/vseg*/seg…_<内容键>.mp4`（PR#1 合入融合）：段文件名
  携带内容键（段区间 + 画布/模式/gpu + 每层上游内容键与参数 + 段字幕 ass 字节）。
  改字幕/改图层/换 gfx 内容后旧段不再命中——PR 回归场景「改字幕不重渲」关闭。
  段数（nseg）与 GPU 开关由区间和键内 gpu 位天然隔离（PR 的 tag 变体方案被此取代）。
- 键变即新条目，旧键是垃圾由 `vtx gc` 回收；`vtx df`/`vtx snapshot`/VERSIONS
  记账不受影响。

## HTML 依赖

外部 CSS、JavaScript、图片、字体等本地依赖需显式列出（PR#1：`cache_dependencies`
参与 gfx 缓存键与回执身份），例如：

```json
"project": {
  "fps": 30, "width": 1920, "height": 1080, "duration_frames": 180,
  "cache_dependencies": ["gfx/style.css", "gfx/logo.png"]
}
```

不存在或非文件依赖会报错（Ctx 构造即快速失败）。声明属项目级——任一依赖变化会使
全部 gfx 页重渲（保守过失效，可接受）。未声明的间接依赖、网络资源、运行时下载和
FFmpeg 与未列入上述 Electron 身份的代码升级不在此覆盖范围；建议使用内嵌或明确声明的静态本地素材。
未追踪的渲染器/运行依赖升级后须显式隔离新缓存或清理再重渲。输入在一次命令期间应
保持不变，同一项目不要同时运行多个写入命令。

## 阶段回执（PR#1）

`receipt.json` 绑定 input_fingerprint = sha256(timeline 字节 + 源 stat 身份 +
gfx html 字节 + 声明依赖内容)——与缓存键同等敏感（任何缓存键会变的输入变化，
回执门同样重置），但不做全量媒体哈希（GB 级 I/O 不可接受）。输入变化不继承旧的
成功状态。CLI 在阶段开始前撤销其旧成功及下游成功，失败重跑不能保留旧 QC PASS。
audio 要求当前 prepare，video（有 gfx 时）要求当前 gfx，mux 要求当前 video/audio，
qc 要求当前 mux；单跑某阶段前置不满足会直接拒绝。可以直接运行 `all` 完成依赖顺序。
旧工程首次需完整重建一次；不直接认领旧产物。

历史缓存保留以便恢复和审计；工具的显式 `clean` 仍删除项目 cache/ 与 out/。本次不
自动删除用户旧工程或媒体。回执是流程来源检查，不是对最终文件外部篡改的完整性证明
（源指纹的采样哈希亦非对抗性保证）。

## 中断与恢复

单文件 FFmpeg 媒体输出先写同目录事务临时件；编码成功、完整媒体校验与输入身份复核
通过才原子替换目标。失败保留先前完整目标，CLI 阶段开始撤销的成功回执不会复活。
重跑原阶段或 all 即可恢复，**这是实际写操作**，不要求先清空项目缓存。

抽音频、seg、分段及 pass 的命中须有成功解码绑定的 SHA256 完成凭据，或对旧缓存
重新校验：WAV 样本数据长度必须与头部一致；旧抽音频还须核对源时长，未知或不精确
则保守重建一次。视频须完整解码；合成段满足精确段长，预编码允许已认证的短源自然
结束（帧数选项是上限，不以补帧伪装完成）。单独存在文件、整数帧数旁车或流头不能
证明中断文件完整。合法缓存可复用，损坏节点
重建；CDP/CLI 的 PNG 逐帧恢复沿用 gfx 完整性门。Electron 仅复用绑定相同身份、
namespace、完整帧摘要和成功 run 回执的完整页；缺失/损坏完成标记的页须全部请求新帧，
不能把现存 PNG 补签为 Electron 成功。每个新块须有本次完整 adapter/worker 回执，
非空 failed、非零退出、缺帧/错后端/缺错误门均硬失败，无回落。整页所有块通过、PNG
完整性与输入身份门通过后才原子发布 complete.json。配置与入口见
[Electron 导航接线](../experiments/gfx_electron_worker/README.md#alpha-导航与生产接线候选非原视频全片验收)。

杀死整个写进程遗留的同目标临时件，下一次写入持该目标事务锁后清理；其他目标的
临时件不受影响。进程崩溃发生在媒体替换与完成凭据写入之间时，留下完整旧式媒体，
再次命中必须重新校验。凭据不是签名，不覆盖有权限的主动篡改，亦不承诺断电 fsync
持久性；不要同时运行同项目写命令或把 gc 与渲染并发。

## 回归

- `python -m unittest discover -s tests -v`：内容/时间戳/依赖/同名页面/回执失效
  测试，以及可选 Chrome/FFmpeg 集成用例（`VTX_TEST_CHROME` / `VTX_TEST_FFMPEG`）。
- `python tests/run_cache_regression.py --repo <checkout> --output <new-dir> --ffmpeg <executable> --chrome <executable>`：初次渲染→同长素材替换且恢复时间戳→修改
  字幕/HTML→不清缓存重跑→独立冷启动对照。比较完整 180 帧的缩略 RGB 与解码 PCM。
- `tests/run_e2e.py` 与 `tests/verify_e2e.py`：12 秒单段/并行全流程、严格 QC、
  接缝和音频对照。

Windows 实测旧候选版本暖/冷最大单帧平均差 100.64/255、音频不一致且旧成片字节
完全不变；修复版本差为 0、音频一致，成片已更新。Linux 侧由上述合成回归 +
voice-ime 真实工程 E2E 复验（合并裁定见 merge commit f0c98dd 与
`vtx/cache_state.py` 模块头）。这是确定性合成用例的正确性结果，
不是长视频或跨平台性能结论。
