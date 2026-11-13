"""MusicXML（.mxl 压缩 score-partwise）读器——零观点（docs/PLAN_musicxml.md §二）。

stdlib（zipfile+xml.etree）解析 openscore 双库 .mxl（zip 内 META-INF/
container.xml 指向根 xml——L 库 score.xml / SQ 库 sqNNN.xml，实读 full-path
不猜测）；零 pip、零命名空间假设（双库 221 标签全裸实测）。

产出 = {meta: {...}, parts: [{id, name, program, lo, hi, notes,
note_voices, n_grace, n_cue, n_unpitched, dynamics}]}，
notes = [pitch, beat, dur, vel]（beat/dur 单位=四分音符浮点，
duration/divisions 以 Fraction 精确内算后 round 6 输出）。

零观点契约（PLAN_musicxml §二，三轮对抗审查定稿）：
    - vel 恒 64.0（MusicXML 无 MIDI 速度层；dynamics 收 (beat, symbol,
      staff) 证据不应用——表情层归自研引擎全权重写，PLAYBOOK）；
    - 小节长 = 该 part 该小节 max cursor（pickup/X 编号/劈小节天然正确，
      实测优于名义长模型）；偏离拍面名义长 >1 division 且非 implicit
      → warn；
    - divisions = per-part running state（重声明幂等；真值中途变更全库
      0 件实测，L1 合成用例为唯一覆盖；小节中途变更 raise）；
    - tie 解算在 part 扫描后全局做（onset 序单遍）：配对键=per-part
      (pitch)——跨 voice tie 是实存语义（全库 514 件，Mondnacht 18 对
      琶音持续音），邻接命中允许跨 voice；邻接失败回退最近在前 pending
      （窗=主导拍面小节长，quarter 域，禁小节序号差实现）+warn；再失败
      悬空 stop；残余 start=独立音+warn；grace 的 tie 标记一律忽略
      （Ghost Road dur 膨胀 +67 quarter 教训）；
    - backup 越界 → 钳小节起点 + warn（时号 desync 件钳 0 即唯一正确
      解释，不 raise——轮 1 实锤 2 件）；跨 part 小节长失同步（容差
      0.02 quarter 吸收 1/480 级舍入）→ warn（25 件实存，批转清单披露）；
    - grace 保留 dur=0.0（不发明时值，下游裁定）；cue 剔除（提示音，
      MuseScore 导出 MIDI 默认不发声——写它发声反而是发明；cursor 照推
      duration 保时间轴）；unpitched 不出音 cursor 照推；
    - tempo：sound@tempo 直读 quarter-BPM（spec 口径，禁 metronome
      beat-unit 双重换算）；sound 缺失时 metronome fallback（beat-unit
      因子 + 附点 ×1.5^dots）；主导 = dominant-by-span（对齐 smf_import
      哲学，并列取先）；反复/volta 不展开（按书写序一遍），
      has_repeat/has_ending 透出供下游勾稽（PLAYBOOK 反复铁律）。
"""

import sys
import zipfile
import xml.etree.ElementTree as ET
from fractions import Fraction

STEP_SEMI = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
BEAT_UNIT_FACTOR = {"whole": Fraction(4), "half": Fraction(2),
                    "quarter": Fraction(1), "eighth": Fraction(1, 2),
                    "16th": Fraction(1, 4), "32nd": Fraction(1, 8),
                    "64th": Fraction(1, 16), "128th": Fraction(1, 32)}
DESYNC_TOL_Q = Fraction(1, 50)       # 跨 part 小节长容差（0.02 quarter）
VEL_DEFAULT = 64.0                   # 零观点力度（mezzo）


class MusicXmlImportError(Exception):
    """MusicXML 解析失败（非 zip/无 container/坏结构等）。"""


def _midi_pitch(p_el):
    """<pitch> → MIDI number： (octave+1)*12 + step 半音 + alter。"""
    step = p_el.findtext("step")
    if step not in STEP_SEMI:
        raise MusicXmlImportError(f"非法 step {step!r}")
    octave = p_el.findtext("octave")
    if octave is None:
        raise MusicXmlImportError("pitch 缺 octave")
    alter = p_el.findtext("alter")
    alter_i = round(float(alter)) if alter else 0        # 微分音就近取整
    return (int(octave) + 1) * 12 + STEP_SEMI[step] + alter_i


