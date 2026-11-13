"""project.json 装载、校验与渲染编排（PLAN.md §5 R3 / BG-D5/D7/D9）。

schema v1 语义规范的规范性文档 = bgm_gen/schema/project_v1.json；
本模块是运行期校验器（系统 python3 无 jsonschema，PLAN BG-D3），两者必须同步维护。

关键语义：
    meta.sr/channels 全局权威（BG-D5）——所有 stem 引擎声明同一 sr/channels，
    envelope 契约保证引擎输出相等，不符即拒。
    tracks[].notes 时间基准 = 轨道局部 beat（beat 0 对齐 clip.start_beat 的全局位置）；
    轨道整体经 clip 窗平移到全局时间线，stem 截断/补零到窗长。
    sections 仅时间窗标注/段落命名，渲染不依赖。
    automation 时间基准 = 全局 beat。
    工程时长 = max(duration_hint, 所有轨 clip 结束秒)，全体轨补零/截断到该时长。

v1 编排只接受确定性引擎（synth/sf2/sfz/sample）；ai/demucs 属整段素材/降维工具
（BG-D10），走 bgm stem / ai 产线，不作为工程轨。
"""

import json
import math
import os
import re
import stat
import tempfile

from ..engines import run_engine
from ..engines.base import EngineError, default_out, get_descriptor, \
    synth_program_param_keys, validate_param_value
from ..engines.midi_util import NOTE_EVENT_TYPES

PROJECT_SPEC_VERSION = "1"
SUPPORTED_SPEC_VERSIONS = ("1", "2")   # v3："2" 本期增 $ref 槽位 + master.qc.duration_min_s
TRACK_ENGINES = ("synth", "sf2", "sfz", "sample")
# v1 版本前置门（PLAN_v3 §3.3/R3-P7）：spec_version="1" 的轨带这些键一律拒绝
V2_ONLY_TRACK_KEYS = ("events", "$ref", "params", "bend_range", "space")
DEFAULT_TAIL_BEATS = 4.0          # 无 clip 时：max(note 结束) + 4 beat 释放尾巴
AUTOMATION_PARAMS = ("gain_db", "pan", "lowpass_hz")
META_KEYS = {"spec_version", "sr", "channels", "bpm", "key", "timesig", "seed", "duration_hint"}
TRACK_KEYS = {"name", "engine", "instrument", "sf2", "bank", "program",
              "notes", "events", "params", "bend_range", "gain_db", "pan",
              "space", "clip", "bus"}
# v3.2 轨道空间字段闭集（PLAN_v3 空间化轮）：depth 必填，width/send_db 可选
SPACE_KEYS = {"depth", "width", "send_db"}
AUTOMATION_KEYS = {"target_track", "param", "points", "curve"}
MASTER_CHAINS = ("loudnorm", "matchering")

# v3 R2（PLAN_v3 §3.2/§3.6）：事件类型闭集（与 engines.midi_util.EVENT_TYPES 同源）
EVENT_TYPES = ("cc", "pitch_bend", "keyswitch", "program_change",
               "note_on", "note_off")
# 事件逐类型字段闭集（未知字段报错——与 TRACK_KEYS 封闭集同惯例，防拼错静默吃默认值）
_EVENT_FIELDS = {
    "cc": {"ctrl", "value"},
    "pitch_bend": {"semitones"},
    "keyswitch": {"pitch"},
    "program_change": {"program", "bank"},
    "note_on": {"pitch", "vel"},
    "note_off": {"pitch", "vel"},
}
# params 投影锁定集（params 不可覆盖）= track_projection 映射所产出的字段 +
# 结构必填字段（PLAN_v3 §3.6）。引擎侧结果：
#   synth 合法={params}；sf2={gain,channel}；sfz=∅；sample=∅
PROJECTION_LOCKED_PARAMS = frozenset({
    "bpm", "notes", "program", "sf2", "sfz", "bank", "instrument",
    "hits", "length_s", "events", "bend_range"})


class ProjectError(Exception):
    """project.json 校验/编排失败。消息含可定位字段路径。"""


# ---------------------------------------------------------------- 装载与校验

