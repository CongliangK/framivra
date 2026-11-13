# bgm 音色目录（timbres）

> 生成：2026-10-11 · `bgm catalog --write` 扫描真实磁盘生成（synth 段=引擎白名单，代码事实）。
> sfz 根：`$MUSIC_ASSETS_ROOT/sfz` · 采样根：`$MUSIC_SAMPLES_ROOT`

| 音色源 | 引擎 | 规模 | 引用方式 |
|---|---|---|---|
| synth 内置音色 | synth | 7（打击 3 + 调性 4） | payload.program = 音色名 |
| SoundFont | sf2 | 2 库 × GM 128 program | payload.sf2/bank/program（速记 `FluidR3_GM:0:48`） |
| SFZ 乐器 | sfz | 4 库 / 685 个 .sfz | payload.sfz = 路径（绝对 / 相对 / 纯文件名） |
| one-shot 采样 | sample | Kenney 753 ogg + drums_gdc 12 wav + mixkit 1169 mp3 | payload.instrument / hits[].file（相对 samples_root） |

## synth —— synthlib 内置音色（7）

实现：`bgm_gen/synthlib.py`（纯 numpy）；引擎白名单 engines/synth.py。

| 音色 | 类型 | pitch | 可调 params（payload.params） | 说明 |
|---|---|---|---|---|
| hat | percussive | ignored | decay | 踩镲：7kHz 以上带通噪声 × 指数衰减 |
| kick | percussive | ignored | — | 808 血统底鼓：音高 155Hz 下扫 45Hz |
| snare | percussive | ignored | — | 军鼓：185/330Hz body + 带通噪声 snap |
| bass | tonal | MIDI pitch → f0 | — | 合成 bass：基频+八度泛音，tanh 饱和 |
| fm_ep | tonal | MIDI pitch → f0 | ratio, idx, bright_decay | FM 电钢琴（DX7 tine 两算子近似，亮度随调制指数衰减） |
| lead | tonal | MIDI pitch → f0 | — | 合成 lead：双振荡器微失谐 + 迟到颤音 |
| nylon | tonal | MIDI pitch → f0 | bright, pick, body, decay_ref | 尼龙弦吉他单音拨弦（Karplus-Strong 拨弦物理模型：延迟线弦+梳状拨弦+FFT 琴体共振；默认 bright 0.55 激励亮度 / pick 0.26 拨弦位置 / body 1.0 琴体共鸣 / decay_ref 6.0 参考音 G2 的 T60 秒——与 synthlib.nylon 签名默认值一致） |

payload 用法（描述符 engines/descriptors/synth.json）：

```
payload（描述符 v1.1.0 · tier A）:
  *program        enum  枚举 kick/snare/hat/fm_ep/bass/lead/nylon；synthlib 音色函数名：kick/snare/hat=打击乐（pitch 忽略），fm_ep/bass/lead/nylon=调性（pitch→f0）；nylon=Karplus-Strong 尼龙弦吉他拨弦物理模型（时值语义：输出=dur+0.9s 自然延音+80ms 释音）
  *notes          tuple BG-D7 统一记谱 [[pitch(MIDI 0-127), beat(浮点), dur(浮点 beat), vel(0-127)], ...]；beat/dur 单位=四分音符；vel→gain=vel/127（线性），总线叠加后 softclip
   bpm            float 默认 120.0；范围 1..…；bpm；beat→秒换算：at = beat * 60/bpm
   params         object默认 {}；按 program 透传为 synthlib 函数 kwargs；合法键随 program 变（invariant synth_params_by_program 强制）
```

## sf2 —— SoundFont 库（2）

音色 = bank/program 寻址；bank 0 = GM 128 melodic（下表标准名），bank 128 = GM 打击组。

| 名字（payload.sf2） | 路径 | 存在 | 大小 MB |
|---|---|---|---|
| FluidR3_GM | `/usr/share/soundfonts/FluidR3_GM.sf2` | ✓ | 148.4 |
| GeneralUser_GS | `$MUSIC_ASSETS_ROOT/sf2/GeneralUser_GS.sf2` | ✓ | 32.3 |

工程轨 instrument 速记法：`instrument 速记 "FluidR3_GM:0:48" = 名字:bank:program（core/project.py track_engine_spec）`。

### GM 128 program（bank 0，program 0..127）

| prog | 名称 | prog | 名称 | prog | 名称 | prog | 名称 |
|---|---|---|---|---|---|---|---|
| 0 | Acoustic Grand Piano | 32 | Acoustic Bass | 64 | Soprano Sax | 96 | FX 1 (rain) |
| 1 | Bright Acoustic Piano | 33 | Electric Bass (finger) | 65 | Alto Sax | 97 | FX 2 (soundtrack) |
| 2 | Electric Grand Piano | 34 | Electric Bass (pick) | 66 | Tenor Sax | 98 | FX 3 (crystal) |
| 3 | Honky-tonk Piano | 35 | Fretless Bass | 67 | Baritone Sax | 99 | FX 4 (atmosphere) |
| 4 | Electric Piano 1 | 36 | Slap Bass 1 | 68 | Oboe | 100 | FX 5 (brightness) |
| 5 | Electric Piano 2 | 37 | Slap Bass 2 | 69 | English Horn | 101 | FX 6 (goblins) |
| 6 | Harpsichord | 38 | Synth Bass 1 | 70 | Bassoon | 102 | FX 7 (echoes) |
| 7 | Clavi | 39 | Synth Bass 2 | 71 | Clarinet | 103 | FX 8 (sci-fi) |
| 8 | Celesta | 40 | Violin | 72 | Piccolo | 104 | Sitar |
| 9 | Glockenspiel | 41 | Viola | 73 | Flute | 105 | Banjo |
| 10 | Music Box | 42 | Cello | 74 | Recorder | 106 | Shamisen |
| 11 | Vibraphone | 43 | Contrabass | 75 | Pan Flute | 107 | Koto |
| 12 | Marimba | 44 | Tremolo Strings | 76 | Blown Bottle | 108 | Kalimba |
| 13 | Xylophone | 45 | Pizzicato Strings | 77 | Shakuhachi | 109 | Bag pipe |
| 14 | Tubular Bells | 46 | Orchestral Harp | 78 | Whistle | 110 | Fiddle |
| 15 | Dulcimer | 47 | Timpani | 79 | Ocarina | 111 | Shanai |
| 16 | Drawbar Organ | 48 | String Ensemble 1 | 80 | Lead 1 (square) | 112 | Tinkle Bell |
| 17 | Percussive Organ | 49 | String Ensemble 2 | 81 | Lead 2 (sawtooth) | 113 | Agogo |
| 18 | Rock Organ | 50 | SynthStrings 1 | 82 | Lead 3 (calliope) | 114 | Steel Drums |
| 19 | Church Organ | 51 | SynthStrings 2 | 83 | Lead 4 (chiff) | 115 | Woodblock |
| 20 | Reed Organ | 52 | Choir Aahs | 84 | Lead 5 (charang) | 116 | Taiko Drum |
| 21 | Accordion | 53 | Voice Oohs | 85 | Lead 6 (voice) | 117 | Melodic Tom |
| 22 | Harmonica | 54 | Synth Voice | 86 | Lead 7 (fifths) | 118 | Synth Drum |
| 23 | Tango Accordion | 55 | Orchestra Hit | 87 | Lead 8 (bass + lead) | 119 | Reverse Cymbal |
| 24 | Acoustic Guitar (nylon) | 56 | Trumpet | 88 | Pad 1 (new age) | 120 | Guitar Fret Noise |
| 25 | Acoustic Guitar (steel) | 57 | Trombone | 89 | Pad 2 (warm) | 121 | Breath Noise |
| 26 | Electric Guitar (jazz) | 58 | Tuba | 90 | Pad 3 (polysynth) | 122 | Seashore |
| 27 | Electric Guitar (clean) | 59 | Muted Trumpet | 91 | Pad 4 (choir) | 123 | Bird Tweet |
| 28 | Electric Guitar (muted) | 60 | French Horn | 92 | Pad 5 (bowed) | 124 | Telephone Ring |
| 29 | Overdriven Guitar | 61 | Brass Section | 93 | Pad 6 (metallic) | 125 | Helicopter |
| 30 | Distortion Guitar | 62 | SynthBrass 1 | 94 | Pad 7 (halo) | 126 | Applause |
| 31 | Guitar harmonics | 63 | SynthBrass 2 | 95 | Pad 8 (sweep) | 127 | Gunshot |

