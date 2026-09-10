#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bed_static_duck.py · 床乐全局增益 + 口播窗静态避让（通用化收编自 voice-ime/cache/p3_audio/bed_duck.py）

用途：对 timeline.json 的床乐轨（bed）做确定性音量编排——全局 dB 衰减 + 口播窗内
额外 duck。静态分段实现，规避 sidechaincompress 长流异常（voice-ime D32 记录：653s
全图使无口播段床乐反常 +19dB；新项目启用 duck 前必须做开/关 A/B 响度对照）。

机制（与源项目逐行等价）：
  · GLOBAL = 10^(global_db/20)，全部 bed 事件音量 ×此值；
  · DUCK   = 10^(duck_db/20)，口播窗内 bed 段再 ×此值；
  · 口播窗 = voice 事件合并（相邻 gap ≤ merge_gap 帧归并）；
  · bed 事件在口播窗边界切段，src_in 保持连续（音乐不断跳）；
  · 原事件边缘淡入出只保留在首/末段；避让段加微淡入出（attack/release 帧，
    同轨禁止重叠故只用微淡入出模拟 duck 手感）。
  · 幂等：timeline _meta.bed_static_duck 标记（参数相同则跳过，不同则报错退出）。

用法：
  python3 bed_static_duck.py [-t timeline.json] [--track bed] [--voice-track voice]
      [--global-db -8] [--duck-db -4] [--merge-gap 20]
      [--attack 5] [--release 8] [--no-backup]

来源项目：voice-ime（决策 D32/D34/D35：床乐避让走静态分段而非 sidechaincompress）。
"""
import argparse
import json
import shutil
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description="床乐全局增益+口播窗静态避让（确定性，替代 sidechaincompress）")
    ap.add_argument("-t", "--timeline", default="./timeline.json")
    ap.add_argument("--track", default="bed", help="床乐轨名（默认 bed）")
    ap.add_argument("--voice-track", default="voice", help="口播轨名（默认 voice）")
    ap.add_argument("--global-db", type=float, default=-8.0, help="床乐全局增益 dB（默认 -8）")
    ap.add_argument("--duck-db", type=float, default=-4.0, help="口播窗内额外避让 dB（默认 -4）")
    ap.add_argument("--merge-gap", type=int, default=20, help="口播窗合并阈值帧（默认 20）")
    ap.add_argument("--attack", type=int, default=5, help="避让段微淡入帧（默认 5）")
    ap.add_argument("--release", type=int, default=8, help="避让段微淡出帧（默认 8）")
    ap.add_argument("--backup", dest="backup", action="store_true", default=True,
                    help="写 timeline.pre-duck.json 备份（默认开）")
    ap.add_argument("--no-backup", dest="backup", action="store_false")
    args = ap.parse_args()

    TL = Path(args.timeline).resolve()
    GLOBAL = 10 ** (args.global_db / 20)
    DUCK = 10 ** (args.duck_db / 20)
    ATTACK_F, RELEASE_F = args.attack, args.release

    params = {
        "track": args.track, "voice_track": args.voice_track,
        "global_db": args.global_db, "duck_db": args.duck_db,
        "merge_gap": args.merge_gap, "attack": args.attack, "release": args.release,
    }

    tl = json.loads(TL.read_text())
    tracks = {tr["name"]: tr for tr in tl["audio_tracks"]}
    for name in (args.track, args.voice_track):
        if name not in tracks:
            raise SystemExit(f"FATAL: 音轨 {name} 不存在（现有：{list(tracks)}）")

    # 幂等：_meta.bed_static_duck 标记
    marker = tl.get("_meta", {}).get("bed_static_duck")
    if marker:
        if marker.get("params") == params:
            print("✓ 已应用同参数（_meta.bed_static_duck），跳过（幂等）")
            return
        raise SystemExit("FATAL: _meta.bed_static_duck 参数不同，先从备份恢复或手动清理标记")

    bed = list(tracks[args.track]["events"])

    # 口播窗（合并 gap<=merge_gap）
    vo = sorted(tracks[args.voice_track]["events"], key=lambda e: e["in"])
    wins = []
    for e in vo:
        if wins and e["in"] - wins[-1][1] <= args.merge_gap:
            wins[-1][1] = max(wins[-1][1], e["out"])
        else:
            wins.append([e["in"], e["out"]])
    print(f"口播窗 {len(wins)} 个：{[tuple(w) for w in wins][:5]} ...")

    def inside_voice(a, b):
        """段 [a,b) 是否整体落在某口播窗内（切分后必然二择一）"""
        return any(w[0] <= a and b <= w[1] for w in wins)

    if args.backup:
        bak = TL.parent / "timeline.pre-duck.json"
        if not bak.exists():
            shutil.copy2(TL, bak)
            print(f"备份 → {bak.name}")

    new_events = []
    for ev in sorted(bed, key=lambda e: e["in"]):
        T, S = ev["in"], ev.get("src_in", 0)
        E = ev["out"]
        bounds = sorted({T, E} | {w for w0, w1 in wins for w in (w0, w1) if T < w < E})
        for f1, f2 in zip(bounds, bounds[1:]):
            seg = dict(ev)
            seg["in"], seg["out"] = f1, f2
            seg["src_in"] = S + (f1 - T)
            seg["volume"] = round(ev["volume"] * GLOBAL * (DUCK if inside_voice(f1, f2) else 1.0), 6)
            # 原事件边缘淡入出只保留在其首/末段；避让段加微淡入出
            if f1 != T: seg.pop("fade_in_frames", None)
            if f2 != E: seg.pop("fade_out_frames", None)
            if inside_voice(f1, f2):
                seg["fade_in_frames"] = max(seg.get("fade_in_frames", 0) or 0, ATTACK_F)
                seg["fade_out_frames"] = max(seg.get("fade_out_frames", 0) or 0, RELEASE_F)
            new_events.append(seg)

    tracks[args.track]["events"] = new_events
    tl["_meta"] = tl.get("_meta", {})
    tl["_meta"]["bed_static_duck"] = {
        "note": (f"bed 全局 {args.global_db}dB + 口播窗静态避让 {args.duck_db}dB"
                 f"（{len(wins)} 窗分段，src 连续；确定性替代 sidechaincompress）"),
        "script": "video_tools/scripts/bed_static_duck.py",
        "params": params,
    }
    TL.write_text(json.dumps(tl, ensure_ascii=False, indent=1))

    n_duck = sum(1 for e in new_events if inside_voice(e["in"], e["out"]))
    print(f"✓ bed {len(bed)} 事件 → {len(new_events)} 段（其中口播避让段 {n_duck}）")
    print(f"  全局 ×{round(GLOBAL, 6)}（{args.global_db}dB）；避让段再 ×{round(DUCK, 6)}（{args.duck_db}dB）")


if __name__ == "__main__":
    main()
