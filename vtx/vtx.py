#!/usr/bin/env python3
"""vtx — 视频剪辑中间层 CLI（timeline.json → ffmpeg，帧精确合成）

用法：
  vtx.py [-t TIMELINE] [--dry-run] <command>

命令（按依赖顺序，all = 依次全部执行）：
  validate   校验 timeline.json 契约（对照素材实测，失败即退出）
  audit      指标审计（建议性，不阻断）：画面构成/黑场窗口/无声窗口/字幕阅读速度/语速，对照实测基准，写 audit_report.json
  prepare    提取素材音频 → 48kHz 立体声 PCM
  gfx        渲染 kind=gfx 轨的 HTML 动效 → 透明 PNG 序列（Chrome headless，带缓存）
  video      黑场画布 + 全部视频轨按图层序时移叠加 + 字幕烧录（v2 轨道等价模型）
  audio      多轨音频混音（帧网格对齐、fade/pan/loop、duck 音乐避让、EBU R128 响度归一 -14 LUFS）
  mux        视音频封装

迭代提速（2026-09-08，video/mux 专属标志）：
  --draft            草稿模式：540p 半分辨率 + NVENC 硬编码（不可用自动回落 x264 veryfast），
                     跳过 qc_clean 基准轨渲染，产物 out/draft.mp4（mux --draft → out/draft_final.mp4），
                     不触碰正式产物 video_noaudio.mp4 / final.mp4 / cache/subs.ass
  --draft --frames N 只渲前 N 帧（输入按头窗截短，避免 drain 全量解码；改动集中片头时提速一个量级）
  --gpu              正式参数 GPU 渲染：NVENC p7+tune hq+vbr cq17（1080p 交付透明），
                     产物仍是 video_noaudio.mp4；NVENC 不可用自动回落 x264 crf17 medium
  qc         渲染后质检（帧数/底轨切点帧比对/上层窗口生效/泄漏/响度归一），写 qc_report.json
  clean      清空 cache/ 与 out/

约定：AI 智能体不应手写 ffmpeg 剪辑命令；只编写/修改 timeline.json 并调用本工具。
每条时间量都是整数帧；区间 [in, out) 左闭右开。回执写入 out/receipt.json。
契约 v2（D15 轨道等价模型）：video_tracks 所有轨道等价，数组顺序即图层顺序（[0] 最底）；
画布为黑场，无任何覆盖性约束；kind（aroll/broll/screen/pip/gfx）纯语义标注，渲染不区分；
clip 的 scale/x/y/opacity/fade 全部可选（缺省＝全屏不透明），fade 统一为 alpha 淡入淡出。
仅依赖 python3 标准库 + ffmpeg/ffprobe/google-chrome。
"""
import argparse, json, os, subprocess, sys, hashlib, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

VTX_DIR = Path(__file__).parent
DRY = False

class Ctx:
    def __init__(self, timeline_path: Path):
        self.tl_path = timeline_path
        self.root = timeline_path.parent
        self.tl = json.loads(timeline_path.read_text())
        self.fps = self.tl["project"]["fps"]
        self.dur = self.tl["project"]["duration_frames"]
        self.w = self.tl["project"]["width"]
        self.h = self.tl["project"]["height"]
        self.spf = 1.0 / self.fps
        self.spf_samples = 48000 // self.fps   # 30fps → 1600（24fps 时代为 2000），须整除
        self.cache = self.root / "cache"; self.out = self.root / "out"
        self.probes = {}

    # ---- 基础设施 ----
    def run(self, cmd, need=True):
        if DRY and str(cmd[0]).endswith("ffmpeg"):
            print("[dry-run]", " ".join(map(str, cmd))[:300] + (" …" if len(cmd) > 20 else ""))
            return ""
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 and need:
            print("CMD FAILED:", " ".join(map(str, cmd))[:300], file=sys.stderr)
            print(r.stderr[-1500:], file=sys.stderr); sys.exit(1)
        return r

    def probe(self, path: Path):
        path = Path(path)
        if path in self.probes: return self.probes[path]
        # 不用 -count_frames（逐帧解码计数：voice-ime 实测 1080p 全源 12-27s/个，
        # validate+video 每次进程合计 ~2.5min 纯浪费）。mp4/mov 流头 nb_frames 即精确；
        # 流头缺失才回落 -count_packets（仅解封装，秒级）。
        r = self.run(["ffprobe", "-v", "error", "-print_format", "json",
                      "-show_streams", "-show_format", str(path)])
        d = json.loads(r.stdout)
        v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
        a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
        if v is not None:
            nb = str(v.get("nb_frames") or "")
            if not nb.isdigit() or int(nb) == 0:
                r2 = self.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                               "-count_packets", "-show_entries", "stream=nb_read_packets",
                               "-of", "csv=p=0", str(path)])
                nb = r2.stdout.strip() if r2 else ""
            info = {"fps": eval(v["r_frame_rate"]),
                    "nb_frames": int(nb) if nb.isdigit() else 0,
                    "w": int(v["width"]), "h": int(v["height"]),
                    "has_video": True, "has_audio": a is not None}
        else:  # 纯音频源（音效/BGM/环境声文件）：时长换算到项目帧网格，仅供音频事件引用
            dur = float(d["format"]["duration"])
            info = {"fps": None, "nb_frames": int(dur * self.fps),
                    "w": 0, "h": 0, "has_video": False, "has_audio": a is not None}
        self.probes[path] = info; return info

    def receipt(self, stage, ok, detail=""):
        f = self.out / "receipt.json"
        rec = json.loads(f.read_text()) if f.exists() else {"timeline": None, "stages": {}}
        rec["timeline"] = hashlib.sha256(self.tl_path.read_bytes()).hexdigest()
        rec["updated"] = time.strftime("%F %T")
        rec["stages"][stage] = {"ok": bool(ok), "detail": detail}
        self.out.mkdir(exist_ok=True); f.write_text(json.dumps(rec, ensure_ascii=False, indent=2))

    def src_path(self, sid):
        return self.root / next(s for s in self.tl["sources"] if s["id"] == sid)["path"]

    # ---- 16:9 中心裁切（任意源尺寸通用，保持偶数） ----
    def crop169(self, sid):
        i = self.probe(self.src_path(sid))
        cw = i["w"] - i["w"] % 2
        ch = int(i["w"] * 9 / 16); ch -= ch % 2
        if ch > i["h"]: ch = i["h"] - i["h"] % 2; cw = int(i["h"] * 16 / 9); cw -= cw % 2
        return f"crop={cw}:{ch}:0:{(i['h'] - ch) // 2}"

