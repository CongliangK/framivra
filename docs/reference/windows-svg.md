# Windows SVG：生产入口与内部验收边界

## 受限 `gfx` 配置

Windows x64 原生配置只支持 `gfx`、单个 SVG 剪辑、无普通素材/字幕/插件、`core` 松散帧缓存。字体可为空，或按标准 portable-v2 清单的顺序声明锁定的四份 Noto/DejaVu 字体；不接受任意字体。带字体需要完整源码检出或受管 editable 安装提供基线锁，单独 wheel 不提供此配置。不开放任意工程的 `all`、单帧预览或打包缓存。未配置时仍拒绝生产准入，不查 PATH、不回退内部验收。

运行前用本次进程环境设置 `VTX_WINDOWS_SVG_PORTABLE`（官方 Inkscape 1.4.4 x64 便携解包根，含 `inkscape` 子目录）与 `VTX_WINDOWS_SVG_SCRATCH`（已存在、仅可信账户可写的短 ASCII 暂存根）。工程、便携包与暂存使用可信的固定 NTFS 根，暂存与工程/包不得重叠。工程产品根也必须由可信写者控制；完成标记不是可跨信任边界验证的签名。`python -m vtx -t <timeline.json> gfx` 会启动受监管的 Inkscape 并写 `cache/` 与 `out/receipt.json`。正常暖用仍重做运行身份预检，但不执行帧导出。

[运行时](<../../vtx/render/gfx_svg_win_runtime.py>)锁定官方包、私有字体配置、经离线目录签名验证的系统文件和原句柄。[发布](<../../vtx/render/gfx_svg_win_publish.py>)在同一缓存页锁下锚读验证或写入逐帧 PNG，最后提交完成标记，写回执沿用原指纹及撤销语义。加载事件约束实际 Job 的文件身份，不证明映射内存字节；Job 是进程所有权边界，不是权限沙箱。

## 锁定 portable-v2 主片的 `all`

在上述两个 SVG 根之外，设置 `VTX_WINDOWS_SVG_MEDIA_BIN` 指向已验的 Gyan FFmpeg 9.0.2 full 静态版 `bin`（固定 `ffmpeg.exe` 和 `ffprobe.exe` 字节）。三个变量仅为本次主片操作配置；不要在普通项目的环境里全局设置。暂存路径也不能含 `%`，且不得与媒体工具目录重叠。只接受 portable-v2 的原始时间线、帧、字体、声明依赖和两份 WAV。

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
