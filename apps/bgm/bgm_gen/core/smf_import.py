"""SMF（标准 MIDI 文件）format 0/1 读器——零观点（PLAN_v3 §4.2 ①）。

纯标准库字节解析；不做任何音乐解释（乐器识别/量化/表情一律留给下游）。
产出 = {meta: {bpm, ppq}, tracks: [{name, program, lo, hi, notes}]}，
notes = [pitch, beat, dur, vel] 四元组（beat/dur 单位=四分音符浮点，
由 tick/ppq 精确换算——Mutopia 384 PPQN 全整数可精确表示）。

零观点契约：
    - 主导 tempo = 覆盖 tick 时长最长者，并列取先（先=时间序在前；
      合成双 tempo 用例锁定 tests/test_smf_import.py）；
    - 轨内多 program_change 取首个值，其余仅计数（evidence 日志可见）；
    - note 配对键 = (channel, pitch)——同轨多通道不串音；
    - dur=0（同 tick on/off）钳制为 1 tick（beat=1/ppq）；
    - 未配对 note_off（与悬空 note_on）→ stderr 告警，不进 notes；
    - 逐轨证据日志（轨名/PC 值/音域/音符数）打 stderr，供 mapping 判定，
      零观点不解释。
"""

import struct
import sys

MIN_DUR_TICKS = 1                 # dur=0 钳制下限（tick 口径）


class SmfImportError(Exception):
    """SMF 解析失败（坏块头/坏 VLQ/事件数据不足等字节层问题）。"""


