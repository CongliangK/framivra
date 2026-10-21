# MG 模板验证记录

本页保留原研究/复审轮次及其冻结源码、影片和未验收证据；当前目录与制作入口见[风格集合](../../../templates/mg-styles/README.md)，当前结论摘要见[状态矩阵](status.md)。历史路径与哈希不作为当前目录的操作命令。
验证环境：2026-10-20，Linux 云工作区；实现范围见 [模板操作入口](../../../templates/mg-styles/README.md)，来源与许可见 [研究记录](research.md)。本记录由独立验证席产出，技术结论不构成艺术审查或用户视觉确认。

## 当前验收边界

- 新增的十五款独立 HTML 页面最新冻结回归为 a29d82c 的 13 项（75.882 秒、40 文件哈希前后一致）；后续 f3ea10e 的 recipe/消费面轻量改动独审另计，未重复 suite。较早 legacy Canvas 原型另有源码/内容/帧状态静态验证；本环境浏览器仍阻塞，两条浏览器路径都未取得真实像素验收。不能将它们合称已完成成片的 HTML/Canvas 路线。
- 非浏览器十五种语义媒体路径已有各两套内容的完整技术记录；当前较优示例统一以 `/workspace/mg-style-evidence/preferred-media-index.json` 的实际路径/哈希为准，修订批逐项核对后更新。旧初始 14 种/28 片索引保存在 accepted-media-index-14-initial.json 与 temporal/accepted-media-index.snapshot.json，仅供历史溯源；其后完整静态审查发现的 skyline、路标消失、填色/阅读停留问题，不因旧 all/QC 通过而变成艺术终验通过。
- 逐帧 3D 旧完整两片已完成技术验收，原生 960×540/64 samples、1920×1080 成片上采样；其方块/环体穿地与外部重生成低位像素不一致保留。新 ground-fixed 两套完整 120 帧已完成源哈希、all/strict/全解码、全帧静态图审与独立固定资产消费重放；当前 preferred 为 15 款/30 片。新源码 f47 外部重生成 A 严格 RGBA FAIL、B 单例 PASS 分账保留。三底片 storyboard 与完整 Blender 序列分开记录。
- all / strict QC / 全片解码 / 帧数对账及静态全帧条带覆盖，与实时播放/听审、阅读节奏与用户观看确认分别记录。参考提示音未完成有意声音设计；本地媒体未上传 GitHub，未通过改门、改权限或禁用浏览器 sandbox 处理实际失败。

## 首批基线检查

- `tests/gfx/test_mg_styles.py` 六项测试通过：全部款式各用英语单条与中文特殊字符三条内容生成；检查 v2 时间线、整数帧、单页 clip、依赖文件存在；非法帧/内容拒绝且不创建输出；已有目录不覆盖；dry 不落盘。
- Node 模拟 Canvas 对全部款式检查帧 0、1、9、45、80、89 的绘制操作、非有限数值、save/restore 平衡及 45→89→0→45 乱序重放。操作序列复现通过。**模拟 Canvas 不实际绘制，不能证明像素确定性、字体适配或视觉质量。**
- 全部款式生成 90 帧、1920×1080、30fps 项目，真实 `vtx validate` 全部退出 0。

集成席另报告：模板与存量布局等联合测试共 16 项通过，文档门 7 项通过；此两项由集成席执行，验证席未重复运行。挂载复核 `/home/agent/.agents/skills/video/` 下 SKILL.md、assets、references、scripts、templates、vtx 全部 MISSING，不能报告 BAD=0；未修改挂载或权限。

本地原始日志在 `/workspace/mg-style-evidence/`：`unit.log`、`validate-summary.json`、各款 `*-validate.log`；带提示音最终输入见 `audio-validate-summary.json` 与各款 `*-audio-validate.log`。媒体及缓存未纳入 Git。

## 实际成片尝试与阻塞

对 `flat-vector` 执行完整 `vtx all`，**退出 1，未产出成片**。`all-flat-vector.log` 记录两项独立失败：

1. gfx 环境身份拒绝：`native ELF renderer required: /usr/bin/chromium`。本机命令是包装入口，未获得合格渲染环境身份。
2. 当次模板无音轨，音频阶段生成 `amix=inputs=0`，FFmpeg 拒绝输入数量。该问题已在模板生成器修正：加入原创确定性参考提示音，不修改内核或门禁。最终输入全部款式重新 validate 均退出 0；代表性 `flat-vector` 的 prepare/audio 分阶段退出 0（`prepare-audio.log`、`audio.log`），音频终态 I=-14.8 LUFS、TP=-4.4 dBFS。此分阶段结果不代表 mux/QC 或完整 all 通过。

浏览器失败的原始命令、退出码及当时 stderr 已整理在 `/workspace/mg-style-evidence/browser-failures.txt`（历史工具输出转录，没有重新启动）：仓库 `Browser(tabs=1).start()` 退出 1；`chromium --headless --disable-gpu --dump-dom about:blank` 退出 1；加可写 `--user-data-dir=/workspace/mg-style-browser-probe` 的探测退出 134。这些是应用/环境配置错误，**不是自动审批拒绝**。GitHub GraphQL 的 Forbidden 请求亦已暂停，未重试。

另以仓库 Browser 启动浏览器报 `chrome exited during startup`；使用可写项目外 profile 获取 Chromium stderr，指出 SUID sandbox helper 未正确配置、要求 root 所有者与 mode 4755。验证席未修改权限，未加入禁用 sandbox 参数，未请求提权；环境阻塞后停止浏览器启动。

因此 HTML/Canvas 路径未完成真实 PNG 渲染、同帧像素复现、抽帧艺术审查、黑场检测或成片 QC（非浏览器路径成果见下节）。不得将单元测试、操作序列或 validate 通过记作成片验收。需在具备合格原生浏览器与沙箱的环境补验；参考提示音应按创作要求替换为有许可的正式配乐或旁白。

## 非浏览器路径补验

扩展单元测试 `tests.gfx.test_mg_styles` 与 `tests.gfx.test_mg_captions` 共 9 项通过（11.449s，无 skip），日志 `/workspace/mg-style-evidence/unit-expanded.log`。字幕测试包含中文、语义 punchFrame、subject_rect、区间边界、实际 PNG 与 direct-seek 像素一致。

字幕 PNG 模块完成后，独立验证席以真实 Pillow RGBA 图像检查 0、1、15、29、30、45、59、60、75、89 帧：同输入同帧重绘的像素字节一致，中央主体保护区的 alpha 全零。原始像素哈希与墨迹边界在 `/workspace/mg-style-evidence/caption-pixel-check.json`。这是字幕层的实际像素检查，不外推到浏览器 Canvas 款式或最终合成。

后续成片可将 Blender/Pillow 已生成帧编码为独立媒体源，再由既有 timeline/vtx 完成剪辑合成；不使用 gfx 缓存内部 PNG 目录冒充成功回执。普通透明视频源需要 clip 的实际淡入/淡出参数触发既有 alpha 分段编码路径，源容器带 alpha 本身不足以保留 alpha；最终应以实际合成抽帧确认。

### 真实 3D 帧序列

独立验证席核对 `/tmp/mg-3d-real-30/` 的 30 张 PNG，文件 SHA-256 全部与 `evidence.json` 一致。实际 Blender 4.3.2 / Cycles CPU / 16 samples / 640×360 / 30fps。抽取 f0、3、9、15、24、29 制作并查看 `/workspace/mg-style-evidence/blender-real-contact.jpg`：真实实体、柔光与接触阴影成立；低分辨率低采样仍有明显粒噪，副标题与标签偏小，f3 的第一个球体与数值标签交叠。此序列支持真实 3D 路径成立，尚不足以声称最终艺术质量通过；不是 HTML 2.5D 页面的真实渲染。

### 像素任务真实成片

`/workspace/mg-style-evidence/deep/pixel/out/final.mp4` 已通过完整 `vtx all`（exit 0），1920×1080、30fps、300 帧、10.000 秒。`qc --strict` 8/8 通过，真实解码帧数 300；全片 FFmpeg 解码退出 0、日志无警告，blackdetect 无 `black_start` 区间。成片 715255 字节，SHA-256 `73163638f0f6bc86fbf02e88e0627f7f9ecd6f19db546d43902b124fbd9dbc7f`。原始日志、`validation-summary.json`、`final-probe.json` 与十帧精确抽帧接触表 `contact.jpg` 均在同项目目录。

曾因生成纯视频素材缺音轨被 `validate` 拒绝；已保留 `media/pixel-video-only.mp4`，将参考 cue 音轨附入新素材，视频流 copy，无内核修改。最终混音仍仅引用独立 cue.wav，未重复播放素材附带音轨。源素材编码/封装与时间线剪辑合成分别记账。

