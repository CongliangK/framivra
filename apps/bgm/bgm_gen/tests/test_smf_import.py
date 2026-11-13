"""SMF 读器测试（PLAN_v3 §6 test_smf_import 行）。

[fast] Mutopia Symphony5_1 实测锚定（8295 音符/1013 拍/192 qBPM/384 PPQN/13 轨）
        + running status 合成用例 + 双 tempo 并列取先合成用例 + dur=0 钳制
        + 未配对 note_off / 悬空 note_on 告警 + 音域/PC 证据。
合成 SMF 由测试内字节级构造（纯标准库，不依赖外部库）。
"""

import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers                                              # noqa: E402

from bgm_gen.core.smf_import import (SmfImportError, dominant_tempo,   # noqa: E402
                                     import_smf, parse_track)

pytestmark = pytest.mark.fast

FATE_MID = os.path.join(helpers.EXAMPLES_DIR, "fate", "Symphony5_1.mid")


# ------------------------------------------------------------ 合成 SMF 工具

def _vlq(n):
    """整数 → SMF VLQ 字节（delta-time 编码）。"""
    if n == 0:
        return b"\x00"
    out = bytearray()
    while n:
        out.insert(0, n & 0x7F)
        n >>= 7
    for i in range(len(out) - 1):
        out[i] |= 0x80
    return bytes(out)


def _mtrk(events, end=True):
    """事件列 [(delta, status, data...), ...] → MTrk 块（含 end_of_track）。"""
    body = bytearray()
    for delta, status, data in events:
        body += _vlq(delta) + bytes([status]) + bytes(data)
    if end:
        body += b"\x00\xff\x2f\x00"
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def _smf(fmt, ppq, tracks):
    blob = b"MThd" + struct.pack(">IHHH", 6, fmt, len(tracks), ppq)
    for t in tracks:
        blob += t
    return blob


def _write_smf(path, blob):
    with open(path, "wb") as f:
        f.write(blob)
    return path


# ------------------------------------------------------------ Mutopia 锚定

def test_mutopia_anchors():
    """R1 审查实测锚点（PLAN_v3 §4.1）：format1/13 轨/384PPQN/8295 音符/
    1013 四分拍/恒 192 qBPM；轨 0 = meta 轨（tempo 在此）。"""
    if not os.path.exists(FATE_MID):
        pytest.skip("examples/fate/Symphony5_1.mid 缺失（新机需先入仓）")
    d = import_smf(FATE_MID, log=False)
    assert d["meta"]["format"] == 1
    assert d["meta"]["ntracks"] == 13
    assert d["meta"]["ppq"] == 384
    assert d["meta"]["bpm"] == 192.0
    assert sum(len(t["notes"]) for t in d["tracks"]) == 8295
    # 1013 四分拍 = max(on+dur)（谱面总长）
    max_beat = max(max((n[1] + n[2] for n in t["notes"]), default=0.0)
                   for t in d["tracks"])
    assert round(max_beat, 3) == 1013.0
    # 轨 0 无音符（meta 轨）；12 乐器轨名非语义（'one:'...'twelve:'）
    assert d["tracks"][0]["notes"] == []
    assert [t["name"] for t in d["tracks"][1:]] == \
        ["one:", "two:", "three:", "four:", "five:", "six:", "seven:",
         "eight:", "nine:", "ten:", "eleven:", "twelve:"]
    # PC 证据：GM 解码正确轨（73=Flute..43=Contrabass）+ 圆号/小号失真=69
    pcs = [t["program"] for t in d["tracks"][1:]]
    assert pcs == [73, 68, 71, 70, 69, 69, 47, 40, 40, 41, 42, 43]
    # 定音鼓（轨 7）音域 43..48；vel 恒 90
    timp = d["tracks"][7]
    assert (timp["lo"], timp["hi"]) == (43, 48)
    assert {n[3] for t in d["tracks"][1:] for n in t["notes"]} == {90.0}
    # 音域证据字段
    vc = d["tracks"][11]
    assert (vc["lo"], vc["hi"]) == (36, 69)