def _beats_sum(beats_txt):
    """拍号分子原文求和（复合拍 "3+4" → 7；原文透传进 meta.time）。"""
    try:
        return sum(int(x) for x in beats_txt.split("+"))
    except ValueError:
        return None                                       # 异常分子：不做名义卫哨


def _iter_root(zip_path):
    """打开 .mxl → (ElementTree root, rootfile 路径)。"""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            try:
                cont = ET.fromstring(zf.read("META-INF/container.xml"))
            except KeyError:
                raise MusicXmlImportError("缺 META-INF/container.xml")
            root_path = None
            for rf in cont.iter("rootfile"):
                root_path = rf.get("full-path")
                if root_path:
                    break
            if not root_path:
                raise MusicXmlImportError("container.xml 无 rootfile full-path")
            try:
                data = zf.read(root_path)
            except KeyError:
                raise MusicXmlImportError(f"rootfile {root_path!r} 不在 zip 内")
    except zipfile.BadZipFile:
        raise MusicXmlImportError("非 zip 文件（.mxl 应为 zip 容器）")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise MusicXmlImportError(f"根 xml 解析失败：{e}")
    if root.tag != "score-partwise":
        raise MusicXmlImportError(f"不支持 {root.tag}（仅 score-partwise；"
                                  "timewise 双库无实例）")
    return root, root_path