独立查看 f0、29、59、89、119、149、179、209、269、299：中文任务/动作/奖励文字可读，角色按跳跃、跨沟、低头、收集四段推进，进度最终点满并出现完成卡，无明显构图溢出或切黑。当前能力完成全片解码与静帧检查，**未进行实时完整视频播放或听审**；这份 Pillow 像素任务成片不替代原 Canvas pixel 页验收，也不代表其余款式成片通过。


像素成片执行路径的精确记录：命令为 `python3 vtx/vtx.py -t /workspace/mg-style-evidence/deep/pixel/timeline.json all`，cwd 为 `/workspace/framivra`；时间线只有普通 broll 媒体源，没有 gfx clip，gfx 回执为 `0 gfx clips`，后端为既有 FFmpeg 普通源视频合成。启动未添加任何 VTX 环境覆盖或浏览器参数，`execution-environment.json` 留存环境与命令。本次生成器 `render_pixel.py` SHA-256 为 `1542e2c4b5b5c5348087749e9686a7bc9196164369bac19fb3c99f170708430a`，生成时入口与创意输入哈希见 `source-receipt.json`。这是新增独立 Pillow 素材生成路径，**没有绕过浏览器拒绝，也没有使 HTML/Canvas 路径获得成功凭证**；浏览器阻塞仍在。

### 反应字幕初次合成（历史失败，修复版见后）

`deep/captions/out/final.mp4` 已生成，300 帧/10.000 秒/1080p；完整 all 与 strict QC 均退出 1、7/8 通过。唯一失败为上层全屏 clip 生效探针 f150：整帧差 3.3、墨迹覆盖 2.7%，未达到既有门槛。全片解码退出 0，blackdetect 无黑场；成片 SHA-256 `abbfff4bdc2f74637e80590c71bd8b41366f33cc786fe562372f77484854d618`，979362 字节。失败未被豁免或改门。

查看该项目 `contact.jpg`：中段反应字可读、未遮英雄；f269 反应“任务完成！”叠到背景奖励框原有文字，构图需修。保护主体不等于保护背景 UI。逐帧验证全部 300 张字幕 PNG 在指定主体区 alpha 为零，报告 `subject-alpha-check.json`；这不消除上述 UI 交叠。后续应据实际资产几何与画面信息区修复，而不是修改 QC 阈值。


### 真实 3D 合成技术预览

`deep/three-d-reviewed/out/final.mp4` 已完整 all 退出 0、strict QC 8/8；1920×1080、30fps、30 帧、1.000 秒。全片解码退出 0、blackdetect 无黑场。SHA-256 `40d7255fd3c79b7d0f79cb11ee1210fc26e6590523a8430d0078089cfd1b69be`，1228485 字节。实际 Blender 脚本快照 SHA-256 `be08a92f3e15db2e900e0280bc0e776ec5f8fac84183bf4cb81aaac6fe2d77a8`，8 samples，仅作技术预览，原始来源绑定见 `source-receipt.json`。

查看 f0、3、9、15、24、29 接触表：三对象的球体/金属倒角立方体/圆环材质区分可见，中文标题与标签存在，数值不再与跳起对象重叠。标签和副标题仍偏小，8 samples 的噪点可见；一秒片段不足以评估完整叙事节奏。此项证明真实 Cycles → 视频素材 → vtx 完整链可执行，不代表完整高质量 3D 成片或原 HTML 页验收。未实时播放或听审。


### 不同内容与最终检查补充

独立验证 `pixel-alt`：英文夜间投递任务，三步障碍顺序、配色、seed、奖励与中文四步不同；180 帧/6.000 秒，完整 all 退出 0，strict QC 8/8、全片解码退出 0、零黑场。SHA-256 `45059e94c1057b3b7cf81ba67573a9a7a07cbe10596647829c5defb8c19c9eff`，414362 字节；已查看其十帧接触表，动作顺序/英文奖励可读。两套像素任务证明相同渲染器可替换内容，仍未做实时全片播放或听审。

集成席最终报告：模板、字幕、像素、媒体几何与存量布局联合 27 项零 skip 通过（7.112s），原始日志 `deep/unit-complete.log`；此联合命令由集成席执行，验证席不冒领。

字幕新版实际资产边界为 alpha 联合 bbox 加留白，使用普通视频源 x/y 放回原坐标。独立验证 `captions-reviewed` 的 300 张 cropped PNG：按 `[600,364,1304,500]` 还原到完整透明画布后，每帧 RGBA 像素字节与原图全部一致，报告 `crop-restoration-check.json`。这表达独立字幕素材的实际空间范围，没有放大文字或修改 QC 阈值。末段重复反应 cue 已移除以保护奖励文字。

最终执行路径逐项源回执、创意输入、命令、时间线 SHA 与环境记录在 `deep/source-input-summary.json`，各项目 `source-receipt.json` 绑定生成时源码；源脚本此后修改不继承旧成片验证。首次 pixel 的音轨封装修复由验证席独立命令补上，因此其 `build_media.py` 旧哈希与最终共享编码器不同；`attach-source-audio-command.json` 留存具体修复，视频流没有重编码。

生成时源码哈希（按真实回执，不用最终源码倒填）：

| 项目 | build_media.py SHA-256 | 专属渲染器 SHA-256 |
|---|---|---|
| pixel | `cfea4641641b0ada70a72b18a6bd0a7ca484f904bcd91f8fd5ef2ed975163632` | render_pixel.py `1542e2c4b5b5c5348087749e9686a7bc9196164369bac19fb3c99f170708430a` |
| pixel-alt | `cb0511bab40c9f727c65f5b8d104a8de804df06c8a0693a2a5b50fff84eb0501` | render_pixel.py `1542e2c4b5b5c5348087749e9686a7bc9196164369bac19fb3c99f170708430a` |
| captions-reviewed | `5b7ed295f583e1e0db6a80a1acf58903d3376e27d4fea413fa449477ce6b7ec1` | captions.py `a57e0bc02fc22cba26d9f7bfe824103dc2542d6c598b8298f2408fbab7298fd3` |
| captions-alt-reviewed | `5b7ed295f583e1e0db6a80a1acf58903d3376e27d4fea413fa449477ce6b7ec1` | captions.py `a57e0bc02fc22cba26d9f7bfe824103dc2542d6c598b8298f2408fbab7298fd3` |
| three-d-reviewed | `cb0511bab40c9f727c65f5b8d104a8de804df06c8a0693a2a5b50fff84eb0501` | render_3d.py `be08a92f3e15db2e900e0280bc0e776ec5f8fac84183bf4cb81aaac6fe2d77a8` |


### 反应字幕修复版成片

`deep/captions-reviewed/out/final.mp4` 完整 all 退出 0、strict QC 8/8；300 帧/10.000 秒/1920×1080/30fps，全片解码退出 0，blackdetect 无黑场。SHA-256 `387af464989bcae51256ee4d1f64a27471e50f5b4ae067b7c941c0e9f400ef0a`，939725 字节。十帧精确抽帧接触表已查看：四个动作反应 cue 清楚、未遮英雄，f269/f299 的背景奖励文字无遮挡。独立艺术审查席另行查看同接触表，给出抽帧初审通过；没有实时声音或完整播放终验。旧版失败与旧哈希保留，不替换成新版成功。


`deep/captions-alt-reviewed/out/final.mp4` 也完成 all 退出 0、strict QC 8/8；180 帧/6.000 秒/1080p，全片解码退出 0、零黑场，SHA-256 `1c333bfd97de641a8f9ed339947307fc6f1b937cb2834fccf1a7a0c89be80bcf`，652226 字节。替换为英文夜间投递背景及三组更长中文反应 cue、不同入出点/逐字节拍/语义 punchFrame；十帧接触表已查看，文字完整、无遮挡，无重复奖励覆盖。两套背景、反应文案及节奏均实际渲染合成，而不是只改 JSON 后做静态判断。未实时播放/听审。


独立艺术复审对第二套字幕的补充：布局初审通过，但 f60 的“包裹拿到了”早于角色到第 2 个收集物中心，尚需按语义事件精校 cue 或改为预告文案。技术通过不代表所有文案时机完全校准；此问题留存，不将静帧抽查称作全片艺术终验。


### 第二套字幕最终语义修复

