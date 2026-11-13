# 乐谱数据源登记表（REGISTRY）

> 普查与采集轮：2026-08-25（双工位：采集+深查）。逐源核实许可，全部证据为当日实抓文本/文件，无凭记忆断言。
> 逐件清单见同目录 `MANIFEST.json`（3821 条）；磁盘布局 `$MUSIC_ASSETS_ROOT/scores/`。
> 主线复核已过（2026-08-25）：MANIFEST 机器统计与报告一致、kernScores/Gutenberg 两处引文独立抽查逐字吻合、零 NC 混入；§9 R-* 系列为产线验收规则（BG-D18）。

---

## 1. Mutopia Project —— **收（整库，附 FLAG 子集）**

- **结论**：整库采集。2053 件全部许可可判定，**零 NC**。PD 1070 件（直用）、CC-BY 240 件（白名单，需署名）、CC-BY-SA 743 件（其中 2 件版本未标；**FLAG 待用户裁定**，红线口径：CC-BY-SA 存疑）。
- **获取方式**：
  - GitHub 镜像 `MutopiaProject/MutopiaProject` 浅克隆（仅 .ly 源码 + 元数据，无 PDF/MIDI；clone @ `2144afd6f52d56c5b6995b8b589ef1268b3139f0`）→ `$MUSIC_ASSETS_ROOT/scores/mutopia-repo/`（115MB）。
  - MIDI 逐件取自官网 ftp（URL 与仓库路径同构：`https://www.mutopiaproject.org/ftp/<Composer>/<Cat>/<Piece>/<stem>.mid`；多乐章捆绑件为 `<piece>-mids.zip`），直连、并发 6、UA 自报，2026-08-25 取回 → `$MUSIC_ASSETS_ROOT/scores/mutopia-midi/`（21MB，2056 个 .mid）。官网已无 MIDI 的 308 件中 128 件经 IA 2020-03 快照补缺（逐条 note 标注 `midi-from-snapshot2020-03`），余 180 件以 lilypond 格式收录（`format:"lilypond"`，指向 .ly）。
- **许可模型与证据**：
  - 逐件许可在仓库内 .ly 头部机器可读：新式 `license = "Public Domain"`，旧式 `copyright = "Public Domain"`；17 件仅自由文本声明（已逐条 note 标注 `license-evidence=free-text-match-in-ly`）。
  - 逐件证据引文示例（Symphony5_1.ly 头部）：`license = "Public Domain"`、`footer = "Mutopia-2017/11/05-941"`；每条 MANIFEST 条目带 `license_evidence.url`（raw.githubusercontent 指针）+ 逐字 quote。
  - 全站说明（https://www.mutopiaproject.org/legal.html，实抓）："All music on Mutopia may be freely downloaded, printed, copied, distributed, modified, performed and recorded."，并给出三族许可（Public Domain / Creative Commons Attribution / Attribution-ShareAlike）的商用许可说明——"make commercial use of the work"。
  - 交叉核验：IA 2020-03 快照逐件 rdf `<mp:licence>` 与当前 .ly 头部比对，许可漂移（license drift）件在 notes 标注。
- **数据量**：2053 件；MIDI 2056 文件 + lilypond 180 件（无 midi 者以 ly 收录）；仓库浅克隆 115MB。
- **质量印象**（SMF 读器抽查 6 件）：format 1，主流 1-7 音符轨、191-1628 音符/件，单一 tempo 为主；声部完整度中上（合唱 4 声部、吉他单轨、钢琴 2 轨均见）。LilyPond 源码级（可再渲染）。**注意**：MIDI 是制谱副产品，表情信息有限（力度多为固定档），改编渲染需走仓库自研表情链（参考 fate 工程路线）。
- **注记**：仓库 `unfinished/`（未发布件）未收录；`collections/`（件集分组，如 bachwtk=平均律、beetson=奏鸣曲）可作选曲导航，未单独建条目。

## 2. OpenScore Lieder / String Quartets（GitHub）—— **收（整库，CC0）**

- **结论**：两库整库采集，**CC0 1.0**（最干净档）。
- **获取方式**：`git clone --depth 1`（proxy）@ `6b2dc542…`（Lieder）/ `91c780ac…`（StringQuartets）→ `$MUSIC_ASSETS_ROOT/scores/openscore-lieder/`（835MB）、`$MUSIC_ASSETS_ROOT/scores/openscore-stringquartets/`（现 1.6GB）。
- **许可证据**：两库根目录 `LICENSE.txt` = CC0 1.0 全文（"Creative Commons Legal Code — CC0 1.0 Universal"）；GitHub API 许可检测同为 `CC0-1.0`。逐件条目 evidence url 指向该文件。
- **数据量与格式**：Lieder 1462 件（`.mxl` 压缩 MusicXML + mscx/mscz/歌词 txt/RomanText 和声分析，19 世纪艺术歌曲，107 作曲家）；StringQuartets 122 件（`.mxl`，47 作曲家，海顿-巴托克范围）。**取舍记录**：SQ 库 613 个 PDF（~1.4GB 级）已从工作区删除以控预算，git 对象保留可 `git checkout` 恢复；Lieder 全保留。
- **底层作品版权核验**：两库 composers.tsv 卒年统计——Lieder 最晚卒年 1949、SQ 最晚 1950，全部满足 life+70（2026 口径）；语料许可（CC0）管转写/制谱层。
- **质量印象**（mxl 抽查 6 件）：Lieder 2 分部（人声+钢琴）、68-246 小节、774-1994 音符；SQ 4 分部（四重奏全编制）、单文件含全曲 13k-23k 音符——**MusicXML 完整符号级（力度/踏板/歌词全在）**，质量显著高于 MIDI。
- **注记**：逐件 musescore.com 官方页 URL 已从库内 data/scores.tsv join 进 MANIFEST `source_url`。
- **派生 MIDI 库（2026-08-27，闲时任务四 R3）**：`$MUSIC_ASSETS_ROOT/scores/openscore-midi/{lieder,sq}/`——自研 MusicXML→MIDI 转换链产物（1583/1584 件，260 万音符；vel 恒 64 无表情底稿、反复未展开按书写序单遍；1 件跳过=15 (part,voice) 对真满件）。抽检：6 件 mutopia 交叉源 Voice 相似度全 ≥0.95（音高级解析错 0）+4 件 roundtrip 零错配。入库清单 [OPENSCORE_MIDI.md](OPENSCORE_MIDI.md)、全量 inventory `out/openscore_midi_inventory.json`、dataset 登记 MANIFEST `datasets.openscore-midi`。

