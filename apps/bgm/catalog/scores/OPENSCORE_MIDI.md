# openscore-midi · 派生 MIDI 库入库清单（闲时任务四 R3，2026-08-27）

> openscore 双库（Lieder 1462 + SQ 122，全 CC0）.mxl → MIDI 批量转换产物。
> 转换器：`examples/convert_openscore.py`（读器 `bgm_gen/core/musicxml_import.py`，设计契约 `docs/PLAN_musicxml.md`）。
> 数据落点：`$MUSIC_ASSETS_ROOT/scores/openscore-midi/{lieder,sq}/`（镜像源目录结构）。
> 全量清单（逐件 JSON）：`out/openscore_midi_inventory.json`。许可随源件（CC0，零署名义务）；**产物为无表情底稿**（vel 恒 64，禁当直接成品用）。

## 一、总量

| 项 | 数 |
|---|---|
| 转换成功 / 总数 | **1583 / 1584** |
| 跳过 | 1（Parratt《Out in the windy West》lc6681689：8 声部合唱 15 个 (part,voice) 对占满全部可用通道+内部同 pitch 交叠，无溢出通道——`ChannelOverflow` 显式跳过非静默） |
| 写出音符总数 | 2,603,762 |
| 剔除 grace / cue | 19,654 / 1,168（cue 与普查轮 201 件/1168 音完全一致） |
| 交叠分道音符 | 184（溢出通道复用制） |
| ppq 量化件 | 0（LCM 全部 ≤100000 护栏内） |
| 带反复结构件 | 368（**未展开**——零观点按书写序单遍，投产前按 PLAYBOOK 反复铁律自查） |

## 二、抽检对拍（10 件）

### L2 交叉源 6 件（mutopia 独立第二源，版本级独立，Voice 轨纯音高序列）

协议：编制核对+移调探针（全 件 shift=0）+grace/cue 双滤；相似度=difflib.SequenceMatcher(autojunk=False).ratio()；**硬门 ≥0.95 全过**：

| 曲目 | 我方件 | 人声 | 相似度 | 差异归因 |
|---|---|---|---|---|
| Gretchen am Spinnrade D118 | 256 | 256 | **1.0000** | 零差异 |
| Ich will meine Seele Op.48/5 | 64 | 64 | **1.0000** | 零差异 |
| Erlkönig D328 | 332 | 337 | 0.9925 | 5 处单音插入（openscore=单簧管改编版装饰差异） |
| Du bist die Ruh D776 | 155 | 155 | 0.9871 | m69 两音版本编辑（Breitkopf 1895 vs Peters） |
| Widmung Op.25/1 | 150 | 146 | 0.9797 | 1 插入+1 删除五音块（版本编辑） |
| Einsamkeit D911 | 135 | 139 | 0.9635 | 7 处散点单音（Winterreise 原始版 vs 常见版） |

**音高级错误率（可比对材料口径）**：equal 块命中 1059/1062 = **0 残差解析错**（3 处不一致全部为版本编辑差异，逐块归因如上）。

**门下披露件**（不计入硬门，归因成立）：Ich grolle nicht 0.9407（12 连单音插入=版本写法差异）；An die Musik 0.6383 / Die Forelle 0.7935 / Auf dem Wasser **0.5000**（三件 `has_repeat=true`——mutopia 展开多段诗节、我方按书写序单遍；Auf dem Wasser 336=112×3 精确整倍数为直接证据；前缀段全等）。

### L3 roundtrip 4 件（无 mutopia 源，自洽验证）

| 曲目 | 音符（写=读） | 错配 | ppq |
|---|---|---|---|
| Ständchen D.957/4 | 1104 = 1104 | **0** | 12 |
| Mondnacht Op.39/5 | 1203 = 1203 | **0** | 4 |
| Haydn SQ Op.76/1（Hob.III:75，四乐章） | 9118 = 9118 | **0** | 24 |
| Wolf Verborgenheit | 1157 = 1157 | **0** | 4 |

## 三、警告披露（逐件明细在 inventory JSON `warn` 字段）

| 类 | 件数 | 说明 |
|---|---|---|
| backup 钳 0 | 3 | Faltis《Heimkehr》（转录级坏件）/ Mahler《Wenn mein Schatz…》m92 / Schumann《Schneeglöckchen》m33——后两件=时号 desync 下 backup 按旧时号全长回退，钳 0 即唯一正确解释 |
| 跨 part 失同步 | 24 | 各 part 持过时时号或内容缺填（0.02 quarter 容差口径）；SQ 含 Arriaga/Bridge 等 1/480 级舍入已豁免 |
| tie 非邻接回退 | 149 | 窗内（主导拍面小节长）回退配对，逐件 warn 计数 |
| tie 悬空 | 110 | start/stop 悬空按独立音保留 |
| 同 beat 双 tempo | 45 | dominant 后序胜出（与"并列取先"倒置已披露） |
| 名义长偏差 | 108 | 实测小节长偏离拍面（劈小节对/首末短小节等，max_cursor 模型按实测长处理） |

## 四、复现

```bash
python3 examples/convert_openscore.py            # 全量批转（~2 分钟）
# 抽检协议见 bgm_gen/tests/test_musicxml_import.py（L2/L3 例）与 docs/PLAN_musicxml.md §四
```

- 转换确定性：同输入重跑逐字节一致（写出层纯 tick 域整数运算+固定遍历序）。
- 上游溯源：源件四要素在 `catalog/scores/MANIFEST.json`（openscore-lieder/sq 逐件 CC0 登记，2026-08-25）。
