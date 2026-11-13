"""R2 事件流 + params 透传槽 + track_projection 数据化测试（PLAN_v3 §6 test_events 行）。

[fast]  ①事件编码字节锚点（独立 SMF 读器对拍）：event_sort_key 全序（同 tick
        类型序/pitch 升序/插入序）、bank-PC 复合（CC0/32 在 0xC0 前）、相邻同
        tick 双 KS on/off/on/off 各 +1 tick 错开、cc round-half-up（64.5→65，
        防 banker's rounding）+clamp、pitch_bend 14bit 映射往返（±2→0..16383，
        clamp）、ppq 入参生效、**write_notes_smf 产物 .mid 直接 sha 锚点**
        （旧路径字节零改动锁——红线⑤）；
        ②工程校验负例族：XOR 双向（notes+note 事件并存；二者皆无）、悬空
        note_on、同 pitch 交叠、note_off 无 on、note_on vel=0、synth/sample 轨
        任何事件、sf2 轨 keyswitch/program_change、sfz 轨弯音缺 bend_range、
        params 未知键/越界值/投影锁定键（params.bpm）/跨引擎错键（synth 轨
        params.gain）；正例：events-only 轨缺省 clip 推导（note_off 最大 beat
        +4 拍尾巴）、混装轨（notes+cc）通过、sf2 params.gain 合法、v2 三键齐备；
        ③渲染语义：events-only 轨经 sfz 小样渲染出声；混装轨（notes+cc）渲染与
        纯 notes 轨逐位一致（cc 不干扰音符字节——同 tick 排序正确性）；
        ④重组装深比较锁：minimal.json + baselines_w2a 全部 spec（track 引擎 6 条）+
        out/eden_v2/project.json（缺失则 skip）——测试内冻结的旧 if/elif 参照实现
        产出期望 payload，断言数据化后 track_engine_spec 输出逐字段相等。
[slow]  冒烟四件：①VPO 弦乐 CC1 渐强事件链（RMS 分段单调上升）；②弯音微分音
        （sfz 轨 pitch_bend 事件频移可测）+ 实测 VPO 目标文件实际弯音量程，证据
        写入 examples/fate/bend_range_evidence.json（R4 mapping 消费）；③keyswitch
        探针：VPO KS 文件（sw_last 语义）keyswitch 事件切换演奏法有效（RMS/起音
        变化可测），无效则如实记录触发回退策略；④sf2 轨 params.gain 生效
        （v2 工程 params={"gain":0.5} 渲染 RMS 低于 gain=1.0）。

纪律：渲染输出一律写 helpers.tmpdir；examples/fate/bend_range_evidence.json 为
R2 证据交付物（sfizz 确定性渲染 + numpy 确定性测量 → 逐次字节稳定）。
"""

import json
import os
import struct
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers                                              # noqa: E402

helpers.in_proc_env()
from bgm_gen.engines import run_engine                      # noqa: E402
from bgm_gen.engines import midi_util as mu                 # noqa: E402
from bgm_gen.core.project import (ProjectError, build_render_plan,  # noqa: E402
                                  load_project, track_cc_curve,
                                  track_engine_spec)

MINIMAL = os.path.join(helpers.EXAMPLES_DIR, "minimal.json")
EDEN_V2 = os.path.join(helpers.OUT_DIR, "eden_v2", "project.json")
# 2026-09-09 分家：examples/fate/ 迁 music_project——弯音量程证据文件随 fate 工程
# 走（其消费方=R4 命运 mapping）；本测试的量程实测仍有效，证据改落临时目录。
BEND_EVIDENCE = os.path.join(helpers.tmpdir("bend_evidence"),
                             "bend_range_evidence.json")

# write_notes_smf 产物 .mid 直接 sha 锚点（旧路径字节零改动；2026-08-23 R2 冻结）
ANCHOR_MID_SHA = "d5c60f1b16920f1d879fa2cca2e428b5985343dd2b4a65cd45e0a192dcd12a3e"
ANCHOR_MID_SHA_CH9_PROG = "edbaafa7e978c96c3a6625ee6929718c87c2ec29f8d80f61a984313501ba7bc4"
_ANCHOR_NOTES = [[36, 0, 0.25, 127], [38, 1, 0.5, 100], [48, 2, 1.0, 64]]

_CELLO = "VSCO2/CelloEnsSusVib.sfz"


# ---------------------------------------------------------------- SMF 读器（测试内独立实现）
def _vlq(data, i):
    v = 0
    while True:
        b = data[i]
        i += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, i


def _read_smf(path):
    """SMF 读取 → (ppq, [(track_idx, tick, status_or_kind, d1, d2)...])。
    编码器恒写完整 status（无 running status）；meta tempo/eot 记为 ("tempo"/"eot")。"""
    blob = open(path, "rb").read()
    assert blob[:4] == b"MThd"
    fmt, ntrk, ppq = struct.unpack(">HHH", blob[8:14])
    assert fmt == 1
    events, off = [], 14
    for ti in range(ntrk):
        assert blob[off:off + 4] == b"MTrk"
        ln = struct.unpack(">I", blob[off + 4:off + 8])[0]
        data = blob[off + 8:off + 8 + ln]
        off += 8 + ln
        t, i = 0, 0
        while i < len(data):
            d, i = _vlq(data, i)
            t += d
            st = data[i]
            if st == 0xFF:                      # meta
                mt, i = data[i + 1], i + 2
                ml, i = _vlq(data, i)
                if mt == 0x51 and ml == 3:
                    events.append((ti, t, "tempo", int.from_bytes(data[i:i + 3], "big")))
                elif mt == 0x2F:
                    events.append((ti, t, "eot", None))
                i += ml
            else:
                n = 2 if st in (0xC0, 0xD0) else 3      # PC/channel pressure = 2 字节
                events.append((ti, t, st, data[i + 1],
                               data[i + 2] if n == 3 else None))
                i += n                                  # i 在 status 位：越过 1 status + n-1 data
    return ppq, events