最终交付证据采用 `deep/captions-alt-final/out/final.mp4`，将第二 cue 改为“包裹就在前面，继续前进！”；时间窗与逐字节拍不变，消除旧版在收集前宣布“拿到了”的问题。已重新生成源素材并完整 all 退出 0、strict QC 8/8，180 帧/6.000 秒/1080p；全片解码退出 0、零黑场。最终 SHA-256 `68777b2f3cf228d7cb303d46beb04f696ee15aae12b9406469c6989dbb124e0d`，664204 字节。十帧接触表已查看，预告文案完整，角色与奖励文字无遮挡；旧案例和审查意见保留，不倒填旧片通过语义修复。

该轮生成器快照 `build_media.snapshot.py` SHA-256 `acb3dbbc5fe2d76a78e35b778c003bc0ef035333ba9d218341e9dbeed8922c7c`；字幕渲染器 SHA-256 `a57e0bc02fc22cba26d9f7bfe824103dc2542d6c598b8298f2408fbab7298fd3`；创意输入 SHA-256 `31ace5d8c7d465b55a5a05c92063468dc9663bc02e3e196dc3c7ef2b43b9fdb1`，绑定见该项目 `source-receipt.json`，完整命令与无 VTX 覆盖/无浏览器参数记录见 `deep/source-input-summary.json`。180 张 cropped PNG 放回 asset bounds `[368,364,1530,502]` 后 RGBA 字节逐帧与原图一致。

最终联合检查由集成席执行：27 项零 skip 通过（6.729s），日志 `deep/unit-release.log`。另独立核对 `deep/three-d-final-alt` 的 3 张 640×360/8 samples PNG 与 evidence 哈希一致并查看接触表：单橙色金属 cube、stage 布局与中文替换成立；这是不同内容三帧验证，不是第二支 3D 成片。

## 第三批验证准备与阻塞精确记录

GitHub Draft PR 创建动作的已知结果如下（历史输出整理，未重试）：

```text
gh pr create --draft --base main --head codex/mg-style-templates --title 'feat(templates): editable MG style source batch (render acceptance pending)' --body-file /tmp/mg-pr-body.md
Post "https://api.github.com/graphql": Forbidden
exit 1
```

上述工具输出没有提供 HTTP 状态码或响应码，不能推断或写作 403。Draft PR 创建未获成功确认，GitHub 操作保持暂停。它与 Chromium 的应用配置错误分别记录，均不能表述为已经通过。

Blender 去噪能力只读探测：`/workspace/mg-style-evidence/blender-capabilities.log`，exit 0，4.3.2 / Cycles 可用；当前 denoiser RNA 枚举为空，默认值伴随无匹配枚举告警。既有真实渲染失败原文为 `Failed to denoise, build has no OpenImageDenoise support`，日志 `/tmp/mg-3d-denoised.log`。因此本机不能宣称已启用 OIDN/OptiX 去噪；保留 denoise=False 与固定 seed，可提高采样完成质量验证。默认 adaptive sampling 已开启，阈值约 .01；未据此开展性能优化或更换渲染器。浏览器仍暂停，没有再次启动。

### 3D 章节叙事与高采样关键帧

第三批冻结脚本 SHA-256 `c74c3662212fbc430c33267396289335328ba49b12a2691150acb704eb79a64a`，两套内容各实际渲染四帧，Cycles CPU / 256 samples / 640×360，denoising=false。A `/tmp/mg-story-final-a/` 的帧号为 `[0,84,180,239]`，B `/tmp/mg-story-final-b/` 为 `[0,75,180,239]`。独立核对八张 PNG SHA-256 全部与各自 evidence.json 一致。证据明确 `frames=240` 是作品时长而 `complete_sequence=false`、`rendered_frames` 只列上述四帧，**不能称作两支八秒完整成片**。

已查看独立接触表 `third-3d-keyframes-a.jpg`、`third-3d-keyframes-b.jpg`：scatter → assemble → result 三章的对象关系有变化，连接为真实 3D 连杆；三对象与五对象、stage/arc、文案、材质、关系图与结尾策略可替换。标题、章节、数值与标签未见交叠，高采样材质细节比早期低采样预览改善。关键帧抽查不覆盖过渡全段与音画终验。

所用本机 `NotoSansCJK-Bold.ttc` 文件归属由 `dpkg-query -S` 证实为 fonts-noto-cjk；本机包版权文件 `/usr/share/doc/fonts-noto-cjk/copyright` 的 Files:* 段（213–215 行）标注 SIL-1.1。字体文件未打包；任意其他外部字体仍须单独核对，不从这份本机证据推导未知字体许可。


### Line Art / Liquid 两组内容真实成片

第三批四个项目均为 1920×1080、30fps、240 帧/8.000 秒，完整 all 退出 0，strict QC 8/8、全片解码退出 0，blackdetect 无黑场。原始 all/qc-strict/decode/blackdetect 日志、最终 probe、帧哈希、接触表与源回执在 `/workspace/mg-style-evidence/third/<项目>/`；综合绑定记录为 `third/validation-source-summary.json`。独立核对每份 source-receipt 的生成器/渲染器哈希与捕获快照文件全部一致，Pillow/numpy/FFmpeg 实际版本亦在回执。

| 项目 | 成片字节 | final.mp4 SHA-256 |
|---|---:|---|
| line-a | 464860 | `de74a789fe7fffff53081d9da793437558f20a65df128d867834f34e0ef5714b` |
| line-b | 428973 | `88af7fce468f27caa5987f7be2ef283b34adb23daf1017d4dc84065c732f7363` |
| liquid-a | 659508 | `ff2353d143d99b6e0f5d6b1cf6e1d4b60471d436c6e303661066cabc9cef33d7` |
| liquid-b | 570990 | `6856031933d2f71cf20fafde36e45b719e063e787de11df133986c5954e932e2` |

Line A 是种子→树→书→房屋四章，Line B 是房屋→书→网络三章；已看编码成片接触表，连续绘笔桥、旧图淡化、末物体原轮廓填色均实际可见，文本未见越界。两内容改变对象数量/语义/节拍与最终剪影，不是同图换色。Line 渲染器 SHA-256 `ac5b1e13574a898f9e9bcade4f6c1a0a05f8613b90356c1779481c351a88aca6`。

Liquid A/B 更换语义、配色、seed、量感和四段帧窗口，使用真实隐式场轮廓而非移动圆替代融合。已查看实际 stage 边界前后及最终状态：gather→merge→split→resolve 轮廓行为成立，文字与液体主体未见遮挡。当前验收源码 SHA-256 `db2f5862f3a0a547104e3197e7110dc69450dd367a62e20992079add593764ed`（960 mask 版）；不复用早期低分辨率截图。

场景边界接触表 `scene-contact.jpg` 按各自输入窗口取端点，不拿固定节拍冒充内容节奏。液体边界还对编码成片中央液体区域 `[240,300,1680,800]` 的相邻帧 RGB 差实测：A 三切点平均绝对差约 .021–.035/255，B 约 .007–.012/255，原始数值见各项目 `boundary-field-deltas.json`。这些是局部像素连续性证据，不是艺术质量或所有帧连续性的通用阈值。

上述项目均使用普通 broll 视频素材路径、没有 gfx clip；实际命令为项目 timeline 的 `python3 vtx/vtx.py -t <timeline> all`，cwd `/workspace/framivra`，VTX 环境覆盖为空、浏览器参数为空。Pillow/NumPy 生成源帧并编码为独立素材，最终剪辑/混音/合成/QC 全走 vtx；浏览器保持暂停。验证席未实时全片播放或听审，静帧与技术检查不能冒称用户观看确认。


### 更严预检后的最终源码复验

Liquid 最终预检源码 SHA-256 为 `24bf5f6710e3757dd5626d5f9277061a08db20b59ec708e1e2600950761c4c17`。按此新捕获源码实际重建 `third/liquid-a-final`、`third/liquid-b-final`，均完整 all 退出 0、strict QC 8/8、全片解码退出 0、零黑场，240 帧/8 秒。两支最终成片 SHA-256 分别仍为 `ff2353d143d99b6e0f5d6b1cf6e1d4b60471d436c6e303661066cabc9cef33d7`、`6856031933d2f71cf20fafde36e45b719e063e787de11df133986c5954e932e2`，与先前已查看成片逐字节相同。这证明这两份合法内容的画面未因更严校验改变；不是把旧回执冒充新源码执行。旧 case 仍按各自源码哈希存档。

字幕当前更严预检/字形布局源码 SHA-256 `fe9bf301c4ef51e8985b7204a24b3b42ea6afbdb4fdaec22257a3e6fadd5536b`，补验 `third/caption-current` 的 60 帧/2 秒接入工程：all 退出 0、strict QC 8/8、全片解码退出 0、零黑场，SHA-256 `83c314a1409a7b50daf1e40822fe818113e3e1829a9c9ff2eacbf6644aa4a7c9`，177034 字节。已查看十帧接触表，逐字出现与完整可读保持段成立、无裁字或英雄遮挡；此项仅作当前预检和渲染接入验证，不声称第二轮字幕长艺术片终验。此前长片各绑定其已记录的旧字幕源码版本。

