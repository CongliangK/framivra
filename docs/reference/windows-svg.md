# Windows SVG：生产入口与内部验收边界

## 普通 SVG-sequence 工程

Windows x64 原生支持多轨、多剪辑的静态 SVG-sequence；允许配合普通本地音视频，不支持同一时间线混用 HTML 与 SVG 图形轨，也不支持字幕、激活插件、GPU、草稿/区间合成或打包缓存。保持 `core` 模式。未配置或身份校验失败时拒绝，不查 PATH，也不回退内部验收。

- `VTX_WINDOWS_SVG_PORTABLE` 指向官方 Inkscape 1.4.4 x64 便携解包根（含 `inkscape`）；`VTX_WINDOWS_SVG_SCRATCH` 指向已存在、仅可信账户可写的短 ASCII 暂存根，不能含 `;` 或 `%`（媒体阶段）。
- 时间线交付、严格 QC 和单帧预览另需 `VTX_WINDOWS_SVG_MEDIA_BIN`，指向字节锁定的 Gyan FFmpeg 9.0.2 full 静态版 `bin`。工程、工具、暂存根必须位于可信的固定 NTFS 且互不重叠。普通源须为工程内精确相对名的单文件 WAV、MP4、MOV 等[支持格式](<../../vtx/render/gfx_svg_win_media.py>)；不接收外部路径、播放列表或网络源。
- 字体可为空，或声明工程内可信的 TTF/OTF/TTC/OTC 文件。每组字体独立暂存和查询，使用已绑定的字体字节和私有 Fontconfig，不回退系统字体。相同输入可以复用认证帧；使用不同字体或更换字节会改变缓存身份。

`python -m vtx -t <timeline.json> gfx` 写 `cache/` 和 `out/receipt.json`；若计划随后单独运行 `video`，请从 `gfx` 起保持同一媒体工具配置。`all` 写缓存、回执和成片，执行原有严格 QC；只栅格化可用 `gfx`，没有可测音频的工程不会因此自动满足成片 QC。`qc --strict` 重新验证当前已认证成片及需要的参考画面，不重新合成。单帧可使用 `timeline frame --render --include-png --prepare-gfx`：只在私有暂存中准备当前帧并内联返回 PNG，不写工程缓存或正式回执；它不表示连续预览或压缩成片逐像素一致。

原生[运行时](<../../vtx/render/gfx_svg_win_runtime.py>)锁定官方包、私有字体配置和通过离线目录签名验证的系统文件。[发布](<../../vtx/render/gfx_svg_win_publish.py>)在缓存页锁下验证或发布 PNG，最后写完成标记。完成标记不是跨信任边界的签名；Job 监管进程所有权，不证明映射内存字节，也不是权限沙箱。

## 锁定 portable-v2 主片的 `all`

当原始时间线与 portable-v2 完全一致时，采用锁定配置，额外核对清单、逐帧字节、字体、声明依赖和两份 WAV。任何锁定输入验证失败都不回退到普通配置。三个环境变量仅为当前操作设置。

