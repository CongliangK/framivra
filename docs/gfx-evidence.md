# Golden 独立运行证据

`scripts/gfx_golden.py verify-experiment` 是 #62 的独立严格验收入口。
普通 `compare` 与旧 `--strict` 的抽样、mtime 语义保持兼容；它们不能证明独立实验。
`verify-experiment --help` 列出必需文件参数。执行会原子替换指定报告文件。
#102 的 `verify_manifest_compatibility` 不变，不能用忽略环境差异的开关代替它。

## 生产者与信任边界

实验运行器必须在两臂启动前冻结计划，分配不同 run_id，保存计划规范化 SHA256；
每臂渲染器捕获实际运行环境、逐帧来源和完成摘要，只在全部成功后提交 complete 回执。
本工具检查证据一致性，不是签名、时间戳服务或对恶意生产者的真实性证明。
从旧缓存重新扫描、touch、把旧回执改名或补一个 run_id 都不是合法的证据生产流程。
生产渲染器尚不生成此契约；#107/#118/#119 的运行器接入与实机验收是独立义务。
合成 PNG 测试只证明校验器规则，不能升级为真实 Chrome/GPU 性能或后端验收。

## 文件契约

所有摘要使用 SHA256 小写 hex。对象摘要由 `scripts/gfx_evidence.py digest` 定义：
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

## 环境与结论

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

## 严格证据的可用性要求

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