三份最终复验的包装器快照 SHA-256 均为 `bbd86f532c0f61fd889bae9451594ed69c6d0cd3f383b27f98a7e0297db36f2a`，build.py 为 `b4b028329d2d0f1087a2b28b923b84a328c72371b4f5fa8ecf0e0ddd171eb271`。快照文件、创意输入与运行环境已在各自 source-receipt 和 `third/validation-source-summary.json` 核对，未混用实时变化的仓库文件作生成凭证。

### 独立审查接续与源码边界

原独立审查席已留下艺术/源码发现；第三批后期多次发生该审查席的输出流中断。这是交互输出故障，不是环境权限拒绝，也不能据此声称原审查席正常完成了全部终审。未编写核心渲染器的验证席另行只读核对 README、build_media 与 storyboard compositor，并继续负责成片技术与代表帧审查。

README 明确区分十五款 Canvas prototype 与五种媒体路径、full 与 storyboard、参考音轨与声音设计、外部字体与未嵌入依赖、真实像素与静态测试。包装器以同一份捕获源码字节执行预检/生成并写入快照；renderer/composer 的文件身份指向工程快照，避免凭证读到后改的 live 文件。compositor 在写出前核对底片 scene.blend、创意 JSON、源帧/hash、尺寸与不透明性，逐帧记录持有底片或擦入边界；原生尺寸不缩放。

通用导入边界：compositor 绑定当前 validator 哈希，但不强制外部底片 evidence.script_sha256 必须等于该 validator。当前受控工程可另外核对二者一致；这不是对未知外部底片来源的真实性或跨渲染器像素等价承诺。技术核对与代表帧审查的范围分别记录，不将接续审查冒记为原审查席已正常收尾。


### 原生高采样 3D 底片的完整八秒分镜合成

`third/3d-story-a`、`third/3d-story-b` 均已完整 all 退出 0、strict QC 8/8、全片解码退出 0，blackdetect 无黑场；1920×1080、30fps、240 帧/8.000 秒。A 成片 SHA-256 `c34e09b1a4f1b0c5696375057db91e28b5269f3c3fba42d8c6af51842c418a33`，1968568 字节；B 为 `37b3e39668f67e8b3214c13a6d677087e8795fdda7c393740c4fc740f19b036b`，2024905 字节。

实际底片为 Cycles CPU / 128 samples / 原生 1920×1080 / denoising=false，每份三张：A `[24,96,180]`、B `[30,90,180]`。各 Blender evidence 明确 complete_sequence=false。冻结渲染器 SHA-256 `fff0311b8edd5e0ab648a7a74eeabc86d73d47d5c5c5dc4c3341c1fbcf92296e`，与上述旧 `/tmp` 四关键帧的 c74 脚本不同；不能混记。冻结 compositor SHA-256 `3a8250e9a458f1d272e19112b9e5f4de0eeb7f2d8823203a49e18bb1f6ceb3cf`。

独立逐帧检查全部 480 张合成 PNG：A 192 张停留帧与底片原生像素完全相同、48 张擦入帧的两侧区域分别与记录的入/出底片对应区域完全相同；B 分别为 180 与 60 张。所有 PNG 文件哈希与 frame_map/evidence 一致，尺寸全部保持原生 1920×1080，没有缩放；实际渲染器快照、Blender evidence.script_sha256 与 compositor validator 哈希三者均为 fff0311b…92296e。各项目 `independent-composite-pixel-check.json` 留存结果。

独立查看两份含章节前帧/擦入中帧/落定帧/结尾的十帧 `scene-contact.jpg`：材质和接触阴影清楚、三对象与五对象的关系/章节各异、稳态标题与标签无交叠。擦入中有明确竖向边界，会临时切分不同底片中的物体（例如 A f84/f168、B f75/f165）；这是声明的二维遮罩效果，不能描述为连续三维运动、碰撞或相机插值。源码、元数据、文档与本验收均区分完整合成序列和不完整 Blender 序列。参考音轨尚未同步章节，也未实时全片播放/听审；结论限于技术与代表帧艺术初审。

七个第三批最终工程（3D A/B、Line A/B final、Liquid A/B final、caption-current）的全部捕获源文件与 commit `b2de453624bc368e656d396dbed4cada86623ed7` 中对应文件逐一 SHA-256 核对一致，同时与工程快照一致；详见 `third/final-commit-source-check.json`。此核对不把旧工程较早包装器快照替换成新 commit 身份。


Line 最终 A/B 工程也已按 b2de453 的包装器快照重建并完整 all 退出 0、strict QC 8/8、全片解码退出 0、零黑场，均 240 帧/8 秒；最终成片与先前已查看的 Line A/B 逐字节相同（分别 de74a789…5714b、88af7fce…f7363），但源身份以 `line-a-final`、`line-b-final` 新 receipt 为准。最终七工程运行摘要、source-receipt、实际命令/环境与成片哈希汇总在 `third/final-validation-summary.json`，代码绑定核对在 `third/final-commit-source-check.json`。当前第三批所有独立验证命令已结束，无仍在运行的验证任务；浏览器与 GitHub 失败动作维持暂停。

## 第四批：真正逐帧三维连续路线（独立记录）

前述第三批结果已冻结；本节后续结果只绑定第四批新源码与工程，不将底片分镜合成替代连续三维定义特征。计划两套各 120 帧/4 秒的 Cycles CPU 连续渲染，原生 960×540、64 samples，进入现有 1920×1080 时间线时明确上采样；此 profile 仅作完整连续路线技术验证，不能称作高档画质终片。没有实际完成记录前不预报成功。逐帧耗时只从新任务实际日志统计，不把旧高采样底片耗时直接外推。

独立源码阅读看到整数帧纯函数的相机轨道/仰角、物体位置/旋转、体积守恒非均匀 scale，以及每帧 camera-space UI 位置/旋转/scale；最终实际成片仍须核对，源码存在不是像素验收。

历史 Eevee 失败动作补录（执行席提供的既有证据，未重试）：

```text
/usr/bin/blender --background --python-exit-code 1 --python templates/mg-styles/render_3d.py -- --content templates/mg-styles/examples/three-d-story.json --output /tmp/mg-story-eevee --frames 240 --width 640 --height 360 --samples 64 --engine BLENDER_EEVEE_NEXT --render-frames 180 > /tmp/mg-story-eevee.log 2>&1
EGL Error (0x3009): EGL_BAD_MATCH: Arguments are inconsistent (for example, a valid context requires buffers not supplied by a valid surface).
Failed to create /home/agent/.cache/mesa_shader_cache for shader cache (Read-only file system)---disabling.
```

执行席报告先 Ctrl-C 后 TERM 终止，exec 会话最终退出码 130，另 `_db` cache 路径有同类只读错误。这是应用/环境配置错误及人工终止，不是自动审批拒绝；未重试 Eevee、未更改缓存权限。后续连续路线沿用可执行的 Cycles CPU，不为该失败切换权限或禁用浏览器 sandbox。


### 第四批 CEL / Morph / HUD 与末态回归

七个独立普通媒体工程均实际运行 `python3 vtx/vtx.py -t <工程>/timeline.json all`，退出 0；另跑 `qc --strict` 为 8/8、FFmpeg `-xerror` 全片解码退出 0、blackdetect 无黑场。命令没有浏览器 flags 或 VTX 环境覆盖；本轮不重试浏览器。证据位于 `/workspace/mg-style-evidence/fourth/` 各工程的 all.log、qc-strict.log、decode.log、blackdetect.log、final-probe.json、validation-summary.json，汇总 `final-validation-summary.json`。源素材均为原生 1920×1080；成片 30fps。

| 工程 | 实际帧数/秒数 | 成片 SHA-256 |
|---|---|---|
| cel-a | 240 / 8 | `7a5a1019ab576e992d6757ea78315a520c299d08841b9b12db8e1f812d7f007a` |
| cel-b | 240 / 8 | `3b3f0c09393d3d24cd8eee0486909744dfafdbfe07c05c7df16fd8ec50a1c601` |
| morph-a | 240 / 8 | `99453f39f9077f54f883e5ee58d3f7c1a612a71b5db145f159f2c1d57c0ba0d0` |
| morph-b | 180 / 6 | `6046d2e6c540b7314c49b564cc87c921086ab5f6f014f49417c4a387b2346761` |
| hud-a | 180 / 6 | `8100ceebe1c93bbe8e9e0e1b69c1e795fd87f9928f6e92b3331673c2614dd171` |
| hud-b | 240 / 8 | `b521c25d1f36a40f241ed8eaeb86fe5a8033ef3cc8f8260530e8a4394c9b962d` |
| line-boundary | 30 / 1 | `b5e9b25ed9d63d5ed5cd6d5e3c2b92540445b428c8d327cc369cdac619545647` |

