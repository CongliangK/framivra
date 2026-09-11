#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""indextts_worker.py · IndexTTS-2.5 引擎重活件（口播占位语音，克隆本人音色）

**运行解释器**：本文件不由系统 python3 跑，而由配置解释器执行——
  config.tts.python ?: ~/miniconda3/envs/indextts/bin/python（3.10.19）
                        ?: ~/AI/IndexTTS/repo/.venv/bin/python（3.11.13）
（官方支持区间 3.10-3.11；该解释器内可 import indextts / torch）。
通常经 `tts_cli.py gen --engine indextts` 以 subprocess 拉起，也可手动：
  ~/miniconda3/envs/indextts/bin/python indextts_worker.py --spec spec.json --project <项目> --runid <id>

来源：voice-ime/assets/voice_tts/ gen.py + run_v2.py 内化（参数化重写）。
模型单次加载、多窗串行（4080 RTF≈0.2，27 窗量级 2-4 分钟，voice-ime 实测）。
逐窗流程（run_v2.py 修正版语义）：
  1) infer(音色参考, text, raw, "mixed", duration_factor=1.0) 自然语速出 raw
     （22.05kHz 中间物 → cache/tmp/<runid>/raw_<ID>.wav）
  2) 偏差 >7% 时 duration_factor = clamp(budget/raw, 0.5, 2.0) 重生成一次。
     方向（勿与 atempo 混淆，run_v2.py 修正注记照录）：duration_factor 是
     IndexTTS2 的模型参数、语义=时长倍率（A7 实证：自然 5.5s 传 1.21 → 6.8s），
     故过长的句应传 budget/实测（<1）让它说短；而 atempo 贴预算参数=raw/budget。
  3) atempo.apply_atempo 贴预算 → assets/voice_tts/final/<ID>.wav（48k/mono/s16）
  4) levelnorm 峰值归一 -6dBFS（修克隆低电平坑：raw 批次 mean -44~-51dB）
  5) manifest 记账 → cache/reports/tts_manifest_<runid>.json
OOM（torch.cuda.OutOfMemoryError / CUDA error）→ 中文原因打印 + 退出码 3
（tts_cli 据此提示 --engine edge 降级；IME sidecar 同卡共驻时 OOM 属预期路径）。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import atempo  # noqa: E402  （纯 ffmpeg/stdlib，任意解释器可跑）
import levelnorm  # noqa: E402

DEFAULT_REPO = os.path.expanduser("~/AI/IndexTTS/repo")
REGEN_DEV = 0.07        # 偏差阈值：|budget/raw - 1| > 7% 重生成（run_v2 同款）
FACTOR_LO, FACTOR_HI = 0.5, 2.0
EXIT_OOM = 3


def n_chars(t: str) -> int:
    import re
    return len(re.sub(r"\s", "", t or ""))


