# 音源覆盖缺口与补源普查（timbres_GAPS）

> 生成：2026-08-25 · 音源调研工（吉他轮暴露系统性盲区后的全面盘点）。
> 基线：`catalog/timbres.md`（2026-08-25 `bgm catalog --write`）增量更新；探针数据 `out/timbres_audit/probe_results.json`（GM/SFZ 81 键探针）+ `vcsl_probe.json`（VCSL 6 件）。
> 探针口径：3s 单音（C4=pitch 60 vel 100，bpm 60），指标=RMS/峰值 dBFS+谱质心 Hz+前 50ms 攻击能量占比。质量档判定：多力度分层+多轮替=高；单层单采样=低。
> 许可铁律（红线①）：CC0/MIT/Apache/BSD/CC-BY 可；NC/未声明默认排除；GPL 数据文件记 FLAG。

---

## 一、乐器覆盖矩阵

图例：质量档 高/中/低 ｜ RR=轮替 × 力度层 ｜ 许可缩写：CC0 / BY3=CC-BY 3.0 / BY4=CC-BY 4.0 / GUS=GeneralUser 自有许可 / VPO-L=VPO 自由使用许可（再分发署名） ｜ ★=本轮试听实测。

### 1. 键盘族

| 乐器 | 采样源（档/RR/许可） | 合成源（档） | GM 兜底（档，FluidR3/GU 实测 RMS） | 判定 |
|---|---|---|---|---|
| 三角钢琴 | SalamanderGrand（高 / 多力度 / BY3 署名义务） | — | prog 0 未测（有 Salamander 无需） | **覆盖 OK** |
| 立式钢琴 | VSCO2 UprightPiano（中 / 单层 / CC0，探针 -47.8 偏弱可用） | — | — | OK |
| 电钢 Rhodes/EP | GregSullivan CP80★（中高 / 多力度 / BY3，探针 -33.1 健康）；Pianet T 端口损坏（缺样本） | synth fm_ep（中，两算子 FM） | EP1/EP2（低-中，-31.8/-36.7） | CP80 可引入 |
| 羽管键琴 | **VCSL 5 架**（中 / 56 区 / CC0，Flemish 8'★ -37.2 健康） | — | 6（低，-37.5/-47.3） | **缺口→VCSL 可补** |
| 钢片琴 celesta | VPO Keys/celesta（中 / CC0 同库许可 VPO-L，探针 -35.9 健康） | — | 8（低-中，-39.2/-39.5） | 覆盖 OK（VPO） |
| 音乐盒 | 无 | — | 10（低，GU -47.5 弱） | **缺口（无干净补源确认）** |
| felt piano | 无 | — | — | **缺口（候选许可/格式双卡）** |

### 2. 拨弦族

| 乐器 | 采样源 | 合成源 | GM 兜底 | 判定 |
|---|---|---|---|---|
| 尼龙吉他 | FreePats Spanish classical★（中低 / 单层采样 / CC0，-37.3 健康；官方自述"录音条件不理想"） | synth nylon=KS 物理模型（中，MF 盲听两轮定档） | 24（低，-40.0/-42.3） | KS 主力+FreePats 备胎 |
| 钢弦原声吉他 | 无干净源（FreePats steel=GPL 特例 FLAG；Ovation=未声明） | — | 25（低，-42.5，衰减 13.5s 单采样） | **缺口 top1** |
| 电吉他 | Emilyguitar★（高 / 4 力度×3 RR+release 采样 / CC0，-40.6）；shinyguitar、black-and-green 两仓（CC0 未试听） | — | 26/27/29/30（低，-36.5~-40.6） | Emilyguitar 可引入 |
| 低音吉他（bass guitar） | Karoryfer big-little-bass/fashion/pasta/swag/growly/black-and-blue 六仓（CC0，未装未试听） | synth bass（中） | 33-39（低-中） | 采样可补（按需） |
| 竖琴 | VPO harp 3 变体 sustain/dampened/KS（中高 / VPO-L，-32.1 健康）；VSCO2 Harp（中 / CC0，-50.6 偏弱）；VCSL Concert+ Folk Harp★（中 / 2 力度 / CC0，-47.0） | — | 46（低，-40.1/-39.2） | **覆盖 OK（VPO 主力）** |
| 曼陀林 | Terkelsen's Mandolin★（中 / 2 力度 / **许可未声明=排除中**，-37.6 健康待裁定） | — | 105（低，-47.0/-43.7） | 半缺口（有件无许可） |
| 班卓 | ganjo★（中 / CC0，-44.8 攻击占比 0.79 干脆拨弦）；FlameStudios Kay 5弦（GPL 数据 FLAG） | — | 105 同上 | ganjo 可引入 |
| 尤克里里 | FreePats ukulele 2026-08-11★（中 / CC0 / tenor / C4-C6+2 打击 articulation，-41.8 健康） | — | 无 GM 件 | 可引入 |
| Koto/Shamisen/Sitar | 无采样 | — | 104/106/107（低，sitar -44.7/-42.9） | 缺口（低优先） |

