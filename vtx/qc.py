"""vtx 渲染后质检（qc 阶段）：帧数/底轨切点帧比对/上层窗口生效/泄漏/响度归一/解码域真峰。

原 vtx.py qc 函数整体迁移（2026-09-10 模块化拆分），检查内容/顺序/名称与阈值不变。
方案三阶段1（2026-09-13）新增：响度检查 -vn+peak 并消费 loudness_cache 回执；
命中段免检（qc_pass_segs.json，见下方辅助函数注释）。
v2 §1.3（2026-09-13）新增：解码域真峰 TP 判定项（阈 -1.0 dBFS，与 gates G3 同一
权威值、同一回执不重测）——limiter 恒在 build_audio 尾部后，成片 TP 超阈 = 上游
削波/限幅失效，属产物可信度问题，归内核 qc。
"""
import json, re, sys, tempfile
from contextvars import copy_context
import operator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import loudness_cache, fstxn
from .diagnostics import (emit, producer_exit, required_field, operation,
                          key_path, subtitle_location)
from .hygiene import gfx_dir   # R-05 探针口径对齐：gfx 内容域 bbox 需定位 png 序列目录

# ---- QC 阈值命名常量（值不变，仅命名；2026-09-10 收拢）----
QC_CUT_MEAN_MAX = 6.0        # 底轨切点帧比对：平均像素差上限
QC_WINDOW_MEAN_MIN = 5.0     # 上层窗口生效：平均像素差下限（或覆盖率达到下限即生效）
QC_WINDOW_COVER_MIN = 0.03   # 上层窗口生效：覆盖率下限
QC_PIXEL_DIFF = 24           # 「显著差异像素」判定阈值（0-255）
QC_LEAK_MEAN_MAX = 2.0       # 空窗泄漏：平均像素差上限
QC_LEAK_COVER_MAX = 0.02     # 空窗泄漏：覆盖率上限
# 响度归一分级（P6 修复 B-P1-1，总控裁定 2026-09-14 对齐 §1.2）：|I-目标| >3
# 硬 FAIL（增益链断裂，与 build_audio 终态校验同一硬阈）；(1.5, 3] **放行带
# WARN 注记**——限幅响度损耗属内容级（发布侧内容门 G2 拦截口径），边界内容 +
# aac 解码域 0.1~0.3 LU 漂移不得让 `vtx all` 在 qc 处 flaky 翻车（audio 阶段
# WARN 放行而 qc ±1.5 硬 FAIL 的门内矛盾就此消解）；≤1.5 静默通过（v1 语义）。
QC_LOUDNESS_TOL = 1.5        # 响度归一容差（LUFS，≤ 此值静默通过）
QC_LOUDNESS_FAIL = 3.0       # 响度归一硬 FAIL 阈（> 此值，对齐 build_audio）
TARGET_LUFS = -14            # 项目目标响度（EBU R128，与 stages.build_audio 同一权威值）
QC_TP_MAX = -1.0             # 解码域真峰阈（dBFS，v2 §1.3：G3 的 -1.0 为唯一权威）

# ---- QC 命中段免检记录（方案三阶段1，2026-09-13）----
# 记录 cache/qc_pass_segs.json：{"version":1,"final":{size,mtime_ns,frames},
# "segs":[{name,a,b,key,size,mtime_ns}],"checks":{检查名:上轮是否 pass}}。
# 免检判定（非 --strict，见 qc() 内）：final (size,mtime_ns) 与记录一致 + 检查项
# 上轮 pass + 探测帧全部落在 stat 一致的记录段 [a,b) 内 → 跳过实际探测（仍占
# checks 位，pass=True，detail 前缀「免检」——检查项数量不减少）。final 重封装
# （stat 变）→ 记录整体失效全检；段文件被换/重写（stat 变）→ 该段失配实测。
# 全局元数据项（帧数/时长/分辨率/响度/并发窗能量）不在此机制内，永远实测。
# 裁定记录（与方案原文的偏差，总控未应答现场问询、子代理按验收一致性裁定）：
# 原文「全检全过才写记录，有 ✗ 删记录」在存在持续假阴性 ✗ 的项目（bench_p2 恒
# 74/75，c4_mini 已证伪探针假阴性）会使记录永不建立、机制永不生效，与验收要求
# 「74/75 不变 + 温态免检生效」矛盾——改为按检查项记忆：每轮刷新记录，✗ 项永远
# 实测（下轮必复测，持续失败持续可见），逐项内容绑定的安全性与全过门等价。
_QC_SEG_RECORD = "qc_pass_segs.json"
_VSEG_NAME = re.compile(r"^seg(\d+)_(\d{6})_([0-9a-f]{12})\.mp4$")