## 3. Open Goldberg Variations / Open Well-Tempered Clavier —— **不采（MusicXML 登录墙）**

- **结论**：谱面（MusicXML/MIDI）现仅在 musescore.com 各曲页提供，**MusicXML 导出需登录账号**（musescore.com 机制，见 §5），无干净无头批量路径；官方站现为 Bandcamp 落地页，无谱面文件。WTC 官方站 welltemperedclavier.org 仅音频零售链接，无谱面下载。
- **许可证据**（实抓）：opengoldbergvariations.org 首页称录音与乐谱 "free of copyright (all uses allowed)"（未用 CC0 字样；Wikipedia 记载 CC0，本项目不以其为准）；IMSLP BWV 988 页实抓见 Open Goldberg 录音条目标注 "Creative Commons Zero 1.0"（音频，非谱面）。
- **替代覆盖**：同曲目已由本采集覆盖——Mutopia `bachgb` 件集（Goldberg BWV 988 全变奏）与 `bachwtk` 件集（平均律，k.s.norstum 制谱）均已入库（许可随件）。IA 有 CC0 录音（如 archive.org/details/OpenGoldbergVariations），属音频素材不在本轮乐谱范围。
- **若用户坚持要 Open 版 MusicXML**：需人工注册 musescore.com 账号逐曲导出（32+96 曲），或等 musescore API key 申请——成本高，不建议。

## 4. Internet Archive —— **部分收（Mutopia 2020-03 MIDI 快照）；无其它干净成规模机器可读乐谱集合**

- **收录**：item `free-midis-public-domain--classical-music`（= mutopia-2020-03.zip，23.7MB）：5209 个 .mid + 2123 个逐件许可 rdf + 236 log。licenseurl = `creativecommons.org/publicdomain/mark/1.0/`（PDM 标记，第三方上传者 John T. Prince 所标）。用途：① 官网缺 MIDI 的 128 件补缺；② 逐件 `<mp:licence>` 与当前 .ly 头交叉核验。落盘 `$MUSIC_ASSETS_ROOT/scores/ia-mutopia-snapshot-2020-03/`（98MB 解包）。**许可判定仍以 Mutopia 官方逐件声明为准**（PDM 为镜像者标注，非权威）。
- **未收**：IA 上 Musopen 系列 item（musopen-chopin 等，CC0/PD）均为**音频录音**非乐谱；搜索 `licenseurl:publicdomain + midi/musicxml + software/texts`（84/2219 命中）无古典乐谱机器可读成规模集合（命中均为流行歌 tab、软件、扫描 PDF）。
- **质量印象**：快照 rdf 是 Mutopia 官方 piece-data schema（`http://www.mutopiaproject.org/piece-data/0.1/`），字段全（title/composer/opus/style/source/licence/midFile/id），可直接复用为未来增量同步的比对基线。

## 5. MuseScore.com —— **机制调研（不批量）**

- **机制结论**：逐曲页有机器可读的 `License` 行（Score info 表，`<tr><td>License</td><td>…`），批量获取受 Cloudflare 反爬门控——裸 curl 取回 challenge 页（15KB），无 JS 通道（WebFetch 403，webReader 可读但属付费池工具）；MusicXML 导出需登录。**判定：非干净无头路径，产线不采**；其 PD 语料的机器可读分发已由 OpenScore GitHub 镜像（§2，CC0）覆盖。
- **抽样验证（2 件，webReader 实读）**：
  - openscore-lieder-corpus/scores/7086632（Debussy *Les cloches*）→ "License: Public Domain — This score is in the public domain. May be used freely for any purpose."
  - openscore-lieder-corpus/scores/5093434（Schubert *Nachtviolen*）→ 同上 "Public Domain"。

## 6. 排除项（一句结论登记）

