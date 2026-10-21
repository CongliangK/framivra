#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""content_gates.py · 成片内容级验证门（通用化收编自 voice-ime ver_gates 系列的零耦合门）

用途：发布前对成片做与具体项目零耦合的硬门断言（音频响度结构/黑帧/规格/自定义）。
依赖：numpy + 系统 ffmpeg/ffprobe。

门清单（全部 argparse 可调阈值）：
  G1  口播窗−间隙窗中位响度差 ≥ --g1-min-gap-db（默认 4.5）+ 口播窗均匀极差
      ≤ --g1-spread-db（默认 8）——从 timeline voice 轨事件窗+audio-wav 分段 ebur128 测
  G2  全片综合响度 ∈ [--g2-lo=-15.5, --g2-hi=-14] LUFS
  G3  编码后解码真峰 ≤ --g3-tp=-1.0 dBFS（解码域测成片，不信 wav 域——voice-ime D31
      教训：原生 aac 对真人素材 TP 过冲失控，终检必测编码后解码 TP）
  G7  blackdetect 零黑帧（d=0.02 pix_th=0.08）
  G10 帧数/时长/帧率断言（从 timeline project.duration_frames 推导期望值）+ 打包编码器
      报告（h264/aac/faststart，NVENC 等 encoder tag 为信息项）
  --project-gates some.json：用户自定义简单字段相等断言，JSON 形如
      {"frames": 16746, "first_voice_frame": 30, "voice_events": 33}
      ——frames 对照成片帧数，其余字段对照 timeline 结构（voice 事件数/首口播帧等，
      支持任意 "{n}_events" 对照对应名音轨事件数）。

用法（项目根）：
  python3 content_gates.py [-t timeline.json] [--media out/final.mp4]
      [--audio-wav out/audio.wav] [--project-gates gates.json] [--skip G1,G8]
      [--count-frames] [--out out/content_gates.json]

--out JSON 留档（2026-09-14）：红队实证「音频链改动后必须重跑 gates」此前只打印
不落盘、无留档证据——加 --out 把本轮门结论（各门 pass/detail/skip）、实测数值
（metrics：G1 中位/极差、G2 I、G3 TP、G7 黑帧、G10 帧数…）、输入指纹（media/
timeline 的 md5/size/mtime）与时间戳写成一份 JSON。默认不写（兼容不变）。

方案三阶段1提速（2026-09-13，历史记录：
https://github.com/CongliangK/framivra/tree/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/plans-2026-09-11）：
  - G1 短窗 ebur128 逐窗 spawn 改分批（≤16 窗/进程，lufs_batch），失败回落逐窗；
  - G2/G3 优先消费响度回执（vtx/loudness_cache.py，同一解码域输入全链只测一次），
    命中则全片 pass 只保留 blackdetect 链；未命中照旧测 I/TP 并归档回执；
  - G10/PG:frames 帧数计数默认 -count_packets（仅解封装；h264 每包恰一帧，
    check_4k.sh 帧数门同口径），--count-frames 恢复 -count_frames 真解码口径。

