"""MusicXML 读器测试（docs/PLAN_musicxml.md §四 三层验证 L1/L2/L3）。

[fast] L1 合成 .mxl（tmp zip 构造，纯标准库）逐语义钉死——divisions 变更/
        backup+forward/chord/grace/tie 邻接·链·跨 voice·回退·悬空/measure
        rest/pickup/非整数小节号/非连续 voice+channel 分配/tempo 双分支/
        dynamics 证据/repeat 透出/cue·unpitched/错误分层。
        L2 交叉源（版本级独立：openscore Breitkopf 1895 vs mutopia Peters）
        Voice-only 逐位（difflib SequenceMatcher，硬门 ≥0.95）+移调探针；
        钢琴仅音高集合弱验证。
        L3 roundtrip（per-voice channel 写出→import_smf 读回）音数全等
        硬断言+逐音 1 tick 容差。
真实库件（MUSIC_ASSETS_ROOT，skip-gated）：三件目标曲锚点（前奏 48/61/
43+2、调号拍号 tempo）+ Du bist die Ruh .mxl vs mutopia D776 MIDI。
"""

import difflib
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers                                              # noqa: E402

from bgm_gen.core.musicxml_import import (MusicXmlImportError,   # noqa: E402
                                          import_musicxml)

pytestmark = pytest.mark.fast

from bgm_gen.paths import assets_root

ASSETS = os.path.join(assets_root(), "scores")
STAENDCHEN = (f"{ASSETS}/openscore-lieder/scores/Schubert,_Franz/"
              "Schwanengesang,_D.957/4_Ständchen/lc5004835.mxl")
DUBIST = (f"{ASSETS}/openscore-lieder/scores/Schubert,_Franz/"
          "Op.59/3_Du_bist_die_Ruh/lc4919879.mxl")
MONDNACHT = (f"{ASSETS}/openscore-lieder/scores/Schumann,_Robert/"
             "Liederkreis,_Op.39/5_Mondnacht/lc4987640.mxl")
MUTOPIA_D776 = (f"{ASSETS}/mutopia-midi/ftp/SchubertF/D776/"
                "SchubertF-D776_DuBistDieRuh/SchubertF-D776_DuBistDieRuh.mid")

sys.path.insert(0, helpers.EXAMPLES_DIR)
import convert_openscore                                    # noqa: E402


# ------------------------------------------------------------ 合成 .mxl 工具

def _mxl(tmp_path, xml, root_name="score.xml", name="t"):
    """XML 字符串 → tmp .mxl（zip：container.xml + 根 xml）。"""
    p = tmp_path / f"{name}.mxl"
    cont = ('<?xml version="1.0" encoding="UTF-8"?><container><rootfiles>'
            f'<rootfile full-path="{root_name}" '
            'media-type="application/vnd.recordare.musicxml+xml"/>'
            '</rootfiles></container>')
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("META-INF/container.xml", cont)
        zf.writestr(root_name, xml)
    return str(p)


def _doc(parts, work_title="T"):
    """parts=[(pid, name, midi_program 或 None, measures_xml)] → score xml。

    measures_xml 须由 _ms() 构造的完整 <measure> 元素拼接。
    """
    pl = ""
    for pid, name, prog, _m in parts:
        mi = (f'<midi-instrument id="{pid}-I1"><midi-channel>1'
              f'</midi-channel><midi-program>{prog}</midi-program>'
              f'</midi-instrument>' if prog is not None else "")
        pl += (f'<score-part id="{pid}"><part-name>{name}</part-name>'
               f'{mi}</score-part>')
    body = "".join(f'<part id="{pid}">{m}</part>' for pid, _n, _p, m in parts)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<score-partwise version="4.0"><work><work-title>{work_title}'
            f'</work-title></work><identification>'
            '<creator type="composer">Test Composer</creator></identification>'
            f'<part-list>{pl}</part-list>{body}</score-partwise>')


def _ms(num, inner, implicit=False):
    """小节包裹（num 可为 X1 等非整数显示标签）。"""
    imp = ' implicit="yes"' if implicit else ""
    return f'<measure number="{num}"{imp}>{inner}</measure>'


