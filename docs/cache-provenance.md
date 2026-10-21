# 缓存、回执与渲染证据

本文维护输入身份、失效/恢复、质量标签与独立 Golden 证据；操作顺序见 [剪辑配方](skill/references/edit.md)。缓存命中、阶段成功、包完整性和像素等价是不同结论，不能互相替代。

## 产物身份

- **HTML gfx 页缓存** `cache/gfx/<key>/`：键 = sha1(html 字节 + 显式依赖内容 + fps +
  视口 + 时长 + [渲染环境身份](#gfx-environment))。同名页不同内容、同页换时长自然分键。
- **CEF Alpha 身份**：显式 `VTX_GFX=cef` 将协议修订、A0/A1 模式和 worker
  可执行文件 SHA256 并入 gfx 键；缺省路由 `cache/gfx_exp/cef-alpha/<key>/`。
  `VTX_GFX_CACHE_NS` 可覆盖命名空间。生效 CEF 身份和命名空间同时进入阶段回执；
  分段与多 pass 键使用 gfx 相对 cache 的完整路径，不能跨命名空间误命中旧视频段。
- **Electron Alpha 身份**：`VTX_GFX=electron` 将实际 binary、导航 worker、错误门、codec、Python 适配/生产门/PNG 校验/监管器及输出配置纳入同一内容键；缺省 namespace 为 `electron_b0`。只读桥复用生产 validator 核验真实 adapter/worker 回执，不补签旧 PNG。strict 依赖只支持 CDP，Electron/CEF 明确拒绝。
- **SVG 序列身份**：显式 `svg-sequence` 使用 `cache/gfx_exp/svg_inkscape_v1/<key>/`。
  manifest、每帧 SVG、声明字体/依赖及可选生成器文件的全文摘要，与画布/fps/帧数、
  Inkscape/fontconfig 身份、运行库及实现摘要共同成键；生成器只记录、不执行。
  完成凭据绑定该输入记录与逐帧 PNG SHA256，不沿用 HTML 环境证明。
- **环境边界**：CDP/CLI/CEF 见[渲染环境身份](#gfx-environment)，Electron/SVG 各有独立身份；
  身份匹配不证明跨后端像素等价。段中间物 `vseg_pass` 属 gc 白名单。
- **seg 预编码** `cache/seg/<key>.mov|mp4` 与 **抽音频** `cache/audio/<key>.wav`：
  键 = sha1(源 stat 身份 + 切片/窗口/fade/编码参数)。源 stat 身份 = path + size +
  mtime_ns + **首/尾 64KB 采样哈希**；这是 Fast 采样身份，首尾不变且恢复 mtime 的中间字节修改不受保证。
- **分段渲染段缓存** `cache/vseg*/seg…_<内容键>.mp4`：段文件名
  携带内容键（段区间 + 画布/模式/gpu + 每层上游内容键与参数 + 段字幕 ass 字节）。
  改字幕/改图层/换 gfx 内容后旧段不再命中。
  段数（nseg）与 GPU 开关由区间和键内 gpu 位天然隔离。
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

Linux 解析器采用保守的主机/启动周期边界，不承诺跨机或重启后缓存共享。
同路径同大小并恢复 mtime 的替换仍按内容识别；哈希 memo 同时检查 inode、ctime。
字体集合、配置和运行库成员均在复核时重新枚举。任务不接纳中途的新身份；
环境发生变化，阶段完成门拒绝发布成功回执。

未知平台、缺字体清单、缺运行库、不可读资源、无法解析的动态链接或覆盖路径均为
`unknown`，不能用两条相等的 unknown 记录证明等价。时间线可加载以显示诊断，
实际渲染与缓存消费明确拒绝；没有静默复用或假定环境不变的降级。

Windows 分支接纳原生 x64、64 位进程中的 Chrome/Edge CDP 或 CLI，不接纳 CEF 或模式覆盖。
浏览器必须是实际 Application 分发中的 AMD64 PE，且版本目录与对应运行库一致；
身份绑定分发资源、系统库与驱动、系统/用户字体及注册表配置、机器和启动周期。
不接受字体或 loader 覆盖环境。这里描述解析器支持范围，不表示已经完成原生 Windows
全链验收，也不证明跨机器或跨平台像素一致；SVG 等独立后端以自身契约为准。

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
真实 CDP 缓存验收入口见 [Darwin 集成运行器](../tests/runners/run_macos_cdp_acceptance.py)，
全链音视频与字幕入口见 [端到端运行器](../tests/runners/run_e2e.py)。本解析器不接纳上述范围之外的平台或运行模式。

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
env python3 tests/runners/run_gfx_environment_acceptance.py --help
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
`tests/qc/test_qc_sampling.py`；这不是整片内容或任意小区域泄漏的保证。

`receipt.json` 绑定 input_fingerprint = sha256(timeline 字节 + 源 stat 身份 +
生效 gfx 路由的输入身份)：HTML 绑定页面、声明依赖、后端/环境及命名空间；SVG 绑定上述独立输入记录。
与缓存键同等敏感（任何缓存键会变的输入变化，
回执门同样重置），但不做全量媒体哈希（GB 级 I/O 不可接受）。输入变化不继承旧的
成功状态。CLI 在阶段开始前撤销其旧成功及下游成功，失败重跑不能保留旧 QC PASS。
audio 要求当前 prepare，video（有 gfx 时）要求当前 gfx，mux 要求当前 video/audio，
qc 要求当前 mux；单跑某阶段前置不满足会直接拒绝。可以直接运行 `all` 完成依赖顺序。
旧工程首次需完整重建一次；不直接认领旧产物。

历史缓存保留以便恢复和审计；工具的显式 `clean` 仍删除项目 cache/ 与 out/。本次不
自动删除用户旧工程或媒体。回执是流程来源检查，不是对最终文件外部篡改的完整性证明
（源指纹的采样哈希亦非对抗性保证）。

## 中断与恢复

项目自有单文件媒体由 `atomic_media` 统一持目标锁，先在同目录临时件生产，完整
校验与输入身份复核通过才替换目标并写 `<媒体文件名>.complete.json`。`Ctx.run`
与并行 FFmpeg 任务经适配器接入，denoise 的多步生产链直接接入；管道、null 输出与
图片不属于此事务。替换前失败保留原目标，CLI 已撤销的成功回执不会复活。
重跑原阶段或 all 即可恢复，**这是实际写操作**，不要求先清空项目缓存。

裸抽音频、seg 预编码、vseg 分段/pass、range 子段与 bgm-render/denoise/hello-plugin
源处理缓存，均只接受生产者完成凭据：schema、媒体 SHA256 与调用方要求的帧数
必须匹配。缺失或失配的凭据直接导致重建，不靠重新解码、旧音频时长检查或帧数
旁车认领旧媒体，缓存读取不补签凭据。生产时 WAV 样本数据须与头部长度一致，
视频须完整解码；合成段要求精确段长，预编码允许已认证的短源自然结束（帧数选项
是上限，不以补帧伪装完成）。subs 预览的 seg 与 qc_clean 也复用此校验；后者
须匹配全片帧数，失效时沿用图层/黑场回落。完成凭据不证明 qc_clean 属于当前
timeline；原始输入和 PNG 页不因此改为单文件媒体凭据契约。

CDP/CLI 的 PNG 逐帧恢复沿用 gfx 完整性门。Electron 仅复用绑定相同身份、
namespace、完整帧摘要和成功 run 回执的完整页；缺失/损坏完成标记的页须全部请求新帧，
不能把现存 PNG 补签为 Electron 成功。每个新块须有本次完整 adapter/worker 回执，
非空 failed、非零退出、缺帧/错后端/缺错误门均硬失败，无回落。整页所有块通过、PNG
完整性与输入身份门通过后才原子发布 complete.json。封存路线的配置与复现入口见
[Electron 导航接线](../experiments/gfx_electron_worker/README.md#alpha-导航与管线接线复现入口)。

杀死整个写进程遗留的同目标临时件，下一次写入持该目标事务锁后清理；其他目标
及完成凭据自身的临时件不被该清理认领。媒体替换后、凭据写入前崩溃，媒体虽可能
完整，但缺失或与新字节不符的凭据不能命中，下次由生产者重建。

mux 的历史轮转/旧版核对在替换前回调中执行，VERSIONS 记账在媒体与凭据发布后
执行；两者都在最终目标锁内。回调自己的副作用不作全局回滚：替换前回调失败保留
原媒体，但已发生的历史清理/提示状态不撤销；替换后回调失败会报错，已发布媒体
不回滚，也不会记阶段成功。dry-run 不调用生产者或提交回调。

普通 clean/gc（含 gc --all）按各自模式和年龄规则决定媒体去留，再保留其精确
完成凭据：owner 必须是 cache/ 或 out/ 下现存、路径链无符号链接的普通媒体文件，
且 owner 及祖先不在本次删除计划中。凭据保留不扩大媒体保留范围、不认证内容；
孤儿凭据、相似前缀与事务临时件不据此豁免。危险全清理不采用此配对保留规则。
凭据不是签名，不覆盖有权限的主动篡改，亦不承诺断电 fsync 持久性；不要同时
运行同项目写命令或把 gc 与渲染并发。

## 回归

- `python -m unittest discover -s tests -v`：内容/时间戳/依赖/同名页面/回执失效
  测试，以及可选 Chrome/FFmpeg 集成用例（`VTX_TEST_CHROME` / `VTX_TEST_FFMPEG`）。
- `python tests/runners/run_cache_regression.py --repo <checkout> --output <new-dir> --ffmpeg <executable> --chrome <executable>`：初次渲染→同长素材替换且恢复时间戳→修改
  字幕/HTML→不清缓存重跑→独立冷启动对照。比较完整 180 帧的缩略 RGB 与解码 PCM。
- `tests/runners/run_e2e.py` 与 `tests/runners/verify_e2e.py`：12 秒单段/并行全流程、严格 QC、
  接缝和音频对照。

Windows 实测旧候选版本暖/冷最大单帧平均差 100.64/255、音频不一致且旧成片字节
完全不变；修复版本差为 0、音频一致，成片已更新。Linux 侧由上述合成回归 +
voice-ime 真实工程 E2E 复验（合并裁定见 merge commit f0c98dd 与
`vtx/cache_state.py` 模块头）。这是确定性合成用例的正确性结果，
不是长视频或跨平台性能结论。

## 质量与证据标签（#90，契约评审候选）

本节为 [文档写入与评审](DOC-CHARTER.md#写入与评审) 的待维护方评审候选，合入前须取得文档及契约维护方同意；
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
生产运行器尚未生成严格证据的限制见 [Golden 证据](#golden-evidence)。

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

<a id="golden-evidence"></a>

## Golden 独立运行证据

`scripts/gfx/gfx_golden.py verify-experiment` 是 #62 的独立严格验收入口。
普通 `compare` 与旧 `--strict` 的抽样、mtime 语义保持兼容；它们不能证明独立实验。
`verify-experiment --help` 列出必需文件参数。执行会原子替换指定报告文件。
#102 的 `verify_manifest_compatibility` 不变，不能用忽略环境差异的开关代替它。

### 生产者与信任边界

实验运行器必须在两臂启动前冻结计划，分配不同 run_id，保存计划规范化 SHA256；
每臂渲染器捕获实际运行环境、逐帧来源和完成摘要，只在全部成功后提交 complete 回执。
本工具检查证据一致性，不是签名、时间戳服务或对恶意生产者的真实性证明。
从旧缓存重新扫描、touch、把旧回执改名或补一个 run_id 都不是合法的证据生产流程。
生产渲染器尚不生成此契约；#107/#118/#119 的运行器接入与实机验收是独立义务。
合成 PNG 测试只证明校验器规则，不能升级为真实 Chrome/GPU 性能或后端验收。

### 文件契约

所有摘要使用 SHA256 小写 hex。对象摘要由 `scripts/gfx/gfx_evidence.py` 的 `digest()` 定义：
UTF-8 JSON，键排序、无空白、不转义 Unicode、不允许 NaN。输入指纹由同模块
`input_fingerprint` 定义，覆盖实际 timeline 文件字节摘要、全部 gfx HTML 与声明依赖的路径和
完整文件内容摘要。不声称发现未声明或网络依赖；实验运行器须提前把这些输入冻结并声明。

计划对象：
- schema = `vtx.gfx_comparison_plan/1`
- input_fingerprint：冻结输入的指纹
- required_instances：非空对象数组，每项 src、正整数 dur；无重复，必须等于 timeline
  的完整 gfx `(src,dur)` 集合，不允许漏页、漏时长或偷偷缩小验收范围
- variable_axes：事前声明的实验变量字符串数组；仅允许 backend_id、effective_backend、
  chromium_version 及具体 config.KEY。字体、输入、视口、色彩不能放行
- arms：恰有 baseline、candidate 两对象，各含 run_id、backend_id、effective_backend、
  chromium_version（完整实测版本字符串）、config（键集合相同的配置对象）

每臂回执对象：
- schema = `vtx.gfx_run/1`，complete = true，run_id 与计划对应
- plan_sha256、input_fingerprint：绑定冻结计划和输入
- config：实际生效配置，与该臂计划精确一致
- env_manifest：#102 `vtx.env_manifest/1` 对象；其中 run_id 与该臂一致，
  backend_id 与 runtime_backend_truth.backend_id 一致；env_sha256 绑定完整 manifest
- #48 的可选生产环境绑定字段与可信边界见[渲染环境身份](#gfx-environment)。
- instances：与计划完全相同的实例集合，每项含 src、dur、frame_dir、frames
- frame_dir：该实例的独立绝对帧目录，可映射隔离缓存，不需要改 HTML 换键
- frames：恰好 `0000` 到格式化的 `dur-1`（最小四位）的对象映射；每项含
  sha256（解码 RGBA 像素摘要）、run_id、origin
- origin：基线只允许 rendered；候选允许 rendered 或 static_copy，拒绝 cache_hit、
  未知来源、其他运行的帧。static_copy 还须 head_frame、static_status=verified_static；
  头帧必须是同实例同运行、编号更小的 rendered 帧。候选复制帧和头帧、独立基线对应
  两帧都须逐帧像素一致。declared_static 不能充当验证证据，复制链不被接受

基线 Golden 沿用 build 格式；meta.run_evidence 嵌入基线回执，meta.env_manifest
必须等于回执中的 manifest。build 的可选 --run-evidence 只嵌入已有回执，不认证其内容；
验收时仍须通过所有检查。旧 Golden 缺少身份可继续普通 compare，但不能严格验收。
每个必需实例必须 mode=full 且 frames 精确覆盖全区间。验证重新读取独立基线与候选 PNG，
检查 PNG 格式、实际视口、完成摘要与 Golden 摘要。候选不能复用基线路径或同一个 inode。

### 环境与结论

每臂实际生效后端、完整 Chromium 版本、配置须符合该臂预声明；内部接口证据来源与
必需键按 #102 校验。缺失 manifest、后端静默回退、未声明变量变化均失败。
两臂字体内容指纹、srgb 色彩、视口以及 OS/CPU/显示/GPU 驱动记录须一致。
被声明的后端或配置变量可以不同；这是受控比较策略，不改变 #102 严格一致性接口。
若 Chromium 版本差异事前声明，仍可收集像素比较结果，但报告列出两侧实际版本与混杂项，
ok=false、same_version_causal=false，不自动授予同版本因果或任何复现等级。

报告包含实际环境、run_ids、plan_sha256，hashed_frames 为成功解码摘要数（两臂合计），
compared_frames 为两臂均通过完成摘要核验后实际比较的帧对数。差异、缺失/不可读（含完成
摘要不符）、未知来源、未覆盖实例分列表与计数；存在上述任一问题均不能通过。
完整且逐帧一致的独立运行才 ok=true。报告同目录临时文件加原子替换，失败不写成功结论。

### 严格证据的可用性要求

运行时后端分类另从实测字段复核，不能只把 effective_backend 声明值复制到计划。
CDP 使用 system_info.gpu.featureStatus.gpu_compositing 与 auxAttributes.glRenderer；
Electron 使用 gpu_feature_status.gpu_compositing 与 gpu_info_complete.auxAttributes.glRenderer；
CEF 使用 gl_probe.chrome_gpu_features.Compositing 与 gl_probe.webgl.renderer。
明确的软件合成状态可证明 software_skia；SwiftShader、Vulkan、NVIDIA 硬件的分类须有对应
实际 renderer 字符串，硬件分类同时须有硬件合成状态。未知、矛盾或当前分类器不支持的
观测失败关闭，不从启动模式、设备清单或运行参数猜测。各 source 的必需原始对象不能为空；
完整 Chromium 版本须与 CDP product、CEF cef_version.chromium 或 Electron chrome_version 一致。

OS 须有 system/release/version/machine，CPU 须有非 unknown 型号；显示证据须有完整环境
记录及 session_type。无显示器须显式记录 none 与空环境；X11 须有成功 xdpyinfo，Wayland
须有 WAYLAND_DISPLAY。GPU 须有非空设备名、驱动及正容量记录；纯 software_skia/SwiftShader
运行可显式使用 {"status":"not_applicable","reason":"software_backend"}，不能把 null 或
unavailable 当控制项已核验。#102 采集器的 unavailable 仍原样保留，严格验收不能静默放行。

输入指纹绑定实际 timeline 文件字节 SHA256，验证前后重读并与 Ctx 载入字节对账。
帧文件身份从解码所用的已打开文件描述符取得，基线和候选全部实例的 inode 集合须不相交，
包括错编号、跨实例硬链接和符号链接。缺实例及缺帧计数分别包含回执与 Golden 的遗漏。

路径解析错误（含帧根或 timeline 符号链接循环）归入失败报告，替换此前成功报告。
report_written=true 表示报告已成功提交；报告目标不可写或其父目录循环时，退出非零并在
标准输出返回 ok=false、report_written=false 和写入错误，不声称覆盖了不可访问的旧报告。
消费方须同时检查本次进程退出码及报告提交结果，不能忽略写入错误而读取旧文件。
