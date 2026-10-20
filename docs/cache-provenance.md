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
  视口 + 时长 + [渲染环境身份](#gfx-environment))。同名页不同内容、同页换时长自然分键。
- **CEF Alpha 身份**：显式 `VTX_GFX=cef` 将协议修订、A0/A1 模式和 worker
  可执行文件 SHA256 并入 gfx 键；缺省路由 `cache/gfx_exp/cef-alpha/<key>/`。
  `VTX_GFX_CACHE_NS` 可覆盖命名空间。生效 CEF 身份和命名空间同时进入阶段回执；
  分段与多 pass 键使用 gfx 相对 cache 的完整路径，不能跨命名空间误命中旧视频段。
- **Electron Alpha 身份**：`VTX_GFX=electron` 将实际 binary、导航 worker、错误门、codec、Python 适配/生产门/PNG 校验/监管器及输出配置纳入同一内容键；缺省 namespace 为 `electron_b0`。只读桥复用生产 validator 核验真实 adapter/worker 回执，不补签旧 PNG。strict 依赖只支持 CDP，Electron/CEF 明确拒绝。
- **环境边界**：CDP/CLI/CEF 的身份与未知状态见[渲染环境身份](#gfx-environment)；
  Electron 保留独立历史身份。段中间物 `vseg_pass` 属 gc 白名单。
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

不存在或非文件依赖会报错（Ctx 构造即快速失败）。项目级依赖变化会使全部 gfx 页重渲。
gfx clip 可选 `cache_dependencies: ["gfx/page.css"]`，与项目声明取并集，只影响声明它的
页面。两级路径均相对 timeline 根目录；非空片段声明按解析后的路径去重排序，全量
内容哈希捕获恢复 mtime 的中间字节修改。共享 resolver 输出路径、全文摘要、声明来源、
HTML 的规范 file URI、策略版本及闭包状态；gfx/回执/impact/GC/diff 消费同一合同。
仅经静态子集确认无资源引用的 legacy HTML 保留历史键；资源型或未知 HTML 显式迁移
到 v1 键，避免同字节不同目录别名。旧目录不自动删除，不能冒充 strict 命中。

项目可选 `gfx_dependency_policy: "strict"`，默认 `legacy` 保持原渲染行为并报告闭包
未证明。strict v1 只接受惰性 HTML、声明的本地栅格图、样式及字体；HTML 白名单见
`vtx/gfx_dependencies.py`。CSS 支持带引号的 `@import "x.css";`、`url("x.png")`、
有限样式函数及 font-face/media/supports；递归引用必须逐项声明。
HTML 子集要求显式配对的非 void 标签，拒绝隐式闭合、非 void 自闭合和 raw-text
内部嵌套标签；CSS 字符串及圆/方/花括号边界必须完整配对。语法不确定时 legacy
报告 unknown 并保留路径身份，strict 前置拒绝，不能用后续拦截弥补错误静态证明。
CSS 以逻辑请求 URL 为相对引用基址，符号链接目标仅用于文件声明和字节归属；
同一 CSS 经不同别名请求必须分别扫描，记录请求 URL 与规范文件路径的对应关系。
畸形 URL 解析错误转成闭包不确定性；文件读取失败不作为解析异常吞掉。脚本、事件属性、HTML 注释、
iframe/SVG/base/srcset、网络/data/blob URL、CSS 转义/注释/自定义变量、未知函数和
at-rule 均拒绝，无法解析的页面失败，不能以扫描无匹配声称闭包完整。

strict v1 限 POSIX file URI，其他平台明确拒绝。strict 仅走独立 CDP 静态截图路径：导航前关闭页面脚本执行并安装 Fetch 策略，允许
的本地请求由锁定字节响应，其他请求阻止且本页失败。每次导航必须实际观察到主文档
及扫描资源的拦截；Chrome 不支持 file 拦截或未加载某个扫描资源时保守失败。
Fetch.enable 成功、Network 日志或有效 PNG 均不构成拦截证明。严格页不混用旧帧，
不重启降级、不走 CLI 回退；完成标记绑定依赖摘要与实际执行路径，旧标记不得补认证。
浏览器、系统字体及渲染环境身份使用下述独立解析器；#47 的资源闭包不替代环境身份。
真实 Chrome 请求、像素变化及失效对账是必须的验收门，静态/mock 测试不能替代。

Golden 使用原始完整 clip 重建身份；旧 Golden schema 无法表达同 src/dur 的多个
不同依赖身份，因此明确拒绝歧义。输入在一次命令期间应保持不变，同一项目不要
同时运行多个写入命令。升级渲染环境后需按既有清理流程重新构建。

<a id="gfx-environment"></a>

## 渲染环境身份（#48）

`vtx/gfx_environment.py` 是 CDP/CLI/CEF 共享解析器，追加于 #47 依赖身份之后，
同时进入 gfx 键和阶段输入指纹；下游分段沿用 gfx 身份。CDP 与 CLI 分键。
环境记录以 `vtx.gfx-environment/1` 编号，页完成凭证保存完整字段与 SHA256，
阶段回执记录环境摘要和可信状态。收集只读，不启动浏览器。

内容轴包括实际原生浏览器文件与版本、CEF worker/运行库及分发资源、动态链接库、
图形库与驱动、Python/numpy/Pillow 运行库、全部平台字体清单的文件内容及生效配置、相关进程环境、
NAVFREE/STATIC、启动参数及渲染实现摘要。字体不限于模板预设家族，包含任意 HTML
所用的系统回退字体。视口、fps、时长由页键绑定，凭证另绑定视口和 fps。
CDP/CLI 固定 device scale、srgb 和字体 hinting 参数，共用同一参数定义；CLI
使用独立 profile，排除桌面浏览器偏好/扩展。CEF 沿用其 worker 的锁定模式。
PNG 压缩级别和调度并发只影响编码/执行，不加入 RGBA 环境轴。

CDP HTML 可显式设置 `VTX_GFX_PNG_FAST=1`（默认 `0`，其他值拒绝），使本次
实际截图请求追加 `optimizeForSpeed: true`；普通请求参数不变。每次启动浏览器
均读取本机 `/json/protocol`，确认该参数是可选 boolean；能力或截图失败时本次
fast 渲染失败，不降级 CLI。SVG、CLI、CEF、Electron 不支持此开关。
它只选择 PNG 编码策略，不加入 RGBA 环境轴或改变像素缓存身份；实现文件变更
仍按既有源码摘要使身份失效。已有合法缓存可以复用，不强制更换命名空间。
性能对照须使用显式的新实验命名空间，避免将缓存命中算作加速。

fast 的渲染 stats 和阶段回执 `extra.png_capture` 记录请求/实际发出的截图参数、
协议核验及截图调用成功/失败次数，另列复用、canvas 导出和静态复制帧数。
清理失败仍保留这些计数，记录清理步骤/异常类型；若渲染已经失败，保留原异常。
截图调用次数包含准备探针、白黑双截及失败重试，不能当作新产帧数；协议成功
只证明浏览器声明支持，不能证明压缩倍率或像素等价。全缓存命中不起浏览器、
不探测，实际截图参数为空；canvas/静态复制也不冒充 fast 截图。dry-run 不探测、
不写回执。此接线没有全片性能验收结论，不能由局部实验外推全片减半。

Linux 解析器采用保守的主机/启动周期边界，不承诺跨机或重启后缓存共享。
同路径同大小并恢复 mtime 的替换仍按内容识别；哈希 memo 同时检查 inode、ctime。
字体集合、配置和运行库成员均在复核时重新枚举。任务不接纳中途的新身份；
环境发生变化，阶段完成门拒绝发布成功回执。

未知平台、缺字体清单、缺运行库、不可读资源、无法解析的动态链接或覆盖路径均为
`unknown`，不能用两条相等的 unknown 记录证明等价。时间线可加载以显示诊断，
实际渲染与缓存消费明确拒绝；没有静默复用或假定环境不变的降级。

Darwin 分支接纳原生 arm64 的 Google Chrome app/CDP 与 CEF worker app。
它绑定完整 app/framework/资源及内部符号链接、原生依赖、实际 dyld cache 与全部
具名 subcache 的文件内容、CoreText 可见字体文件和字面名称清单、回退配置、
系统/用户字体偏好、OS 与启动周期及工具身份。Mach-O 独立 helper 的
`@executable_path` 按该可执行文件目录解析；bundle 内旧版本成员同样纳入检查。
应用偏好域取自实际 renderer/helper app 的 CFBundleIdentifier，绑定相应系统/用户
plist 及 ByHost 文件内容摘要，不读取 Chrome profile/history，也不保存偏好原值。
选中的偏好文件及 ByHost 目录不支持符号链接。未知库、外部 bundle 链接、缺失或
不可读 cache、loader 覆盖均拒绝；不支持 Rosetta、Darwin CLI/Electron 或其他浏览器。
首次收集读取实际文件；后续沿用 inode/size/mtime/ctime 哈希 memo，仍复核成员与
元数据。CPU 合成测试不代表实机验收，输入绑定不证明跨平台像素一致。
真实 CDP 缓存验收入口见 [Darwin 集成运行器](../tests/run_macos_cdp_acceptance.py)，
全链音视频与字幕入口见 [端到端运行器](../tests/run_e2e.py)。其他平台继续拒绝。

缺少环境字段的旧页不得补签成功，即使所有 PNG 都能解码。首次使用须重渲，
旧目录保留供审计。中断续渲只允许本解析器生成且绑定当前页合同的
`environment.pending.json`；不是完整页消费凭证。完整性、依赖与环境门全部通过
后原子发布 `complete.json`，删除 pending。桥与 FrameSource 消费也核验环境凭证。
CDP 自动回落 CLI 的页明确失败，不发布完成凭证或成功回执；须显式以
`VTX_GFX=cli` 重跑获得独立身份。回落前撤销 pending，防止混合帧被续签；
带 `mixed_producer` 的历史页也不允许复用或消费。

`input-bound` 仅表示所列输入已绑定，`pixel_equivalence=unproven` 不因摘要相等
变成像素等价或 Verified Exact。生效 GPU/Chromium 内部接口、实际字体消费和独立
全帧对照仍由 #102/#62 的运行证据承担。Golden 冻结计划可为每臂指定
`input_environment_sha256`，运行回执须提供匹配、完整且非 unknown 的
`input_environment` 凭证；原有真实运行门不减弱。

真实验收摘要与每帧摘要见
[环境验收证据](../tests/fixtures/gfx-environment/acceptance-2026-10-18.json)。
该受控夹具覆盖完整 HTML、JavaScript 和系统文本字体，不是整片性能或跨机像素证明。
浏览器版本不同也可能逐帧相等，缓存仍须按身份分离。CEF 运行库负例为同一 worker、
同一路径下替换 libcef.so 文件（追加不参与装载的 ELF 尾字节），证明内容失效；
它不是运行库版本升级成功的声明。复现实验会向显式指定的新证据目录写入文件并
启动真实浏览器，参数和前置工具通过零写态帮助查看：

```bash
env python3 tests/run_gfx_environment_acceptance.py --help
```

## 阶段回执（PR#1）

QC 的段免检记录另绑定 `vtx/qc.py` 中 `_QC_GRAY_FILTER` 的精确采样表达式。
记录 schema 升为 v2：新读取器拒绝 v1（即使 filter 相同），旧 v1 读取器也拒绝 v2，
因此升级和回退均重新测量。缺少或不匹配 `probe_filter` 的记录不免检，下一次 QC
实测后刷新；不删除媒体、
重编码或放宽阈值。当前表达式保留 bicubic 并开启准确舍入，避免已观察到的
FFmpeg 7.1.5 x86_64 黑场缩小偏差；源片、成片与 `qc_clean` 共用该探针。
`--strict` 仍全部实测。此身份不包含完整 FFmpeg/CPU 环境，环境升级不构成跨版本
像素保证；需要重新核验时使用 strict。黑场、近黑、声明范围与泄漏的 CPU 回归见
`tests/test_qc_sampling.py`；这不是整片内容或任意小区域泄漏的保证。

`receipt.json` 绑定 input_fingerprint = sha256(timeline 字节 + 源 stat 身份 +
gfx html 字节 + 声明依赖内容 + 生效后端/环境身份/实验命名空间)——与缓存键同等敏感（任何缓存键会变的输入变化，
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
完整性与输入身份门通过后才原子发布 complete.json。封存路线的配置与复现入口见
[Electron 导航接线](../experiments/gfx_electron_worker/README.md#alpha-导航与管线接线复现入口)。

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

## 修改影响快照（#52，CPU 分析面）

两份旧/新 timeline 都会读取当前素材，不能还原原位修改前的字节。新增
`diff --capture-impact` 保存修改前输入身份；随后以快照与当前 timeline 对比。
捕获为 CPU/x264、formal、当前 `VTX_VIDEO_SEGMENTS` 合同，不探测 GPU。
必须显式设置 `VTX_SEG_NVENC=0`；snapshot 的 execution 字段记录实际分析参数。
以下只输出 JSON 到标准输出，零项目写入：

```bash
env VTX_SEG_NVENC=0 python3 vtx/vtx.py diff --capture-impact timeline.json --dry-run
```

显式指定 `--json NEW_PATH` 才写分析文件，拒绝覆盖已有文件或链接；与 dry 冲突。
第二次使用既有 diff 两参数形式，将 before 快照放在第一位、当前 timeline 放在
第二位，输出 affected_nodes（节点、旧新键、原因）、new_artifacts、
retired_artifacts、conservative_nodes 和 uncertainties。建议重跑阶段只是分析结果，
不改变 all 的编排。未变音频不建议重混，不表示 all 会自动跳过 audio。

快照比较要求同一项目根。v2 快照补齐 gfx 底轨的 QC 基准段与无字幕 pass，拒绝旧 v1
快照（需重新捕获），避免把遗漏节点误当新产物。主图与 QC 各自计算切点；草稿
使用 vseg_draft，正式 gfx 底轨另含 vseg_qc。缓存路径集合依据修改前完整温缓存假设，不检查产物是否
缺失/损坏，也不把历史其他代缓存算作必然命中。未知依赖明确列出并扩大保守节点集；
静态声明不证明动态 JavaScript、网络或插件闭包。窗口媒体缺少尺寸探测时列为未知。
媒体仍使用现有 Fast 采样身份，首尾不变且恢复 mtime 的中间字节修改不受保证（#85）；
声明依赖使用完整摘要，包含这种中间字节修改。该报告不是 Verified 身份证明。

pass/vseg 键与实际渲染共享纯身份函数和段 ASS 字节生成。抽取 pass 身份函数会改变
合成代码摘要，因此升级后视频合成段与 pass 中间物一次性冷重建；旧缓存不自动删除。
不固定旧摘要来伪造兼容。gfx 后端实现、QC 质量等级与此分析合同独立。


HTML 验收夹具保留 90 帧/页长 profile；受限 workflow 显式选择 48 帧/页 correctness
profile，仍为 30 场景、1080p/30fps、生产编码质量及八段。字幕按比例落在第八页
（48 帧 profile 为 [341,357)），完整解码、warm 字节/mtime、全部 mutation 和 raw delta
断言保持。原 2700 帧 hosted 运行在主视频完成后、QC 合成阶段触及既有 900 秒上限，
未通过验收；不能据此判定生产回归或 OOM。48 帧 profile 的通过须由最终版本实际运行
证明。900 秒命令、3000 秒边界预算及 65 分钟作业上限不延长。事件 JSONL 和有界
失败路径/大小清单只辅助定位，不替代完整库存、解码或成功回执。
