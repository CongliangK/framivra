"""harvest：引擎描述符手动收割器（PLAN_v3 §3.1 机制 A / BG-D13 防漂移四件套）。

用法（cwd 无关；模块级仅标准库——engines/__init__ pkgutil 自动发现安全）：
    python3 -m bgm_gen.engines.harvest write [engine ...]   事实表 → 描述符
    python3 -m bgm_gen.engines.harvest log [engine ...]     证据日志 harvest_log/<engine>.log
    python3 -m bgm_gen.engines.harvest verify [engine ...]  描述符 vs 事实表 + 二进制实测

事实表 = 本模块 ENGINE_FACTS（人编辑的唯一事实源）。收割法（C12）：
第一事实源=引擎源码的 payload 读取行为（tier A 程序化收割）；sfizz 为 tier B
（CLI --help 参数名表 + SFZ 规范 + 源码佐证，黑盒二进制）。引擎/二进制升级流程：
先 diff 描述符再重收割 = 改 ENGINE_FACTS → `harvest write` → git diff → 行为探针。

日志纪律（C10/R3a-#4）：证据日志仅存命令行、参数名表、类型/单位/短语义摘要
（≤1 行/参数）、来源 URL/路径指针；禁止粘贴手册/帮助正文段落。
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DESC_DIR = os.path.join(HERE, "descriptors")
LOG_DIR = os.path.join(DESC_DIR, "harvest_log")

_COMMON_NOTES_SCHEMA = {
    "minItems": 1,
    "items": {
        "type": "array",
        "minItems": 4,
        "maxItems": 4,
        "prefixItems": [
            {"type": "number", "minimum": 0, "maximum": 127},
            {"type": "number", "minimum": 0},
            {"type": "number", "minimum": 0},
            {"type": "number", "minimum": 0, "maximum": 127},
        ],
    },
}

_PRIMARY_FACT = {"synth": None, "sample": None, "sf2": "fluidsynth",
                 "sfz": "sfizz_render", "master": "ffmpeg",
                 "demucs": "demucs", "ai": "comfyui_version"}

# ------------------------------------------------------------ 事实表（唯一事实源）

ENGINE_FACTS = {
    "synth": {
        "version": "1.1.0",
        "binary_pin": {
            "version": None,
            "sha256": None,
            "size": None,
            "note": "无独立二进制：纯 numpy（synthlib，内部 SR 恒 44100，随机源全部种子化→deterministic；nylon 随机源=splitmix64 整数哈希流，BG-D16 纪律）；解释器=runtime.json python_system（miniconda base）",
            "additional_pins": [
                {"name": "ffmpeg", "version": "8.1.2", "sha256": None,
                 "note": "spec.sr≠44100 时 _wavio.resample_wav_file 重采样（系统包；doctor 探测交叉核验）"},
            ],
        },
        "sources": [
            "bgm_gen/engines/synth.py（render() 的 payload 读取行为——收割第一事实源）",
            "bgm_gen/synthlib.py（音色函数签名=params 合法键事实源；nylon=Karplus-Strong 尼龙拨弦，2026-08-25 guitar_test30 轮 MF 盲听两轮定档新增）",
        ],
        "parameters": [
            {"id": "program", "type": "enum",
             "enum": ["kick", "snare", "hat", "fm_ep", "bass", "lead", "nylon"],
             "stepped": True, "flags": ["required"],
             "description": "synthlib 音色函数名：kick/snare/hat=打击乐（pitch 忽略），fm_ep/bass/lead/nylon=调性（pitch→f0）；nylon=Karplus-Strong 尼龙弦吉他拨弦物理模型（时值语义：输出=dur+0.9s 自然延音+80ms 释音）"},
            {"id": "notes", "type": "tuple", "flags": ["required"],
             "schema": _COMMON_NOTES_SCHEMA,
             "description": "BG-D7 统一记谱 [[pitch(MIDI 0-127), beat(浮点), dur(浮点 beat), vel(0-127)], ...]；beat/dur 单位=四分音符；vel→gain=vel/127（线性），总线叠加后 softclip"},
            {"id": "bpm", "type": "float", "min": 1, "default": 120.0,
             "unit": "bpm", "description": "beat→秒换算：at = beat * 60/bpm"},
            {"id": "params", "type": "object", "default": {},
             "description": "按 program 透传为 synthlib 函数 kwargs；合法键随 program 变（invariant synth_params_by_program 强制）"},
        ],
        "input_ports": [
            {"id": "notes", "type": "note_array", "description": "BG-D7 统一记谱（唯一音频内容入口）"},
        ],
        "events_supported": [],
        "formats": ["wav（pcm_s16le）"],
        "track_projection": {
            "bpm": "meta.bpm",
            "notes": "track.notes",
            "program": "track.instrument",
            "params": "—（v1 工程轨无通道；v2=track.params 覆盖合并，钩子④，R2 落地）",
            "hooks": [],
            "note": "R1 锁 core/project.py track_engine_spec 的 if/elif 原样；数据化=R2（重组装深比较锁）",
        },
        "invariants": [
            {"id": "synth_params_by_program", "check": True,
             "rule": "params 键必须 ∈ program 对应的 synthlib 函数签名参数：kick/snare/bass/lead=∅；hat={decay}；fm_ep={ratio, idx, bright_decay}；nylon={bright, pick, body, decay_ref}"},
            {"id": "synth_notes_semantics", "check": False,
             "rule": "perc 行 pitch 忽略（仅占时长）；vel=0 合法（gain 0）；dur=0 行宽容（引擎 dur_s=0 行为，不拒）；notes 空列表由渲染层 render_synth_notes 守卫拒绝"},
        ],
        "coverage": {
            "tier": "A",
            "evidence": [
                "bgm_gen/engines/synth.py（render() 的 payload 读取行为——收割第一事实源 C12）",
                "bgm_gen/synthlib.py（音色函数签名=params 合法键事实源；nylon 段内注释=拨弦物理模型与 MF 盲听定档证据指针）",
            ],
            "probes": [
                "bgm_gen/tests/test_descriptors.py [fast] synth_default（program=kick 默认 bpm）/ synth_fm_ep_boundary（bpm 边界 200+params 合法组合）+ determinism 双渲",
                "bgm_gen/tests/test_guitar_test_data.py [fast] nylon_probe（program=nylon 出参契约+params 合法组合+determinism 双渲，2026-08-25 新增）",
            ],
            "gaps": [
                "dur=0 行不拒绝（引擎内 dur_s=0 宽容，与 midi 层钳 1 tick 口径不同属引擎差异）",
                "vel=0 不拒绝（gain 0，仅占时长）",
                "spec.sr≠44100 依赖 ffmpeg 重采样（未在描述符参数面建模，binary_pin additional_pins 注记）",
                "bpm 收紧 min=1（工程层 meta.bpm lo=0.0001 与之分层不一致；生产路径无影响，R1 审查 E-1 记录）",
                "nylon 同 pitch 同 params 重复触发同波形（无 round-robin 轮替）——时序解耦由上游 humanize/warp 层承担；vel→gain 线性（亮度 bright 为轨级参数非逐音符），2026-08-25 注记",
            ],
        },
    },
    "sf2": {
        "version": "1.1.0",
        "binary_pin": {
            "version": "2.5.4",
            "sha256": None,
            "size": None,
            "note": "libfluidsynth.so.3（系统包；FluidSynth runtime version 2.5.4，doctor 经 `fluidsynth --version` 探测交叉核验）",
            "additional_pins": [
                {"name": "ffmpeg", "version": "8.1.2", "sha256": None,
                 "note": "ffmpeg_match_wav 声道/sr 兜底转换（系统包）"},
            ],
        },
        "sources": [
            "bgm_gen/engines/sf2.py（ctypes 调用点：program_select/player/file_renderer/apply_gain_wav——收割第一事实源）",
            "bgm_gen/engines/midi_util.py（R2：events_to_smf 编码器——事件通道字节层）",
        ],
        "parameters": [
            {"id": "sf2", "type": "path", "flags": ["required"],
             "description": "SoundFont：runtime.json sf2 表名字（FluidR3_GM/GeneralUser_GS）或绝对路径（resolve_sf2；文件存在性由引擎守卫）"},
            {"id": "notes", "type": "tuple", "flags": ["required"],
             "schema": _COMMON_NOTES_SCHEMA,
             "description": "BG-D7 统一记谱 [[pitch, beat, dur, vel], ...]（midi_util write_notes_smf → 临时 SMF；SMF 不写 program change，音色由 program_select 显式指定）"},
            {"id": "bank", "type": "int", "min": 0, "max": 128, "default": 0,
             "description": "bank（128=GM 打击组）；经 fluid_synth_program_select 显式选音色，可覆盖非 GM-bank0 预设"},
            {"id": "program", "type": "int", "min": 0, "max": 127, "default": 0,
             "description": "GM program（0..127）"},
            {"id": "bpm", "type": "float", "min": 1, "default": 120.0,
             "unit": "bpm", "description": "beat→秒换算（写入 SMF set_tempo）"},
            {"id": "gain", "type": "float", "min": 0, "default": 1.0,
             "description": "线性输出增益（apply_gain_wav 后处理；gain==1 时 no-op 字节不动）"},
            {"id": "channel", "type": "int", "min": 0, "max": 15, "default": None,
             "description": "MIDI 通道；缺省=bank==128 ? 9 : 0（引擎派生，invariant sf2_channel_default_derived 文档化）"},
            {"id": "events", "type": "array", "flags": ["optional"],
             "schema": {
                 "items": {
                     "type": "object",
                     "required": ["type", "t"],
                     "properties": {
                         "t": {"type": "number", "minimum": 0},
                         "ctrl": {"type": "number", "minimum": 0, "maximum": 127},
                         "value": {"type": "number", "minimum": 0, "maximum": 127},
                         "semitones": {"type": "number"},
                         "pitch": {"type": "number", "minimum": 0, "maximum": 127},
                         "program": {"type": "number", "minimum": 0, "maximum": 127},
                         "bank": {"type": "number", "minimum": 0, "maximum": 128},
                         "vel": {"type": "number", "minimum": 0, "maximum": 127},
                     },
                 },
             },
             "description": "v2 控制事件数组（PLAN_v3 §3.2/BG-D14；R2 落地）：{type,t}+逐类型字段；type 六类闭集与逐类型字段约束由工程层 core/project.py 校验（描述符 schema 为结构性兜底）；非空时引擎改走 events_to_smf（无 events 保持旧路径字节不动）"},
            {"id": "bend_range", "type": "float", "min": 0, "exclusive_min": True,
             "default": 2.0, "unit": "st",
             "description": "弯音量程半音数（缺省 2.0）：pitch_bend 事件 14bit 线性映射 clamp(round_half_up(8192*(1+semitones/bend_range)), 0, 16383)；轨级声明经 track_projection 透传"},
        ],
        "input_ports": [
            {"id": "notes", "type": "note_array", "description": "BG-D7 记谱"},
            {"id": "sf2", "type": "asset_path", "description": "SoundFont 名或绝对路径"},
        ],
        "events_supported": [
            {"type": "cc", "note": "fluid_player 标准透传（libfluidsynth 文件渲染）"},
            {"type": "pitch_bend", "note": "默认量程 ±2 半音（轨级 bend_range 透传，缺省 2）"},
            {"type": "note_on", "note": "仅作 notes 投影载体（XOR），经投影合成 SMF 音符"},
            {"type": "note_off", "note": "同 note_on（vel=0 编码）"},
        ],
        "formats": ["wav（pcm_s16le）"],
        "track_projection": {
            "bank": "track.bank",
            "bend_range": "track.bend_range（显式声明时透传；缺省 2 由引擎层默认）",
            "bpm": "meta.bpm",
            "channel": "—（缺省按 bank 派生）",
            "events": "track.events（控制事件；非空才注入 payload——无 events 保持旧路径字节不动）",
            "gain": "—（v2=track.params 覆盖合并，钩子④）",
            "hooks": [
                "① sf2 instrument 速记解析（显式优先）",
                "③ meta 注入（bpm/sr）",
                "④ params 覆盖合并（显式优先）"
            ],
            "note": "R2 数据化：if/elif 退役为 track_projection + 四类封闭钩子（PLAN_v3 §3.1；重组装深比较锁 tests/test_events.py）",
            "notes": "track.notes（note 事件载体 XOR 时由组装层 events_to_notes 合成）",
            "program": "track.program",
            "sf2": "track.instrument（速记 名:bank:program 解析——钩子①；显式 bank/program 字段优先）"
        },
        "invariants": [
            {"id": "sf2_channel_default_derived", "check": False,
             "rule": "channel 缺省 = bank==128 ? 9 : 0（GM 打击组惯例 ch10；引擎内派生）"},
            {"id": "sf2_program_select_explicit", "check": False,
             "rule": "bank/program 经 fluid_synth_program_select 显式选音色、SMF 不写 program change——因此 program_change 事件被拒（events_supported 未列出）"},
            {"id": "sf2_gain_pcm16_only", "check": False,
             "rule": "gain 后处理仅 PCM16 wav（apply_gain_wav，sampwidth≠2 报 EngineError）"},
        ],
        "coverage": {
            "tier": "A",
            "evidence": [
                "bgm_gen/engines/sf2.py（ctypes 调用点：program_select/player/file_renderer/apply_gain_wav——收割第一事实源 C12）",
                "bgm_gen/engines/midi_util.py（R2：events_to_smf 编码器——事件通道字节层，tests/test_events.py 锚点锁定）",
                "FluidSynth runtime version 2.5.4（`fluidsynth --version` 实测，doctor 交叉核验）",
            ],
            "probes": [
                "bgm_gen/tests/test_descriptors.py [fast] sf2_full（bank/program/gain/channel/bpm 全参数赋值）+ sf2_perc_bank128（channel 派生 9）+ determinism 双渲",
                "bgm_gen/tests/test_events.py [fast] 编码锚点（ppq/bend 14bit/混装轨逐位一致）+ [slow] params.gain 生效冒烟（RMS 变化）",
            ],
            "gaps": [
                "fluidsynth settings API 未接入描述符（仅 doctor 版本交叉核验）",
                "sf2 文件本身能力面（bank/program 实际集合）不在描述符内——依赖 SoundFont 文件内容",
                "program_change/keyswitch 事件被拒（护 program_select 显式选音色——events_supported 未列出；PLAN_v3 §3.2 消费矩阵）",
                "gain<0（相位翻转）被拒绝（min 0，收紧面；引擎原无检查）",
                "channel/bank/program 引擎原用 int() 宽容转换（如 0.5→0），描述符 int 型收紧为整值/整值浮点",
                "bank 上限 128/channel 上限 15 为描述符单向收紧（引擎 int() 无上界；R1 审查 A1 记录）",
                "bpm 收紧 min=1（同 synth；R1 审查 E-1 记录）",
            ],
        },
        "log_extra": [
            "R2（2026-08-23）：parameters +events（type=array, optional；事件对象 schema 片段）+bend_range（float>0 默认 2）；version 1.0.0→1.1.0（BG-D14：payload.events 引入伴随描述符 bump，envelope spec_version 不变）；来源指针=PLAN_v3 §3.2/§5 R2 行 + bgm_gen/tests/test_events.py",
        ],
    },
    "sfz": {
        "version": "1.1.0",
        "binary_pin": {
            "version": "1.2.3-4.fc44",
            "sha256": "caa9b3104beaa0059cd00e3deab697f2ddb2423d0e4b777b9fe0598c3e46ddd5",
            "size": 1212944,
            "note": "sfizz_render 无 --version（实测输出 'Option version does not exist'）——version 取 audinux COPR rpm 登记事实 sfizz-1.2.3-4.fc44；doctor 核验退化为 sha/size（C2，sha=advisory 黄灯）",
        },
        "sources": [
            "bgm_gen/engines/sfz.py（渲染选项使用面：--sfz/--midi/--wav/-s——源码是第一事实源）",
            "bgm_gen/engines/midi_util.py（R2：events_to_smf 编码器——事件通道字节层）",
            "sfizz_render.sh --help 参数名表（--sfz/--midi/--wav/-b/--blocksize/-s/--samplerate/-q/--quality/-p/--polyphony/-v/--verbose/--log/--use-eot/-h/--help；仅参数名表，不粘帮助正文）",
            "URL: https://sfzformat.com/（SFZ 规范：事件通道语义参考）",
            "URL: https://copr.fedorainfracloud.org/coprs/audinux/（audinux COPR sfizz-1.2.3-4.fc44.x86_64.rpm，BSD-2-Clause；binary_pin 版本来源，sfizz_render.sh 头部注释）",
        ],
        "parameters": [
            {"id": "sfz", "type": "path", "flags": ["required"],
             "description": "SFZ 引用：绝对路径 / 相对 assets_root/sfz（别名前缀归一 VSCO2→VSCO2_CE、Karoryfer→karoryfer）/ 纯文件名树内 sorted 唯一匹配（resolve_sfz；文件存在性由引擎守卫）"},
            {"id": "notes", "type": "tuple", "flags": ["required"],
             "schema": _COMMON_NOTES_SCHEMA,
             "description": "BG-D7 统一记谱 [[pitch, beat, dur, vel], ...]（midi_util write_notes_smf → 临时 SMF → sfizz_render --midi）"},
            {"id": "bpm", "type": "float", "min": 1, "default": 120.0,
             "unit": "bpm", "description": "beat→秒换算（写入 SMF set_tempo）"},
            {"id": "events", "type": "array", "flags": ["optional"],
             "schema": {
                 "items": {
                     "type": "object",
                     "required": ["type", "t"],
                     "properties": {
                         "t": {"type": "number", "minimum": 0},
                         "ctrl": {"type": "number", "minimum": 0, "maximum": 127},
                         "value": {"type": "number", "minimum": 0, "maximum": 127},
                         "semitones": {"type": "number"},
                         "pitch": {"type": "number", "minimum": 0, "maximum": 127},
                         "program": {"type": "number", "minimum": 0, "maximum": 127},
                         "bank": {"type": "number", "minimum": 0, "maximum": 128},
                         "vel": {"type": "number", "minimum": 0, "maximum": 127},
                     },
                 },
             },
             "description": "v2 控制事件数组（PLAN_v3 §3.2/BG-D14；R2 落地）：{type,t}+逐类型字段；type 六类闭集与逐类型字段约束由工程层 core/project.py 校验（描述符 schema 为结构性兜底）；非空时引擎改走 events_to_smf（无 events 保持旧路径字节不动）"},
            {"id": "bend_range", "type": "float", "min": 0, "exclusive_min": True,
             "default": 2.0, "unit": "st",
             "description": "弯音量程半音数（缺省 2.0）：pitch_bend 事件 14bit 线性映射 clamp(round_half_up(8192*(1+semitones/bend_range)), 0, 16383)；SFZ 文件级 pitch_wheel_range 逐文件不同——工程轨含弯音时必须显式声明（轨级 track.bend_range 经 track_projection 透传）"},
        ],
        "input_ports": [
            {"id": "notes", "type": "note_array", "description": "BG-D7 记谱"},
            {"id": "sfz", "type": "asset_path", "description": "SFZ 引用（resolve_sfz 规则）"},
        ],
        "events_supported": [
            {"type": "cc", "note": "sfizz 经 SMF 透传"},
            {"type": "pitch_bend", "note": "量程按轨级 bend_range（SFZ 文件级 pitch_wheel_range 逐文件不同——v2 含弯音事件的 sfz 轨必须显式声明）"},
            {"type": "keyswitch", "note": "sw_last 语义（VPO KS 文件实测）；KS 键区逐乐器避让音域"},
            {"type": "program_change", "note": "SFZ 文件内 <control>/program 解析（能力面=文件本身）"},
            {"type": "note_on", "note": "notes 投影载体（XOR，组装层合成——R2 落地）"},
            {"type": "note_off", "note": "同 note_on"},
        ],
        "formats": ["wav（sfizz_render --wav）"],
        "track_projection": {
            "bend_range": "track.bend_range（sfz 轨含 pitch_bend 必须显式声明；透传编码器）",
            "bpm": "meta.bpm",
            "events": "track.events（控制事件；非空才注入 payload——无 events 保持旧路径字节不动）",
            "hooks": [
                "③ meta 注入（bpm/sr）",
                "④ params 覆盖合并（显式优先）"
            ],
            "note": "R2 数据化：if/elif 退役为 track_projection + 四类封闭钩子（PLAN_v3 §3.1；重组装深比较锁 tests/test_events.py）",
            "notes": "track.notes（note 事件载体 XOR 时由组装层 events_to_notes 合成）",
            "sfz": "track.instrument"
        },
        "invariants": [
            {"id": "sfz_tool_required", "check": False,
             "rule": "runtime.json tools.sfizz_render 必须存在（引擎守卫；doctor 校验）"},
            {"id": "sfz_output_sr_exact", "check": False,
             "rule": "输出 sr=sfizz -s 参数（spec.sr 声明则必须相等——envelope 契约）；stderr 'Cannot set current thread scheduling' 属无害噪音（JACK 探测失败）"},
        ],
        "coverage": {
            "tier": "B",
            "evidence": [
                "bgm_gen/engines/sfz.py（渲染选项使用面：--sfz/--midi/--wav/-s——源码是第一事实源 C12）",
                "bgm_gen/engines/midi_util.py（R2：events_to_smf 编码器——事件通道字节层，tests/test_events.py 锚点锁定）",
                "sfizz_render.sh --help 参数名表实测（--blocksize/--quality/--polyphony/--use-eot 存在但未被引擎使用；harvest_log 存档）",
                "SFZ 规范 sfzformat.com（事件通道语义参考）",
                "audinux COPR sfizz-1.2.3-4.fc44.x86_64.rpm（BSD-2-Clause；binary_pin 版本来源，sfizz_render.sh 头部注释）",
            ],
            "probes": [
                "bgm_gen/tests/test_descriptors.py [fast] sfz_default（VSCO2 CelloEnsSusVib）/ sfz_bpm_boundary（bpm 边界近点 150）+ determinism 双渲",
                "bgm_gen/tests/test_events.py [fast] 编码锚点（ppq/KS 错开/bend 14bit/混装轨逐位一致）+ [slow] 冒烟：CC1 渐强（VPO PERF）/ keyswitch sw_last / pitch_bend 微分音（实测弯音量程→examples/fate/bend_range_evidence.json）",
            ],
            "gaps": [
                "能力面=SFZ 文件本身（对 AI 无闸门）；描述符只覆盖渲染选项与事件通道",
                "--blocksize/--quality/--polyphony/--use-eot 等 CLI 选项未接入引擎 payload（引擎只用 --sfz/--midi/--wav/-s）",
                "payload.events 通道 R2 落地（描述符 version 1.1.0；envelope spec_version 不变——BG-D14 口径）；SFZ 文件级 pitch_wheel_range 逐文件不同——工程轨含弯音必须显式声明 bend_range（实测证据 examples/fate/bend_range_evidence.json）",
                "tier B：参数语义依赖 CLI --help 参数名表+SFZ 规范佐证，非纯源码收割（sfizz 为黑盒二进制）",
                "bpm 收紧 min=1（同 synth；R1 审查 E-1 记录）",
            ],
        },
        "log_extra": [
            "R2（2026-08-23）：parameters +events（type=array, optional；事件对象 schema 片段）+bend_range（float>0 默认 2；SFZ 文件级 pitch_wheel_range 逐文件不同，工程轨含弯音时必须显式声明）；version 1.0.0→1.1.0（BG-D14：payload.events 引入伴随描述符 bump，envelope spec_version 不变）；来源指针=PLAN_v3 §3.2/§5 R2 行 + bgm_gen/tests/test_events.py + examples/fate/bend_range_evidence.json",
        ],
    },
    "sample": {
        "version": "1.0.0",
        "binary_pin": {
            "version": None,
            "sha256": None,
            "size": None,
            "note": "无独立二进制：numpy 时间线混合（wave 直读 PCM）；非 PCM/异 sr 采样经 ffmpeg 解码重采样（_wavio.read_audio 兜底）；解释器=runtime.json python_system",
            "additional_pins": [
                {"name": "ffmpeg", "version": "8.1.2", "sha256": None,
                 "note": "解码/重采样兜底（系统包）"},
            ],
        },
        "sources": [
            "bgm_gen/engines/sample.py（payload 双入口读取行为——收割第一事实源）",
            "bgm_gen/engines/_wavio.py（解码/写盘路径）",
        ],
        "parameters": [
            {"id": "hits", "type": "array",
             "schema": {
                 "minItems": 1,
                 "items": {
                     "type": "object",
                     "required": ["file"],
                     "properties": {
                         "file": {"type": "str", "description": "采样文件（绝对路径直通；相对 samples_root 严格拼接）"},
                         "at": {"type": "number", "minimum": 0, "description": "触发时刻（秒，默认 0）"},
                         "gain_db": {"type": "number", "description": "触发增益（dB，默认 0；10^(db/20) 线性）"},
                     },
                 },
             },
             "description": "入口一（hits 优先）：[{file, at(秒,默认0), gain_db(默认0)}, ...]；numpy 等功率叠加"},
            {"id": "instrument", "type": "path",
             "description": "入口二（notes 记谱法）的采样文件（file 解析规则同 hits[].file；走 notes 入口时必填——invariant sample_hits_or_notes_entry 强制）"},
            {"id": "notes", "type": "tuple", "schema": _COMMON_NOTES_SCHEMA,
             "description": "入口二（BG-D7 记谱法）：[[pitch(仅记录用), beat, dur, vel], ...]；at=beat*60/bpm；gain_db=20*log10(max(vel,1)/100)"},
            {"id": "bpm", "type": "float", "min": 1, "default": 120.0,
             "unit": "bpm", "description": "notes 入口的 beat→秒换算"},
            {"id": "length_s", "type": "float", "min": 0, "exclusive_min": True,
             "flags": ["required"], "unit": "s",
             "description": "输出总时长（秒；混合结果不足补零、超出截断）——无条件必填（两种入口同）"},
        ],
        "input_ports": [
            {"id": "hits", "type": "hit_list", "description": "入口一：命中列表"},
            {"id": "notes", "type": "note_array", "description": "入口二：BG-D7 记谱"},
            {"id": "instrument", "type": "asset_path", "description": "入口二的采样文件"},
        ],
        "events_supported": [],
        "formats": ["wav（pcm_s16le）"],
        "track_projection": {
            "bpm": "meta.bpm",
            "notes": "track.notes",
            "instrument": "track.instrument",
            "length_s": "track.window_s（clip×spb 派生量——钩子②）",
            "hooks": ["② sample length_s 派生量（clip×spb）"],
            "note": "R1 锁 core/project.py track_engine_spec 的 if/elif 原样；数据化=R2",
        },
        "invariants": [
            {"id": "sample_hits_or_notes_entry", "check": True,
             "rule": "hits 与 notes 至少一种入口且非空（hits 优先，与引擎一致）；走 notes 入口必须带 instrument（非空路径字符串）"},
            {"id": "sample_length_s_required", "check": False,
             "rule": "length_s 无条件必填（parameters flags=required 已表达；引擎内 n_out<=0 双保险守卫）"},
            {"id": "sample_gain_mapping", "check": False,
             "rule": "notes 入口 vel→gain_db=20*log10(max(vel,1)/100)；hits 入口 gain_db 直用"},
            {"id": "sample_hit_at_nonneg", "check": False,
             "rule": "hit.at 负值引擎循环内拒绝（hits[i].at 非法（负值））；start>=n_out 的 hit 跳过不报错"},
        ],
        "coverage": {
            "tier": "A",
            "evidence": [
                "bgm_gen/engines/sample.py（payload 双入口读取行为——收割第一事实源 C12）",
                "bgm_gen/engines/_wavio.py（解码/写盘路径）",
            ],
            "probes": [
                "bgm_gen/tests/test_descriptors.py [fast] sample_hits（hits+gain_db+length_s）+ sample_notes（instrument/notes/bpm 入口二）+ determinism 双渲",
            ],
            "gaps": [
                "采样根 samples_root 为跨项目指针（runtime.json，doctor 校验；改挪需用户拍板）",
                "hits[] 逐条 at/gain_db 类型在描述符 schema 覆盖，但 hits 内 file 存在性逐条由引擎循环守卫",
                "notes 入口 pitch 仅记录用（不驱动任何合成）",
                "hits=[]（伴合法 notes）旧引擎回退 notes 入口，现被 minItems 1 拒——合理 bugfix 收紧（R1 审查 E-2 记录）",
                "bpm 收紧 min=1（同 synth；R1 审查 E-1 记录）",
            ],
        },
    },
    "master": {
        "version": "1.0.0",
        "binary_pin": {
            "version": "8.1.2",
            "sha256": None,
            "size": None,
            "note": "ffmpeg（系统包；loudnorm 两遍法 + ebur128 复测，`ffmpeg -version` 探测交叉核验）",
            "additional_pins": [
                {"name": "matchering", "version": "2.0.6", "sha256": None,
                 "note": "comfyui env python 子进程跑 mx.process(target, reference, results=[pcm16])（无 CLI；doctor 经 env import 探测）"},
            ],
        },
        "sources": [
            "bgm_gen/engines/master.py（loudnorm 两遍法参数组装 + matchering mx.process 调用——收割第一事实源）",
            "ffmpeg loudnorm 过滤器文档（参数语义/单位参考；第一事实源=源码）",
            "comfyui env 实测：matchering 2.0.6（mx.process，入口形态 W1B RUNBOOK §3）",
        ],
        "parameters": [
            {"id": "input", "type": "path", "flags": ["required"],
             "description": "输入音频路径（文件存在性由引擎守卫）"},
            {"id": "mode", "type": "enum", "enum": ["loudnorm", "matchering"],
             "stepped": True, "default": "loudnorm",
             "description": "母带策略：loudnorm=ffmpeg 两遍法（-14LUFS/-1dBTP）；matchering=参考匹配（mx.process）"},
            {"id": "target_lufs", "type": "float", "default": -14.0, "unit": "LUFS",
             "description": "目标综合响度（仅 loudnorm；EBU R128，B 站标准）"},
            {"id": "true_peak", "type": "float", "default": -1.0, "unit": "dBTP",
             "description": "真峰值上限（仅 loudnorm；linear=true 优先，约束不满足时 ffmpeg 自动回退 dynamic 并在 summary 说明）"},
            {"id": "ref", "type": "path",
             "description": "matchering 模式必填：参考音频路径（invariant master_matchering_ref_required 强制非空；文件存在性由引擎守卫）"},
            {"id": "out", "type": "path",
             "description": "输出 wav 路径；缺省 <仓库根>/out/smoke/master/{mode}_{track}.wav（track=输入文件名去扩展名）"},
        ],
        "input_ports": [
            {"id": "input", "type": "audio_path", "description": "待母带音频"},
            {"id": "ref", "type": "audio_path", "description": "matchering 参考音频"},
        ],
        "events_supported": [],
        "formats": ["wav（pcm_s16le，输出保持输入 sr）"],
        "invariants": [
            {"id": "master_matchering_ref_required", "check": True,
             "rule": "mode=matchering → ref 必填（非空字符串）；文件存在性由引擎守卫（master payload.ref 不存在）"},
            {"id": "master_loudnorm_only_fields", "check": False,
             "rule": "target_lufs/true_peak 仅 loudnorm 模式生效；loudnorm 内部 LRA 恒 11、linear=true 固定（参数面未展开）"},
            {"id": "master_output_keeps_input_sr", "check": False,
             "rule": "loudnorm 第二遍 -ar 输入 sr（输出保持输入 sr）；matchering pcm16 结果同源 sr"},
        ],
        "coverage": {
            "tier": "A",
            "evidence": [
                "bgm_gen/engines/master.py（loudnorm 两遍法参数组装 + matchering mx.process 调用——收割第一事实源 C12）",
                "ffmpeg loudnorm 过滤器文档（参数语义/单位参考；第一事实源=源码）",
                "comfyui env 实测：matchering 2.0.6（mx.process，入口形态 W1B RUNBOOK §3）",
            ],
            "probes": [
                "bgm_gen/tests/test_descriptors.py [fast] master_loudnorm（target_lufs=-16/true_peak=-1.5 边界近点）+ determinism 双渲",
                "bgm_gen/tests/test_descriptors.py [slow] master_matchering（mode=matchering+ref 条件组合）",
            ],
            "gaps": [
                "matchering Config() 未参数化（仅 ref 暴露；mx 全参数面未展开）",
                "loudnorm LRA 恒 11、print_format/linear 固定（引擎内硬编码，非 payload 参数）",
                "matchering 无 CLI（mx.process 子进程脚本由引擎生成）——描述符覆盖 payload 面，不覆盖 mx 内部 config",
            ],
        },
    },
    "demucs": {
        "version": "1.0.0",
        "binary_pin": {
            "version": "4.1.0",
            "sha256": None,
            "size": None,
            "note": "comfyui env python -m demucs（子进程；doctor 经 env import demucs 探测交叉核验；权重缓存 HF_HOME 见 runtime.json demucs.env）",
        },
        "sources": [
            "bgm_gen/engines/demucs.py（payload 读取 + CLI 组装 + BG-D8 显存闸接线——收割第一事实源）",
            "comfyui env 实测：demucs 4.1.0 / torch 2.11.0+cu130",
        ],
        "parameters": [
            {"id": "input", "type": "path", "flags": ["required"],
             "description": "输入音频路径（文件存在性由引擎守卫）"},
            {"id": "model", "type": "str", "default": "htdemucs",
             "description": "demucs 模型名（枚举不封闭，透传 demucs CLI -n）"},
            {"id": "out_dir", "type": "path",
             "description": "stems 根目录；缺省 <仓库根>/out/smoke/demucs/；产物约定 {out_dir}/{track}/{stem}.wav"},
            {"id": "device", "type": "enum", "enum": ["auto", "gpu", "cpu"],
             "stepped": True, "default": "auto",
             "description": "auto/gpu 走 BG-D8 显存闸（auto 超时降级 cpu 并记录；gpu 超时抛错）；cpu=直跑不占锁"},
            {"id": "two_stems", "type": "str",
             "description": "两分轨模式（如 'vocals'→vocals/no_vocals）；缺省四分轨 vocals/drums/bass/other"},
            {"id": "vram_need_mb", "type": "int", "min": 0, "default": 4096,
             "unit": "MB", "description": "显存闸需求（BG-D8 gpu_guard）"},
            {"id": "vram_timeout_s", "type": "float", "min": 0, "default": 120.0,
             "unit": "s", "description": "显存闸等待上限（auto 超时降级 cpu；gpu 超时抛错）"},
            {"id": "offline", "type": "bool", "default": True,
             "description": "恒注入 HF_HUB_OFFLINE=1（权重已缓存；防静默回退下载第二份旧格式 .th 权重）"},
        ],
        "input_ports": [
            {"id": "input", "type": "audio_path", "description": "待分轨音频"},
        ],
        "events_supported": [],
        "formats": ["wav（htdemucs 恒 44100；spec.sr 声明其他值→引擎内 ffmpeg 重采样全部 stems）"],
        "invariants": [
            {"id": "demucs_env_required", "check": False,
             "rule": "runtime.json demucs.python + demucs.env.HF_HOME 必须齐（引擎守卫；HF_HOME 防回退下载旧权重——W1B 坑）"},
            {"id": "demucs_native_sr_44100", "check": False,
             "rule": "htdemucs 恒输出 44100（与输入 sr 无关）；引擎内按 spec.sr 重采样归一（BG-D5）；输出 sr≠44100 视为异常报 EngineError"},
            {"id": "demucs_track_from_input_name", "check": False,
             "rule": "track=输入文件名去扩展名；出参 path=vocals stem（惯例主输出；纯器乐输入接近无声是正确路由）"},
        ],
        "coverage": {
            "tier": "A",
            "evidence": [
                "bgm_gen/engines/demucs.py（payload 读取 + CLI 组装 + BG-D8 显存闸接线——收割第一事实源 C12）",
                "comfyui env 实测：demucs 4.1.0 / torch 2.11.0+cu130",
            ],
            "probes": [
                "bgm_gen/tests/test_descriptors.py [slow] demucs 探针（vram_need_mb/vram_timeout_s/offline/model 全参数赋值；输入 out/smoke/demucs/demucs_in10s.wav 缺失时 pytest.skip——worktree 无此文件，主树合流后会有）",
            ],
            "gaps": [
                "model 名枚举不封闭（任何 demucs 模型名透传；模型能力面=demucs 仓库）",
                "GPU 显存闸策略（排队/降级/阈值）在 gpu_guard 内部，不在描述符参数面（vram_need_mb/vram_timeout_s 已暴露）",
                "determinism=tolerant（GPU 归约非逐位；分级证据 tests/baselines_w2b.json）",
            ],
        },
    },
    "ai": {
        "version": "1.0.0",
        "binary_pin": {
            "version": "0.33.0",
            "sha256": None,
            "size": None,
            "note": "ComfyUI comfyui_version（GET /system_stats 实测；doctor 交叉核验）",
        },
        "sources": [
            "bgm_gen/engines/ai.py（提交/轮询/取回行为——收割第一事实源）",
            "ComfyUI /system_stats 实测 comfyui_version=0.33.0（binary_pin 来源）",
            "ComfyUI /object_info 实测（params 点路径注入面；service 探针交叉核验）",
        ],
        "parameters": [
            {"id": "workflow", "type": "path", "flags": ["required"],
             "description": "ComfyUI API 格式 workflow JSON 文件路径（同 legacy/gen.py 用法；文件存在性由引擎守卫）"},
            {"id": "params", "type": "object", "default": {},
             "description": "点路径覆盖：{节点id.inputs.字段: 值}（如 94.inputs.duration/104s.inputs.filename_prefix）；值限 int/float/str/bool；路径不存在即报错（引擎守卫 _apply_overrides）"},
            {"id": "timeout_s", "type": "float", "min": 1, "default": 600.0,
             "unit": "s", "description": "完成等待上限（超时抛 EngineError，任务仍在队列/执行）"},
            {"id": "out", "type": "path",
             "description": "输出文件（音频扩展名结尾→首个输出写入该路径）或目录；缺省 <仓库根>/out/smoke/ai/（多音频全落盘，第一个为出参 path）"},
        ],
        "input_ports": [
            {"id": "workflow", "type": "json_path", "description": "ComfyUI API 格式 workflow"},
            {"id": "params", "type": "override_map", "description": "点路径覆盖（节点id.inputs.字段）"},
        ],
        "events_supported": [],
        "formats": [".wav", ".flac", ".mp3", ".ogg", ".m4a", ".opus"],
        "invariants": [
            {"id": "ai_params_value_types", "check": False,
             "rule": "params 覆盖值仅限 int/float/str/bool（引擎守卫 _apply_overrides；其余类型 EngineError）"},
            {"id": "ai_params_path_exists", "check": False,
             "rule": "点路径必须命中 workflow 顶层节点与其 inputs 字段（引擎守卫；路径不存在即报错）"},
            {"id": "ai_service_dependency", "check": False,
             "rule": "ComfyUI base_url 可达才执行（service 档语义；不可达 EngineError，pytest 显式 skip 绝不静默当绿）"},
        ],
        "coverage": {
            "tier": "A",
            "evidence": [
                "bgm_gen/engines/ai.py（提交/轮询/取回行为——收割第一事实源 C12）",
                "ComfyUI /system_stats 实测 comfyui_version=0.33.0（binary_pin 来源）",
                "ComfyUI /object_info 实测（params 点路径注入面；service 探针交叉核验）",
            ],
            "probes": [
                "bgm_gen/tests/test_descriptors.py [service] ai 探针（8188 不可达 skip；探针=参数注入点被 /object_info 接受，非每参数全渲）",
            ],
            "gaps": [
                "params 点路径覆盖集随 workflow 文件变化（描述符不枚举具体节点/字段——workflow 文件本身是事实源）",
                "determinism=nondeterministic（GPU 扩散采样，seed 固定亦不保证逐位一致；冻结复现协议=生成物落盘后工程按绝对路径引用）",
                "底层模型版本（ACE-Step 1.5/Music3 等）不在描述符内（workflow 文件事实，非引擎契约面）",
            ],
        },
    },
}


# ------------------------------------------------------------ 描述符生成/核验

def descriptor_from_facts(engine):
    facts = ENGINE_FACTS[engine]
    desc = {
        "engine": engine,
        "version": facts["version"],
        "binary_pin": facts["binary_pin"],
        "parameters": facts["parameters"],
        "input_ports": facts["input_ports"],
        "events_supported": facts["events_supported"],
        "formats": facts["formats"],
    }
    if "track_projection" in facts:
        desc["track_projection"] = facts["track_projection"]
    desc["invariants"] = facts["invariants"]
    desc["coverage"] = facts["coverage"]
    return desc


def _dump(desc):
    return json.dumps(desc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def cmd_write(engines):
    """事实表 → descriptors/<engine>.json（git 内描述符与事实表逐字节可再生成）。"""
    for eng in engines:
        facts = ENGINE_FACTS[eng]
        desc = descriptor_from_facts(eng)
        path = os.path.join(DESC_DIR, f"{eng}.json")
        os.makedirs(DESC_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(_dump(desc))
        print(f"已写入 {path}（v{desc['version']} · tier {facts['coverage']['tier']}）")
    return 0


def _param_line(p):
    bits = [p["id"], p.get("type", "?"),
            ",".join(p.get("flags") or []) or "-"]
    if p.get("default") is not None:
        bits.append(f"默认{p['default']}")
    if p.get("min") is not None or p.get("max") is not None:
        bits.append(f"范围{p.get('min', '…')}..{p.get('max', '…')}")
    if p.get("enum"):
        bits.append("枚举" + "/".join(str(e) for e in p["enum"]))
    if p.get("unit"):
        bits.append(p["unit"])
    return " | ".join(bits) + " | " + (p.get("description") or "")


def cmd_log(engines):
    """证据日志 harvest_log/<engine>.log（纪律 C10：命令行/参数名表/类型单位/
    短语义摘要 ≤1 行/参数/来源指针；禁粘手册/帮助正文）。"""
    os.makedirs(LOG_DIR, exist_ok=True)
    for eng in engines:
        facts = ENGINE_FACTS[eng]
        desc = descriptor_from_facts(eng)
        pin = facts["binary_pin"]
        lines = [
            f"# {eng} 描述符收割证据日志（harvest.py 生成）",
            f"generated: {datetime.date.today().isoformat()}",
            f"tool: python3 -m bgm_gen.engines.harvest log {eng}",
            f"descriptor: bgm_gen/engines/descriptors/{eng}.json"
            f"（v{desc['version']} · tier {facts['coverage']['tier']}）",
            "harvest_method: "
            + ("A=程序化收割（第一事实源=引擎源码 payload 读取行为）"
               if facts["coverage"]["tier"] == "A"
               else "B=AI 起草+探针验证（CLI --help 参数名表+SFZ 规范+源码佐证）"),
            "sources:",
        ]
        for s in facts["sources"]:
            lines.append(f"  - {s}")
        lines.append("binary_facts:")
        lines.append(f"  version = {pin.get('version')!r}"
                     + (f"（{pin.get('note')}）" if pin.get("note") else ""))
        if pin.get("sha256"):
            lines.append(f"  sha256 = {pin['sha256']}")
        if pin.get("size"):
            lines.append(f"  size = {pin['size']}")
        for a in pin.get("additional_pins") or []:
            lines.append(f"  {a['name']} = {a.get('version')!r}"
                         + (f"（{a['note']}）" if a.get("note") else ""))
        lines.append("params（参数名表；类型/单位/短语义 ≤1 行/参数）:")
        for p in facts["parameters"]:
            lines.append(f"  {_param_line(p)}")
        lines.append("events_supported: "
                     + (",".join(e["type"] for e in facts["events_supported"])
                        or "（无——synth/sample 拒任何事件）"))
        lines.append("coverage.gaps:")
        for g in facts["coverage"]["gaps"]:
            lines.append(f"  - {g}")
        for extra in facts.get("log_extra") or []:
            lines.append(f"changelog: {extra}")
        path = os.path.join(LOG_DIR, f"{eng}.log")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"已写入 {path}")
    return 0


def _probe_binary(fact_name, rt):
    """实测二进制事实（与 doctor 同口径的收割器独立实现）。
    fluidsynth/ffmpeg=版本串；sfizz_render=(sha256, size)；其余返回 None。"""
    if fact_name == "fluidsynth":
        try:
            r = subprocess.run(["fluidsynth", "--version"], capture_output=True,
                               text=True, timeout=30)
            m = re.search(r"runtime version ([\d.]+)",
                          (r.stdout or "") + (r.stderr or ""))
            return m.group(1) if m else None
        except Exception:                                        # noqa: BLE001
            return None
    if fact_name == "ffmpeg":
        try:
            r = subprocess.run(["ffmpeg", "-version"], capture_output=True,
                               text=True, timeout=30)
            m = re.search(r"ffmpeg version ([\d.]+)", r.stdout or "")
            return m.group(1) if m else None
        except Exception:                                        # noqa: BLE001
            return None
    if fact_name == "sfizz_render":
        tool = (rt.get("tools") or {}).get("sfizz_render") or ""
        bin_path = tool
        if os.path.basename(tool) == "sfizz_render.sh":
            alt = os.path.join(os.path.dirname(tool), "usr", "bin", "sfizz_render")
            if os.path.isfile(alt):
                bin_path = alt
        if not os.path.isfile(bin_path):
            return ("missing",)
        try:
            with open(bin_path, "rb") as f:
                data = f.read()
            return (hashlib.sha256(data).hexdigest(), len(data))
        except Exception:                                        # noqa: BLE001
            return ("unreadable",)
    return None


def cmd_verify(engines):
    """①描述符 == 事实表（深比较）②便宜二进制（fluidsynth/ffmpeg/sfizz）实测比对。
    comfyui/demucs/matchering 为 env 探针，只打印不拉红（doctor 以 warn 项覆盖）。"""
    from ..paths import effective_runtime
    rt = effective_runtime()
    bad = []
    for eng in engines:
        facts = ENGINE_FACTS[eng]
        want = descriptor_from_facts(eng)
        path = os.path.join(DESC_DIR, f"{eng}.json")
        if not os.path.isfile(path):
            bad.append(f"{eng}：描述符缺失（应 `harvest write`）")
            continue
        with open(path, encoding="utf-8") as f:
            got = json.load(f)
        if got != want:
            bad.append(f"{eng}：描述符与事实表不一致（改事实表后须 `harvest write`）")
        pin = facts["binary_pin"]
        checks = [(_PRIMARY_FACT.get(eng), pin)]
        checks += [(a.get("name"), a) for a in pin.get("additional_pins") or []]
        for fact_name, p in checks:
            if fact_name in (None, "demucs", "matchering", "comfyui_version"):
                continue            # 纯 numpy 实现 / env 探针：doctor 负责（warn 语义）
            got_v = _probe_binary(fact_name, rt)
            if fact_name == "sfizz_render":
                if (not isinstance(got_v, tuple) or len(got_v) < 2
                        or got_v[0] != pin["sha256"] or got_v[1] != pin["size"]):
                    bad.append(f"{eng}.{fact_name}：实测 sha/size 与事实表不符"
                               f"（{got_v}）")
                else:
                    print(f"  {eng}.{fact_name} = {got_v[0][:16]}…/{got_v[1]}（sha/size 一致）")
            elif p.get("version") is not None and got_v != p["version"]:
                bad.append(f"{eng}.{fact_name}：实测 {got_v!r} ≠ 事实表 {p['version']!r}")
            else:
                print(f"  {eng}.{fact_name} = {got_v}（一致）")
    if bad:
        print("verify 失败：")
        for b in bad:
            print(f"  - {b}")
        return 1
    print(f"verify 通过（{len(engines)} 引擎描述符与事实表一致）")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="harvest", description="引擎描述符手动收割器（PLAN_v3 §3.1；事实表=本模块 ENGINE_FACTS）")
    p.add_argument("cmd", choices=["write", "log", "verify"],
                   help="write=事实表→描述符；log=证据日志；verify=一致性+二进制实测")
    p.add_argument("engines", nargs="*",
                   help="引擎名（缺省=全部 7 引擎）")
    a = p.parse_args(argv)
    engines = a.engines or sorted(ENGINE_FACTS)
    unknown = [e for e in engines if e not in ENGINE_FACTS]
    if unknown:
        p.error(f"未知引擎：{unknown}；已知：{sorted(ENGINE_FACTS)}")
    if a.cmd == "write":
        return cmd_write(engines)
    if a.cmd == "log":
        return cmd_log(engines)
    return cmd_verify(engines)


if __name__ == "__main__":
    sys.exit(main())