def _attrs(div=4, beats=4, bt=4, fifths=0):
    return (f'<attributes><divisions>{div}</divisions><key><fifths>{fifths}'
            f'</fifths></key><time><beats>{beats}</beats><beat-type>{bt}'
            f'</beat-type></time></attributes>')


def _n(step, octave, dur, voice="1", alter=None, chord=False, grace=False,
       tie=None, rest=False, cue=False):
    """note XML 构造。dur 单位=divisions（grace 无 duration）。"""
    if rest:
        head = "<rest/>"
    else:
        alt = f"<alter>{alter}</alter>" if alter is not None else ""
        head = f"<pitch><step>{step}</step>{alt}<octave>{octave}</octave></pitch>"
    ties = "".join(f'<tie type="{t}"/>' for t in (tie or []))
    body = ""
    if grace:
        body += "<grace/>"
    if cue:
        body += "<cue/>"
    if chord:
        body += "<chord/>"
    body += head
    if not grace:
        body += f"<duration>{dur}</duration>"
    body += f"<voice>{voice}</voice>{ties}"
    return f"<note>{body}</note>"


def _simple_part(measures_xml, pid="P1", name="Piano", prog=1):
    return _doc([(pid, name, prog, measures_xml)])


# ------------------------------------------------------------ L1 合成用例

def test_container_rootfile_resolution(tmp_path):
    """根 xml 名实读 container.xml（sqNNN.xml 场景）；坏容器/坏 zip 拒收。"""
    xml = _simple_part(_ms(1, _attrs() + _n("C", 4, 16)))
    d = import_musicxml(_mxl(tmp_path, xml, root_name="sq9999999.xml",
                             name="a"), log=False)
    assert d["parts"][0]["notes"] == [[60.0, 0.0, 4.0, 64.0]]
    bad = tmp_path / "bad.mxl"
    bad.write_bytes(b"not a zip")
    with pytest.raises(MusicXmlImportError):
        import_musicxml(str(bad), log=False)
    no_cont = tmp_path / "nocont.zip"
    with zipfile.ZipFile(no_cont, "w") as zf:
        zf.writestr("score.xml", xml)
    with pytest.raises(MusicXmlImportError):
        import_musicxml(str(no_cont), log=False)


def test_pitch_math_and_program(tmp_path):
    """音高换算（含双升/降）+ midi-program 1-based→0-based + meta 基础。"""
    xml = _doc([("P1", "Voice", 69,
                 _ms(1, _attrs() + _n("B", 3, 4, alter=-1)
                     + _n("C", 4, 4, alter=2) + _n("G", 9, 8)))])
    d = import_musicxml(_mxl(tmp_path, xml), log=False)
    assert d["meta"]["composer"] == "Test Composer"
    assert d["meta"]["title"] == "T"
    assert d["parts"][0]["program"] == 68          # 69-1
    assert [r[0] for r in d["parts"][0]["notes"]] == [58.0, 62.0, 127.0]


def test_divisions_measure_boundary_change(tmp_path):
    """divisions 边界变更（L1 唯一覆盖——全库 0 件真变更）：quarter 域累计。"""
    m1 = _ms(1, _attrs(div=4, beats=4) + _n("C", 4, 16))      # 整小节 4q
    m2 = _ms(2, '<attributes><divisions>8</divisions></attributes>'
             + _n("D", 4, 8, voice="1") + _n("E", 4, 8, voice="2")
             + '<backup><duration>16</duration></backup>' + _n("D", 3, 8, voice="5"))
    d = import_musicxml(_mxl(tmp_path, _simple_part(m1 + m2)), log=False)
    notes = d["parts"][0]["notes"]
    assert [n[:3] for n in notes] == [
        [60.0, 0.0, 4.0],        # m1 C4 整小节
        [50.0, 4.0, 1.0],        # backup 后 voice5 D3@4.0（(beat,pitch) 序在前）
        [62.0, 4.0, 1.0],        # m2 D4（div=8：8/8=1q）
        [64.0, 5.0, 1.0],        # m2 E4
    ]
    assert d["meta"]["n_measures"] == 2


