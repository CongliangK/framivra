"""SMF（标准 MIDI 文件）写入器——自 gm_lofi.py 上移为库函数（PLAN.md BG-D4/BG-D7）。

约定（BG-D7 工程格式，与 project.json 记谱统一）：
    notes 统一入口 = [pitch(MIDI 0-127), beat(浮点), dur(浮点 beat), vel(0-127)]
    内部换算 tick = round(beat * PPQ)，PPQ = 480 固定（Division 高 15 位=PPQ）。

格式 format 1：第 0 轨固定为 tempo 轨（set_tempo + end_of_track），
演奏轨依次追加。delta-time 用 VLQ 编码（修复史：低位先行、高位补 0x80）。

写入器只负责字节层；时长/力度语义（一轨一音色、GM 打击位 ch9）由调用方编排。
本模块不是引擎（无 @register_engine），engines/__init__ 自动发现它时仅当普通库导入。
"""

import struct

PPQ = 480


def _clamp(v, lo, hi, what):
    if not lo <= v <= hi:
        raise ValueError(f"{what} 越界：{v}（合法 {lo}..{hi}）")
    return int(round(v))


class Trk:
    """一条 MIDI 演奏轨。ch=0..15；prog=GM program change（None=不写）。

    prog 事件在 tick 0 与其他事件一起按 tick 稳定排序（prog 先加入，同为 0 时在前）。
    """

    def __init__(self, ch=0, prog=None):
        self.ch = _clamp(ch, 0, 15, "ch")
        self.prog = None if prog is None else _clamp(prog, 0, 127, "prog")
        self.ev = []
        if self.prog is not None:
            self.ev.append((0, bytes([0xC0 | self.ch, self.prog])))

    def note(self, pitch, beat, dur, vel):
        """BG-D7 统一入口：pitch/beat/dur/vel。beat 单位=四分音符（浮点）。"""
        pitch = _clamp(pitch, 0, 127, "pitch")
        vel = _clamp(vel, 1, 127, "vel")
        t, d = int(round(beat * PPQ)), max(1, int(round(dur * PPQ)))
        self.ev += [(t, bytes([0x90 | self.ch, pitch, vel])),
                    (t + d, bytes([0x80 | self.ch, pitch, 0]))]
        return self

    def note_row(self, row):
        """接受 [pitch, beat, dur, vel] 列表形式（project.json notes 元素）。"""
        pitch, beat, dur, vel = row
        return self.note(pitch, beat, dur, vel)


def vlq(n):
    """delta-time 变长整数（低位组先行，最高组不带 0x80）。n>=0。"""
    if n < 0:
        raise ValueError(f"VLQ 不接受负数：{n}")
    out = [n & 0x7F]
    while n >> 7:
        n >>= 7
        out.append((n & 0x7F) | 0x80)
    return bytes(reversed(out))


def write_smf(path, tracks, bpm=120.0, ppq=PPQ):
    """写 format-1 SMF。tracks=[Trk,...]；bpm 写入第 0 轨 set_tempo。"""
    if not tracks:
        raise ValueError("write_smf: tracks 为空")
    uspq = int(round(60_000_000 / bpm))
    chunks = []
    for tr in tracks:
        body, last = b"", 0
        for t, e in sorted(tr.ev, key=lambda x: x[0]):
            if t < last:
                raise ValueError(f"write_smf: 负 delta（tick {t} < {last}）")
            body += vlq(t - last) + e
            last = t
        body += b"\x00\xff\x2f\x00"
        chunks.append(b"MTrk" + struct.pack(">I", len(body)) + body)
    tempo = b"\x00\xff\x51\x03" + uspq.to_bytes(3, "big") + b"\x00\xff\x2f\x00"
    chunks.insert(0, b"MTrk" + struct.pack(">I", len(tempo)) + tempo)
    head = b"MThd" + struct.pack(">IHHH", 6, 1, len(chunks), ppq)
    with open(path, "wb") as f:
        f.write(head + b"".join(chunks))


def write_notes_smf(path, notes, bpm=120.0, ch=0, prog=None):
    """便捷入口：notes=[[pitch,beat,dur,vel],...] → 单轨 SMF。返回 Trk（供调试）。"""
    tr = Trk(ch=ch, prog=prog)
    for row in notes:
        tr.note_row(row)
    write_smf(path, [tr], bpm=bpm)
    return tr


# ---------------------------------------------------------------- v3 R2 事件流
# 本段（含至文件尾）为 v3 R2 新增（PLAN_v3 §3.2 机制 B / BG-D14 草案）。
# 上方 write_smf / write_notes_smf / Trk / vlq 旧路径字节零改动（红线⑤/R3a-#10），
# 由 tests/test_events.py 的 .mid 直接 sha 锚点 + w2a/minimal 渲染锚点双重锁定。

import math                                                    # noqa: E402

EVENT_TYPES = ("cc", "pitch_bend", "keyswitch", "program_change",
               "note_on", "note_off")