def _read_vlq(data, pos):
    """读 VLQ delta-time。返回 (value, new_pos)；越界抛 SmfImportError。"""
    value = 0
    for _ in range(4):                                   # VLQ 至多 4 字节
        if pos >= len(data):
            raise SmfImportError(f"VLQ 越界（pos={pos}，len={len(data)}）")
        b = data[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            return value, pos
    raise SmfImportError("VLQ 超 4 字节（非法 SMF）")


def _parse_header(blob):
    """MThd → (format, ntracks, ppq)。SMPTE timecode division 拒收。"""
    if len(blob) < 14 or blob[:4] != b"MThd":
        raise SmfImportError("非 SMF 文件（缺 MThd 头）")
    length, fmt, ntracks, division = struct.unpack(">IHHH", blob[4:14])
    if length != 6:
        raise SmfImportError(f"MThd length={length}（标准 SMF 恒 6）")
    if division & 0x8000:
        raise SmfImportError("SMPTE timecode division 不支持（仅 PPQ）")
    if division == 0:
        raise SmfImportError("division=0（PPQ 非法）")
    if fmt not in (0, 1):
        raise SmfImportError(f"format {fmt} 不支持（仅 0/1；format 2 非流式）")
    return fmt, ntracks, division


def _split_tracks(blob, ntracks):
    """顺序切出 ntracks 条 MTrk 数据体（字节层，不解析）。"""
    tracks, pos = [], 14
    for i in range(ntracks):
        if pos + 8 > len(blob) or blob[pos:pos + 4] != b"MTrk":
            raise SmfImportError(f"第 {i} 轨缺 MTrk 头（pos={pos}）")
        length = struct.unpack(">I", blob[pos + 4:pos + 8])[0]
        pos += 8
        if pos + length > len(blob):
            raise SmfImportError(f"第 {i} 轨 MTrk length={length} 越界")
        tracks.append(blob[pos:pos + length])
        pos += length
    return tracks


def parse_track(data):
    """单轨字节流 → (tempo_events, name, program, lo, hi, notes, warnings)。

    tempo_events = [(tick, uspq)]（本轨全部 set_tempo，时间序）；
    program = 首个 program_change 值（无则 None）；
    lo/hi = pitch 最小/最大（无音符则 None）；
    notes = [[pitch, beat, dur, vel], ...]（beat=tick/ppq；完成态配对产物，
    保 on 事件序）；warnings = 告警串列表（调用方决定去 stderr）。
    """
    tempo_events, warnings = [], []
    name, program = None, None
    notes, pending = [], {}                   # pending: (ch,pitch) -> [tick, vel, 序]
    tick, pos = 0, 0
    running = None                            # running status（0x80-0xEF）
    n_prog = 0
    order = 0
    while pos < len(data):
        delta, pos = _read_vlq(data, pos)
        tick += delta
        b = data[pos] if pos < len(data) else None
        if b is None:
            raise SmfImportError("事件字节不足（轨道尾截断）")
        if b & 0x80:                          # 显式 status
            pos += 1
            if b < 0xF0:
                running = b
            elif b in (0xF0, 0xF7):           # SysEx：length 前缀跳过
                _ln, pos = _read_vlq(data, pos)
                pos += _ln
                continue
            elif b == 0xFF:                   # meta
                mtype = data[pos] if pos < len(data) else None
                if mtype is None:
                    raise SmfImportError("meta 类型字节缺失")
                pos += 1
                mlen, pos = _read_vlq(data, pos)
                if pos + mlen > len(data):
                    raise SmfImportError("meta 数据越界")
                payload = data[pos:pos + mlen]
                pos += mlen
                if mtype == 0x51 and mlen == 3:            # set_tempo
                    tempo_events.append((tick,
                                         payload[0] << 16 | payload[1] << 8
                                         | payload[2]))
                elif mtype == 0x03 and name is None:       # 轨名（首个）
                    name = payload.decode("latin-1", "replace").strip("\x00")
                elif mtype == 0x2F:                        # end_of_track
                    break
                continue
            else:                             # 0xF1-0xF6/F8-FE：1-2 定长字节
                skip = 1 if b in (0xF1, 0xF3) else 0
                pos += skip
                continue
        else:                                  # running status 数据字节
            if running is None:
                raise SmfImportError(f"无 running status 的数据字节 0x{b:02x}")
            b = running
        if b is None or b >= 0xF0:
            continue                           # （不可达；防御）
        status, ch = b & 0xF0, b & 0x0F
        need = {0x80: 2, 0x90: 2, 0xA0: 2, 0xB0: 2, 0xE0: 2,
                0xC0: 1, 0xD0: 1}[status]
        if pos + need > len(data):
            raise SmfImportError("通道事件数据字节不足")
        args = data[pos:pos + need]
        pos += need
        if status == 0x90 and args[1] > 0:     # note on
            key = (ch, args[0])
            if key in pending:
                warnings.append(f"tick {tick}: ch{ch} pitch {args[0]} "
                                f"note_on 交叠（前一 on 未 off，丢弃新 on）")
            else:
                pending[key] = (tick, args[1], order)
                order += 1
        elif status == 0x80 or (status == 0x90 and args[1] == 0):   # note off
            key = (ch, args[0])
            hit = pending.pop(key, None)
            if hit is None:
                warnings.append(f"tick {tick}: ch{ch} pitch {args[0]} "
                                f"未配对 note_off（无对应 on，忽略）")
            else:
                on_tick, vel, _seq = hit
                dur = tick - on_tick
                if dur < MIN_DUR_TICKS:
                    dur = MIN_DUR_TICKS       # dur=0（同 tick on/off）钳制
                notes.append((on_tick, args[0], vel, dur))
        elif status == 0xC0:                   # program_change（零观点取首）
            if program is None:
                program = args[0]
            n_prog += 1
    for (ch, pitch), (on_tick, _vel, _seq) in sorted(pending.items()):
        warnings.append(f"tick {on_tick}: ch{ch} pitch {pitch} "
                        f"悬空 note_on（缺配对 off，忽略）")
    if n_prog > 1:
        warnings.append(f"轨内 {n_prog} 个 program_change（取首个，余仅计数）")
    pitches = [p for _, p, _, _ in notes]
    return (tempo_events, name, program,
            min(pitches) if pitches else None,
            max(pitches) if pitches else None,
            sorted(notes), warnings)


def dominant_tempo(all_tempos, total_ticks):
    """主导 tempo = 覆盖 tick 时长最长者，并列取先（时间序在前）。

    all_tempos = [(tick, uspq)] 全轨合并时间序；覆盖时长 = 下一 tempo tick
    （无则 total_ticks）− 本 tick。
    """
    if not all_tempos:
        return None
    best_tick, best_uspq, best_span = None, None, -1
    for i, (tick, uspq) in enumerate(all_tempos):
        end = all_tempos[i + 1][0] if i + 1 < len(all_tempos) else total_ticks
        span = end - tick
        if span > best_span:                   # 严格大于 = 并列取先
            best_tick, best_uspq, best_span = tick, uspq, span
    return best_uspq


def import_smf(path, log=True):
    """SMF 文件 → {meta: {bpm, ppq}, tracks: [...]}（PLAN_v3 §4.2 ①）。

    log=True 时逐轨证据日志打 stderr（轨名/PC/音域/音符数——供 mapping
    判定，零观点不解释）。bpm = 60e6/主导 uspq，四舍五入 0.1；无 tempo
    → bpm=None（调用方决定缺省）。
    """
    with open(path, "rb") as f:
        blob = f.read()
    fmt, ntracks, ppq = _parse_header(blob)
    track_blobs = _split_tracks(blob, ntracks)
    parsed = [parse_track(data) for data in track_blobs]
    all_tempos = sorted((t, uspq)
                        for tempo_events, *_ in parsed
                        for t, uspq in tempo_events)
    total_ticks = max((notes[-1][0] + notes[-1][3]
                       if notes else 0)
                      for _t, _n, _p, _lo, _hi, notes, _w in parsed)
    uspq = dominant_tempo(all_tempos, total_ticks)
    bpm = round(60_000_000.0 / uspq, 1) if uspq else None
    tracks = []
    for i, (tempo_events, name, program, lo, hi, notes, warnings) in \
            enumerate(parsed):
        rows = [[float(p), round(on_tick / ppq, 6),
                 round(dur / ppq, 6), float(vel)]
                for on_tick, p, vel, dur in notes]
        tracks.append({"name": name or f"track{i}", "program": program,
                       "lo": lo, "hi": hi, "notes": rows})
        if log:
            n_tempo = len(tempo_events)
            print(f"[smf_import] track {i}: name={name!r} pc={program} "
                  f"range={lo}..{hi} notes={len(rows)}"
                  + (f" tempo_events={n_tempo}" if n_tempo else ""),
                  file=sys.stderr)
            for w in warnings:
                print(f"[smf_import]   警告: {w}", file=sys.stderr)
    return {"meta": {"bpm": bpm, "ppq": ppq, "format": fmt,
                     "ntracks": ntracks},
            "tracks": tracks}