def test_backup_chord_grace_semantics(tmp_path):
    """backup 回退/chord 共 onset 不推进/grace dur=0 不推进且 onset=cursor。"""
    m1 = _ms(1, _attrs(div=4, beats=4)
             + _n("C", 5, 4, voice="1") + _n("E", 5, 4, voice="1", chord=True)
             + _n("D", 5, 4, voice="1")
             + _n("G", 4, 4, voice="1", grace=True)  # grace 不推进
             + _n("E", 5, 4, voice="1")
             + '<backup><duration>8</duration></backup>'
             + _n("C", 3, 8, voice="5"))
    d = import_musicxml(_mxl(tmp_path, _simple_part(m1)), log=False)
    p = d["parts"][0]
    by_key = {(n[0], n[1]): n[2] for n in p["notes"]}
    assert by_key[(72.0, 0.0)] == 1.0             # C5 头音
    assert by_key[(76.0, 0.0)] == 1.0             # 和弦 E5 同 onset
    assert by_key[(74.0, 1.0)] == 1.0             # D5
    assert by_key[(76.0, 2.0)] == 1.0             # grace 后正式音不位移
    assert by_key[(48.0, 1.0)] == 2.0             # backup(8) 后 voice5 C3@1q
    grace = [n for n in p["notes"] if n[2] == 0.0]
    assert len(grace) == 1 and grace[0][0] == 67.0 and grace[0][1] == 2.0
    assert p["n_grace"] == 1


def test_tie_adjacent_chain_and_cross_voice(tmp_path):
    """邻接合并+三段链+跨 voice 邻接（Mondnacht 琶音持续音形态）。"""
    m1 = _ms(1, _attrs(div=2, beats=2) + _n("C", 4, 4, voice="1", tie=["start"])
             + '<backup><duration>4</duration></backup>'
             + _n("G", 3, 4, voice="6", tie=["start"]))
    m2 = _ms(2, _n("G", 3, 2, voice="5", tie=["stop"])
             + '<backup><duration>2</duration></backup>'
             + _n("C", 4, 4, voice="1", tie=["stop", "start"]))
    m3 = _ms(3, _n("C", 4, 4, voice="1", tie=["stop"]))
    d = import_musicxml(_mxl(tmp_path, _simple_part(m1 + m2 + m3)), log=False)
    notes = {(n[0], n[1]): n[2] for n in d["parts"][0]["notes"]}
    assert notes[(60.0, 0.0)] == 6.0                # 链三段：3×2q
    assert notes[(55.0, 0.0)] == 3.0                # 跨 voice 邻接：2q+1q
    assert len(d["parts"][0]["notes"]) == 2


def test_tie_fallback_window_and_dangling(tmp_path, capsys):
    """非邻接窗内回退（+warn）；窗外悬空双保留（start/stop 各自独立+warn）。"""
    # C4 start@0 dur1q；stop@4.0（gap 3q ≤ 窗=4/4 名义 4q）→ 回退合并
    m1 = _ms(1, _attrs(div=2, beats=4) + _n("C", 4, 2, voice="1", tie=["start"])
             + '<backup><duration>2</duration></backup>' + _n("D", 4, 8, voice="2"))
    # E4 窗外悬空对：start@5q end6q，stop@12q（gap 6q > 窗 4q）
    m2 = _ms(2, _n("C", 4, 2, voice="1", tie=["stop"])
             + _n("E", 4, 2, voice="1", tie=["start"])
             + _n("F", 4, 4, voice="1"))
    m3 = _ms(3, _n("E", 4, 2, voice="1") + _n("F", 4, 6, voice="1"))
    m4 = _ms(4, _n("E", 4, 2, voice="1", tie=["stop"])
             + _n("F", 4, 6, voice="1"))
    d = import_musicxml(_mxl(tmp_path, _simple_part(m1 + m2 + m3 + m4)),
                        log=True)
    notes = {(n[0], n[1]): n[2] for n in d["parts"][0]["notes"]}
    assert notes[(60.0, 0.0)] == 2.0                # gap 3q 窗内回退合并 1+1
    assert notes[(64.0, 5.0)] == 1.0                # 悬空 start 独立保留
    assert notes[(64.0, 12.0)] == 1.0               # 悬空 stop 独立保留
    err = capsys.readouterr().err
    assert "非邻接配对" in err and "悬空" in err