NOTE_EVENT_TYPES = ("note_on", "note_off")
# 同 tick 规范全序的类型序（C3）：keyswitch→program_change(含 bank 前置)→cc→
# pitch_bend→note_off→note_on。
EVENT_TYPE_ORDER = {"keyswitch": 0, "program_change": 1, "cc": 2,
                    "pitch_bend": 3, "note_off": 4, "note_on": 5}


def round_half_up(x):
    """编码值舍入 = round-half-up（C3b，明示：64.5→65，防 banker's rounding）。"""
    return math.floor(x + 0.5)


def _tick(beat, ppq):
    """beat → tick（与 Trk.note 同 round 语义，保证 notes 投影与旧路径 tick 一致）。"""
    return int(round(float(beat) * ppq))


def _clamp_round(v, lo, hi):
    """数值 → clamp 后 round-half-up 的整数（cc/弯音编码值域）。"""
    return int(min(hi, max(lo, round_half_up(v))))


def _midi_pitch(v):
    """pitch/vel 字段 → 0..127 整数（越界 ValueError，与 Trk._clamp 同口径）。"""
    v = int(round(float(v)))
    if not 0 <= v <= 127:
        raise ValueError(f"MIDI 值越界：{v}（合法 0..127）")
    return v


def event_sort_key(ev, idx, ppq=PPQ):
    """同 tick 规范全序（C3）：(编码 tick, 类型序, pitch 升序, 插入序)。

    单一实现，编码器与 show 共用导入（禁两处实现）。主键=**编码后 tick**（R2 审查
    官 A·1 修复：曾用原始浮点 beat，相邻音符边界浮点不同但同 tick 时 note_on 被
    排在 note_off 前掐死音头）。note_off 投影事件（_on_t/_dur）按其真实编码 tick
    计（on_tick + max(1, round(dur*ppq))，与 _encode_event 同公式）。第三键：有
    pitch 的事件（keyswitch/note_on/note_off）用 pitch，无 pitch 用 0；第四键=
    原始插入序（稳定性）。idx 由调用方 enumerate 注入。
    """
    t = ev.get("t", 0.0)
    if ev["type"] == "note_off" and "_on_t" in ev:
        tick = _tick(ev["_on_t"], ppq) + max(1, round(ev["_dur"] * ppq))
    else:
        tick = _tick(t, ppq)
    return (tick, EVENT_TYPE_ORDER[ev["type"]], ev.get("pitch", 0), idx)


def notes_to_events(notes):
    """notes 四元组 → note_on/off 事件对（混装轨合并编码用）。

    note_off 携带内部键 _on_t（on 的原始 beat）与 _dur（仅编码器/排序键读：
    off tick = on_tick + max(1, round(dur*ppq))，与 Trk.note 旧路径逐字同源；
    R2 审查官 A·2 修复：不再以 (t+dur)-dur 重算 on tick，避免 ulp 边界漂移）。
    """
    out = []
    for pitch, beat, dur, vel in notes:
        out.append({"type": "note_on", "t": float(beat),
                    "pitch": float(pitch), "vel": float(vel)})
        out.append({"type": "note_off", "t": float(beat) + float(dur),
                    "pitch": float(pitch), "_on_t": float(beat),
                    "_dur": float(dur)})
    return out


def _encode_event(ev, ppq, ch, bend_range):
    """单个事件 → (tick, 事件字节)。keyswitch/program_change 可能产多字节事件组。"""
    typ = ev["type"]
    t = _tick(ev["t"], ppq)
    if typ == "cc":
        val = _clamp_round(ev.get("value", 0), 0, 127)
        ctrl = _midi_pitch(ev.get("ctrl", 0))    # 越界 raise（与 pitch 同口径，R2 审查 A·5）
        return [(t, bytes([0xB0 | ch, ctrl, val]))]
    if typ == "pitch_bend":
        semi = float(ev.get("semitones", 0.0))
        br = float(bend_range) if bend_range and float(bend_range) > 0 else 2.0
        v = _clamp_round(8192.0 * (1.0 + semi / br), 0, 16383)
        return [(t, bytes([0xE0 | ch, v & 0x7F, (v >> 7) & 0x7F]))]
    if typ == "keyswitch":
        pitch = _midi_pitch(ev.get("pitch"))
        # off tick = clamp(min(下一 KS tick-1, KS tick+0.25beat·ppq), ≥KS tick+1)。
        # 下一 KS tick 与同 tick 错开（+j）由 events_to_smf 预排（_ks_off/_ks_shift）。
        off = ev["_ks_off"] if "_ks_off" in ev else t + max(1, round(0.25 * ppq))
        shift = ev.get("_ks_shift", 0)
        return [(t + shift, bytes([0x90 | ch, pitch, 127])),
                (off + shift, bytes([0x80 | ch, pitch, 0]))]
    if typ == "program_change":
        prog = _midi_pitch(ev.get("program"))
        out = []
        bank = ev.get("bank")
        if bank is not None:                     # C3a：bank CC 是 PC 的组成部分，
            bank = _clamp_round(bank, 0, 128)    # 紧贴 0xC0 之前，不参与通用排序
            out.append((t, bytes([0xB0 | ch, 0, (bank >> 7) & 0x7F])))   # CC0 msb
            out.append((t, bytes([0xB0 | ch, 32, bank & 0x7F])))         # CC32 lsb
        out.append((t, bytes([0xC0 | ch, prog])))
        return out
    if typ == "note_on":
        pitch = _midi_pitch(ev.get("pitch"))
        vel = int(round(float(ev.get("vel", 0))))
        if not 1 <= vel <= 127:                  # vel=0 由校验层拒；此处与 Trk._clamp 同口径
            raise ValueError(f"note_on vel 越界：{vel}（合法 1..127）")
        return [(t, bytes([0x90 | ch, pitch, vel]))]
    if typ == "note_off":
        pitch = _midi_pitch(ev.get("pitch"))
        if "_dur" in ev:                         # notes 投影：Trk 同公式（旧路径字节一致）
            t = _tick(ev["_on_t"], ppq) + max(1, round(ev["_dur"] * ppq))
        return [(t, bytes([0x80 | ch, pitch, 0]))]
    raise ValueError(f"未知事件类型：{typ!r}（v2 闭集 EVENT_TYPES）")