def _channel_events(evs):
    """通道事件（排除 meta/eot）。"""
    out = []
    for e in evs:
        if isinstance(e[2], int) and e[2] < 0xF0:
            out.append((e[1], e[2], e[3], e[4]))
    return out


# ------------------------------------------------------------ ① 编码字节锚点 [fast]
@pytest.mark.fast
def test_event_sort_key_total_order():
    """C3 全序：同 tick 类型序 keyswitch→program_change→cc→pitch_bend→note_off→
    note_on；pitch 升序；插入序稳定。"""
    events = [
        {"type": "note_on", "t": 0, "pitch": 72, "vel": 100},
        {"type": "note_off", "t": 0, "pitch": 72},
        {"type": "cc", "t": 0, "ctrl": 1, "value": 1},
        {"type": "cc", "t": 0, "ctrl": 2, "value": 2},
        {"type": "pitch_bend", "t": 0, "semitones": 1},
        {"type": "program_change", "t": 0, "program": 1},
        {"type": "keyswitch", "t": 0, "pitch": 36},
        {"type": "note_on", "t": 0, "pitch": 60, "vel": 90},   # pitch 60 先于 72
        {"type": "keyswitch", "t": 1, "pitch": 40},             # t 升序压过类型序
    ]
    order = [events[i]["type"] for i, _ in sorted(
        enumerate(events), key=lambda pair: mu.event_sort_key(pair[1], pair[0]))]
    assert order == ["keyswitch", "program_change", "cc", "cc", "pitch_bend",
                     "note_off", "note_on", "note_on", "keyswitch"]
    assert events[8]["pitch"] == 40                             # 末位 = t=1 的 KS
    # 插入序稳定：同键事件保原序
    ev2 = [{"type": "cc", "t": 0, "ctrl": 9, "value": 9},
           {"type": "cc", "t": 0, "ctrl": 8, "value": 8}]
    order2 = [ev2[i]["ctrl"] for i, _ in sorted(
        enumerate(ev2), key=lambda pair: mu.event_sort_key(pair[1], pair[0]))]
    assert order2 == [9, 8]


@pytest.mark.fast
def test_same_tick_float_boundary_note_off_before_on():
    """R2 审查官 A·1 回归锁：相邻同音高音符边界浮点不同但编码同 tick 时，
    note_off 必须先于 note_on（排序主键=编码 tick 而非浮点 beat），否则新音符
    音头被掐（旧路径 write_notes_smf 为 off→on 续接）。"""
    notes = [[60, 5.11031, 0.6348, 100], [60, 5.7449, 1.0, 100]]
    events = mu.notes_to_events(notes) + [
        {"type": "cc", "t": 5.7449, "ctrl": 1, "value": 100}]
    order = [e["type"] for _, e in sorted(
        enumerate(events), key=lambda p: mu.event_sort_key(p[1], p[0], 480))]
    # tick(5.11031+0.6348)=tick(5.7449)=2758：同 tick 内 cc→note_off1→note_on2。
    # 修复前（浮点主键）off1(5.74511) 会排在 on2(5.7449) 之后 → 音头被掐。
    assert order == ["note_on", "cc", "note_off", "note_on", "note_off"], order


@pytest.mark.fast
def test_bank_pc_composite_cc0_cc32_before_pc():
    """C3a：带 bank 的 PC = CC0/CC32 紧贴 0xC0 之前；bank CC 不参与通用 cc 排序
    （同 tick 下 PC 复合体整体按 program_change 类型序排前）。"""
    path = os.path.join(helpers.tmpdir("ev"), "pc_bank.mid")
    mu.events_to_smf(path, [
        {"type": "note_on", "t": 0, "pitch": 60, "vel": 100},
        {"type": "program_change", "t": 0, "program": 5, "bank": 1},
    ], bpm=120)
    _, evs = _read_smf(path)
    assert _channel_events(evs) == [
        (0, 0xB0, 0, 0), (0, 0xB0, 32, 1), (0, 0xC0, 5, None),
        (0, 0x90, 60, 100)]
    # bank 128 = GM 打击：msb=1 lsb=0
    path2 = os.path.join(helpers.tmpdir("ev"), "pc_bank128.mid")
    mu.events_to_smf(path2, [{"type": "program_change", "t": 0, "program": 0,
                              "bank": 128}], bpm=120)
    _, evs2 = _read_smf(path2)
    assert _channel_events(evs2) == [(0, 0xB0, 0, 1), (0, 0xB0, 32, 0),
                                     (0, 0xC0, 0, None)]


@pytest.mark.fast
def test_adjacent_same_tick_double_ks_stagger():
    """相邻同 tick 双 KS：编码 on/off/on/off 各 +1 tick 确定性错开（R3-P8）。
    KS1（pitch36）on@0 off@1（被"下一 KS tick-1"钳制）；KS2（pitch38）on@1 off@121。"""
    path = os.path.join(helpers.tmpdir("ev"), "ks2.mid")
    mu.events_to_smf(path, [
        {"type": "keyswitch", "t": 0, "pitch": 36},
        {"type": "keyswitch", "t": 0, "pitch": 38},
    ], bpm=120)
    ppq, evs = _read_smf(path)
    assert ppq == 480
    notes = [(t, st, d1) for t, st, d1, _ in _channel_events(evs)]
    assert notes == [(0, 0x90, 36), (1, 0x80, 36), (1, 0x90, 38), (121, 0x80, 38)]


@pytest.mark.fast
def test_keyswitch_off_tick_clamped_by_next_ks():
    """KS off tick = clamp(min(下一 KS tick-1, KS tick+0.25beat·ppq), ≥KS tick+1)；
    beat 0 合法（KS tick0 边界，R3-P8）。"""
    path = os.path.join(helpers.tmpdir("ev"), "ks_next.mid")
    mu.events_to_smf(path, [
        {"type": "keyswitch", "t": 0, "pitch": 36},
        {"type": "keyswitch", "t": 0.25, "pitch": 40},   # tick 120：off 被钳到 119
    ], bpm=120)
    _, evs = _read_smf(path)
    notes = [(t, st, d1) for t, st, d1, _ in _channel_events(evs)]
    assert notes == [(0, 0x90, 36), (119, 0x80, 36), (120, 0x90, 40), (240, 0x80, 40)]