def test_grace_tie_exempt(tmp_path):
    """grace 带 tie 一律忽略（Ghost Road dur 膨胀教训）。"""
    m1 = _ms(1, _attrs() + _n("C", 4, 4, grace=True, tie=["start"])
             + _n("C", 4, 16))
    d = import_musicxml(_mxl(tmp_path, _simple_part(m1)), log=False)
    p = d["parts"][0]
    assert len(p["notes"]) == 2
    assert [n[2] for n in p["notes"]] == [0.0, 4.0]  # 无合并膨胀


def test_measure_rest_pickup_and_nominal(tmp_path):
    """整小节休止推进；implicit pickup 短小节（max_cursor 模型）。"""
    pickup = _ms(0, _attrs(div=4, beats=4) + _n("C", 4, 4, rest=True),
                 implicit=True)
    m1 = _ms(1, _n("C", 4, 16))
    d = import_musicxml(_mxl(tmp_path, _simple_part(pickup + m1)), log=False)
    assert d["parts"][0]["notes"] == [[60.0, 1.0, 4.0, 64.0]]  # pickup=1q
    assert d["meta"]["n_measures"] == 2


def test_cue_unpitched_skip_but_advance(tmp_path):
    """cue 剔除+unpitched 不出音，cursor 均照推（时间轴不缩）。"""
    m1 = _ms(1, _attrs() + _n("C", 4, 4, cue=True)
             + '<note><unpitched><display-step>E</display-step>'
             '<display-octave>4</display-octave></unpitched><duration>4'
             '</duration><voice>1</voice></note>'
             + _n("D", 4, 8))
    d = import_musicxml(_mxl(tmp_path, _simple_part(m1)), log=False)
    p = d["parts"][0]
    assert p["notes"] == [[62.0, 2.0, 2.0, 64.0]]   # onset=8/4=2q
    assert p["n_cue"] == 1 and p["n_unpitched"] == 1


def test_noninteger_measure_numbers_and_voices(tmp_path):
    """X1 编号小节按位置索引；非连续 voice {1,5} 透出。"""
    m1 = _ms("X1", _attrs(beats=2) + _n("C", 4, 8))
    m2 = _ms(1, _n("D", 4, 4, voice="1")
             + '<backup><duration>4</duration></backup>' + _n("E", 3, 8, voice="5"))
    d = import_musicxml(_mxl(tmp_path, _simple_part(m1 + m2)), log=False)
    assert d["meta"]["n_measures"] == 2
    p = d["parts"][0]
    assert sorted(set(p["note_voices"])) == ["1", "5"]
    assert len(p["note_voices"]) == len(p["notes"])


def test_tempo_dominant_and_metronome_fallback(tmp_path):
    """sound 直读+dominant-by-span；sound 缺失走 metronome（含附点）。"""
    m1 = _ms(1, _attrs(beats=4)
             + '<direction><direction-type><words>a</words></direction-type>'
             '<sound tempo="90"/></direction>' + _n("C", 4, 16))
    m2 = _ms(2, '<direction><direction-type><words>b</words></direction-type>'
             '<sound tempo="180"/></direction>' + _n("C", 4, 16))
    d = import_musicxml(_mxl(tmp_path, _simple_part(m1 + m2)), log=False)
    assert d["meta"]["bpm"] == 90.0                  # 4q vs 4q 并列取先
    # metronome fallback：附点二分 ×60 → 60×(2×1.5)=180 quarter-BPM
    mm = _ms(1, '<direction><direction-type><metronome><beat-unit>half'
             '</beat-unit><beat-unit-dot/><per-minute>60</per-minute>'
             '</metronome></direction-type></direction>'
             + _attrs() + _n("C", 4, 16))
    d2 = import_musicxml(_mxl(tmp_path, _simple_part(mm), name="t2"), log=False)
    assert d2["meta"]["bpm"] == 180.0


