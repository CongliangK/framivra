# 第三方来源、署名与许可边界

本仓原创代码和文档采用 [MIT](<LICENSE>)。第三方音色、预览中的源采样、乐谱、模型与引用不因此改为 MIT；其权利与义务分别按上游条款判断。本仓不分发音色库、工具二进制或模型权重；仅分发下列八件渲染试听与元数据。

## 音色与数据来源

| 来源 / 作者 | 许可及义务 | 官方来源 |
|---|---|---|
| Salamander Grand Piano V3 / Alexander Holm；SFZ 化 kinwie（衍生层随库声明） | [CC-BY 3.0](https://creativecommons.org/licenses/by/3.0/)；发布使用该音色的音频须保留作者、来源、许可及改动说明 | [原库](https://archive.org/details/SalamanderGrandPianoV3)、[SFZ](https://github.com/sfzinstruments/SalamanderGrandPiano) |
| Virtual Playing Orchestra / Paul Battersby | 官方允许使用音库创作音乐（含商用）；采样再分发按分层来源条款处理，不能概括为单一 CC-BY。包含 CC-BY-SA 3.0/4.0、CC0、Sampling Plus 1.0 等来源；应保留库内 Documentation/license.htm、逐 SFZ 来源及作者署名。本仓渲染预览附作者与官网作为保守署名 | [官网与最新许可](https://virtualplaying.com/virtual-playing-orchestra/) |
| GeneralUser GS / S. Christian Collins | GeneralUser GS License v2.0：允许音乐创作、商用；源样本权利仍各自保留。下载须从作者页面获取，不在自己的网页直链作者下载文件。预览附来源，非额外强制署名 | [官网](https://schristiancollins.com/generaluser.php)、[作者仓库](https://github.com/mrbumpy409/GeneralUser-GS) |
| FluidR3 / Frank Wen | MIT；完整声明见下节；本仓预览保留来源及声明 | [FluidSynth 音源页](https://www.fluidsynth.org/soundfonts/)、[上游镜像](https://github.com/FluidSynth/fluidsynth/wiki/SoundFont) |
| VSCO 2 Community Edition / Versilian Studios 及贡献者 | [CC0](https://creativecommons.org/publicdomain/zero/1.0/)；无强制署名 | [官方仓库（SFZ 分支）](https://github.com/sgossner/VSCO-2-CE/tree/SFZ) |
| Karoryfer / bigcat cello、Meatbass、War Tuba 作者 | CC0；无强制署名 | [官方免费库](https://www.karoryfer.com/karoryfer-samples)、[cello](https://github.com/sfzinstruments/karoryfer-bigcat.cello)、[Meatbass](https://github.com/sfzinstruments/karoryfer.meatbass)、[War Tuba](https://github.com/sfzinstruments/karoryfer.war-tuba) |
| Kenney | CC0；无强制署名 | [官方资源](https://kenney.nl/assets) |
| Groove MIDI Dataset / E-GMD，Google LLC | [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/)；成品须署名 Google LLC、数据集、来源、许可与改动；引用 Gillick et al. 2019 / Callender, Hawthorne & Engel 2020 | [GMD](https://magenta.withgoogle.com/datasets/groove)、[E-GMD](https://magenta.withgoogle.com/datasets/e-gmd) |

## 仓内 preview MP3

预览是 Scoreweft contributors 编写固定乐句、经对应引擎渲染的试听派生件，统一做逐段峰值归一化（-6 dBFS）、截断/淡入淡出/拼接及 MP3 编码；不是音色库本体，亦不代表上游背书。再分发时请同时保留本节与 [预览索引](<catalog/preview/INDEX.md>)，适用内容条款见上表。

| 预览 | 来源 / 义务行 |
|---|---|
| [synth.mp3](<catalog/preview/synth.mp3>) | 本仓 synthlib 程序化合成；MIT，Copyright (c) 2026 Scoreweft contributors |
| [sf2_fluidr3.mp3](<catalog/preview/sf2_fluidr3.mp3>) | FluidR3 by Frank Wen (MIT)；保留本页完整 MIT 声明及来源 |
| [sf2_generaluser.mp3](<catalog/preview/sf2_generaluser.mp3>) | GeneralUser GS by S. Christian Collins；GeneralUser GS License v2.0，许可边界与来源见上表 |
| [sfz_vsco2_ce.mp3](<catalog/preview/sfz_vsco2_ce.mp3>) | VSCO 2 CE / Versilian Studios 及贡献者 (CC0)，无强制署名 |
| [sfz_karoryfer.mp3](<catalog/preview/sfz_karoryfer.mp3>) | Karoryfer bigcat cello / Meatbass / War Tuba (CC0)，无强制署名 |
| [sfz_salamander.mp3](<catalog/preview/sfz_salamander.mp3>) | 钢琴音源：Salamander Grand Piano V3 by Alexander Holm (CC-BY 3.0) — https://archive.org/details/SalamanderGrandPianoV3；SFZ 化 kinwie；上述处理构成改动说明 |
| [sfz_vpo.mp3](<catalog/preview/sfz_vpo.mp3>) | 弦乐音源：Virtual Playing Orchestra by Paul Battersby — https://virtualplaying.com；官方音乐自由使用声明与分层许可见上表；上述处理构成改动说明 |
| [sample_kenney.mp3](<catalog/preview/sample_kenney.mp3>) | Kenney (CC0) — https://kenney.nl/assets，无强制署名 |

Mixkit 与 Sonniss GDC 裸采样预览不随开源版发布，生成器也不再生成它们。需用户自行获取素材并核验当前条款；Mixkit 不得将素材单独作为 stock、工具或模板内容再分发。Sonniss 条款未完成独立复核，不把历史目录登记视为再分发授权。

### FluidR3 MIT 声明

以下原文来自已安装 FluidR3 包的 COPYING：

```text
Copyright (c) 2000-2002, 2008 Frank Wen <getfrank@gmail.com>

Permission is hereby granted, free of charge, to any person
obtaining a copy of this software and associated documentation
files (the "Software"), to deal in the Software without
restriction, including without limitation the rights to use,
copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following
conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.
```

## 外部工具边界

| 工具 | 当前调用方式 / 许可 |
|---|---|
| [FluidSynth](https://www.fluidsynth.org/) | LGPL-2.1-or-later；ctypes 动态加载用户系统库，不含二进制。下游若捆绑、修改或改为静态链接，须另行履行 LGPL（包括可替换/重新链接等适用要求） |
| [FFmpeg](https://ffmpeg.org/legal.html) | CLI 子进程；LGPL/GPL 取决于编译配置，不含二进制 |
| [sfizz](https://github.com/sfztools/sfizz) | sfizz_render CLI 子进程；BSD-2-Clause（pugixml MIT），不含二进制 |
| [demucs](https://github.com/adefossez/demucs) | 独立 Python 环境 CLI 子进程；代码与登记的 htdemucs 权重 MIT，均自行获取 |
| [matchering](https://github.com/sergree/matchering) | GPLv3；可选分支在独立环境由独立解释器调用其库 API；本体不导入、不捆绑其库，默认 master 走 FFmpeg loudnorm |
| [ComfyUI](https://github.com/Comfy-Org/ComfyUI) | GPL-3.0；HTTP 调用用户本地服务（默认 127.0.0.1:8188），不分发或修改该服务 |
| [Audiveris](https://github.com/Audiveris/audiveris) | AGPL-3.0；外部本地 CLI，仅执行、不分发；修改、捆绑或提供远程服务时须另审对应义务 |

上述边界描述针对本仓当前分发方式，不是对 GPL/LGPL/AGPL 义务的一概豁免；下游打包、链接方式或服务模式变化须重新核验。numpy（BSD-3-Clause）与 pytest（MIT，仅开发）由用户安装，其许可证仍适用。

## 乐谱、证据与模型

- 乐谱库仅登记外部元数据，不分发谱本体；Mutopia 逐件为 Public Domain / CC-BY，OpenScore 为 CC0。使用 PD 时核验底层作品与制谱层的分发辖区；CC-BY 成品须作者、源 URL、具体许可版本及改动说明。CC-BY-SA 标记项保持只检索不投产，NC 与未明示许可项禁入。详见 [REGISTRY](<catalog/scores/REGISTRY.md>) 与 [MANIFEST](<catalog/scores/MANIFEST.json>)。
- [IMSLP 存证摘录](<catalog/scores/evidence/imslp/spring_rv269_vilaprinyo_entry.html>) 来源：[RV269 作品页](https://imslp.org/wiki/Violin_Concerto_in_E_major,_RV_269_(Vivaldi,_Antonio))，抓取 2026-08-27，条目 #811966。仅作 Public Domain (dedicated) 许可标签的事实取证；站点 HTML 不改授本仓 MIT，亦不意味着整站开放授权。
- ComfyUI 工作流只是配置，不含模型权重。ACE-Step 1.5 按已登记 Apache-2.0 口径使用，并以所取得权重的实际条款为准。**MiniMax Music3 权重许可未完成登记：用户自行获取并遵守其条款；MIT 不覆盖权重、不保证其可商用，商用资格未核实前不得进入商用产线。**