@pytest.mark.fast
def test_cc_round_half_up_and_clamp():
    """C3b：cc 编码 round-half-up（64.5→65、0.5→1，防 banker's rounding）+ clamp 0..127。"""
    path = os.path.join(helpers.tmpdir("ev"), "cc.mid")
    mu.events_to_smf(path, [
        {"type": "cc", "t": 0, "ctrl": 7, "value": 64.5},
        {"type": "cc", "t": 0.5, "ctrl": 7, "value": 0.5},
        {"type": "cc", "t": 1.0, "ctrl": 7, "value": 0.4},
        {"type": "cc", "t": 1.5, "ctrl": 7, "value": 300},   # clamp → 127
        {"type": "cc", "t": 2.0, "ctrl": 7, "value": -5},    # clamp → 0
    ], bpm=120)
    _, evs = _read_smf(path)
    vals = [d2 for t, st, d1, d2 in _channel_events(evs) if st == 0xB0]
    assert vals == [65, 1, 0, 127, 0]


@pytest.mark.fast
def test_pitch_bend_14bit_roundtrip_and_clamp():
    """0xE0 14bit 线性映射 clamp(round_half_up(8192*(1+semitones/bend_range)),0,16383)。"""
    path = os.path.join(helpers.tmpdir("ev"), "bend.mid")
    mu.events_to_smf(path, [
        {"type": "pitch_bend", "t": 0, "semitones": 2.0},     # +range → 16383
        {"type": "pitch_bend", "t": 0.5, "semitones": -2.0},  # -range → 0
        {"type": "pitch_bend", "t": 1.0, "semitones": 0.0},   # 8192
        {"type": "pitch_bend", "t": 1.5, "semitones": 5.0},   # 超量程 → clamp 16383
        {"type": "pitch_bend", "t": 2.0, "semitones": -9.0},  # → clamp 0
    ], bpm=120)
    _, evs = _read_smf(path)
    vals = [(d2 << 7) | d1 for t, st, d1, d2 in _channel_events(evs) if st == 0xE0]
    assert vals == [16383, 0, 8192, 16383, 0]
    # 显式 bend_range=12：+12 半音才满量程
    path2 = os.path.join(helpers.tmpdir("ev"), "bend12.mid")
    mu.events_to_smf(path2, [
        {"type": "pitch_bend", "t": 0, "semitones": 12.0},
        {"type": "pitch_bend", "t": 0.5, "semitones": 6.0},   # 8192*(1.5)=12288
    ], bpm=120, bend_range=12.0)
    _, evs2 = _read_smf(path2)
    vals2 = [(d2 << 7) | d1 for t, st, d1, d2 in _channel_events(evs2) if st == 0xE0]
    assert vals2 == [16383, 12288]


@pytest.mark.fast
def test_ppq_param_effective():
    """ppq 入参生效（命运链路用 384 整数倍 PPQ 避免微移）。"""
    path = os.path.join(helpers.tmpdir("ev"), "ppq.mid")
    mu.events_to_smf(path, [{"type": "cc", "t": 1.0, "ctrl": 1, "value": 1}],
                     bpm=120, ppq=384)
    ppq, evs = _read_smf(path)
    assert ppq == 384
    assert _channel_events(evs)[0][0] == 384


@pytest.mark.fast
def test_write_notes_smf_mid_sha_anchor():
    """旧路径 write_notes_smf 产物 .mid 直接 sha 锚点（字节零改动锁；既有渲染
    锚点为间接锚，本测试为直接锚——PLAN_v3 §3.2）。"""
    for tag, ch, prog, sha in (("a", 0, None, ANCHOR_MID_SHA),
                               ("b", 9, 0, ANCHOR_MID_SHA_CH9_PROG)):
        path = os.path.join(helpers.tmpdir("ev"), f"wn_{tag}.mid")
        mu.write_notes_smf(path, _ANCHOR_NOTES, bpm=120, ch=ch, prog=prog)
        assert helpers.sha256(path) == sha, f"write_notes_smf 字节漂移（{tag}）"


@pytest.mark.fast
def test_notes_to_events_shape():
    """notes 四元组 → on/off 对；note_off 带内部 _dur（编码器 Trk 同公式求 off tick）。"""
    evs = mu.notes_to_events([[36, 1.0, 0.5, 100], [48, 2.0, 0.25, 64]])
    assert [e["type"] for e in evs] == ["note_on", "note_off", "note_on", "note_off"]
    assert evs[0] == {"type": "note_on", "t": 1.0, "pitch": 36.0, "vel": 100.0}
    assert evs[1]["t"] == 1.5 and evs[1]["_dur"] == 0.5
    assert evs[3]["t"] == 2.25 and evs[3]["pitch"] == 48.0


@pytest.mark.fast
def test_note_off_velocity_zero_byte_anchor():
    """§3.2 类型表：note_off 编码 velocity=0（与既有 Trk.note 出口一致）——
    全部 0x80 事件第 2 数据字节恒 0（note 载体 note_off 与 keyswitch off 同口径；
    R4-W2 覆盖审计补——此前各字节锚点均未断言该数据字节）。"""
    path = os.path.join(helpers.tmpdir("ev"), "noff.mid")
    mu.events_to_smf(path, [
        {"type": "note_on", "t": 0, "pitch": 60, "vel": 100},
        {"type": "note_off", "t": 1, "pitch": 60},
        {"type": "keyswitch", "t": 2, "pitch": 36},
    ], bpm=120)
    _, evs = _read_smf(path)
    offs = [(t, d1, d2) for t, st, d1, d2 in _channel_events(evs) if st == 0x80]
    # note_off@beat1 → tick480 vel0；KS off = 960 + 0.25beat(120tick) = 1080 vel0
    assert offs == [(480, 60, 0), (1080, 36, 0)], offs
    # 旧路径 write_notes_smf 同口径（Trk note off velocity=0，红线⑤字节不变）
    path2 = os.path.join(helpers.tmpdir("ev"), "noff_old.mid")
    mu.write_notes_smf(path2, [[60, 0, 1.0, 100]], bpm=120)
    _, evs2 = _read_smf(path2)
    offs2 = [(d1, d2) for t, st, d1, d2 in _channel_events(evs2) if st == 0x80]
    assert offs2 == [(60, 0)], offs2