| 源 | 结论 |
|---|---|
| kernScores / CCARH（含 GitHub `humdrum-tools/bach-wtc` 镜像） | CC BY-NC-SA——NC 禁入，排除（既有裁定）。 |
| IMSLP 合成板块（performance recordings） | CC BY-NC——排除（既有裁定；本次另实抓确认 IMSLP 页对 Open Goldberg 录音标 CC0，但那是上传者自标音频，不改变合成板块整体 NC 口径）。 |
| Project Gutenberg 音乐部 | Copyrighted——排除（既有裁定）。 |
| freemidi / mididb 等无许可声明站 | 来源不明默认排除（既有裁定）。 |
| jsbach.net MIDI 页 | 无许可声明（本轮经 IA Grossman item 描述间接发现）——排除。 |
| WIMA（Icking 归档并入 CPDL） | 许可逐件混杂且站点不可达性高，未展开——默认排除。 |

## 7. FLAG 清单（待用户裁定）

1. **Mutopia CC-BY-SA 子集 743 件**（`flag:"FLAG:CC-BY-SA(pending-user-ruling)"`，其中 2 件版本未标）：ShareAlike 传染性对"改编成自研引擎渲染的 BGM 成品"是否构成衍生作品义务，需用户裁定；裁定前该子集只检索不投产。注意其中含大量核心曲目（如 ksnortum 制谱的 WTC 全集、部分 Bach/Grieg 件）。
2. **Mutopia 2 件 CC-BY-SA 版本未标**（原始声明仅 `cc-by-sa`，Szervács 3 件中 2 件）：同上 FLAG 且版本未知，从严处理。
3. **Mutopia 180 件 ly-only**（官网与快照均无 MIDI）：LilyPond 渲染器未装（本机无 lilypond 二进制），暂为源码形态；投产需装 lilypond 或走 MusicXML 转换链。

## 8. 汇总统计（2026-08-25 定稿）

- 条目总数 **3821**：Mutopia 2237（midi 2057 + lilypond 180；许可分布 PD 1183 / CC-BY 250 / CC-BY-SA 804——**条目级**，含 fate 参照条目；**件级**为 PD 1070 / CC-BY 240 / CC-BY-SA 743，见 §1）+ OpenScore Lieder 1462 + StringQuartets 122。
- 打标（tags）条目 156：知名作品按公认性格打标（规则表内置于采集脚本 `$MUSIC_ASSETS_ROOT/scores/tools/compile_manifest.py` TAG_RULES），其余仅结构信息（style/instrument/opus 在 notes）。
- 磁盘新增合计 **≈2.68GB**（≤5GB 预算内；不含他工位 `scans/` 18MB——那是 IMSLP 扫描镜像工位产物，与本采集无关）。
- 各库 commit 锚点与说明见 `MANIFEST.json` 顶层 `source_commits`/`notes_top`。

---

# 深查工位附录（甲/乙/丙，2026-08-25）

> 深查工位（许可核查与路线评估）产物，与上文 §1-8（采集工位）互补：§9 甲=逐站法理深查（把 §6 排除表中的"既有裁定"升级为第一方逐字证据）；§10 乙=管弦作品钢琴缩谱间接路线盘点；§11 丙=OMR 工具可行性。方法纪律：每站直查（WebFetch/web_reader/本机代理 curl `HTTPS_PROXY（由使用者自行配置）`），逐字引文以当日实抓为准；打不开处如实标注。

## 9. 甲、源站许可深查（逐站登记）

### 9.1 IMSLP（imslp.org）——缩谱/扫描主力源（限标签白名单）

- **辖区口径**（服务器在加拿大）："IMSLP's main servers are located in Canada."（IMSLP:Copyrights）；"As the IMSLP servers are physically hosted in Canada, IMSLP has to follow Canadian copyright law."（IMSLP:FAQ）；"Any work that is in the public domain in Canada can be uploaded through the main IMSLP servers (located in Canada)."（IMSLP:Creative Commons and Performance Restricted licenses）
- **辖区判据**（IMSLP:Copyright Made Simple，逐字）：
  - Canada: "A work is in the public domain if the last surviving author/librettist/arranger died before 1972."（附注：加拿大 2023 起非追溯延至 life+70，仅适用 1972 年后身故者）
  - US: "Any work first published before 1931 is in the public domain."（2026 年口径）
  - EU/life+70: "Died before 1956: YES"；遗作 "…may be under copyright for 25 years from the date of first publication or performance."
  - Taiwan/China/香港（life+50）: "Died before 1976: YES"
- **扫描不产生新版权**（关键）："Note that scanning or transferring an existing edition or recording does not create a new copyright (as Canadian law does not allow for new copyright in mechanical/procedural/literal copying)."（CC/PR 页）
- **上传件政策**："Creative Commons 'No Derivatives' licenses will no longer be allowed for new submissions. Creative Commons 'Non-Commercial' licenses are deprecated and their use is strongly discouraged. … Note that these policies do not apply to items submitted before these changes were announced."（**NC 存量仍在库**）；Performance-Restricted："PR-licensed files are not free for any purpose"；平台承诺 "The copyright status of every file is marked, and it is clearly indicated if it is not legal to download a file in the EU."
- **实际标签全集**（当日 9 个作品页 1336 条目解析实例）：`Public Domain`；`Public Domain (dedicated)`；`Public Domain - Non-PD EU`；`Public Domain - Non-PD US`；`Public Domain - PML-Asia, Non-PD Canada, Non-PD US`；`Public Domain - Non-PD US, Non-PD EU`；`Public Domain - See notes on copyright status for urtext editions`；CC Attribution/Attribution-ShareAlike/Attribution-NonCommercial(-ShareAlike)/Zero 1.0 各型。条目右侧三旗列（CA/US/EU）：V=可用、C=受限、N/A=不适用、**数字=该辖区剩余年限**（实例：Holst 1949-51 Curwen 版 US 列=47）。**勘误：任务书假设的 "NLR" 标签在现行 IMSLP 体系不存在。**
- **验收规则（R-IMSLP）**：① 仅收 `Public Domain`（无 Non-PD 后缀）且三旗全 V，或 `Public Domain (dedicated)`（即 worldwide PD）；② 带 `Non-PD XX`=FLAG（辖区受限），除非用户就分发辖区明确裁定；③ urtext 标签可用但四要素记录出版社+年代，不引用其评注；④ CC 件：CC0 收、CC-BY 收（记署名）、CC-BY-SA=FLAG、一切 NonCommercial=禁入、Performance-Restricted=禁入；⑤ 站内下载有 JS 防爬（friendlytest/friendlyredirect，curl 不可用），批量取件走 IA `imslp` collection（见 9.8）。

