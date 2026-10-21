# MG 验证摘要

2026-10-20 Linux云工作区的历史验收，只绑定下列源码/媒体。用途与逐款限制见[状态与来源](status.md)，使用方法归[模板包](../../../templates/mg-styles/README.md)。逐轮命令、旧哈希表和审查过程保存在[固定验证原文](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/reports/mg-styles/validation.md)与[固定独审原文](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/reports/mg-styles/review.md)，不再维护开发流水账。

## 结论与证据身份

| 面 | 实际结论 | 不证明 |
|---|---|---|
| legacy Canvas / 独立HTML | 输入/状态/命令流及局部真实RGBA检查通过；浏览器启动受阻 | 浏览器字体/像素、真实HTML成片、实时播放 **UNRUN** |
| 15种普通媒体，各两题材 | 独立源帧→编码→timeline all→strict QC8/8→全片解码/帧数/零黑场；优选30片完成全帧静态条带与选定原生图审 | 静态全帧不是逐帧原生全检、实时观看、听审或用户艺术终验 |
| 3D storyboard | 三张原生1080p/128spp底片组成完整240帧二维遮罩合成，源保持/擦入像素核对通过 | Blender完整序列为false；不是连续三维运动 |
| 3D连续源 | 新ground-fixed各120帧完整Cycles；几何、媒体与冻结资产重复消费通过 | 原生540p上采样/粒噪；外部源再生成A严格FAIL，B仅f47单例PASS |
| 声音 | 分批实际媒体音频数值检查通过 | 半秒网格占位提示音未消费视觉cue；无有意配乐/听审/艺术声画同步通过 |

证据根为仓外 `/workspace/mg-style-evidence/`。`preferred-media-index.json` 给优选30片的路径/成片SHA；`accepted-media-index-14-initial.json` 与 `temporal/accepted-media-index.snapshot.json` 保留旧28片，不把旧音频/条带索引指向新影片。每个工程的 `source-receipt.json` 绑定执行时捕获脚本、创意输入与环境，随后修改的live源码不继承旧回执。媒体/大文件/字体未上传Git；这些本地路径不是公开下载地址。

## 冻结源码与独审

| 冻结提交 | 实际检查 | 精确边界 |
|---|---|---|
| `dac7457fe628001cedbb648776affdc0b9ef76c9` | 媒体17模块126项，284.533s、零skip；77文件前后SHA一致 | `media-tests-dac7457.log`及`-source.json`；独审读日志并重算清单，不冒领完整重跑 |
| `436306580cd86e4b6080611702b806655d931fae` | HTML11项，98.388s，40文件一致 | 历史冻结；后续修复不得回填此轮 |
| `a29d82ca209d589127901de5766b382263c19454` | HTML13项，75.882s，40文件前后一致 | 最终HTML冻结日志及40文件清单；Node操作/状态，不是浏览器像素 |
| `f3ea10e3c2b4662614cf0a5b2d0dc476d950a576` | D4 recipe字段拆分及15卡/8键轻量检查，独审diff | 未重复完整suite，不标作13项全量重跑 |

独立审查者未编写模板实现。明确反例包括HUD末态错误数值、玻璃端点/小数值、预检来源哈希竞态、storyboard目录/编号/捕获依赖、字幕晚punch与阅读预算、贴纸揭示未完成、Flat/Soft-3D过渡不足、Liquid末态不足、Pixel采集/奖励窗、Bauhaus轴心中途越界及Flat标签跳变；对应局修按实际helpers/Canvas CTM、RGBA或文字调用复验闭合。原独审席后期输出流中断，另有未编写核心实现的验证席接续；不冒称原席完成全部终审。最终源码可在声明限制内交付，不由局部复验推出全部合法输入、浏览器效果或艺术终验。全部细粒度输入、源码SHA与独审归属可由固定复审原文追溯。

## 失败与未验项目

| 原失败 / 缺口 | 处置及不能升级的结论 |
|---|---|
| 初次HTML flat-vector all退出1、无成片 | 原生ELF身份拒绝、Chromium启动失败；无音轨另触发amix=0，加入参考音后prepare/audio成功仅修音频支路，不代表浏览器或all成功。 |
| Chromium sandbox配置错误 | 原始探测exit1/134；没有改权限、禁用sandbox或切换环境绕过。HTML真实PNG、同帧像素、黑场和成片QC仍UNRUN。 |
| GitHub GraphQL `Forbidden` | 当时draft创建exit1、未获成功确认，未给HTTP码；不是推断出的403，也不是自动审批拒绝。后续PR合入不改写当次失败。 |
| 云端六件live挂载MISSING | 未安装/改权限；源码验证不能声称用户挂载BAD=0。 |
| 字幕首次300帧合成 | all/strict退出1、7/8；f150覆盖探针FAIL且f269覆盖奖励文字。按真实alpha范围裁切还原并删冲突cue，另生成修正版通过；旧FAIL不豁免，人物区alpha为零不能证明背景UI安全。 |
| 旧字幕B提前宣布收集/长句读不完 | 分别另改预告语、短句并绑定新片；布局抽审不能证明语义时机或真实阅读完成。 |
| 旧Pixel skyline重置、Synthwave山缝/屏内路标消失、Line满色留时太短 | 全帧静态审查在技术QC通过后发现；修后分别重渲readable/domain工程，不拿稀疏旧contact抵消时序失败。 |
| 旧Iso A叠塔、Collage父子pivot漂移、Sticker/Glass阅读预算不足 | 新分散布局、正确铰链与reading-v2各自重建；旧技术通过不改成旧艺术通过。Collage B结论另由40帧延至100帧并验证源前缀相同。 |
| 低采样3D遮挡/粒噪；旧连续A cube/B torus穿地 | 旧源绑定 `7decc1187d36586ed1959ff4bfabf962d7bf9527`；新ground-fixed另验，不把修复倒填已启动旧任务。 |
| Blender去噪/EEVEE | OIDN不受该build支持；EEVEE发生EGL_BAD_MATCH/只读cache错误，终止exit130。未改权限重试；Cycles CPU是独立已验证媒体路线，不授予失败引擎通过。 |
| 辅助脚本/连接故障 | 旧3D contact将dict当list失败，另修辅助工具退出0不重跑all；一次transport中断经查无进程/无日志确认未执行后仅重发一次，不是权限拒绝。 |
| reading-v2重复后处理 | 第二进程FileExists停止并污染phone辅助stderr；`postprocess-incident.json`与旧audio失败保留。新`audio-verified/`四片errors=[]、phone360帧复解码一致，未将原聚合失败改绿。 |