payload 用法（描述符 engines/descriptors/sf2.json）：

```
payload（描述符 v1.1.0 · tier A）:
  *sf2            path  SoundFont：runtime.json sf2 表名字（FluidR3_GM/GeneralUser_GS）或绝对路径（resolve_sf2；文件存在性由引擎守卫）
  *notes          tuple BG-D7 统一记谱 [[pitch, beat, dur, vel], ...]（midi_util write_notes_smf → 临时 SMF；SMF 不写 program change，音色由 program_select 显式指定）
   bank           int   默认 0；范围 0..128；bank（128=GM 打击组）；经 fluid_synth_program_select 显式选音色，可覆盖非 GM-bank0 预设
   program        int   默认 0；范围 0..127；GM program（0..127）
   bpm            float 默认 120.0；范围 1..…；bpm；beat→秒换算（写入 SMF set_tempo）
   gain           float 默认 1.0；范围 0..…；线性输出增益（apply_gain_wav 后处理；gain==1 时 no-op 字节不动）
   channel        int   范围 0..15；MIDI 通道；缺省=bank==128 ? 9 : 0（引擎派生，invariant sf2_channel_default_derived 文档化）
   events         array v2 控制事件数组（PLAN_v3 §3.2/BG-D14；R2 落地）：{type,t}+逐类型字段；type 六类闭集与逐类型字段约束由工程层 core/project.py 校验（描述符 schema 为结构性兜底）；非空时引擎改走 events_to_smf（无 events 保持旧路径字节不动）
   bend_range     float 默认 2.0；范围 0..…；st；弯音量程半音数（缺省 2.0）：pitch_bend 事件 14bit 线性映射 clamp(round_half_up(8192*(1+semitones/bend_range)), 0, 16383)；轨级声明经 track_projection 透传
```

## sfz —— SFZ 乐器库（4 库，685 个 .sfz）

根：`$MUSIC_ASSETS_ROOT/sfz` · 引用规则：绝对路径 / 相对 assets_root/sfz / 纯文件名树内唯一匹配（engines/sfz.py resolve_sfz） · 目录别名：VSCO2→VSCO2_CE、Karoryfer→karoryfer。

> 文件名常见缩写：**SEC**=section 合奏部 · **SOLO**=独奏 · **SUS/VIB/Stac/Leg**=sustain/vibrato/staccato/legato 演奏法 · **KS**=keyswitch 键切换 · **DXF**=dynamic crossfade 力度层交叉淡化 · **Ens**=ensemble。

### SalamanderGrand（1） — CC-BY 3.0（作者 Alexander Holm；SFZ 化 kinwie）——**视频简介需署名**
**全部位于库根**（1 个）：
- `Salamander Grand Piano V3.sfz`