def load_project(path):
    """读 project.json → 校验 → 返回（补默认值后的）规范 dict。失败抛 ProjectError。

    校验风格：收集全部错误一次性报告，每条带字段路径（如 tracks[1].notes[2].vel）；
    $ref 展开轨的错误路径附加来源标注（如 tracks[2].notes[0] (tracks/vla.json)）。

    v3 双布局（PLAN_v3 §3.3 / C5）：
        - 版本前置门：spec_version="1" 的轨带 events/$ref/params/bend_range 一律
          拒绝（先查版本再展开，报字段路径；v1 零迁移）；
        - 仅 spec_version="2" 允许 track 槽位 {"$ref": "tracks/<file>.json"}：
          相对工程文件所在目录解析、禁绝对路径、外部文件内不得再嵌 $ref、
          $ref 对象不得带兄弟字段；展开为完整 track 对象并记录来源映射
          （派生键 _ref_src，save_project 白名单过滤）。
        - 派生键：_ref_base_dir=工程文件目录（merge_layout 解析用）、
          _uses_refs=原始布局是否含 $ref 槽位（渲染快照口径用）。
    """
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        raise ProjectError(f"{path}: JSON 解析失败：{e}") from e
    if not isinstance(raw, dict):
        raise ProjectError(f"{path}: 顶层必须是 object")

    errs = []
    base_dir = os.path.dirname(os.path.abspath(path))

    # ---- 版本前置门 + $ref 展开（json.load 之后、校验之前；PLAN_v3 §3.3/R3-P7）
    raw_version = str((raw.get("meta") or {}).get("spec_version", PROJECT_SPEC_VERSION))
    raw_tracks = raw.get("tracks")
    if isinstance(raw_tracks, list) and raw_version == PROJECT_SPEC_VERSION:
        for i, tr in enumerate(raw_tracks):
            if isinstance(tr, dict):
                for k in V2_ONLY_TRACK_KEYS:
                    if k in tr:
                        errs.append(f"tracks[{i}].{k}：spec_version=\"1\" 工程不支持"
                                    f"（v2 专属字段；v1 零迁移，PLAN_v3 §3.3）")
    uses_refs = False
    if isinstance(raw_tracks, list) and raw_version == "2":
        for i, tr in enumerate(raw_tracks):
            if not (isinstance(tr, dict) and "$ref" in tr):
                continue
            uses_refs = True
            p = f"tracks[{i}]"
            if len(tr) != 1:
                errs.append(f"{p}.$ref：$ref 槽位必须是纯对象，不得带兄弟字段")
                raw_tracks[i] = {k: v for k, v in tr.items() if k != "$ref"}
                continue
            ext, err = _read_ref_track(base_dir, tr["$ref"])
            if err:
                errs.append(f"{p}.$ref：{err}")
                raw_tracks[i] = {}
            else:
                raw_tracks[i] = ext

    proj = dict(raw)
    proj["name"] = _str(raw, "name", errs, default=os.path.splitext(
        os.path.basename(path))[0], allow_empty=False)
    proj["_ref_base_dir"] = base_dir
    proj["_uses_refs"] = uses_refs

    meta = _obj(raw, "meta", errs, required=True)
    meta = meta if meta is not None else {}
    _unknown(meta, META_KEYS, errs, "meta")
    m = {}
    m["spec_version"] = str(meta.get("spec_version", PROJECT_SPEC_VERSION))
    if m["spec_version"] not in SUPPORTED_SPEC_VERSIONS:
        errs.append(f"meta.spec_version：{m['spec_version']} 不受支持"
                    f"（当前 {list(SUPPORTED_SPEC_VERSIONS)}）")
    m["sr"] = _int(meta, "sr", errs, path="meta", lo=8000, hi=384000, default=44100)
    m["channels"] = _int(meta, "channels", errs, path="meta", lo=1, hi=2, default=2)
    m["bpm"] = _num(meta, "bpm", errs, path="meta", lo=0.0001, default=120.0)
    m["key"] = meta.get("key")
    if m["key"] is not None and not isinstance(m["key"], str):
        errs.append("meta.key：必须是字符串（如 \"D minor\"）或 null")
    ts = meta.get("timesig")
    if ts is not None and (not isinstance(ts, str) or "/" not in ts):
        errs.append("meta.timesig：必须是 \"n/m\" 形式字符串")
    m["timesig"] = ts
    seed = meta.get("seed")
    if seed is not None and not isinstance(seed, int):
        errs.append("meta.seed：必须是整数或 null")
    m["seed"] = seed
    m["duration_hint"] = _num(meta, "duration_hint", errs, path="meta",
                              lo=0.0001, default=None)
    proj["meta"] = m

    # ---- tracks
    tracks_raw = raw.get("tracks")
    if not isinstance(tracks_raw, list) or not tracks_raw:
        raise ProjectError("tracks：必须是非空数组（校验中止，先修这一条）")
    is_v2 = str(m["spec_version"]) == "2"           # v2 轨专属键仅 v2 校验
    tracks, names = [], set()
    for i, tr in enumerate(tracks_raw):
        p = f"tracks[{i}]"
        n0 = len(errs)
        if not isinstance(tr, dict):
            errs.append(f"{p}：必须是 object")
            continue
        ref_src = tr.pop("_ref_src", None)          # $ref 展开的来源映射（派生键）
        _unknown(tr, TRACK_KEYS, errs, p)
        t = {}
        t["name"] = _str(tr, "name", errs, path=p, allow_empty=False)
        if t["name"]:
            # m-3：轨名将直接用作 stems/ 下文件名——拒绝路径分隔符与 "."，
            # 防越权写出（如 "a/b" 逃逸轨目录）
            if any(c and c in t["name"] for c in (os.sep, os.altsep)) \
                    or t["name"] == ".":
                errs.append(f"{p}.name：{t['name']!r} 不得作为轨名——"
                            "轨名将用作 stems/ 下文件名，不得含路径分隔符（或等于 '.'）")
            elif t["name"] in names:
                errs.append(f"{p}.name：轨名重复 {t['name']!r}")
            else:
                names.add(t["name"])
        t["engine"] = _str(tr, "engine", errs, path=p, allow_empty=False)
        if t["engine"] and t["engine"] not in TRACK_ENGINES:
            errs.append(f"{p}.engine：{t['engine']!r} 不能作为工程轨（v1 支持确定性引擎 "
                        f"{list(TRACK_ENGINES)}；ai/demucs 走 bgm stem/ai 产线，BG-D10）")
        t["instrument"] = _str(tr, "instrument", errs, path=p, allow_empty=False)
        t["bank"] = _int(tr, "bank", errs, path=p, lo=0, hi=128, default=0)
        t["program"] = _int(tr, "program", errs, path=p, lo=0, hi=127, default=0)
        # 记录是否显式给出（instrument 的 "sf2名:bank:program" 速记只填补未显式给的字段）
        t["bank_explicit"] = "bank" in tr
        t["program_explicit"] = "program" in tr

        notes_raw = tr.get("notes")
        if notes_raw is None:
            notes_raw = []
        if not isinstance(notes_raw, list):
            errs.append(f"{p}.notes：必须是数组 [[pitch,beat,dur,vel],...]")
            notes_raw = []
        elif not notes_raw and not is_v2:
            # v2 notes 改 optional（XOR 门接管"轨必须能发声"）；v1 保持 required+非空
            errs.append(f"{p}.notes：必须是非空数组 [[pitch,beat,dur,vel],...]")
        notes = []
        for j, row in enumerate(notes_raw):
            q = f"{p}.notes[{j}]"
            if not isinstance(row, (list, tuple)) or len(row) != 4:
                errs.append(f"{q}：必须是 4 元数组 [pitch, beat, dur, vel]")
                continue
            pitch, beat, dur, vel = row
            ok = True
            for v, lo, hi, tag in ((pitch, 0, 127, "pitch"), (beat, 0, None, "beat"),
                                   (dur, 1e-9, None, "dur"), (vel, 0, 127, "vel")):
                if not isinstance(v, (int, float)) or isinstance(v, bool) \
                        or not math.isfinite(v):
                    errs.append(f"{q}.{tag}：必须是（有限）数值，得到 {v!r}")
                    ok = False
                    continue
                if v < lo or (hi is not None and v > hi):
                    errs.append(f"{q}.{tag}：{v} 越界（合法 {lo}..{hi if hi is not None else '∞'}）")
                    ok = False
            if ok:
                notes.append([float(pitch), float(beat), float(dur), float(vel)])
        t["notes"] = notes

        # ---- v3 R2：v2 轨专属（events/params/bend_range；PLAN_v3 §3.2/§3.6）
        events = _validate_track_events(tr, t["engine"], bool(notes), errs, p, is_v2)
        if events:
            t["events"] = events
        params = _validate_track_params(tr, t["engine"], errs, p, is_v2)
        if params:
            t["params"] = params
        bend_range = _validate_track_bend_range(tr, t["engine"], events, errs, p, is_v2)
        if bend_range is not None:
            t["bend_range"] = bend_range

        # ---- v3.2：轨道空间字段（depth/width/send_db；空间化轮）
        space = _validate_track_space(tr, errs, p, is_v2)
        if space is not None:
            t["space"] = space

        t["gain_db"] = _num(tr, "gain_db", errs, path=p, lo=-80, hi=24, default=0.0)
        t["pan"] = _num(tr, "pan", errs, path=p, lo=-1, hi=1, default=0.0)

        clip = tr.get("clip")
        if clip is None:
            if notes:
                end = max((n[1] + n[2] for n in notes), default=0.0)
            else:
                # events-only 轨缺省 clip：note_off 最大 beat + 4 拍尾巴
                #（v1 "notes 末+4" 语义等价物，PLAN_v3 §3.2 投影规范③）
                end = max((e["t"] for e in events if e["type"] == "note_off"),
                          default=0.0)
            clip = {"start_beat": 0.0, "end_beat": end + DEFAULT_TAIL_BEATS}
            t["clip_explicit"] = False
        else:
            if not isinstance(clip, dict):
                errs.append(f"{p}.clip：必须是 object")
                clip = {"start_beat": 0.0, "end_beat": 1.0}
            else:
                clip = {"start_beat": _num(clip, "start_beat", errs, path=f"{p}.clip",
                                           lo=0.0, default=0.0),
                        "end_beat": _num(clip, "end_beat", errs, path=f"{p}.clip",
                                         lo=1e-9, default=None)}
                if clip["end_beat"] is None:
                    errs.append(f"{p}.clip：缺 end_beat（显式 clip 必须给出；"
                                f"或整个省略 clip 由 notes 推导）")
                if clip["end_beat"] is not None and clip["start_beat"] is not None \
                        and clip["end_beat"] <= clip["start_beat"]:
                    errs.append(f"{p}.clip：end_beat ({clip['end_beat']}) 必须大于 "
                                f"start_beat ({clip['start_beat']})")
            t["clip_explicit"] = True
        t["clip"] = clip

        bus = tr.get("bus", [])
        if bus is None:
            bus = []
        if not isinstance(bus, list) or any(not isinstance(x, str) or not x.strip()
                                            for x in bus):
            errs.append(f"{p}.bus：必须是 ffmpeg 滤镜字符串数组（如 [\"lowpass=f=8000\"]）")
            bus = []
        bad = [x for x in bus if any(c in x for c in ";[]")]
        if bad:
            errs.append(f"{p}.bus：滤镜串不得含 ; [ ]（会破坏图嵌入）：{bad}")
        t["bus"] = bus
        if ref_src:
            t["_ref_src"] = ref_src        # 来源映射留档（show 标注来源文件；save 白名单过滤）
        tracks.append(t)
        if ref_src:
            for k in range(n0, len(errs)):          # 校验报错路径附加来源标注
                if errs[k].startswith(p):
                    errs[k] = errs[k].replace("：", f" ({ref_src})：", 1)
    proj["tracks"] = tracks

    if not names:
        raise ProjectError("tracks[].name 全部无效（校验中止）：" + "; ".join(errs))

    # ---- sections（仅标注，渲染不依赖）
    secs = raw.get("sections", [])
    if secs is None:
        secs = []
    if not isinstance(secs, list):
        errs.append("sections：必须是数组")
        secs = []
    out_secs = []
    for i, s in enumerate(secs):
        if not isinstance(s, dict):
            errs.append(f"sections[{i}]：必须是 object")
            continue
        name = _str(s, "name", errs, path=f"sections[{i}]", allow_empty=False)
        sb = _num(s, "start_beat", errs, path=f"sections[{i}]", lo=0.0, default=0.0)
        lb = _num(s, "length_beat", errs, path=f"sections[{i}]", lo=1e-9, default=None)
        out_secs.append({"name": name, "start_beat": sb, "length_beat": lb})
    proj["sections"] = out_secs

    # ---- automation
    autos_raw = raw.get("automation", []) or []
    if not isinstance(autos_raw, list):
        errs.append("automation：必须是数组")
        autos_raw = []
    autos = []
    _seen_auto = set()
    for i, a in enumerate(autos_raw):
        p = f"automation[{i}]"
        if not isinstance(a, dict):
            errs.append(f"{p}：必须是 object")
            continue
        _unknown(a, AUTOMATION_KEYS, errs, p)
        tgt = _str(a, "target_track", errs, path=p, allow_empty=False)
        if tgt and tgt not in names:
            errs.append(f"{p}.target_track：{tgt!r} 不在轨名表中 {sorted(names)}")
        param = _str(a, "param", errs, path=p, allow_empty=False)
        if param and param not in AUTOMATION_PARAMS:
            errs.append(f"{p}.param：{param!r} 不受支持（v1 支持 {list(AUTOMATION_PARAMS)}）")
        curve = _str(a, "curve", errs, path=p, default="linear", allow_empty=False)
        if curve not in ("linear", "exp"):
            errs.append(f"{p}.curve：必须是 linear|exp")
        pts_raw = a.get("points")
        if not isinstance(pts_raw, list) or len(pts_raw) < 2:
            errs.append(f"{p}.points：至少两个点 [[beat,value],...]")
            pts_raw = []
        pts = []
        for j, pt in enumerate(pts_raw):
            if not isinstance(pt, (list, tuple)) or len(pt) != 2:
                errs.append(f"{p}.points[{j}]：必须是 [beat, value] 二元数组")
                continue
            b, v = pt
            if not isinstance(b, (int, float)) or isinstance(b, bool) or b < 0:
                errs.append(f"{p}.points[{j}].beat：必须是非负数值")
                continue
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                errs.append(f"{p}.points[{j}].value：必须是数值")
                continue
            pts.append([float(b), float(v)])
        if len(pts) >= 2 and any(pts[k + 1][0] - pts[k][0] <= 0 for k in range(len(pts) - 1)):
            errs.append(f"{p}.points：beat 必须严格递增")
        if param == "pan":
            for j, (_, v) in enumerate(pts):
                if not -1 <= v <= 1:
                    errs.append(f"{p}.points[{j}].value：pan 值越界 -1..1")
            if curve == "exp":
                errs.append(f"{p}：pan 自动化不支持 exp 曲线（仅 linear）")
        if param == "lowpass_hz":
            for j, (_, v) in enumerate(pts):
                if v <= 0:
                    errs.append(f"{p}.points[{j}].value：lowpass_hz 必须为正")
        autos.append({"target_track": tgt, "param": param, "points": pts,
                      "curve": curve})
        _k = (tgt, param)
        if _k in _seen_auto:
            errs.append(f"automation：{(tgt, param)} 重复（同轨同参数只允许一条，"
                        f"多点请并入 points）")
        _seen_auto.add(_k)
    proj["automation"] = autos

    # ---- sidechain（单对象；多组 duck 用 targets[]，v1 不设数组入口）
    sc = raw.get("sidechain")
    if sc is not None:
        if not isinstance(sc, dict):
            errs.append("sidechain：必须是 object")
            sc = None
        else:
            trig = _str(sc, "trigger_track", errs, path="sidechain", allow_empty=False)
            if trig and trig not in names:
                errs.append(f"sidechain.trigger_track：{trig!r} 不在轨名表中 {sorted(names)}")
            tgts = sc.get("targets")
            if not isinstance(tgts, list) or not tgts:
                errs.append("sidechain.targets：必须是非空数组")
                tgts = []
            for j, t in enumerate(tgts):
                if not isinstance(t, str) or t not in names:
                    errs.append(f"sidechain.targets[{j}]：{t!r} 不在轨名表中 {sorted(names)}")
            if len(set(tgts)) != len(tgts):
                errs.append("sidechain.targets：存在重复轨名")
            sc = {"trigger_track": trig,
                  "targets": tgts,
                  "threshold_db": _num(sc, "threshold_db", errs, path="sidechain",
                                       lo=-90, hi=0, default=-24.0),
                  "ratio": _num(sc, "ratio", errs, path="sidechain", lo=1, hi=20, default=4.0),
                  "attack_ms": _num(sc, "attack_ms", errs, path="sidechain",
                                    lo=0.01, hi=1000, default=20.0),
                  "release_ms": _num(sc, "release_ms", errs, path="sidechain",
                                     lo=1, hi=10000, default=300.0)}
    proj["sidechain"] = sc

    # ---- master
    mst = raw.get("master", {}) or {}
    if not isinstance(mst, dict):
        errs.append("master：必须是 object")
        mst = {}
    chain = _str(mst, "chain", errs, path="master", default="loudnorm",
                 allow_empty=False)
    if chain not in MASTER_CHAINS:
        errs.append(f"master.chain：必须是 {list(MASTER_CHAINS)}")
    ref = mst.get("ref")
    if chain == "matchering" and not ref:
        errs.append("master.ref：matchering 模式必填参考音频路径")
    qc = mst.get("qc", {}) or {}
    if not isinstance(qc, dict):
        errs.append("master.qc：必须是 object")
        qc = {}
    for k in ("silent_window_max_s", "bpm_tol_pct", "hf_ratio_max", "duration_min_s"):
        v = qc.get(k)
        if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0):
            errs.append(f"master.qc.{k}：必须是正数值或 null")
    kt = qc.get("key_top2")
    if kt is not None and not isinstance(kt, bool):
        errs.append("master.qc.key_top2：必须是布尔或 null")
    # ---- master.pre_limit（v2 专属：母前峰值限幅，PLAN_v3 §4.3 crest 预案）
    pre_limit = None
    if "pre_limit" in mst and mst["pre_limit"] is not None:
        # null = 显式关；空对象 {} 违反 schema required ceiling_db → 拒绝
        # （R4 审查 B·M-4：原先 falsy 真值门使 {} 静默当 off）
        if raw_version != "2":
            errs.append("master.pre_limit：仅 spec_version=\"2\" 支持（v1 冻结）")
        else:
            pl = mst["pre_limit"]
            if not isinstance(pl, dict):
                errs.append("master.pre_limit：必须是 object")
            else:
                unknown = set(pl) - {"ceiling_db", "attack_ms", "release_ms"}
                if unknown:
                    errs.append(f"master.pre_limit：未知键 {sorted(unknown)}")
                if "ceiling_db" not in pl:
                    errs.append("master.pre_limit.ceiling_db：必填")
                pre_limit = {
                    "ceiling_db": _num(pl, "ceiling_db", errs,
                                       path="master.pre_limit", lo=-40, hi=12),
                    "attack_ms": _num(pl, "attack_ms", errs,
                                      path="master.pre_limit", lo=0.1, hi=100,
                                      default=5.0),
                    "release_ms": _num(pl, "release_ms", errs,
                                       path="master.pre_limit", lo=1, hi=2000,
                                       default=150.0),
                }
    # ---- master.space_ir（v2 专属：工程级共享厅堂 IR 声明，v3.2 空间化轮）
    space_ir = None
    if "space_ir" in mst and mst["space_ir"] is not None:
        # null = 显式关（=缺省，wet 总线不启用——渲染位级与现状一致）
        if raw_version != "2":
            errs.append("master.space_ir：仅 spec_version=\"2\" 支持（v1 冻结）")
        else:
            sir = mst["space_ir"]
            if not isinstance(sir, dict):
                errs.append("master.space_ir：必须是 object")
            else:
                unknown = set(sir) - {"rt60_s", "seed"}
                if unknown:
                    errs.append(f"master.space_ir：未知键 {sorted(unknown)}"
                                f"（允许 [rt60_s, seed]）")
                for k in ("rt60_s", "seed"):
                    if k not in sir:
                        errs.append(f"master.space_ir.{k}：必填")
                rt60 = _num(sir, "rt60_s", errs, path="master.space_ir",
                            lo=0.4, hi=4.0)
                sd = sir.get("seed")
                if sd is not None and (not isinstance(sd, int)
                                       or isinstance(sd, bool)):
                    errs.append(f"master.space_ir.seed：必须是整数，得到 {sd!r}")
                    sd = None
                if rt60 is not None and sd is not None:
                    space_ir = {"rt60_s": rt60, "seed": sd}

    proj["master"] = {
        "target_lufs": _num(mst, "target_lufs", errs, path="master", lo=-40, hi=0,
                            default=-14.0),
        "true_peak_db": _num(mst, "true_peak_db", errs, path="master", lo=-40, hi=0,
                             default=-1.0),
        "chain": chain,
        "ref": ref,
        "lufs_tol": _num(mst, "lufs_tol", errs, path="master", lo=0.01, default=1.0),
        # pre_limit/space_ir 仅在声明时入键——v1 落盘不得出现 null 值键
        **({} if pre_limit is None else {"pre_limit": pre_limit}),
        **({} if space_ir is None else {"space_ir": space_ir}),
        "qc": {"silent_window_max_s": qc.get("silent_window_max_s", 1.5),
               "bpm_tol_pct": qc.get("bpm_tol_pct"),
               "key_top2": qc.get("key_top2"),
               "hf_ratio_max": qc.get("hf_ratio_max"),
               "duration_min_s": qc.get("duration_min_s")},
    }

    if errs:
        raise ProjectError(f"{path}：project.json 校验失败（{len(errs)} 处）：\n  "
                           + "\n  ".join(errs))
    return proj