# ------------------------------------------------------- ② 工程校验负例族 [fast]
def _v2(d, name, mutate):
    proj = helpers.load_json(MINIMAL)
    proj["meta"]["spec_version"] = "2"
    mutate(proj)
    path = os.path.join(d, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(proj, f, ensure_ascii=False)
    return path


NEG_CASES = [
    ("xor_both", "notes 与 note_on/note_off 事件互斥",
     lambda p: p["tracks"][1].__setitem__("events", [
         {"type": "note_on", "t": 0, "pitch": 60, "vel": 100},
         {"type": "note_off", "t": 1, "pitch": 60}])),
    ("xor_none", "二者必须恰有其一",
     lambda p: (p["tracks"][1].__setitem__("notes", []),
                p["tracks"][1].__setitem__("events", []))),
    ("dangling_on", "悬空 note_on",
     lambda p: (p["tracks"][1].__setitem__("notes", []),
                p["tracks"][1].__setitem__("events", [
                    {"type": "note_on", "t": 0, "pitch": 60, "vel": 100}]))),
    ("overlap", "交叠",
     lambda p: (p["tracks"][1].__setitem__("notes", []),
                p["tracks"][1].__setitem__("events", [
                    {"type": "note_on", "t": 0, "pitch": 60, "vel": 100},
                    {"type": "note_on", "t": 0.5, "pitch": 60, "vel": 100},
                    {"type": "note_off", "t": 1, "pitch": 60},
                    {"type": "note_off", "t": 1.5, "pitch": 60}]))),
    ("off_no_on", "无对应 note_on 的 note_off",
     lambda p: (p["tracks"][1].__setitem__("notes", []),
                p["tracks"][1].__setitem__("events", [
                    {"type": "note_off", "t": 0, "pitch": 60}]))),
    ("vel_zero", "vel：0 越界",
     lambda p: (p["tracks"][1].__setitem__("notes", []),
                p["tracks"][1].__setitem__("events", [
                    {"type": "note_on", "t": 0, "pitch": 60, "vel": 0},
                    {"type": "note_off", "t": 1, "pitch": 60}]))),
    ("synth_any_event", "引擎 synth 不支持 cc 事件",
     lambda p: p["tracks"][0].__setitem__("events", [
         {"type": "cc", "t": 0, "ctrl": 7, "value": 10}])),
    ("sample_note_event", "引擎 sample 不支持 note_on 事件",
     lambda p: p["tracks"][2].__setitem__("events", [
         {"type": "note_on", "t": 0, "pitch": 70, "vel": 100},
         {"type": "note_off", "t": 0.1, "pitch": 70}])),
    ("sf2_keyswitch", "引擎 sf2 不支持 keyswitch 事件",
     lambda p: p["tracks"][1].__setitem__("events", [
         {"type": "keyswitch", "t": 0, "pitch": 36}])),
    ("sf2_program_change", "引擎 sf2 不支持 program_change 事件",
     lambda p: p["tracks"][1].__setitem__("events", [
         {"type": "program_change", "t": 0, "program": 5}])),
    ("sfz_bend_no_decl", "必须显式声明",
     lambda p: (p["tracks"][1].__setitem__("engine", "sfz"),
                p["tracks"][1].__setitem__("instrument", _CELLO),
                p["tracks"][1].__setitem__("events", [
                    {"type": "pitch_bend", "t": 0, "semitones": 1.0}]))),
    ("params_unknown_key", "未知参数",
     lambda p: p["tracks"][1].__setitem__("params", {"nope": 1})),
    ("params_out_of_range", "gain=-1.0 越界",
     lambda p: p["tracks"][1].__setitem__("params", {"gain": -1.0})),
    ("params_locked_key", "投影锁定字段",
     lambda p: p["tracks"][1].__setitem__("params", {"bpm": 90})),
    ("params_cross_engine_key", "未知参数",
     lambda p: p["tracks"][0].__setitem__("params", {"gain": 0.5})),
    ("params_events_locked", "投影锁定字段",
     lambda p: p["tracks"][1].__setitem__("params", {"events": []})),
    ("bend_range_nonpositive", "bend_range：必须是正数",
     lambda p: p["tracks"][1].__setitem__("bend_range", 0)),
    ("event_unknown_type", "不在事件类型闭集",
     lambda p: p["tracks"][1].__setitem__("events", [
         {"type": "rpn", "t": 0}])),
    ("event_bad_cc_ctrl", "ctrl：必须是整数",
     lambda p: p["tracks"][1].__setitem__("events", [
         {"type": "cc", "t": 0, "ctrl": 1.5, "value": 10}])),
]


@pytest.mark.fast
@pytest.mark.parametrize("name,expect,mutate", NEG_CASES, ids=[c[0] for c in NEG_CASES])
def test_v2_event_param_validation_negative(name, expect, mutate):
    d = helpers.tmpdir("ev_neg")
    path = _v2(d, name, mutate)
    with pytest.raises(ProjectError) as ei:
        load_project(path)
    assert expect in str(ei.value), f"{name} 报错缺 {expect!r}：{ei.value}"
    assert "tracks[" in str(ei.value), f"{name} 报错缺字段路径：{ei.value}"


@pytest.mark.fast
def test_v2_events_only_track_default_clip_derivation():
    """events-only 轨缺省 clip = note_off 最大 beat + 4 拍尾巴（v1 "notes 末+4" 等价物）。"""
    d = helpers.tmpdir("ev_clip")
    proj = helpers.load_json(MINIMAL)
    proj["meta"]["spec_version"] = "2"
    tr = proj["tracks"][1]
    tr.pop("clip")
    tr["notes"] = []
    tr["events"] = [
        {"type": "note_on", "t": 0, "pitch": 60, "vel": 100},
        {"type": "note_off", "t": 2.5, "pitch": 60},
        {"type": "note_on", "t": 1, "pitch": 64, "vel": 90},
        {"type": "note_off", "t": 1.5, "pitch": 64},
        {"type": "cc", "t": 0, "ctrl": 7, "value": 100},
    ]
    path = os.path.join(d, "clip.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(proj, f, ensure_ascii=False)
    loaded = load_project(path)
    t = loaded["tracks"][1]
    assert t["clip"] == {"start_beat": 0.0, "end_beat": 6.5}
    assert t["clip_explicit"] is False
    # note 载体投影为 notes；控制事件入 payload.events
    plan = build_render_plan(loaded)
    spec = track_engine_spec(plan["tracks"][1], loaded, "/tmp/x.wav")
    assert spec["payload"]["notes"] == [[60.0, 0.0, 2.5, 100.0], [64.0, 1.0, 0.5, 90.0]]
    assert [e["type"] for e in spec["payload"]["events"]] == ["cc"]
    assert track_cc_curve(t, 7) == [(0.0, 100.0)]


@pytest.mark.fast
def test_v2_mixed_track_and_params_positive():
    """正例：混装轨（notes+cc+pitch_bend+keyswitch+PC）与 params.gain 合法；sfz 声明
    bend_range 后弯音合法。"""
    d = helpers.tmpdir("ev_pos")
    proj = helpers.load_json(MINIMAL)
    proj["meta"]["spec_version"] = "2"
    tr = proj["tracks"][1]                      # sf2（FluidR3_GM:0:48 速记）
    tr["events"] = [
        {"type": "cc", "t": 0, "ctrl": 7, "value": 100},
        {"type": "cc", "t": 1, "ctrl": 64, "value": 0},
        {"type": "pitch_bend", "t": 1.5, "semitones": 0.5},
    ]
    tr["params"] = {"gain": 0.5, "channel": 1}
    tr["bend_range"] = 12
    path = os.path.join(d, "pos.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(proj, f, ensure_ascii=False)
    loaded = load_project(path)
    t = loaded["tracks"][1]
    assert [e["type"] for e in t["events"]] == ["cc", "cc", "pitch_bend"]
    assert t["params"] == {"gain": 0.5, "channel": 1}
    assert t["bend_range"] == 12.0
    spec = track_engine_spec(build_render_plan(loaded)["tracks"][1], loaded, "/tmp/x.wav")
    assert spec["payload"]["gain"] == 0.5 and spec["payload"]["channel"] == 1
    assert spec["payload"]["bend_range"] == 12.0
    assert spec["payload"]["program"] == 48          # 速记 :0:48 仍生效
    # sfz 轨：弯音 + 显式 bend_range → 合法
    proj2 = helpers.load_json(MINIMAL)
    proj2["meta"]["spec_version"] = "2"
    proj2["tracks"][1]["engine"] = "sfz"
    proj2["tracks"][1]["instrument"] = _CELLO
    proj2["tracks"][1]["bend_range"] = 2
    proj2["tracks"][1]["events"] = [{"type": "pitch_bend", "t": 0, "semitones": 1.0},
                                    {"type": "keyswitch", "t": 0, "pitch": 36},
                                    {"type": "program_change", "t": 1, "program": 5}]
    path2 = os.path.join(d, "pos_sfz.json")
    with open(path2, "w", encoding="utf-8") as f:
        json.dump(proj2, f, ensure_ascii=False)
    loaded2 = load_project(path2)
    assert [e["type"] for e in loaded2["tracks"][1]["events"]] == \
        ["pitch_bend", "keyswitch", "program_change"]


# ------------------------------------------------------- ③ 渲染语义 [fast]
def _single_track_v2(d, name, track, meta=None):
    proj = {"name": name,
            "meta": {"spec_version": "2", "sr": 44100, "channels": 2, "bpm": 120.0,
                     "duration_hint": 4.0, **(meta or {})},
            "tracks": [track]}
    path = os.path.join(d, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(proj, f, ensure_ascii=False)
    return path


def _render_track_spec(proj_path, track_name, out):
    """load → plan → track_engine_spec → run_engine（v2 全链，免 mixer/master）。"""
    helpers.require_engine_assets("sfz")
    loaded = load_project(proj_path)
    plan = build_render_plan(loaded)
    t = next(x for x in plan["tracks"] if x["name"] == track_name)
    spec = track_engine_spec(t, loaded, out)
    return run_engine(spec)


@pytest.mark.fast
def test_events_only_track_renders_sound_via_sfz():
    """events-only 轨经 sfz 小样渲染出声（note 载体 → notes 合成 → SMF 全链）。"""
    d = helpers.tmpdir("ev_render_evonly")
    track = {"name": "evonly", "engine": "sfz", "instrument": _CELLO,
             "notes": [],
             "events": [{"type": "note_on", "t": 0, "pitch": 48, "vel": 100},
                        {"type": "note_off", "t": 0.9, "pitch": 48},
                        {"type": "cc", "t": 0, "ctrl": 7, "value": 120}]}
    r = _render_track_spec(_single_track_v2(d, "evonly", track), "evonly",
                           os.path.join(d, "evonly.wav"))
    assert r["duration_s"] > 0.5 and r["rms_db"] > -60


@pytest.mark.fast
def test_mixed_track_cc_does_not_disturb_note_bytes():
    """混装轨（notes+cc）与纯 notes 轨的 notes 部分逐位一致（cc 不干扰音符字节——
    同 tick 排序正确性；无 events 旧路径 vs events 新路径位级锁）。"""
    d = helpers.tmpdir("ev_render_mixed")
    notes = [[48, 0, 0.9, 100], [52, 1.9, 0.9, 100], [55, 3.8, 0.9, 100]]
    plain = {"name": "m", "engine": "sfz", "instrument": _CELLO, "notes": notes}
    mixed = {"name": "m", "engine": "sfz", "instrument": _CELLO, "notes": notes,
             "events": [                       # 无 opcode 的 cc（sfizz 忽略→字节不变）
                 {"type": "cc", "t": 0, "ctrl": 20, "value": 0},
                 {"type": "cc", "t": 1.9, "ctrl": 21, "value": 64.5},   # 与 note 同 tick
                 {"type": "cc", "t": 3.8, "ctrl": 22, "value": 100}]}
    r1 = _render_track_spec(_single_track_v2(d, "plain", plain), "m",
                            os.path.join(d, "plain.wav"))
    r2 = _render_track_spec(_single_track_v2(d, "mixed", mixed), "m",
                            os.path.join(d, "mixed.wav"))
    assert helpers.sha256(r1["path"]) == helpers.sha256(r2["path"]), \
        "混装轨 cc 干扰了音符字节（同 tick 排序/编码漂移）"


@pytest.mark.fast
def test_events_only_track_note_only_projects_no_events_key():
    """note-only 事件轨（无控制事件）：payload 不含 events 键（引擎走旧路径），
    notes 由合成——防止 note 载体经 events 通道重复编码（回归锁）。"""
    d = helpers.tmpdir("ev_noteonly")
    track = {"name": "no", "engine": "sfz", "instrument": _CELLO, "notes": [],
             "events": [{"type": "note_on", "t": 0, "pitch": 48, "vel": 100},
                        {"type": "note_off", "t": 0.9, "pitch": 48}]}
    loaded = load_project(_single_track_v2(d, "no", track))
    spec = track_engine_spec(build_render_plan(loaded)["tracks"][0], loaded,
                             "/tmp/x.wav")
    assert "events" not in spec["payload"]
    assert spec["payload"]["notes"] == [[48.0, 0.0, 0.9, 100.0]]


# ---------------------------------------------- ④ 重组装深比较锁 [fast]
def _reference_track_engine_spec(track, proj, stem_path):
    """旧 if/elif 参照实现——v3 R2 动工前 git 版本（f691a21）逐字冻结，
    产出期望 envelope；数据化后 track_engine_spec 输出必须逐字段相等。"""
    m = proj["meta"]
    payload = {"bpm": m["bpm"], "notes": track["notes"]}
    eng = track["engine"]
    if eng == "synth":
        payload["program"] = track["instrument"]
    elif eng == "sf2":
        sf2_name = track["instrument"]
        bank, program = track["bank"], track["program"]
        if sf2_name.count(":") == 2:               # "FluidR3_GM:0:48" 速记
            name_part, b_s, p_s = sf2_name.rsplit(":", 2)
            try:
                b_i, p_i = int(b_s), int(p_s)
            except ValueError:
                raise ProjectError(
                    f"轨 {track['name']}：instrument 速记 {sf2_name!r} 的 bank/program "
                    f"段不是整数") from None
            if 0 <= b_i <= 128 and 0 <= p_i <= 127:
                sf2_name = name_part
                bank = bank if track.get("bank_explicit") else b_i
                program = program if track.get("program_explicit") else p_i
        payload.update({"sf2": sf2_name, "bank": bank, "program": program})
    elif eng == "sfz":
        payload["sfz"] = track["instrument"]
    elif eng == "sample":
        payload["instrument"] = track["instrument"]
        payload["length_s"] = track["window_s"]
    else:                                          # load_project 已挡，双保险
        raise ProjectError(f"轨 {track['name']}：引擎 {eng} 不是工程轨引擎")
    return {"engine": eng, "spec_version": "1", "sr": m["sr"],
            "channels": m["channels"], "out": stem_path, "payload": payload}


def _assert_lock(proj, stem_path, tag):
    plan = build_render_plan(proj)
    for t in plan["tracks"]:
        want = _reference_track_engine_spec(t, proj, stem_path)
        got = track_engine_spec(t, proj, stem_path)
        assert got == want, (
            f"{tag}/轨 {t['name']}（{t['engine']}）重组装漂移：\n"
            f"  参照 {want}\n  数据化 {got}")


def _w2a_track_proj(spec):
    """baselines_w2a spec（run_engine 层）→ 合成 (track, proj)，使旧/新
    track_engine_spec 均可组装该 spec 的 payload。"""
    payload = spec["payload"]
    meta = {"sr": int(spec.get("sr", 44100)),
            "channels": int(spec.get("channels", 2)),
            "bpm": float(payload.get("bpm", 120.0))}
    eng = spec["engine"]
    if eng == "synth":
        instrument = payload.get("program")
    elif eng == "sf2":
        instrument = payload.get("sf2")
    elif eng == "sample":
        instrument = (payload.get("hits") or [{}])[0].get("file")
    else:
        instrument = payload.get("sfz")
    track = {"name": f"w2a_{eng}", "engine": eng,
             "instrument": instrument,
             "notes": [[70, 0, 0.1, 90]] if eng == "sample" else payload["notes"],
             "bank": 0, "program": 0,
             "bank_explicit": False, "program_explicit": False,
             "window_s": float(spec.get("duration_s", 4.0))}
    if eng == "sf2":
        if "bank" in payload:
            track["bank"], track["bank_explicit"] = payload["bank"], True
        if "program" in payload:
            track["program"], track["program_explicit"] = payload["program"], True
    if "params" in payload:                              # synth 轨：键=描述符参数 id "params"
        track["params"] = {"params": payload["params"]}
    return track, {"meta": meta}


@pytest.mark.fast
def test_reassembly_deep_comparison_lock():
    """重组装深比较锁（PLAN_v3 §3.1 v1 组装结果不变锁）：minimal + baselines_w2a
    全部 track 引擎 spec + out/eden_v2（只读，缺失 skip）——数据化前后逐字段相等。"""
    stem = "/tmp/r2_deeplock.wav"
    _assert_lock(load_project(MINIMAL), stem, "minimal")

    w2a = helpers.load_json(helpers.BASELINES_W2A)["baselines"]
    track_specs = [b for b in w2a if b["engine"] in ("synth", "sf2", "sfz", "sample")]
    assert len(track_specs) == 7
    for b in track_specs:
        helpers.require_fixture(os.path.join(helpers.WORKSPACE, b["spec"]))
        spec = helpers.load_json(os.path.join(helpers.WORKSPACE, b["spec"]))
        track, proj = _w2a_track_proj(spec)
        got = track_engine_spec(track, proj, stem)
        # 旧 if/elif 参照：v1 不可表达 params（v1 工程无此键），params 样本以
        # 冻结基线 spec payload 直接对拍（下方断言，更强）
        if "params" not in spec["payload"]:
            want = _reference_track_engine_spec(track, proj, stem)
            assert got == want, f"w2a {b['engine']}.{b['case']} 重组装漂移"
        # sample 基线 spec 为 hits 入口（v1 工程轨不可表达），组装形态为
        # instrument/notes 入口——仅 synth/sf2/sfz 断言 payload 逐字段相等
        if b["engine"] != "sample":
            assert got["payload"] == spec["payload"], (
                f"w2a {b['engine']}.{b['case']}：track_engine_spec 组装 ≠ 基线 spec payload\n"
                f"  组装 {got['payload']}\n  基线 {spec['payload']}")

    if not os.path.isfile(EDEN_V2):
        pytest.skip("out/eden_v2/project.json 缺失（主树合流后全量跑）")
    _assert_lock(load_project(EDEN_V2), stem, "eden_v2")


# ------------------------------------------------- [slow] 冒烟四件（-m slow -k events）
def _load_wav_mono(path):
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        n, ch = w.getnframes(), w.getnchannels()
        raw = np.frombuffer(w.readframes(n), dtype="<i2").reshape(-1, ch).astype(float) / 32768.0
    return sr, raw[:, 0]


def _rms_db(mono, sr, t0, t1):
    seg = mono[int(t0 * sr):int(t1 * sr)]
    if not len(seg):
        return -90.0
    return float(20.0 * np.log10(max(np.sqrt(np.mean(seg ** 2)), 1e-12)))


def _f0_hz(path, sr, pitch, nfft=65536):
    """基频测量（确定性）：4 窗窄带 argmax 取中位数。窄带=名义音高 0.7..1.4×
    （管乐基频弱、谐波强，全带 argmax 会误锁谐波——法号实测教训）；多窗中位数
    平抑 sustain 采样 vibrato 的窗内相位漂移（单窗实测偏差可达 ±0.2 半音）。"""
    _, mono = _load_wav_mono(path)
    nominal = 440.0 * 2.0 ** ((pitch - 69) / 12.0)
    freqs = np.fft.rfftfreq(nfft, 1.0 / sr)
    lo, hi = int(nominal * 0.7 / sr * nfft), int(nominal * 1.4 / sr * nfft)
    peaks = []
    for t0, t1 in ((0.7, 0.9), (0.85, 1.05), (1.0, 1.2), (1.15, 1.35)):
        n = int((t1 - t0) * sr)
        seg = mono[int(t0 * sr):int(t0 * sr) + n] * np.hanning(n)
        spec = np.abs(np.fft.rfft(seg, nfft))
        peaks.append(freqs[lo + int(np.argmax(spec[lo:hi]))])
    return float(np.median(peaks))


def _sfz_render(out, sfz, notes, events=None, bend_range=None):
    payload = {"sfz": sfz, "bpm": 120.0, "notes": notes}
    if events:
        payload["events"] = events
    if bend_range is not None:
        payload["bend_range"] = float(bend_range)
    return run_engine({"engine": "sfz", "spec_version": "1", "sr": 44100,
                       "out": out, "payload": payload})


@pytest.mark.slow
def test_slow_cc1_crescendo_vpo_strings():
    """①VPO 弦乐 + CC1 渐强事件链：RMS 分段单调上升（PERF-staccato：set_cc1=64
    默认 + gain_cc1=34——CC1 上推=渐强；sustain 文件无 CC1 opcode 的实测注记见
    报告，行为探针取 CC1 语义文件）。"""
    pf = "VPO/Strings/1st-violin-SEC-PERF-staccato.sfz"
    levels = [0, 32, 64, 96, 127]
    events, notes = [], []
    for i, lv in enumerate(levels):
        t0 = i * 2.0                       # beat；120bpm → 每段 1s
        events.append({"type": "cc", "t": t0, "ctrl": 1, "value": lv})
        notes += [[67, t0 + k * 0.5, 0.2, 100] for k in range(4)]
    r = _sfz_render(os.path.join(helpers.tmpdir("ev_slow"), "cc1.wav"),
                    pf, notes, events=events)
    sr, mono = _load_wav_mono(r["path"])
    rms = [_rms_db(mono, sr, i + 0.15, i + 0.85) for i in range(5)]
    assert all(rms[k] > rms[k - 1] for k in range(1, 5)), \
        f"CC1 渐强 RMS 未单调上升：{rms}"
    assert rms[-1] - rms[0] > 20.0, f"CC1 渐强动态不足：{rms}"


@pytest.mark.slow
def test_slow_pitch_bend_microtonal_and_range_evidence():
    """②弯音微分音：sfz 轨 pitch_bend 事件频移可测（全量弯音→实际弯音量程）；
    实测 VPO 目标文件 pitch_wheel_range → examples/fate/bend_range_evidence.json
    （R4 mapping 消费；确定性渲染+确定性 FFT 测量 → 文件字节稳定）。"""
    d = helpers.tmpdir("ev_slow_bend")
    targets = [("VPO/Strings/1st-violin-SEC-sustain.sfz", 69),
               ("VPO/Woodwinds/flute-SEC-sustain.sfz", 74),
               ("VPO/Brass/french-horn-SEC-sustain.sfz", 62)]
    files = []
    for sfz, pitch in targets:
        plain = _sfz_render(os.path.join(d, f"p_{pitch}.wav"), sfz,
                            [[pitch, 0, 1.5, 100]])
        bent = _sfz_render(os.path.join(d, f"b_{pitch}.wav"), sfz,
                           [[pitch, 0, 1.5, 100]],
                           events=[{"type": "pitch_bend", "t": 0, "semitones": 2.0}],
                           bend_range=2.0)
        f0p = _f0_hz(plain["path"], 44100, pitch)
        f0b = _f0_hz(bent["path"], 44100, pitch)
        shift = float(12.0 * np.log2(f0b / f0p))
        assert 0.3 < shift < 24.0, f"{sfz} 弯音频移异常：{shift}"
        files.append({"sfz": sfz, "probe_pitch": pitch,
                      "bend_range_declared_st": 2.0, "full_bend_14bit": 16383,
                      "f0_plain_hz": round(f0p, 2), "f0_bent_hz": round(f0b, 2),
                      "measured_shift_semitones": round(shift, 3),
                      "pitch_wheel_range_st": round(shift, 3)})
    # 主目标文件（命运弦乐主力）断言：实测量程=SFZ 默认 2 半音
    main = files[0]
    assert abs(main["measured_shift_semitones"] - 2.0) < 0.3, \
        f"VPO sustain 弯音量程实测 {main['measured_shift_semitones']} ≠ 2"
    evidence = {
        "generated": "2026-08-23",
        "worker": "R2 事件流（bgm_gen/tests/test_events.py slow ②）",
        "purpose": "VPO 目标文件实际弯音量程（pitch_wheel_range）实测证据——R4 命运 "
                   "mapping 弯音轨 bend_range 声明消费；无效则回填默认 2 并记录",
        "method": "sfizz 确定性渲染：同 note 无弯音 vs 全量弯音（pitch_bend 14bit=16383，"
                  "bend_range 声明 2.0 半音）→ numpy FFT 基频差 → 实测半音移=文件量程",
        "engine_binary": "sfizz-1.2.3-4.fc44（descriptors/sfz.json v1.1.0）",
        "conclusion": "VPO 目标文件均未声明 pitch_wheel_range → 实测=SFZ 默认 2 半音；"
                      "命运 mapping 弯音轨须显式声明 bend_range（校验强制）",
        "files": files,
    }
    os.makedirs(os.path.dirname(BEND_EVIDENCE), exist_ok=True)
    with open(BEND_EVIDENCE, "w", encoding="utf-8") as f:
        json.dump(evidence, f, ensure_ascii=False, indent=2)
        f.write("\n")


@pytest.mark.slow
def test_slow_keyswitch_sw_last_vpo():
    """③keyswitch 探针：VPO KS 文件（sw_last 语义）keyswitch 事件切换演奏法有效
    （sustain 有释放尾巴、staccato 一击即止——尾部 RMS 可测）；异常如实记录触发
    回退策略（PLAN_v3 §4.2）。"""
    d = helpers.tmpdir("ev_slow_ks")
    ks = "VPO/Strings/1st-violin-SEC-KS-C2.sfz"
    sus = _sfz_render(os.path.join(d, "ks_sus.wav"), ks, [[67, 1.0, 2.0, 100]],
                      events=[{"type": "keyswitch", "t": 0, "pitch": 36}])   # c2=sustain
    stac = _sfz_render(os.path.join(d, "ks_stac.wav"), ks, [[67, 1.0, 2.0, 100]],
                       events=[{"type": "keyswitch", "t": 0, "pitch": 40}])  # e2=staccato
    sr, m_sus = _load_wav_mono(sus["path"])
    _, m_stac = _load_wav_mono(stac["path"])
    onset_sus = _rms_db(m_sus, sr, 0.6, 1.0)
    onset_stac = _rms_db(m_stac, sr, 0.6, 1.0)
    tail_sus = _rms_db(m_sus, sr, 1.6, 2.0)
    tail_stac = _rms_db(m_stac, sr, 1.6, 2.0)
    assert onset_sus > -50 and onset_stac > -50, \
        f"KS 切换后两轨均未出声：{onset_sus}/{onset_stac}"
    assert tail_sus - tail_stac > 12.0, (
        f"KS 切换无效（sustain/staccato 尾部能量差 {tail_sus - tail_stac:.1f}dB ≤ 12dB；"
        f"tail_sus={tail_sus} tail_stac={tail_stac}）——若复现失败，触发回退策略："
        f"双轨叠置（fate_motif 已验证手法，PLAN_v3 §4.2）")


@pytest.mark.slow
def test_slow_sf2_params_gain_effective():
    """④sf2 轨 params.gain 生效（§3.6 钩子④）：v2 工程 params={"gain":0.5} 渲染
    RMS 低于 gain=1.0（≈6dB）；混入 no-op cc 事件同时覆盖 sf2 events 分支。"""
    d = helpers.tmpdir("ev_slow_gain")
    notes = [[48, 0, 0.9, 100], [52, 1.9, 0.9, 100], [55, 3.8, 0.9, 100]]
    track = {"name": "g", "engine": "sf2", "instrument": "FluidR3_GM:0:48",
             "bank": 0, "program": 48, "notes": notes,
             "events": [{"type": "cc", "t": 0, "ctrl": 20, "value": 0}]}
    p05 = _single_track_v2(d, "g05", dict(track, params={"gain": 0.5}))
    p10 = _single_track_v2(d, "g10", dict(track, params={"gain": 1.0}))
    r05 = _render_track_spec(p05, "g", os.path.join(d, "g05.wav"))
    r10 = _render_track_spec(p10, "g", os.path.join(d, "g10.wav"))
    diff = r10["rms_db"] - r05["rms_db"]
    assert 4.0 < diff < 8.0, f"params.gain 未生效（RMS 差 {diff:.2f}dB ∉ 4..8）"