独立实际查看七份解码接触表：CEL 两/三角色中文标题、角色与纸面构图可读；额外查看 cel-a anticipation/action/smear/hold 条带，f8 收缩预备、f12–13 smear、f74–82 跳起、f130–142 横压竖拉可见。源 PNG 解码像素核对 f0=f1、f30=f31=f32，f10≠f11≠f12，说明按 exposure 保持与动作切换并存，不是仅源码声明。Morph 的过渡中帧接触表显示圆→叶→星的连续轮廓变化，第二内容为滴→圆→箭头；标签与终态清楚。HUD 两内容各有搜索/探测/确认阶段，锁定框跟随输入路径，最终面板分别显示输入的 -67dBm/2186m 与 36.5m/2.4MPa 等值；这些是模拟展示，不是实测遥测。上述均限代表帧初审，未实时全片播放或听审。

line-boundary 是 30 帧技术回归，不作艺术长片：最后 house beat 仅 `[28,30)` 且 hold=1；实际源 f29 与最终解码 f29 均已出现完整金色 house 填充。`independent-last-frame-fill.json` 记录同一内部采样点 (1368,553) 的源 RGB (216,189,130)、编码 RGB (214,186,128)，差异来自有损编码，不要求字节相同。末帧图与接触表同时已查看。

所有 receipt 中列出的 renderer/build/wrapper SHA-256 均与工程内捕获快照逐一核对一致。CEL 渲染器为 `91c054d70ddc913b31739c16055868281c44f2677c606db7c9ea4b57608d0c62`；HUD 为 `757f49afac5b816af627661a38f56e9d2fe8ca8911818a1be48fd47d5859b607`。Morph A 的旧捕获为 `e53d6a5d1f4ed14a117fc148c26745277bce859ce67112734effb41009b0cdff`，B 使用已加缺字预检的新捕获 `0cce35a58da72a2223992926bbd710d91c3fa760ff7d7deb155bc948ea9e1c06`；不能把 A 的合法内容成功说成执行了后加的字形拒绝。Line 边界工程捕获 `2ea9affdd54e984590405ec3377b914d8ecd2596d63a18283ca80233b27bbf2a`，不同于第三批原始 Line 源版本。

### 连续三维进行中：独立几何检查的实际局限

A/B 的完整 120 帧真实 Cycles 连续任务仍运行；在 source-receipt 出现并完成最终 all/QC 之前不宣称成片验收。对各任务已保存的 scene.blend 另行只读求值全部 120 帧，没有另渲染或写回。A/B 均有 120 个不同相机位置；可见文字相机投影 bbox 最大分量漂移分别约 4.84e-7 / 5.96e-7，scale 乘积约 1。原生 960×540 / 64 samples、项目 1920×1080 上采样的界限保持。

几何求值同时发现穿地：A cube 的世界网格最低 z=-0.05849252，B torus 为 -0.01275517（地面 z=0）；球和其他环体最低值为正。此证据存于 continuous/3d-{a,b}/independent-scene-audit.json/log。不能将体积守恒或影子效果描述成完整物理正确，也不能把后续修补小样归到这两个已在运行的旧快照。

后续 ground-fix 源的 B 内容另作 120 帧无像素渲染检查：冻结渲染器 SHA-256 `835070e936fc770aed7079fea8fc1b663ffe2aa0c939c0d26cc541d45f4ec2f6`，独立外部 harness 仅让 requested_frames 返回空数组，按原捕获源创建新 scene 后求值全部帧。球最低 z=0.0003449591、torus 最低 z=0.0000099996，均不穿地，UI bbox 漂移仍约 5.96e-7。证据 continuous/ground-fix-b-audit/audit-summary.json。此为新源几何修复证据，未渲染任何像素，不能改写旧 full job 的既有局限或成片身份。


## 第五批：等距组装、扁平因果图与 Synthwave 旅程

六工程各自 all 退出 0、另跑 strict QC 8/8、FFmpeg -xerror 全片解码退出 0、blackdetect 无黑场，1920×1080/30fps。证据位于 `/workspace/mg-style-evidence/fifth/` 下各工程 all.log、qc-strict.log、decode.log、blackdetect.log、final-probe.json、contact.jpg 与 validation-summary.json；汇总 final-validation-summary.json。仍使用普通 source 媒体路径，没有浏览器 flags 或 VTX 环境覆盖，没有重试已暂停的浏览器/GitHub 动作。

| 工程 | 实际帧数/秒数 | 成片 SHA-256 |
|---|---|---|
| iso-a | 240 / 8 | `d192632074b9d19614a9006d0b4bd1ed93aa22b6529dac0ea47a58a1c1a6b288` |
| iso-b | 210 / 7 | `dbe4af6ddd46b1a8f9e09b87f1e553c8a741cfb55be2ce679d78c5ed6bb52c60` |
| flat-a | 180 / 6 | `f95f51cafea6ff011608d3647a67679ab1ad1f12064d77fc353f640e855fd726` |
| flat-b | 240 / 8 | `010a6c49a0b7ca5be727b946c91e7938a11c05102183a842f774de959fb1d399` |
| synth-a-fixed | 180 / 6 | `7c740b220d285677394dd71238920ce7550fcb65cca928020021c6894032690c` |
| synth-b-fixed | 240 / 8 | `83e3fd00f3fb81278bab880e69acc87acfdc61d4eff1181b7eba713ae996abc1` |

独立查看六份实际解码接触表：等距两内容分别表达社区供能→生活/创作/花园、学习→研究→共享网络，基地/墙/屋顶/激活阶段与建筑深度层次可见；它是程序绘制的等距 2.5D，不称 Blender 三维。Flat 两内容分别为三卡产品因果链、六卡双路节能行动，问题→构建→结果重排有不同空间组织，标题、数值、说明可读；中间存在声明的全幅圆形遮罩转场。Synthwave 两内容有不同标题、途经点、目的地与节奏；chrome 字、分层山、透视网格/门架、抵达阅读段清楚。仅作代表帧艺术初审，未实时全片播放/听审。

旧 fifth/synth-a 捕获在山脉 world-grid 修复前，保留为已知 seam 缺陷的历史工程，不纳入成功表。fixed A/B 才是本次实际验收：独立读取捕获 renderer，在 travel 133.2→133.5、layer=1 边界，两组共有 29 个世界顶点均 x 位移 -0.18、y 不变；实际顺序源最后 10 帧的解码 RGBA 哈希各自完全相同，证明两内容抵达段停稳。independent-journey-check.json 留证。后来的字体预检补丁不反向改写这些既有捕获源身份，表中每项 source-receipt 的源 SHA 均另与工程内快照对账。

独立乱序重放另覆盖 CEL A、Morph B、HUD B、Iso B、Flat B、fixed Synthwave A：新导入各工程捕获 renderer，按末帧→1/3→0→1/3 调用，实际 RGBA 与原顺序生成 PNG 逐像素相同，同帧重复结果一致。各 independent-frame-replay.json 记录精确源哈希与逐帧像素哈希；仅承诺该固定环境，不外推跨机器像素等价。

第五批旧 Iso A 艺术结论更正：虽然深度排序和技术链通过，energy(0,0) 与 homes(1,1) 在同一投影 x−y 列，f59/f119/f239 叠塔遮挡后方节点，不能称艺术辨识全部通过。该旧技术证据保留；新 iso-a-fixed 采用 4×4 分散节点坐标与较低高度，实际输出仍需另审，不将旧图替作新结果。

iso-a-fixed 已另完成 240 帧/8 秒：all 退出 0、strict QC 8/8、解码退出 0、无黑场、ffprobe 实际解码 240 帧。成片 SHA-256 `9cfad459507465862f5472d0ba927487779d95857fb70d5f6d521f75a11ed00d`，989439 字节。独立查看新成片接触表，f59/f119/f239 四节点分散、可各自辨认，旧叠塔的示例布局缺陷在新内容中修正；旧工程技术记录和艺术失败仍保留，不更改源凭证。

连续任务中的环境连接状态：主控在一次连接中断通知后只读确认环境仍可用，旧 A/B Blender 进程 PID 25999/26280 继续存在、已落盘 49/40 张 PNG，日志正在渲染 f49/f40。未重启任务、未补渲染或更改权限；这是运行中存活检查，不是最终成功回执，也不是自动审批拒绝。


