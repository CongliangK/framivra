#!/usr/bin/env python3
"""audit_bundle.py — BGM 正式版分轨审计包（人机协作交付门执行器，2026-09-10 用户裁定默认必做）

背景：用户必须在 Blender 里逐轨屏蔽/独听对比，才能参与到混音平衡与编排的创作裁决中
（voice-ime BGM v2 首例）。因此每一轮**正式版本** BGM 挂载进时间线后，除 vtx 渲染外
必须产出分轨审计包；草稿/内部迭代轮不要求。

一键五步：
  ①按轨道名前缀选中音轨，源 stem 逐条 ffmpeg→320k MP3（快速试听件；生产 flac 是正本）
  ②生成审计 timeline：逐轨一列、生产音量/事件窗原样、源指向 MP3、无 voice 轨=无 duck
    ——全部 unmute 相加≈成片床乐状态，屏蔽任一轨即 A/B 其真实贡献
  ③vtx validate 硬门
  ④json2blend → <audit-dir>/audit.blend（Blender 预览工程）
  ⑤verify_blend 结构校验

用法：
  python3 audit_bundle.py -t <项目>/timeline.json --prefix bgm2-
      [--prefix 可多次] [--mp3-dir <dir>] [--audit-dir <dir>] [--force] [--skip-blend]
      [--whole] [--silence-threshold -50dB] [--min-silence 1.0] [--pad 0.25]
  缺省 mp3-dir/audit-dir = <项目>/cache/audit_bgm/{mp3,.}；mp3 已存在且比源新则跳过转码
  （分轨重渲后自动失效重转），--force 强制全量重转。

静音切分（2026-09-10 用户裁定，默认开启；--whole 关闭）：分轨不是全程有声音，审计
timeline 按每轨实际发声区间切段（ffmpeg silencedetect 反相 + pad 余量 + 帧对齐），
事件仍锚定视频绝对时间——Blender 里每轨只在其发声段出现条带，静音区留白，观演/刮擦
不用再拖过无声段。源 MP3 仍只有每轨一条（事件 src_in 指回原位），零数据重复。

给用户的 Blender 用法（附在交付信息里）：
  blender <项目>/cache/audit_bgm/audit.blend → Sequencer 工作区 → 空格播放；
  通道头小喇叭=整轨静音；Ctrl+I 反选+H=独听该轨；选中条 Y 切开后可屏蔽某段时间区。
  注意：独听比成片"干"是预期（厅堂 wet 只在参考混）；这里无口播 duck，比成片略"满"。
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def run(cmd, cwd=None, tail=3):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    out = "\n".join(r.stdout.strip().splitlines()[-tail:]) if r.stdout.strip() else ""
    print(f"[exit={r.returncode}] {' '.join(map(str, cmd[:4]))}…")
    if out:
        print(out)
    if r.returncode != 0:
        print(r.stderr[-1500:], file=sys.stderr)
        sys.exit(1)
    return r


def content_spans(src: Path, dur_s: float, fps: int, thr: str, min_sil: float, pad: float):
    """silencedetect 反相求发声区间：[silence_start, silence_end) 的补集，加 pad 余量，
    合并后按帧对齐。返回 [(in_f, out_f), ...]。"""
    r = subprocess.run(
        ["ffmpeg", "-nostats", "-i", str(src),
         "-af", f"silencedetect=noise={thr}:d={min_sil}", "-f", "null", "-"],
        capture_output=True, text=True)
    sils = []
    for line in r.stderr.splitlines():
        if "silence_start:" in line:
            sils.append([float(line.split("silence_start:")[1].split()[0]), None])
        elif "silence_end:" in line and sils:
            sils[-1][1] = float(line.split("silence_end:")[1].split()[0])
    if sils and sils[-1][1] is None:          # 片尾静默无 end
        sils[-1][1] = dur_s
    # 反相：相邻静默之间即发声段
    spans = []
    if not sils:
        spans = [(0.0, dur_s)]
    else:
        edges = [0.0]
        for s in sils:
            edges += [s[0], s[1]]
        edges.append(dur_s)
        for a, b in zip(edges[0::2], edges[1::2]):
            if b - a > 1e-6:
                spans.append((a, b))
    # pad + 合并 + 帧对齐
    out = []
    for a, b in spans:
        a, b = max(0.0, a - pad), min(dur_s, b + pad)
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return [(round(a * fps), round(b * fps)) for a, b in out]


def main():
    ap = argparse.ArgumentParser(description="BGM 分轨审计包生成器")
    ap.add_argument("-t", "--timeline", required=True, type=Path, help="项目生产 timeline.json")
    ap.add_argument("-p", "--prefix", action="append", default=None,
                    help="音轨名前缀，可多次（默认 bgm-；注意是 timeline 轨道名非源 ID）")
    ap.add_argument("--mp3-dir", type=Path, default=None)
    ap.add_argument("--audit-dir", type=Path, default=None)
    ap.add_argument("--force", action="store_true", help="强制重转全部 MP3")
    ap.add_argument("--skip-blend", action="store_true", help="只做 MP3+审计 timeline，不跑 Blender")
    ap.add_argument("--whole", action="store_true", help="关闭静音切分（每轨一条全程事件）")
    ap.add_argument("--silence-threshold", default="-50dB", help="silencedetect 噪声门（默认 -50dB）")
    ap.add_argument("--min-silence", type=float, default=1.0, help="最短静默段秒数（默认 1.0）")
    ap.add_argument("--pad", type=float, default=0.25, help="每段两端余量秒数（默认 0.25）")
    a = ap.parse_args()

    tl_path = a.timeline.resolve()
    root = tl_path.parent
    tl = json.loads(tl_path.read_text())
    audit_dir = (a.audit_dir or root / "cache" / "audit_bgm").resolve()
    mp3_dir = (a.mp3_dir or audit_dir / "mp3").resolve()
    mp3_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)

    prefixes = a.prefix or ["bgm-"]
    sel = [tr for tr in tl["audio_tracks"] if tr["name"].startswith(tuple(prefixes))]
    if not sel:
        sys.exit(f"无音轨匹配前缀 {prefixes}（现有轨：{[t['name'] for t in tl['audio_tracks']]}）")

    # ① MP3 导出（同轨多事件取第一个事件的 src；loop 组共享同一源）
    src_of = {s["id"]: root / s["path"] for s in tl["sources"]}
    mp3_of = {}
    for tr in sel:
        src = src_of[tr["events"][0]["src"]]
        dst = mp3_dir / f"{tr['name']}.mp3"
        mp3_of[tr["name"]] = dst
        if (dst.exists() and not a.force
                and dst.stat().st_mtime > src.stat().st_mtime):
            print(f"[skip] {dst.name}（比源新）")
            continue
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src),
                        "-map", "0:a", "-c:a", "libmp3lame", "-b:a", "320k", str(dst)],
                       check=True)
        print(f"[mp3 ] {tr['name']} ← {src.name}")

    # ② 审计 timeline（音量取生产值；事件窗=静音切分后的发声段，锚定视频绝对时间）
    fps = tl["project"]["fps"]
    sources, tracks, report = [], [], []
    for tr in sel:
        sid = tr["name"]
        sources.append({"id": sid,
                        "path": os.path.relpath(mp3_of[sid], audit_dir)})
        vol = tr["events"][0].get("volume", 1.0)
        if a.whole:
            evs = [{"src": sid, "src_in": e.get("src_in", 0),
                    "in": e["in"], "out": e["out"], "volume": vol}
                   for e in tr["events"]]
            report.append((sid, vol, 1, None))
        else:
            r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                "format=duration", "-of", "csv=p=0", str(mp3_of[sid])],
                               capture_output=True, text=True, check=True)
            dur = float(r.stdout.strip())
            spans = content_spans(mp3_of[sid], dur, fps,
                                  a.silence_threshold, a.min_silence, a.pad)
            # 发声段 × 生产事件窗求交：逐段继承所在事件的 volume（分段音量轨的审计
            # 正确性；2026-09-10 修——旧版取首事件 volume 施加全轨，分段音量会听错）
            evs = []
            for i, o in spans:
                cur = i
                for e in sorted(tr["events"], key=lambda x: x["in"]):
                    s, t = max(cur, e["in"]), min(o, e["out"])
                    if s < t:
                        evs.append({"src": sid, "src_in": s, "in": s, "out": t,
                                    "volume": e.get("volume", 1.0)})
                        cur = t
                    if cur >= o:
                        break
            vols = sorted({e["volume"] for e in evs})
            voiced = sum(e["out"] - e["in"] for e in evs) / fps
            report.append((sid, vols, len(evs), voiced))
        tracks.append({"name": sid, "events": evs})

    audit = {
        "version": 2,
        "_meta": {
            "note": "BGM 分轨审计时间线（audit_bundle.py 生成）：逐轨一列、音量=生产值、"
                    "事件按各轨实际发声区间切段（silencedetect 反相+pad，锚定视频绝对时间），"
                    "静音区在 Blender 里留白不出现条带；源=320k MP3 导出件（每轨一条，"
                    "事件 src_in 指回原位）。全部 unmute 相加≈成片床乐状态（无 voice 轨="
                    "无 duck，比成片略满）；屏蔽任一轨即 A/B 其贡献。独听偏干属预期（厅堂 "
                    "wet 仅参考混）。",
            "generated_by": "audit_bundle.py",
            "silence": f"threshold={a.silence_threshold} min_silence={a.min_silence}s pad={a.pad}s"
                       if not a.whole else "whole（未切分）",
            "from_timeline": str(tl_path),
        },
        "project": dict(tl["project"]),
        "sources": sources,
        "video_tracks": [],
        "subtitles": [],
        "audio_tracks": tracks,
    }
    at_path = audit_dir / "timeline.json"
    at_path.write_text(json.dumps(audit, ensure_ascii=False, indent=1))
    n_ev = sum(len(t["events"]) for t in tracks)
    print(f"[json ] {at_path}（{len(tracks)} 轨 / {n_ev} 事件）")
    for sid, vol, n, voiced in report:
        tail = f" 发声 {voiced:.1f}s" if voiced is not None else ""
        vs = "/".join(str(v) for v in vol) if isinstance(vol, list) else str(vol)
        print(f"       {sid:16s} vol={vs:<9} {n} 段{tail}")

    # ③ validate
    vtx = Path.home() / ".agents/skills/video/vtx/vtx.py"
    run([sys.executable, str(vtx), "-t", str(at_path), "validate"], tail=2)
    if a.skip_blend:
        print("[done] --skip-blend：审计 blend 未生成")
        return

    # ④⑤ json2blend + verify_blend（cwd=bridge：两脚本按自身位置工作）
    bridge = Path(__file__).resolve().parent
    blend = audit_dir / "audit.blend"
    run(["blender", "--background", "--factory-startup",
         "--python", str(bridge / "json2blend.py"), "--", str(at_path), str(blend)],
        cwd=bridge, tail=3)
    run(["blender", "--background",
         "--python", str(bridge / "verify_blend.py"), "--", str(at_path), str(blend)],
        cwd=bridge, tail=1)
    print(f"[done] 打开：blender {blend}")


if __name__ == "__main__":
    main()