def _scan_part(part_el):
    """单 part 扫描（phase A）：measure 逐元素 cursor 模拟。

    返回 dict：raw_notes（含 tie 标记的记录）/ measure_lens（quarter）/
    tempo_events / dynamics / time_sigs（(start_q, 分子, 分母)，含首个）/
    has_repeat / has_ending / 计数器 / warnings。
    raw_notes 记录 = [pitch, onset_q, dur_q, voice, is_grace, tie_stop,
    tie_start, seq]。
    """
    divisions = None
    raw = []
    measure_lens = []
    tempo_events = []                 # [(beat_q, bpm)]
    dynamics = []                     # [(beat_q, symbol, staff)]
    time_sigs = []                    # [(start_q, beats_sum, beat_type)]
    warnings = []
    n_grace = n_cue = n_unpitched = n_grace_tie = 0
    divisions_set = set()
    key_sigs = []                      # [(start_q, fifths)]（含首个）
    has_repeat = has_ending = False
    measure_start = Fraction(0)
    seq = 0
    for mi, measure in enumerate(part_el.findall("measure")):
        implicit = measure.get("implicit") == "yes"
        cursor = 0
        max_cursor = 0
        last_onset = None
        time_sig_here = None
        for el in measure:
            tag = el.tag
            if tag == "attributes":
                d = el.findtext("divisions")
                if d is not None:
                    nd = int(d)
                    if nd <= 0:
                        raise MusicXmlImportError(f"divisions={nd}（须正）")
                    if divisions is not None and nd != divisions and cursor > 0:
                        raise MusicXmlImportError(
                            f"divisions 小节中途变更（measure {mi + 1}，"
                            "全库无实例，不支持）")
                    divisions = nd
                    divisions_set.add(nd)
                t = el.find("time")
                if t is not None:
                    beats = t.findtext("beats")
                    bt = t.findtext("beat-type")
                    if beats is not None and bt is not None:
                        time_sig_here = (_beats_sum(beats), int(bt), beats)
                k = el.find("key")
                if k is not None and k.findtext("fifths") is not None:
                    key_sigs.append((measure_start,
                                     int(k.findtext("fifths"))))
            elif tag == "note":
                is_grace = el.find("grace") is not None
                is_cue = el.find("cue") is not None
                is_chord = el.find("chord") is not None
                dur_txt = el.findtext("duration")
                if is_grace:
                    dur_div = 0
                elif dur_txt is None:
                    raise MusicXmlImportError(
                        f"音符缺 duration（非 grace；measure {mi + 1}）")
                else:
                    dur_div = int(dur_txt)
                if divisions is None:
                    raise MusicXmlImportError(
                        f"遇音符但 divisions 未初始化（measure {mi + 1}）")
                voice = el.findtext("voice") or "1"
                if is_chord and last_onset is not None:
                    onset_div = last_onset            # 和弦内音：不推进 cursor
                else:
                    onset_div = cursor
                    last_onset = cursor
                    if not is_grace:
                        cursor += dur_div
                        if cursor > max_cursor:
                            max_cursor = cursor
                if is_cue and el.find("rest") is None:
                    n_cue += 1
                    continue                           # 提示音：不发声
                if el.find("rest") is not None:
                    continue
                if el.find("unpitched") is not None:
                    n_unpitched += 1
                    if n_unpitched <= 3:
                        warnings.append(f"measure {mi + 1}: unpitched 音跳过"
                                        "（cursor 照推）")
                    continue
                p_el = el.find("pitch")
                if p_el is None:
                    raise MusicXmlImportError(
                        f"非休止音符缺 pitch/unpitched（measure {mi + 1}）")
                pitch = _midi_pitch(p_el)
                onset_q = measure_start + Fraction(onset_div, divisions)
                dur_q = Fraction(0) if is_grace else Fraction(dur_div, divisions)
                ties = {t.get("type") for t in el.findall("tie")}
                if is_grace:
                    n_grace += 1
                    if ties:
                        n_grace_tie += 1              # 豁免（计数披露）
                    ties = set()
                raw.append([pitch, onset_q, dur_q, voice, is_grace,
                            "stop" in ties, "start" in ties, seq])
                seq += 1
            elif tag == "backup":
                d = el.findtext("duration")
                if d is None:
                    raise MusicXmlImportError("backup 缺 duration")
                cursor -= int(d)
                if cursor < 0:
                    warnings.append(f"measure {mi + 1}: backup 越界"
                                    f"（{int(d)} → 钳小节起点）")
                    cursor = 0
                last_onset = None
            elif tag == "forward":
                d = el.findtext("duration")
                if d is None:
                    raise MusicXmlImportError("forward 缺 duration")
                cursor += int(d)
                if cursor > max_cursor:
                    max_cursor = cursor
                last_onset = None
            elif tag == "direction":
                beat_q = (measure_start + Fraction(cursor, divisions)
                          if divisions else measure_start)
                bpm_found = False
                sound = el.find("sound")
                if sound is not None:
                    tempo = sound.get("tempo")
                    if tempo:
                        bpm = float(tempo)             # spec：恒 quarter-BPM
                        if 0 < bpm < 10000:
                            tempo_events.append((beat_q, bpm))
                            bpm_found = True
                if not bpm_found:                      # metronome fallback
                    for mt in el.iter("metronome"):
                        bu = mt.findtext("beat-unit")
                        pm = mt.findtext("per-minute")
                        if bu in BEAT_UNIT_FACTOR and pm:
                            dots = len(mt.findall("beat-unit-dot"))
                            factor = BEAT_UNIT_FACTOR[bu] * Fraction(3, 2) ** dots
                            bpm = float(pm) * float(factor)
                            tempo_events.append((beat_q, bpm))
                            break
                for dt in el.findall("direction-type"):
                    dyn = dt.find("dynamics")
                    if dyn is not None and len(dynamics) < 10_000:
                        if len(dynamics) == 9_999:
                            warnings.append("dynamics 证据超 10000 条截断")
                        for sym_el in dyn:
                            staff_txt = el.findtext("staff")
                            dynamics.append((beat_q, sym_el.tag,
                                             int(staff_txt) if staff_txt else None))
                            break                       # 首个符号
            elif tag == "sound":                        # measure 直挂 sound
                tempo = el.get("tempo")
                if tempo and divisions:
                    bpm = float(tempo)
                    if 0 < bpm < 10000:
                        tempo_events.append((measure_start
                                             + Fraction(cursor, divisions), bpm))
            elif tag == "barline":
                rep = el.find("repeat")
                if rep is not None:
                    has_repeat = True
                if el.find("ending") is not None:
                    has_ending = True
        if time_sig_here is not None:
            time_sigs.append((measure_start, time_sig_here[0], time_sig_here[1],
                              time_sig_here[2]))
        mlen = Fraction(max_cursor, divisions) if divisions else Fraction(0)
        measure_lens.append(mlen)
        # 名义长卫哨（implicit 小节豁免；异常分子跳过）
        if (time_sig_here is not None and divisions and not implicit
                and time_sig_here[0] is not None and mlen > 0):
            nominal_q = Fraction(time_sig_here[0] * 4, time_sig_here[1])
            if abs(mlen - nominal_q) > Fraction(1, divisions):
                warnings.append(f"measure {mi + 1}: 实测长 {float(mlen):.3}q"
                                f" ≠ 拍面名义 {float(nominal_q):.3}q")
        measure_start += mlen
    return {"raw": raw, "measure_lens": measure_lens,
            "divisions_set": sorted(divisions_set),
            "tempo_events": tempo_events, "dynamics": dynamics,
            "time_sigs": time_sigs, "key_sigs": key_sigs,
            "has_repeat": has_repeat,
            "has_ending": has_ending, "n_grace": n_grace, "n_cue": n_cue,
            "n_unpitched": n_unpitched, "n_grace_tie": n_grace_tie,
            "warnings": warnings, "end_q": measure_start}