def _load_qc_pass_record(c):
    """读免检记录；缺失/损坏/version 不符/final stat 失配/segs 空 → None（全检）。"""
    try:
        rec = json.loads((c.cache / _QC_SEG_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("version") != 1:
        return None
    fin = rec.get("final") or {}
    try:
        st = (c.out / "final.mp4").stat()
    except OSError:
        return None
    ops = _QCOperations(c)
    source = {"provider": "qc_pass_record", "resource": str(c.cache / _QC_SEG_RECORD)}
    if ops.get(fin, "size", "$.final", source=source) != st.st_size or ops.get(fin, "mtime_ns", "$.final", source=source) != st.st_mtime_ns:
        return None   # final 重封装（stat 变）→ 记录整体失效
    if not isinstance(rec.get("segs"), list) or not rec["segs"] \
            or not isinstance(rec.get("checks"), dict):
        return None
    return rec


def _collect_active_segs(c):
    """当前生效段序列 → [{name,a,b,key,size,mtime_ns}]；不可得 → None（机制不生效）。
    段文件名只带段起点（seg{si}_{a:06d}_{key}.mp4），b = 下一段起点（末段 = c.dur）。
    以 cache/vseg/list.txt（最近一次段渲染的拼接清单）为准——直接 glob seg* 会把
    同槽旧键残留也当成生效段，产生重叠 [a,b) 区间：stat 未变的陈旧段可错误豁免
    被篡改的现役段所在帧（清单之外的一切不参与免检，见实现报告）。"""
    vdir = c.cache / "vseg"
    lst = vdir / "list.txt"
    if not lst.exists():
        return None
    names = []
    for line in lst.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^file '(.+)'$", line.strip())
        if m:
            names.append(Path(m.group(1)).name)
    parsed = []
    for nm in names:
        m = _VSEG_NAME.match(nm)
        if not m:
            return None   # 清单含非标准段名（理论不可达）→ 放弃，全检
        parsed.append((nm, int(m.group(2)), m.group(3)))
    if not parsed or parsed[0][1] != 0 \
            or any(parsed[i][1] >= parsed[i + 1][1] for i in range(len(parsed) - 1)):
        return None   # 段序列须从 0 起且起点严格递增（连续覆盖 [0,c.dur)）
    segs = []
    for i, (nm, a, key) in enumerate(parsed):
        try:
            st = (vdir / nm).stat()
        except OSError:
            return None
        segs.append({"name": nm, "a": a,
                     "b": parsed[i + 1][1] if i + 1 < len(parsed) else c.dur,
                     "key": key, "size": st.st_size, "mtime_ns": st.st_mtime_ns})
    return segs


def _write_qc_pass_record(c, frames_nb, checks):
    """每轮非 dry qc 结束刷新记录：segs/final 重新绑定当前 stat；checks 按检查名
    记忆（同名多项须全部 pass 才可免检——防同名 gfx 页一项假阴性被另一项真过
    掩盖成免检）。落盘失败不影响 qc 主流程（下轮全检而已）。"""
    mem = {}
    for x in checks:
        mem[x["name"]] = mem.get(x["name"], True) and bool(x["pass"])
    segs = _collect_active_segs(c) or []
    try:
        st = (c.out / "final.mp4").stat()
    except OSError:
        return
    rec = {"version": 1,
           "final": {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
                     "frames": frames_nb},
           "segs": segs, "checks": mem}
    try:
        fstxn.atomic_write(c.cache / _QC_SEG_RECORD,
                           json.dumps(rec, ensure_ascii=False, indent=1))
    except OSError:
        pass



# Only native, in-memory operations belong here. Context methods, filesystem
# reads and external providers execute outside these wrappers, so an opaque
# provider failure can never be relabeled as a bad timeline operand.
_QC_NATIVE = {
    "add": operator.add, "subtract": operator.sub, "multiply": operator.mul,
    "divide": operator.truediv, "floor_divide": operator.floordiv,
    "less_than": operator.lt, "less_equal": operator.le,
    "greater_equal": operator.ge, "greater_than": operator.gt,
    "getitem": operator.getitem, "contains": operator.contains,
    "get": lambda obj, key, default: obj.get(key, default),
    "int": int, "float": float, "str": str, "round": round,
    "min": min, "max": max, "len": len, "iter": iter, "range": range,
    "Path": Path, "json.loads": json.loads, "re.search": re.search,
}


def _qc_operand(value):
    """Diagnostic evidence must not invoke arbitrary object repr/iteration hooks."""
    if type(value) in (str, int, float, bool, type(None), list, tuple, dict):
        return value
    if type(value) in (type(Path()),):
        return str(value)
    return {"type": type(value).__name__}


class _QCOperations:
    def __init__(self, context):
        self.context = context

    def native(self, name, *values, path, paths=(), source=None):
        fn = _QC_NATIVE[name]
        evidence = []
        for i, value in enumerate(values):
            item = {"value": _qc_operand(value)}
            if i < len(paths):
                origin = paths[i]
                if isinstance(origin, tuple):
                    item["json_paths"] = list(origin)
                elif origin.startswith("provider:"):
                    item["provider_path"] = origin[len("provider:"):]
                elif origin.startswith("source:"):
                    item["source_field"] = origin[len("source:"):]
                elif origin.startswith("$"):
                    item["json_path"] = origin
                else:
                    item["role"] = origin
            evidence.append(item)
        operands = {"operation": name, "values": evidence}
        return operation(self.context, lambda: fn(*values), path=path,
                         rule="qc.metadata_operation" if source and "provider" in source else "qc.operation",
                         operands=operands, source=source,
                         status="unknown" if source and "provider" in source else "fail")

    def field(self, obj, key, parent, *, source=None):
        if source is None:
            if type(key) is int:
                return operation(self.context, lambda: obj[key], path=f"{parent}[{key}]",
                                 rule="qc.required_field", operands={"operation": "getitem", "key": key})
            return required_field(self.context, obj, key, parent,
                                  rule="qc.required_field")
        if "provider" in source:
            source = dict(source, provider_path=(f"{parent}[{key}]" if type(key) is int
                                                else key_path(parent, key)))
            return self.native("getitem", obj, key, path="$", paths=("provider:" + parent,), source=source)
        return operation(self.context, lambda: obj[key], path=parent,
                         rule="qc.required_field", source=dict(source, field=key),
                         operands={"operation": "getitem", "key": key})

    def get(self, obj, key, parent, default=None, *, source=None):
        if source is not None:
            source = dict(source, provider_path=key_path(parent, key))
        return self.native("get", obj, key, default, path="$" if source else parent,
                           paths=(("provider:" + parent) if source else parent,), source=source)

    def indexed(self, value, path, *, source=None):
        return enumerate(self.native("iter", value, path=path, source=source))

    def upper_tracks(self, tracks):
        return operation(self.context, lambda: tracks[1:], path="$.video_tracks",
                         rule="qc.operation", operands={"operation": "slice", "start": 1})

    def clips(self, track, path):
        clips = self.field(track, "clips", path)
        indices = {}
        observed = []
        ordinal = 0
        def key(clip):
            nonlocal ordinal
            index = ordinal
            ordinal += 1
            indices[id(clip)] = index
            clip_path = f"{path}.clips[{index}]"
            value = self.field(clip, "dst_in", clip_path)
            observed.append({"json_path": clip_path + ".dst_in", "value": _qc_operand(value)})
            return value
        ordered = operation(self.context, lambda: sorted(clips, key=key),
                            path=path + ".clips", rule="qc.operation",
                            operands={"operation": "sorted", "key": "dst_in", "values": observed})
        return [(indices[id(clip)], clip) for clip in ordered]



def qc(c):
    # dry-run 无产物可检（ffprobe 不在 dry 门控内，探测不存在的 final.mp4 会误报退出）
    # —— 整阶段跳过，真实运行时逐项检查（2026-09-11 门控）
    if getattr(c, "dry", False):
        print("  [dry-run] qc：产物未产出，跳过探测（命令预览模式不做质量检查）")
        emit(c, "qc：dry-run 跳过产物探测", rule_id="qc.dry_run",
             measured={"dry_run": {"value": True, "unit": "boolean"}},
             severity="info", status="skipped",
             suggested_actions=[{"op": "run_qc", "params": {"dry_run": False}}])
        return
    # 性能升级（2026-09-10，voice-ime 实测 qc 674s → 目标 <120s）三处病灶与对策：
    #   1) select=eq(n,N) 无输入 seek：每个探测点从片头解码到第 N 帧（多在 8000+ 帧），
    #      约 70 探测点 × 2 源串行 → 改 -ss 输入 seek（transcode 下帧精确）+ -frames:v 1；
    #   2) -count_frames 全片解码数帧 → 默认容器元数据（--strict 才解码计数）；
    #   3) 全片 qc_clean 基准轨 + 全部检查串行 → 探测帧现场合成底轨基准 +
    #      帧探测/ebur128/volumedetect 三组线程并行（墙钟 ≈ max）。
    ops = _QCOperations(c)
    native, field, get = ops.native, ops.field, ops.get
    rep = {"checks": []}
    def ok(name, passed, detail="", **diagnostic):
        rep["checks"].append({"name": name, "pass": bool(passed), "detail": detail})
        print(("  ✓ " if passed else "  ✗ ") + name + (f"  {detail}" if detail else ""))
        diagnostic.setdefault("severity", "info" if passed else "error")
        diagnostic.setdefault("status", "pass" if passed else "fail")
        emit(c, name + (f"  {detail}" if detail else ""), **diagnostic)
    skipped_probes = []   # qc_report.json 的 skipped_probes 字段素材（免检项+覆盖区间）
    def ok_exempt(name, span, **diagnostic):
        """免检项入账：照常占 checks 位（pass=True），detail 前缀「免检」——
        检查项数量与名称同全检，只是免实际探测。"""
        measured = dict(diagnostic.get("measured") or {})
        measured["exempt_segment"] = {"start": span[0], "end": span[1],
                                       "unit": "frames", "end_exclusive": True}
        diagnostic.update(measured=measured, status="skipped", severity="info",
                          suggested_actions=[{"op": "run_qc", "params": {"strict": True}}])
        ok(name, True, f"免检（段 [{span[0]},{span[1]}) 内容键命中）", **diagnostic)
        skipped_probes.append({"name": name, "seg": list(span)})
    final = c.out / "final.mp4"
    # 帧数/时长/分辨率：容器元数据（流头 nb_frames；缺失回落 duration×fps 四舍五入）。
    # -count_frames 逐帧解码全片实测数十秒，仅 --strict 时使用（真解码计数）。
    r = c.run(["ffprobe", "-v", "error", "-print_format", "json",
               "-select_streams", "v:0", "-show_entries", "stream=nb_frames,width,height",
               "-show_entries", "format=duration", str(final)])
    output_source = {"provider": "ffprobe", "resource": str(final)}
    d = native("json.loads", r.stdout, path="$", source=output_source)
    streams = field(d, "streams", "$", source=output_source)
    st = field(streams, 0, "$.streams", source=output_source)
    fmt = field(d, "format", "$", source=output_source)
    duration = field(fmt, "duration", "$.format", source=output_source)
    dur = native("float", duration, path="$", source=dict(
        output_source, provider_path="$.format.duration"))
    if getattr(c, "strict", False):
        r = c.run(["ffprobe", "-v", "error", "-count_frames", "-print_format", "json",
                   "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", str(final)])
        counted = native("json.loads", r.stdout, path="$", source=output_source)
        counted_streams = field(counted, "streams", "$", source=output_source)
        counted_stream = field(counted_streams, 0, "$.streams", source=output_source)
        read_frames = field(counted_stream, "nb_read_frames", "$.streams[0]", source=output_source)
        nb = native("int", read_frames, path="$", source=dict(
            output_source, provider_path="$.streams[0].nb_read_frames")); nb_src = "解码计数"
        nb_method = "decoded_frame_count"
    else:
        count_source = dict(output_source, provider_path="$.streams[0].nb_frames")
        nb0 = native("str", get(st, "nb_frames", "$.streams[0]", source=count_source) or "",
                     path="$", source=count_source)
        nb = (native("int", nb0, path="$", source=count_source)
              if (container_count := nb0.isdigit() and native("int", nb0, path="$", source=count_source) > 0)
              else native("round", native("multiply", dur, c.fps, path="$",
                          paths=("provider:$.format.duration", "$.project.fps"), source=output_source),
                          path="$", source=output_source))
        nb_src = "容器元数据"
        nb_method = ("container_frame_count"
                     if container_count
                     else "duration_fps_estimate")
    tracks = field(c.tl, "video_tracks", "$")
    bottom_gfx = bool(tracks) and get(field(tracks, 0, "$.video_tracks"), "kind", "$.video_tracks[0]") == "gfx"

    # ---- 帧提取原语：-ss 输入 seek + -frames:v 1（精确取帧）----
    # 关键正确性点：-ss 在 -i 前（输出流帧号从 0 重计）→ 不再保留 select=eq(n,N)，
    # 用输入 seek 时刻取帧；transcode 模式下输入 seek 为帧精确（从关键帧解码前向
    # 丢弃到目标时刻）。seek 语义 = 输出 pts ≥ 目标的首帧，故目标须落在
    # (pts[n-1], pts[n]] = ((n-1)/fps, n/fps]：取 (n-0.2)/fps（n=0 用 0）。
    # 已对 final/qc_clean/4 个源 × 多帧位验证与旧 select=eq(n,N) 提帧逐字节一致。
    def gray_frame(path: Path, n: int, pre_crop: str = "", tag: str = "", *, origin="$") -> bytes:
        vf = (f"{pre_crop}," if pre_crop else "") + "scale=48:27,format=gray"
        if n:
            offset = native("subtract", n, 0.2, path=origin)
            seconds = native("divide", offset, c.fps, path=origin,
                             paths=(origin, "$.project.fps"))
            seek = native("max", 0.0, seconds, path=origin)
            ss = f"{seek:.6f}"
        else:
            ss = "0"
        # Tags identify sample points, not unique concurrent calls. Each probe
        # owns its directory; equal frame/tag probes must never share output.
        temporary = tempfile.TemporaryDirectory(prefix="qc_probe_", dir=c.cache)
        tmp = Path(temporary.name) / "qc_probe_frame.raw"
        try:
            c.run(["ffmpeg", "-v", "error", "-y", "-ss", ss,
                   "-i", str(path), "-an", "-vf", vf, "-frames:v", "1", "-f", "rawvideo", str(tmp)])
            return tmp.read_bytes()
        finally:
            failed = sys.exc_info()[0] is not None
            try:
                temporary.cleanup()
            except Exception:
                if not failed:
                    raise  # A cleanup failure cannot replace a producer/I/O failure.
    # 底轨基准帧（等价于全片 qc_clean.mp4 在该帧的像素）：qc_clean 本就是 tracks[0]
    # 底轨 clip 全屏无叠层的第二遍合成，探测帧现场从源视频提帧（src_in + 帧偏移，
    # 输入 seek）过同一 crop169+scale 链即等价；无 clip 覆盖 → 黑帧（画布为黑场）。
    # 探测点均取 clip 中段，天然避开 fade 边界。
    _BLACK = bytes(48 * 27)
    def clip_end(cl, path):
        return native("add", field(cl, "dst_in", path), field(cl, "dur", path),
                      path=path, paths=(path + ".dst_in", path + ".dur"))

    def clip_mid(cl, path, divisor=2):
        return native("add", field(cl, "dst_in", path), native(
            "floor_divide", field(cl, "dur", path), divisor,
            path=path + ".dur"), path=path, paths=(path + ".dst_in", path + ".dur"))

    def clip_contains(cl, frame, path):
        return (native("less_equal", field(cl, "dst_in", path), frame,
                       path=path, paths=(path + ".dst_in", "sample_frame"))
                and native("less_than", frame, clip_end(cl, path), path=path,
                           paths=("sample_frame", (path + ".dst_in", path + ".dur"))))

    def bottom_frame(df: int, tag: str, post_crop: str = None, *, origin="$") -> bytes:
        if bottom_gfx:  # 底轨是 gfx（无源视频）：回落全片 qc_clean 基准轨
            return gray_frame(c.cache / "qc_clean.mp4", df,
                              pre_crop=f"crop={post_crop}" if post_crop else "", tag=tag,
                              origin=origin)
        for ci, cl in ops.clips(field(tracks, 0, "$.video_tracks"), "$.video_tracks[0]"):
            path = f"$.video_tracks[0].clips[{ci}]"
            if clip_contains(cl, df, path) and native("contains", cl, "src_in", path=path):
                sf = native("add", field(cl, "src_in", path), native(
                    "subtract", df, field(cl, "dst_in", path), path=path,
                    paths=("sample_frame", path + ".dst_in")), path=path,
                    paths=(path + ".src_in", (path + ".dst_in",)))
                # Context methods may fail independently; keep their exceptions opaque.
                pre = (f"{c.crop169(field(cl, 'src', path))},scale={c.w}:{c.h}:flags=lanczos"
                       + (f",crop={post_crop}" if post_crop else ""))
                return gray_frame(c.src_path(field(cl, "src", path)), sf,
                                  pre_crop=pre, tag=tag, origin=path)
        return _BLACK

    def mean_difference(a, b, origin, frame):
        total = sum(abs(x - y) for x, y in zip(a, b))
        count = len(a)
        return native("divide", total, count, path="$",
                      paths=("rawvideo.absolute_difference_sum", "rawvideo.sample_count"),
                      source={"provider": "ffmpeg", "resource": str(final),
                              "measurement": "rawvideo", "frame": frame, "origin": origin})

    def fdiff(n: int, crop: str = None, tag: str = "", *, origin="$.video_tracks"):
        a = gray_frame(final, n, pre_crop=f"crop={crop}" if crop else "", tag=tag + "a", origin=origin)
        b = bottom_frame(n, tag=tag + "b", post_crop=crop, origin=origin)
        mean = mean_difference(a, b, origin, n)
        changed = sum(1 for x, y in zip(a, b) if abs(x - y) > QC_PIXEL_DIFF)
        cover = native("divide", changed, len(a), path="$",
                       paths=("rawvideo.changed_samples", "rawvideo.sample_count"),
                       source={"provider": "ffmpeg", "resource": str(final),
                               "measurement": "rawvideo", "frame": n, "origin": origin})
        return mean, cover

    # ---- 命中段免检（方案三阶段1）：--strict 整体禁用；记录不可用 → 全检 ----
    # 判定在任务生成阶段做：免检项不 submit 探测但仍占 checks 位（输出顺序/名称
    # 与全检完全一致）。_seg_rec 由 _load_qc_pass_record 校验过 final stat 绑定。
    _seg_rec = None if getattr(c, "strict", False) else _load_qc_pass_record(c)
    exempt = {}    # 任务键 -> 覆盖段区间 (a,b)（免测项）
    cut_sf = {}    # cut 免测项的比对帧 sf（检查名要显示 src:帧号）
    def seg_span_of(f0):
        """f0 是否落在某 stat 与记录一致的段 [a,b) 内；是 → (a,b)，否 → None。"""
        if _seg_rec is None:
            return None
        provider = {"provider": "qc_pass_record", "resource": str(c.cache / _QC_SEG_RECORD)}
        for si, s in enumerate(_seg_rec["segs"]):
            record_path = f"$.segs[{si}]"
            if (native("less_equal", field(s, "a", record_path, source=provider), f0,
                       path="$", source=dict(provider, provider_path=record_path + ".a"))
                    and native("less_than", f0, field(s, "b", record_path, source=provider),
                               path="$", source=dict(provider, provider_path=record_path + ".b"))):
                try:
                    segment = native("divide", c.cache / "vseg", field(s, "name", record_path, source=provider),
                                     path="$", source=dict(provider, provider_path=record_path + ".name"))
                    st = segment.stat()
                except OSError:
                    return None
                if st.st_size == field(s, "size", record_path, source=provider) and st.st_mtime_ns == field(s, "mtime_ns", record_path, source=provider):
                    return (field(s, "a", record_path, source=provider), field(s, "b", record_path, source=provider))
                return None   # 段文件被换/重写（stat 变）→ 该段失配实测
        return None
    def check_exempt(frames, name):
        """检查项免测判定：上轮该名 pass 且全部探测帧都在 stat 一致段内 →
        合并覆盖区间 (min_a, max_b)；否则 None。"""
        if _seg_rec is None or not _seg_rec["checks"].get(name):
            return None
        spans = [seg_span_of(f0) for f0 in frames]
        if any(s is None for s in spans):
            return None
        return (min(a_ for a_, _ in spans), max(b_ for _, b_ in spans))

    def gfx_content_crop(cl):
        """gfx overlay 内容域 crop（R-05 探针口径对齐，2026-09-17 定谳单）：
        从 png 稳态帧（dur//2）alpha 通道提取内容 bbox，叠加 clip x/y 放置偏移，
        偶对齐后作 fdiff 采样域。背景：c4_mini 类「全画布 png+小内容条带」页
        （内容仅占画布 4.63%）在整帧 48:27 口径被稀释——放置域实测 cover 82%
        vs 整帧 2.9%，双阈双低误判「未生效」恒红。png 缺失/读取失败/内容全透明
        （空页）/矩形退化 → None 回落整帧口径（旧行为）。仅改采样域：阈值、
        检查名、checks 数量全部不变；域内信号单调不弱于整帧，既有 PASS 不翻。"""
        try:
            png = gfx_dir(c, cl) / f"f{cl['dur'] // 2:04d}.png"
            if not png.is_file():
                return None
            with png.open("rb") as fh:
                head = fh.read(24)
            if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
                return None
            pw = int.from_bytes(head[16:20], "big")
            ph = int.from_bytes(head[20:24], "big")
            if pw <= 0 or ph <= 0:
                return None
            tmp = c.cache / "qc_gfx_bbox.raw"
            c.run(["ffmpeg", "-v", "error", "-y", "-i", str(png), "-vf", "format=rgba",
                   "-frames:v", "1", "-f", "rawvideo", str(tmp)], need=False)
            raw = tmp.read_bytes(); tmp.unlink()
            if len(raw) < pw * ph * 4:
                return None
            # alpha>24 视为内容（与 QC_PIXEL_DIFF 同量级的存在性阈）；
            # translate + 逐行 find 全 C 路径，1080p 单帧毫秒级。
            t = raw[3::4].translate(bytes(1 if v > 24 else 0 for v in range(256)))
            x0 = y0 = x1 = y1 = -1
            for y in range(ph):
                row = t[y * pw:(y + 1) * pw]
                i = row.find(1)
                if i < 0:
                    continue
                if y0 < 0:
                    y0 = y
                y1 = y
                if x0 < 0 or i < x0:
                    x0 = i
                j = row.rfind(1)
                if j > x1:
                    x1 = j
            if x0 < 0 or y0 < 0:
                return None   # 内容全透明（空页）→ 回落整帧，判定将如实 FAIL
            ox = int(cl.get("x", 0)); oy = int(cl.get("y", 0))
            cx0 = max(0, x0 + ox); cy0 = max(0, y0 + oy)
            cx1 = min(c.w, x1 + 1 + ox); cy1 = min(c.h, y1 + 1 + oy)
            w = cx1 - cx0; h = cy1 - cy0
            if w < 2 or h < 2:
                return None
            w -= w % 2; h -= h % 2   # 偶对齐（对齐 composite 放置 //2*2 口径）
            return f"{w}:{h}:{cx0}:{cy0}"
        except Exception:
            return None   # 任何失败回落整帧——探针永不因本 helper 阻断 qc

    # ---- 并行任务收集（结果按 timeline 顺序回填，checks 顺序不变）----
    tasks = []   # (key, callable)
    ti_ = 0
    def launch(fn):
        nonlocal ti_
        tasks.append((f"t{ti_}", fn)); ti_ += 1
    # 底轨（tracks[0]）全屏 clip 的切点帧比对：比对语义 = qc_clean 基准帧（仅底轨
    # 全屏合成）vs 源帧。qc_clean 去全片化后，前者由 bottom_frame 现场合成（同
    # crop169+scale 链，像素等价），后者不变。
    cut_res = {}
    cut_frames = {}
    def cut_diff(cl, j, key, path):
        df = native("add", field(cl, "dst_in", path), j, path=path,
                    paths=(path + ".dst_in", "sample_offset"))
        sf = native("add", field(cl, "src_in", path), j, path=path,
                    paths=(path + ".src_in", "sample_offset"))
        a = bottom_frame(df, tag=f"c{df}_", origin=path)
        b = gray_frame(c.src_path(field(cl, "src", path)), sf,
                       pre_crop=f"{c.crop169(field(cl, 'src', path))},scale={c.w}:{c.h}:flags=lanczos",
                       tag=f"c{df}_", origin=path)
        mean = mean_difference(a, b, path, df)
        # 检查名仍显示 clip 起点；工作索引绝不取代原 timeline 索引。
        cut_frames[key] = df
        cut_res[key] = (sf, mean)
    if tracks and not bottom_gfx:
        for ci, cl in ops.clips(field(tracks, 0, "$.video_tracks"), "$.video_tracks[0]"):
            path = f"$.video_tracks[0].clips[{ci}]"
            if not native("contains", cl, "src_in", path=path): continue
            j = native("max", 2, native("add", get(cl, "fade_in_frames", path, 0), 1,
                       path=path + ".fade_in_frames"), path=path + ".fade_in_frames")
            if get(cl, "fade_out_frames", path):
                j = native("max", j, native("subtract", native("subtract",
                    field(cl, "dur", path), field(cl, "fade_out_frames", path),
                    path=path, paths=(path + ".dur", path + ".fade_out_frames")), 1,
                    path=path, paths=((path + ".dur", path + ".fade_out_frames"),)), path=path)
            j = native("min", j, native("subtract", field(cl, "dur", path), 1,
                       path=path + ".dur"), path=path)
            key = f"cut:{field(cl, 'dst_in', path)}:{field(cl, 'src', path)}"
            sf = native("add", field(cl, "src_in", path), j,
                        path=path, paths=(path + ".src_in", "sample_offset"))
            e = check_exempt([native("add", field(cl, "dst_in", path), j, path=path,
                                    paths=(path + ".dst_in", "sample_offset"))],
                             f"底轨切点帧比对 dst={field(cl, 'dst_in', path)} src={field(cl, 'src', path)}:{sf}")
            if e is not None:
                exempt[key] = e; cut_sf[key] = sf
            else:
                launch(lambda cl=cl, j=j, key=key, path=path: cut_diff(cl, j, key, path))
    # 上层轨（tracks[1:]）逐 clip 窗口生效检查：窗口 clip 比对其放置区域，全屏 clip 比对整帧
    up_res = {}
    gfx_samples = {}
    def gfx_probes(cl, path):
        # Preserve the three original expressions, including duplicate accesses.
        return sorted({clip_mid(cl, path, 4), clip_mid(cl, path), native(
            "subtract", clip_end(cl, path), native("floor_divide", field(cl, "dur", path), 4,
            path=path + ".dur"), path=path, paths=((path + ".dst_in", path + ".dur"), path + ".dur"))})

    for ti, tr in enumerate(ops.upper_tracks(tracks), start=1):
        track_path = f"$.video_tracks[{ti}]"
        for ci, cl in ops.indexed(field(tr, "clips", track_path), track_path + ".clips"):
            path = f"{track_path}.clips[{ci}]"
            if get(tr, "kind", track_path) == "gfx":
                probes = gfx_probes(cl, path)
                key = f"gfx:{id(cl)}"
                gfx_samples[key] = probes
                gfx_path = native("Path", field(cl, "src", path), path=path + ".src")
                e = check_exempt(probes, f"gfx 窗口 {gfx_path.stem} 生效")
                if e is not None:
                    exempt[key] = e
                else:
                    gcrop = gfx_content_crop(cl)   # R-05 既有任意失败回落，保持不变。
                    launch(lambda probes=probes, key=key, gcrop=gcrop, path=path:
                           up_res.__setitem__(key, [fdiff(m, crop=gcrop, tag=f"{m}_{i}", origin=path)
                                                   for i, m in enumerate(probes)]))
                continue
            windowed = any(native("contains", cl, k, path=path) for k in ("scale", "x", "y"))
            m = clip_mid(cl, path)
            if windowed:
                key = f"win:{id(cl)}"
                e = check_exempt([m], f"窗口 clip {field(tr, 'name', track_path)} @frame {m} 生效")
                if e is not None:
                    exempt[key] = e
                    continue   # 免检路径无需 crop（c.probe 也可省一次）
                resource = c.src_path(field(cl, "src", path))
                i = c.probe(resource)
                provider = {"provider": "source_probe", "resource": str(resource), "origin": path}
                sc = get(cl, "scale", path, 1.0)
                width = field(i, "w", "$", source=provider)
                w2 = native("int", native("multiply", width, sc, path=path,
                    paths=("provider:$.w", path + ".scale"), source=provider),
                    path=path, source=provider)
                height = field(i, "h", "$", source=provider)
                h2 = native("int", native("multiply", height, sc, path=path,
                    paths=("provider:$.h", path + ".scale"), source=provider),
                    path=path, source=provider)
                crop = (f"{w2}:{h2 + 2}:"
                        f"{native('max', 0, native('subtract', get(cl, 'x', path, 0), 10, path=path + '.x'), path=path + '.x')}:"
                        f"{native('max', 0, native('subtract', get(cl, 'y', path, 0), 10, path=path + '.y'), path=path + '.y')}")
                launch(lambda m=m, crop=crop, key=key, path=path:
                       up_res.__setitem__(key, fdiff(m, crop=crop, tag=f"{m}_", origin=path)))
            else:
                key = f"full:{id(cl)}"
                e = check_exempt([m], f"全屏 clip {field(tr, 'name', track_path)} @frame {m} 生效")
                if e is not None:
                    exempt[key] = e
                else:
                    launch(lambda m=m, key=key, path=path:
                           up_res.__setitem__(key, fdiff(m, tag=f"{m}_", origin=path)))
    # Coverage carries the producer origin, including external subtitle indices.
    covered = []
    for si, subtitle in ops.indexed(field(c.tl, "subtitles", "$"), "$.subtitles"):
        path, source = subtitle_location(c, si)
        covered.append((field(subtitle, "in", path, source=source),
                        field(subtitle, "out", path, source=source), path, source,
                        "source:in" if source else path + ".in",
                        "source:out" if source else path + ".out"))
    for ti, tr in enumerate(ops.upper_tracks(tracks), start=1):
        track_path = f"$.video_tracks[{ti}]"
        for ci, cl in ops.indexed(field(tr, "clips", track_path), track_path + ".clips"):
            path = f"{track_path}.clips[{ci}]"
            covered.append((field(cl, "dst_in", path), clip_end(cl, path), path, None,
                            path + ".dst_in", (path + ".dst_in", path + ".dur")))

    def in_bottom_fade(f0):
        if bottom_gfx: return False
        track_path = "$.video_tracks[0]"
        bottom = field(tracks, 0, "$.video_tracks")
        for ci, cl in ops.indexed(field(bottom, "clips", track_path), track_path + ".clips"):
            path = f"{track_path}.clips[{ci}]"
            if clip_contains(cl, f0, path):
                off = native("subtract", f0, field(cl, "dst_in", path),
                             path=path, paths=("sample_frame", path + ".dst_in"))
                return (native("less_than", off, get(cl, "fade_in_frames", path, 0), path=path,
                               paths=("sample_offset", path + ".fade_in_frames"))
                        or native("greater_equal", off, native("subtract", field(cl, "dur", path),
                                  get(cl, "fade_out_frames", path, 0), path=path,
                                  paths=(path + ".dur", path + ".fade_out_frames")),
                                  path=path, paths=("sample_offset", (path + ".dur", path + ".fade_out_frames"))))
        return False

    def interval_contains(a, b, frame, path, source=None, start_path=None, end_path=None):
        return (native("less_equal", a, frame, path=path,
                       paths=(start_path or path + ".in", "sample_frame"), source=source)
                and native("less_than", frame, b, path=path,
                           paths=("sample_frame", end_path or path + ".out"), source=source))

    free_frame = next((f0 for f0 in native("range", native("subtract", c.dur, 1,
                       path="$.project.duration_frames"), 0, -1, path="$.project.duration_frames")
                       if not any(interval_contains(a, b, f0, path, source, start, end)
                                  for a, b, path, source, start, end in covered)
                       and not in_bottom_fade(f0)), None)
    free_res = {}
    if free_frame is not None:
        e = check_exempt([free_frame], f"空窗无叠加泄漏 @frame {free_frame}")
        if e is not None:
            exempt["f"] = e
        else:
            launch(lambda: free_res.__setitem__("f", fdiff(free_frame, tag=f"{free_frame}_")))
    # 响度归一验证（EBU R128 整合响度应 ≈ 目标值）——与帧探测并行。
    # 方案三阶段1（2026-09-13）：①加 -vn 免整遍视频解码（-f null 默认流选择会连
    # 视频一起解码，本机实测 4.9s→0.4s）；②peak=true 顺带测 TP 归档回执
    # （vtx/loudness_cache.py——同一解码域输入全链只测一次）；③先查回执，命中免测
    # （内容键绑定，文件任何变动自然失效重测，不跨版本复用数字）。
    # v2 §1.3（2026-09-13）：TP 从「只归档回执」升为内核 qc 判定项（阈 -1.0 dBFS，
    # 与 gates G3 同一权威值、同一回执不重测）——检查项见下方 checks 回填段。
    def loudness():
        hit = loudness_cache.lookup(final, root=c.root)
        cache_source = {"provider": "loudness_cache", "resource": str(final)}
        if hit is not None:
            return (field(hit, "i_lufs", "$", source=cache_source),
                    field(hit, "tp_dbfs", "$", source=cache_source), True)
        def measure():
            r2 = c.run(["ffmpeg", "-nostats", "-vn", "-i", str(final),
                        "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"])
            provider = {"provider": "ffmpeg", "resource": str(final), "measurement": "ebur128"}
            mI = native("re.search", r"I:\s*(-?[\d.]+) LUFS", r2.stderr, path="$", source=provider)
            mP = native("re.search", r"Peak:\s*(-?[\d.]+) dBFS", r2.stderr, path="$", source=provider)
            return (native("float", mI.group(1), path="$", source=dict(provider, field="i_lufs")) if mI else None,
                    native("float", mP.group(1), path="$", source=dict(provider, field="tp_dbfs")) if mP else None)
        r = loudness_cache.measure_and_store(final, measure, root=c.root)
        return (field(r, "i_lufs", "$", source=cache_source),
                field(r, "tp_dbfs", "$", source=cache_source), False)

    def vol(f0, f1):
        begin = native("multiply", f0, c.spf, path="$.audio_tracks",
                       paths=("sample_frame", "$.project.fps"))
        span = native("multiply", native("subtract", f1, f0, path="$.audio_tracks"),
                      c.spf, path="$.audio_tracks", paths=("sample_span", "$.project.fps"))
        r = c.run(["ffmpeg", "-v", "info", "-ss", f"{begin:.3f}", "-t", f"{span:.3f}",
                   "-i", str(final), "-map", "0:a", "-af", "volumedetect", "-f", "null", "-"])
        provider = {"provider": "ffmpeg", "resource": str(final), "measurement": "volumedetect"}
        m = native("re.search", r"mean_volume:\s*(-?[\d.]+) dB", r.stderr, path="$", source=provider)
        # Preserve the legacy sentinel and whether a measurement actually exists.
        return (native("float", m.group(1), path="$", source=provider), True) if m else (-999, False)

    stacked = None
    marks = []
    for ti, tr in ops.indexed(field(c.tl, "audio_tracks", "$"), "$.audio_tracks"):
        track_path = f"$.audio_tracks[{ti}]"
        for ei, event in ops.indexed(field(tr, "events", track_path), track_path + ".events"):
            path = f"{track_path}.events[{ei}]"
            marks.append((field(event, "in", path), field(event, "out", path), path))
    for f0 in native("range", 0, c.dur, 24, path="$.project.duration_frames"):
        active = sum(1 for a, b, path in marks if interval_contains(a, b, f0, path))
        if stacked is None or active > stacked[0]:
            stacked = (active, f0)
    # 并发窗探测帧域防御（P6 修复 B-P3-5a）：v1 直接取 stacked[1]+2..+10，短 timeline
    # （dur < 探测帧上界）时 -ss 越片尾 volumedetect 空读 -999 假 FAIL；无任何
    # 音频事件（stacked None）时 v1 在 dry 外路径裸 TypeError。
    vol_span = None
    if stacked is not None:
        v1_end = min(stacked[1] + 10, c.dur)
        v1_beg = min(stacked[1] + 2, max(v1_end - 1, 0))
        vol_span = (v1_beg, v1_end)
    if c.dry:
        loud_v, loud_tp, loud_hit, v1 = None, None, False, -999   # dry-run 不执行，直接走缺省
        v1_measured = False
    else:
        # 三组互不依赖：帧探测（内部 8 线程并发 ffmpeg）/ ebur128 / volumedetect 并行，墙钟 ≈ max
        with ThreadPoolExecutor(3) as gex:
            def run_probes():
                # 每任务独立 ffmpeg 子进程；结果按 key 回填 dict，报告顺序不受并发影响
                # （免检项在生成阶段已被剔除，此处全部为需实测任务）
                with ThreadPoolExecutor(8) as pex:
                    futs = [pex.submit(copy_context().run, fn) for _, fn in tasks]
                    for f in futs: f.result()
            fp = gex.submit(copy_context().run, run_probes)
            fl = gex.submit(copy_context().run, loudness)
            fv = gex.submit(copy_context().run, lambda: vol(*vol_span) if vol_span else (-999, False))
            loud_v, loud_tp, loud_hit = fl.result(); v1, v1_measured = fv.result(); fp.result()

    # ---- 按原顺序回填 checks（内容/顺序/名称与旧版一致）----
    ok(f"成片帧数 = {c.dur}", nb == c.dur, f"实测 {nb}（{nb_src}）",
       rule_id="qc.frame_count", json_path="$.project.duration_frames",
       measured={"value": nb, "unit": "frames", "method": nb_method},
       threshold={"equals": c.dur, "unit": "frames"},
       suggested_actions=[{"op": "inspect_render", "params": {"stages": ["video", "mux"],
                           "expected_frames": c.dur}}])
    ok(f"成片时长 ≈ {native('multiply', c.dur, c.spf, path='$.project', paths=('$.project.duration_frames', '$.project.fps')):.2f}s", abs(dur - (expected_seconds := native("multiply", c.dur, c.spf, path="$.project", paths=("$.project.duration_frames", "$.project.fps")))) < 0.05, f"实测 {dur:.3f}s",
       rule_id="qc.duration", json_path="$.project.duration_frames",
       measured={"value": dur, "unit": "seconds"},
       threshold={"target": expected_seconds, "absolute_error_lt": 0.05, "unit": "seconds"},
       suggested_actions=[{"op": "inspect_render", "params": {"stages": ["video", "mux"],
                           "expected_seconds": expected_seconds}}])
    observed_height = {"status": "not_observed"}
    ok(f"分辨率 {c.w}x{c.h}", (observed_width := field(st, "width", "$.streams[0]", source=output_source)) == c.w and (observed_height := field(st, "height", "$.streams[0]", source=output_source)) == c.h,
       rule_id="qc.resolution", json_path="$.project",
       measured={"width": observed_width,
                 "height": observed_height, "unit": "pixels"},
       threshold={"width_equals": c.w, "height_equals": c.h, "unit": "pixels"},
       suggested_actions=[{"op": "inspect_render", "params": {"stages": ["video", "mux"],
                           "expected_width": c.w, "expected_height": c.h}}])
    cut_threshold = {"mean_pixel_difference": {"lt": QC_CUT_MEAN_MAX, "unit": "8-bit intensity"}}
    window_threshold = {
        "operator": "or",
        "mean_pixel_difference": {"gt": QC_WINDOW_MEAN_MIN, "unit": "8-bit intensity"},
        "coverage": {"gt": QC_WINDOW_COVER_MIN, "unit": "ratio",
                     "pixel_difference_gt": {"value": QC_PIXEL_DIFF, "unit": "8-bit intensity"}}}
    leak_threshold = {
        "operator": "and",
        "mean_pixel_difference": {"lt": QC_LEAK_MEAN_MAX, "unit": "8-bit intensity"},
        "coverage": {"lt": QC_LEAK_COVER_MAX, "unit": "ratio",
                     "pixel_difference_gt": {"value": QC_PIXEL_DIFF, "unit": "8-bit intensity"}}}
    if tracks and not bottom_gfx:
        # Sort the work, never the source identity: paths use timeline indices.
        for ci, cl in ops.clips(field(tracks, 0, "$.video_tracks"), "$.video_tracks[0]"):
            path = f"$.video_tracks[0].clips[{ci}]"
            if not native("contains", cl, "src_in", path=path): continue
            key = f"cut:{field(cl, 'dst_in', path)}:{field(cl, 'src', path)}"
            diagnostic = {"rule_id": "qc.cut_frame", "json_path": path,
                          "threshold": cut_threshold,
                          "suggested_actions": [{"op": "inspect_clip", "params": {
                              "json_path": path, "fields": ["src", "src_in", "dst_in", "dur"]}}]}
            if key in exempt:
                ok_exempt(f"底轨切点帧比对 dst={field(cl, 'dst_in', path)} src={field(cl, 'src', path)}:{cut_sf[key]}",
                          exempt[key], **diagnostic)
                continue
            sf, d_ = cut_res[key]
            ok(f"底轨切点帧比对 dst={field(cl, 'dst_in', path)} src={field(cl, 'src', path)}:{sf}", d_ < QC_CUT_MEAN_MAX, f"平均像素差 {d_:.2f}/255",
               measured={"mean_pixel_difference": {"value": d_, "unit": "8-bit intensity"},
                         "source_frame": {"value": sf, "unit": "frames"},
                         "destination_frame": {"value": cut_frames[key], "unit": "frames"}},
               **diagnostic)
    for ti, tr in enumerate(ops.upper_tracks(tracks), start=1):
        track_path = f"$.video_tracks[{ti}]"
        for ci, cl in ops.indexed(field(tr, "clips", track_path), track_path + ".clips"):
            path = f"{track_path}.clips[{ci}]"
            diagnostic = {"json_path": path, "threshold": window_threshold,
                          "suggested_actions": [{"op": "inspect_clip", "params": {
                              "json_path": path, "fields": ["src", "dst_in", "dur", "scale", "x", "y"]}}]}
            if get(tr, "kind", track_path) == "gfx":
                key = f"gfx:{id(cl)}"
                diagnostic["rule_id"] = "qc.gfx_window"
                if key in exempt:
                    ok_exempt(f"gfx 窗口 {native('Path', field(cl, 'src', path), path=path + '.src').stem} 生效", exempt[key], **diagnostic)
                    continue
                stats = up_res[key]
                dmax = max(s[0] for s in stats); cov = max(s[1] for s in stats)
                ok(f"gfx 窗口 {native('Path', field(cl, 'src', path), path=path + '.src').stem} 生效", dmax > QC_WINDOW_MEAN_MIN or cov > QC_WINDOW_COVER_MIN,
                   f"采样 {[clip_mid(cl, path, 4), clip_mid(cl, path), native('subtract', clip_end(cl, path), native('floor_divide', field(cl, 'dur', path), 4, path=path + '.dur'), path=path, paths=((path + '.dst_in', path + '.dur'), path + '.dur'))]} 最大均值差 {dmax:.1f}，覆盖 {cov*100:.1f}%",
                   measured={"mean_pixel_difference": {"value": dmax, "unit": "8-bit intensity", "aggregation": "max"},
                             "coverage": {"value": cov, "unit": "ratio", "aggregation": "max"},
                             "frames": {"values": gfx_samples[key], "unit": "frames"}},
                   **diagnostic)
                continue
            windowed = any(native("contains", cl, k, path=path) for k in ("scale", "x", "y"))
            m = clip_mid(cl, path)
            if windowed:
                key = f"win:{id(cl)}"
                diagnostic["rule_id"] = "qc.clip_window"
                if key in exempt:
                    ok_exempt(f"窗口 clip {field(tr, 'name', track_path)} @frame {m} 生效", exempt[key], **diagnostic)
                    continue
                d_, cov = up_res[key]
                ok(f"窗口 clip {field(tr, 'name', track_path)} @frame {m} 生效", d_ > QC_WINDOW_MEAN_MIN or cov > QC_WINDOW_COVER_MIN,
                   f"区域差 {d_:.1f}，覆盖 {cov*100:.1f}%",
                   measured={"mean_pixel_difference": {"value": d_, "unit": "8-bit intensity"},
                             "coverage": {"value": cov, "unit": "ratio"},
                             "frame": {"value": m, "unit": "frames"}}, **diagnostic)
            else:
                key = f"full:{id(cl)}"
                diagnostic["rule_id"] = "qc.fullscreen_clip"
                if key in exempt:
                    ok_exempt(f"全屏 clip {field(tr, 'name', track_path)} @frame {m} 生效", exempt[key], **diagnostic)
                    continue
                d_, cov = up_res[key]
                ok(f"全屏 clip {field(tr, 'name', track_path)} @frame {m} 生效", d_ > QC_WINDOW_MEAN_MIN or cov > QC_WINDOW_COVER_MIN,
                   f"整帧差 {d_:.1f}，覆盖 {cov*100:.1f}%",
                   measured={"mean_pixel_difference": {"value": d_, "unit": "8-bit intensity"},
                             "coverage": {"value": cov, "unit": "ratio"},
                             "frame": {"value": m, "unit": "frames"}}, **diagnostic)
    leak_diagnostic = {"rule_id": "qc.overlay_leak", "json_path": "$.video_tracks",
                       "threshold": leak_threshold,
                       "suggested_actions": [{"op": "inspect_composite", "params": {
                           "frame": free_frame, "json_paths": ["$.video_tracks", "$.subtitles"]}}]}
    if free_frame is not None:
        if "f" in exempt:
            ok_exempt(f"空窗无叠加泄漏 @frame {free_frame}", exempt["f"], **leak_diagnostic)
        else:
            d_, cov = free_res["f"]
            ok(f"空窗无叠加泄漏 @frame {free_frame}", d_ < QC_LEAK_MEAN_MAX and cov < QC_LEAK_COVER_MAX, f"差异 {d_:.2f}，覆盖 {cov*100:.1f}%",
               measured={"mean_pixel_difference": {"value": d_, "unit": "8-bit intensity"},
                         "coverage": {"value": cov, "unit": "ratio"},
                         "frame": {"value": free_frame, "unit": "frames"}}, **leak_diagnostic)
    else:
        emit(c, "空窗无叠加泄漏：无可用的无遮挡且不在 fade 边界的采样帧",
             status="unknown", severity="info", measured=None, **leak_diagnostic)
    # 响度/TP 检查项（P6 修复 B-P3-4）：测量失败（None）不再是「检查项静默消失」
    # ——final 的音频流异常到测不出响度/真峰，属产物可信度问题，显性 FAIL。
    loud_diagnostic = {
        "rule_id": "qc.integrated_loudness", "json_path": "$.audio_tracks",
        "measured": {"value": loud_v, "unit": "LUFS", "receipt_hit": loud_hit},
        "threshold": {"target": {"value": TARGET_LUFS, "unit": "LUFS"},
                      "warn_absolute_deviation_gt": {"value": QC_LOUDNESS_TOL, "unit": "LU"},
                      "fail_absolute_deviation_gt": {"value": QC_LOUDNESS_FAIL, "unit": "LU"}},
        "suggested_actions": [{"op": "inspect_audio_gain", "params": {
            "json_path": "$.audio_tracks", "target_lufs": TARGET_LUFS,
            "stages": ["audio", "mux"]}}]}
    loud_source = {"provider": "loudness_cache" if loud_hit else "ffmpeg",
                   "resource": str(final), "measurement": "ebur128"}
    if loud_v is not None:
        dev = abs(native("subtract", loud_v, TARGET_LUFS, path="$",
                         source=dict(loud_source, field="i_lufs")))
        loud_diagnostic["measured"]["absolute_deviation"] = {"value": dev, "unit": "LU"}
        if dev > QC_LOUDNESS_FAIL:
            ok(f"响度归一 ≈ {TARGET_LUFS:g} LUFS", False,
               f"实测 {loud_v} LUFS（偏离 {dev:.1f} LU >{QC_LOUDNESS_FAIL:g}——疑增益链断裂，"
               f"对齐 build_audio 终态硬阈）" + ("（回执命中，免测）" if loud_hit else ""),
               **loud_diagnostic)
        elif dev > QC_LOUDNESS_TOL:
            ok(f"响度归一 ≈ {TARGET_LUFS:g} LUFS", True,
               f"实测 {loud_v} LUFS（偏离 {dev:.1f} LU ∈({QC_LOUDNESS_TOL:g},{QC_LOUDNESS_FAIL:g}]——"
               f"限幅响度损耗属内容级，WARN 放行；发布侧内容门 G2 拦截口径）"
               + ("（回执命中，免测）" if loud_hit else ""),
               status="warn", severity="warning", **loud_diagnostic)
        else:
            ok(f"响度归一 ≈ {TARGET_LUFS:g} LUFS", True,
               f"实测 {loud_v} LUFS" + ("（回执命中，免测）" if loud_hit else ""),
               **loud_diagnostic)
    else:
        ok(f"响度归一 ≈ {TARGET_LUFS:g} LUFS", False,
           "ebur128 解析失败（final 音频流异常或不可测）", status="unknown",
           **loud_diagnostic)
    tp_diagnostic = {
        "rule_id": "qc.true_peak", "json_path": "$.audio_tracks",
        "measured": {"value": loud_tp, "unit": "dBFS", "receipt_hit": loud_hit},
        "threshold": {"lte": QC_TP_MAX, "unit": "dBFS"},
        "suggested_actions": [{"op": "inspect_audio_clipping", "params": {
            "json_path": "$.audio_tracks", "max_true_peak_dbfs": QC_TP_MAX,
            "stages": ["audio", "mux"]}}]}
    if loud_tp is not None:
        # v2 §1.3 解码域真峰 TP 判定：阈 -1.0 dBFS（G3 同一权威值，同一回执不重测）。
        # vtx audio 已内置 alimiter，成片 TP 超阈 = 上游削波/限幅失效，须查上游。
        ok(f"解码域真峰 TP ≤ {QC_TP_MAX:g} dBFS", native("less_equal", loud_tp, QC_TP_MAX, path="$", source=dict(loud_source, field="tp_dbfs")),
           f"实测 {loud_tp} dBFS" + ("（回执命中，免测）" if loud_hit else "")
           + ("" if native("less_equal", loud_tp, QC_TP_MAX, path="$", source=dict(loud_source, field="tp_dbfs"))
              else "——audio 已内置 alimiter，若仍超标检查上游削波"),
           **tp_diagnostic)
    else:
        ok(f"解码域真峰 TP ≤ {QC_TP_MAX:g} dBFS", False,
           "ebur128 peak 解析失败（final 音频流异常或不可测）", status="unknown",
           **tp_diagnostic)
    ok(f"{stacked[0] if stacked else 0} 轨并发窗口有能量", v1 > -50,
       f"mean {v1:.1f} dB"
       + (f" @frame {stacked[1]}" if stacked else "（无任何音频事件）"),
       rule_id="qc.stacked_audio_energy", json_path="$.audio_tracks",
       measured={"mean_volume": {"value": v1 if v1_measured else None, "unit": "dB"},
                 "active_events": {"value": stacked[0] if stacked else 0, "unit": "events"},
                 "probe_span": {"start": vol_span[0] if vol_span else None,
                                "end": vol_span[1] if vol_span else None,
                                "unit": "frames", "end_exclusive": True}},
       threshold={"gt": -50, "unit": "dB"},
       **({"status": "unknown"} if not v1_measured else {}),
       suggested_actions=[{"op": "inspect_audio_mix", "params": {
           "json_path": "$.audio_tracks", "frame": stacked[1] if stacked else None,
           "fields": ["src", "in", "out", "volume"]}}])
    from .quality import qc_quality
    rep["skipped_probes"] = skipped_probes
    rep["quality"] = qc_quality(rep)
    fstxn.atomic_write(c.out / "qc_report.json", json.dumps(rep, ensure_ascii=False, indent=2))
    failed = [x for x in rep["checks"] if not x["pass"]]
    print("质量标签：" + rep["quality"]["display"] + "（visual_content=unknown）")
    print(f"QC：{len(rep['checks']) - len(failed)}/{len(rep['checks'])} 通过")
    if skipped_probes:
        print(f"⚠ 免检 {len(skipped_probes)} 项（内容键命中段，stat 绑定一致）："
              "段内像素级篡改若同时保持段文件 stat 不变则本模式不查——--strict 恢复全检")
    c.receipt("qc", not failed, f"{len(rep['checks'])-len(failed)}/{len(rep['checks'])}",
              extra={"skipped_probes": skipped_probes})
    # 免检记录每轮刷新（方案三阶段1）：segs/final 重绑当前 stat，checks 按名记忆
    # pass——✗ 项下轮必复测；记录在 exit 判定前写（失败轮同样刷新，失败项记忆
    # 为 False 即「不可免检」，安全性不受影响）。
    _write_qc_pass_record(c, nb, rep["checks"])
    if failed: producer_exit(1)