def test_dynamics_evidence_staff(tmp_path):
    """dynamics 证据 (beat, symbol, staff) 不应用（vel 恒 64）。"""
    m1 = _ms(1, _attrs() + '<direction placement="above"><direction-type>'
             '<dynamics><pp/></dynamics></direction-type><staff>1</staff>'
             '</direction>' + _n("C", 4, 16))
    d = import_musicxml(_mxl(tmp_path, _simple_part(m1)), log=False)
    assert d["parts"][0]["dynamics"] == [(0.0, "pp", 1)]
    assert {n[3] for n in d["parts"][0]["notes"]} == {64.0}


def test_barline_repeat_flags_and_backup_clamp(tmp_path, capsys):
    """repeat/ending 透出；backup 越界钳 0+warn（不 raise）。"""
    m1 = _ms(1, _attrs(beats=1) + _n("C", 4, 4)
             + '<barline location="right"><bar-style>light-heavy'
             '</bar-style><repeat direction="backward"/></barline>')
    d = import_musicxml(_mxl(tmp_path, _simple_part(m1)), log=False)
    assert d["meta"]["has_repeat"] and not d["meta"]["has_ending"]
    m2 = _ms(1, _attrs(beats=1) + _n("C", 4, 4)
             + '<backup><duration>8</duration></backup>' + _n("D", 4, 4))
    d2 = import_musicxml(_mxl(tmp_path, _simple_part(m2), name="t2"), log=True)
    err = capsys.readouterr().err
    assert "backup 越界" in err
    assert [62.0, 0.0, 1.0, 64.0] in d2["parts"][0]["notes"]  # 钳后 onset=0


def test_cross_part_desync_warning(tmp_path, capsys):
    """跨 part 小节长失同步 → warn（0.02q 容差）。"""
    p1_m = _ms(1, _attrs(beats=4) + _n("C", 4, 16))
    p2_m = _ms(1, _attrs(beats=4) + _n("C", 4, 12))          # 3q ≠ 4q
    xml = _doc([("P1", "Voice", 69, p1_m), ("P2", "Piano", 1, p2_m)])
    d = import_musicxml(_mxl(tmp_path, xml), log=True)
    err = capsys.readouterr().err
    assert "失同步" in err
    assert d["meta"]["n_parts"] == 2


def test_error_layers(tmp_path):
    """错误分层：timewise/无 part-list/divisions 未初始化/非 grace 缺 duration。"""
    timewise = ('<?xml version="1.0"?><score-timewise>'
                '<measure><part/></measure></score-timewise>')
    with pytest.raises(MusicXmlImportError):
        import_musicxml(_mxl(tmp_path, timewise, name="a1"), log=False)
    with pytest.raises(MusicXmlImportError):
        import_musicxml(_mxl(tmp_path, _ms(1, _n("C", 4, 4)), name="a2"),
                        log=False)                     # 无 part-list
    no_div = ('<?xml version="1.0"?><score-partwise version="4.0"><part-list>'
              '<score-part id="P1"><part-name>x</part-name></score-part>'
              '</part-list><part id="P1">'
              + _ms(1, _n("C", 4, 4)) + '</part></score-partwise>')
    with pytest.raises(MusicXmlImportError):
        import_musicxml(_mxl(tmp_path, no_div, name="a3"), log=False)
    no_dur = _simple_part(_ms(1, _n("C", 4, 4)
                              .replace("<duration>4</duration>", "")))
    with pytest.raises(MusicXmlImportError):
        import_musicxml(_mxl(tmp_path, no_dur, name="a4"), log=False)


def test_channel_allocator():
    """per-file 全局分配+跳 ch9+>15 raise（convert_openscore 写出层）。"""
    parsed = {"parts": [
        {"note_voices": ["1"] * 3},
        {"note_voices": ["1", "2", "5", "6", "7", "1", "2", "5"]},
    ]}
    m = convert_openscore.allocate_channels(parsed)
    assert sorted(m.values()) == [0, 1, 2, 3, 4, 5]   # 6 对 → ch0-5
    assert 9 not in m.values()
    with pytest.raises(convert_openscore.ChannelOverflow):
        convert_openscore.allocate_channels(
            {"parts": [{"note_voices": [str(i + 1)]} for i in range(16)]})


# ------------------------------------------------------------ 真实库锚点

def _skip_if_missing(*paths):
    for p in paths:
        if not os.path.exists(p):
            pytest.skip(f"{p} 缺失（MUSIC_ASSETS_ROOT 未挂载或库未取回）")


