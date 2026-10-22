# 项目验收包

使用 `vtx.inspection.acceptance` 为项目构建独立证据包，再由接收方复核媒体、输入和构建记录；此入口不改变渲染 CLI 或缓存键。
`vtx.acceptance/1` 导出媒体、输入和构建证据。[源码冻结验收](../releases.md#冻结验收与发布)入口
`scripts/release/release_acceptance.py` 调用本模块构建、复核独立包并执行输入/产物篡改负例；
这说明源码中的接线，不代表当前提交已经执行或通过发行验收。公开 Demo 的操作入口与证据边界见
[standard Demo 运行指南](demo-fixture.md#公开-standard-demo-生产运行)。

```bash
python3 -m vtx.inspection.acceptance --help
python3 -m vtx.inspection.acceptance build --help
python3 -m vtx.inspection.acceptance verify --help
python3 tests/runners/acceptance_fixture.py --help
```

写操作：build 的两个位置参数为 timeline 文件、新建包目录。包目录父目录须已存在且可信；禁止覆盖、复用、符号链接、父级穿越和源项目内输出。输入只接受项目内相对普通文件。脚本 acceptance_fixture.py 的位置参数同样是新目录；它用公开的 testsrc2/sine 生成 2 秒 CPU 样例，然后走正式 Framivra all 至 mux、qc --strict，保留 anchor.json、verification.json 和真实包的 tamper-results.json（媒体、输入副本、manifest、保持 stat 的当前输入四类破坏负例）。不是 FFmpeg 直接生成最终交付件。失败日志也保留，不将失败包装为成功。

## 格式与复核

- manifest.json：schema、UUID run_id、UTC 时间、timeline、inputs、dependency_closure、build、gates、files、accepted。
- inputs/：原始 timeline 字节、sources、gfx src、cache_dependencies、subtitles_src。每项包含相对路径、角色、完整 SHA-256/size、导出身份和运行期身份等级。
- artifacts/：存在则收录 final.mp4、receipt.json、qc_report.json、audio_report.json；缺失不捏造。
- environment.json：Python/Framivra/平台、git commit/工作树状态、内核 Python/JSON 文件摘要、ffmpeg/ffprobe 原始版本输出。只记录这些字段及显式参数覆盖，不转存进程环境或凭据。渲染子进程仅继承 PATH、临时 HOME/XDG_CONFIG_HOME、固定 locale、Windows 必需 SYSTEMROOT 及显式覆盖；其余 VTX 参数不继承；VTX_CONFIG 指向临时空配置，配置内容和摘要随包记录。版本探针、fixture 源生成和渲染统一采用同一环境白名单，不继承 FFREPORT 等变量。没有浏览器/GPU 内部探针，runtime_backend_truth 明示 unknown。
- build.log：两次实际命令及合并 stdout/stderr。build 保留命令、执行退出码、CPU 参数及输入稳定性。结构化命令用 $PROJECT/<workspace_timeline> 表示可搬迁的项目根；该相对文件名必须与 inputs/ 下打包 timeline 一致，原始日志仍保留实际临时路径。
- files：除 manifest 自身外所有包文件的完整 SHA-256 与 size；未登记文件、丢失文件及内容变化均拒绝。

如需核对构建与子进程事件的关联，见 [执行事件的验收桥](../reference/execution-events.md#acceptance-bridge)。关联完整性与包完整性是不同结论，缺失事件不能推定运行成功。

build 返回的 manifest_sha256 必须由接收方独立留存；verify 的必填 --manifest-sha256 使用该值。包内放一份摘要不能自证。可选 --source-root 指向原项目，另复核当前输入；未传时只能复核包内输入副本，结果显式标注 live_inputs_checked=false。复核不执行包里的命令。

版本化元数据拒绝未知字段，执行序列/逐命令退出码须与整体退出码一致；缺 Python/Framivra 版本拒绝。命名工具探针须匹配精确命令，成功/失败结果与异常分支字段互斥且完整；unknown 也不能缺失错误或执行结果。存在 receipt 时，两侧 input_fingerprint 必须为相同的 64 位小写 SHA-256；只有失败且无 receipt 时允许 null。源 Fast token 的大小与采样摘要由包内字节复核，mtime 仅为历史记录。回执扩展阶段保留，禁止与包保留门名称冲突。

每个阶段和附加门均有 passed/failed/skipped/unknown；原始 QC checks 和 skipped_probes 全量保留。缺 QC、工具版本未知、进程失败、输入在构建期间变更均不能通过。结构 QC 不等于全片视觉正确：无独立视觉 oracle 时 visual_content 固定 unknown，accepted=false。CLI 退出码 0 仅用于所有门通过，2 表示证据包可复核但验收未全过，1 表示格式/完整性错误。当前生成器不提供视觉 oracle，因此正常 CPU 样例也返回验收未全过；fixture 脚本仅对真实管线/结构 QC 的预期结果返回 0。

## 信任与范围

完整导出摘要是每次导出重新读取的包完整性证据。运行期 sources/receipt 仍标 Fast；其他小文件内容身份按现有实现标 Verified，不把包摘要倒推成运行期 Verified 缓存。身份语义与限制见 [缓存与回执](../cache-provenance.md)。本工具不提供持久化身份缓存或真实性能验收。

依赖发现限于现有回执声明面；HTML 间接引用、系统字体、插件及外部资源闭包 unknown，self_contained 固定 false。仅证实声明输入的稳定性，不声称完整复现。源码 commit、脏工作树及内核文件摘要用于定位实际构建，不把脏树冒称纯 commit。

外部摘要只锚定接收方所见字节，不能证明生产者诚实、日志真实或输入确实被渲染使用；没有签名、可信执行证明或 GPU 验收。输入/输出父目录须在执行期间静止可信；静态拒绝符号链接不等于抵抗恶意并发文件系统替换。新目录中断后保留未完成证据，不生成成功 manifest，不允许在原目录重试。
