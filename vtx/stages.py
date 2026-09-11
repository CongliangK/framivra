"""vtx 阶段实现：validate / audit / prepare / audio / mux（原 vtx.py 各函数整体迁移）。

timeline 契约权威定义互指（三处需同步修改，2026-09-10 定）：
  ① 本文件 validate() —— 机制合法性权威（对照素材实测，硬门）
  ② vtx/timeline.schema.json —— 结构权威（字段/类型）
  ③ vtx/preview.html —— 可视化预览权威（timeline → 浏览器预览）
"""
import json, re, subprocess, sys
from pathlib import Path

from infra import Ctx, run_parallel

# ---- 命名常量（值不变，仅命名；2026-09-10 收拢）----
TARGET_LUFS = -14.0      # 项目目标响度（EBU R128，与 qc.py 同一权威值）
DUCK_THRESHOLD = 0.03    # sidechaincompress 阈值（duck 音乐避让）
DUCK_RATIO = 6
DUCK_ATTACK_MS = 150
DUCK_RELEASE_MS = 700
DUCK_MAKEUP = 1


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
    tasks = []
    for s in c.tl["sources"]:
        wav = c.cache / "audio" / f"{s['id']}.wav"
        if not wav.exists():
            tasks.append((f"audio {s['id']}",
                          ["ffmpeg", "-v", "error", "-y", "-i", str(c.src_path(s["id"])),
                           "-vn", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(wav)]))
    run_parallel(c, tasks, "音频提取")
    msg = f"{len(c.tl['sources'])} → 48kHz stereo PCM"
    print("✓ 音频提取：" + msg); c.receipt("prepare", True, msg)


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
                chains.append(f"{trk_lbl[ti]}[sc{j}]sidechaincompress=threshold={DUCK_THRESHOLD}:ratio={DUCK_RATIO}:"
                              f"attack={DUCK_ATTACK_MS}:release={DUCK_RELEASE_MS}:makeup={DUCK_MAKEUP}[td{ti}]")
                trk_lbl[ti] = f"[td{ti}]"
        else:
            print("⚠ duck 声明无效：找不到 duck_by 指向的非 duck 轨，跳过避让")
    # 总混音（不归一，保留音量比例）→ 定长。响度归一改两遍法（2026-09-08）：
    # 第一遍 ebur128 实测原始混音 I，第二遍施加恒定静态增益到目标响度。
    # 单遍 dynamic loudnorm 在长节目上增益随内容游走——voice-ime v3.2/v3.3 实测
    # 前段 -11.8、后段 -21.7~-32 LUFS 的区域塌陷（2026-09-08 定位）。
    # 峰值安全由项目侧 post_loudness.sh 的 alimiter 兜底，响度链顺序不变。
    mix_head = (f"{''.join(trk_lbl[ti] for ti in range(len(atracks)))}"
                f"amix=inputs={len(atracks)}:duration=longest:normalize=0,aresample=48000,"
                f"atrim=end_sample={c.dur*c.spf_samples},asetpts=PTS-STARTPTS")
    gain = 0.0
    if not c.dry:
        meas_cmd = list(cmd)
        meas_cmd[meas_cmd.index("error")] = "info"  # -v error → info：ebur128 Summary 是 info 级
        r = c.run(meas_cmd + ["-filter_complex", ";".join(chains + [mix_head + ",ebur128=framelog=quiet[amet]"]),
                              "-map", "[amet]", "-f", "null", "-"])
        mI = re.search(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
        if not mI: print("✗ 原始混音响度测量失败"); sys.exit(1)
        raw_i = float(mI.group(1)); gain = TARGET_LUFS - raw_i
        print(f"  原始混音 I={raw_i:.1f} LUFS → 静态增益 {gain:+.1f}dB")
    c.run(cmd + ["-filter_complex", ";".join(chains + [mix_head + f",volume={gain:+.2f}dB[aout]"]),
                 "-map", "[aout]", "-c:a", "pcm_s16le", "-ar", "48000", str(c.out / "audio.wav")])
    msg = f"{len(atracks)} 轨 {len(evs)} 事件（帧网格对齐"
    if duck_tis: msg += f"，{len(duck_tis)} 轨 duck 避让"
    msg += f"，响度 {TARGET_LUFS:g} LUFS（两遍静态增益）"
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