## 第六批：贴纸证据、纸偶拼贴、解析玻璃与机械包豪斯

下列八份最终示例分别实际 all 退出 0、strict QC 8/8、FFmpeg -xerror 全片解码退出 0、blackdetect 无黑场；ffprobe 解码帧数逐项等于输入窗口，原生源与成片均 1920×1080/30fps。普通媒体路径，无浏览器旗标或 VTX 环境覆盖。每份工程 source-receipt 声明的 build/renderer/wrapper 哈希均与工程捕获快照相同；完整命令日志、帧数、成片哈希、依赖版本与接触表位于 `/workspace/mg-style-evidence/sixth/`，汇总 final-validation-summary.json。

| 工程 | 实际帧数/秒数 | 成片 SHA-256 |
|---|---|---|
| sticker-a | 240 / 8 | `261fcbe37e062c3176aa514ddd918a88722a4a021eda04fde209cb2145b05a60` |
| sticker-b | 240 / 8 | `9b3183fafd7d189b70e1b021c6fac3a923202e478f6e3507a6d02d3ab78b0f24` |
| bauhaus-a | 180 / 6 | `c003b4062ab4f78d94f56be0af7816e90bf46c3e8c36b49a49cf52fb7f5a9fd2` |
| bauhaus-b | 180 / 6 | `b58125617496866dd17e028a60bdedeb569912303ed19a68afed48c99eb45a08` |
| glass-a | 240 / 8 | `e9fd6b229d57db10b31e981487b26b05d089033db05bee639366359091ee07f4` |
| glass-b | 180 / 6 | `6d33ed102e2a133c1a81bd7d9d0969a7dabb159ef545d995e678c4f224153b34` |
| collage-a-fixed | 240 / 8 | `b62b6055353fa9ffb142142098ac97f3d140768f0266d9a75265b6ee9b26da99` |
| collage-b-fixed | 180 / 6 | `974a5c0d8cdf77474eafade94ac8549ec5871f6ed9d7c3db52853e4660d58dd2` |

独立实际查看八份最终解码接触表。Sticker 两例分别手机分解/正值质量柱、节水场景/负值变化柱，零基线方向、统一单位、来源示意提示可读，镜头拉远组织前述证据。转场行进时会有正在进出视口的内容，落定阶段才作阅读；结尾总览缩小，不承诺总览每个小字可读。Bauhaus 两例模数一致，红黄蓝黑几何在不同轴心/级联拍表中重组，标题、拍名与构图区保持；参考音轨没有按拍表定制同步。

Glass 两例已看接触表及原生中/末帧，标题/章名保持独立可读区，中心微 UI 分别出现数值、波形或开关，末态主卡可读。镜片穿越时局部色散/折射会变形旁卡小字，不能描述为所有文字任意帧完全清晰或物理光线模拟；真实形变范围与声明的解析图像空间光学相符。没有做实时播放或听审。

Collage 旧 A/B 虽 all/QC/解码通过，但独立源码复审发现非零 child pivot 配合 parent angle 的补偿少旋转一侧，原反例 parent=90°、child relative=0、pivot=(.5,0)、scale=100 会无端平移 (+50,-50)。两旧工程保留为技术历史，不作正确关节运动的最终艺术通过。fixed A/B 是重新生成和验收的不同工程；独立固定源数学反例现 parent/child 坐标同为 (960,540)、attachment offset=(0,0)，各源 PNG f0=f1，固定 B 乱序重放与原顺序 PNG 像素一致。接触表中父子纸偶/夸张比例与撕纸过渡成立；只是程序纸偶，不称照片拼贴或声音口型同步。证据 independent-hinge-check.json、independent-exposure-check.json、independent-frame-replay.json。

本轮启动 collage-a-fixed all 时曾一次收到 `Failed to create unified exec process: exec-server transport disconnected`。随后只读 pwd 成功、没有该 vtx 进程且 all.log 尚不存在，确认未执行后只重发一次，最终 all 退出 0；这是连接故障，不是自动审批权限拒绝，未做权限恢复或重复渲染旧项目。


### Line / Liquid 当前源码回归

另按当前源码实际重建四份 240 帧/8 秒工程，全部 all 退出 0、strict QC 8/8、全片解码退出 0、零黑场、实际解码帧数 240。独立已查看四接触表：线条桥接与末态填充清楚、液体聚散及最终字块在轮廓内可读。证据 `/workspace/mg-style-evidence/current/final-validation-summary.json` 和各工程日志/receipt；旧 third 工程继续绑定旧版本，不改写成当前源码执行。

| 工程 | 成片 SHA-256 |
|---|---|
| line-a-current | `fbfa91b97d83d6063bb8b0ce78585061da7bce2fd8fd028a52de25707717aef0` |
| line-b-current | `ac560aa8b83ca5a0d673a2ab136072427383bd45cdbb781fdeb188f833388a7e` |
| liquid-a-current | `cb36e87e71f678e22fe0678f9ae854e6e4be3bfd2e97e54e152c89b6917b0c9a` |
| liquid-b-current | `288ff37bc7f9926ca0a7410eb0ddaa8deb6f707634d786e3e664035ed5b19379` |

当前回归亦未实时播放/听审，未测试跨设备像素等价。FFprobe 帧数与 timeline 帧数的独立对账记录另存 decoded-frame-count-audit.json；解码成功与艺术判断分别记录。


### 最新冻结源码测试与 Blender 重生成边界

集成席在 commit `d27580464281aa24294e43289aab0c0bc892d601` 冻结源码/fixture，模板全组实际 123 项测试通过，245.246 秒、零 skip，source manifest 记录 unchanged_during_run=true、exit_code=0。验证席只读核对 all-template-tests-final.log 与 all-template-tests-final-source.json，未重复执行这组。此前 1d9273a 的 122 项保留独立 -1d9273a 日志/manifest，不与最终数目混用。玻璃小数值新补丁有另行定向测试及五个整数代表帧 RGB 不变证据；已验收 Glass A/B 的整数内容源身份不因此变成执行新小数源码。

集成席另在连续 A/B 当前旧捕获 renderer `bdbb38117631cc2711aa645cb6dcd4639fb6cbbfa48cc39e466456888e3645cc` 上，以相同 native 960×540、Cycles CPU、64 samples、threads=4、seed=0 新进程各重渲染 f47 到独立 replay-{a,b}-47。严格 RGBA 逐字节相同两者均为 **false**：A 的 518400 个 RGB 像素中 3 个不同、最大差 1 级、逐通道平均绝对差 2.57201646e-6（均以 0..255 通道单位计）；B 为 1 个不同、最大差 1 级、平均 6.43004115e-7（同口径）。完整像素哈希与统计见 continuous/independent-native-frame-replay.json；没有改容差门将其记为通过，也未归因具体 CPU/浮点路径。

验证席随后仅加载原/replay 四份 scene.blend 在 f47 只读求值：A 20 对象、B 16 对象的 world matrix、求值 world mesh vertex 哈希与 camera 属性完全相同，列出的 engine/resolution/samples/seed/color-management 设置也相同；见 independent-replay-scene-comparison.json/log。此检查没有渲染或保存，不覆盖引擎所有内部状态，不构成像素差异根因证明。动画状态由整数帧确定、最终源媒体哈希固定并由 vtx 消费，与外部 Blender 重新生成图像能否 bit-identical 是不同结论；当前外部重生成严格像素一致性未通过，不能用 fixed-media 消费结果替代。


### 连续三维最终完整技术验收

continuous/3d-a、3d-b 的原始 Blender 作业均正常结束，各自完整渲染 120 张原生 960×540 PNG、64 samples/Cycles CPU。独立逐一核对全部 240 张 PNG 的 SHA-256 与 Blender evidence 相同，complete_sequence=true、尺寸无混用，捕获 renderer 与 evidence.script_sha256、receipt 声明源与快照也均相同；source commit 为 `7decc1187d36586ed1959ff4bfabf962d7bf9527`，renderer bdbb3811…645cc，不能记成后来 ground-fix 源。固定源编码后由既有 timeline/vtx 执行视频、音频、mux 与 QC，不走 gfx 或浏览器。

两工程分别 `python3 vtx/vtx.py -t <工程>/timeline.json all` 退出 0，另跑 strict QC 8/8、FFmpeg -xerror 完整解码退出 0、blackdetect 无黑场、ffprobe 解码 120 帧/4.000 秒/30fps/1920×1080。源为 960×540，成片是明确上采样；没有称原生1080全片。