def test_notes_beat_quarter_precision():
    """beat/dur = tick/ppq 四分音符浮点；Mutopia 384 PPQN 下 1/4 拍网格全精确。"""
    if not os.path.isfile(FATE_MID):
        pytest.skip("examples/fate/Symphony5_1.mid 已迁 music_project（2026-09-09 分家）")
    d = import_smf(FATE_MID, log=False)
    for t in d["tracks"]:
        for p, b, dur, v in t["notes"]:
            assert abs(b * 4 - round(b * 4)) < 1e-9   # 1/4 拍网格
            assert abs(dur * 4 - round(dur * 4)) < 1e-9


# ------------------------------------------------------------ 合成用例

def test_running_status(tmp_path):
    """running status：连续同 status 事件省略状态字节。"""
    # 轨 0（meta/tempo 120BPM=500000us）+ 轨 1：三音 on/off 全走 running
    meta = _mtrk([(0, 0xFF, [0x51, 3, 0x07, 0xA1, 0x20])])
    tr = bytearray()
    tr += _vlq(0) + bytes([0x90, 60, 100])                    # 显式 note on
    tr += _vlq(96) + bytes([62, 100])                         # running（0x90）
    tr += _vlq(0) + bytes([64, 100])                          # running
    tr += _vlq(96) + bytes([0x80, 60, 0])                     # 显式 note off
    tr += _vlq(0) + bytes([62, 0]) + _vlq(0) + bytes([64, 0])  # running off
    tr += b"\x00\xff\x2f\x00"
    body = bytes(tr)
    path = _write_smf(str(tmp_path / "rs.mid"),
                      _smf(1, 384, [meta,
                                    b"MTrk" + struct.pack(">I", len(body))
                                    + body]))
    d = import_smf(path, log=False)
    assert d["meta"]["bpm"] == 120.0
    notes = d["tracks"][1]["notes"]
    assert notes == [[60.0, 0.0, 0.5, 100.0],     # off@192：dur=192 tick
                     [62.0, 0.25, 0.25, 100.0],
                     [64.0, 0.25, 0.25, 100.0]]


def test_dual_tempo_tie_takes_first(tmp_path):
    """双 tempo 覆盖 tick 时长并列 → 取时间序在前（PLAN_v3 §4.2 ①）。"""
    # tempo A@0 覆盖 [0,384)、tempo B@384 覆盖 [384,768)——等长并列 → A 胜
    tr = _mtrk([
        (0, 0xFF, [0x51, 3, 0x07, 0xA1, 0x20]),    # 500000us = 120BPM
        (384, 0xFF, [0x51, 3, 0x0F, 0x42, 0x40]),   # 1000000us = 60BPM
        (0, 0x90, [60, 64]),
        (0, 0x80, [60, 0]),
    ])
    path = _write_smf(str(tmp_path / "tie.mid"), _smf(0, 384, [tr]))
    d = import_smf(path, log=False)
    assert d["meta"]["bpm"] == 120.0        # 并列取先：120（先出现者）


def test_dominant_tempo_longest_span():
    """主导 tempo = 覆盖 tick 最长者（非并列场景）。"""
    assert dominant_tempo([(0, 500000), (100, 1000000)], 500) == 1000000
    assert dominant_tempo([(0, 500000), (400, 1000000)], 500) == 500000
    assert dominant_tempo([], 100) is None


def test_dur_zero_clamped(tmp_path):
    """同 tick on/off（dur=0）→ 钳制为 1 tick。"""
    tr = _mtrk([
        (0, 0xFF, [0x51, 3, 0x07, 0xA1, 0x20]),
        (96, 0x90, [60, 100]),
        (0, 0x80, [60, 0]),                 # 同 tick off → dur=1 tick
    ])
    path = _write_smf(str(tmp_path / "dur0.mid"), _smf(0, 384, [tr]))
    d = import_smf(path, log=False)
    assert d["tracks"][0]["notes"] == [[60.0, 0.25, round(1 / 384, 6), 100.0]]