# ---------------------------------------------------------------- validate
def validate(c: Ctx):
    errs = []
    def err(m): errs.append(m)
    srcs = {s["id"]: s for s in c.tl["sources"]}
    for sid, s in srcs.items():
        p = c.src_path(sid)
        if not p.exists(): err(f"source {sid}: 文件不存在 {p}"); continue
        i = c.probe(p)
        if i["has_video"]:
            if abs(i["fps"] - c.fps) > 0.01: err(f"source {sid}: fps={i['fps']} ≠ {c.fps}（须先 CFR 归一化）")
        if not i["has_audio"]: err(f"source {sid}: 无音频轨")   # 纯视频源同禁；纯音频源合法（has_video=False）
    # v2 轨道等价：无 base 特权、无覆盖性约束；唯一机制约束 = 同轨 clips 不重叠
    for tr in c.tl["video_tracks"]:
        kind = tr.get("kind", "?")
        clips = sorted(tr["clips"], key=lambda x: x["dst_in"])
        if not clips: err(f"轨道 {tr['name']}: 无 clips")
        for a, b in zip(clips, clips[1:]):
            if a["dst_in"] + a["dur"] > b["dst_in"]:
                err(f"同轨 clips 重叠: {tr['name']} @帧 {a['dst_in'] + a['dur']} > {b['dst_in']}")
        for cl in clips:
            if cl["dst_in"] < 0 or cl["dst_in"] + cl["dur"] > c.dur:
                err(f"{tr['name']}: clip 区间越界 @dst_in={cl['dst_in']}")
            if kind == "gfx":
                if not (c.root / cl["src"]).exists(): err(f"gfx clip html 不存在: {cl['src']}")
                continue
            if cl["src"] not in srcs: err(f"clip 引用未知 src {cl['src']}"); continue
            if "src_in" not in cl or "src_out" not in cl:
                err(f"普通 clip 缺 src_in/src_out: {tr['name']} dst_in={cl['dst_in']}"); continue
            if cl["src_out"] - cl["src_in"] != cl["dur"]:
                err(f"EDL 不变量破坏: {tr['name']} dst_in={cl['dst_in']}")
            p = c.src_path(cl["src"])
            if p.exists():
                i = c.probe(p)
                if not i["has_video"]:
                    err(f"视频 clip 引用了纯音频源: {tr['name']} src={cl['src']}")
                elif cl["src_out"] > i["nb_frames"]:
                    err(f"切割越界: {tr['name']} src={cl['src']} src_out={cl['src_out']} > 实测 {i['nb_frames']}")
            if cl.get("fade_in_frames", 0) + cl.get("fade_out_frames", 0) > cl["dur"]:
                err(f"fade 总长超过片段: {tr['name']} dst_in={cl['dst_in']}")
    for s in c.tl["subtitles"]:
        if not (0 <= s["in"] < s["out"] <= c.dur): err(f"字幕区间非法: {s['text'][:10]}")
    for tr in c.tl["audio_tracks"]:
        evs = sorted(tr["events"], key=lambda e: e["in"])
        for e in evs:
            if not (0 <= e["in"] < e["out"] <= c.dur): err(f"音频事件区间非法: {tr['name']}@{e['in']}")
            if e["src"] not in srcs: err(f"音频引用未知 src {e['src']}"); continue
            i = c.probe(c.src_path(e["src"]))
            need = e.get("src_in", 0) + (e["out"] - e["in"])
            if not e.get("loop") and need > i["nb_frames"]:
                err(f"音频源不足: {tr['name']}@{e['in']} 需 {need} 帧 > {i['nb_frames']}（或声明 loop）")
        for a, b in zip(evs, evs[1:]):
            if a["out"] > b["in"]: err(f"音频轨内事件重叠: {tr['name']}")
    if errs:
        print(f"校验失败（{len(errs)} 项）："); [print("  ✗", e) for e in errs]
        c.receipt("validate", False, f"{len(errs)} errors"); sys.exit(1)
    nclips = sum(len(t["clips"]) for t in c.tl["video_tracks"])
    msg = (f"{len(srcs)} 源 / {len(c.tl['video_tracks'])} 视频轨 {nclips} clips / "
           f"{len(c.tl['subtitles'])} 字幕 / "
           f"{len(c.tl['audio_tracks'])} 音轨 {sum(len(t['events']) for t in c.tl['audio_tracks'])} 事件")
    print("✓ 校验通过：" + msg); c.receipt("validate", True, msg)

# ---------------------------------------------------------------- audit
# 指标审计（建议性）：对照主流 UP主 实测基准（叙事风格分析/画面构成与节奏，2026-08-21）
# 检查的是"编排合理性"而非机制合法性——只提示，不阻断渲染。validate 是硬门，audit 是体检。
def _union(wins):
    out = []
    for s, e in sorted(wins):
        if out and s <= out[-1][1]: out[-1] = (out[-1][0], max(out[-1][1], e))
        else: out.append((s, e))
    return out

def _complement(wins, total):
    out, cur = [], 0.0
    for s, e in _union(wins):
        if s > cur: out.append((cur, s))
        cur = max(cur, e)
    if cur < total: out.append((cur, total))
    return out