| 工程 | 最终 SHA-256 | 字节 |
|---|---|---|
| 3d-a | `8c67a34c9730d109812f5abfb8bda44976fab0f527d454bfc2f5ba28543abdc3` | 1649956 |
| 3d-b | `0ae3fe6df05c0d92d6ffd53d2d8b23813c56767c4cd004784e9f7095572bc4f2` | 1395866 |

证据 home：continuous/final-validation-summary.json；各项目 all.log、qc-strict.log、decode.log、blackdetect.log、final-probe.json、independent-complete-source-audit.json。最终辅助接触表脚本初次把 beats dict 当 list 切片而失败，导致自动包装流程退出 1；这是外部诊断工具错误，实际 all/QC/解码已成功。只修正该辅助脚本的类型判定、重抽接触表到 scene-contact-helper-fixed.log，退出 0；没有重复启动 Blender 或 all，原失败日志保留。

独立已看两份全部 120 帧解码缩略条带（128×72、15 列、无跳帧），再结合原生 f40/44/47、章节边界与最终帧接触表：真实相机视角变化、横压竖拉、连接杆生长和末段抬升连续存在，三对象与两对象内容不同；标题/值/标签保持视口布局，未见章节文字相互覆盖。全部 240 成片帧另有 native rawvideo SHA-256 与连续 PTS 记录，位于 continuous/temporal/filmstrips/ 与 filmstrip-index.json。完整静态条带检查不是实时播放或听审，也不取消前述穿地、64-sample 噪声/上采样或外部再生成低位不一致限制。技术连续路线已闭环，高档画质与所有艺术细节尚不能据此宣称终验。

实际日志逐帧耗时统计：A 120 帧最短 13.05 秒、中位 31.615 秒、最长 75.01 秒，帧报告时间总和 4074.92 秒；B 为 21.25 / 30.34 / 69.98 秒、总和 4017.15 秒。它们是 Blender 帧报告之和，不是完整进程墙钟或性能基准，不含后续编码/vtx/QC，也不能外推别的内容/profile。render-progress.json 保留具体口径，未进行性能改动。


## 完整静态时序审查后的修订批

旧 28 片完整解码共 6210 帧，native frame hash/连续 PTS/128×72 全帧条带均对账；前十四片由 B 审全部 3240 缩略帧与原生定位，后十四片由新独立审查席审全部 2970 缩略帧。该覆盖不是实时播放/听审，更不能直接推导整体艺术通过。完整审查新增发现旧 Pixel skyline 在周期边界整排重置（两份字幕复用底片也受影响）、旧 Line 全填终态尾停留过短、旧 Synthwave 路标屏内消失，以及 Sticker/Glass 的阅读末态预算偏短。旧片技术通过和源 SHA 保留，修正版必须绑定新媒体，不继承旧图作为修复证明。证据 temporal/review-first14.json、review-last14.json、reading-windows.json、semantic-events.json。

### Pixel / Line / Captions 六份新示例

执行席实际完成 readable 下六新工程 build/all/check 均退出 0，strict QC 8/8、全片解码退出 0、无黑场；验证席另独立全片解码/原生帧 SHA 与 PTS、实际查看全部 1440 帧静态条带和关键 native。六份 receipt 声明的 build/renderer/wrapper 均逐字节匹配 commit `dac7457fe628001cedbb648776affdc0b9ef76c9`，见 readable/independent-temporal-review.json 与 temporal/readable-six/filmstrip-index.json。未重复执行席的 all 或音频审计。

| 新工程 | 帧数 | 最终 SHA-256 |
|---|---|---|
| pixel-a-fixed | 300 | `2e366ea3a5264089f6d09454694f5e5ae8d9f01678d41e530e22afdbd357f3ee` |
| pixel-b-fixed | 180 | `eddf3135b096a5f8bdd213a51ef92cd9e7bbb73b560efeb58d7013549c073c0c` |
| line-a-readable | 240 | `795fb4c4bfa737f788c3d4cd3e080c9267e5810faa6adcf04cec3dc0be306f14` |
| line-b-readable | 240 | `18b8fbd1822e97258989516cbce9ca2bdfc744eb7ee92bf6bbe8c0d1a6ac0749` |
| captions-a-pixel-fixed | 300 | `011a7db11ef12690a72a91f93cf7100d3b43332544f1e953d6fb380b5482945f` |
| captions-b-pixel-fixed | 180 | `bae724b8fca5cb912ae59f303b3933489ba0ca0e56ba5a5b07878c0a160c1e08` |

Pixel A f46→47、B f38→39 的编码 native 并列图已查看，skyline 保持建筑身份连续平移；两份字幕使用修后底片，不再继承旧重置。Line 末 beat 明确分为 fill_frames=12、hero_hold=30，实际 f210..239 的全部 30 张源 PNG 各自逐像素相同，已看编码 f210/f239 的满色 house/network。旧仅 1/7 帧满态的记录不改写。字幕 B 改为五/六/五个字元的短句（含标点），45 帧窗口保留，stagger=1；native f30/f75/f130 可读且未遮人物，旧长句阅读预算不作为新短句证明。所有新时序判断仍为静态序列/原生帧审查，不是人类实时阅读终验。

旧 accepted-media-index 的初始 28 条索引保存在 accepted-media-index-14-initial.json 与 temporal/accepted-media-index.snapshot.json。新增 preferred-media-index.json 单独记录当前较优媒体替换，domain 六片已逐项纳入；Sticker/Glass 阅读 v2 四片也已完整独审并纳入；ground-fixed 两片现也已完整独审并纳入，不把早期索引所绑定的音频/全帧统计指向后改的新片。


### 媒体与 HTML 分别冻结的最新测试

媒体模块组在 `dac7457fe628001cedbb648776affdc0b9ef76c9` 冻结：17 模块实际 126 项测试通过，284.533 秒、零 skip，77 份源/fixture/tests 前后哈希一致；日志 media-tests-dac7457.log 与 -source.json。HTML 组另在 `436306580cd86e4b6080611702b806655d931fae` 冻结，11 项通过、98.388 秒、40 文件前后一致，日志 html-tests-4363065.log 与 -source.json。验证席只读核对日志，未重复执行。它们绑定不同快照，不合并写成“137 项全部验证当前所有源码”；其后 Liquid HTML 最短窗口等修订进入新的 HTML 冻结回归：`a29d82c` 实际 13 项通过、75.882 秒，40 份文件前后哈希一致。后续已 push 的 `f3ea10e3c2b4662614cf0a5b2d0dc476d950a576` 仅补 D4 recipe 字段与轻量 15 cards/8 keys 消费面检查，已有独立审查记录，未重复整组 suite；不能将 13 项回归反向标成 f3ea10e 全量重跑。436 的 11 项保留为历史，当前 HTML 回归引用新的 13 项。HTML 静态/操作测试仍不是浏览器真实像素验收。


### Synthwave / Flat / HUD 域边界修订六片

domain-fixes 下 synth-a/b、flat-a/b、hud-a/b 六片均实际完成 all 退出 0、strict QC 8/8、完整解码退出 0、blackdetect 无 interval，帧数与各自 180/240 帧声明相符。执行席与验证席分别实际查看六份完整有序帧条带与原生定位：Synthwave 旧屏内突然消失位置仍见路标，后续逐渐完整出屏；Flat 全量卡片末态存在；HUD 首帧与不同内容的保护布局有效。验证席逐字节核对声明源码匹配 dac7457，独立记录 domain-fixes/independent-temporal-review.json，完整最终 SHA、命令与检查结果在 domain-validation-summary.json 和 preferred-media-index.json，不把旧片 SHA 移作新修复证据。静态条带检查不等于实时播放。

### 声音信号与视觉时机的验收边界

旧初始 28 片的音频数值审计保存在 temporal/audio/，全部 28 片无技术错误：48kHz 双声道、stream 音画时长差为零，I 为 -14.8 至 -14.7 LUFS、true peak 为 -4.4 至 -4.2 dBTP，无近满刻度样本或达到检测窗口的静音。源 cue 左右完全相同，成片声道仅有 AAC 微差，是近双单声道，不能把双声道元数据当作空间声音设计。AAC 解码尾部 padding 0 至 18.67ms 不等于 stream 音画错位。原始日志、PCM 与 channel-audit.json 保留在外部证据目录。

修后 readable 六片、domain-fixes 六片分别另跑新媒体音频审计，各 6/6、errors=[]，见 temporal/audio-readable-fixed/audio-audit.json 与 domain-fixes/audio/audio-audit.json；不复用原 28 片音频结果。全部仍使用半秒网格的程序参考提示音，未完成有意配乐或听审。旧 28 片精选 125 项视觉落定/强调仅 36 项距该网格不超过一帧；domain 新 86 项非边界事件中 31 项距实测 rise 不超过一帧、55 项超过。事件对应关系见各 semantic-events.json，这些统计明确暴露占位音没有消费视觉 cue 表，不能宣称艺术声画同步通过。