### VPO（454） — CC-BY-SA 组件 + 自由使用许可（用库制音乐完全自由、含商用；再分发采样需署名，作者 Paul Battersby；全文=库内 Documentation/license.htm）
**Brass/**（125）：
- `Brass/all-brass-SEC-PERF-panned.sfz`
- `Brass/all-brass-SEC-PERF-staccato-panned.sfz`
- `Brass/all-brass-SEC-PERF-staccato.sfz`
- `Brass/all-brass-SEC-PERF.sfz`
- `Brass/all-brass-SEC-accent-panned.sfz`
- `Brass/all-brass-SEC-accent.sfz`
- `Brass/all-brass-SEC-normal-DXF.sfz`
- `Brass/all-brass-SEC-normal-mod-wheel-panned.sfz`
- `Brass/all-brass-SEC-normal-mod-wheel.sfz`
- `Brass/all-brass-SEC-normal-panned-DXF.sfz`
- `Brass/all-brass-SEC-staccato-panned.sfz`
- `Brass/all-brass-SEC-staccato.sfz`
- `Brass/all-brass-SEC-sustain-panned.sfz`
- `Brass/all-brass-SEC-sustain.sfz`
- `Brass/all-brass-SOLO-PERF-panned.sfz`
- `Brass/all-brass-SOLO-PERF-staccato-panned.sfz`
- `Brass/all-brass-SOLO-PERF-staccato.sfz`
- `Brass/all-brass-SOLO-PERF.sfz`
- `Brass/all-brass-SOLO-accent-panned.sfz`
- `Brass/all-brass-SOLO-accent.sfz`
- `Brass/all-brass-SOLO-normal-DXF.sfz`
- `Brass/all-brass-SOLO-normal-mod-wheel-panned.sfz`
- `Brass/all-brass-SOLO-normal-mod-wheel.sfz`
- `Brass/all-brass-SOLO-normal-panned-DXF.sfz`
- `Brass/all-brass-SOLO-staccato-panned.sfz`
- `Brass/all-brass-SOLO-staccato.sfz`
- `Brass/all-brass-SOLO-sustain-panned.sfz`
- `Brass/all-brass-SOLO-sustain.sfz`
- `Brass/bass-trombone-SOLO-KS-C6.sfz`
- `Brass/bass-trombone-SOLO-PERF-KS-A5.sfz`
- `Brass/bass-trombone-SOLO-PERF-KS-C6.sfz`
- `Brass/bass-trombone-SOLO-PERF-staccato.sfz`
- `Brass/bass-trombone-SOLO-PERF.sfz`
- `Brass/bass-trombone-SOLO-accent.sfz`
- `Brass/bass-trombone-SOLO-normal-mod-wheel.sfz`
- `Brass/bass-trombone-SOLO-staccato.sfz`
- `Brass/bass-trombone-SOLO-sustain.sfz`
- `Brass/french-horn-SEC-KS-C6-DXF.sfz`
- `Brass/french-horn-SEC-KS-C6.sfz`
- `Brass/french-horn-SEC-PERF-KS-C6.sfz`
- `Brass/french-horn-SEC-PERF-staccato.sfz`
- `Brass/french-horn-SEC-PERF.sfz`
- `Brass/french-horn-SEC-accent-DXF.sfz`
- `Brass/french-horn-SEC-accent.sfz`
- `Brass/french-horn-SEC-normal-DXF.sfz`
- `Brass/french-horn-SEC-normal-mod-wheel.sfz`
- `Brass/french-horn-SEC-staccato-DXF.sfz`
- `Brass/french-horn-SEC-staccato.sfz`
- `Brass/french-horn-SEC-sustain-DXF.sfz`
- `Brass/french-horn-SEC-sustain.sfz`
- `Brass/french-horn-SOLO-KS-C6-DXF.sfz`
- `Brass/french-horn-SOLO-KS-C6.sfz`
- `Brass/french-horn-SOLO-PERF-KS-C6.sfz`
- `Brass/french-horn-SOLO-PERF-staccato.sfz`
- `Brass/french-horn-SOLO-PERF.sfz`
- `Brass/french-horn-SOLO-accent-DXF.sfz`
- `Brass/french-horn-SOLO-accent.sfz`
- `Brass/french-horn-SOLO-normal-DXF.sfz`
- `Brass/french-horn-SOLO-normal-mod-wheel.sfz`
- `Brass/french-horn-SOLO-staccato-DXF.sfz`
- `Brass/french-horn-SOLO-staccato.sfz`
- `Brass/french-horn-SOLO-sustain-DXF.sfz`
- `Brass/french-horn-SOLO-sustain.sfz`
- `Brass/trombone-SEC-KS-C6-DXF.sfz`
- `Brass/trombone-SEC-KS-C6.sfz`
- `Brass/trombone-SEC-PERF-KS-C6.sfz`
- `Brass/trombone-SEC-PERF-staccato.sfz`
- `Brass/trombone-SEC-PERF.sfz`
- `Brass/trombone-SEC-accent-DXF.sfz`
- `Brass/trombone-SEC-accent.sfz`
- `Brass/trombone-SEC-normal-DXF.sfz`
- `Brass/trombone-SEC-normal-mod-wheel.sfz`
- `Brass/trombone-SEC-staccato-DXF.sfz`
- `Brass/trombone-SEC-staccato.sfz`
- `Brass/trombone-SEC-sustain-DXF.sfz`
- `Brass/trombone-SEC-sustain.sfz`
- `Brass/trombone-SOLO-KS-C6-DXF.sfz`
- `Brass/trombone-SOLO-KS-C6.sfz`
- `Brass/trombone-SOLO-PERF-KS-C6.sfz`
- `Brass/trombone-SOLO-PERF-staccato.sfz`
- `Brass/trombone-SOLO-PERF.sfz`
- `Brass/trombone-SOLO-accent-DXF.sfz`
- `Brass/trombone-SOLO-accent.sfz`
- `Brass/trombone-SOLO-normal-DXF.sfz`
- `Brass/trombone-SOLO-normal-mod-wheel.sfz`
- `Brass/trombone-SOLO-staccato-DXF.sfz`
- `Brass/trombone-SOLO-staccato.sfz`
- `Brass/trombone-SOLO-sustain-DXF.sfz`
- `Brass/trombone-SOLO-sustain.sfz`
- `Brass/trumpet-SEC-KS-C2-DXF.sfz`
- `Brass/trumpet-SEC-KS-C2.sfz`
- `Brass/trumpet-SEC-PERF-KS-C2.sfz`
- `Brass/trumpet-SEC-PERF-staccato.sfz`
- `Brass/trumpet-SEC-PERF.sfz`
- `Brass/trumpet-SEC-accent-DXF.sfz`
- `Brass/trumpet-SEC-accent.sfz`
- `Brass/trumpet-SEC-normal-DXF.sfz`
- `Brass/trumpet-SEC-normal-mod-wheel.sfz`
- `Brass/trumpet-SEC-staccato-DXF.sfz`
- `Brass/trumpet-SEC-staccato.sfz`
- `Brass/trumpet-SEC-sustain-DXF.sfz`
- `Brass/trumpet-SEC-sustain.sfz`
- `Brass/trumpet-SOLO-KS-C2-DXF.sfz`
- `Brass/trumpet-SOLO-KS-C2.sfz`
- `Brass/trumpet-SOLO-PERF-KS-C2.sfz`
- `Brass/trumpet-SOLO-PERF-staccato.sfz`
- `Brass/trumpet-SOLO-PERF.sfz`
- `Brass/trumpet-SOLO-accent-DXF.sfz`
- `Brass/trumpet-SOLO-accent.sfz`
- `Brass/trumpet-SOLO-normal-DXF.sfz`
- `Brass/trumpet-SOLO-normal-mod-wheel.sfz`
- `Brass/trumpet-SOLO-staccato-DXF.sfz`
- `Brass/trumpet-SOLO-staccato.sfz`
- `Brass/trumpet-SOLO-sustain-DXF.sfz`
- `Brass/trumpet-SOLO-sustain.sfz`
- `Brass/tuba-SOLO-KS-C5.sfz`
- `Brass/tuba-SOLO-KS-C6.sfz`
- `Brass/tuba-SOLO-PERF-KS-C5.sfz`
- `Brass/tuba-SOLO-PERF-KS-C6.sfz`
- `Brass/tuba-SOLO-PERF-staccato.sfz`
- `Brass/tuba-SOLO-PERF.sfz`
- `Brass/tuba-SOLO-accent.sfz`
- `Brass/tuba-SOLO-normal-mod-wheel.sfz`
- `Brass/tuba-SOLO-staccato.sfz`
- `Brass/tuba-SOLO-sustain.sfz`

**Keys/**（1）：
- `Keys/celesta.sfz`

**Percussion/**（16）：
- `Percussion/bassdrum-snare-cymbals.sfz`
- `Percussion/bassdrum.sfz`
- `Percussion/cymbals.sfz`
- `Percussion/glockenspiel.sfz`
- `Percussion/misc.sfz`
- `Percussion/snare.sfz`
- `Percussion/timpani-KS-C6.sfz`
- `Percussion/timpani-hit-LR.sfz`
- `Percussion/timpani-hit-n-roll.sfz`
- `Percussion/timpani-hit.sfz`
- `Percussion/timpani-roll.sfz`
- `Percussion/tubular-bells.sfz`
- `Percussion/vibraphone-KS-C2.sfz`
- `Percussion/vibraphone-auto-damp.sfz`
- `Percussion/vibraphone-open.sfz`
- `Percussion/xylophone.sfz`

**Strings/**（164）：
- `Strings/1st-violin-SEC-KS-C2.sfz`
- `Strings/1st-violin-SEC-KS-C3.sfz`
- `Strings/1st-violin-SEC-PERF-KS-C2.sfz`
- `Strings/1st-violin-SEC-PERF-KS-C3.sfz`
- `Strings/1st-violin-SEC-PERF-pizzicato.sfz`
- `Strings/1st-violin-SEC-PERF-staccato.sfz`
- `Strings/1st-violin-SEC-PERF-tremolo.sfz`
- `Strings/1st-violin-SEC-PERF.sfz`
- `Strings/1st-violin-SEC-accent.sfz`
- `Strings/1st-violin-SEC-normal-mod-wheel.sfz`
- `Strings/1st-violin-SEC-pizzicato.sfz`
- `Strings/1st-violin-SEC-staccato.sfz`
- `Strings/1st-violin-SEC-sustain.sfz`
- `Strings/1st-violin-SEC-tremolo.sfz`
- `Strings/1st-violin-SOLO-KS-C2.sfz`
- `Strings/1st-violin-SOLO-KS-C3.sfz`
- `Strings/1st-violin-SOLO-PERF-KS-C2.sfz`
- `Strings/1st-violin-SOLO-PERF-KS-C3.sfz`
- `Strings/1st-violin-SOLO-PERF-pizzicato.sfz`
- `Strings/1st-violin-SOLO-PERF-staccato.sfz`
- `Strings/1st-violin-SOLO-PERF-tremolo.sfz`
- `Strings/1st-violin-SOLO-PERF.sfz`
- `Strings/1st-violin-SOLO-accent.sfz`
- `Strings/1st-violin-SOLO-normal-mod-wheel.sfz`
- `Strings/1st-violin-SOLO-pizzicato.sfz`
- `Strings/1st-violin-SOLO-staccato.sfz`
- `Strings/1st-violin-SOLO-sustain.sfz`
- `Strings/1st-violin-SOLO-tremolo.sfz`
- `Strings/2nd-violin-SEC-KS-C2.sfz`
- `Strings/2nd-violin-SEC-KS-C3.sfz`
- `Strings/2nd-violin-SEC-PERF-KS-C2.sfz`
- `Strings/2nd-violin-SEC-PERF-KS-C3.sfz`
- `Strings/2nd-violin-SEC-PERF-pizzicato.sfz`
- `Strings/2nd-violin-SEC-PERF-staccato.sfz`
- `Strings/2nd-violin-SEC-PERF-tremolo.sfz`
- `Strings/2nd-violin-SEC-PERF.sfz`
- `Strings/2nd-violin-SEC-accent.sfz`
- `Strings/2nd-violin-SEC-normal-mod-wheel.sfz`
- `Strings/2nd-violin-SEC-pizzicato.sfz`
- `Strings/2nd-violin-SEC-staccato.sfz`
- `Strings/2nd-violin-SEC-sustain.sfz`
- `Strings/2nd-violin-SEC-tremolo.sfz`
- `Strings/2nd-violin-SOLO-KS-C2.sfz`
- `Strings/2nd-violin-SOLO-KS-C3.sfz`
- `Strings/2nd-violin-SOLO-PERF-KS-C2.sfz`
- `Strings/2nd-violin-SOLO-PERF-KS-C3.sfz`
- `Strings/2nd-violin-SOLO-PERF-pizzicato.sfz`
- `Strings/2nd-violin-SOLO-PERF-staccato.sfz`
- `Strings/2nd-violin-SOLO-PERF.sfz`
- `Strings/2nd-violin-SOLO-accent.sfz`
- `Strings/2nd-violin-SOLO-normal-mod-wheel.sfz`
- `Strings/2nd-violin-SOLO-pizzicato.sfz`
- `Strings/2nd-violin-SOLO-staccato.sfz`
- `Strings/2nd-violin-SOLO-sustain.sfz`
- `Strings/all-strings-SEC-PERF-panned.sfz`
- `Strings/all-strings-SEC-PERF-pizzicato-panned.sfz`
- `Strings/all-strings-SEC-PERF-pizzicato.sfz`
- `Strings/all-strings-SEC-PERF-staccato-panned.sfz`
- `Strings/all-strings-SEC-PERF-staccato.sfz`
- `Strings/all-strings-SEC-PERF-tremolo-panned.sfz`
- `Strings/all-strings-SEC-PERF-tremolo.sfz`
- `Strings/all-strings-SEC-PERF.sfz`
- `Strings/all-strings-SEC-accent-panned.sfz`
- `Strings/all-strings-SEC-accent.sfz`
- `Strings/all-strings-SEC-normal-mod-wheel-panned.sfz`
- `Strings/all-strings-SEC-normal-mod-wheel.sfz`
- `Strings/all-strings-SEC-pizzicato-panned.sfz`
- `Strings/all-strings-SEC-pizzicato.sfz`
- `Strings/all-strings-SEC-staccato-panned.sfz`
- `Strings/all-strings-SEC-staccato.sfz`
- `Strings/all-strings-SEC-sustain-panned.sfz`
- `Strings/all-strings-SEC-sustain.sfz`
- `Strings/all-strings-SEC-tremolo-panned.sfz`
- `Strings/all-strings-SEC-tremolo.sfz`
- `Strings/all-strings-SOLO-PERF-panned.sfz`
- `Strings/all-strings-SOLO-PERF-pizzicato-panned.sfz`
- `Strings/all-strings-SOLO-PERF-pizzicato.sfz`
- `Strings/all-strings-SOLO-PERF-staccato-panned.sfz`
- `Strings/all-strings-SOLO-PERF-staccato.sfz`
- `Strings/all-strings-SOLO-PERF.sfz`
- `Strings/all-strings-SOLO-accent-panned.sfz`
- `Strings/all-strings-SOLO-accent.sfz`
- `Strings/all-strings-SOLO-normal-mod-wheel-panned.sfz`
- `Strings/all-strings-SOLO-normal-mod-wheel.sfz`
- `Strings/all-strings-SOLO-pizzicato-panned.sfz`
- `Strings/all-strings-SOLO-pizzicato.sfz`
- `Strings/all-strings-SOLO-staccato-panned.sfz`
- `Strings/all-strings-SOLO-staccato.sfz`
- `Strings/all-strings-SOLO-sustain-panned.sfz`
- `Strings/all-strings-SOLO-sustain.sfz`
- `Strings/bass-SEC-KS-C5.sfz`
- `Strings/bass-SEC-KS-C6.sfz`
- `Strings/bass-SEC-PERF-KS-C5.sfz`
- `Strings/bass-SEC-PERF-KS-C6.sfz`
- `Strings/bass-SEC-PERF-pizzicato.sfz`
- `Strings/bass-SEC-PERF-staccato.sfz`
- `Strings/bass-SEC-PERF-tremolo.sfz`
- `Strings/bass-SEC-PERF.sfz`
- `Strings/bass-SEC-accent.sfz`
- `Strings/bass-SEC-normal-mod-wheel.sfz`
- `Strings/bass-SEC-pizzicato.sfz`
- `Strings/bass-SEC-staccato.sfz`
- `Strings/bass-SEC-sustain.sfz`
- `Strings/bass-SEC-tremolo.sfz`
- `Strings/bass-SOLO-KS-C5.sfz`
- `Strings/bass-SOLO-KS-C6.sfz`
- `Strings/bass-SOLO-PERF-KS-C5.sfz`
- `Strings/bass-SOLO-PERF-KS-C6.sfz`
- `Strings/bass-SOLO-PERF-pizzicato.sfz`
- `Strings/bass-SOLO-PERF-staccato.sfz`
- `Strings/bass-SOLO-PERF.sfz`
- `Strings/bass-SOLO-accent.sfz`
- `Strings/bass-SOLO-normal-mod-wheel.sfz`
- `Strings/bass-SOLO-pizzicato.sfz`
- `Strings/bass-SOLO-staccato.sfz`
- `Strings/bass-SOLO-sustain.sfz`
- `Strings/cello-SEC-KS-C6.sfz`
- `Strings/cello-SEC-PERF-KS-C6.sfz`
- `Strings/cello-SEC-PERF-pizzicato.sfz`
- `Strings/cello-SEC-PERF-staccato.sfz`
- `Strings/cello-SEC-PERF-tremolo.sfz`
- `Strings/cello-SEC-PERF.sfz`
- `Strings/cello-SEC-accent.sfz`
- `Strings/cello-SEC-normal-mod-wheel.sfz`
- `Strings/cello-SEC-pizzicato.sfz`
- `Strings/cello-SEC-staccato.sfz`
- `Strings/cello-SEC-sustain.sfz`
- `Strings/cello-SEC-tremolo.sfz`
- `Strings/cello-SOLO-KS-C6.sfz`
- `Strings/cello-SOLO-PERF-KS-C6.sfz`
- `Strings/cello-SOLO-PERF-pizzicato.sfz`
- `Strings/cello-SOLO-PERF-staccato.sfz`
- `Strings/cello-SOLO-PERF.sfz`
- `Strings/cello-SOLO-accent.sfz`
- `Strings/cello-SOLO-normal-mod-wheel.sfz`
- `Strings/cello-SOLO-pizzicato.sfz`
- `Strings/cello-SOLO-staccato.sfz`
- `Strings/cello-SOLO-sustain.sfz`
- `Strings/harp-KS-B7.sfz`
- `Strings/harp-KS-C0.sfz`
- `Strings/harp-dampened.sfz`
- `Strings/harp-sustain.sfz`
- `Strings/viola-SEC-KS-C2.sfz`
- `Strings/viola-SEC-PERF-KS-C2.sfz`
- `Strings/viola-SEC-PERF-pizzicato.sfz`
- `Strings/viola-SEC-PERF-staccato.sfz`
- `Strings/viola-SEC-PERF-tremolo.sfz`
- `Strings/viola-SEC-PERF.sfz`
- `Strings/viola-SEC-accent.sfz`
- `Strings/viola-SEC-normal-mod-wheel.sfz`
- `Strings/viola-SEC-pizzicato.sfz`
- `Strings/viola-SEC-staccato.sfz`
- `Strings/viola-SEC-sustain.sfz`
- `Strings/viola-SEC-tremolo.sfz`
- `Strings/viola-SOLO-KS-C2.sfz`
- `Strings/viola-SOLO-PERF-KS-C2.sfz`
- `Strings/viola-SOLO-PERF-pizzicato.sfz`
- `Strings/viola-SOLO-PERF-staccato.sfz`
- `Strings/viola-SOLO-PERF.sfz`
- `Strings/viola-SOLO-accent.sfz`
- `Strings/viola-SOLO-normal-mod-wheel.sfz`
- `Strings/viola-SOLO-pizzicato.sfz`
- `Strings/viola-SOLO-staccato.sfz`
- `Strings/viola-SOLO-sustain.sfz`

**Vocals/**（9）：
- `Vocals/choir-FEMALE-PERF.sfz`
- `Vocals/choir-FEMALE-normal-mod-wheel.sfz`
- `Vocals/choir-FEMALE-sustain.sfz`
- `Vocals/choir-MALE-PERF.sfz`
- `Vocals/choir-MALE-normal-mod-wheel.sfz`
- `Vocals/choir-MALE-sustain.sfz`
- `Vocals/choir-MIXED-PERF.sfz`
- `Vocals/choir-MIXED-normal-mod-wheel.sfz`
- `Vocals/choir-MIXED-sustain.sfz`

**Woodwinds/**（139）：
- `Woodwinds/all-woodwinds-SEC-PERF-panned.sfz`
- `Woodwinds/all-woodwinds-SEC-PERF-staccato-panned.sfz`
- `Woodwinds/all-woodwinds-SEC-PERF-staccato.sfz`
- `Woodwinds/all-woodwinds-SEC-PERF.sfz`
- `Woodwinds/all-woodwinds-SEC-accent-panned.sfz`
- `Woodwinds/all-woodwinds-SEC-accent.sfz`
- `Woodwinds/all-woodwinds-SEC-normal-mod-wheel-panned.sfz`
- `Woodwinds/all-woodwinds-SEC-normal-mod-wheel.sfz`
- `Woodwinds/all-woodwinds-SEC-staccato-panned.sfz`
- `Woodwinds/all-woodwinds-SEC-staccato.sfz`
- `Woodwinds/all-woodwinds-SEC-sustain-panned.sfz`
- `Woodwinds/all-woodwinds-SEC-sustain.sfz`
- `Woodwinds/all-woodwinds-SOLO-PERF-panned.sfz`
- `Woodwinds/all-woodwinds-SOLO-PERF-staccato-panned.sfz`
- `Woodwinds/all-woodwinds-SOLO-PERF-staccato.sfz`
- `Woodwinds/all-woodwinds-SOLO-PERF.sfz`
- `Woodwinds/all-woodwinds-SOLO-accent-panned.sfz`
- `Woodwinds/all-woodwinds-SOLO-accent.sfz`
- `Woodwinds/all-woodwinds-SOLO-normal-mod-wheel-panned.sfz`
- `Woodwinds/all-woodwinds-SOLO-normal-mod-wheel.sfz`
- `Woodwinds/all-woodwinds-SOLO-staccato-panned.sfz`
- `Woodwinds/all-woodwinds-SOLO-staccato.sfz`
- `Woodwinds/all-woodwinds-SOLO-sustain-panned.sfz`
- `Woodwinds/all-woodwinds-SOLO-sustain.sfz`
- `Woodwinds/alto-flute-SOLO-KS-C2.sfz`
- `Woodwinds/alto-flute-SOLO-PERF-KS-C2.sfz`
- `Woodwinds/alto-flute-SOLO-PERF-staccato.sfz`
- `Woodwinds/alto-flute-SOLO-PERF.sfz`
- `Woodwinds/alto-flute-SOLO-accent.sfz`
- `Woodwinds/alto-flute-SOLO-normal-mod-wheel.sfz`
- `Woodwinds/alto-flute-SOLO-staccato.sfz`
- `Woodwinds/alto-flute-SOLO-sustain.sfz`
- `Woodwinds/bass-clarinet-SOLO-KS-C1.sfz`
- `Woodwinds/bass-clarinet-SOLO-KS-C6.sfz`
- `Woodwinds/bass-clarinet-SOLO-PERF-KS-C6.sfz`
- `Woodwinds/bass-clarinet-SOLO-PERF-staccato.sfz`
- `Woodwinds/bass-clarinet-SOLO-PERF.sfz`
- `Woodwinds/bass-clarinet-SOLO-accent.sfz`
- `Woodwinds/bass-clarinet-SOLO-normal-mod-wheel.sfz`
- `Woodwinds/bass-clarinet-SOLO-staccato.sfz`
- `Woodwinds/bass-clarinet-SOLO-sustain.sfz`
- `Woodwinds/bassoon-SEC-KS-C6.sfz`
- `Woodwinds/bassoon-SEC-PERF-KS-C6.sfz`
- `Woodwinds/bassoon-SEC-PERF-staccato.sfz`
- `Woodwinds/bassoon-SEC-PERF.sfz`
- `Woodwinds/bassoon-SEC-accent.sfz`
- `Woodwinds/bassoon-SEC-normal-mod-wheel.sfz`
- `Woodwinds/bassoon-SEC-staccato.sfz`
- `Woodwinds/bassoon-SEC-sustain.sfz`
- `Woodwinds/bassoon-SOLO-KS-C6.sfz`
- `Woodwinds/bassoon-SOLO-PERF-KS-C6.sfz`
- `Woodwinds/bassoon-SOLO-PERF-staccato.sfz`
- `Woodwinds/bassoon-SOLO-PERF.sfz`
- `Woodwinds/bassoon-SOLO-accent.sfz`
- `Woodwinds/bassoon-SOLO-normal-mod-wheel.sfz`
- `Woodwinds/bassoon-SOLO-staccato.sfz`
- `Woodwinds/bassoon-SOLO-sustain.sfz`
- `Woodwinds/clarinet-SEC-KS-C2.sfz`
- `Woodwinds/clarinet-SEC-PERF-KS-C2.sfz`
- `Woodwinds/clarinet-SEC-PERF-staccato.sfz`
- `Woodwinds/clarinet-SEC-PERF.sfz`
- `Woodwinds/clarinet-SEC-accent.sfz`
- `Woodwinds/clarinet-SEC-normal-mod-wheel.sfz`
- `Woodwinds/clarinet-SEC-staccato.sfz`
- `Woodwinds/clarinet-SEC-sustain.sfz`
- `Woodwinds/clarinet-SOLO-KS-C2.sfz`
- `Woodwinds/clarinet-SOLO-PERF-KS-C2.sfz`
- `Woodwinds/clarinet-SOLO-PERF-staccato.sfz`
- `Woodwinds/clarinet-SOLO-PERF.sfz`
- `Woodwinds/clarinet-SOLO-accent.sfz`
- `Woodwinds/clarinet-SOLO-normal-mod-wheel.sfz`
- `Woodwinds/clarinet-SOLO-staccato.sfz`
- `Woodwinds/clarinet-SOLO-sustain.sfz`
- `Woodwinds/contrabassoon-SOLO-KS-C4.sfz`
- `Woodwinds/contrabassoon-SOLO-KS-C5.sfz`
- `Woodwinds/contrabassoon-SOLO-KS-C6.sfz`
- `Woodwinds/contrabassoon-SOLO-PERF-KS-C4.sfz`
- `Woodwinds/contrabassoon-SOLO-PERF-KS-C5.sfz`
- `Woodwinds/contrabassoon-SOLO-PERF-KS-C6.sfz`
- `Woodwinds/contrabassoon-SOLO-PERF-staccato.sfz`
- `Woodwinds/contrabassoon-SOLO-PERF.sfz`
- `Woodwinds/contrabassoon-SOLO-accent.sfz`
- `Woodwinds/contrabassoon-SOLO-normal-mod-wheel.sfz`
- `Woodwinds/contrabassoon-SOLO-staccato.sfz`
- `Woodwinds/contrabassoon-SOLO-sustain.sfz`
- `Woodwinds/english-horn-SOLO-KS-C2.sfz`
- `Woodwinds/english-horn-SOLO-PERF-KS-C2.sfz`
- `Woodwinds/english-horn-SOLO-PERF-staccato.sfz`
- `Woodwinds/english-horn-SOLO-PERF.sfz`
- `Woodwinds/english-horn-SOLO-accent.sfz`
- `Woodwinds/english-horn-SOLO-normal-mod-wheel.sfz`
- `Woodwinds/english-horn-SOLO-staccato.sfz`
- `Woodwinds/english-horn-SOLO-sustain.sfz`
- `Woodwinds/flute-SEC-KS-C2.sfz`
- `Woodwinds/flute-SEC-KS-C3.sfz`
- `Woodwinds/flute-SEC-PERF-KS-C2.sfz`
- `Woodwinds/flute-SEC-PERF-KS-C3.sfz`
- `Woodwinds/flute-SEC-PERF-staccato.sfz`
- `Woodwinds/flute-SEC-PERF.sfz`
- `Woodwinds/flute-SEC-accent.sfz`
- `Woodwinds/flute-SEC-normal-mod-wheel.sfz`
- `Woodwinds/flute-SEC-staccato.sfz`
- `Woodwinds/flute-SEC-sustain.sfz`
- `Woodwinds/flute-SOLO-KS-C2.sfz`
- `Woodwinds/flute-SOLO-KS-C3.sfz`
- `Woodwinds/flute-SOLO-PERF-KS-C2.sfz`
- `Woodwinds/flute-SOLO-PERF-KS-C3.sfz`
- `Woodwinds/flute-SOLO-PERF-staccato.sfz`
- `Woodwinds/flute-SOLO-PERF.sfz`
- `Woodwinds/flute-SOLO-accent.sfz`
- `Woodwinds/flute-SOLO-normal-mod-wheel.sfz`
- `Woodwinds/flute-SOLO-staccato.sfz`
- `Woodwinds/flute-SOLO-sustain.sfz`
- `Woodwinds/oboe-SEC-KS-C2.sfz`
- `Woodwinds/oboe-SEC-PERF-KS-C2.sfz`
- `Woodwinds/oboe-SEC-PERF-staccato.sfz`
- `Woodwinds/oboe-SEC-PERF.sfz`
- `Woodwinds/oboe-SEC-accent.sfz`
- `Woodwinds/oboe-SEC-normal-mod-wheel.sfz`
- `Woodwinds/oboe-SEC-staccato.sfz`
- `Woodwinds/oboe-SEC-sustain.sfz`
- `Woodwinds/oboe-SOLO-KS-C2.sfz`
- `Woodwinds/oboe-SOLO-PERF-KS-C2.sfz`
- `Woodwinds/oboe-SOLO-PERF-staccato.sfz`
- `Woodwinds/oboe-SOLO-PERF.sfz`
- `Woodwinds/oboe-SOLO-accent.sfz`
- `Woodwinds/oboe-SOLO-normal-mod-wheel.sfz`
- `Woodwinds/oboe-SOLO-staccato.sfz`
- `Woodwinds/oboe-SOLO-sustain.sfz`
- `Woodwinds/piccolo-SOLO-KS-C2.sfz`
- `Woodwinds/piccolo-SOLO-KS-C3.sfz`
- `Woodwinds/piccolo-SOLO-PERF-KS-C2.sfz`
- `Woodwinds/piccolo-SOLO-PERF-KS-C3.sfz`
- `Woodwinds/piccolo-SOLO-PERF-staccato.sfz`
- `Woodwinds/piccolo-SOLO-PERF.sfz`
- `Woodwinds/piccolo-SOLO-accent.sfz`
- `Woodwinds/piccolo-SOLO-normal-mod-wheel.sfz`
- `Woodwinds/piccolo-SOLO-staccato.sfz`
- `Woodwinds/piccolo-SOLO-sustain.sfz`

### VSCO2_CE（75） — CC0 1.0 Universal（仓库根 LICENSE 即 CC0 全文；Readme.txt 明示教育/商用均可）
**全部位于库根**（75 个）：
- `BassoonStac.sfz`
- `BassoonSus.sfz`
- `BassoonVib.sfz`
- `CelloEns-KS.sfz`
- `CelloEnsPizz.sfz`
- `CelloEnsSpic.sfz`
- `CelloEnsSusVib-Quiet.sfz`
- `CelloEnsSusVib.sfz`
- `CelloEnsTrem.sfz`
- `Clarinet-KS.sfz`
- `ClarinetStac.sfz`
- `ClarinetSus.sfz`
- `Contrabass-KS.sfz`
- `ContrabassPizz.sfz`
- `ContrabassSpic.sfz`
- `ContrabassSusNV.sfz`
- `ContrabassSusVB-Quiet.sfz`
- `ContrabassSusVB.sfz`
- `ContrabassTrem.sfz`
- `FHornMute.sfz`
- `FHornStac.sfz`
- `FHornSus.sfz`
- `Flute-KS.sfz`
- `FluteExpVib.sfz`
- `FluteStac.sfz`
- `FluteSusNV.sfz`
- `FluteSusVib.sfz`
- `GM-StylePerc.sfz`
- `Glockenspiel.sfz`
- `Harp.sfz`
- `Marimba.sfz`
- `OboeStac.sfz`
- `OboeSusNV.sfz`
- `OboeSusVib.sfz`
- `OrganLoud.sfz`
- `OrganLoudPedal.sfz`
- `OrganQuiet.sfz`
- `OrganQuietPedal.sfz`
- `PiccoloStac.sfz`
- `PiccoloSus.sfz`
- `SViolin-KS.sfz`
- `SViolinPizz.sfz`
- `SViolinSpic.sfz`
- `SViolinTrem.sfz`
- `SViolinVib-Quiet.sfz`
- `SViolinVib.sfz`
- `Timpani.sfz`
- `TimpaniRolls.sfz`
- `TromboneStac.sfz`
- `TromboneSus.sfz`
- `TromboneVib.sfz`
- `TrumpetHarmonMuteSus.sfz`
- `TrumpetStac.sfz`
- `TrumpetStraightMuteSus.sfz`
- `TrumpetSus.sfz`
- `TrumpetSusVib.sfz`
- `Tuba-KS.sfz`
- `TubaStac.sfz`
- `TubaSus.sfz`
- `TubularBells.sfz`
- `UprightPiano.sfz`
- `VSUpright1.sfz`
- `ViolaEns-KS.sfz`
- `ViolaEnsPizz.sfz`
- `ViolaEnsSpic.sfz`
- `ViolaEnsSusVib-Quiet.sfz`
- `ViolaEnsSusVib.sfz`
- `ViolaEnsTrem.sfz`
- `ViolinEns-KS.sfz`
- `ViolinEnsPizz.sfz`
- `ViolinEnsSpic.sfz`
- `ViolinEnsSusVib-Quiet.sfz`
- `ViolinEnsSusVib.sfz`
- `ViolinEnsTrem.sfz`
- `Xylophone.sfz`

### karoryfer（155） — CC0 1.0 Universal（包内 LICENSE 为 CC0 全文；与官网 free-samples 页声明一致）
> war_tuba 只用顶层 Programs 入口（1-solo-legato … 7-tubaborg，项目约定）
**bigcat_cello/**（39）：
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/01- Bowed (velocity layer).sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/02- Bowed (mod wheel).sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/03- Plucked.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_eight_legato_first_map.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_eight_legato_first_map_mw.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_eight_legato_map.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_eight_legato_map_mw.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_eight_map.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_eight_map_mw.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_four_legato_first_map.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_four_legato_first_map_mw.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_four_legato_map.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_four_legato_map_mw.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_four_map.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_four_map_mw.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_marcato_map.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_marcato_mono_map.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_staccato_map.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_sus_for_marcato_poly_map.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_sus_for_marcato_poly_map_mw.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_sus_legato_first_map.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_sus_legato_first_map_mw.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_sus_legato_for_marcato_mono_first_map.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_sus_legato_for_marcato_mono_first_map_mw.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_sus_legato_for_marcato_mono_map.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_sus_legato_for_marcato_mono_map_mw.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_sus_legato_map.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_sus_legato_map_mw.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_sus_map.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_arco_sus_map_mw.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_pizz_basic.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_pizz_eight.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_pizz_four.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_pizz_legato_basic.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_pizz_legato_eight.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_pizz_legato_first_basic.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_pizz_legato_first_eight.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_pizz_legato_first_four.sfz`
- `bigcat_cello/Karoryfer_Bigcat_cello/Programs/vc_pizz_legato_four.sfz`

**meatbass/**（39）：
- `meatbass/Meatbass/Programs/01_arco_modwheel.sfz`
- `meatbass/Meatbass/Programs/02_arco_3vel.sfz`
- `meatbass/Meatbass/Programs/03_arco_5vel.sfz`
- `meatbass/Meatbass/Programs/04_pizz.sfz`
- `meatbass/Meatbass/Programs/arco_basic.sfz`
- `meatbass/Meatbass/Programs/arco_basic_legato_map.sfz`
- `meatbass/Meatbass/Programs/arco_basic_map.sfz`
- `meatbass/Meatbass/Programs/arco_looped_basic_legato_map.sfz`
- `meatbass/Meatbass/Programs/arco_looped_basic_map.sfz`
- `meatbass/Meatbass/Programs/arco_looped_six_legato_first_map.sfz`
- `meatbass/Meatbass/Programs/arco_looped_six_legato_map.sfz`
- `meatbass/Meatbass/Programs/arco_looped_six_map.sfz`
- `meatbass/Meatbass/Programs/arco_looped_three_legato_map.sfz`
- `meatbass/Meatbass/Programs/arco_looped_three_map.sfz`
- `meatbass/Meatbass/Programs/arco_mw_basic_legato_map.sfz`
- `meatbass/Meatbass/Programs/arco_mw_basic_map.sfz`
- `meatbass/Meatbass/Programs/arco_mw_six_legato_first_map.sfz`
- `meatbass/Meatbass/Programs/arco_mw_six_legato_map.sfz`
- `meatbass/Meatbass/Programs/arco_mw_six_map.sfz`
- `meatbass/Meatbass/Programs/arco_mw_three_legato_map.sfz`
- `meatbass/Meatbass/Programs/arco_mw_three_map.sfz`
- `meatbass/Meatbass/Programs/arco_six.sfz`
- `meatbass/Meatbass/Programs/arco_six_legato_first_map.sfz`
- `meatbass/Meatbass/Programs/arco_six_legato_map.sfz`
- `meatbass/Meatbass/Programs/arco_six_map.sfz`
- `meatbass/Meatbass/Programs/arco_three.sfz`
- `meatbass/Meatbass/Programs/arco_three_legato_map.sfz`
- `meatbass/Meatbass/Programs/arco_three_map.sfz`
- `meatbass/Meatbass/Programs/pizz_basic.sfz`
- `meatbass/Meatbass/Programs/pizz_basic_legato_map.sfz`
- `meatbass/Meatbass/Programs/pizz_basic_map.sfz`
- `meatbass/Meatbass/Programs/pizz_six.sfz`
- `meatbass/Meatbass/Programs/pizz_six_legato_first_map.sfz`
- `meatbass/Meatbass/Programs/pizz_six_legato_map.sfz`
- `meatbass/Meatbass/Programs/pizz_six_map.sfz`
- `meatbass/Meatbass/Programs/pizz_three.sfz`
- `meatbass/Meatbass/Programs/pizz_three_legato_first_map.sfz`
- `meatbass/Meatbass/Programs/pizz_three_legato_map.sfz`
- `meatbass/Meatbass/Programs/pizz_three_map.sfz`

**war_tuba/**（77）：
- `war_tuba/War_tuba/Programs/1-solo-legato.sfz`
- `war_tuba/War_tuba/Programs/2-solo-poly.sfz`
- `war_tuba/War_tuba/Programs/3-duo-legato.sfz`
- `war_tuba/War_tuba/Programs/4-duo-poly.sfz`
- `war_tuba/War_tuba/Programs/5-trio-legato.sfz`
- `war_tuba/War_tuba/Programs/6-trio-poly.sfz`
- `war_tuba/War_tuba/Programs/7-tubaborg.sfz`
- `war_tuba/War_tuba/Programs/dbl_legato/staccatissimo_cnd.sfz`
- `war_tuba/War_tuba/Programs/dbl_legato/staccatissimo_dyn.sfz`
- `war_tuba/War_tuba/Programs/dbl_legato/staccatissimo_legato_cnd.sfz`
- `war_tuba/War_tuba/Programs/dbl_legato/staccatissimo_legato_dyn.sfz`
- `war_tuba/War_tuba/Programs/dbl_legato/staccato_cnd.sfz`
- `war_tuba/War_tuba/Programs/dbl_legato/staccato_dyn.sfz`
- `war_tuba/War_tuba/Programs/dbl_legato/staccato_legato_cnd.sfz`
- `war_tuba/War_tuba/Programs/dbl_legato/staccato_legato_dyn.sfz`
- `war_tuba/War_tuba/Programs/dbl_legato/sustain_cnd.sfz`
- `war_tuba/War_tuba/Programs/dbl_legato/sustain_dyn.sfz`
- `war_tuba/War_tuba/Programs/dbl_legato/sustain_legato_cnd.sfz`
- `war_tuba/War_tuba/Programs/dbl_legato/sustain_legato_dyn.sfz`
- `war_tuba/War_tuba/Programs/dbl_poly/staccatissimo_cnd.sfz`
- `war_tuba/War_tuba/Programs/dbl_poly/staccatissimo_dyn.sfz`
- `war_tuba/War_tuba/Programs/dbl_poly/staccato_cnd.sfz`
- `war_tuba/War_tuba/Programs/dbl_poly/staccato_dyn.sfz`
- `war_tuba/War_tuba/Programs/dbl_poly/sustain_cnd.sfz`
- `war_tuba/War_tuba/Programs/dbl_poly/sustain_dyn.sfz`
- `war_tuba/War_tuba/Programs/legato/fx_cnd.sfz`
- `war_tuba/War_tuba/Programs/legato/fx_dyn.sfz`
- `war_tuba/War_tuba/Programs/legato/staccatissimo_cnd.sfz`
- `war_tuba/War_tuba/Programs/legato/staccatissimo_dyn.sfz`
- `war_tuba/War_tuba/Programs/legato/staccatissimo_legato_cnd.sfz`
- `war_tuba/War_tuba/Programs/legato/staccatissimo_legato_dyn.sfz`
- `war_tuba/War_tuba/Programs/legato/staccato_cnd.sfz`
- `war_tuba/War_tuba/Programs/legato/staccato_dyn.sfz`
- `war_tuba/War_tuba/Programs/legato/staccato_legato_cnd.sfz`
- `war_tuba/War_tuba/Programs/legato/staccato_legato_dyn.sfz`
- `war_tuba/War_tuba/Programs/legato/sustain_cnd.sfz`
- `war_tuba/War_tuba/Programs/legato/sustain_dyn.sfz`
- `war_tuba/War_tuba/Programs/legato/sustain_legato_cnd.sfz`
- `war_tuba/War_tuba/Programs/legato/sustain_legato_dyn.sfz`
- `war_tuba/War_tuba/Programs/poly/fx_cnd.sfz`
- `war_tuba/War_tuba/Programs/poly/fx_dyn.sfz`
- `war_tuba/War_tuba/Programs/poly/staccatissimo_cnd.sfz`
- `war_tuba/War_tuba/Programs/poly/staccatissimo_dyn.sfz`
- `war_tuba/War_tuba/Programs/poly/staccato_cnd.sfz`
- `war_tuba/War_tuba/Programs/poly/staccato_dyn.sfz`
- `war_tuba/War_tuba/Programs/poly/sustain_cnd.sfz`
- `war_tuba/War_tuba/Programs/poly/sustain_dyn.sfz`
- `war_tuba/War_tuba/Programs/tri_legato/staccatissimo_cnd.sfz`
- `war_tuba/War_tuba/Programs/tri_legato/staccatissimo_dyn.sfz`
- `war_tuba/War_tuba/Programs/tri_legato/staccatissimo_legato_cnd.sfz`
- `war_tuba/War_tuba/Programs/tri_legato/staccatissimo_legato_dyn.sfz`
- `war_tuba/War_tuba/Programs/tri_legato/staccato_cnd.sfz`
- `war_tuba/War_tuba/Programs/tri_legato/staccato_dyn.sfz`
- `war_tuba/War_tuba/Programs/tri_legato/staccato_legato_cnd.sfz`
- `war_tuba/War_tuba/Programs/tri_legato/staccato_legato_dyn.sfz`
- `war_tuba/War_tuba/Programs/tri_legato/sustain_cnd.sfz`
- `war_tuba/War_tuba/Programs/tri_legato/sustain_dyn.sfz`
- `war_tuba/War_tuba/Programs/tri_legato/sustain_legato_cnd.sfz`
- `war_tuba/War_tuba/Programs/tri_legato/sustain_legato_dyn.sfz`
- `war_tuba/War_tuba/Programs/tri_poly/staccatissimo_cnd.sfz`
- `war_tuba/War_tuba/Programs/tri_poly/staccatissimo_dyn.sfz`
- `war_tuba/War_tuba/Programs/tri_poly/staccato_cnd.sfz`
- `war_tuba/War_tuba/Programs/tri_poly/staccato_dyn.sfz`
- `war_tuba/War_tuba/Programs/tri_poly/sustain_cnd.sfz`
- `war_tuba/War_tuba/Programs/tri_poly/sustain_dyn.sfz`
- `war_tuba/War_tuba/Programs/tubaborg/noise.sfz`
- `war_tuba/War_tuba/Programs/tubaborg/noise_mono.sfz`
- `war_tuba/War_tuba/Programs/tubaborg/single_cycle_center.sfz`
- `war_tuba/War_tuba/Programs/tubaborg/single_cycle_center_mono.sfz`
- `war_tuba/War_tuba/Programs/tubaborg/single_cycle_sides.sfz`
- `war_tuba/War_tuba/Programs/tubaborg/single_cycle_sides_mono.sfz`
- `war_tuba/War_tuba/Programs/tubaborg/sub.sfz`
- `war_tuba/War_tuba/Programs/tubaborg/sub_mono.sfz`
- `war_tuba/War_tuba/Programs/tubaborg/sustains_center.sfz`
- `war_tuba/War_tuba/Programs/tubaborg/sustains_center_mono.sfz`
- `war_tuba/War_tuba/Programs/tubaborg/sustains_sides.sfz`
- `war_tuba/War_tuba/Programs/tubaborg/sustains_sides_mono.sfz`

payload 用法（描述符 engines/descriptors/sfz.json）：

```
payload（描述符 v1.1.0 · tier B）:
  *sfz            path  SFZ 引用：绝对路径 / 相对 assets_root/sfz（别名前缀归一 VSCO2→VSCO2_CE、Karoryfer→karoryfer）/ 纯文件名树内 sorted 唯一匹配（resolve_sfz；文件存在性由引擎守卫）
  *notes          tuple BG-D7 统一记谱 [[pitch, beat, dur, vel], ...]（midi_util write_notes_smf → 临时 SMF → sfizz_render --midi）
   bpm            float 默认 120.0；范围 1..…；bpm；beat→秒换算（写入 SMF set_tempo）
   events         array v2 控制事件数组（PLAN_v3 §3.2/BG-D14；R2 落地）：{type,t}+逐类型字段；type 六类闭集与逐类型字段约束由工程层 core/project.py 校验（描述符 schema 为结构性兜底）；非空时引擎改走 events_to_smf（无 events 保持旧路径字节不动）
   bend_range     float 默认 2.0；范围 0..…；st；弯音量程半音数（缺省 2.0）：pitch_bend 事件 14bit 线性映射 clamp(round_half_up(8192*(1+semitones/bend_range)), 0, 16383)；SFZ 文件级 pitch_wheel_range 逐文件不同——工程轨含弯音时必须显式声明（轨级 track.bend_range 经 track_projection 透传）
```

## sample —— one-shot 采样（采样根 `$MUSIC_SAMPLES_ROOT`）

### Kenney（10 包，753 ogg，CC0）

#### casino-audio（55 ogg）— 引用前缀 `_raw/kenney/kenney_casino-audio/`（相对 samples_root；sample 引擎为严格路径拼接，无模糊匹配）

| 类目 | ogg | 示例 |
|---|---|---|
| （包根） | 1 | `Preview.ogg` |
| Audio | 54 | `card-fan-1.ogg`、`card-fan-2.ogg`、`card-place-1.ogg` |

#### digital-audio（63 ogg）— 引用前缀 `_raw/kenney/kenney_digital-audio/`（相对 samples_root；sample 引擎为严格路径拼接，无模糊匹配）

| 类目 | ogg | 示例 |
|---|---|---|
| （包根） | 1 | `Preview.ogg` |
| Audio | 62 | `highDown.ogg`、`highUp.ogg`、`laser1.ogg` |

#### impact-sounds（130 ogg）— 引用前缀 `_raw/kenney/kenney_impact-sounds/`（相对 samples_root；sample 引擎为严格路径拼接，无模糊匹配）

| 类目 | ogg | 示例 |
|---|---|---|
| Audio | 130 | `footstep_carpet_000.ogg`、`footstep_carpet_001.ogg`、`footstep_carpet_002.ogg` |

#### interface-sounds（100 ogg）— 引用前缀 `_raw/kenney/kenney_interface-sounds/`（相对 samples_root；sample 引擎为严格路径拼接，无模糊匹配）

| 类目 | ogg | 示例 |
|---|---|---|
| Audio | 100 | `back_001.ogg`、`back_002.ogg`、`back_003.ogg` |

#### music-jingles（86 ogg）— 引用前缀 `_raw/kenney/kenney_music-jingles/`（相对 samples_root；sample 引擎为严格路径拼接，无模糊匹配）

| 类目 | ogg | 示例 |
|---|---|---|
| （包根） | 1 | `Preview.ogg` |
| Audio/8-Bit jingles | 17 | `jingles_NES00.ogg`、`jingles_NES01.ogg`、`jingles_NES02.ogg` |
| Audio/Hit jingles | 17 | `jingles_HIT00.ogg`、`jingles_HIT01.ogg`、`jingles_HIT02.ogg` |
| Audio/Pizzicato jingles | 17 | `jingles_PIZZI00.ogg`、`jingles_PIZZI01.ogg`、`jingles_PIZZI02.ogg` |
| Audio/Sax jingles | 17 | `jingles_SAX00.ogg`、`jingles_SAX01.ogg`、`jingles_SAX02.ogg` |
| Audio/Steel jingles | 17 | `jingles_STEEL00.ogg`、`jingles_STEEL01.ogg`、`jingles_STEEL02.ogg` |

#### rpg-audio（52 ogg）— 引用前缀 `_raw/kenney/kenney_rpg-audio/`（相对 samples_root；sample 引擎为严格路径拼接，无模糊匹配）

| 类目 | ogg | 示例 |
|---|---|---|
| （包根） | 1 | `Preview.ogg` |
| Audio | 51 | `beltHandle1.ogg`、`beltHandle2.ogg`、`bookClose.ogg` |

#### sci-fi-sounds（73 ogg）— 引用前缀 `_raw/kenney/kenney_sci-fi-sounds/`（相对 samples_root；sample 引擎为严格路径拼接，无模糊匹配）

| 类目 | ogg | 示例 |
|---|---|---|
| Audio | 73 | `computerNoise_000.ogg`、`computerNoise_001.ogg`、`computerNoise_002.ogg` |

#### ui-audio（52 ogg）— 引用前缀 `_raw/kenney/kenney_ui-audio/`（相对 samples_root；sample 引擎为严格路径拼接，无模糊匹配）

| 类目 | ogg | 示例 |
|---|---|---|
| （包根） | 1 | `Preview.ogg` |
| Audio | 51 | `click1.ogg`、`click2.ogg`、`click3.ogg` |

#### voiceover-pack（95 ogg）— 引用前缀 `_raw/kenney/kenney_voiceover-pack/`（相对 samples_root；sample 引擎为严格路径拼接，无模糊匹配）

| 类目 | ogg | 示例 |
|---|---|---|
| （包根） | 3 | `Preview (Female).ogg`、`Preview (Male).ogg`、`Preview.ogg` |
| Female | 46 | `1.ogg`、`10.ogg`、`2.ogg` |
| Male | 46 | `1.ogg`、`10.ogg`、`2.ogg` |

#### voiceover-pack-fighter（47 ogg）— 引用前缀 `_raw/kenney/kenney_voiceover-pack-fighter/`（相对 samples_root；sample 引擎为严格路径拼接，无模糊匹配）

| 类目 | ogg | 示例 |
|---|---|---|
| （包根） | 1 | `Preview.ogg` |
| Audio | 46 | `1.ogg`、`10.ogg`、`2.ogg` |

### drums_gdc（12 one-shot，Sonniss GDC 2026 精选）

路径：`$MUSIC_ASSETS_ROOT/samples/drums_gdc`（manifest.json 逐条元数据 12 条）

> 注意：在 samples_root 之外，sample 引擎须用绝对路径引用。

| 组 | wav | 示例 |
|---|---|---|
| clap | 1 | `clap_ui_snap.wav` |
| closed_hat | 2 | `closedhat_hard_metal_tick.wav`、`closedhat_soft_glassy.wav` |
| kick | 3 | `kick_punch_combat.wav`、`kick_sub_boom_deep.wav` |
| open_hat | 1 | `openhat_coin_flip.wav` |
| perc | 2 | `perc_electric_tonal_impact.wav`、`perc_zap_scifi_burst.wav` |
| ride | 1 | `ride_ting_metal.wav` |
| snare | 2 | `snare_crunchy_blast.wav`、`snare_transient_smash.wav` |

### _raw 其余原始包（包级计数）

| 包 | 规模 | 说明 |
|---|---|---|
| mixkit | 36 类目 / 1169 mp3 | 可商用免署名；类目：alerts、ambience、beep、bell、boom、chimes、click、ding、drone、electricity、error、game、game-over、glitch、high-tech、hit、hum、impact、interface、keyboard、lose、notification、pop、rewind、sparkle、swish、swoosh、synth、technology、tones、transition、type、typewriter、white-noise、whoosh、win |
| sonniss | 5 分卷 zip / 6.92 GB | 原始分卷 zip，未解压（精选件已入 drums_gdc） |
| 99sounds | 0 文件 | 空目录（Gumroad 渠道未脚本化） |
| bgm/（samples_root 下） | 8 个音频 | AI 生成试听样（整曲 BGM，非 one-shot 音色） |

payload 用法（描述符 engines/descriptors/sample.json）：

```
payload（描述符 v1.0.0 · tier A）:
   hits           array 入口一（hits 优先）：[{file, at(秒,默认0), gain_db(默认0)}, ...]；numpy 等功率叠加
   instrument     path  入口二（notes 记谱法）的采样文件（file 解析规则同 hits[].file；走 notes 入口时必填——invariant sample_hits_or_notes_entry 强制）
   notes          tuple 入口二（BG-D7 记谱法）：[[pitch(仅记录用), beat, dur, vel], ...]；at=beat*60/bpm；gain_db=20*log10(max(vel,1)/100)
   bpm            float 默认 120.0；范围 1..…；bpm；notes 入口的 beat→秒换算
  *length_s       float 范围 0..…；s；输出总时长（秒；混合结果不足补零、超出截断）——无条件必填（两种入口同）
```

## 非静态音色源（说明）

非静态音色源：ai 引擎（ComfyUI ACE-Step 1.5 / Music3，text-to-music 整段素材生成，无固定音色，工作流见 workflows/）；demucs 引擎（htdemucs 分轨降维，非音色源）；master 引擎（母带响度处理，非音色源）。

- `ai` 引擎：工作流模板在 `workflows/`（wf_ace15*.json / wf_music3.json 等），payload.params 按节点点路径覆盖（duration/seed/style 等）。
- `demucs` 引擎：`bgm stem` 子命令（四分轨 vocals/drums/bass/other）。
- `master` 引擎：`bgm master` 子命令（loudnorm / matchering 两策略）。