def audit(c: Ctx):
    D = c.dur * c.spf
    lines = []
    def ind(name, status, detail=""):
        lines.append({"name": name, "status": status, "detail": detail})
        print(f"  {'✓' if status=='ok' else ('⚠' if status=='warn' else 'ℹ')} {name}  {detail}")
    # 1) 画面构成（kind 时间占比，同一 kind 多轨取并集）
    kind_wins = {}
    for tr in c.tl["video_tracks"]:
        kd = tr.get("kind", "other")
        kind_wins.setdefault(kd, [])
        kind_wins[kd] += [(cl["dst_in"] * c.spf, (cl["dst_in"] + cl["dur"]) * c.spf) for cl in tr["clips"]]
    comp = {kd: sum(e - s for s, e in _union(w)) for kd, w in kind_wins.items()}
    comp_s = " / ".join(f"{k} {v/D*100:.0f}%" for k, v in sorted(comp.items()))
    ind("画面构成", "ok", comp_s)
    if "aroll" not in comp and "gfx" not in comp and "screen" not in comp:
        ind("模块完整性", "warn", "无 aroll/screen/gfx——纯 B-roll 成片，请确认是有意为之")
    for kd in ("broll", "screen", "gfx"):
        if kd not in comp: ind(f"缺少 {kd} 模块", "info", "可用该类画面增加视觉变化（主流技术视频均有覆盖）")
    # 2) 黑场窗口（无任何视频层的帧段——机制合法，但多半是编排遗漏）
    all_v = [w for ws in kind_wins.values() for w in ws]
    blacks = [(s, e) for s, e in _complement(all_v, D) if e - s > 0.5]
    if blacks:
        fmt = "、".join(f"{s:.1f}-{e:.1f}s" for s, e in blacks[:5])
        ind("黑场窗口（无视频内容）", "warn", f"{len(blacks)} 段：{fmt}（若非有意留黑请补覆盖层）")
    else:
        ind("黑场窗口", "ok", "全程有画面")
    # 3) 无声窗口（BGM 铁律：主流视频口播间隙 100% 有音乐/音效铺底）
    all_a = [(e["in"] * c.spf, e["out"] * c.spf) for tr in c.tl["audio_tracks"] for e in tr["events"]]
    silents = [(s, e) for s, e in _complement(all_a, D) if e - s > 0.3]
    if silents:
        fmt = "、".join(f"{s:.1f}-{e:.1f}s" for s, e in silents[:5])
        ind("无声窗口（无任何音频事件）", "warn", f"{len(silents)} 段：{fmt}——bed 轨应全程铺底")
    else:
        ind("无声窗口", "ok", "全程有音频（BGM 铺底达标）")
    # 4) 口播覆盖率（voice 轨并集；主流观察 83-99.7%，本项目目标 85-90%）
    voice_tr = [tr for tr in c.tl["audio_tracks"] if tr["name"] == "voice"]
    if voice_tr:
        vw = _union([(e["in"] * c.spf, e["out"] * c.spf) for e in voice_tr[0]["events"]])
        cov = sum(e - s for s, e in vw) / D
        st = "ok" if 0.70 <= cov <= 0.98 else "warn"
        ind("口播覆盖率（voice 轨）", st, f"{cov*100:.1f}%（主流观察 83-99.7%，目标 85-90%）")
        # 5) 语速（字幕字数 ÷ voice 占用秒数；标准值 5.5 字/s＝主流中位，基准见 data/speech_rate.json）
        chars = sum(len(s["text"]) for s in c.tl["subtitles"])
        vsec = sum(e - s for s, e in vw)
        if vsec > 0 and chars > 0:
            rate = chars / vsec
            st = "ok" if 4.8 <= rate <= 6.3 else "warn"
            ind("全片语速（字/秒）", st,
                f"{rate:.2f} 字/s（标准值 5.5，偏差 {(rate-5.5)/5.5*100:+.0f}%；"
                f"主流中位 5.56、P25-P75 4.95~5.76；<4.8 偏慢拖节奏，>6.3 高密度口播需确认是有意为之）")
    else:
        ind("voice 轨", "warn", "没有名为 voice 的音轨，口播覆盖率/语速无法评估")
    # 6) 字幕质量（阅读速度/长度/最短时长/相互重叠）
    #    瞬时语速基准（15758 行实测）：P50=5.68 P90=7.41 P95=8.02 P99=9.37；>8字/s 占 5.1%
    n_fast = n_long = n_short = 0
    for s in c.tl["subtitles"]:
        d = (s["out"] - s["in"]) * c.spf
        n = len(s["text"])
        if n / d > 9.0: n_fast += 1
        elif n / d > 7.5: n_long += 1
        if d < 0.6: n_short += 1
    subs_s = sorted(c.tl["subtitles"], key=lambda x: x["in"])
    overlaps = sum(1 for a, b in zip(subs_s, subs_s[1:]) if a["out"] > b["in"])
    st = "ok" if (n_fast == 0 and overlaps == 0) else "warn"
    ind("字幕阅读速度", st,
        f"过快(>9字/s≈P99) {n_fast} 条 / 偏快(>7.5≈P92) {n_long} 条 / 过短(<0.6s) {n_short} 条 / 相互重叠 {overlaps} 条")
    rep = {"benchmarks": "叙事风格分析与稿子方向.md / 画面构成与节奏.md（2026-08-21 实测）",
           "indicators": lines}
    (c.out / "audit_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2))
    warns = sum(1 for x in lines if x["status"] == "warn")
    print(f"审计：{len(lines)} 项指标，{warns} 条建议（详见 out/audit_report.json，建议性不阻断）")
    c.receipt("audit", True, f"{len(lines)} indicators, {warns} advisories")

# ---------------------------------------------------------------- prepare
def prepare(c: Ctx):
    (c.cache / "audio").mkdir(parents=True, exist_ok=True)
    for s in c.tl["sources"]:
        wav = c.cache / "audio" / f"{s['id']}.wav"
        if not wav.exists():
            c.run(["ffmpeg", "-v", "error", "-y", "-i", str(c.src_path(s["id"])),
                   "-vn", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(wav)])
    msg = f"{len(c.tl['sources'])} → 48kHz stereo PCM"
    print("✓ 音频提取：" + msg); c.receipt("prepare", True, msg)

# ---------------------------------------------------------------- gfx
def gfx(c: Ctx):
    total = 0
    for tr in c.tl["video_tracks"]:
        if tr.get("kind") != "gfx": continue
        for cl in tr["clips"]:
            cid = Path(cl["src"]).stem          # 缓存键 = html 文件名去扩展（同 html 复用帧缓存）
            n = cl["dur"]
            d = c.cache / cid
            if d.exists() and len(list(d.glob("f*.png"))) != n:  # 帧数不符（换时长）→ 清缓存重渲
                import shutil; shutil.rmtree(d)
            d.mkdir(parents=True, exist_ok=True)
            if len(list(d.glob("f*.png"))) == n:
                print(f"✓ {cid}: {n} 帧已缓存"); continue
            html = (c.root / cl["src"]).resolve()
            def shot(i, d=d, html=html):
                return subprocess.run(
                    ["google-chrome", "--headless", "--disable-gpu", "--hide-scrollbars",
                     "--window-size=1920,1080", "--default-background-color=00000000",
                     f"--screenshot={d}/f{i:04d}.png", f"file://{html}?f={i}"], capture_output=True)
            with ThreadPoolExecutor(6) as ex: list(ex.map(shot, range(n)))
            got = len(list(d.glob("f*.png")))
            if got != n: print(f"✗ {cid}: 期望 {n} 帧得到 {got}"); sys.exit(1)
            print(f"✓ {cid}: 渲染 {n} 帧（含 alpha）")
            total += 1
    c.receipt("gfx", True, f"{total} gfx clips")

# ---------------------------------------------------------------- video
def nvenc_ok(c: Ctx) -> bool:
    import os
    if os.environ.get("VTX_NO_NVENC"): return False
    if DRY: return True
    # 探测尺寸须 ≥ NVENC 最小宽 145（128 会假失败）
    r = c.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x144:d=0.04",
               "-frames:v", "1", "-c:v", "h264_nvenc", "-f", "null", "-"], need=False)
    return r != "" and r.returncode == 0