### Sticker / Glass 阅读 v2：技术记录与后处理事件

readable/sticker-phone-v2、sticker-water-v2 各 360 帧，glass-ideas-v2、glass-reflection-v2 各 300 帧，四片 build/all/check 共 12 阶段均退出 0。唯一成功全帧条带含 1320 帧 native hash/PTS，位于 temporal/readable-c-v2/filmstrip-index.json。验证席已实际查看两份 Sticker 的全部 720 帧静态条带与原生近景，并核对最终媒体 SHA、360 连续 PTS、检查结果及捕获源码逐字节匹配 dac7457；见 readable/sticker-v2-independent-temporal-review.json。问题→结构→证据→结论→总览连续存在，证据与结论近景分别停留，末总览承担关系定位而非细字阅读。实际源证据静止 78 帧；phone 结论近景/总览为 45/40 帧，water 为 42/36 帧，各有逐帧 hash。Glass 图像全帧由 fresh 独立审查席实际查看全部 600 帧条带与 12 张 native，实际 final select 解码 RGB 与这些 PNG 逐字节一致，0..299 PTS/原片及条带 SHA 另核；见 temporal/readable-c-v2/independent-glass-review.json，不重复计为验证席亲看。未见新未声明 reset、关键标题/底字互遮或末 UI 未完成，局部镜片有意扭曲仍依赖清晰时段阅读。Ideas UI 可读尾窗分别 73/67/91 帧，Reflection 两项各 121 帧，空间光场继续运动，不能称全图静止。四份新媒体已替换 preferred 中原 Sticker/Glass 两套，当前 28 条中的 16 条为本轮修后媒体。

辅助后处理曾重复启动：原成功子进程仍在运行，新进程因既有输出 FileExists 停止，并把 phone stderr 辅助日志写成失败；没有改动媒体或源码。该失败保留于 postprocess-incident.json 与旧 audio/audio-audit.json，不冒充成功。phone 另独立完整重解码 360 帧，native hash/PTS 记录完全相同、verified stderr 为空。四片音频在全新 audio-verified/ 目录另审，audio-audit.json 实际 4/4、errors=[]，channel-audit.json 独立保留；当前成功音频凭证明确指向 audio-verified，未听审。


阅读 v2 最终媒体 SHA-256：

| 工程 | 帧数 | SHA-256 |
|---|---|---|
| sticker-phone-v2 | 360 | `db40e4dcb0c18ed22b1845dcf8c882e6cc5126e060b04d7afa4bf5e7c8170a42` |
| sticker-water-v2 | 360 | `78423b7bad35c8eb6b7ecab3b506cf620c70e2647c24ef97139b26ff2a1d8cf7` |
| glass-ideas-v2 | 300 | `2a33bedc580ec8a56154ffa99bdf192ad8d8a653746c51968a622a70c4a2b3ae` |
| glass-reflection-v2 | 300 | `d97a5eeecc4afa23c3dafc0b9adfbcc408ecd09cca6e6436734519ca0992a77b` |


### Collage B 结论阅读修订

readable/collage-b-readable 仅替换示例 JSON 的末 resolve 结束帧 180→240，renderer 不变；实际重新 build/all/check 均退出 0，strict QC、240 帧完整解码与 blackdetect 无黑场通过。最终 8 秒媒体 SHA-256 为 `cd177ca8f3350312571c537ead5d9673bca9114dfae8a0cd0c9aea141773f6ce`。fresh 独审查看全部 240 帧条带及 f139..239 原生结论，另从实际成片 select 解码 f140/239 与图像 RGB 逐字节一致。旧前 180 张源 PNG 全部 same-bytes，末 f140..239 的 100 张源 PNG 同 hash，旧 40 帧结论停留由新例的 100 帧/3.33 秒替换；见 independent-readability-review.json、source-prefix-identity.json、readability-audit.json。

该片新音频审计 audio-tech/audio-audit.json 为 1 片、errors=[]，音画 stream 时长差为零，-14.8 LUFS/-4.4 dBTP、无满幅越界；仍是占位提示音、未听审。preferred 索引仅替换原 collage-b-fixed 条目，旧媒体/receipt 与历史索引保留，未声称旧全组测试覆盖新 JSON。当前 28 条中的 17 条为本轮修后媒体，ground-fixed 两片随后完成验收并纳入，总计 15 款/30 片。


## Ground-fixed 连续三维最终后验

continuous-ground-fixed/3d-a、3d-b 两套均由唯一 4057 驱动实际完成 build/all/check，六阶段退出 0；各为 120 帧/4 秒、原生 960×540、64 samples/Cycles CPU4/seed0/no-denoise，成片 1920×1080 明确上采样。逐一核对全部 240 张新源 PNG 与 evidence frame_sha256 相同，完整序列标记、creative 与捕获 renderer 哈希一致；renderer 为 `c8f1838929c01a56d72f89f0f950b9135175cb2a6cd04e16f2a6aed776f67d40`，不得混用旧 bdbb 源。completion-verification.json 与两项目 independent-complete-source-audit.json 保留对账。strict QC 各 8/8、完整解码/blackdetect 退出 0、无黑场且帧数各 120；唯一新增全帧条带在 a-independent-temporal/、b-independent-temporal/，240 native hash/PTS 连续。验证席实际看 B 全部 120 静态帧与 final native f47/119，真实相机、形变、连杆生长及末段抬升连续，固定 UI 未见互遮；64-sample 噪声与上采样柔化仍可见。A 图像全帧由 fresh 独审另计。

此前独立 scene-only 对两个新 .blend 的 120 帧世界顶点检查均为正高度，A 最低约 0.000009972602、B 约 0.000009999610；这与已完片证据共同补上旧快照穿地问题，不等于全面物理模拟验收。两个新媒体另在 audio-verified/ 实审音频，2/2、errors=[]，提示音与视觉语义的艺术同步及听审仍未通过该数值检查证明。

等待双完整源/all/check 后，按同输入、同 c8f183 捕获源码、同 960×540/64spp/CPU4/seed0/no-denoise 各用一个独立 Blender 新进程仅重渲 f47 一次，两个进程退出 0，没有重启全片或重试：A 严格 RGBA 相同为 false，518400 像素中 3 个 RGB 像素改变、最大 1 级、meanabs 0.0000019290123456790124（0..255 单位）；B 严格 RGBA 相同为 true、零变像素。见 independent-native-frame-replay.json、replay-a-47/、replay-b-47/ 与命令/日志。没有容差通过或根因归因；B 仅此单帧新进程例通过，不能泛化全部帧或任意环境外部再生成 bit-identical。固定捕获源媒体进入 vtx 的帧契约，与 Blender 外部重新生成的严格像素一致性分别记录。


A 的 fresh 图审已完成全部 120 帧条带、11 张源关键帧与 5 张实际 final 1920×1080 select 原生图，见 3d-a/independent-visual-review.json；未观察到旧 cube 穿地截平、新异常 reset 或固定 UI 互遮，保留粒噪与原生540上采样限制。preferred-media-index.json 已纳入新 ground-fixed A/B，总计十五款各两套、30 片；旧初始28与旧穿地两片作为历史保留。新 A/B 最终 SHA-256 分别为 `638ee17254c5e44994f4dd90dd0e8e2ebe2d4f8b4333171f6a0b004d1bb2658d`、`7dd6a79f5f4b3a7fc8f62c66343f6f971b6ac571208f05a120a2cdb438eecd58`。

### 冻结资产消费的独立重复验证

另建全新 frozen-consumption-replay/a、b 最小工程，只复制原 timeline.json 与其全部引用 cue.wav、media/scene.mp4，相对路径不变、逐文件 SHA 相同。各工程仅一次 vtx all 与 strict QC，均退出 0；未启动 Blender/浏览器、未改原工程。FFmpeg 对原新 ground 成片与该独立工程成片分别完整解码为 rgb24 framehash，各 120 帧的 decoded RGB SHA、PTS/时长记录逐行严格相同，两个最终 MP4 文件 SHA 也各自相同。命令、日志及比较位于 continuous-ground-fixed/frozen-consumption-replay/comparison.json。

该结果证明本环境此两份冻结资产与时间线的重复消费通过，不改写外部 Blender 源重新生成合同：A f47 的三像素/一级差仍为严格 FAIL，原始 PNG/JSON 保留，不重试筛选、不改容差；B 仅 f47 单例通过，未证明完整外部重新生成逐位一致。