源码/输入拒绝、几何、固定环境乱序重放、全链技术、静态图像、实时节奏、听感和用户确认是不同层次。没有改内核、阈值或门禁来取得上述成功。

## 新媒体证据闭环

优选路径见状态表；每轮均保留捕获源、实际命令/环境、最终probe及all/strict/decode/blackdetect结果。非浏览器素材经普通broll/timeline路径消费、无gfx clips，不伪造浏览器成功回执。关键新批的独立绑定如下：

| 证据族 | 验证范围与证据文件 |
|---|---|
| `readable/` Pixel/Line/Captions六片 | 源匹配dac7457，1440帧全解码静态序列及native定位；`independent-temporal-review.json`、`temporal/readable-six/filmstrip-index.json`。Line末30张源RGBA相同，Pixel周期边界连续，字幕改短句。 |
| `domain-fixes/` Synth/Flat/HUD六片 | 全链、全帧条带和源匹配dac7457；`independent-temporal-review.json`、`domain-validation-summary.json`。分别核屏内路标、全卡末态和安全名带/正确终值。 |
| `readable/` Sticker/Glass-v2四片 | 1320帧native hash/PTS及完整静态条带；Sticker720帧，Glass独审600帧+12张native；`sticker-v2-independent-temporal-review.json`、`temporal/readable-c-v2/independent-glass-review.json`。 |
| `readable/collage-b-readable` | 240帧；前180张源与旧片相同，末100张同SHA；实际MP4 f140/239解码与所审PNG一致。`independent-readability-review.json`、`source-prefix-identity.json`。 |

旧28片音频审计无技术错误（48kHz、近双单声道、stream时长差0，I−14.8至−14.7LUFS、TP−4.4至−4.2dBTP），但125个视觉事件仅36个距半秒音符网格≤1帧；domain新86项仅31项距实测rise≤1帧。AAC尾部padding不等于stream错位；双声道元数据不等于空间声。readable六片、domain六片、reading-v2四片、Collage B与ground两片均另审新音频，不能复用旧28片报告。数值合格不证明听感或语义同步。

## 连续3D：源重生成与冻结资产消费分账

旧完整A/B绑定上述7decc118，renderer `bdbb38117631cc2711aa645cb6dcd4639fb6cbbfa48cc39e466456888e3645cc`；各120帧技术链通过，但cube最低z−0.05849252、torus−0.01275517，外部f47重生成均有低位像素差。世界网格/相机设置相同只排除已比较状态，不证明根因或完整引擎状态一致。

新 `continuous-ground-fixed/3d-{a,b}` 捕获renderer `c8f1838929c01a56d72f89f0f950b9135175cb2a6cd04e16f2a6aed776f67d40`（匹配dac7457）；Linux6.18.55、Blender4.3.2、宿主Python3.12.14/FFmpeg7.1.5。各120帧/4秒，原生960×540、64spp、CPU4、seed0/animated_seed=false/no-denoise；成片1080p上采样。全部240源PNG与evidence SHA一致，完整序列/输入/源码核对、all/strict8/8/全解码/零黑场/帧数均通过。

新两片完整静态条带及原生关键帧独审完成；两场景120帧world mesh最低z均为正（约0.000009972602/0.000009999610），闭合旧穿地但不是物理模拟终验。A看全部120缩略帧、11张源和5张final原生图，B看全部120缩略帧及final f47/119；不是所有帧原生全检。新A帧报告13.43–47.66s、中位29.86s，B21.07–46.62s、中位29.475s，仅该profile帧报告，不是全链墙钟/性能基准。

| 新媒体 | final.mp4 SHA-256 |
|---|---|
| A | `638ee17254c5e44994f4dd90dd0e8e2ebe2d4f8b4333171f6a0b004d1bb2658d` |
| B | `7dd6a79f5f4b3a7fc8f62c66343f6f971b6ac571208f05a120a2cdb438eecd58` |

外部Blender各新进程仅重渲f47一次：**A严格RGBA FAIL**（518400像素中3个RGB像素差1级，meanabs0.0000019290123456790124，0..255单位）；B该单帧严格相同。`independent-native-frame-replay.json`保留原图/命令/结果，没有改容差、重试筛选或宣称全部外部源逐位一致。

另建 `frozen-consumption-replay/a,b`，复制timeline及全部引用cue.wav/scene.mp4、逐文件SHA相同；各仅一次all与strict均exit0。各120帧decoded RGB SHA、PTS/时长逐行相同，最终MP4 SHA亦相同，见 `frozen-consumption-replay/comparison.json`。这是本环境**冻结资产消费PASS**，不覆盖上述**外部源重生成FAIL**；无浏览器/Blender重启，原工程未改。