### 9.2 kernScores / CCARH——禁入（第一方铁证封存，补强 §6 既有裁定）

- 主站 kern.humdrum.org 当日 503（两通道一致）；kern.ccarh.org 正常但首页无许可页。
- **第一方引文**（KernScores 数据上游仓、维护者 Craig Sapp 本人，`https://raw.githubusercontent.com/craigsapp/mozart-piano-sonatas/main/LICENSE.txt`）：
  > "A digital edition of Mozart piano sonatas from volume 20 of the Alte Mozart-Ausgabe (1878) … Copyright (C) 2004-2018 Craig Stuart Sapp … **Licensed with Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0)** https://creativecommons.org/licenses/by-nc-sa/4.0"
- kern.ccarh.org 首页实见："For users with access to **copyright restricted** materials, login below:"
- humdrum-data 元仓无整体许可（README 逐字指向各上游仓 "repository-license: URL to License information for the repository (if any)"）；第三方佐证：Zenodo 3551003 标 CC BY-NC-SA 3.0、欧盟 Polifonia 再分发按 CC BY-NC-SA 4.0（网络检索佐证）。
- **封存结论**：禁入（NC 红线）；**kern/Humdrum 溯源数据（含第三方转手）同罪**。

### 9.3 Project Gutenberg 音乐部——禁入（实证 §6 既有裁定）

- eBook #156（贝五，producer Geof Pawlicki）页面 Copyright 字段逐字："**Copyrighted. Read the copyright notice inside this book for details.**"（gutenberg.org/ebooks/156）。PG 文字类 PD 惯例不可外推至音乐部。

### 9.4 Musopen（musopen.org）——次要源（保守口径；逐字引文待补录）

- CF 盾：WebFetch 403、curl 挑战页、web_reader 复抓 500/CF。首次 web_reader 成功抓取确认 FAQ 结构（Q1 "What is copyright?" … Q4 "Can I add content to Musopen that I got elsewhere?" Q5 "What content on Musopen can I use and reuse…"），但正文在我处截断——**逐字引文未固化，待人工浏览器一次补录**（musopen.org/faq/）。
- 结论（保守口径）：用户自担 PD 表示+平台不逐一审查+DMCA 兜底；录音/乐谱 PD/CC 混布；**未见 MIDI 分发栏目**（与先验"Musopen 无 MIDI"一致）。
- R-Musopen：仅收逐件明示 PD（含出处）的扫描或 CC0/CC-BY 件；优先级低于 IMSLP；录音类与符号路线无关。

### 9.5 CPDL / ChoralWiki——次要源（逐件混布）

- 站点页脚逐字："Web page content is available under the CPDL copyright license; **please see individual editions for their copyright terms**."
- Help:Copyright Issues FAQ 逐字："The CPDL Copyright is a type of open-source license which allows the end user to use a score freely…"（copyleft 型）；"No, although most scores in the database have a CPDL copyright, there are several other types of copyrights… a **non-profit copyright which allows only non-commercial uses**. Some editors also stipulate that their scores can only be used for religious purposes."
- 结论：合唱编制为主、现代制谱为主，对本产线供给≈0；R-CPDL：仅收逐件 PD/CC0/CC-BY，CPDL 版权（copyleft）=FLAG，NC/宗教限制=禁入。

### 9.6 MuseScore.com——机制（补强 §5：官方许可选项表）

- 官方 Score Uploading Guidelines 逐字（许可选项表）：
  > "None (all rights reserved) … No reuse allowed."
  > "Creative Commons – Attribution (CC-BY) … **Anyone can reuse, adapt, or share your score, including commercially, if they credit you.**"
  > "Attribution – Non-Commercial … Allows remixing and adaptation, but not for profit."
  > "Public Domain (PD) | **Your score and the underlying music are both free of copyright.** | Anyone can use the score for any purpose."
  > "Creative Commons Copyright Waiver | **You waive all copyright to your own score.** | The score becomes fully public domain."
  > 上传表单警示："Do not uncheck this box unless adding an original composition, or you are 100% sure that the composition is within the public domain."
- **CC-PD 与真奉献区别**（据官方文案）：PD=上传者断言"谱+底层音乐均无版权"（自担）；Copyright Waiver=仅放弃自己制谱贡献（CC0 式），底层作品在版权期时 Waiver 救不了底层。
- R-MuseScore：仅收谱页明示 PD（且底层作品独立核实）或 CC0/CC-BY；NC/ND/ARR=禁入；入库记 URL+许可行摘录+取回日期。

