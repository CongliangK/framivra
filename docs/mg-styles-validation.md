# MG 模板验证记录

验证环境：2026-10-20，Linux 云工作区；实现范围见 [模板操作入口](../templates/mg-styles/README.md)，来源与许可见 [研究记录](mg-styles-research.md)。本记录由独立验证席产出，技术结论不构成艺术审查或用户视觉确认。

## 当前验收边界

- 15 款 HTML/Canvas 源码已有不同内容生成、帧操作重放与 vtx validate 验证；浏览器环境仍阻塞，不能声称 15 款已真实渲染通过。
- 独立非浏览器媒体路径：Pixel、反应字幕、Line Art、Liquid 各两套实际内容已完整 all / strict QC / 全片解码 / 零黑场通过，详见下方对应最终哈希与回执。早期失败和旧样例只作溯源，不继承新版通过结论。
- 3D 具有早期一秒技术预览、新版高采样章节关键帧与两套已完整验收的八秒 storyboard 媒体。storyboard 是真实 Cycles 底片的二维编排，不能称作逐帧 Blender 动画。
- 所有结论均未包括实时完整视频播放/听审或用户观看确认；本地媒体未上传 GitHub。实际失败没有通过改门、改权限或禁用浏览器 sandbox 处理。

## 首批基线检查

- `tests/test_mg_styles.py` 六项测试通过：全部款式各用英语单条与中文特殊字符三条内容生成；检查 v2 时间线、整数帧、单页 clip、依赖文件存在；非法帧/内容拒绝且不创建输出；已有目录不覆盖；dry 不落盘。
- Node 模拟 Canvas 对全部款式检查帧 0、1、9、45、80、89 的绘制操作、非有限数值、save/restore 平衡及 45→89→0→45 乱序重放。操作序列复现通过。**模拟 Canvas 不实际绘制，不能证明像素确定性、字体适配或视觉质量。**
- 全部款式生成 90 帧、1920×1080、30fps 项目，真实 `vtx validate` 全部退出 0。

集成席另报告：模板与存量布局等联合测试共 16 项通过，文档门 7 项通过；此两项由集成席执行，验证席未重复运行。挂载复核 `/home/agent/.agents/skills/video/` 下 SKILL.md、assets、references、scripts、templates、vtx 全部 MISSING，不能报告 BAD=0；未修改挂载或权限。

本地原始日志在 `/workspace/mg-style-evidence/`：`unit.log`、`validate-summary.json`、各款 `*-validate.log`；带提示音最终输入见 `audio-validate-summary.json` 与各款 `*-audio-validate.log`。媒体及缓存未纳入 Git。

## 实际成片尝试与阻塞

对 `flat-vector` 执行完整 `vtx all`，**退出 1，未产出成片**。`all-flat-vector.log` 记录两项独立失败：

1. gfx 环境身份拒绝：`native ELF renderer required: /usr/bin/chromium`。本机命令是包装入口，未获得合格渲染环境身份。
2. 当次模板无音轨，音频阶段生成 `amix=inputs=0`，FFmpeg 拒绝输入数量。该问题已在模板生成器修正：加入原创确定性参考提示音，不修改内核或门禁。最终输入全部款式重新 validate 均退出 0；代表性 `flat-vector` 的 prepare/audio 分阶段退出 0（`prepare-audio.log`、`audio.log`），音频终态 I=-14.8 LUFS、TP=-4.4 dBFS。此分阶段结果不代表 mux/QC 或完整 all 通过。

浏览器失败的原始命令、退出码及当时 stderr 已整理在 `/workspace/mg-style-evidence/browser-failures.txt`（历史工具输出转录，没有重新启动）：仓库 `Browser(tabs=1).start()` 退出 1；`chromium --headless --disable-gpu --dump-dom about:blank` 退出 1；加可写 `--user-data-dir=/workspace/mg-style-browser-probe` 的探测退出 134。这些是应用/环境配置错误，**不是自动审批拒绝**。GitHub GraphQL 的 Forbidden 查询亦已暂停，未重试。

另以仓库 Browser 启动浏览器报 `chrome exited during startup`；使用可写项目外 profile 获取 Chromium stderr，指出 SUID sandbox helper 未正确配置、要求 root 所有者与 mode 4755。验证席未修改权限，未加入禁用 sandbox 参数，未请求提权；环境阻塞后停止浏览器启动。

因此 HTML/Canvas 路径未完成真实 PNG 渲染、同帧像素复现、抽帧艺术审查、黑场检测或成片 QC（非浏览器路径成果见下节）。不得将单元测试、操作序列或 validate 通过记作成片验收。需在具备合格原生浏览器与沙箱的环境补验；参考提示音应按创作要求替换为有许可的正式配乐或旁白。

## 非浏览器路径补验

扩展单元测试 `tests.test_mg_styles` 与 `tests.test_mg_captions` 共 9 项通过（11.449s，无 skip），日志 `/workspace/mg-style-evidence/unit-expanded.log`。字幕测试包含中文、语义 punchFrame、subject_rect、区间边界、实际 PNG 与 direct-seek 像素一致。

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
