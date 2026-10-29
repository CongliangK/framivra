#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bed_static_duck.py · 床乐全局增益 + 口播窗静态避让（通用化收编自 voice-ime/cache/p3_audio/bed_duck.py；
P2b 自 scripts/ 迁入 plugins/duck-static/，命令面与 _meta 标记语义不变）

用途：对 timeline.json 的床乐轨（bed）做确定性音量编排——全局 dB 衰减 + 口播窗内
额外 duck。静态分段实现，规避 sidechaincompress 长流异常（voice-ime D32 记录：653s
全图使无口播段床乐反常 +19dB；新项目启用 duck 前必须做开/关 A/B 响度对照）。

机制（与源项目逐行等价；纯函数 voice_windows/transform_events 同时是
plugins/duck-static 插件 audio_events 钩子的变换核心——同一实现，脚本烘焙与
管线内存变换永不分叉）：
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

管线内等价物（推荐新项目用）：音轨级键
  "duck_strategy": {"mode": "static", "global_db": -8, ...}（见 plugins/duck-static/README.md）
——纯内存变换不写 timeline、无 _meta 标记；本脚本产物（已烘焙 timeline）与插件键
互斥（validate 检测 _meta.bed_static_duck 即报错，防双施）。

来源项目：voice-ime（决策 D32/D34/D35：床乐避让走静态分段而非 sidechaincompress）。
"""
import argparse
import json
import copy
import sys
import uuid
from pathlib import Path

# ---- 纯函数核心（插件与 CLI 共用的单一实现；帧域数学，零 vtx 依赖）----

DEFAULTS = {"mode": "static", "global_db": -8.0, "duck_db": -4.0,
            "voice_track": "voice", "merge_gap": 20, "attack": 5, "release": 8}


def voice_windows(voice_events, merge_gap: int = 20) -> list:
    """口播窗 = voice 事件按 in 排序后合并（相邻 gap ≤ merge_gap 帧归并）。

    返回 [[in, out], ...]（帧区间，闭开语义与事件一致）。"""
    wins = []
    for e in sorted(voice_events, key=lambda e: e["in"]):
        if wins and e["in"] - wins[-1][1] <= merge_gap:
            wins[-1][1] = max(wins[-1][1], e["out"])
        else:
            wins.append([e["in"], e["out"]])
    return wins


def transform_events(events, wins, global_db: float, duck_db: float,
                     attack: int, release: int) -> list:
    """床乐事件 → 口播窗边界切段 + 音量编排（bed_static_duck v1 数学逐行等价）。

    - 全段 volume × 10^(global_db/20)；段整体落在某口播窗内再 × 10^(duck_db/20)
      （切分后必然二择一：段与窗边界已对齐，不存在半窗段）；
    - src_in 连续（音乐不断跳）；原事件边缘淡入出只保留在首/末段；
    - 避让段加微淡入出（attack/release 帧，取与既有值的较大者）。
    纯函数：不改性入参（返回全新事件列表）。"""
    GLOBAL = 10 ** (global_db / 20)
    DUCK = 10 ** (duck_db / 20)

    def inside_voice(a, b):
        """段 [a,b) 是否整体落在某口播窗内（切分后必然二择一）"""
        return any(w[0] <= a and b <= w[1] for w in wins)

    new_events = []
    for ev in sorted(events, key=lambda e: e["in"]):
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
                seg["fade_in_frames"] = max(seg.get("fade_in_frames", 0) or 0, attack)
                seg["fade_out_frames"] = max(seg.get("fade_out_frames", 0) or 0, release)
            # 钳制 fade ≤ 段长（2026-10-05 D-A：切段可短于原 fade——如尾段 42f < 原
            # fade_out 45f → 混音链 afade st<0 报错；微淡入出同理可能超短段）
            dur = f2 - f1
            if seg.get("fade_in_frames"): seg["fade_in_frames"] = min(seg["fade_in_frames"], dur)
            if seg.get("fade_out_frames"): seg["fade_out_frames"] = min(seg["fade_out_frames"], dur)
            new_events.append(seg)
    return new_events


def _api():
    try:
        from vtx import api
    except ModuleNotFoundError as exc:
        if exc.name != "vtx":
            raise
        sys.path.append(str(Path(__file__).resolve().parents[2]))
        from vtx import api
    return api


def prepare_bake(snapshot, track_name, wins, params, marker, op_id):
    """Pure adapter. Preserve first split ID; freeze all other IDs in request.

    Only bed volume/fades/segmentation and the named root marker are owned;
    source, loop, pan, track delay, unrelated metadata and extensions survive.
    """
    tl = copy.deepcopy(snapshot["document"])
    track = next(t for t in tl["audio_tracks"] if t["name"] == track_name)
    old = track["events"]
    managed = snapshot["identity_mode"] == "managed"
    events, operations, new_ids = [], [], []
    for ev in sorted(old, key=lambda e: e["in"]):
        segments = transform_events([ev], wins, params["global_db"], params["duck_db"],
                                    params["attack"], params["release"])
        current = ev.get("id")
        for index, seg in enumerate(segments):
            if managed:
                if index:
                    identity = "duck-" + uuid.uuid5(uuid.NAMESPACE_URL, op_id + ":" + ev["id"] + ":" + str(index)).hex
                    new_ids.append(identity)
                    operations.append(dict(op="block.split", id=current, at=seg["in"],
                                           new_id=identity, fade_policy="reapply-edge-fades"))
                    current = identity
                seg["id"] = current
            events.append(seg)
        if managed:
            for seg in segments:
                # Exact old transform materializes src_in=0 even without cuts.
                if len(segments) == 1 and "src_in" not in ev:
                    operations.append(dict(op="block.trim", id=seg["id"], start=seg["in"],
                                           end=seg["out"], fade_policy="reapply-edge-fades"))
                for key in ("volume", "fade_in_frames", "fade_out_frames"):
                    if key in seg and (key not in ev or json.dumps(seg[key]) != json.dumps(ev[key])):
                        operations.append(dict(op="parameters.update", id=seg["id"],
                                               path=[key], value=seg[key]))
                    elif key not in seg and key in ev:
                        operations.append(dict(op="parameters.unset", id=seg["id"], path=[key]))
    request = dict(protocol=2, operation="reconcile" if managed else "replace",
                   op_id=op_id, base_revision=snapshot["revision"],
                   identity_mode=snapshot["identity_mode"], payload_mode="document",
                   purpose="edit" if managed else "legacy-write")
    if managed:
        if old != sorted(old, key=lambda e: e["in"]):
            for seg in events:
                operations.append(dict(op="block.move", id=seg["id"], track_id=track["id"], at=seg["in"]))
        request.update(owner="duck-static.bake", owned_ids=[ev["id"] for ev in old],
                       new_ids=new_ids, operations=operations,
                       owned_meta_keys=["bed_static_duck"], meta_updates={"bed_static_duck": marker})
    else:
        track["events"] = events
        tl.setdefault("_meta", {})["bed_static_duck"] = marker
        request["document"] = tl
    return request, events


def publish_bake(path, snapshot, request, backup=True, dry_run=False):
    """One main publish, preceded by an independent create-only exact backup.

    Not a two-target atomic transaction. A successful backup can survive a later
    stale CAS/failure. It is a detached artifact, never the authority for retry.
    """
    api = _api()
    plan = api.timeline_write_plan(path, request)
    if not plan["ok"]:
        boundary = ("既有operation结果待查/恢复，不能假定timeline未改，勿盲重交新op"
                    if plan.get("code") in ("outcome_unknown", "recovery_required") else
                    "本次timeline未改，未创建备份")
        raise RuntimeError("%s: %s; op_id=%s; %s" %
                           (plan.get("code"), plan.get("message"), request["op_id"], boundary))
    if dry_run:
        return plan
    bak = Path(path).parent / "timeline.pre-duck.json"
    backup_created = False
    if backup and not plan.get("replayed"):
        br = dict(protocol=2, operation="create",
                  op_id="duck-backup-" + uuid.uuid5(uuid.NAMESPACE_URL, request["op_id"]).hex,
                  base_revision=None, identity_mode=snapshot["identity_mode"],
                  payload_mode="exact-import", raw_bytes=snapshot["raw_bytes"],
                  purpose="detached-copy" if snapshot["identity_mode"] == "managed" else "frozen-copy")
        bp = api.timeline_write_plan(bak, br)
        if bak.exists():
            if not bp["ok"] and bp.get("code") == "recovery_required":
                raise RuntimeError("backup recovery_required; main timeline未提交；查/恢复backup operation=%s，勿盲重交新op" % br["op_id"])
            # Existing artifacts are never overwritten or used as a main base.
        else:
            result = api.timeline_transaction(bak, br) if bp["ok"] else bp
            if not result["ok"]:
                raise RuntimeError("backup %s: %s; main timeline未提交；backup operation=%s（可查/恢复同operation）" %
                                   (result.get("code"), result.get("message"), br["op_id"]))
            backup_created = True
            print(f"备份 → {bak.name}")
    result = api.timeline_transaction(path, request)
    if not result["ok"]:
        unknown = result.get("code") in ("outcome_unknown", "recovery_required")
        boundary = "结果未知，可能已提交；查/恢复同operation，勿盲重交新op" if unknown else "本次timeline未改；不自动rebase或重试"
        raise RuntimeError("%s: %s; op_id=%s; %s; backup_created=%s backup=%s" %
                           (result.get("code"), result.get("message"), request["op_id"], boundary,
                            backup_created, bak if backup else None))
    return result


# ---- CLI（命令面与 _meta 标记语义不变）----

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
    ap.add_argument("--dry-run", action="store_true", help="仅公开 publisher 纯 plan，零写")
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

    snapshot = _api().read_timeline_for_write(TL)
    if snapshot["document"] is None:
        raise SystemExit("FATAL: timeline不存在")
    tl = snapshot["document"]
    op_id = "duck-" + uuid.uuid4().hex
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

    # 口播窗（合并 gap<=merge_gap）——与插件 audio_events 同一实现
    wins = voice_windows(tracks[args.voice_track]["events"], args.merge_gap)
    print(f"口播窗 {len(wins)} 个：{[tuple(w) for w in wins][:5]} ...")

    def inside_voice(a, b):
        """段 [a,b) 是否整体落在某口播窗内（切分后必然二择一）"""
        return any(w[0] <= a and b <= w[1] for w in wins)

    # 分段变换——与插件 audio_events 同一实现；烘焙前先检查原始事件契约。
    new_events = transform_events(bed, wins, args.global_db, args.duck_db,
                                  ATTACK_F, RELEASE_F)
    for i, event in enumerate(new_events):
        fi, fo = event.get("fade_in_frames", 0), event.get("fade_out_frames", 0)
        dur = event["out"] - event["in"]
        if fi + fo > dur:
            raise SystemExit(
                f"FATAL: track={args.track!r} events[{i}] "
                f"[{event['in']}, {event['out']}) fade_in_frames={fi}, "
                f"fade_out_frames={fo}, duration_frames={dur}: 淡入出总长超过事件时长。"
                "请改用内存 duck_strategy，或缩短 --attack/--release（必要时调整原事件淡入出）；"
                "timeline 和备份均未写入。")

    marker = {
        "note": (f"bed 全局 {args.global_db}dB + 口播窗静态避让 {args.duck_db}dB"
                 f"（{len(wins)} 窗分段，src 连续；确定性替代 sidechaincompress）"),
        "script": "video_tools/plugins/duck-static/bed_static_duck.py",
        "params": params,
    }
    request, _ = prepare_bake(snapshot, args.track, wins, params, marker, op_id)
    publish_bake(TL, snapshot, request, backup=args.backup, dry_run=args.dry_run)
    if args.dry_run:
        print("✓ dry-run：纯计划，timeline、备份与history均未写入")
        return

    n_duck = sum(1 for e in new_events if inside_voice(e["in"], e["out"]))
    print(f"✓ bed {len(bed)} 事件 → {len(new_events)} 段（其中口播避让段 {n_duck}）")
    print(f"  全局 ×{round(GLOBAL, 6)}（{args.global_db}dB）；避让段再 ×{round(DUCK, 6)}（{args.duck_db}dB）")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError) as exc:
        raise SystemExit("FATAL: " + str(exc))