来源项目：voice-ime（决策 D31/D32/D34/D35/D39；蓝本 cache/v39/ver_gates_v39.py）。
"""
import argparse
import hashlib
import json
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

# vtx 侧模块复用（方案三阶段1）：响度回执在 vtx/loudness_cache.py——v2 §1.4 包化：
# 注入 repo 根 + 包导入（不再注入 repo/vtx，防平铺/包双模块裂分）。
# P2a 迁入 apps/gates/ 后深一层：parents[0]=apps/gates、[1]=apps、[2]=repo 根
# （经 scripts/ 兼容 symlink 调用时 resolve() 先归位到本文件，两路径同锚定）。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from vtx import loudness_cache

ok_all = True

# --out JSON 留档收集器（2026-09-14）：gate()/note_skip() 顺手记账，主流程语义不变。
GATE_ROWS = []   # [{"name", "pass", "detail"}]
SKIP_ROWS = []   # ["SKIP Gx（原因）", ...]
METRICS = {}     # {"G1": {...实测数值...}, "G2_G3": {...}, ...}


def gate(name, cond, detail):
    global ok_all
    print(f"{'✓' if cond else '✗'} {name}: {detail}")
    ok_all &= bool(cond)
    GATE_ROWS.append({"name": name, "pass": bool(cond), "detail": detail})


def note_skip(msg):
    """SKIP 行统一出口：打印 + 留档收集（--out 时进 report.skipped；命名避开
    main 内的局部变量 skip——args.skip 解析出的门名集合）。"""
    print(msg)
    SKIP_ROWS.append(msg)


def fingerprint(p: Path) -> dict:
    """输入指纹（--out 留档用）：exists/bytes/mtime/md5（1MB 分块流式）。"""
    try:
        st = p.stat()
    except OSError:
        return {"exists": False}
    h = hashlib.md5()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return {"exists": True, "bytes": st.st_size,
            "mtime": time.strftime("%F %T", time.localtime(st.st_mtime)),
            "md5": h.hexdigest()}


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def lufs(a, b, src):
    r = run(["ffmpeg", "-nostdin", "-hide_banner", "-ss", f"{a:.3f}",
             "-t", f"{b-a:.3f}", "-i", str(src), "-vn",
             "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"])
    m = re.search(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    return float(m.group(1)) if m else None


# G1 批量化单批上限（方案三阶段1）：bench_p2 口径 63 窗 → 4 批，进程数 63→4；
# 批太大时单进程内多路解码互相争核，16 为保守值（总收益 ~3s 级，顺手项）。
G1_BATCH = 16


def lufs_batch(windows, src, batch=G1_BATCH):
    """多窗批量 ebur128（方案三阶段1）：每批 ≤batch 窗一个 ffmpeg 进程——N 路
    `-ss a -t d -i <同一源>` 输入 + filter_complex 每路 [i:a]ebur128=peak=true[ai]
    + N 个 `-map [ai] -f null -` 输出。返回与 windows 等长的 I 值列表（float）。

    取舍（数值等价的前提）：多实例 ebur128 的 Summary 块在 stderr 的完成序不
    保证，无法把第 k 个 I 对回第 k 窗——但 G1 只用中位/极差（与窗序无关），
    按值集合还原即等价；调用方须保证同批窗口同属一类（speech/gaps 分开批）。
    任一批 returncode != 0 或解析出的 Summary 数 < 窗数 → 整体返回 None
    （回落逐窗旧路径 lufs()，对齐 G2/G3+G7 合并 pass 的回落惯例）。
    """
    out = []
    for i in range(0, len(windows), batch):
        chunk = windows[i:i + batch]
        cmd = ["ffmpeg", "-nostdin", "-hide_banner"]
        fc = []
        for j, (a, b) in enumerate(chunk):
            cmd += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(src)]
            fc.append(f"[{j}:a]ebur128=peak=true:framelog=quiet[a{j}]")
        cmd += ["-filter_complex", ";".join(fc)]
        for j in range(len(chunk)):
            cmd += ["-map", f"[a{j}]", "-f", "null", "-"]
        r = run(cmd)
        vals = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
        if r.returncode != 0 or len(vals) != len(chunk):
            return None
        out += [float(v) for v in vals]
    return out


def moov_before_mdat(p):
    with open(p, "rb") as fh:
        pos = 0
        while True:
            fh.seek(pos)
            hdr = fh.read(8)
            if len(hdr) < 8:
                return False
            size, typ = struct.unpack(">I4s", hdr)
            typ = typ.decode("latin1")
            if typ == "moov":
                return True
            if typ == "mdat":
                return False
            if size == 1:
                ext = fh.read(8)
                size = struct.unpack(">Q", ext)[0]
            if size < 8:
                return False
            pos += size


def main():
    ap = argparse.ArgumentParser(description="成片内容级验证门（零耦合通用门）")
    ap.add_argument("-t", "--timeline", default="./timeline.json")
    ap.add_argument("--media", default="./out/final.mp4", help="成片（G2/G3/G7/G10 用）")
    ap.add_argument("--audio-wav", default="./out/audio.wav",
                    help="终混 wav（G1 优先用；缺省回落成片音轨）")
    ap.add_argument("--project-gates", default=None, help="自定义字段相等断言 JSON")
    ap.add_argument("--skip", default="", help="跳过的门，逗号分隔（如 G1,G7）")
    ap.add_argument("--g1-min-gap-db", type=float, default=4.5)
    ap.add_argument("--g1-spread-db", type=float, default=8.0)
    ap.add_argument("--g2-lo", type=float, default=-15.5)
    ap.add_argument("--g2-hi", type=float, default=-14.0)
    ap.add_argument("--g3-tp", type=float, default=-1.0)
    ap.add_argument("--g7-black-d", type=float, default=0.02)
    ap.add_argument("--g7-pix-th", type=float, default=0.08)
    ap.add_argument("--count-frames", action="store_true",
                    help="G10/PG:frames 帧数恢复 -count_frames 真解码计数口径"
                         "（默认 -count_packets 仅解封装：h264 每包恰一帧，"
                         "check_4k.sh 帧数门同口径先例，bench_p2 实测 26.6s→0.12s）")
    ap.add_argument("--out", default=None, metavar="PATH",
                    help="JSON 留档：各门 pass/detail/skip + 实测数值 + media/timeline"
                         " 指纹（md5/size/mtime）+ 时间戳（默认不写，兼容不变）")
    args = ap.parse_args()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    TL_PATH = Path(args.timeline)
    MEDIA = Path(args.media)
    AW = Path(args.audio_wav)

    # 相对路径默认按 CWD 解析；若落空且给了 timeline，则回退按 timeline 所在目录再解析一次
    # （防止跨目录运行时成片门被静默 SKIP——R5 验收 #1）
    def _rebase(p: Path) -> Path:
        if not p.is_absolute() and not p.exists() and TL_PATH.exists():
            cand = TL_PATH.parent / p
            if cand.exists():
                return cand
        return p

    MEDIA = _rebase(MEDIA)
    AW = _rebase(AW)

    have_tl = TL_PATH.exists()
    if have_tl:
        TL = json.loads(TL_PATH.read_text())
        F = TL["project"]["fps"]
        DURF = TL["project"]["duration_frames"]
    else:
        TL, F, DURF = None, 30, None

    # ---------- G1 口播窗−间隙窗响度结构 ----------
    if "G1" in skip:
        pass
    elif not have_tl:
        note_skip("SKIP G1（无 timeline）")
    else:
        vo = sorted((e for tr in TL["audio_tracks"] if tr["name"] == "voice"
                     for e in tr["events"]), key=lambda e: e["in"])
        if not vo:
            note_skip("SKIP G1（voice 轨无事件）")
        else:
            asrc = AW if AW.exists() else MEDIA
            speech = [(e["in"] / F, e["out"] / F) for e in vo if e["out"] - e["in"] >= 2.5]
            gap_f = []
            cur = vo[0]["in"]
            for e in vo:
                if e["in"] > cur:
                    gap_f.append((cur, e["in"]))
                cur = e["out"]
            if cur < DURF:
                gap_f.append((cur, DURF))
            gaps = [(a / F, b / F) for a, b in gap_f if (b - a) >= 3.0]
            # 批量测量（方案三阶段1）：speech/gaps 分开批（同批同类的值集合还原，
            # 见 lufs_batch 注释）；任一批失败整体回落逐窗旧路径（打印 ⚠）。
            t0 = time.monotonic()
            sp_all = lufs_batch(speech, asrc)
            gp_all = lufs_batch(gaps, asrc)
            dt = time.monotonic() - t0
            if sp_all is None or gp_all is None:
                print("⚠ G1 批量测量失败（某批 ffmpeg 失败或 Summary 数不符），回落逐窗旧路径")
                sp = [v for v in (lufs(a, b, asrc) for a, b in speech) if v is not None]
                gp = [v for v in (lufs(a, b, asrc) for a, b in gaps) if v is not None]
            else:
                sp, gp = sp_all, gp_all
                n_w = len(speech) + len(gaps)
                n_b = (len(speech) + G1_BATCH - 1) // G1_BATCH \
                    + (len(gaps) + G1_BATCH - 1) // G1_BATCH
                print(f"[timing] G1 批量测量（{n_w} 窗 {n_b} 批）: {dt:.1f}s")
            if not sp or not gp:
                note_skip(f"SKIP G1（窗不足：口播 {len(sp)} / 间隙 {len(gp)}）")
            else:
                sv = sorted(sp)
                gv = sorted(gp)
                med_s, med_g = sv[len(sv) // 2], gv[len(gv) // 2]
                spread = sv[-1] - sv[0]
                METRICS["G1"] = {"med_s_lufs": round(med_s, 2), "med_g_lufs": round(med_g, 2),
                                 "gap_db": round(med_s - med_g, 2), "spread_db": round(spread, 2),
                                 "n_speech": len(sp), "n_gaps": len(gp),
                                 "audio_src": str(asrc)}
                gate("G1 口播窗−间隙窗+均匀",
                     med_s - med_g >= args.g1_min_gap_db and spread <= args.g1_spread_db,
                     f"中位 {med_s:.1f} vs {med_g:.1f} = +{med_s-med_g:.1f} dB"
                     f"（门 +{args.g1_min_gap_db}）；口播窗极差 {spread:.1f} dB"
                     f"（门 ≤{args.g1_spread_db}）；音频源={asrc}")

    # ---------- G2/G3 响度与真峰（解码域）＋ G7 黑帧（合并单次全片解码） ----------
    # G2/G3 原用 -vn、G7 原用 -an 各解一遍全片；现合并为一条 -filter_complex
    # （音频链 ebur128 + 视频链 blackdetect，双 -map 到 null）单次解码同时提取。
    # 合并命令失败时回落旧两步路径（稳妥优先）。
    # 响度回执（方案三阶段1）：先查 loudness_cache 回执——命中则 G2/G3 直接用
    # 回执 I/TP，全片 pass 只保留 [0:v]blackdetect 链（不再挂 ebur128）；未命中
    # 照旧测 I/TP（G7 照跑），测得后归档回执供 qc/下一轮 gates 复用（同一解码
    # 域输入全链只测一次；wav 域回执不入——post_loudness 语义不同，总控裁定）。
    mI = mP = None
    blacks = None
    lc_hit = None    # 响度回执命中条目（dict）或 None
    if MEDIA.exists():

        def meas_lufs_peak():
            r = run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(MEDIA), "-vn",
                     "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"])
            return (re.search(r"I:\s+(-?[\d.]+) LUFS", r.stderr),
                    re.search(r"Peak:\s+(-?[\d.]+) dBFS", r.stderr))

        def detect_blacks():
            bd = run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(MEDIA),
                      "-vf", f"blackdetect=d={args.g7_black_d}:pix_th={args.g7_pix_th}",
                      "-an", "-f", "null", "-"])
            return re.findall(r"black_start:(\S+)\s+black_end:(\S+)", bd.stderr)

        need_audio = ("G2" not in skip or "G3" not in skip)
        if need_audio:
            lc_hit = loudness_cache.lookup(MEDIA)
        if "G7" in skip:
            if need_audio and lc_hit is None:
                mI, mP = meas_lufs_peak()
        else:
            t0 = time.monotonic()
            if lc_hit is not None:
                r = run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(MEDIA),
                         "-filter_complex",
                         f"[0:v]blackdetect=d={args.g7_black_d}:pix_th={args.g7_pix_th}[vmet]",
                         "-map", "[vmet]", "-f", "null", "-"])
                dt = time.monotonic() - t0
                blacks = re.findall(r"black_start:(\S+)\s+black_end:(\S+)", r.stderr)
                if r.returncode != 0:
                    print("⚠ 黑场单链 pass 失败（响度回执命中态），回落独立 blackdetect")
                    blacks = detect_blacks()
                else:
                    print(f"[timing] 全片解码黑场 pass（G7，响度回执命中免音频测量）: {dt:.1f}s")
            else:
                r = run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(MEDIA),
                         "-filter_complex",
                         f"[0:a]ebur128=peak=true:framelog=quiet[amet];"
                         f"[0:v]blackdetect=d={args.g7_black_d}:pix_th={args.g7_pix_th}[vmet]",
                         "-map", "[amet]", "-map", "[vmet]", "-f", "null", "-"])
                dt = time.monotonic() - t0
                mI = re.search(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
                mP = re.search(r"Peak:\s+(-?[\d.]+) dBFS", r.stderr)
                blacks = re.findall(r"black_start:(\S+)\s+black_end:(\S+)", r.stderr)
                if r.returncode != 0 or not mI:
                    print("⚠ G2/G3+G7 合并解码 pass 失败，回落两步路径")
                    mI, mP = meas_lufs_peak()
                    blacks = detect_blacks()
                else:
                    print(f"[timing] 全片解码合并 pass（G2/G3+G7）: {dt:.1f}s")
        if lc_hit is not None:
            full_i, tp = lc_hit["i_lufs"], lc_hit["tp_dbfs"]
            METRICS["G2_G3"] = {"i_lufs": full_i, "tp_dbfs": tp, "from_receipt": True}
            gate("G2 全片响度", args.g2_lo <= full_i <= args.g2_hi,
                 f"I={full_i} LUFS（门 [{args.g2_lo},{args.g2_hi}]，回执命中免测）")
            gate("G3 解码真峰", tp <= args.g3_tp,
                 f"TP={tp} dBFS（门 ≤{args.g3_tp}，解码域回执命中免测 {MEDIA}——不信 wav 域）")
        else:
            # 归档回执：本轮实测得到完整 (I, TP) 才写（run_fn 直返现值不再解码）
            if mI and mP:
                _i, _p = float(mI.group(1)), float(mP.group(1))
                loudness_cache.measure_and_store(MEDIA, lambda: (_i, _p))
            if mI:
                full_i = float(mI.group(1))
                METRICS.setdefault("G2_G3", {})["i_lufs"] = full_i
                gate("G2 全片响度", args.g2_lo <= full_i <= args.g2_hi,
                     f"I={full_i} LUFS（门 [{args.g2_lo},{args.g2_hi}]）")
            if mI and mP:
                tp = float(mP.group(1))
                METRICS.setdefault("G2_G3", {}).update(
                    {"tp_dbfs": tp, "from_receipt": False})
                gate("G3 解码真峰", tp <= args.g3_tp,
                     f"TP={tp} dBFS（门 ≤{args.g3_tp}，解码域测 {MEDIA}——不信 wav 域）")
    else:
        note_skip(f"SKIP G2/G3（{MEDIA} 不存在）")

    # ---------- G7 黑闪门（结果取自上方合并解码；blacks=None = 跳过或无成片） ----------
    if "G7" in skip:
        pass
    elif blacks is not None:
        METRICS["G7"] = {"blacks": len(blacks),
                         "black_starts": [b[0] for b in blacks]}
        gate("G7 零黑帧", len(blacks) == 0,
             f"blackdetect(d={args.g7_black_d},pix_th={args.g7_pix_th}) 命中 {len(blacks)} 处"
             + (f"：t={[b[0] for b in blacks]}" if blacks else ""))
    else:
        note_skip("SKIP G7（无成片）")

    # ---------- G10 帧数/时长/帧率 + 打包 ----------
    # 帧数计数口径（方案三阶段1，2026-09-13 校准）：默认 -count_packets 仅解封装
    # （bench_p2 实测 -count_frames 全片解码 26.6s → -count_packets 0.12s，同值
    # 16746——h264 每包恰一帧；check_4k.sh 帧数门同口径先例）；--count-frames
    # 恢复 -count_frames 真解码计数。detail 文案注明计数口径字段以便核对。
    cnt_flag = "-count_frames" if args.count_frames else "-count_packets"
    cnt_field = "nb_read_frames" if args.count_frames else "nb_read_packets"
    if "G10" in skip:
        pass
    elif have_tl and MEDIA.exists():
        pr = run(["ffprobe", "-v", "error", "-show_entries",
                  "stream=index,codec_name,profile,bit_rate,sample_rate,channels,r_frame_rate:"
                  "format=duration:stream_tags=encoder", "-of", "json", str(MEDIA)])
        stj = json.loads(pr.stdout)
        try:
            st = {s["index"]: s for s in stj["streams"]}
            fmt = stj["format"]
            v, a = st[0], st[1]
        except (KeyError, IndexError):
            # 非有效媒体（无 streams/format/轨序异常）——显式 FAIL 该门而非裸崩溃（R5 验收 #2）
            METRICS["G10"] = {"media": str(MEDIA), "probe_raw_keys": sorted(stj.keys())}
            gate("G10 帧数+封装", False,
                 f"成片非有效媒体（ffprobe 无完整 streams/format：{sorted(stj.keys())}）")
            st = fmt = v = a = None
        if st is not None:
            fc = run(["ffprobe", "-v", "error", cnt_flag, "-select_streams", "v:0",
                  "-show_entries", f"stream={cnt_field}", "-of", "csv=p=0", str(MEDIA)])
            nb_read = int(fc.stdout.strip())
            dur_expect = DURF / F
            fs_ok = moov_before_mdat(str(MEDIA))
            METRICS["G10"] = {"frames": nb_read, "duration_s": fmt["duration"],
                              "r_frame_rate": v["r_frame_rate"], "faststart": fs_ok,
                              "frames_expect": DURF, "count_mode": cnt_field,
                              "video_encoder": v.get("tags", {}).get("encoder"),
                              "audio": f"{a.get('codec_name')}-{a.get('profile')} "
                                       f"{int(a.get('bit_rate', 0))//1000}k "
                                       f"{a.get('sample_rate')}Hz {a.get('channels')}ch"}
            gate("G10 帧数+封装",
                 nb_read == DURF and abs(float(fmt["duration"]) - dur_expect) < 0.001
                 and v["r_frame_rate"] == f"{F}/1" and fs_ok,
                 f"{nb_read}f/{fmt['duration']}s/{v['r_frame_rate']}"
                 f"（门 {DURF}f/{dur_expect:.3f}s/{F}/1；计数口径 {cnt_field}）；"
                 f"[信息] enc={v.get('tags', {}).get('encoder')} / "
                 f"{a.get('codec_name')}-{a.get('profile')} "
                 f"{int(a.get('bit_rate', 0))//1000}k {a.get('sample_rate')}Hz "
                 f"{a.get('channels')}ch / faststart={fs_ok}")
    else:
        note_skip("SKIP G10（须 timeline+成片齐备）")

    # ---------- 自定义项目门 ----------
    if args.project_gates:
        pg = json.loads(Path(args.project_gates).read_text())
        for key, want in pg.items():
            if key == "frames":
                if MEDIA.exists():
                    fc = run(["ffprobe", "-v", "error", cnt_flag,
                              "-select_streams", "v:0",
                              "-show_entries", f"stream={cnt_field}",
                              "-of", "csv=p=0", str(MEDIA)])
                    got = int(fc.stdout.strip())
                    METRICS.setdefault("PG", {})[key] = got
                    gate(f"PG:{key}", got == want,
                         f"{got}（门 {want}，计数口径 {cnt_field}）")
            elif key == "first_voice_frame" and have_tl:
                vo = sorted((e for tr in TL["audio_tracks"]
                             if tr["name"] == "voice" for e in tr["events"]),
                            key=lambda e: e["in"])
                got = vo[0]["in"] if vo else None
                METRICS.setdefault("PG", {})[key] = got
                gate(f"PG:{key}", got == want, f"{got}（门 {want}）")
            elif key.endswith("_events") and have_tl:
                tname = key[:-len("_events")]
                tr = next((t for t in TL["audio_tracks"] if t["name"] == tname), None)
                got = len(tr["events"]) if tr else None
                METRICS.setdefault("PG", {})[key] = got
                gate(f"PG:{key}", got == want, f"{got}（门 {want}）")
            elif key == "duration_frames" and have_tl:
                METRICS.setdefault("PG", {})[key] = DURF
                gate(f"PG:{key}", DURF == want, f"{DURF}（门 {want}）")
            else:
                note_skip(f"SKIP PG:{key}（无法解析的字段）")

    # ---------- --out JSON 留档（2026-09-14） ----------
    if args.out:
        report = {"tool": "apps/gates/content_gates.py",
                  "timestamp": time.strftime("%F %T"),
                  "pass": ok_all,
                  "gates": GATE_ROWS, "skipped": SKIP_ROWS, "metrics": METRICS,
                  "media": {"path": str(MEDIA), **fingerprint(MEDIA)},
                  "timeline": {"path": str(TL_PATH), "frames": DURF, "fps": F,
                               **(fingerprint(TL_PATH) if have_tl else {"exists": False})},
                  "params": {"skip": sorted(skip),
                             "g1_min_gap_db": args.g1_min_gap_db,
                             "g1_spread_db": args.g1_spread_db,
                             "g2_lo": args.g2_lo, "g2_hi": args.g2_hi,
                             "g3_tp": args.g3_tp, "count_frames": args.count_frames}}
        outp = Path(args.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        print(f"[out] gates 留档 → {outp}")

    print("PASS" if ok_all else "FAIL")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