# ------------------------------------------------- v3 R2 事件流/params 校验
# （PLAN_v3 §3.2 机制 B / §3.6 机制 F；仅 spec_version="2" 生效，
#   v1 由装载层版本前置门拒绝 events/params/bend_range 键。）

def _event_num(ev, key, errs, q, lo=None, hi=None, integral=False,
               default=None, optional=False):
    """事件数值字段校验（缺失/类型/范围/整值）。返回 float；缺失且 optional 返回默认。"""
    if key not in ev:
        if optional:
            return float(default) if default is not None else None
        errs.append(f"{q}.{key}：缺失（必填）")
        return None
    v = ev[key]
    if not isinstance(v, (int, float)) or isinstance(v, bool) \
            or not math.isfinite(v):
        errs.append(f"{q}.{key}：必须是（有限）数值，得到 {v!r}")
        return None
    if integral and float(v) != int(v):
        errs.append(f"{q}.{key}：必须是整数，得到 {v!r}")
        return None
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        errs.append(f"{q}.{key}：{v} 越界（合法 {lo}..{hi}）")
        return None
    return float(v)


def _validate_track_events(tr, engine, has_notes, errs, p, is_v2):
    """v2 track.events 校验（PLAN_v3 §3.2）：返回规范事件列表（仅完整合法者）。

    逐元素：dict；t 数值≥0（轨局部 beat）；type ∈ 六类闭集（C11）；逐类型字段
    （cc{ctrl:int 0..127, value:num 0..127}；pitch_bend{semitones:float}；
    keyswitch{pitch:int 0..127}；program_change{program:int 0..127,
    bank?:int 0..128}；note_on{pitch, vel:int 1..127}；note_off{pitch,
    vel? 默认 0}）；未知字段拒绝。
    跨事件：XOR 门（notes 与 note 事件互斥且恰有其一）；note 配对（同 pitch
    交叠/悬空 note_on/无 on 的 note_off 拒绝）；事件能力门（描述符
    events_supported：sf2 仅 cc/pitch_bend+note 载体、sfz 全集、synth/sample 全拒）。
    """
    if not is_v2:
        return []
    raw = tr.get("events")
    if raw is None:
        # R3 审查 M-3：events 键缺席也必须过 XOR 门——notes 删空且无事件的
        # "哑轨"拒绝（此前早退绕过，edit 可写出无载体轨）。
        if not has_notes:
            errs.append(f"{p}：轨必须能发声——notes 与 note 事件二者必须恰有其一（XOR）")
        return []
    if not isinstance(raw, list):
        errs.append(f"{p}.events：必须是数组")
        return []
    events = []
    for j, ev in enumerate(raw):
        q = f"{p}.events[{j}]"
        if not isinstance(ev, dict):
            errs.append(f"{q}：必须是 object")
            continue
        typ = ev.get("type")
        if typ not in EVENT_TYPES:
            errs.append(f"{q}.type：{typ!r} 不在事件类型闭集 "
                        f"{list(EVENT_TYPES)}（未知类型拒绝，C11）")
            continue
        for f in ev:
            if f not in ("type", "t") and f not in _EVENT_FIELDS[typ]:
                errs.append(f"{q}：未知字段 {f!r}（{typ} 事件允许 "
                            f"{sorted(_EVENT_FIELDS[typ] | {'t', 'type'})}）")
        ok = True
        t = _event_num(ev, "t", errs, q, lo=0)
        if t is None:
            ok = False
        if typ == "cc":
            ctrl = _event_num(ev, "ctrl", errs, q, lo=0, hi=127, integral=True)
            value = _event_num(ev, "value", errs, q, lo=0, hi=127)
            if None in (ctrl, value):
                ok = False
            elif ok:
                events.append({"type": "cc", "t": t, "ctrl": ctrl, "value": value})
        elif typ == "pitch_bend":
            semitones = _event_num(ev, "semitones", errs, q)
            if semitones is None:
                ok = False
            elif ok:
                events.append({"type": "pitch_bend", "t": t,
                               "semitones": semitones})
        elif typ == "keyswitch":
            pitch = _event_num(ev, "pitch", errs, q, lo=0, hi=127, integral=True)
            if pitch is None:
                ok = False
            elif ok:
                events.append({"type": "keyswitch", "t": t, "pitch": pitch})
        elif typ == "program_change":
            program = _event_num(ev, "program", errs, q, lo=0, hi=127, integral=True)
            bank = _event_num(ev, "bank", errs, q, lo=0, hi=128, integral=True,
                              optional=True)
            if program is None:
                ok = False
            elif ok:
                e = {"type": "program_change", "t": t, "program": program}
                if bank is not None:
                    e["bank"] = bank
                events.append(e)
        elif typ == "note_on":
            pitch = _event_num(ev, "pitch", errs, q, lo=0, hi=127, integral=True)
            vel = _event_num(ev, "vel", errs, q, lo=1, hi=127, integral=True)
            if None in (pitch, vel):
                ok = False
            elif ok:
                events.append({"type": "note_on", "t": t, "pitch": pitch,
                               "vel": vel})
        else:                                           # note_off
            pitch = _event_num(ev, "pitch", errs, q, lo=0, hi=127, integral=True)
            vel = _event_num(ev, "vel", errs, q, lo=0, hi=127, integral=True,
                             default=0, optional=True)
            if pitch is None:
                ok = False
            elif ok:
                events.append({"type": "note_off", "t": t, "pitch": pitch,
                               "vel": vel if vel is not None else 0.0})

    # ---- XOR 门 + note 配对（PLAN_v3 §3.2 投影规范①②）
    note_events = [e for e in events if e["type"] in NOTE_EVENT_TYPES]
    if has_notes and note_events:
        errs.append(f"{p}：notes 与 note_on/note_off 事件互斥（XOR 规则——note 事件"
                    f"载体二选一，PLAN_v3 §3.2）")
    if not has_notes and not note_events:
        errs.append(f"{p}：轨必须能发声——notes 与 note 事件二者必须恰有其一（XOR）")
    pending = {}
    ordered = sorted(((e, j) for j, e in enumerate(events)
                      if e["type"] in NOTE_EVENT_TYPES),
                     key=lambda x: (x[0]["t"], x[1]))
    for e, j in ordered:
        q = f"{p}.events[{j}]"
        pitch = int(e["pitch"])
        if e["type"] == "note_on":
            if pitch in pending:
                errs.append(f"{q}：同 pitch {pitch} 交叠（前一 note_on 未 off 又来"
                            f"同 pitch note_on；复音请用不同 pitch）")
            else:
                pending[pitch] = (j, q)
        elif pitch not in pending:
            errs.append(f"{q}：无对应 note_on 的 note_off（悬空 off）")
        else:
            del pending[pitch]
    for j, q in pending.values():
        errs.append(f"{q}：悬空 note_on（缺配对 note_off）")

    # ---- 事件能力门（描述符 events_supported；PLAN_v3 §3.1 消费矩阵）
    try:
        allowed = {e["type"] for e in get_descriptor(engine).get("events_supported") or []}
    except EngineError as e:
        allowed = None
        errs.append(f"{p}.events：引擎 {engine} 描述符不可用（{e}）")
    if allowed is not None:
        for e in events:
            if e["type"] not in allowed:
                errs.append(f"{p}.events：引擎 {engine} 不支持 {e['type']} 事件"
                            f"（描述符 events_supported={sorted(allowed)}；"
                            f"PLAN_v3 §3.2 消费矩阵）")
    return events