从源码按[主片准备说明](../../templates/demo-timeline-svg/README.md#准备与正式运行)执行。PowerShell 示例（`$Project` 指向可信固定 NTFS 上一个全新、仓库 / HOME / 缓存外的目录，`$Fonts` 指向四份锁定字体所在目录；父目录和私有暂存必须已存在）：

```powershell
python -X utf8 scripts/demo.py preflight --fonts-dir $Fonts
python -X utf8 scripts/demo.py prepare --output $Project --fonts-dir $Fonts
python -X utf8 scripts/demo.py verify --project $Project
python -X utf8 -m vtx -t "$Project/timeline.json" all
python -X utf8 -m vtx -t "$Project/timeline.json" qc --strict
```

检查预检的 `prepare_ready`，并在任一后续步骤非零退出时停止。预检不启动渲染器；`prepare` 写入工程并执行真正的产品 `validate`，`verify` 只查输入；`all` 写缓存和封装并默认做严格 QC。`all` 仅限 CPU 完整输出、不运行插件；阶段串行，视频分段仍由 `VTX_VIDEO_SEGMENTS` 控制。单独的 `qc --strict` 要求当前指纹下成功的封装回执及两份有证书的媒体（终片和 QC 参考），重新测量并写 QC 报告与回执，不重建媒体；不匹配时请重跑 `all`。

帧缓存丢失或已有常规文件的内容、完成标记损坏时，`all` 在缓存页锁下重建；遇到链接、路径别名或共享冲突仍拒绝。FFmpeg 在私有 Job 中处理被锁定的素材、认证帧和中间媒体；私有拼接清单只引用已认证段。媒体出 Job 后完整解码并提交，暖认证绑定目标、输入和工具，QC 回执绑定本轮实际终片和参考视频。工程、工具与暂存根须由可信写者控制；这些检查不提供权限沙箱，也不开放通用 Windows 工程合成。

## 内部证据

- Windows SVG：[生产身份](<../../vtx/render/gfx_svg.py>)未获验证即在 PATH/缓存查找前拒绝，不回退内部验收；[锚读](<../../vtx/render/gfx_svg_win_input.py>)仅认授权稳定根及直连固定 NTFS，原始相对声明先验后并集（`/`、精确路径），逐级拒重解析、同句柄限量验单链接。
- [Windows SVG-only 回执](<../../vtx/cache/cache_state.py>)的清单和声明依赖字节沿用项目根句柄读取与限额；缺失、拒读或无法读取均直接失败，不记 MISSING 或回退；不是原子指纹。[影响分析](<../../vtx/inspection/impact.py>)用调用方原始根和精确相对名复核已观测的 SVG 文件，不以报表绝对路径代替，也不作原子快照。
- [Windows Fontconfig 检查](<../../vtx/render/gfx_svg_win_fontconfig.py>)私有 Job 以固定官方 DLL 快照查单 face；900 帧另核对私有字体集四文件的 22 个 face 及索引，结算后清空探针缓存再启动 Inkscape。DLL 与字体用各自只共享读的原句柄限量验单链接和摘要，复制只写已验字节；不证映射字节、渲染选字、加载闭包或产物资格。启动所有权或结算未证不读回执、不删暂存。
- [Windows SVG 快照](<../../vtx/render/gfx_svg_win_snapshot.py>)目录句柄拒重解析/物理重叠，验独占包树，不启进程。[内部验收](<../../vtx/render/gfx_svg_win_shell.py>)固定官方 `.com` 验几何/锁字体单帧、180 帧几何及 portable-v2 `f0135`/900 帧（句柄锚定、锁 SVG/字体）。900 帧锚读锁定时间线/清单及固定声明依赖（声明不选路径、非原子），生产静检、同快照查暂存字体 face；同上下文验版本/资源根及首个 stdout 整行后的根进程活映像路径（包 bin 精确匹配整树验真并持有的只读文件，System32/WinSxS 默认不验文件身份）；同原句柄逐模块用独立卷映射核对 loader 名与 VM 映射名，只在原枚举基址查元数据，要求已提交映像及分配/区域基址一致；对获验的 System32/WinSxS 相对名称集合返版本化摘要；仅可信内部审计可传入冻结字面清单，[验收](<../../vtx/render/gfx_svg_win_system.py>)在首个 worker 前持有系统盘文件并限量验字节，结算后比对路径集合及按原句柄复核，不更新信任；逐帧验像素，仅返摘要。Job 结算未证不读结果、不删操作目录；路径相符不证映射对象或字节、字形选择、瞬时/子进程/最终加载、成片、缓存或发布资格。
- [Windows SVG PE](<../../vtx/render/gfx_svg_win_imports.py>)锚读已验包树固定根的 AMD64 普通/延迟声明（非绑定 IAT），可精确匹配 bin 候选导出。转发链另限调用方逐边选定的原句柄；无选址回退、循环或歧义即拒，不授加载闭包、缓存或生产资格。
- [Windows SVG 本机目录签名](<../../vtx/render/gfx_svg_win_catalog.py>)仅限可信内部 900 调用方按冻结磁盘范围逐文件指定 CatRoot basename 和原句柄 SHA-256 Authenticode 摘要，可选签名者 DER 指纹；首个 worker 前以离线 WinTrust 验成员，不枚举目录签名；传目录签名路径而非句柄，不证提供者重读成员，不查吊销或授产品、缓存、QC 资格。