def _dominant_measure_q(time_sigs, end_q):
    """主导拍面小节长（quarter）：覆盖 span 最长的 time sig，并列取先。

    time_sigs = [(start_q, beats_sum, beat_type, 原文分子)]（part 扫描期
    逐变更点收集）。无 time sig / 异常分子 → 缺省 4.0。
    """
    best, best_span = Fraction(4), Fraction(-1)
    for i, (start, beats, bt, _raw) in enumerate(time_sigs):
        if beats is None:
            continue
        nxt = time_sigs[i + 1][0] if i + 1 < len(time_sigs) else end_q
        span = nxt - start
        if span > best_span:
            best_span = span
            best = Fraction(beats * 4, bt)
    return best


def _resolve_ties(raw, window_q, warnings):
    """tie 全局解算（phase B，PLAN_musicxml §二.5）。

    onset 序单遍：stop 音先试精确邻接（end == onset，Fraction 判；跨
    voice 允许——邻接即同声部物理连续语义），失败回退最近在前 pending
    （0 < gap ≤ window_q）+warn，再失败悬空（独立保留）+warn；
    start 压栈；链中段（stop+start）合并后再压栈。残余 start 独立保留
    +warn。返回完成态记录 [pitch, onset, dur, voice, is_grace]（onset 序；
    合并产物 voice 取 start 段）。grace 的 tie 标志已在扫描层剥除，
    天然原样穿透。
    """
    out = []
    pending = []                       # [pitch, onset, dur, voice]
    for rec in sorted(raw, key=lambda r: (r[1], r[7])):
        pitch, onset, dur, voice, is_grace, has_stop, has_start, _seq = rec
        merged = None
        if has_stop:
            adj = [i for i, p in enumerate(pending)
                   if p[0] == pitch and p[1] + p[2] == onset]
            if adj:
                p = pending.pop(adj[0])               # FIFO（确定性）
                merged = [p[0], p[1], p[2] + dur, p[3]]
            else:
                cands = [(onset - (p[1] + p[2]), i) for i, p in enumerate(pending)
                         if p[0] == pitch
                         and Fraction(0) < onset - (p[1] + p[2]) <= window_q]
                if cands:
                    _dist, i = min(cands)
                    p = pending.pop(i)
                    merged = [p[0], p[1], p[2] + dur, p[3]]
                    warnings.append(f"tie 非邻接配对 pitch {pitch}"
                                    f"@{float(onset):.3}q（gap={float(_dist):.3}q）")
                else:
                    warnings.append(f"tie stop 悬空 pitch {pitch}"
                                    f"@{float(onset):.3}q（按独立音保留）")
        if has_start:
            pending.append(merged if merged is not None
                           else [pitch, onset, dur, voice])
        else:
            out.append(merged if merged is not None
                       else [pitch, onset, dur, voice, is_grace])
    for p in pending:
        warnings.append(f"tie start 悬空 pitch {p[0]}@{float(p[1]):.3}q"
                        "（独立保留）")
        out.append([p[0], p[1], p[2], p[3], False])
    return out