def _validate_track_params(tr, engine, errs, p, is_v2):
    """v2 track.params 校验（PLAN_v3 §3.6）：键 ∈ 该轨引擎描述符 parameters ids
    减去投影锁定集；值逐键 base.validate_param_value（EngineError 带参数名）。
    报错带字段路径 tracks[i].params.<key>。返回合法 params dict。"""
    if not is_v2:
        return {}
    params = tr.get("params")
    if params is None:
        return {}
    if not isinstance(params, dict):
        errs.append(f"{p}.params：必须是 object（键=描述符参数 id 减投影锁定集，"
                    f"PLAN_v3 §3.6）")
        return {}
    try:
        ids = {pa["id"] for pa in get_descriptor(engine).get("parameters") or []}
    except EngineError as e:
        errs.append(f"{p}.params：引擎 {engine} 描述符不可用（{e}）")
        return {}
    out = {}
    for k, v in params.items():
        q = f"{p}.params.{k}"
        if not isinstance(k, str) or not k:
            errs.append(f"{p}.params：键必须是非空字符串，得到 {k!r}")
            continue
        if k not in ids:
            errs.append(f"{q}：未知参数（{engine} 描述符 parameters 无此项；"
                        f"合法={sorted(ids - PROJECTION_LOCKED_PARAMS)}）")
            continue
        if k in PROJECTION_LOCKED_PARAMS:
            errs.append(f"{q}：投影锁定字段不可经 params 覆盖"
                        f"（track_projection 映射所产出的字段/结构必填字段，"
                        f"PLAN_v3 §3.6）")
            continue
        try:
            validate_param_value(engine, k, v)
        except EngineError as e:
            errs.append(f"{q}：{e}")
            continue
        out[k] = v
    # synth 轨嵌套 params（payload.params=synthlib kwargs）键集随 track.instrument
    # （=program）变——工程层同源校验（R2 审查 B·A2：编辑层不得写出渲染必失败的工程）
    if engine == "synth" and isinstance(out.get("params"), dict):
        allowed = set(synth_program_param_keys(tr.get("instrument")))
        unknown = set(out["params"]) - allowed
        if unknown:
            errs.append(f"{p}.params.params：program {tr.get('instrument')!r} 不接受键 "
                        f"{sorted(unknown)}（合法：{sorted(allowed)}）")
            out.pop("params", None)
    return out