### 3. 旋律色彩族

| 乐器 | 采样源 | GM 兜底 | 判定 |
|---|---|---|---|
| 长笛族 | VPO flute 全演奏法（中高 / VPO-L，-27.7）；VSCO2 Flute 4 变体（中 / CC0）；Ixox.Flute（BY4 候补）；VCSL alto-flute? 无（VCSL 有 recorder/ocarina） | 73（中，-30.0/-32.4） | 覆盖 OK |
| 竖笛 | VCSL Baroque 全家 S/A/T/B×Sustain/Stac/SusVib（中 / CC0，alto sustain★ -47.6，17s 自然延音） | 74（中，-29.4/-30.8） | VCSL 补强 |
| 口哨 | 无采样 | 78（**中，-25.8/-25.5 两库电平最健康件之一**） | GM 兜底即主力 |
| 排箫/瓶哨/陶笛 | VCSL Ocarina Typical/Small + Train Whistle（CC0） | 75/76/79（中，-25~-27 健康） | GM 可用+VCSL 补 |
| 萨克斯族 | **MTG.SoloSax S/A/T/B 全套★（高 / 多力度+legato / BY4，alto 探针 -35.9 健康）**；Karoryfer bear-sax/weresax（CC0 备选） | 64-67（低，未逐件测） | MTG 可引入（现零 sax 采样） |
| 小号 | VPO trumpet 全演奏法（中高 / VPO-L，-26.7；**VPO 失控清单语境注意**）+VSCO2 7 变体（中 / CC0） | 56（中，-33.5/-35.7） | 覆盖 OK |
| 圆号 | VPO/VSCO2（中高，VPO -27.1） | 60（中，-28.4/-32.3） | 覆盖 OK |
| 口琴 | **VCSL Hohner Special20-C/F + Super64×3 力度（中 / CC0，试听未过——稀疏检出目录未含）** | 22（低-中，未测） | VCSL 补 |
| 弦乐合奏/分部 | VPO 164 sfz（高 / 力度层+演奏法全家 / VPO-L）+VSCO2 32 件（中高 / CC0） | 48（中，-34.7/-36.3） | 覆盖强 |
| 大提琴/低音提琴 | Karoryfer 三件（高 / 3-5 力度层 / CC0）+VPO/VSCO2 | 42/43（中） | 覆盖强 |
| 唱诗班 | VPO choir F/M/MIXED（中 / VPO-L）；Karoryfer gogodze-phu vol I/II（CC0 民俗鼓/声） | 52（中低，-34.3/-35.1） | OK |

### 4. 打击色彩