def test_target_three_anchors():
    """三件目标曲锚点（R1 普查+三轮审查复核值）：前奏音符数/调号/速度。"""
    _skip_if_missing(STAENDCHEN, DUBIST, MONDNACHT)
    d = import_musicxml(DUBIST, log=False)
    assert d["meta"]["bpm"] == 80.0 and d["meta"]["time"] == "3/8"
    assert d["meta"]["key"] == -3 and d["meta"]["n_measures"] == 82
    piano = d["parts"][1]
    assert piano["name"] == "Piano" and piano["program"] == 0
    intro = [n for n in piano["notes"] if n[1] < 10.5]        # 7 小节 × 1.5q
    assert len(intro) == 61                                   # 63 raw − 2 tie
    assert (min(n[0] for n in intro), max(n[0] for n in intro)) == (58.0, 82.0)
    d = import_musicxml(STAENDCHEN, log=False)
    assert d["meta"]["has_repeat"] and d["meta"]["bpm"] == 62.0
    piano = d["parts"][1]
    intro = [n for n in piano["notes"] if n[1] < 12.0]
    assert len(intro) == 48 and piano["n_grace"] == 2
    d = import_musicxml(MONDNACHT, log=False)
    piano = d["parts"][1]
    intro = [n for n in piano["notes"] if n[1] < 7.5]
    assert len([n for n in intro if n[2] > 0]) == 43           # −2 grace −4 tie


def test_l2_cross_source_voice():
    """L2 交叉源：Du bist die Ruh Voice 轨 vs mutopia D776（版本级独立）。

    协议（PLAN_musicxml §四.2）：编制核对+移调探针+grace 双滤；
    相似度=difflib.SequenceMatcher(autojunk=False).ratio() 硬门 ≥0.95；
    解析性错误 0 容限（差异块须归因版本编辑）。
    """
    _skip_if_missing(DUBIST, MUTOPIA_D776)
    from bgm_gen.core.smf_import import import_smf
    ours = import_musicxml(DUBIST, log=False)
    voice = ours["parts"][0]
    assert voice["name"] == "Voice"                            # 编制核对
    ours_seq = [int(n[0]) for n in voice["notes"] if n[2] > 0]  # grace 滤
    mut = import_smf(MUTOPIA_D776, log=False)
    music = [t for t in mut["tracks"] if t["notes"]]
    # 人声轨判别：prog≠0 唯一轨优先（mutopia 钢琴两手恒 prog=0 实测；
    # D547 最少轨启发会误选——R2 审查 m8），否则退最少音轨
    nonzero = [t for t in music if t["program"]]
    vtrack = nonzero[0] if len(nonzero) == 1 \
        else min(music, key=lambda t: len(t["notes"]))
    theirs = [int(n[0]) for n in vtrack["notes"]]

    def match_ratio(a, b, shift):
        bs = [x + shift for x in b]
        return difflib.SequenceMatcher(None, a, bs, autojunk=False).ratio()

    # 移调探针：octave/semitone 偏移全扫描，最佳对齐须为 0
    best_shift = max(range(-14, 15),
                     key=lambda s: match_ratio(ours_seq, theirs, s))
    assert best_shift == 0
    ratio = match_ratio(ours_seq, theirs, 0)
    assert ratio >= 0.95, f"Voice 相似度 {ratio:.4f} < 0.95"
    # 钢琴音高集合弱验证（版本写法漂移——非解析错误面）
    ours_pc = {int(n[0]) for p in ours["parts"][1:]
               for n in p["notes"] if n[2] > 0}
    theirs_pc = {int(n[0]) for t in music if t is not vtrack
                 for n in t["notes"]}
    assert len(ours_pc & theirs_pc) >= 30                      # 音高集合覆盖