def events_to_smf(path, events, bpm=120.0, ppq=PPQ, ch=0, prog=None,
                  bend_range=2.0):
    """事件数组 → SMF format 1（PLAN_v3 §3.2 编码规则）。

    第 0 轨 tempo meta（与 write_smf 同字节构造）+ 第 1 轨事件流（event_sort_key
    规范全序编码）。prog 非 None 时 0xC0 排 tick 0 最前（与 Trk 同惯例）。

    编码逐条（§3.2 类型表）：
        cc           0xB0（value round-half-up 后 clamp 0..127，C3b）
        pitch_bend   0xE0 14bit clamp(round_half_up(8192*(1+semitones/bend_range)), 0, 16383)
        keyswitch    note_on vel127 + note_off；off tick = clamp(min(下一 KS tick-1,
                     KS tick+0.25beat·ppq), ≥KS tick+1)（beat 0 合法）；相邻同 tick
                     双 KS：on/off/on/off 各 +1 tick 确定性错开（_ks_shift）
        program_change  带 bank = CC0/32 紧贴 0xC0 之前复合消息（C3a，不参与通用排序）
        note_on      vel ∈ 1..127（0 由校验层拒）；note_off vel=0
    """
    if not events:
        raise ValueError("events_to_smf: events 为空")
    events = [dict(e) for e in events]           # 浅拷贝：不污染调用方事件列表
    ch = _clamp(ch, 0, 15, "ch")
    order = sorted(enumerate(events),
                   key=lambda pair: event_sort_key(pair[1], pair[0], ppq))

    # keyswitch 预排：off tick 需知道"下一 KS tick"；相邻同 tick 双 KS +1 错开
    ks = [(i, e) for i, e in order if e["type"] == "keyswitch"]
    raw_ticks = [_tick(e["t"], ppq) for _, e in ks]
    for j, (i, e) in enumerate(ks):
        t = raw_ticks[j]
        quarter = max(1, round(0.25 * ppq))
        nxt = raw_ticks[j + 1] if j + 1 < len(ks) else None
        off = max(t + 1, min(t + quarter, nxt - 1 if nxt is not None else t + quarter))
        same_tick_before = sum(1 for k in range(j) if raw_ticks[k] == t)
        events[i]["_ks_off"] = off
        events[i]["_ks_shift"] = same_tick_before

    ev = []
    for i, e in order:
        ev += _encode_event(e, ppq, ch, bend_range)
    if prog is not None:
        ev.insert(0, (0, bytes([0xC0 | ch, _clamp(prog, 0, 127, "prog")])))
    ev.sort(key=lambda x: x[0])                  # 稳定：同 tick 保插入序（含 bank-PC 复合）
    body, last = b"", 0
    for t, e in ev:
        if t < last:
            raise ValueError(f"events_to_smf: 负 delta（tick {t} < {last}）")
        body += vlq(t - last) + e
        last = t
    body += b"\x00\xff\x2f\x00"
    uspq = int(round(60_000_000 / bpm))
    tempo = b"\x00\xff\x51\x03" + uspq.to_bytes(3, "big") + b"\x00\xff\x2f\x00"
    chunks = [b"MTrk" + struct.pack(">I", len(tempo)) + tempo,
              b"MTrk" + struct.pack(">I", len(body)) + body]
    head = b"MThd" + struct.pack(">IHHH", 6, 1, 2, ppq)
    with open(path, "wb") as f:
        f.write(head + b"".join(chunks))
    return path