def test_unpaired_off_and_dangling_on_warnings(tmp_path):
    """未配对 note_off（忽略+告警）与悬空 note_on（忽略+告警）。"""
    tr = _mtrk([
        (0, 0xFF, [0x51, 3, 0x07, 0xA1, 0x20]),
        (0, 0x80, [60, 0]),                 # 未配对 off
        (96, 0x90, [62, 90]),               # 悬空 on（无 off）
        (192, 0x90, [64, 90]),              # 正常配对
        (96, 0x80, [64, 0]),
    ])
    path = _write_smf(str(tmp_path / "warn.mid"), _smf(0, 384, [tr]))
    d = import_smf(path, log=False)
    t = d["tracks"][0]
    assert t["notes"] == [[64.0, 0.75, 0.25, 90.0]]   # 只留配对产物
    # parse_track 返回值尾位 = warnings（import_smf 只打 stderr 不返回）
    result = parse_track(tr[8:])
    warns = result[-1]
    assert any("未配对 note_off" in w for w in warns)
    assert any("悬空 note_on" in w for w in warns)


def test_overlapping_same_pitch_note_on_dropped():
    """同 (ch,pitch) note_on 交叠（前一 on 未 off）→ 丢弃新 on + 告警。"""
    tr = _mtrk([
        (0, 0xFF, [0x51, 3, 0x07, 0xA1, 0x20]),
        (0, 0x90, [60, 100]),
        (96, 0x90, [60, 110]),              # 交叠 on
        (96, 0x80, [60, 0]),
    ])
    result = parse_track(tr[8:])
    tempo, name, prog, lo, hi, notes, warns = result
    assert notes == [(0, 60, 100, 192)]     # 交叠 on 被丢弃，原 on@0 配对 off@192
    assert any("交叠" in w for w in warns)


def test_multi_program_change_takes_first():
    """轨内多 program_change → 取首个，其余计数告警（零观点不解释）。"""
    tr = _mtrk([
        (0, 0xFF, [0x51, 3, 0x07, 0xA1, 0x20]),
        (0, 0xC0, [73]),
        (96, 0xC0, [74]),
        (96, 0xC0, [75]),
    ])
    result = parse_track(tr[8:])
    assert result[2] == 73
    assert any("3 个 program_change" in w for w in result[-1])


def test_bad_smf_rejected(tmp_path):
    """坏头（非 MThd）/SMPTE division/format 2 → SmfImportError。"""
    for blob in (b"XXXX" + b"\x00" * 20,
                 b"MThd" + struct.pack(">IHHH", 6, 0, 1, 0xE728),   # SMPTE
                 b"MThd" + struct.pack(">IHHH", 6, 2, 1, 384)):     # format 2
        path = _write_smf(str(tmp_path / "bad.mid"), blob)
        with pytest.raises(SmfImportError):
            import_smf(path, log=False)


def test_format0_and_multichannel(tmp_path):
    """format 0 支持；同轨多通道 (ch,pitch) 配对不串音。"""
    tr = _mtrk([
        (0, 0xFF, [0x51, 3, 0x07, 0xA1, 0x20]),
        (0, 0x90, [60, 100]),               # ch0 C4
        (0, 0x91, [60, 90]),                # ch1 C4（同 pitch 不同通道）
        (96, 0x80, [60, 0]),                # ch0 off
        (96, 0x81, [60, 0]),                # ch1 off
    ])
    path = _write_smf(str(tmp_path / "fmt0.mid"), _smf(0, 384, [tr]))
    d = import_smf(path, log=False)
    assert d["meta"]["format"] == 0
    assert sorted(d["tracks"][0]["notes"]) == \
        [[60.0, 0.0, 0.25, 100.0],   # ch0：off@96
         [60.0, 0.0, 0.5, 90.0]]    # ch1：off@192（同 pitch 不同通道不串音）
