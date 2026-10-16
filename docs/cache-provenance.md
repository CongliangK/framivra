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
- **键轴边界（诚实注记）**：渲染驱动/算法轴（Chrome 升级、渲染算法变更）与行为开关轴
  （NAVFREE/STATIC/VTX_GFX 引擎选择）不入键——同键不同生成路径的理论风险以人工
  `vtx clean`/`vtx gc` 为补救（R12-G1-037 交界认知在案）；段中间物 `vseg_pass` 已入
  gc 白名单（commit fa650ad）。
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

## 插件代码身份与升级说明（#86）

`plugin_code_digest` 对插件根下的 Python 文件按相对 POSIX 路径全序排序，
路径 UTF-8 字节与完整文件字节分别带长度前缀；内容、读取失败和目录缺失使用
不同标记。`tests/` 与 `__pycache__/` 的排除相对插件根判断，其他扩展名不纳入。
返回值保持 12 位十六进制 SHA1 摘要；独立算法 token 定义在
`vtx/pluginlib/hooks.py`，不依赖插件或内核发行版本。

**发布/迁移说明：这是缓存身份 breaking change。** 升级后，使用生效插件的
时间线回执失效，依赖代码摘要的插件产物键改变，首次运行需完整重建一次。
旧缓存不自动删除；没有生效插件的时间线不追加插件段，回执身份不受此次算法
变更影响。包内移动文件会改变摘要；插件整根迁移且相对路径/内容不变时摘要稳定。
算法可独立发布，不以 #47/#48 为前置。

此处仍保留读取失败时的标记降级以及既有递归扫描行为；它不是原子目录快照或
对抗篡改证明。插件文件在一次命令期间应保持不变。

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
浏览器/FFmpeg/vtx 代码升级不在此覆盖范围；建议使用内嵌或明确声明的静态本地素材。
升级渲染器/运行依赖后请 `vtx clean` 或 `vtx gc` 后重新运行。输入在一次命令期间应
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
重建；PNG 逐帧恢复沿用 gfx 完整性门，不为中断无条件重渲已完成帧。

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

## 质量与证据标签（#90，契约评审候选）

本节为 DOC-CHARTER §25 的待维护方评审候选，合入前须取得文档及契约维护方同意；
自动测试不代替该评审。实现 `vtx/quality.py` 输出附加 `vtx.quality/1` 对象，
只标注，不改变现有退出码、验收门或缓存身份。用途与证据是独立轴，不是天然全序。

| 标签 | 保证 | 不保证 |
|---|---|---|
| Preview | 明确标识的草稿/近似用途 | 完整质量门、复现或像素一致 |
| Production | production_gates 与 visual_content 均显式 passed | 输入身份、重复构建、逐像素一致 |
| Reproducible | Verified 内容身份、已验证环境身份、固定环境独立重复比较均 passed | 视觉意图正确、跨环境一致 |
| Verified Exact | #62 严格独立 Golden 验证通过，内容/环境身份齐全，限定范围逐帧 RGBA8 像素一致 | 最终 MP4 字节、音频、未声明依赖、范围外内容正确 |

规范化 evidence 含 production_gates、visual_content、content_identity、
environment_identity、repeat_comparison、strict_exact；值仅为 passed、failed、partial、
unknown。缺失、未执行、未知值均归 unknown；partial 不等于 passed。
content_identity=passed 表示所述 scope 内 Verified 完整内容身份，不是 Fast 采样身份。
分类器消费可信生产者的规范化观察，不认证任意 JSON 的真实性；旁车没有签名。

唯一 display 优先规则为 Verified Exact → Reproducible → purpose → unknown。
purpose 独立保留 Preview / Production / unknown；reproducible 与 verified_exact 分别
保留布尔资格。Exact 须 strict_exact、content_identity、environment_identity 全 passed；
Reproducible 须后三者中的 strict_exact 换为 repeat_comparison 后全 passed。
Production 须两个质量门全 passed。显式 preview 优先决定用途；证据标签不抹去用途。

普通阶段回执绑定 final_movie 范围，每次落笔/撤销阶段重算标签，不继承旧最高标签。
草稿成功可标 Preview；正式 mux/qc 存在时不用保留的草稿替代正式结论。
运行时身份仍为 Fast（content_identity=partial）。QC 报告和回执中的技术门可以 passed，
但 visual_content 保持 unknown，因此即使 QC 8/8 也不自动成为 Production。
包含缓存免检探针的质量门标 partial，失败仍为 failed；不更改既有 QC 放行行为。
帧数正确只属技术门，不能证明帧内容；CFR 补帧不能据帧数自动授予复现或 Exact。
#53 的 accepted=false 与 visual_content=unknown 不被标签改写；导出的 receipt/qc
保留相同标签，验收包的既有独立验证规则不变。

#62 verify-experiment 通过时只给 declared_gfx_instances:decoded_RGBA8 范围 Exact；
计划摘要、实例、运行身份与实际环境仍由原报告记录。固定配置/后端/版本的独立比较
另可满足 Reproducible；受控的不同后端或配置比较可以 Exact，但不自动标复现。
普通 compare（包括旧 --strict）、analyze 的 Tier-1/Tier-2、canary 均不提供该严格
证据，标签保持 unknown，Tier-2 预乘等价尤其不能成为 Exact。
生产运行器尚未生成严格证据的限制见 [Golden 证据](gfx-evidence.md)。

成本归因：严格 Golden 报告 quality_cost 单列 strict_golden_verification 的 wall_seconds；
它只计本次校验，不是渲染成本或后端性能，render_seconds 缺证据为 null。
用途/等级不能作为性能提升证据；#63 的渲染成本归因仍须实际运行测量。