def build_video(c: Ctx):
    draft = getattr(c, "draft", False); nframes = getattr(c, "frames", None)
    f = 0.5 if draft else 1.0
    W, H = ((c.w // 2) // 2 * 2, (c.h // 2) // 2 * 2) if draft else (c.w, c.h)
    (c.cache / "seg").mkdir(parents=True, exist_ok=True)
    tracks = c.tl["video_tracks"]
    # 统一预处理：全屏 clip（无 scale/x/y）经 transform 归一到项目分辨率；窗口 clip 按 scale 缩放。
    # 需要 alpha（opacity<1 或 fade）→ ProRes 4444；纯不透明全屏 → libx264。所有轨道等价。
    layers = []      # 每项: {cl, seg|pngdir, x, y, w2, h2|None, gfx:bool, ti}
    for ti, tr in enumerate(tracks):
        for ci, cl in enumerate(tr["clips"]):
            kind = tr.get("kind")
            if kind == "gfx":
                layers.append({"cl": cl, "pngdir": c.cache / Path(cl["src"]).stem,
                               "x": cl.get("x", 0), "y": cl.get("y", 0), "w2": None, "h2": None,
                               "gfx": True, "ti": ti})
                continue
            windowed = any(k in cl for k in ("scale", "x", "y"))
            need_alpha = cl.get("opacity", 1) < 1 or cl.get("fade_in_frames") or cl.get("fade_out_frames")
            vf = [f"trim=start_frame={cl['src_in']}:end_frame={cl['src_out']}", "setpts=PTS-STARTPTS",
                  f"fps={c.fps}"]
            if windowed:
                sc = cl.get("scale", 1.0)
                i = c.probe(c.src_path(cl["src"]))
                w2 = int(i["w"] * sc) // 2 * 2; h2 = int(i["h"] * sc) // 2 * 2
                vf.append(f"scale={w2}:{h2}:flags=lanczos")
                x, y = cl.get("x", 0), cl.get("y", 0)
            else:
                vf += [c.crop169(cl["src"]), f"scale={c.w}:{c.h}:flags=lanczos"]
                w2 = h2 = None; x, y = 0, 0
            if need_alpha:
                vf.append("format=yuva420p")
                if cl.get("fade_in_frames"):
                    vf.append(f"fade=t=in:st=0:d={cl['fade_in_frames']*c.spf:.6f}:alpha=1")
                if cl.get("fade_out_frames"):
                    vf.append(f"fade=t=out:st={(cl['dur']-cl['fade_out_frames'])*c.spf:.6f}:d={cl['fade_out_frames']*c.spf:.6f}:alpha=1")
                if cl.get("opacity", 1) < 1: vf.append(f"colorchannelmixer=aa={cl['opacity']}")
                seg = c.cache / "seg" / f"t{ti:02d}c{ci:02d}.mov"
                vcodec_seg = ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le"]
            else:
                vf.append("format=yuv420p")
                seg = c.cache / "seg" / f"t{ti:02d}c{ci:02d}.mp4"
                vcodec_seg = ["-c:v", "libx264", "-crf", "14", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
            # seg 缓存（2026-09-08）：存在且流头帧数=clip 时长即跳过（只读容器元数据，
            # 不用 -count_frames 解码数帧——33 个 seg 解码计数实测 5-6 分钟）。此前每次
            # video 全量重编 33 个 seg ≈4.5 分钟常量成本。源/clip 变更后须 rm cache/seg
            # 对应文件（同 prepare/gfx 存在性缓存哲学，见 AGENTS D29）。
            if seg.exists():
                fr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                     "-show_entries", "stream=nb_frames", "-of", "csv=p=0", str(seg)],
                                    capture_output=True, text=True).stdout.strip()
                if fr.isdigit() and int(fr) == cl["dur"]:
                    layers.append({"cl": cl, "seg": seg, "x": x, "y": y, "w2": w2, "h2": h2,
                                   "gfx": False, "ti": ti})
                    continue
            c.run(["ffmpeg", "-v", "error", "-y", "-i", str(c.src_path(cl["src"])),
                   "-vf", ",".join(vf), "-frames:v", str(cl["dur"]), "-an",
                   *vcodec_seg, str(seg)])
            layers.append({"cl": cl, "seg": seg, "x": x, "y": y, "w2": w2, "h2": h2, "gfx": False, "ti": ti})
    def fc(fr):
        cs = round(fr * 100 / c.fps); hh, rem = divmod(cs, 360000); mm, rem = divmod(rem, 6000); ss, cs2 = divmod(rem, 100)
        return f"{hh}:{mm:02d}:{ss:02d}.{cs2:02d}"
    ass = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2", "",
           "[V4+ Styles]",
           "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
           f"Style: Def,Noto Sans CJK SC,{58*f:.0f},&H00F8FBFF,&H00000000,&H80000000,1,1,{3*f:.1f},{2*f:.1f},2,{60*f:.0f},{60*f:.0f},{60*f:.0f},1",
           "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for s in c.tl["subtitles"]:
        ass.append(f"Dialogue: 0,{fc(s['in'])},{fc(s['out'])},Def,,0,0,0,,{s['text']}")
    (c.cache / ("subs_draft.ass" if draft else "subs.ass")).write_text("\n".join(ass))

    def composite(layers_subset, out_path, with_subs):
        cmd = ["ffmpeg", "-v", "error", "-y",
               "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={c.fps}:d={c.dur * c.spf:.3f}"]
        for L in layers_subset:
            # 头窗草稿（--frames）：截短每路输入。否则 -frames:v 触发的 drain 会把
            # 52 路输入全部解到 EOF（voice-ime 实测 300 帧耗 4:32，全量解码即瓶颈，
            # 2026-09-08 定位）；截短后仅解码头窗所需帧。
            topts = (["-t", f"{max(2, nframes - L['cl']['dst_in']) / c.fps:.3f}"]
                     if (draft and nframes) else [])
            if L["gfx"]:
                cmd += ["-framerate", str(c.fps), "-start_number", "0", *topts,
                        "-i", str(L["pngdir"] / "f%04d.png")]
            else:
                cmd += [*topts, "-i", str(L["seg"])]
        parts = ["[0:v]format=yuv420p[cv]"]; cur = "cv"
        for k, L in enumerate(layers_subset):
            cl = L["cl"]; idx = k + 1
            t0 = cl["dst_in"] * c.spf; t1 = (cl["dst_in"] + cl["dur"]) * c.spf
            # 次输入提前半帧（dst_in=0 无余量除外）：PNG 序列输入瞬间涌入时 framesync
            # 对「次帧 PTS 恰等于主帧」的首帧偶发配对失败（该层 dst_in 首帧整帧丢失=黑帧，
            # 非确定性复现，2026-08-25 voice-ime 轮7 实测）。次帧恒早于主帧半个采样后，
            # framesync 始终能取到「最近过去帧」配对；视觉前移 1/48s 无感。enable 窗口
            # 同步放宽半帧保持覆盖不变。
            pre = 0.5 if cl["dst_in"] > 0 else 0
            # 草稿模式：各层统一缩到 f 倍（seg/PNG 均按 iw/ih 等比，含窗口 clip）
            shrink = f"scale=trunc(iw*{f}/2)*2:trunc(ih*{f}/2)*2," if draft else ""
            # 2026-09-08 v3.5 渲染棒：tpad 首帧克隆垫片。根因（A/B 已证编码器无关）：
            # 57 层大 graph 下 framesync 对「窗口开启首帧」确定性配对失败——主链帧到达
            # overlay 时次输入首帧尚未产出，framesync 透传主帧=该层整帧丢失（voice-ime
            # v3.5 五点 100% 复现：3859/6049/6499/11779/14029，NVENC/x264 同集）。
            # 修复：次输入头部克隆 1 垫帧（pts=窗口前 1.5 帧），使 slave 流在主帧到达
            # 前已进入传输状态；真首帧 pts 与映射不变，垫帧时刻 enable=0 永不显示。
            # stop 垫：流尾克隆尾帧 1 帧（pts=原尾帧+1/30），把 EOF 边界的主帧配对
            # 异常窗口推开 1 帧；克隆尾帧即使被 enable 右端点（闭区间）拾取，画面与
            # 原尾帧完全相同，视觉零差异（voice-ime r5 实测 5 确定点全消、剩 3858
            # 尾帧侧单点，r6 验证）。
            # ⚠ v3.6 渲染棒实测教训（2026-09-08）：垫帧量扩大（start 2/stop 24）对
            # 「主链中段丢帧」型病灶无效（病灶在次输入流传输中段而非头尾边界），
            # 且 stop 过大时克隆尾帧会被输出为片尾 extra 帧（voice-ime v3.6 r3：
            # 尾部多 10 帧、时长 594.833）。保持 1/1，不要再加大。
            pad = (f"tpad=start_duration={1/c.fps:.6f}:start_mode=clone:"
                   f"stop_duration={1/c.fps:.6f}:stop_mode=clone," if pre else "")
            shift = cl['dst_in'] - pre - (1 if pad else 0)
            parts.append(f"[{idx}:v]{shrink}{pad}setpts=PTS+{shift}/{c.fps}/TB[L{k}T]")
            # eof_action=repeat（非 pass；ffmpeg≥5 枚举名）：pass 在「窗口右端点主帧遇次输入
            # EOF」时直通丢层（黑帧）；repeat 克隆尾帧，窗口外由 enable=0 屏蔽，无残留风险。
            parts.append(f"[{cur}][L{k}T]overlay={int(L['x'] * f)}:{int(L['y'] * f)}:format=auto:"
                         f"enable='between(t,{t0 - pre * c.spf:.6f},{t1:.6f})':eof_action=repeat[L{k}]")
            cur = f"L{k}"
        if with_subs:
            sub_file = c.cache / ("subs_draft.ass" if draft else "subs.ass")
            parts.append(f"[{cur}]subtitles={sub_file}[vout]")
        else:
            parts.append(f"[{cur}]null[vout]")
        if draft:
            if nvenc_ok(c):
                vcodec = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0",
                          "-pix_fmt", "yuv420p"]; enc = "NVENC p4"
            else:
                vcodec = ["-c:v", "libx264", "-crf", "26", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
                enc = "x264 veryfast"
        elif getattr(c, "gpu", False):
            if nvenc_ok(c):
                # GPU 正式渲染：p7 最高质量档 + tune hq + vbr-cq17（对 1080p 交付与
                # 下游 4K 重编码透明；voice-ime 2026-09-08 引入）
                vcodec = ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "17",
                          "-b:v", "0", "-pix_fmt", "yuv420p"]; enc = "NVENC p7"
            else:
                print("⚠ NVENC 不可用，回落 x264 medium（官方参数）")
                vcodec = ["-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p"]
                enc = "x264 medium(回落)"
        else:
            vcodec = ["-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p"]
            enc = "x264 medium"
        # -filter_complex_threads 1：filtergraph 单线程执行（2026-09-08 v3.6 渲染棒）。
        # 根因定位（voice-ime v3.6，61 层 graph 三渲实测）：多线程 filtergraph 下，
        # 多个 framesync 的 enable 窗口边界在 ±20 帧内密集交叠的时间点发生主链整帧
        # 丢失（PTS 空洞，非确定性但病灶区 100% 复现：draft 缺 3 / r1 缺 8 且伴
        # [6180,6193) 近黑崩溃带 / r2 缺 6 / r3 垫帧扩版缺 10——垫帧参数无关，病灶
        # 在次输入流传输中段）。overlay 链本身为串行依赖（L0→L1→…→L60），线程化
        # 无并行收益，强制单线程消除调度竞态。
        # 输出端 CFR 强制（2026-09-08 v3.6 渲染棒，主控拍板兜底）：61 层大 graph 的
        # framesync 存在系统性「层窗口边界密集区中段主链丢帧」（voice-ime v3.6 六渲
        # 实测：NVENC/x264、多/单线程、tpad 1/1 与 2/24 参数无关，病灶 100% 落在
        # 多层共享相近端点的 ±20 帧带，缺连续 2-4 帧 PTS 空洞）。-fps_mode cfr -r 30
        # 使输出端按 30fps 网格强制补帧（相邻帧复制），缺帧恰好落在层切换点（画面
        # 相对静止的 gfx 淡出带），1-4 帧复制视觉不可见。交付口径：这些帧是复制
        # 补偿帧而非原渲染帧，须 blackdetect+帧数+断点内容抽验三重确认。
        c.run(cmd + ["-filter_complex_threads", "1",
                     "-filter_complex", ";".join(parts), "-map", "[vout]",
                     "-frames:v", str(nframes if (draft and nframes) else c.dur),
                     "-fps_mode", "cfr", "-r", str(c.fps),
                     *vcodec, str(out_path)])
        return enc

    enc = composite(layers, c.out / ("draft.mp4" if draft else "video_noaudio.mp4"), True)
    # qc_clean = 仅底轨（tracks[0]）的基准画面，供 QC 做上层窗口生效比对（草稿跳过）
    if not draft:
        bottom = [L for L in layers if L["ti"] == 0]
        composite(bottom, c.cache / "qc_clean.mp4", False)
    msg = f"黑场画布 + {len(tracks)} 轨 {len(layers)} 层等价叠加（时移）+ 字幕｜{enc}"
    if draft:
        msg += f"｜草稿 {W}x{H}" + (f" 前 {nframes} 帧" if nframes else "")
    print("✓ 视频合成：" + msg)
    c.receipt("video_draft" if draft else "video", True, msg)

