# catalog/preview —— 历史音色试听署名索引

本 app 不分发媒体。以下是迁入前固定提交的署名/时间码记录，不代表本地存在 MP3；需试听时由用户自行提供已许可资产并显式运行生成器。

> 由 `tools/make_timbre_previews.py` 生成（引擎真实渲染，重跑确定性再生）。
> **逐段峰值归一化（-6 dBFS）**：库间原始电平差异已被抹平（如 VSCO2 电平缺陷族），
> 电平补偿请查 [../timbres.md](../timbres.md)。每行=一个乐器，时间码可直接拖动播放。
> 这些预览由固定乐句渲染/触发，经归一化、截断、淡入淡出、拼接与 MP3 编码（改动说明）；第三方权利与署名详见 [NOTICE](<../../NOTICE.md>)。Mixkit/Sonniss 裸采样预览未随开源版发布，可在核验条款后本地自建。

## synth · 程序化合成器（synthlib 六音色）

**文件**：`synth.mp3` · 时长 00:20.2 · 6 段

**来源与义务**：自产 synthlib 程序化渲染；MIT，Copyright (c) 2026 Scoreweft contributors。

| 起 | 乐器/段落 | 引擎 |
|---|---|---|
| 00:00.0 | kick · 底鼓（四分律动） | synth |
| 00:03.0 | snare · 军鼓（2/4 反拍） | synth |
| 00:05.5 | hat · 踩镲（八分） | synth |
| 00:08.8 | fm_ep · FM 电钢（旋律） | synth |
| 00:12.6 | bass · 合成贝斯（旋律） | synth |
| 00:16.4 | lead · 合成主音（旋律） | synth |

## sf2 · FluidR3_GM（系统 GM 128 音色）

**文件**：`sf2_fluidr3.mp3` · 时长 01:03.2 · 12 段

**来源与义务**：FluidR3 by Frank Wen (MIT) — https://www.fluidsynth.org/soundfonts/；完整版权与许可声明见 [NOTICE](<../../NOTICE.md>)。

| 起 | 乐器/段落 | 引擎 |
|---|---|---|
| 00:00.0 | p000 Acoustic Grand 声学大钢琴 | sf2 |
| 00:05.4 | p004 Rhodes 电钢 | sf2 |
| 00:10.4 | p011 Vibraphone 颤音琴 | sf2 |
| 00:15.8 | p016 Drawbar Organ 拉杆风琴 | sf2 |
| 00:20.9 | p024 Nylon Guitar 尼龙吉他 | sf2 |
| 00:26.3 | p033 Finger E.Bass 电贝斯 | sf2 |
| 00:31.4 | p040 Violin 小提琴 | sf2 |
| 00:36.8 | p048 Strings Ensemble 弦乐合奏 | sf2 |
| 00:42.2 | p056 Trumpet 小号 | sf2 |
| 00:47.3 | p065 Alto Sax 中音萨克斯 | sf2 |
| 00:52.5 | p073 Flute 长笛 | sf2 |
| 00:57.8 | p088 Warm Pad 暖铺底 | sf2 |

## sf2 · GeneralUser GS（assets GM 128 音色）

**文件**：`sf2_generaluser.mp3` · 时长 01:02.8 · 12 段

**来源与义务**：GeneralUser GS by S. Christian Collins (GeneralUser GS License v2.0) — https://schristiancollins.com/generaluser.php；音乐使用许可与源样本边界见 [NOTICE](<../../NOTICE.md>)。

| 起 | 乐器/段落 | 引擎 |
|---|---|---|
| 00:00.0 | p000 Acoustic Grand 声学大钢琴 | sf2 |
| 00:05.4 | p004 Rhodes 电钢 | sf2 |
| 00:10.5 | p011 Vibraphone 颤音琴 | sf2 |
| 00:15.9 | p016 Drawbar Organ 拉杆风琴 | sf2 |
| 00:20.9 | p024 Nylon Guitar 尼龙吉他 | sf2 |
| 00:26.3 | p033 Finger E.Bass 电贝斯 | sf2 |
| 00:31.4 | p040 Violin 小提琴 | sf2 |
| 00:36.7 | p048 Strings Ensemble 弦乐合奏 | sf2 |
| 00:42.1 | p056 Trumpet 小号 | sf2 |
| 00:47.2 | p065 Alto Sax 中音萨克斯 | sf2 |
| 00:52.3 | p073 Flute 长笛 | sf2 |
| 00:57.4 | p088 Warm Pad 暖铺底 | sf2 |

## sfz · VSCO2 CE 社区版管弦（CC0）

**文件**：`sfz_vsco2_ce.mp3` · 时长 00:54.2 · 14 段