### 9.7 维基百科/维基共享资源 MIDI——污染说今日不成立

- 实测：Commons API 文件名空间检索 `kernscores`、`"kern.humdrum.org"` **零命中**；enwiki 全文仅 "List of online digital musical document libraries" 提及 kernScores。
- 结论：系统性 kernScores 污染**无可检索证据**；真实风险=个别误标。R-Wikimedia：不因 Commons 免检；逐件看文件页许可+来源栏，凡 kern 溯源字样立即弃用。

### 9.8 Internet Archive——item 级标记=上传者自填（另：发现 IMSLP 官方镜像馆藏）

- 官方 Rights 帮助页（help.archive.org/help/rights/）逐字："**The Internet Archive does not make guarantees as to the copyright status of items on archive.org and cannot guarantee information posted on item details or collection pages regarding copyright or other intellectual property rights.** Our Terms of Use require that users make use of the Internet Archive's Collections at their own risk…"；"The person who uploads an item often provides information related to use rights, either by way of directly entering it in the description field or by selection of a Creative Commons license."
- **实用发现**：IA 存在 **`imslp` collection**（IMSLP 官方上传的镜像馆藏，含 Sibley 系扫描与 Pf4h 缩谱），可绕 IMSLP 站内 JS 防爬批量直取，且权利口径沿用 IMSLP 审核（双站互核）。
- R-IA：优先取 `collection:imslp` 条目；其它条目逐件核 rights/licenseurl/description；`inlibrary`/`printdisabled`（借阅受限）与加密 pdf 一律不采（反面实例：`picturesatexhibi0000muss`=1997 B&H 版借阅受限件，已拒）。

## 10. 乙、管弦作品钢琴缩谱间接路线可用性（IMSLP 实查 2026-08-25）

> 9 个作品页全量解析（1336 条目，含 Arrangements 区逐条）；三旗=CA/US/EU。结论：**八件全部存在可商用 PD 缩谱扫描**（霍尔斯特带 US 辖区脚注）——间接路线（缩谱 OMR→符号→自研引擎重配器）材料层面无阻碍。

| 作品 | 可用性 | PD 优选条目（IMSLP id / 大小 / 标签） | FLAG / 禁入 |
|---|---|---|---|
| 贝五 Op.67（全乐章） | 极好：Liszt 独奏 S.464/5 ×3 版；4 手 Czerny/Meves/Scharwenka/Schneider/Ulrich；2Pf8H Kirchner/Burchard | Liszt #61518（5.58MB/43pp，Liszt Gesamtausgabe=Breitkopf 刻版）；Schneider 4 手 #163238（5.46MB/51pp，V/V/V）；**已下载** IA Pf4h 镜像 | Czerny 4 手制谱 #1015960=CC-BY-SA（FLAG）；Isida 系 CC-BY/BY-SA |
| 贝七 Op.92 | 极好：Liszt S.464/7 ×3；Czerny 4 手；Naumann 2Pf；Pauer/Diabelli 独奏 | Liszt #61521（6.30MB/52pp）；Czerny #22228（5.30MB/65pp，V/V/V）；IA 镜像 PMLP01600-…Pf4h.pdf（7.2MB）可直取 | Kalkbrenner 件=CC-BY-SA（FLAG） |
| 贝九 Op.125 | 极好：Liszt 独奏 S.464/9 ×3；**Liszt 双钢琴 S.657**；Czerny/Meves 4 手 | Liszt 2Pf S.657 #112352（9.41MB/108pp，V/V/V）；Czerny 4 手 #533891（12.81MB/83pp）；**已下载** IA Pf4h 镜像 | Knuth 合唱+钢琴=CC BY-NC-SA（**禁入**）；#890768 制谱=CC-BY-SA（FLAG） |
| 德九 Op.95 | 好：4 手 ×2；独奏 Juon/Goetschius/Posa | 4 手 #03494（2.96MB/72pp，V/V/V）；#970074（6.90MB/37pp，V/V/V）；Juon #09927（3.02MB/33pp） | Goetschius #256008 三旗 V/24/14（US/EU 未到期→FLAG）；Nacar=CC-BY-SA（FLAG） |
| 霍尔斯特《行星》Op.32 | **特殊**（Holst 卒 1934；双钢琴=作曲家亲编） | ①1914 手稿（RCM 扫描）#774783/84/85（8.5/13.3/6.8MB，`Public Domain` V/V/V——手写谱 OMR 最难）；②Curwen 印刷版 1949-51 #84968（8.86MB/111pp，`Public Domain - Non-PD US`，US 列=47→约 2044） | ③Sigal 独奏=CC BY-NC-SA（**禁入**，下载量最高件务必避开）；管弦原版 1919-21→PD-US。**US 分发只能手稿版或等 2044；非 US 分发可用 Curwen 版**——入线需用户拍板 |
| 柴六"悲怆"Op.74 | 极好：**作曲家亲编 4 手** ×3；2Pf Langer/Schaefer；独奏 Klengel/Pachulski/Clutsam | 4 手 #161063（7.98MB/75pp，V/V/V）/ #27087（7.34MB/97pp）；Klengel #83304（5.89MB/52pp，V/V/V） | 无 NC 件 |
| 柴《罗密欧与朱丽叶》TH 42 | 好：独奏 Bial；4 手 Rimskaya-Korsakova+佚名；2Pf Klindworth | Bial #57480（2.23MB/21pp，V/V/V，13k 下载）；4 手 #17501（2.69MB/38pp，V/V/V） | Vincent=CC BY-NC-SA（**禁入**）；Leonov=CC-BY（可入） |
| 穆索尔斯基《图画展览会》 | **钢琴原版=原作**（多版 PD 扫描） | 原版 #85544（7.12MB/34pp，V/V/V）/ #272845（2.52MB/48pp）；**已下载** IA 1886 Bessel 版镜像 | Ravel-Starybrat 2Pf=CC BY-NC-SA（**禁入**） |