| 乐器 | 采样源 | GM 兜底 | 判定 |
|---|---|---|---|
| 颤音琴 | VPO 3 变体 open/auto-damp/KS（中 / VPO-L，-30.2）；VCSL 软/硬槌+**弓奏**（中 / CC0，软槌★ -44.7） | 11（中低，-41.0/-36.2） | 覆盖 OK |
| 钟琴 glocken | VPO（中 / 高位键实测 -43.5@p84 OK）；**VSCO2 样本电平异常低 -63.5@p84 判不可用**；VCSL+Tubular Glocken（CC0，soft 层★ -60.9 弱=力度层特性非缺陷） | 9（中，-42.4/-47.3） | VPO 主力 |
| 马林巴 | **VSCO2 样本弱 -54~-57 判不可用**；VCSL Marimba（中 / 2 力度 / CC0，soft 层★ -68.6——soft 层弱但 loud 层待验）；Terkelsen Marimba 多力度多采样但**许可未声明** | 12（中低，-39.4/-43.5） | 半缺口（VCSL 主候选） |
| 木琴 | VPO xylo@p84 -47.4 OK；VSCO2 Xylo -38.5 OK；VCSL 3 槌变体（CC0） | 13 | 覆盖 OK |
| 管钟 | VPO tubular bells（中，-42.5）；VSCO2 TubularBells；VCSL ×3（CC0） | 14（中低） | OK |
| 风铃/钟树/雪橇铃/手钟 | **VCSL Bell Tree/Mark Trees/Hand Chimes/Sleigh Bells/Hand Bells（CC0）**；mixkit chimes 类目 one-shot（中） | 112 tinkle bell（低，-40.8/-42.2） | VCSL 补 |
| 定音鼓 | VSCO2 Timpani+Rolls、VPO 全套（中高） | 47 | OK |
| 鼓组 | drums_gdc+sonicpi（one-shot——**2026-09-09 用户试听否决：组装鼓质量差，正常情况下不要使用**，音乐鼓用途禁入；证据=voiceime_bgm_v2/audition 同律动四源对比 C 段 / CC0+Sonniss）；synth kick/snare/hat（中，峰值因子 13.6dB 四源最低=机械感客观指纹）；DrumGizmo DRSKit/MuldjordKit（高 / 多力度多 RR 多麦 / BY4）、Wilkinson Naked Drums（高 / 10 RR×5 力度 / BY4）、virtuosity_drums（CC0）候补 | 118-119（低-中） | **本地鼓源上限受限已实证**——全键位套鼓（DrumGizmo/Wilkinson）为唯一升级路径 |
| 世界打击 | VCSL conga/bongo/frame drum/tambourine×5/triangle×4/cowbell/claves/guiro/cabasa/shaker/agogo/gong×2/anvil/wine glasses（CC0） | 116-117 | VCSL 补 |

### 5. 低音与氛围族

| 乐器 | 采样源 | 合成/GM | 判定 |
|---|---|---|---|
| 低音提琴（弓/拨） | Meatbass（高 / 3-5 力度 / CC0）+sneakybass+dsmolken（CC0）+VPO/VSCO2 | synth bass（中）；GM 32-39（低-中） | 覆盖强 |
| 管风琴 | VSCO2 OrganLoud/Quiet（**样本电平问题：Quiet 探针 -45.3~-61.0 判弱**）；**VCSL Pipe Organ Loud/Quiet+Pedal+Renaissance Organ（CC0）** | 19-20（低-中） | VCSL 补 |
| pad/氛围垫 | 无专用采样 | GM 88/89（中低，GU -35.7/-37.4 尚可垫底）；synth lead/bass 可派生 | **缺口（可合成路线解）** |
| 钢鼓 | jlearman.SteelDrum（Unlicense=公有域）；Kastendieck.SteelDrum（未声明）；VCSL 无 | 114（低） | 可引入（Unlicense 白名单） |
| 非西方旋律件（erhu/古筝/hungarian zither） | aliexpress-erhu（CC0）、hungarian_zither（CC0）、VCSL Dan Tranh（CC0） | 107-111（低） | 按需 |

---

## 二、GM 兜底逐件抽查结论（新证据）

**覆盖矩阵背后的实测**（31 program × 2 sf2 + 19 SFZ 件，`out/timbres_audit/probe_results.json`）：

1. **两 sf2 无静默件**——31 件全部出声（RMS -25.2 ~ -48.4），GM 兜底"能出声"这一层是可靠的。
2. **质量分层**（判据=单采样无力度分层+频谱单薄+电平）：
   - **不可用作主奏**：吉他全家 24-27、Sitar 104/Banjo 105/Koto 107/Kalimba 108、MusicBox 10、Harpsichord 6（GU -47.3）、TinkleBell 112——BGM 需要这些音色时 GM 兜底**不达标**。
   - **尚可垫底/特色**：Whistle 78（-25.8 全场最健康之一）、Ocarina 79、BottleBlow 76、Choir 52、Pads 88/89、EP 4/5、Recorder 74。
   - FluidR3 与 GeneralUser 互有胜负：GU 的 Marimba/Vibraphone/Kalimba 更健康，FluidR3 的吉他/Harpsichord 电平更高；**不必二选一，两库并存按件择优**（现状即如此）。