def _flush_manifest(manifest, rpt_dir, runid):
    """manifest 增量落盘（P2-6）：每窗成功即写，崩溃/中断后已完成窗口记账仍在盘。"""
    os.makedirs(rpt_dir, exist_ok=True)
    out = os.path.join(rpt_dir, f"tts_manifest_{runid}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)


def manifest_row(wid, text, budget_s, raw_s, factor, final_s, peak_db):
    """manifest 记账行（字段对齐 voice-ime manifest.json + peak_db）。"""
    return {"id": wid, "chars": n_chars(text), "budget_s": round(budget_s, 3),
            "raw_s": round(raw_s, 2), "duration_factor": factor,
            "final_s": round(final_s, 2), "peak_db": round(peak_db, 1)}


def print_followups(project_dir: str):
    """repack_voice.py 同款后续命令序列（gen 完打印）。"""
    tl = os.path.join(project_dir, "timeline.json")
    print(f"\n后续命令（缺一不可，仓库根执行；final 已就位供 vtx/repack 消费）：")
    print(f"  rm {project_dir}/cache/audio/vt_*.wav")
    print(f"  python3 vtx/vtx.py -t {tl} prepare")
    print(f"  python3 vtx/vtx.py -t {tl} audio   # voice 变必重跑")
    print(f"  bash scripts/post_loudness.sh {project_dir}     # audio 后 mux 前必跑")
    print(f"  python3 vtx/vtx.py -t {tl} mux")
    print(f"  python3 vtx/vtx.py -t {tl} qc && python3 vtx/vtx.py -t {tl} audit")
    print(f"（实录终版替换：python3 scripts/repack_voice.py -t {tl}）")


def main():
    ap = argparse.ArgumentParser(description="IndexTTS-2.5 口播占位生成（配置解释器执行）")
    ap.add_argument("--spec", required=True, help="spec.json 路径")
    ap.add_argument("--project", required=True, help="项目目录")
    ap.add_argument("--runid", required=True)
    ap.add_argument("--repo", default=DEFAULT_REPO, help="IndexTTS 仓（含 checkpoints/）")
    ap.add_argument("--voice-ref", required=True, help="音色参考 wav（5-10s 干声）")
    ap.add_argument("--ids", default=None, help="只生成指定窗口（逗号分隔）")
    ap.add_argument("--only-missing", action="store_true", help="final 已存在的窗跳过")
    a = ap.parse_args()

    ckpt = os.path.join(a.repo, "checkpoints")
    cfg_yaml = os.path.join(ckpt, "config.yaml")
    for p in (cfg_yaml, a.voice_ref):
        if not os.path.exists(p):
            print(f"✗ 缺文件：{p}（跑 bash tts/init.sh 核对资产）")
            sys.exit(2)

    with open(a.spec, encoding="utf-8") as f:
        spec = json.load(f)
    windows = spec["windows"]
    ids = set(a.ids.split(",")) if a.ids else None
    if ids:
        windows = [w for w in windows if w["id"] in ids]

    final_dir = os.path.join(a.project, "assets", "voice_tts", "final")
    tmp_dir = os.path.join(a.project, "cache", "tmp", a.runid)
    rpt_dir = os.path.join(a.project, "cache", "reports")
    for d in (final_dir, tmp_dir, rpt_dir):
        os.makedirs(d, exist_ok=True)

    sys.path.insert(0, a.repo)
    try:
        from indextts.infer_v2_5 import IndexTTS2
    except ImportError as e:
        print(f"✗ 解释器无法 import indextts（{e}）——请用 conda indextts env "
              f"或 repo/.venv 解释器（config.tts.python），跑 bash tts/init.sh 核对")
        sys.exit(2)

    print("加载 IndexTTS-2.5（bf16/cuda:0，单次加载多窗串行）…", flush=True)
    try:
        model = IndexTTS2(cfg_path=cfg_yaml, model_dir=ckpt,
                          use_bf16=True, device="cuda:0")
    except Exception as e:  # OOM / 驱动 / 权重问题统一走降级提示
        msg = str(e)
        print(f"✗ 模型加载失败：{msg[:300]}")
        if "out of memory" in msg.lower() or "OOM" in msg:
            print("  显存不足（同卡或有大模型 sidecar 共驻）→ 降级：python3 tts/tts_cli.py gen <spec> --engine edge")
            sys.exit(EXIT_OOM)
        sys.exit(2)

    manifest = []
    for w in windows:
        wid, text, budget = w["id"], w.get("text", ""), w["budget_s"]
        final = os.path.join(final_dir, f"{wid}.wav")
        if a.only_missing and os.path.exists(final):
            print(f"✓ {wid}: final 已存在，跳过（--only-missing）")
            continue
        if not (text or "").strip():
            print(f"✗ {wid}: text 为空，跳过（spec 的 text 需人工回填后再 gen）")
            continue
        raw = os.path.join(tmp_dir, f"raw_{wid}.wav")
        print(f"[{wid}] {n_chars(text)}字 预算 {budget:.2f}s 合成中…", flush=True)
        try:
            model.infer(a.voice_ref, text, raw, "mixed",
                        duration_factor=1.0, verbose=False)
        except Exception as e:
            msg = str(e)
            print(f"✗ [{wid}] 推理失败：{msg[:300]}")
            if "out of memory" in msg.lower():
                print("  显存不足 → 降级：python3 tts/tts_cli.py gen <spec> --engine edge")
                sys.exit(EXIT_OOM)
            sys.exit(2)
        d1 = atempo.probe_duration(raw)
        factor = None
        if abs(budget / d1 - 1) > REGEN_DEV:  # 重生成一次：目标=按预算相对自然的时长倍率
            factor = min(FACTOR_HI, max(FACTOR_LO, budget / d1))
            try:
                model.infer(a.voice_ref, text, raw, "mixed",
                            duration_factor=factor, verbose=False)
            except Exception as e:
                msg = str(e)
                print(f"✗ [{wid}] 重生成失败：{msg[:300]}")
                if "out of memory" in msg.lower():
                    print("  显存不足 → 降级：python3 tts/tts_cli.py gen <spec> --engine edge")
                    sys.exit(EXIT_OOM)
                sys.exit(2)
            print(f"[{wid}] 自然 {d1:.1f}s → factor {factor:.3f} 重生成 "
                  f"{atempo.probe_duration(raw):.1f}s", flush=True)
            raw_s = atempo.probe_duration(raw)
        else:
            raw_s = d1

        final_s = atempo.apply_atempo(raw, budget, final, raw_s=raw_s)
        levelnorm.normalize_file(final)
        peak = levelnorm.peak_db(final)
        manifest.append(manifest_row(wid, text, budget, raw_s, factor,
                                     final_s, peak))
        _flush_manifest(manifest, rpt_dir, a.runid)   # 每窗即落盘（P2-6）：中途 OOM/失败不丢记账
        dev = final_s / budget - 1
        print(f"[{wid}] ✅ final {final_s:.2f}s（预算 {budget:.2f}s，"
              f"Δ{dev:+.1%}，peak {peak:.1f}dB）", flush=True)

    out = os.path.join(rpt_dir, f"tts_manifest_{a.runid}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"\n完成 {len(manifest)} 窗，manifest → {out}")
    print_followups(a.project)


if __name__ == "__main__":
    main()