### 抽样下载清单（3 份，PD/IMSLP 审核镜像，经 IA `imslp` collection 直取）

| 绝对路径 | 大小 | sha256 前 16 | 来源 |
|---|---|---|---|
| `$MUSIC_ASSETS_ROOT/scores/scans/mussorgsky_pictures_1886_bessel_piano_original.pdf` | 1,749,135 B | `0a73559cd8650835` | archive.org/details/imslp-at-an-exhibition-mussorgsky-modest |
| `$MUSIC_ASSETS_ROOT/scores/scans/beethoven_sym5_piano_4hands_imslp_mirror.pdf` | 5,728,090 B（~51pp） | `f24d649405221640` | archive.org/details/imslp-no5-op67-beethoven-ludwig-van |
| `$MUSIC_ASSETS_ROOT/scores/scans/beethoven_sym9_piano_4hands_imslp_mirror.pdf` | 10,354,623 B（~107pp） | `96a4a35a6b82eb31` | archive.org/details/imslp-no9-op125-beethoven-ludwig-van |

注：IMSLP 站内 `/images/*` 直链当日被 JS 闸拦（nginx 302→friendlytest.html，cn 镜像同闸），抽样改走 IA 官方镜像完成（同一扫描，权利口径一致）；>20MB 件（78-120MB 扫描）未下载仅登记。

### 《春》RV 269 增补（闲时任务三 R1，2026-08-27）——IA 镜像失效取证 + 浏览器会话直取

- **IA `imslp` 镜像对本作品无可用件**（元数据 API 实测）：①`imslp-concerto-in-e-major-rv-269-vivaldi-antonio` 条目挂载文件实为 Rousseau《Les Printems》长笛曲（PMLP126432，错配上传）；②全库唯一"四季钢琴改编"件 `imslp-448513-…` 实际 collection=`opensource`（标识符前缀 `imslp-` 仅碰瓷），来源=Anna's Archive 影子库，rights/licenseurl 双空——R-IA 逐件核验拒收，且 AA 副本不问字节等价性一律不采。→ 按 §12 存疑项 5 第二通道「带浏览器会话」直取 IMSLP 站内（单件取证，≤3 候选，不登录，无脚本循环）。
- **候选分检（三分支）**：(a) 合格=`给小提琴和钢琴 (Vilaprinyó)`，**Public Domain (dedicated)**（三旗 !N/!N/!N=无辖区跟踪需求），Jordi Vilaprinyó 自制谱排版（上传 2022-09-23），*#811966，14 页 0.58MB——**已下载**，见 MANIFEST `imslp-scan-811966`；(b) FLAG=`给钢琴四手联弹 (Toni)`，Alceo Toni (1884–1969)，Milan: Notari 1920，标签 `Public Domain - Non-PD EU`（三旗 V/V/40，EU 待 2040）——**登记停用待用户辖区裁定**；(c) 跳过=给钢琴 (Bird)=CC-BY-NC、给钢琴 (Novegno)=CC-BY-NC-SA、给小提琴和钢琴 (Barrero Salinas)=CC-BY-NC、Full Scores 区 Mutopia/Sawano/Daily/Käfer=CC-BY/BY-SA 系（Sawano/Daily/Käfer 可商用但非缩谱形态，不取）。**结论：《春》站内无干净 PD 钢琴独奏/四手缩谱，干净件=小提琴+钢琴形态（钢琴声部即乐队缩谱）**。

## 11. 丙、OMR 可行性

### 11.1 Audiveris 事实卡（GitHub API + raw 实查 2026-08-25）

