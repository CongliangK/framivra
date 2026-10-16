# 缓存与回执：输入变化必须使旧结果失效

> 本文是运行时媒体身份、缓存失效与回执语义的唯一 home。

## 修复的问题

原缓存按文件存在、帧数或 HTML 时间戳判断命中。同路径替换同长素材、修改字幕，
或两个目录使用同名 HTML 时，旧声音/画面可能继续被输出。失败的重跑也可能留下
旧 QC 成功回执。

当前机制：

- **gfx 页缓存** `cache/gfx/<key>/`：键 = sha1(html 字节 + 显式依赖内容 + fps +
  视口 + 时长)。同内容跨路径复用；同名页不同内容、同页换时长自然分键。
- **键轴边界（诚实注记）**：渲染驱动/算法轴（Chrome 升级、渲染算法变更）与行为开关轴
  （NAVFREE/STATIC/VTX_GFX 引擎选择）不入键——同键不同生成路径的理论风险以人工
  `vtx clean`/`vtx gc` 为补救（R12-G1-037 交界认知在案）；段中间物 `vseg_pass` 已入
  gc 白名单（commit fa650ad）。
- **seg 预编码** `cache/seg/<key>.mov|mp4` 与 **抽音频** `cache/audio/<key>.wav`：
  键 = sha1(路径 + Verified 完整内容身份 + 切片/窗口/fade/编码参数)。
  媒体身份使用下节定义的独立 schema/domain，不沿用 Fast 键。
- **分段渲染段缓存** `cache/vseg*/seg…_<内容键>.mp4`（PR#1 合入融合）：段文件名
  携带内容键（段区间 + 画布/模式/gpu + 每层上游内容键与参数 + 段字幕 ass 字节）。
  改字幕/改图层/换 gfx 内容后旧段不再命中——PR 回归场景「改字幕不重渲」关闭。
  段数（nseg）与 GPU 开关由区间和键内 gpu 位天然隔离（PR 的 tag 变体方案被此取代）。
- 键变即新条目，旧键是垃圾由 `vtx gc` 回收；`vtx df`/`vtx snapshot`/VERSIONS
  记账不受影响。

## 媒体身份等级与复用边界（#85）

- Fast：公开 `media_stat_digest` 保留 size:mtime_ns:首尾采样摘要格式；适用快速
  交互预判，不给正式 seg/audio、响度、阶段回执或 QC 免检授权。
- Verified：`framivra.media-identity.v1` 记录 level=Verified、algorithm=sha256、
  scope=complete-file、digest；按块完整读取当前文件，不以 stat 或旁车证明内容不变。
  完整摘要仅说明读取范围的字节身份，不证明视觉质量、环境一致或并发不可变性。
- Reproducible 还需环境身份及独立重复比较，不能从 Verified 自动推导。

选择 Issue #85 后续澄清允许的全量重读正确性路径，不建设受控不可变导入平台。
每个源回执指纹、seg/audio 键、响度查找与 QC 免检边界均重读；同一次读取所得摘要
可供该快照的身份和序列化使用，不跨可变文件边界缓存。Ctx 锁定源身份并在现有阶段、
成功回执、发布等复核点比较；不删除原完成凭据或输入校验。项目输入在命令期间应
保持不变，遵循 SECURITY.md 的单用户本地边界，不扩展 #59/#97 的并发安全范围。

源 manifest 随 `framivra.receipt.v2` 回执的 source_identities 原子持久化，含各路径
完整身份；这是绑定 input_fingerprint 的溯源，不是免读令牌。只读和 dry-run 不为身份
另写旁车。df 与所有 dry-run 使用未激活身份的只读/规划上下文，离线或不可读源不
妨碍这些命令；其 input_fingerprint 为 None，不得继承回执或认证产物。正式构建仍
在 Ctx 初始化时验证；源缺失或读取失败拒绝可信键，不能编码为“确认无依赖”。空 sources 才是
明确无源媒体；未声明依赖仍未获证明。旧 schema 回执清空阶段表，旧 Fast 键失效重建。