**来源与义务**：VSCO 2 CE / Versilian Studios 及贡献者 (CC0) — https://github.com/sgossner/VSCO-2-CE/tree/SFZ；无强制署名。

| 起 | 乐器/段落 | 引擎 |
|---|---|---|
| 00:00.0 | 独奏小提琴 SViolinVib（力度弧） | sfz |
| 00:03.7 | 小提琴组 ViolinEnsSusVib（和弦） | sfz |
| 00:07.5 | 中提琴组 ViolaEnsSusVib（和弦） | sfz |
| 00:11.5 | 大提琴组 CelloEnsSusVib（和弦） | sfz |
| 00:15.4 | 低音提琴 ContrabassSusVB（旋律） | sfz |
| 00:19.0 | 长笛 FluteSusVib | sfz |
| 00:22.5 | 双簧管 OboeSusVib | sfz |
| 00:26.0 | 单簧管 ClarinetSus | sfz |
| 00:29.6 | 大管 BassoonSus | sfz |
| 00:33.1 | 圆号 FHornSus | sfz |
| 00:36.7 | 竖琴 Harp | sfz |
| 00:42.1 | 立式钢琴 UprightPiano（力度弧） | sfz |
| 00:45.4 | 定音鼓滚奏 TimpaniRolls | sfz |
| 00:48.8 | 钟琴 Glockenspiel（库内电平极低，已归一化） | sfz |

## sfz · Karoryfer 三件套（CC0：大提琴/大贝斯/战争大号）

**文件**：`sfz_karoryfer.mp3` · 时长 00:18.4 · 6 段

**来源与义务**：Karoryfer bigcat cello / Meatbass / War Tuba (CC0) — https://www.karoryfer.com/karoryfer-samples；无强制署名。

| 起 | 乐器/段落 | 引擎 |
|---|---|---|
| 00:00.0 | bigcat 大提琴 · 弓奏力度层 | sfz |
| 00:03.3 | bigcat 大提琴 · 拨奏 | sfz |
| 00:06.1 | meatbass 大贝斯 · 弓奏三层力度 | sfz |
| 00:09.4 | meatbass 大贝斯 · 拨奏 | sfz |
| 00:12.5 | war_tuba · 独奏复音 | sfz |
| 00:15.5 | war_tuba · 双人复音（和弦） | sfz |

## sfz · SalamanderGrand V3 大钢琴（CC-BY）

**文件**：`sfz_salamander.mp3` · 时长 00:10.7 · 2 段

**来源与义务**：钢琴音源：Salamander Grand Piano V3 by Alexander Holm (CC-BY 3.0) — https://archive.org/details/SalamanderGrandPianoV3；SFZ 化 kinwie；许可 https://creativecommons.org/licenses/by/3.0/ 。

| 起 | 乐器/段落 | 引擎 |
|---|---|---|
| 00:00.0 | 旋律（pp→f 力度弧，vel 62-110） | sfz |
| 00:05.4 | 柱式和弦强奏 | sfz |

## sfz · VPO 虚拟演奏管弦弦乐（分层许可，SEC-sustain）

**文件**：`sfz_vpo.mp3` · 时长 00:25.2 · 6 段

**来源与义务**：弦乐音源：Virtual Playing Orchestra by Paul Battersby — https://virtualplaying.com；音乐自由使用，采样来源分层许可详见 [NOTICE](<../../NOTICE.md>)。

| 起 | 乐器/段落 | 引擎 |
|---|---|---|
| 00:00.0 | 第一小提琴 1st-violin | sfz |
| 00:04.0 | 第二小提琴 2nd-violin | sfz |
| 00:08.1 | 中提琴 viola | sfz |
| 00:12.3 | 大提琴 cello | sfz |
| 00:16.5 | 低音提琴 bass | sfz |
| 00:20.9 | 全弦乐齐奏 all-strings（和弦收束） | sfz |

## sample · Kenney 全 10 包（CC0，one-shot）

**文件**：`sample_kenney.mp3` · 时长 00:36.0 · 9 段

**来源与义务**：Kenney (CC0) — https://kenney.nl/assets；无强制署名。

| 起 | 乐器/段落 | 引擎 |
|---|---|---|
| 00:00.0 | casino-audio（3 发代表件） | sample |
| 00:04.0 | digital-audio（3 发代表件） | sample |
| 00:08.0 | impact-sounds（3 发代表件） | sample |
| 00:12.0 | interface-sounds（3 发代表件） | sample |
| 00:16.0 | rpg-audio（3 发代表件） | sample |
| 00:20.0 | sci-fi-sounds（3 发代表件） | sample |
| 00:24.0 | ui-audio（3 发代表件） | sample |
| 00:28.0 | voiceover-pack（3 发代表件） | sample |
| 00:32.0 | voiceover-pack-fighter（3 发代表件） | sample |