def import_musicxml(path, log=True):
    """MusicXML (.mxl) → {meta, parts}（PLAN_musicxml §二.1 接口）。

    log=True 时逐 part 证据日志打 stderr（part 名/program/音域/音符数/
    grace 数/警告——供下游判定，零观点不解释）。bpm=主导 tempo（无则
    None）；ppq=首 part 首个 divisions；program=源 midi-program 1-based
    → MIDI 0-based。
    """
    root, _root_path = _iter_root(path)
    part_list = root.find("part-list")
    if part_list is None:
        raise MusicXmlImportError("缺 part-list")
    info = {}
    for sp in part_list.findall("score-part"):
        pid = sp.get("id")
        prog_txt = None
        for mi in sp.findall("midi-instrument"):
            prog_txt = mi.findtext("midi-program")
            if prog_txt:
                break
        info[pid] = (sp.findtext("part-name") or pid,
                     int(prog_txt) - 1 if prog_txt else None)

    scanned = []
    for i, part_el in enumerate(root.findall("part")):
        pid = part_el.get("id") or f"P{i + 1}"
        name, prog = info.get(pid, (pid, None))
        dat = _scan_part(part_el)
        dat.update({"id": pid, "name": name, "program": prog})
        scanned.append(dat)

    # 跨 part 小节长一致性（容差 0.02q；只 warn——25 件 desync 实存，披露制）
    if len(scanned) > 1:
        ref = scanned[0]["measure_lens"]
        for other in scanned[1:]:
            lens = other["measure_lens"]
            n = min(len(ref), len(lens))
            desync = [j + 1 for j in range(n)
                      if abs(ref[j] - lens[j]) > DESYNC_TOL_Q]
            if len(ref) != len(lens):
                desync.append(f"小节数 {len(ref)}≠{len(lens)}")
            if desync:
                head = ", ".join(str(x) for x in desync[:5])
                other["warnings"].append(f"跨 part 小节长失同步：{head}"
                                         f"（共 {len(desync)} 处）")

    parts_out = []
    for dat in scanned:
        window_q = _dominant_measure_q(dat["time_sigs"], dat["end_q"])
        final = _resolve_ties(dat["raw"], window_q, dat["warnings"])
        final.sort(key=lambda r: (r[1], r[0]))          # (beat, pitch) 序
        rows = [[float(r[0]), round(float(r[1]), 6), round(float(r[2]), 6),
                 VEL_DEFAULT] for r in final]
        voices = [r[3] for r in final]
        pitches = [r[0] for r in final]
        parts_out.append({
            "id": dat["id"], "name": dat["name"], "program": dat["program"],
            "lo": min(pitches) if pitches else None,
            "hi": max(pitches) if pitches else None,
            "notes": rows, "note_voices": voices,
            "n_grace": dat["n_grace"], "n_cue": dat["n_cue"],
            "n_unpitched": dat["n_unpitched"],
            "n_grace_tie": dat["n_grace_tie"],
            "divisions": dat["divisions_set"],
            "warnings": list(dat["warnings"]),
            "dynamics": [(round(float(b), 6), s, st)
                         for b, s, st in dat["dynamics"]],
        })

    # ---- 文件级 meta 聚合 ----
    tempo_events = []
    seen_t = set()
    for dat in scanned:
        for b, bpm in dat["tempo_events"]:
            key = (b, bpm)
            if key not in seen_t:
                seen_t.add(key)
                tempo_events.append((b, bpm))
    tempo_events.sort()
    file_warns = []
    for i, (b, bpm) in enumerate(tempo_events):        # 同 beat 双 bpm 冲突披露
        if i + 1 < len(tempo_events) and tempo_events[i + 1][0] == b:
            file_warns.append(f"同 beat {float(b):.3}q 双 tempo 并存"
                              f"（{bpm} 与 {tempo_events[i + 1][1]}——"
                              "dominant 后序胜出，并列取先在此倒置）")
    if file_warns and scanned:
        scanned[0]["warnings"].extend(file_warns)
        parts_out[0]["warnings"].extend(file_warns)
    total_q = max((dat["end_q"] for dat in scanned), default=Fraction(0))
    bpm_dom = _dominant_tempo(tempo_events, total_q)

    first_sig = next((ts for dat in scanned for ts in dat["time_sigs"]), None)
    # time 变更链：各 part 变更点去重（去重键量化 0.02q 网格——跨 part 累计
    # 舍入漂移 Op.18/1 实见 1433.9958/1434.0 近重复对）
    sig_points = []
    seen_sig = set()
    for dat in scanned:
        for ts in dat["time_sigs"]:
            qkey = (round(ts[0] * 50), ts[3], ts[2])
            if qkey not in seen_sig:
                seen_sig.add(qkey)
                sig_points.append((ts[0], ts[3], ts[2]))
    sig_points.sort()
    time_changes = [(round(float(start), 6), f"{raw_num}/{bt}")
                    for start, raw_num, bt in sig_points[1:]]
    # key 首键+变更链（对称 time；各 part 去重同 0.02q 网格）
    key_first = next((kf for dat in scanned for _s, kf in dat["key_sigs"]),
                     None)
    key_points = []
    seen_key = set()
    for dat in scanned:
        for start, kf in dat["key_sigs"]:
            qkey = (round(start * 50), kf)
            if qkey not in seen_key:
                seen_key.add(qkey)
                key_points.append((start, kf))
    key_points.sort()
    key_changes = [(round(float(start), 6), kf)
                   for start, kf in key_points[1:]]
    meta = {
        "bpm": bpm_dom, "ppq": None, "time": None, "key": key_first,
        "n_measures": len(scanned[0]["measure_lens"]) if scanned else 0,
        "title": root.findtext("work/work-title")
        or root.findtext("movement-title"),
        "composer": None, "tempo_events": [(round(float(b), 6), bpm)
                                           for b, bpm in tempo_events],
        "time_changes": time_changes, "key_changes": key_changes,
        "has_repeat": any(d["has_repeat"] for d in scanned),
        "has_ending": any(d["has_ending"] for d in scanned),
        "n_parts": len(scanned),
    }
    for c in root.findall("identification/creator"):
        if c.get("type") == "composer":
            meta["composer"] = (c.text or "").strip()
            break
    if first_sig is not None:
        meta["time"] = f"{first_sig[3]}/{first_sig[2]}"
    # ppq：首 part 首个 divisions（attributes 出现序）
    for part_el in root.findall("part"):
        d = None
        for at in part_el.iter("attributes"):
            d = at.findtext("divisions")
            if d:
                meta["ppq"] = int(d)
                break
        if meta["ppq"] is not None:
            break

    if log:
        for dat, pout in zip(scanned, parts_out):
            print(f"[musicxml_import] part {pout['id']}: name={pout['name']!r} "
                  f"prog={pout['program']} range={pout['lo']}..{pout['hi']} "
                  f"notes={len(pout['notes'])} grace={pout['n_grace']} "
                  f"grace_tie_exempt={pout['n_grace_tie']} "
                  f"cue={pout['n_cue']} unpitched={pout['n_unpitched']}",
                  file=sys.stderr)
            for w in dat["warnings"]:
                print(f"[musicxml_import]   警告: {w}", file=sys.stderr)
        print(f"[musicxml_import] meta: bpm={meta['bpm']} time={meta['time']} "
              f"key={meta['key']} measures={meta['n_measures']} "
              f"repeat={meta['has_repeat']} ending={meta['has_ending']}",
              file=sys.stderr)
    return {"meta": meta, "parts": parts_out}


def _dominant_tempo(tempo_events, total_q):
    """主导 tempo（覆盖 quarter span 最长，并列取先）；空 → None。"""
    if not tempo_events:
        return None
    best_bpm, best_span = None, Fraction(-1)
    for i, (start, bpm) in enumerate(tempo_events):
        end = tempo_events[i + 1][0] if i + 1 < len(tempo_events) else total_q
        span = end - start
        if span > best_span:
            best_span, best_bpm = span, bpm
    return round(best_bpm, 4) if best_bpm else None