def test_l3_roundtrip(tmp_path):
    """L3 roundtrip：per-voice channel 写出→import_smf 读回。

    音数全等硬断言+逐音 pitch 全等、|Δbeat|/|Δdur| ≤ 1 tick。
    """
    _skip_if_missing(DUBIST)
    from bgm_gen.core.smf_import import import_smf
    dst = str(tmp_path / "d776.mid")
    parsed, ev = convert_openscore.convert_one(DUBIST, dst)
    assert ev["ppq"] == 8 and not ev["quantized"]              # LCM(8)=8
    expect = sorted((int(n[0]), n[1], n[2]) for p in parsed["parts"]
                    for n in p["notes"] if n[2] > 0)           # grace 已剔
    back = import_smf(dst, log=False)
    got = sorted((int(n[0]), n[1], n[2]) for t in back["tracks"]
                 for n in t["notes"])
    assert len(got) == len(expect), \
        f"音数不等：读回 {len(got)} vs 写出 {len(expect)}（交叠丢音）"
    tick = 1.0 / ev["ppq"]
    for (p1, b1, d1), (p2, b2, d2) in zip(expect, got):
        assert p1 == p2
        assert abs(b1 - b2) <= tick + 1e-9
        assert abs(d1 - d2) <= tick + 1e-9


def test_l3_overflow_roundtrip(tmp_path):
    """溢出通道路径（R2 审查 M2 复用制）：同 (voice,pitch) 交叠→溢出道→
    读回音数全等（零复用版会让时间不相邻的冲突各耗一道）。"""
    from bgm_gen.core.smf_import import import_smf
    parsed = {"meta": {"bpm": 120}, "parts": [{
        "id": "P1", "name": "Piano", "program": 0,
        "notes": [[60.0, 0.0, 4.0, 64.0], [60.0, 2.0, 1.0, 64.0],
                  [60.0, 100.0, 4.0, 64.0], [60.0, 102.0, 1.0, 64.0]],
        "note_voices": ["1", "1", "1", "1"],
        "divisions": [4],
    }]}
    tracks, ev = convert_openscore.build_tracks(parsed)
    assert ev["n_overlap_moved"] == 2
    assert ev["n_channels"] == 2                 # 两处冲突复用同一溢出道
    dst = str(tmp_path / "ovf.mid")
    convert_openscore.write_smf(dst, tracks, bpm=120.0, ppq=ev["ppq"])
    back = import_smf(dst, log=False)
    got = [n for t in back["tracks"] for n in t["notes"]]
    assert len(got) == 4                         # 零丢音


def test_measure_direct_sound_and_changes(tmp_path):
    """measure 直挂 <sound tempo>；time/key 变更链。"""
    m1 = _ms(1, _attrs(div=4, beats=4, fifths=-1) + '<sound tempo="72"/>'
             + _n("C", 4, 16))
    m2 = _ms(2, '<attributes><time><beats>6</beats><beat-type>8'
             '</beat-type></time><key><fifths>2</fifths></key></attributes>'
             + _n("D", 4, 12))
    d = import_musicxml(_mxl(tmp_path, _simple_part(m1 + m2)), log=False)
    assert d["meta"]["bpm"] == 72.0              # measure 直挂 sound 生效
    assert d["meta"]["time"] == "4/4" and d["meta"]["key"] == -1
    assert d["meta"]["time_changes"] == [(4.0, "6/8")]
    assert d["meta"]["key_changes"] == [(4.0, 2)]


def test_midmeasure_divisions_change_raises(tmp_path):
    """小节中途 divisions 变更（cursor>0）→ raise（防御路径，全库 0 例）。"""
    m1 = _ms(1, _attrs(div=4, beats=4) + _n("C", 4, 4)
             + '<attributes><divisions>8</divisions></attributes>'
             + _n("D", 4, 8))
    with pytest.raises(MusicXmlImportError):
        import_musicxml(_mxl(tmp_path, _simple_part(m1)), log=False)


def test_multi_part_lcm_ppq():
    """pick_ppq：多 part divisions 值集 LCM；护栏回退。"""
    def fake(divs):
        return {"parts": [{"divisions": [d]} for d in divs]}
    assert convert_openscore.pick_ppq(fake([24, 480]))[0] == 480
    assert convert_openscore.pick_ppq(fake([7, 8]))[0] == 56
    assert convert_openscore.pick_ppq(fake([3, 5, 8]))[0] == 120
    ppq, quantized = convert_openscore.pick_ppq(fake([2520, 6240]))
    assert quantized and ppq == 384 * 6240      # LCM 131040 > 护栏 100000