**validate 失败证据边界**：普通 text CLI 与 agent-json 的直接 validate 可在同一次
加载遍历中收集缺失/不可读取源，继续原生校验以报告其它真实错误。该上下文没有
input_fingerprint 或 Verified manifest，永久仅可报告失败；不会开启正式阶段事务，
也不写失败 receipt、receipt.prev、QC 记忆或响度回执。原有成功回执不会被这条失败
报告路径改写，但后续正式命令必须独立重读身份，不能据此继承。其它检查全部通过
仍非零拒绝认证，源随后出现也须重新创建正常上下文，不能原地升级。缓存键、其它
正式阶段及成功回执均拒绝此上下文；插件回调仍遵循本地单用户信任边界，并非沙箱。
不新增公开诊断模式，不按 diagnostics 开关降级：完整输入的正式命令仍为 Verified。

**兼容取舍**：audit/QC 不享有此失败证据例外。旧版声明缺失但未使用源时可能以
MISSING 指纹完成 audit；现在拒绝该不可信成功。未知源的 validate 失败不再留下
正式失败回执/撤销交易。此为 #85 的明确迁移，不承诺未知输入上的所有旧退出码、
副作用或 audit/QC 诊断顺序相同。公开 Fast 仍有中部字节盲区；#52 impact 快照的
输入摘要仍标 Fast，但其调用的正式 audio/seg 键现在能识别该变化，预测的新缓存
产物应随之改变，不能把 Fast 限制误当成正式键仍可复用。

响度使用独立 framivra.loudness.v2 键域及完整 identity；缺失/旧身份不命中，读取失败
不存回执，测量前后身份不一致不存结果。QC version=2 记录完整 final 与当前生效段
身份及实际解析路径；concat 路径按现有生产 writer 的绝对 POSIX/引号转义格式
解析，相对路径以清单目录为基准，非预期本地段目标拒绝免检，不用 basename 替代
实际目标。旧/损坏/不可读记录全检；免检仍需该检查上轮 pass 及相关段内容一致。
验收包 vtx.acceptance/2 的 receipt_identity_scope 限定 declared-source-files；
v1 包只按其 Fast 声明独立验证，不升级。
最终成片 scope 的 content_identity 仍为 partial，未声明依赖及其他输出未被统一证明。

**未满足的验收项**：原“非每次渲染全文件读取”要求在本实现中未满足；真实 ≥1GB
项目冷启动成本门尚未测量，不能以单测或单次 SHA 吞吐宣称通过。须在独立审查后按
实际渲染全路径记录重复读取次数、字节、关键路径增量及持久化成本，再决定性能或
受控导入后续工作。这里的正确性候选不构成 #85 完成或性能验收。

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
浏览器、系统字体及渲染环境身份属于独立 #48，当前不声称覆盖；真实 Chrome 请求、
像素变化及失效对账仍为必须的验收门，静态/mock 测试不能替代。

Golden 使用原始完整 clip 重建身份；旧 Golden schema 无法表达同 src/dur 的多个
不同依赖身份，因此明确拒绝歧义。输入在一次命令期间应保持不变，同一项目不要
同时运行多个写入命令。升级渲染环境后需按既有清理流程重新构建。

## 阶段回执（PR#1）

`receipt.json` 绑定 input_fingerprint = sha256(timeline 字节 + 源 Verified 身份 +
gfx html 字节 + 声明依赖内容)——与缓存键同等敏感（任何缓存键会变的输入变化，
回执门同样重置）。输入变化不继承旧的
成功状态。CLI 在阶段开始前撤销其旧成功及下游成功，失败重跑不能保留旧 QC PASS。
audio 要求当前 prepare，video（有 gfx 时）要求当前 gfx，mux 要求当前 video/audio，
qc 要求当前 mux；单跑某阶段前置不满足会直接拒绝。可以直接运行 `all` 完成依赖顺序。
旧工程首次需完整重建一次；不直接认领旧产物。

历史缓存保留以便恢复和审计；工具的显式 `clean` 仍删除项目 cache/ 与 out/。本次不
自动删除用户旧工程或媒体。回执是流程来源检查，不是对最终文件外部篡改的完整性证明。

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
运行时源媒体采用上述 Verified 身份；final_movie 的 content_identity 仍为 partial。
QC 报告和回执中的技术门可以 passed，
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