def _validate_track_bend_range(tr, engine, events, errs, p, is_v2):
    """v2 track.bend_range 校验（PLAN_v3 §3.2）：数>0；sfz 轨含 pitch_bend 事件
    必须显式声明（SFZ 文件级 pitch_wheel_range 逐文件不同）；sf2 缺省 2（引擎层
    默认，payload 不注入）。返回显式值或 None。"""
    if not is_v2:
        return None
    br = tr.get("bend_range")
    if br is not None:
        if not isinstance(br, (int, float)) or isinstance(br, bool) or br <= 0:
            errs.append(f"{p}.bend_range：必须是正数（半音量程），得到 {br!r}")
            return None
        return float(br)
    if engine == "sfz" and any(e["type"] == "pitch_bend" for e in events):
        errs.append(f"{p}.bend_range：sfz 轨含 pitch_bend 事件必须显式声明"
                    f"（SFZ 文件级 pitch_wheel_range 逐文件不同；PLAN_v3 §3.2）")
    return None


def _validate_track_space(tr, errs, p, is_v2):
    """v3.2 track.space 校验（空间化轮，PLAN_v3 机制模式沿用 pre_limit 四件套）。

    闭集 {depth, width, send_db}（未知键拒绝）；depth 必填 0..1（0=台前 1=厅尾）；
    width 0..1 缺省 0.3（side 增益 = width/0.3，0.3=中性）；send_db -60..12
    缺省按 depth 推导（mixer 层 -12+depth×9，core.space.depth_default_send_db）。
    null = 显式关（与 pre_limit 同惯例）；send_db 仅显式给出才入键（save 不落
    null）。v1 拒绝由 V2_ONLY_TRACK_KEYS 版本前置门承担。返回规范 dict 或 None。
    """
    if not is_v2:
        return None
    if "space" not in tr:
        return None
    sp = tr["space"]
    if sp is None:
        return None                                # null = 显式关（=缺省）
    if not isinstance(sp, dict):
        errs.append(f"{p}.space：必须是 object（{{depth, width?, send_db?}}）")
        return None
    unknown = set(sp) - SPACE_KEYS
    if unknown:
        errs.append(f"{p}.space：未知键 {sorted(unknown)}（允许 {sorted(SPACE_KEYS)}）")
    if "depth" not in sp:
        errs.append(f"{p}.space.depth：必填（0=台前 1=厅尾，v3.2 空间化轮）")
    depth = _num(sp, "depth", errs, path=f"{p}.space", lo=0.0, hi=1.0)
    if depth is None:
        return None
    width = _num(sp, "width", errs, path=f"{p}.space", lo=0.0, hi=1.0,
                 default=0.3)
    out = {"depth": depth, "width": 0.3 if width is None else width}
    send_db = _num(sp, "send_db", errs, path=f"{p}.space", lo=-60, hi=12)
    if send_db is not None:
        out["send_db"] = send_db
    return out


def events_to_notes(events):
    """note 事件对 → notes 四元组投影（PLAN_v3 §3.2 投影规范：组装层合成）。

    前置：校验层已保证无交叠/悬空（配对无歧义）。vel=note_on 的 vel（1..127）；
    dur=t_off-t_on；pitch 取 note_on。输出按 note_on 时间升序（规范形态，与
    notes[] 惯例一致）。show 层"notes=note 事件的视图"复用。
    """
    out, pending = [], {}
    ordered = sorted(((e, j) for j, e in enumerate(events)
                      if e.get("type") in NOTE_EVENT_TYPES),
                     key=lambda x: (x[0]["t"], x[1]))
    for e, _j in ordered:
        pitch = int(e["pitch"])
        if e["type"] == "note_on":
            pending[pitch] = (e["t"], e["vel"])
        else:
            t_on, vel = pending.pop(pitch, (None, None))
            if t_on is not None:
                out.append((float(t_on), [float(pitch), float(t_on),
                                          float(e["t"]) - float(t_on), float(vel)]))
    return [n for _, n in sorted(out)]


def track_cc_curve(track, ctrl):
    """cc 车道投影核心函数（PLAN_v3 §3.2"车道=投影"）：track.events → [(t, value)]
    按 ctrl 过滤、t 升序（同 tick 保插入序）；R3 show cc 的消费接口。
    返回原始 value（编码值由 midi_util.round_half_up 换算，show 层兑现）。"""
    pairs = [(e, i) for i, e in enumerate(track.get("events") or [])
             if e.get("type") == "cc" and int(e.get("ctrl", -1)) == ctrl]
    pairs.sort(key=lambda x: (x[0]["t"], x[1]))
    return [(e["t"], e["value"]) for e, _ in pairs]


# --------------------------------------------------------------- 校验小工具
# 每个 _xxx 返回取值（含默认）并把错误追加进 errs；路径标注让报错可定位。

def _obj(d, key, errs, required=False):
    v = d.get(key)
    if v is None:
        if required:
            errs.append(f"{key}：缺失（必填）")
        return None
    if not isinstance(v, dict):
        errs.append(f"{key}：必须是 object")
        return None
    return v


def _str(d, key, errs, path=None, default=None, allow_empty=True):
    v = d.get(key, default)
    if v is None and not allow_empty:
        errs.append(f"{path or key}：缺失或为空")
        return None
    if v is None:
        return None
    if not isinstance(v, str) or (not allow_empty and not v.strip()):
        errs.append(f"{path or key}：必须是非空字符串")
        return None
    return v


def _unknown(d, allowed, errs, path):
    """未知字段报错（防拼错静默吃默认值——R3 审查 MINOR-1）。"""
    for k in d:
        if k not in allowed:
            errs.append(f"{path}：未知字段 {k!r}（允许：{sorted(allowed)}）")