3. **VSCO2_CE 打击/管风琴电平缺陷**（本轮新发现）：Glockenspiel 高位键 -63.5dB、Marimba -54~-57dB、GM-StylePerc -77.7dB（近废）、OrganQuiet -61dB@vel40——**VSCO2_CE 的 idiophones 与 organ 实际不可直接投产**（需 +20dB 增益补偿或换 VPO/VCSL 同件）。矩阵中 VSCO2 打击一律降档。

---

## 三、缺口清单（按投产影响排序）

| # | 缺口 | 挡住的曲目类型 | 现有兜底 | 影响面 |
|---|---|---|---|---|
| 1 | **钢弦原声吉他采样** | 民谣/流行/乡村色彩 BGM、指弹床乐 | GM 25（低，单采样）；KS nylon 不可替代钢弦亮度 | 高频需求，且无许可干净源（见短名单） |
| 2 | **音乐盒/八音盒** | 童趣/回忆/圣诞/悬疑氛围 BGM | GM 10（低） | 氛围件刚需，候选全部许可/格式卡壳 |
| 3 | **萨克斯族采样**（现零件） | 爵士/城市/怀旧 BGM、solo 色彩 | GM 64-67（低） | MTG.SoloSax CC-BY 已验证可解 |
| 4 | **羽管键琴+竖笛+口琴+管风琴+钟树族**（成组缺口） | 巴洛克/古风/宗教/童话 BGM | GM 6/74/22/19（低-中） | VCSL 单库全解（CC0） |
| 5 | **felt piano** | 忧郁/亲密/慢叙事钢琴床乐 | Salamander 减力度+低通可近似（无独立件） | 无干净源确认 |
| 6 | **高质量 pad/氛围垫采样** | 环境/冥想/科技氛围 BGM | GM 88/89（中低）+synth 派生 | 可走自研合成路线（无许可风险） |
| 7 | 曼陀林 | 意大利民谣/蓝草色彩 | GM 105（低） | 件已试听健康但许可未声明 |
| 8 | Koto/Shamisen/Sitar 等东方弦 | 东方色彩 BGM | GM（低）+VCSL Dan Tranh/erhu/zither（CC0 备选） | 低频需求 |

**修正任务书问号项**（上轮认知偏差）：竖琴不缺（VPO 3 变体健康）；颤音琴不缺（VPO 3 变体+VCSL 弓奏）；钟琴不缺（VPO）；口哨 GM 即可（电平全场最健康档）；吉他缺的是**钢弦原声**而非尼龙（尼龙有 KS+FreePats 双保险）。

---

## 四、补源短名单

### A. 马上可引入（许可+质量双过，试听/取证已完成）