# ---------------------------------------------------------------- audio
def build_audio(c: Ctx):
    atracks = c.tl["audio_tracks"]
    evs = [(ti, tr, e) for ti, tr in enumerate(atracks) for e in tr["events"]]
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for ti, tr, e in evs:
        if e.get("loop"): cmd += ["-stream_loop", "-1"]
        cmd += ["-i", str(c.cache / "audio" / f"{e['src']}.wav")]
    # 事件链（atrim 采样网格/音量/声像/淡变/adelay）→ 轨内子混音 → 补齐成片全长
    chains = []
    trk_lbl = {}
    k = 0
    for ti, tr in enumerate(atracks):
        ev_lbls = []
        for e in tr["events"]:
            dur = e["out"] - e["in"]; sin = e.get("src_in", 0)
            fl = [f"atrim=start_sample={sin*c.spf_samples}:end_sample={(sin+dur)*c.spf_samples}", "asetpts=PTS-STARTPTS"]
            fl.append(f"volume={e['volume']}")
            if e.get("pan"):
                p = e["pan"]; lg = min(1.5, 1 - p * .5); rg = min(1.5, 1 + p * .5)
                fl.append(f"pan=stereo|c0={lg:.3f}*c0|c1={rg:.3f}*c1")
            if e.get("fade_in_frames"): fl.append(f"afade=t=in:st=0:d={e['fade_in_frames']*c.spf:.6f}")
            if e.get("fade_out_frames"):
                fl.append(f"afade=t=out:st={(dur-e['fade_out_frames'])*c.spf:.6f}:d={e['fade_out_frames']*c.spf:.6f}")
            fl.append(f"adelay=delays={e['in']/c.fps*1000:.3f}:all=1")
            chains.append(f"[{k}:a]" + ",".join(fl) + f"[e{k}]")
            ev_lbls.append(f"[e{k}]")
            k += 1
        pad = (f"apad=whole_dur={c.dur*c.spf:.3f},"
               f"atrim=end_sample={c.dur*c.spf_samples},asetpts=PTS-STARTPTS")
        if len(ev_lbls) == 1:
            chains.append(f"{ev_lbls[0]}{pad}[t{ti}]")
        else:
            chains.append(f"{''.join(ev_lbls)}amix=inputs={len(ev_lbls)}:duration=longest:normalize=0,{pad}[t{ti}]")
        trk_lbl[ti] = f"[t{ti}]"
    # 音乐避让：duck:true 的轨被 duck_by（默认 voice）的混音经 sidechaincompress 压低
    duck_tis = [ti for ti, tr in enumerate(atracks) if tr.get("duck")]
    if duck_tis:
        by_names = {n for ti in duck_tis for n in atracks[ti].get("duck_by", ["voice"])}
        sc_tis = [ti for ti, tr in enumerate(atracks) if tr["name"] in by_names and ti not in duck_tis]
        if sc_tis:
            if len(sc_tis) == 1:
                # 修复（2026-09-10，voice-ime BGM v2 轮定位）：asplit=恰 N 路把 sidechain 轨
                # （voice）整轨吞尽，终混仍引用原标签→该 ffmpeg 版本静默解析为空流=口播
                # 整轨缺失（复现：最小三轨 bed+voice+duck bgm）。多分一路并重绑原轨标签，
                # 终混引用恢复有效。
                chains.append(f"{trk_lbl[sc_tis[0]]}asplit={len(duck_tis) + 1}" +
                              "".join(f"[sc{j}]" for j in range(len(duck_tis))) +
                              trk_lbl[sc_tis[0]])
            else:
                # 同族修复：多 sidechain 轨先各自 asplit 保一路原标签再 amix 做 key，
                # 否则 amix 消费原标签后终混同样丢这些轨。
                for ti in sc_tis:
                    chains.append(f"{trk_lbl[ti]}asplit=2[scx{ti}]{trk_lbl[ti]}")
                chains.append(f"{''.join(f'[scx{ti}]' for ti in sc_tis)}"
                              f"amix=inputs={len(sc_tis)}:duration=longest:normalize=0[scm]")
                chains.append(f"[scm]asplit={len(duck_tis)}" + "".join(f"[sc{j}]" for j in range(len(duck_tis))))
            for j, ti in enumerate(duck_tis):
                chains.append(f"{trk_lbl[ti]}[sc{j}]sidechaincompress=threshold=0.03:ratio=6:"
                              f"attack=150:release=700:makeup=1[td{ti}]")
                trk_lbl[ti] = f"[td{ti}]"
        else:
            print("⚠ duck 声明无效：找不到 duck_by 指向的非 duck 轨，跳过避让")
    # 总混音（不归一，保留音量比例）→ 定长。响度归一改两遍法（2026-09-08）：
    # 第一遍 ebur128 实测原始混音 I，第二遍施加恒定静态增益到 -14 LUFS。
    # 单遍 dynamic loudnorm 在长节目上增益随内容游走——voice-ime v3.2/v3.3 实测
    # 前段 -11.8、后段 -21.7~-32 LUFS 的区域塌陷（2026-09-08 定位）。
    # 峰值安全由项目侧 post_loudness.sh 的 alimiter 兜底，响度链顺序不变。
    import re as _re
    mix_head = (f"{''.join(trk_lbl[ti] for ti in range(len(atracks)))}"
                f"amix=inputs={len(atracks)}:duration=longest:normalize=0,aresample=48000,"
                f"atrim=end_sample={c.dur*c.spf_samples},asetpts=PTS-STARTPTS")
    gain = 0.0
    if not DRY:
        meas_cmd = list(cmd)
        meas_cmd[meas_cmd.index("error")] = "info"  # -v error → info：ebur128 Summary 是 info 级
        r = c.run(meas_cmd + ["-filter_complex", ";".join(chains + [mix_head + ",ebur128=framelog=quiet[amet]"]),
                              "-map", "[amet]", "-f", "null", "-"])
        mI = _re.search(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
        if not mI: print("✗ 原始混音响度测量失败"); sys.exit(1)
        raw_i = float(mI.group(1)); gain = -14.0 - raw_i
        print(f"  原始混音 I={raw_i:.1f} LUFS → 静态增益 {gain:+.1f}dB")
    c.run(cmd + ["-filter_complex", ";".join(chains + [mix_head + f",volume={gain:+.2f}dB[aout]"]),
                 "-map", "[aout]", "-c:a", "pcm_s16le", "-ar", "48000", str(c.out / "audio.wav")])
    msg = f"{len(atracks)} 轨 {len(evs)} 事件（帧网格对齐"
    if duck_tis: msg += f"，{len(duck_tis)} 轨 duck 避让"
    msg += f"，响度 -14 LUFS（两遍静态增益）"
    print("✓ 音频混音：" + msg); c.receipt("audio", True, msg)

def mux(c: Ctx):
    if getattr(c, "draft", False):
        c.run(["ffmpeg", "-v", "error", "-y", "-i", str(c.out / "draft.mp4"),
               "-i", str(c.out / "audio.wav"), "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
               "-movflags", "+faststart", str(c.out / "draft_final.mp4")])
        print("✓ 封装 draft_final.mp4（草稿）"); c.receipt("mux_draft", True); return
    # D31 坑位：原生 aac 对真人素材（波峰因子 23-25dB 密集瞬态）解码真峰过冲实测可达 +8.5dB——正式封装用 libfdk_aac
    encs = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    acodec = "libfdk_aac" if "libfdk_aac" in encs else "aac"
    if acodec == "aac":
        print("⚠ libfdk_aac 不可用，回落原生 aac——终检必须复测编码后解码 TP（edit.md 坑表）")
    c.run(["ffmpeg", "-v", "error", "-y", "-i", str(c.out / "video_noaudio.mp4"),
           "-i", str(c.out / "audio.wav"), "-c:v", "copy", "-c:a", acodec, "-b:a", "192k",
           "-movflags", "+faststart", str(c.out / "final.mp4")])
    print(f"✓ 封装 final.mp4（{acodec} 192k）"); c.receipt("mux", True, f"{acodec} 192k")

# ---------------------------------------------------------------- qc
def qc(c: Ctx):
    rep = {"checks": []}
    def ok(name, passed, detail=""):
        rep["checks"].append({"name": name, "pass": bool(passed), "detail": detail})
        print(("  ✓ " if passed else "  ✗ ") + name + (f"  {detail}" if detail else ""))
    r = c.run(["ffprobe", "-v", "error", "-count_frames", "-print_format", "json",
               "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames,width,height",
               "-show_entries", "format=duration", str(c.out / "final.mp4")])
    d = json.loads(r.stdout); st = d["streams"][0]
    nb = int(st["nb_read_frames"]); dur = float(d["format"]["duration"])
    ok(f"成片帧数 = {c.dur}", nb == c.dur, f"实测 {nb}")
    ok(f"成片时长 ≈ {c.dur * c.spf:.2f}s", abs(dur - c.dur * c.spf) < 0.05, f"实测 {dur:.3f}s")
    ok(f"分辨率 {c.w}x{c.h}", st["width"] == c.w and st["height"] == c.h)
    tracks = c.tl["video_tracks"]
    # 底轨（tracks[0]）全屏 clip 的切点帧比对（与 qc_clean 基准逐像素核对）
    if tracks and tracks[0].get("kind") != "gfx":
        for cl in sorted(tracks[0]["clips"], key=lambda x: x["dst_in"]):
            if "src_in" not in cl: continue
            j = max(2, cl.get("fade_in_frames", 0) + 1)
            if cl.get("fade_out_frames"): j = max(j, cl["dur"] - cl["fade_out_frames"] - 1)
            j = min(j, cl["dur"] - 1); df = cl["dst_in"] + j; sf = cl["src_in"] + j
            f1, f2 = c.cache / "qc_a.raw", c.cache / "qc_b.raw"
            c.run(["ffmpeg", "-v", "error", "-y", "-i", str(c.cache / "qc_clean.mp4"),
                   "-vf", f"select=eq(n\\,{df}),scale=48:27,format=gray", "-frames:v", "1", "-f", "rawvideo", str(f1)])
            c.run(["ffmpeg", "-v", "error", "-y", "-i", str(c.src_path(cl["src"])),
                   "-vf", f"select=eq(n\\,{sf}),{c.crop169(cl['src'])},scale={c.w}:{c.h}:flags=lanczos,scale=48:27,format=gray",
                   "-frames:v", "1", "-f", "rawvideo", str(f2)])
            a, b = f1.read_bytes(), f2.read_bytes()
            diff = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
            ok(f"底轨切点帧比对 dst={cl['dst_in']} src={cl['src']}:{sf}", diff < 6.0, f"平均像素差 {diff:.2f}/255")
    def fdiff(n, crop=None):
        vf = f"select=eq(n\\,{n})," + (f"crop={crop}," if crop else "") + "scale=48:27,format=gray"
        for src, tmp in ((c.out / "final.mp4", c.cache / "q1.raw"), (c.cache / "qc_clean.mp4", c.cache / "q2.raw")):
            c.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", vf,
                   "-frames:v", "1", "-f", "rawvideo", str(tmp)])
        a, b = (c.cache / "q1.raw").read_bytes(), (c.cache / "q2.raw").read_bytes()
        mean = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
        cover = sum(1 for x, y in zip(a, b) if abs(x - y) > 24) / len(a)
        return mean, cover
    # 上层轨（tracks[1:]）逐 clip 窗口生效检查：窗口 clip 比对其放置区域，全屏 clip 比对整帧
    for tr in tracks[1:]:
        for ci, cl in enumerate(tr["clips"]):
            win = f" t{tracks.index(tr)}c{ci}" if len(tr["clips"]) > 1 else ""
            if tr.get("kind") == "gfx":
                probes = sorted({cl["dst_in"] + cl["dur"] // 4, cl["dst_in"] + cl["dur"] // 2,
                                 cl["dst_in"] + cl["dur"] - cl["dur"] // 4})
                stats = [fdiff(m) for m in probes]
                dmax = max(s[0] for s in stats); cov = max(s[1] for s in stats)
                ok(f"gfx 窗口 {Path(cl['src']).stem} 生效", dmax > 5.0 or cov > 0.03,
                   f"采样 {probes} 最大均值差 {dmax:.1f}，覆盖 {cov*100:.1f}%")
                continue
            windowed = any(k in cl for k in ("scale", "x", "y"))
            m = cl["dst_in"] + cl["dur"] // 2
            if windowed:
                i = c.probe(c.src_path(cl["src"]))
                sc = cl.get("scale", 1.0)
                w2 = int(i["w"] * sc); h2 = int(i["h"] * sc)
                crop = f"{w2}:{h2 + 2}:{max(0, cl.get('x', 0) - 10)}:{max(0, cl.get('y', 0) - 10)}"
                d_, cov = fdiff(m, crop=crop)
                ok(f"窗口 clip {tr['name']} @frame {m} 生效", d_ > 5.0 or cov > 0.03,
                   f"区域差 {d_:.1f}，覆盖 {cov*100:.1f}%")
            else:
                d_, cov = fdiff(m)
                ok(f"全屏 clip {tr['name']} @frame {m} 生效", d_ > 5.0 or cov > 0.03,
                   f"整帧差 {d_:.1f}，覆盖 {cov*100:.1f}%")
    # 找一个无任何上层（视频上层 clip/字幕）覆盖的空窗帧做泄漏检查；找不到则跳过
    covered = [(s["in"], s["out"]) for s in c.tl["subtitles"]]
    for tr in tracks[1:]:
        covered += [(cl["dst_in"], cl["dst_in"] + cl["dur"]) for cl in tr["clips"]]
    free_frame = next((f0 for f0 in range(c.dur - 1, 0, -1)
                       if not any(a_ <= f0 < b_ for a_, b_ in covered)), None)
    if free_frame is not None:
        d_, cov = fdiff(free_frame)
        ok(f"空窗无叠加泄漏 @frame {free_frame}", d_ < 2.0 and cov < 0.02, f"差异 {d_:.2f}，覆盖 {cov*100:.1f}%")
    # 响度归一验证（EBU R128 整合响度应 ≈ -14 LUFS）
    import re as _re
    r2 = c.run(["ffmpeg", "-nostats", "-i", str(c.out / "final.mp4"),
                "-af", "ebur128=framelog=quiet", "-f", "null", "-"])
    m2 = _re.search(r"I:\s*(-?[\d.]+) LUFS", r2.stderr)
    if m2:
        lufs = float(m2.group(1))
        ok("响度归一 ≈ -14 LUFS", abs(lufs + 14) <= 1.5, f"实测 {lufs} LUFS")
    import re
    def vol(f0, f1):
        r = c.run(["ffmpeg", "-v", "info", "-ss", f"{f0*c.spf:.3f}", "-t", f"{(f1-f0)*c.spf:.3f}",
                   "-i", str(c.out / "final.mp4"), "-map", "0:a", "-af", "volumedetect", "-f", "null", "-"])
        m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
        return float(m.group(1)) if m else -999
    stacked = None
    marks = [(e["in"], e["out"]) for tr in c.tl["audio_tracks"] for e in tr["events"]]
    for f0 in range(0, c.dur, 24):
        active = sum(1 for a_, b_ in marks if a_ <= f0 < b_)
        if stacked is None or active > stacked[0]:
            stacked = (active, f0)
    v1 = vol(stacked[1] + 2, stacked[1] + 10)
    ok(f"{stacked[0]} 轨并发窗口有能量", v1 > -50, f"mean {v1:.1f} dB @frame {stacked[1]}")
    (c.out / "qc_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2))
    failed = [x for x in rep["checks"] if not x["pass"]]
    print(f"QC：{len(rep['checks']) - len(failed)}/{len(rep['checks'])} 通过")
    c.receipt("qc", not failed, f"{len(rep['checks'])-len(failed)}/{len(rep['checks'])}")
    if failed: sys.exit(1)

STAGES = [("validate", validate), ("audit", audit), ("prepare", prepare), ("gfx", gfx),
          ("video", build_video), ("audio", build_audio), ("mux", mux), ("qc", qc)]

def main():
    global DRY
    ap = argparse.ArgumentParser(description="vtx 视频剪辑中间层")
    ap.add_argument("-t", "--timeline", default="timeline.json", help="timeline.json 路径（默认 ./timeline.json）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将执行的 ffmpeg 命令，不执行")
    ap.add_argument("--draft", action="store_true", help="草稿模式（仅 video/mux）：540p+NVENC 快速预览，不影响正式产物")
    ap.add_argument("--frames", type=int, metavar="N", help="video --draft 专属：只渲前 N 帧")
    ap.add_argument("--gpu", action="store_true", help="video 专属：正式参数 GPU 渲染（NVENC p7 hq cq17），产物仍为 video_noaudio.mp4")
    ap.add_argument("command", help="all | " + " | ".join(s[0] for s in STAGES) + " | clean")
    a = ap.parse_args()
    DRY = a.dry_run
    if a.frames and not (a.draft and a.command == "video"):
        print("--frames 仅在 video --draft 下可用"); sys.exit(1)
    if a.draft and a.command not in ("video", "mux"):
        print("--draft 仅作用于 video/mux 阶段"); sys.exit(1)
    if a.gpu and a.command != "video":
        print("--gpu 仅作用于 video 阶段"); sys.exit(1)
    # 不跟随符号链接（resolve() 会穿透 cp -rs 链接树等隔离副本，把 out/ 回执写到 live 项目）
    tp = Path(os.path.abspath(os.path.expanduser(a.timeline)))
    if not tp.exists(): print(f"timeline 不存在: {tp}"); sys.exit(1)
    c = Ctx(tp); c.cache.mkdir(exist_ok=True); c.out.mkdir(exist_ok=True)
    c.draft = a.draft; c.frames = a.frames; c.gpu = a.gpu
    if a.command == "clean":
        import shutil; shutil.rmtree(c.cache, ignore_errors=True); shutil.rmtree(c.out, ignore_errors=True)
        print("✓ 已清空 cache/ out/"); return
    names = [s[0] for s in STAGES] if a.command == "all" else [a.command]
    todo = STAGES if a.command == "all" else [next(s for s in STAGES if s[0] == a.command)]
    for name, fn in todo:
        print(f"== {name} =="); fn(c)
    print("完成：", c.out / "final.mp4" if a.command in ("all", "mux", "qc") else names)

if __name__ == "__main__":
    main()