def _num(d, key, errs, path=None, lo=None, hi=None, default=None):
    p = f"{path}.{key}" if path else key
    v = d.get(key, default)
    if v is None:
        return None
    if not isinstance(v, (int, float)) or isinstance(v, bool) \
            or not math.isfinite(v):
        errs.append(f"{p}：必须是（有限）数值，得到 {v!r}")
        return None
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        errs.append(f"{p}：{v} 越界（合法 {lo}..{hi}）")
        return None
    return float(v)


def _int(d, key, errs, path=None, lo=None, hi=None, default=None):
    v = _num(d, key, errs, path=path, lo=lo, hi=hi, default=default)
    return None if v is None else int(v)


# ------------------------------------------------------------ $ref / 写回 / 双布局

def _read_ref_track(base_dir, ref):
    """读 $ref 外部轨文件 → (track_dict, None) 或 (None, 错误消息)。

    track_dict 带派生键 _ref_src=ref（来源映射；校验报错路径据此附加标注）。
    规则（PLAN_v3 C5）：相对工程文件所在目录解析、禁绝对路径、
    外部文件内不得再嵌 $ref。
    """
    if not isinstance(ref, str) or not ref.strip():
        return None, f"必须是相对路径字符串，得到 {ref!r}"
    if os.path.isabs(ref):
        return None, f"{ref!r} 不允许绝对路径（仅相对工程文件所在目录）"
    ext_path = os.path.normpath(os.path.join(base_dir, ref))
    base_real = os.path.realpath(base_dir)
    if not os.path.realpath(ext_path).startswith(base_real + os.sep):
        return None, f"{ref!r} 越出工程目录（仅允许工程目录内相对路径）"
    try:
        with open(ext_path, encoding="utf-8") as f:
            ext = json.load(f)
    except json.JSONDecodeError as e:
        return None, f"外部文件 {ref!r} JSON 解析失败：{e}"
    except OSError as e:
        return None, f"外部文件 {ref!r} 读取失败：{e}"
    if not isinstance(ext, dict):
        return None, f"外部文件 {ref!r} 顶层必须是完整 track object"
    if "$ref" in ext:
        return None, f"外部文件 {ref!r} 内不得再嵌 $ref（禁止嵌套）"
    ext["_ref_src"] = ref
    return ext, None


def _atomic_replace(path, write):
    """原子写：tmp 放目标同目录 + os.replace。

    /tmp 与产物目录可能跨设备，os.replace 会 EXDEV（demucs.py:51 先例）——
    tmp 必须与目标同目录；替换后权限沿用旧文件（缺省 0644）。
    """
    path = os.path.abspath(path)
    dirn = os.path.dirname(path)
    os.makedirs(dirn, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".bgm_tmp_", dir=dirn)
    try:
        with os.fdopen(fd, "wb") as f:
            write(f)
        if os.path.exists(path):
            os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
        else:
            os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def _atomic_write_bytes(path, blob):
    return _atomic_replace(path, lambda f: f.write(blob))


def _atomic_write_json(path, data):
    def _dump(f):
        f.write((json.dumps(data, ensure_ascii=False, indent=2) + "\n")
                .encode("utf-8"))
    return _atomic_replace(path, _dump)


# 序列化白名单：键序 = schema 属性序（规范化口径）；派生键（bank_explicit/
# program_explicit/clip_explicit/_ref_src/_ref_base_dir/_uses_refs）一律过滤。
_SAVE_KEYS = {
    "meta": ("spec_version", "sr", "channels", "bpm", "key", "timesig", "seed",
             "duration_hint"),
    "track": ("name", "engine", "instrument", "sf2", "bank", "program", "notes",
              "events", "bend_range", "params", "gain_db", "pan", "space",
              "clip", "bus"),
    "clip": ("start_beat", "end_beat"),
    "section": ("name", "start_beat", "length_beat"),
    "automation": ("target_track", "param", "points", "curve"),
    "sidechain": ("trigger_track", "targets", "threshold_db", "ratio",
                  "attack_ms", "release_ms"),
    "master": ("target_lufs", "true_peak_db", "chain", "ref", "lufs_tol",
               "pre_limit", "space_ir", "qc"),
    "qc": ("silent_window_max_s", "bpm_tol_pct", "key_top2", "hf_ratio_max",
           "duration_min_s"),
}
_TOP_KEYS = ("name", "meta", "tracks", "sections", "automation", "sidechain",
             "master")


def _pick(d, keys):
    return {k: d[k] for k in keys if k in d}


def _serializable_track(t):
    """track dict → 序列化形态；$ref 槽位原样保留（布局保全）。

    语义保全细节：bank/program 仅在原始工程显式给出时落盘——否则省略，
    避免默认值 0/0 落盘后于重装载被当作"显式"、进而压过 instrument 的
    "sf2名:bank:program" 速记（track_engine_spec 的显式优先规则）。
    """
    if isinstance(t, dict) and set(t) == {"$ref"} and isinstance(t["$ref"], str):
        return {"$ref": t["$ref"]}
    d = _pick(t, _SAVE_KEYS["track"])
    # 显式信号：load 派生 dict 以 bank_explicit/program_explicit 标志为准；
    # merge 展开的外部文件原始 dict 无派生键，以"键在场"为显式信号
    #（R1 审查官 A·D-1 修复：否则 merge→save 丢显式值 → 位级漂移）。
    if not t.get("bank_explicit", "bank" in t):
        d.pop("bank", None)
    if not t.get("program_explicit", "program" in t):
        d.pop("program", None)
    clip = t.get("clip")
    if isinstance(clip, dict):
        d["clip"] = _pick(clip, _SAVE_KEYS["clip"])
    return d


def _serializable_project(proj):
    """loaded proj dict → 序列化形态（过滤派生键、键序按 schema 属性序）。"""
    out = {k: proj[k] for k in _TOP_KEYS if k in proj}
    out["meta"] = _pick(out.get("meta") or {}, _SAVE_KEYS["meta"])
    out["tracks"] = [_serializable_track(t) for t in proj.get("tracks") or []]
    out["sections"] = [_pick(s, _SAVE_KEYS["section"])
                       for s in proj.get("sections") or []]
    out["automation"] = [_pick(a, _SAVE_KEYS["automation"])
                         for a in proj.get("automation") or []]
    sc = proj.get("sidechain")
    if sc:
        out["sidechain"] = _pick(sc, _SAVE_KEYS["sidechain"])
    mst = proj.get("master")
    if mst:
        out["master"] = _pick(mst, _SAVE_KEYS["master"])
        if isinstance(out["master"].get("qc"), dict):
            out["master"]["qc"] = _pick(out["master"]["qc"], _SAVE_KEYS["qc"])
        if out["master"].get("ref") is None:
            del out["master"]["ref"]             # loudnorm 模式不落 "ref": null
    return out


def save_project(path, project):
    """工程写回器（v3 首个；PLAN_v3 §3.3 / BG-D12 草案）。

    键白名单过滤派生键（bank_explicit/program_explicit/clip_explicit/_ref_src
    等内部键）；键序按 schema 属性序、indent=2、ensure_ascii=False——连续两次
    save 字节相等；原子写 = tmp 放目标同目录 + os.replace。
    布局保全：$ref 槽位原样写回主文件（外部轨文件由 split_layout 维护）。
    """
    if not isinstance(project, dict):
        raise ProjectError("save_project：工程必须是 dict（先 load_project）")
    _atomic_write_json(path, _serializable_project(project))
    return path


def _slugify(name):
    """track.name → 文件 slug（字母/数字/CJK 保留，其余归并成单 -）。"""
    s = re.sub(r"-{2,}", "-", "".join(
        c if c.isalnum() else "-" for c in str(name).lower())).strip("-")
    return s or "track"