| 缺口 | 源 | 许可证据（实抓） | 质量印象（探针） | 体积/通道 | 风险 |
|---|---|---|---|---|---|
| 成组缺口 #4 | **VCSL 官方仓 sgossner/VCSL**（195 乐器：羽管键琴×5/竖琴×2/竖笛全家/口琴×3/管风琴×2/颤音琴弓奏/钟树/雪橇铃/世界打击/Steinway B/尤克里里无） | 仓 README："open CC0 general-purpose sample library…you can do whatever you want with these sounds (even make commercial software), no royalties, no credit, no special terms."；GitHub license=CC0-1.0 | Flemish 8'★ -37.2 健康；harp -47.0 2 力度；vibraphone 软槌 -44.7；recorder 17s 自然延音 | 全仓 ~4GB（wav）；**git sparse 按乐器抽件**（本轮已验证通道：smpldsnds 镜像稀疏检出+ffmpeg 解码试听 OK；投产用官方仓原 wav） | 设计哲学=轻量（多件 1 RR×2-3 力度），独奏 exposed 处不如商业库；打击件样本电平中等需轨级增益 |
| 萨克斯 #3 | **sfzinstruments/MTG.SoloSax** | GitHub license=CC-BY-4.0 | alto★ -35.9 / 峰 -26.4，S/A/T/B 全+legato 变体 | 208MB git clone（codeload tar 代理下三连截断，走 clone） | BY 署名义务=发布页需记名 |
| 电吉他 | **sfzinstruments/karoryfer.emilyguitar v1.001** | 包内 LICENSE=CC0；官网 "All our free sample libraries are under a Creative Commons Zero license." | -40.6 健康；4 力度×3 RR+release 采样（规格全场最高档） | 103.5MB release zip | Epiphone 电声（非原声）；直录无箱模 |
| 班卓 | **sfzinstruments/ganjo v1.000** | 包内 LICENSE.md=CC0 | -44.8，攻击占比 0.786=干脆拨拨 | 24.3MB release zip | 单乐器小件 |
| 尤克里里 | **freepats/ukulele1 2026-08-11** | 包内 LICENSE.txt=CC0 | -41.8 健康（tenor C4-C6+body tap/chuck 两打击 articulation） | 2.9MB 7z | 域窄（两八度） |
| 尼龙吉他备胎 | **FreePats SpanishClassicalGuitar 2019-06-18** | 页面+包内 cc0.txt=CC0 | -37.3 健康，centroid 419 | 19MB wav 解压后 | 官方自述"录音条件不理想"；单层——**KS 合成主力不变**，此件做真人质感备胎 |
| 电钢 CP80 | **sfzinstruments/GregSullivan.E-Pianos** | 包内 LICENSE=CC-BY-3.0；README "with the author permission with the request for attribution" | CP80★ -33.1 健康 | 10.7MB | **Pianet T 端口损坏勿用**（仓内仅 2/40 样本）；Wurlitzer EP200 未含 |
| 钢鼓 | sfzinstruments/jlearman.SteelDrum | LICENSE=Unlicense（公有_domain 等价） | 未试听 | 38.8MB | 低优先 |

### B. 待用户裁定（有硬伤或边界情形）

| 件 | 问题 | 证据 |
|---|---|---|
| Terkelsen's Mandolin（sfzinstruments） | 许可未声明（仓无 LICENSE，上游 schristiancollins.com 免费件页 JS 渲染抓不到条款）→ 按红线默认排除 | README 仅 "Converted from the original GigaSampler version by S. Christian Collins"；两通道取证无果。质量本身健康（★-37.6，2 力度） |
| FlameStudios Kay 5-String Banjo | **样本数据=GNU GPL**（"The source code being the .gig file (of which contains the audio samples)"）——GPL 数据文件记 FLAG，商用音乐作品解释空间存在但红线要求裁定 | 包内 LICENSE.txt 原文；GitHub license=NOASSERTION |
| FreePats Steel-String Acoustic Guitar | GPL v3+ special exception（Gary Campion 授权）——同上 FLAG | 页面原文 "License version 3 or later, with a special exception" |
| jlearman.jRhodes3d（1977 Rhodes 全长采样） | 双许可：乐手制乐=CC0（作者明文），**样本集再分发=CC BY-NC 4.0**——NC 出现在再分发条款，与红线①"NC 禁入"的边界需裁定 | LICENSE 原文 "License for musicians using this to make music: CC0 / To distribute the samples themselves…CC BY-NC 4.0" |
| MuseScore General（MuseScore_General.sf3） | MIT（musescore.org 手册 "released under the MIT license, Courtesy of S. Christian Collins"）——**许可干净**，但价值=GM 兜底升级（35.9MB sf3 全家桶），与现有两 sf2 定位重叠 | 35.9MB；sf3 需 fluidsynth ≥2.2 支持（本机 2.5.4 OK）；按需 |

### C. 被许可证/渠道挡住（如实）

