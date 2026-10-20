# MG 模板验证记录

验证环境：2026-10-20，Linux 云工作区；实现范围见 [模板操作入口](../templates/mg-styles/README.md)，来源与许可见 [研究记录](mg-styles-research.md)。本记录由独立验证席产出，技术结论不构成艺术审查或用户视觉确认。

## 已执行

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