def split_layout(project, project_path, tracks_dir):
    """内联轨 → $ref 双布局（in-place，返回 project；PLAN_v3 §3.3）。

    每内联轨序列化（键白名单）写入 tracks_dir/<slug>.json（slug 由 track.name
    派生，重名加 -2/-3…），槽位替换为 {"$ref": "<相对工程目录的路径>"}；
    已有 $ref 槽位原样保留。$ref 是 v2 能力（v1 前置门拒 $ref），故
    spec_version 非 "2" 时自动提升为 "2"（v1 零迁移、渲染语义不变）。
    """
    if not isinstance(project, dict) or not isinstance(project.get("tracks"), list):
        raise ProjectError("split_layout：工程必须含 tracks 数组（先 load_project）")
    base_dir = os.path.dirname(os.path.abspath(project_path))
    tracks_abs = tracks_dir if os.path.isabs(tracks_dir) \
        else os.path.join(base_dir, tracks_dir)
    os.makedirs(tracks_abs, exist_ok=True)
    meta = project.setdefault("meta", {})
    if str(meta.get("spec_version", PROJECT_SPEC_VERSION)) != "2":
        meta["spec_version"] = "2"
    used, new_tracks = {}, []
    for t in project["tracks"]:
        if isinstance(t, dict) and set(t) == {"$ref"}:
            new_tracks.append(t)
            continue
        slug = _slugify((t or {}).get("name"))
        if slug in used:
            used[slug] += 1
            slug = f"{slug}-{used[slug]}"
        else:
            used[slug] = 1
        file_abs = os.path.join(tracks_abs, f"{slug}.json")
        _atomic_write_json(file_abs, _serializable_track(t))
        ref = os.path.relpath(file_abs, base_dir).replace(os.sep, "/")
        new_tracks.append({"$ref": ref})
    project["tracks"] = new_tracks
    project["_ref_base_dir"] = base_dir
    return project


def merge_layout(project):
    """$ref 槽位反向展开为内联轨（in-place，返回 project；PLAN_v3 §3.3）。

    解析基准 = 派生键 _ref_base_dir（load_project / split_layout 注入）；
    展开规则与装载层一致（相对路径/禁绝对/禁嵌套，见 _read_ref_track）。
    """
    if not isinstance(project, dict) or not isinstance(project.get("tracks"), list):
        raise ProjectError("merge_layout：工程必须含 tracks 数组")
    base = project.get("_ref_base_dir")
    if not base:
        raise ProjectError("merge_layout：工程缺 _ref_base_dir"
                           "（请经 load_project 装载或经 split_layout 拆分）")
    errs = []
    for i, t in enumerate(project["tracks"]):
        if not (isinstance(t, dict) and set(t) == {"$ref"}):
            continue
        ext, err = _read_ref_track(base, t["$ref"])
        if err:
            errs.append(f"tracks[{i}].$ref：{err}")
        else:
            project["tracks"][i] = ext
    if errs:
        raise ProjectError("merge_layout 失败：\n  " + "\n  ".join(errs))
    return project


# ------------------------------------------------------------ 渲染编排计划

def build_render_plan(proj):
    """补全派生量：spb、每轨 clip 秒窗、工程总时长、stem 路径规划。"""
    m = proj["meta"]
    spb = 60.0 / m["bpm"]
    dur_s = m["duration_hint"] or 0.0
    plan_tracks = []
    for t in proj["tracks"]:
        c = t["clip"]
        start_s, end_s = c["start_beat"] * spb, c["end_beat"] * spb
        dur_s = max(dur_s, end_s)
        plan_tracks.append(dict(t, start_s=start_s, end_s=end_s,
                                window_s=end_s - start_s))
    return {"spb": spb, "duration_s": dur_s, "tracks": plan_tracks}


def _projection_source(value):
    """track_projection 映射值 → ('track', field) | ('meta', field) | None。

    值形态（harvest ENGINE_FACTS 约定，PLAN_v3 §3.1）：
        "track.instrument" / "meta.bpm"                        直接投影
        "track.window_s（clip×spb 派生量——钩子②）"             注释段剥离
        "—（缺省按 bank 派生）"                                 不投影（引擎内派生/仅 params 可达）
    """
    if not isinstance(value, str):
        return None
    head = value.split("（", 1)[0].strip()
    if head in ("", "—"):
        return None
    if head.startswith("track."):
        return ("track", head[len("track."):])
    if head.startswith("meta."):
        return ("meta", head[len("meta."):])
    return None


def _hook_sf2_instrument_shorthand(payload, track):
    """钩子①（四类封闭钩子，PLAN_v3 §3.1）：sf2 instrument "名:bank:program"
    速记解析；显式 bank/program 字段优先（显式优先与 v1 if/elif 同惯例）。
    行为逐语义一致于 v1 原实现（重组装深比较锁 tests/test_events.py）。"""
    sf2_name = payload.get("sf2")
    if not isinstance(sf2_name, str) or sf2_name.count(":") != 2:
        return
    name_part, b_s, p_s = sf2_name.rsplit(":", 2)
    try:
        b_i, p_i = int(b_s), int(p_s)
    except ValueError:
        raise ProjectError(
            f"轨 {track.get('name')}：instrument 速记 {sf2_name!r} 的 bank/program "
            f"段不是整数") from None
    if 0 <= b_i <= 128 and 0 <= p_i <= 127:
        payload["sf2"] = name_part
        if not track.get("bank_explicit"):
            payload["bank"] = b_i
        if not track.get("program_explicit"):
            payload["program"] = p_i


def track_engine_spec(track, proj, stem_path):
    """工程轨 → run_engine envelope（v3 R2 数据化：PLAN_v3 §3.1 track_projection）。

    if/elif 退役为：描述符 track_projection 数据映射（"track.x"/"meta.x" 源表达式，
    "—"=不投影，源键缺失即跳过）+ 四类封闭钩子（新增钩子须主线批准）：
        ① sf2 instrument 速记解析（_hook_sf2_instrument_shorthand，显式优先）
        ② sample length_s=clip×spb 派生量（build_render_plan 注入 track.window_s，
           经映射 "track.window_s" 投影——与 v1 派生公式同源）
        ③ meta 注入 bpm/sr（bpm 经映射；sr/channels 在 envelope 层）
        ④ params 覆盖合并（投影产出 → params 覆盖 → 结构字段锁定；
           锁定=校验层 PROJECTION_LOCKED_PARAMS）
    note 事件载体（XOR 下 notes 必空）→ payload.notes 合成（events_to_notes）；
    控制事件 → payload.events（非空才注入——无 events 保持旧路径字节不动）。
    """
    m = proj["meta"]
    eng = track["engine"]
    desc = get_descriptor(eng)
    tp = desc.get("track_projection") or {}
    if not tp:                                          # 与 v1 if/elif else 分支同口径双保险
        raise ProjectError(f"轨 {track['name']}：引擎 {eng} 无 track_projection"
                           f"（不是工程轨引擎）")
    payload = {}
    for pid, src in tp.items():
        if pid in ("hooks", "note"):
            continue
        parsed = _projection_source(src)
        if parsed is None:
            continue
        kind, field = parsed
        if kind == "track" and field in track:
            payload[pid] = track[field]
        elif kind == "meta" and field in m:
            payload[pid] = m[field]
    if eng == "sf2":
        _hook_sf2_instrument_shorthand(payload, track)      # 钩子①
    events = track.get("events") or []
    note_evs = [e for e in events if e["type"] in NOTE_EVENT_TYPES]
    control_evs = [e for e in events if e["type"] not in NOTE_EVENT_TYPES]
    if note_evs:                                            # 投影规范：组装层合成
        payload["notes"] = events_to_notes(note_evs)
    if control_evs:
        payload["events"] = control_evs
    else:
        payload.pop("events", None)   # 投影映射曾写入全量 events；无控制事件时
                                      # 必须移除（否则 note 载体经 events 通道重复编码）
    params = track.get("params")
    if params:
        payload.update(params)                              # 钩子④（显式优先）
    return {"engine": eng, "spec_version": "1", "sr": m["sr"],
            "channels": m["channels"], "out": stem_path, "payload": payload}


def _same_path(a, b):
    try:
        return os.path.abspath(a) == os.path.abspath(b) or (
            os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b))
    except OSError:
        return os.path.abspath(a) == os.path.abspath(b)