- 仓库 `Audiveris/audiveris`，许可 **AGPL-3.0**（API license 字段+LICENSE 文件头）；最新版 **5.11.0**（2026-07-11）；资产含 Ubuntu 22.04/24.04 deb / macOS dmg / Windows msi。
- README 逐字："Since the release 5.5, an installer is provided for each of the main OSes (**Windows**, **Linux** and **macOS**) and **comes with a pre-installed Java Runtime Environment (JRE)**."——自带 JRE，系统 Java 非硬依赖（本机系统 OpenJDK 25.0.4 亦满足）。
- **免 sudo 安装已验证**：`ar x` + `tar xf` 解 Ubuntu 24.04 deb → `$MUSIC_ASSETS_ROOT/tools/audiveris/opt/audiveris/`（bin/Audiveris + lib/app/*.jar + lib/runtime 自带 **Zulu OpenJDK 25.0.3 LTS**）。无头 CLI：`-batch -transcribe -export -output <dir> <input>`。
- **AGPL 义务边界（只引条文不下法律结论）**：LICENSE §0 逐字 "To 'propagate' a work means to do anything with it that, without permission, would make you directly or secondarily liable for infringement under applicable copyright law, **except executing it on a computer or modifying a private copy**."；§13 逐字 "…if you modify the Program, your modified version must prominently offer all users **interacting with it remotely through a computer network** … an opportunity to receive the Corresponding Source of your version…"。事实陈述：本地无头批处理=纯执行（§0 明文排除）；产线当前用法不修改其源码、不分发其副本、不对外提供其网络服务——义务触发点均不涉及；若未来改源码或对外部署服务须重对 §13。

### 11.2 单文件试点（贝五四手版，前 8 页）

- 第一轮（页 1-8）失败：页 1 为封面无谱表 → `StepException: Sheet removed` → `Error in reaching step PAGE` 整本中止。**坑：须跳过封面/空白页（`-sheets 3-8`）。**
- 第二轮（页 3-8，约 2-4 分钟）**成功**：导出 `$MUSIC_ASSETS_ROOT/scores/scans/omr_pilot_sym5/beethoven_sym5_piano_4hands_imslp_mirror.mxl`（47.7KB MusicXML）。
- **质量指标**：3 个 score-part（primo/secundo 分部正确）；1464 小节、3395 音符、493 和弦附加、1039 beam、249 slur；**调号/拍号识别正确**（首小节 key fifths=-3 即 c 小调三降 ✓、time 2/4 ✓）；力度记号 0（日志警示 `*** No installed OCR languages ***`——deb 解包未含 Tesseract 语言数据，文本/力度识别降级；后续需装 traineddata 才能补）。
- **印象结论**：谱面骨架（音高/节奏/和弦/连奏）自动恢复可信，**可用作人机协同校对的底稿**；直接无人值守投产不够（力度文本缺失+老刻版噪声），产线建议"OMR 出底稿→符号层校验→自研引擎重配器"三段式。
- **勘误（2026-08-27，《春》轮深查实物）**：上文"3 个 score-part（primo/secundo 分部正确）"与 .mxl 实况不符——实为 `P1=Voice / P2=Voice / P3=Piano`（Audiveris 缺省命名，无 primo/secundo 语义）；"1464 小节"系 488×3 跨 part 加总非真实小节数；P1/P2 呈休止主导幽灵声部形态（475-480 小节段）。结论不受影响（底稿可用性维持），但分部命名与计数口径以此勘误为准。

### 11.2.1 《春》轮 Audiveris 增补坑（2026-08-27，run1-run5 实测，证据 $MUSIC_ASSETS_ROOT/scores/scans/omr_spring/）

1. **movement 切分导出覆盖**：输入含下一乐章标题页→Audiveris 切多 Score 且无名→全部导出同名 `*.mvtnull.mxl` 相互覆盖（run1 残件=18 小节 Largo）。处置：按乐章页范围切输入，勿跨乐章标题页。
2. **多 PNG 文件参数=各自独立 book**：命令行传 N 个 PNG→N 个 book 各自导出，不合并。处置：先合成多页 TIFF（`magick … -compress lzw`）再喂=单 book 多 sheet。
3. **PDF 直喂分辨率固定 ~300DPI 且不可调**：born-digital 谱 interline 仅 18px（<20 经验线）；400DPI 灰度 TIFF 路径 interline 24-25px，**小提琴快速音群段音符多恢复 28%（606→777）、时值缺口小节 8→2**——小谱距/密集音型件一律走 400DPI TIFF 路径。
4. **.mxl zip 容器 sha 跨跑不稳但内容确定**：zip 时间戳致容器 sha 漂移，**inner MusicXML 逐字节相等（run2/run3 实证）**——复现性验证以解包后 inner XML sha 为准。

### 11.3 其它 OMR 选项（点到为止）

- **oemer**（PyPI 0.1.8，MIT，仓库 BreezeWhite/oemer）：深度学习端到端；依赖 onnxruntime-gpu/opencv/scikit-learn 等 11 项 pip 依赖——按"零新增 pip 依赖"纪律**须用户裁定**（且须隔离环境勿染 comfyui env）。
- **homr**（liebharc/homr，AGPL-3.0，Rust，368★）：同 AGPL 的新一代候选，备用。
- **SmartScore**（商业）：任务口径直接排除。

## 12. 深查工位存疑项与建议落档

**存疑项（待用户/后续）**：
1. **CC-BY-SA 全线 FLAG**（IMSLP/MuseScore/CPDL 各 ShareAlike 件，与 §7 FLAG 清单同源）：识别产物是否构成衍生作品义务敏感，未裁定前不入产线。
2. **霍尔斯特《行星》**：US 分发只有 1914 手稿（OMR 难）或 2044 后 Curwen 版；非 US 可用 Curwen 印刷版。入线与否、入哪个辖区，用户拍板。
3. **Musopen 逐字引文未固化**（CF 盾）：人工浏览器一次补录 FAQ 关键句；不影响保守验收规则先行。
4. IA 下载的贝五/贝九 Pf4h 镜像**版次归属待核**（IMSLP 站内 JS 闸挡住逐条比对；不影响权利口径，只影响登记精度）。
5. IMSLP 站内 JS 防爬 → **批量采集必须走 IA `imslp` collection 或带浏览器会话**（与 §5 MuseScore CF 结论同为本轮两大采集通道约束）。

**建议落档文案（草拟）**：
- 红线补充："乐谱来源验收=REGISTRY §9 R-IMSLP/R-Musopen/R-CPDL/R-MuseScore/R-Wikimedia/R-IA 规则；kernScores（第一方 LICENSE 铁证 CC BY-NC-SA 4.0）与 Project Gutenberg 音乐部（#156 'Copyrighted.'）永久禁入；CC-BY-SA 件未裁定前不入产线。"
- 命运三四乐章注记："干净机器谱缺失结论维持；间接路线材料已备齐（§10 表，八件全有 PD 缩谱）；工具链 Audiveris 5.11.0 免 sudo 装于 `$MUSIC_ASSETS_ROOT/tools/audiveris/`（AGPL，本地无头执行按 §0 条文不触发分发义务），试点已出 MusicXML 底稿（§11.2）。"

---

# 鼓谱/律动库采集轮（2026-08-25 第二轮，§13）

> 动因：用户裁定口播床走鼓驱动路线（防困、弱叙事、不用扩散模型、不原创）→ 鼓的"Mutopia"是否存在。结论：**存在**——Google Magenta 两库为真人鼓手演奏的符号级鼓谱，CC BY 4.0 白名单内。

## 13.1 Groove MIDI Dataset（GMD）——收（CC BY 4.0）

- **官方页**：https://magenta.withgoogle.com/datasets/groove（代理实抓）。许可证逐字："The dataset is made available by Google LLC under a Creative Commons Attribution 4.0 International (CC BY 4.0) License."
- **内容**：1,150 个 MIDI（**beat 503 / fill 647** 两类直分）+22,000+ 小节，10 位匿名真人鼓手（Roland TD-11 电鼓），85 风格（rock 281 / hiphop 91 / funk 族 200+ / jazz / latin / soul / reggae / afrobeat / gospel / dance-breakbeat…），50-290 BPM 逐条标注，info.csv 全元数据（drummer/session/style/bpm/beat_type/time_signature/split）。
- **入库**：`$MUSIC_ASSETS_ROOT/scores/gmd/groove/`（MIDI-only 版 3.26MB，sha256 `651cbc52…41dcc1e`；全量 4.76GB 含 WAV 未取）。
- **数据验证（import_smf 实读）**：单鼓轨直读可用；note 编号=GM 打击乐组（36 Kick/38 Snare/42 闭镲/44 踏镲/46 开镲/45-48 Toms/49 Crash/51 Ride）+ Roland TD-11 扩展件位（22/26/40/43/50/52/53/55/58/59，进管线时做映射表或弃用）；**力度值随音符保留=律动手感资产**。

## 13.2 E-GMD（Expanded GMD）——收（CC BY 4.0）

- **官方页**：https://magenta.withgoogle.com/datasets/e-gmd（代理实抓）；包内 LICENSE 逐字："This work is licensed under the Creative Commons Attribution 4.0 International License."
- **内容**：45,537 条目（**beat 18,576 / fill 26,961**），9 位鼓手，444 小时口径（仅取 MIDI：107MB zip/406MB 解压；音频未取）。风格大类：rock 13072 / funk 6364 / jazz 4171 / hiphop 3827 / latin 3784 / soul 2666 / afrocuban 2537 / punk 2494…
- **入库**：`$MUSIC_ASSETS_ROOT/scores/egmd/e-gmd-v1.0.0/`（sha256 `5e70a6f4…75844d3`）。注意扩展名 `.midi`（非 .mid）。

## 13.3 署名义务

两库为 CC BY 4.0：发布使用其内容的成品负署名义务（Google LLC / Magenta Groove MIDI Dataset 与 E-GMD + 论文引用 Gillick et al. 2019；Callender, Hawthorne & Engel 2020）——与 SalamanderGrand/VPO 的 CC-BY 义务同类管理（红线①口径）。

## 13.4 排除项（本轮取证）

| 源 | 结论 |
|---|---|
| `gvellut/dmp_midi` 及上游 montoyamoraga/stephenhandley 仓库 | 内容为 René-Pierre Bardet《200/260 Drum Machine Patterns》**在版权期教材书**的数字转录（README 自认 "from the books"）——书层版权未清，**禁入**（kernScores 同类） |
| Reddit 800k drum MIDI 档案 | 无许可声明大杂烩（DAW 预设/扒带混源），默认排除 |
| Lakh MIDI Dataset | 全网扒取混源、底层作曲多在版权期，禁入 |
| Breakbeat 转写（Amen 等） | 录音版权+转写归属双不清，禁入 |

## 13.5 次级候选（登记未采）

历史 rudimental 军鼓文献——Bruce & Emmett《Drummer's and Fifer's Guide》(1865)、Strube (1869) 等为**真·公有领域鼓谱**（IMSLP/Archive 扫描可寻），行进/军乐性格、量少、需 OMR——为"军鼓性格"备选，暂不采集。

## 13.6 工程管线落点

GM note → drums_gdc/Kenney 采样映射表（数据层适配）→ tempo warp 对齐段落 BPM → beat=循环床/fill=过门两层供给 → 与古典骨架混合成"古典骨架+采样鼓组"口播床（模块 A）。用户"不原创、不扩散"两约束均满足：素材=真人演奏既有 groove，引擎=采样器。