| 源 | 挡点 |
|---|---|
| Pianobook（felt piano/music box 最大社区库） | 许可非标准化（About 页仅 informally "don't make commercial sample libraries"，terms 页抓取超时 unreachable）+ 格式=Decent Sampler/Kontakt（非 sfz/sf2，管线不兼容）→ **整体不进产线**，个别件如需再逐件裁定 |
| Sonatina Symphonic Orchestra | CC Sampling Plus 1.0（**已退役许可**，不在白名单）；bigcat 原文 "You can use the samples to create any musical work commercial or non-commercial" 但许可标记非标 → 排除（且 VCSL=同生态 CC0 覆盖更广更优） |
| smpldsnds/drum-abuse vol1-5（复古鼓机 61 台 5611 采样） | 仓无 LICENSE（源头=YouTube 合集），未声明默认排除 |
| Karoryfer Marie Ork（声库） | 官网明示 "Only Marie Ork has a different license"（monolithic voice bank 不可开源）→ 排除 |
| VSCO 2 Pro（完整版） | 付费商业库（CE=CC0 子集已是极限）→ 不可 |
| IA/EW（EastWest 等工业库） | 商业授权/授权机限制 → 不可 |
| S. Christian Collins GigaSampler 转换系列（Mandolin/Clavecin/Ovation/DamiensFunky/Terkelsen.Marimba） | 上游免费件页无可抓许可文本（JS 渲染，WebFetch+curl 双通道无果）→ 未声明默认排除，**整族 5 仓一并挂起** |
| OpenSFZ | 无独立实体（搜索证实=sfzinstruments 项目的别称/误传）→ 不存在单独可引库 |
| kernScores 系（BG-D18 已录） | CC BY-NC-SA → 永久禁入（乐谱侧铁律，同源逻辑适用采样侧类比） |

---

## 五、试听下载清单

落位 `$MUSIC_ASSETS_ROOT/samples/_audition/`（总计 751MB，预算 2GB 内）；四要素逐件表=该目录 **MANIFEST.md**；sha256=该目录 SHA256SUMS.txt。

| 件 | 状态 |
|---|---|
| Karoryfer.Emilyguitar.v1.001.zip | 解压 extracted/Emilyguitar/，探针过 |
| ganjo-v1.000.zip | 解压 extracted/ganjo/，探针过 |
| GregSullivan.E-Pianos.tar.gz | 解压，CP80 探针过 / Pianet 缺样本 |
| SpanishClassicalGuitar-SFZ-20190618.7z | 解压，探针过 |
| Ukulele-SFZ+WAV-20260811.7z | 解压，探针过 |
| Terkelsen.Mandolin.tar.gz | 解压，探针过（许可待裁定） |
| MTG.SoloSax（git clone @b494d25） | extracted/MTG.SoloSax/，alto 探针过 |
| vcsl_mirror（smpldsnds @62296c0，稀疏 7 乐器） | 解码探针过 6 件；投产改用官方 sgossner/VCSL |

---

## 六、给主线的落档建议（文案）

1. **VCSL 整库引入为 v3.5 音源基建头号项**：`git clone --filter=blob:none --sparse https://github.com/sgossner/VCSL` 按需抽件（羽管键琴/竖笛/口琴/管风琴/钟树/世界打击优先），CC0 零义务，一次性解掉缺口 #4 全组+部分 #8。assets.json 建议条目：name="VCSL (Versilian Community Sample Library)"，license="CC0 1.0（仓 README 原文+GitHub license 字段双证）"，source="https://github.com/sgossner/VCSL（master，pushed 2026-01-14；releases 无资产，主干即正本）"。
2. **MTG.SoloSax / Emilyguitar / ganjo / FreePats 尤克里里+尼龙 / GregSullivan CP80**：五件试听全过、四要素已录于 _audition/MANIFEST.md，可直接登记（注意 BY 署名义务件：MTG.SoloSax BY4、CP80 BY3——发布页署名清单需同步 docs/REPORT.md §7.6 义务表）。
3. **四件 FLAG 待裁定**（Mandolin / Kay Banjo / FreePats steel / jRhodes3d）：裁定前不投产；若裁定接受 GPL-数据或 CC0-使用+NC-再分发解释，_audition 已备好质量证据。
4. **VSCO2_CE 打击/管风琴电平缺陷**写入坑表：该库 idiophones/organ 投产需 +20dB 或换件，音色编目（timbres.md）建议加"电平警示"注记。
5. **音乐盒与 felt piano 是仅剩的硬缺口**：许可干净源当前不存在（Pianobook 许可+格式双卡）——建议路线=自研合成（music box=正弦+泛音+机械噪声的物理模型，KS 轮先例可复制；felt piano=Salamander 采样层+低通+力度重映射近似），列为 v3.5+ 候选而非等源。