def _read_json_or_none(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def render_project(project_path, out_dir=None, quiet=False, incremental=False, runtime=None):
    """project.json → stems → BG-D5 归一 → 混音总线 → 母带 → mix.flac + qc.json。

    输出：显式 out_dir 或 <调用者 cwd>/out/<name>/{stems/<track>.<engine>.wav, mix.flac, project.json,
    qc.json}（另留中间产物 mix_premaster.wav / mix_master.wav 供排查）。

    v3（PLAN_v3 §3.3）：
        - SameFileError 修复：工程正本已在产物目录时快照复制跳过（同路径检测）；
          其余快照经同目录 tmp + os.replace 原子写（跨设备坑见 demucs.py:51）。
        - 快照口径：v1/内联工程保持 copyfile 字节保真；$ref 工程 = 内联展开
          单文件（save_project 序列化，正本不动）。
        - 增量渲染：incremental=True 显式开启（缺省全量 = 现状行为）；
          BGM_NO_INCREMENTAL=1 为硬闸（读取点在 render_project 内部、赢过
          flag、输出一行日志）——in-process 调用者同受保护。
          每轨 hash=content_hash_key(engine, 组装后 payload, sr, channels)，
          命中边车 <track>.<engine>.render.json（{content_hash, summary:
          {sr,channels,duration_s,rms_db}}）且 wav 存在 → 跳过重渲（日志明示
          "skip re-render"）、返回值从边车 summary 回填；渲染成功先 stem
          （引擎写入）后边车（本函数写入），固定顺序——崩溃窗口最多多渲一次；
          mix/master 恒重跑。
    """
    from .mixer import mix_project
    from .qc import qc_report
    from ..engines.base import ENGINES, content_hash_key, load_runtime
    from ..paths import effective_runtime

    runtime = load_runtime() if runtime is None else effective_runtime(runtime)

    proj = load_project(project_path)
    name = proj["name"] or "project"
    out_dir = os.path.abspath(out_dir or default_out(name))
    stems_dir = os.path.join(out_dir, "stems")
    os.makedirs(stems_dir, exist_ok=True)

    if incremental and os.environ.get("BGM_NO_INCREMENTAL") == "1":
        incremental = False
        print("· BGM_NO_INCREMENTAL=1 硬闸生效：忽略 --incremental（按全量渲染）")

    plan = build_render_plan(proj)

    # 1) stems：资源感知并行渲染（v3.4；确定性 CPU 引擎。GPU 引擎按 BG-D10
    #    不进工程轨——TRACK_ENGINES 校验已保证；防御性兜底见 1c）。
    #    1a 主线程：spec 组装 + content_hash + 增量命中判定（hash 计算保持在
    #       派发前主线程；命中跳过的轨不占并发槽）；
    #    1b 池化：CPU 引擎经 resources.run_pool（ThreadPoolExecutor +
    #       DispatchGate 内存门控；真正的并行发生在引擎的子进程/ctypes 里，
    #       GIL 不挡）；
    #    1c 顺序兜底：非 CPU 池引擎（理论不可达）单独顺序执行——GPU 引擎
    #       （demucs/ai）绝不进池：gpu_guard（BG-D8）已有本包内互斥，池化
    #       会让多任务同时抢显存。
    #    每轨 worker 体 = 旧循环体逐句等价（渲染→时长门→边车"先 stem 后
    #    边车"固定顺序在单轨内保持）；结果按轨序收集，与完成顺序无关——
    #    输出文件路径不变。
    from . import resources as _res

    stem_results = {}
    pending = []                                  # (track, spec, hash_key)
    for t in plan["tracks"]:
        stem_path = os.path.join(stems_dir, f"{t['name']}.{t['engine']}.wav")
        sidecar_path = os.path.splitext(stem_path)[0] + ".render.json"
        spec = track_engine_spec(t, proj, stem_path)
        hash_key = content_hash_key(spec["engine"], spec["payload"],
                                    spec["sr"], spec["channels"])
        sidecar = _read_json_or_none(sidecar_path)
        hit = (incremental and isinstance(sidecar, dict)
               and sidecar.get("content_hash") == hash_key
               and isinstance(sidecar.get("summary"), dict)
               and all(k in sidecar["summary"]
                       for k in ("sr", "channels", "duration_s", "rms_db"))
               and os.path.isfile(stem_path))
        if hit:
            if not quiet:
                print(f"· stem {t['name']}（{t['engine']}）skip re-render（hash 命中）"
                      f"→ {os.path.relpath(stem_path, out_dir)}")
            s = sidecar["summary"]
            stem_results[t["name"]] = {
                "path": stem_path, "sr": s["sr"], "channels": s["channels"],
                "duration_s": s["duration_s"], "rms_db": s["rms_db"],
                "engine": t["engine"],
                "determinism": getattr(ENGINES.get(t["engine"]),
                                       "determinism", "unknown")}
        else:
            if not quiet:
                print(f"· stem {t['name']}（{t['engine']}）→ {os.path.relpath(stem_path, out_dir)}")
            pending.append((t, spec, hash_key))

    def _render_stem(item):
        """单轨 stem 渲染 worker（旧循环体逐句等价；线程内无共享可变状态）。"""
        t, spec, hash_key = item
        try:
            res = run_engine(spec, runtime=runtime)
        except Exception as e:
            raise ProjectError(f"轨道 {t['name']!r}（{t['engine']}）渲染失败：{e}") from e
        # stem 时长门（v3 R4 冻机事故的第二道护栏）：stem 可略超工程时长
        # （音符尾+释放尾，如 minimal strings 8.1s vs 工程 4s），但不得
        # 离谱——无限循环类失控必须在这里挡下，不得进入混音/QC。
        _dur_limit = plan["duration_s"] + 60.0
        if res["duration_s"] > _dur_limit:
            raise ProjectError(
                f"轨道 {t['name']!r}（{t['engine']}）stem 时长 "
                f"{res['duration_s']:.1f}s 超出工程时长+60s 上限"
                f"（{_dur_limit:.1f}s）——疑似音色无限循环或数据异常，拒绝混入")
        # 固定顺序：stem 已由引擎写入，成功后才写边车（崩溃窗口不假命中）。
        # 边车仅在增量模式下写：缺省渲染不污染产物目录（R1 审查 B-1）。
        if incremental:
            _atomic_write_json(
                os.path.splitext(spec["out"])[0] + ".render.json", {
                    "content_hash": hash_key,
                    "summary": {"sr": res["sr"], "channels": res["channels"],
                                "duration_s": res["duration_s"],
                                "rms_db": res["rms_db"]}})
        return t["name"], res

    pool_items = [it for it in pending
                  if it[0]["engine"] in _res.CPU_POOL_ENGINES]
    seq_items = [it for it in pending
                 if it[0]["engine"] not in _res.CPU_POOL_ENGINES]
    if pool_items:
        per_task = _res.max_per_task_gb(it[0]["engine"] for it in pool_items)
        workers = _res.plan_workers(len(pool_items), per_task)
        if not quiet and workers > 1:
            print(f"· stems 并行计划：{len(pool_items)} 任务 workers={workers}"
                  f" per_task≈{per_task:g}GB（cpu×{_res.DEFAULT_CPU_CAP_FACTOR:g}"
                  f" 地板 {_res.mem_floor_gb():.0f}GB 上限 {_res.DEFAULT_HARD_CAP}）")
        for name, res in _res.run_pool(pool_items, _render_stem,
                                       per_task_gb=per_task, workers=workers):
            stem_results[name] = res
    for it in seq_items:                          # GPU 引擎顺序兜底（理论不可达）
        name, res = _render_stem(it)
        stem_results[name] = res

    # 2) BG-D5 归一 + 混音总线 + 母带（mixer 内完成 adelay 平移/apad/atrim 定长）
    if not quiet:
        print(f"· mix 总线（{len(plan['tracks'])} 轨，工程时长 {plan['duration_s']:.3f}s）")
    mix_paths = mix_project(proj, plan, out_dir, runtime=runtime)

    # 3) mix.flac（交付格式，BG-D9）
    import subprocess
    flac = os.path.join(out_dir, "mix.flac")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mix_paths["master"],
                    "-c:a", "flac", flac], check=True)

    # 4) 工程快照 + QC（v3：SameFileError 修复 + $ref 内联展开快照）
    snapshot_path = os.path.join(out_dir, "project.json")
    if not _same_path(project_path, snapshot_path):
        if proj.get("_uses_refs"):
            save_project(snapshot_path, proj)      # $ref 工程：内联展开单文件（正本不动）
        else:
            with open(project_path, "rb") as f:    # v1/内联：copyfile 字节保真语义
                blob = f.read()
            _atomic_write_bytes(snapshot_path, blob)
    report = qc_report(flac, proj, runtime=runtime)
    with open(os.path.join(out_dir, "qc.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return {"name": name, "out_dir": out_dir, "mix": flac,
            "qc": os.path.join(out_dir, "qc.json"), "qc_ok": report["ok"],
            "duration_s": plan["duration_s"],
            "stems": {k: {"path": v["path"], "sr": v["sr"],
                          "duration_s": round(v["duration_s"], 3),
                          "rms_db": round(v["rms_db"], 2)}
                      for k, v in stem_results.items()},
            "premaster": mix_paths["premaster"], "master": mix_paths["master"],
            "mixer": mix_paths.get("summary", {})}
