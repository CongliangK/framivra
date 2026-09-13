#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""edge_worker.py · edge-tts 兜底引擎（免 GPU 免模型，审听级占位）

来源：方案 §4.4 兜底档设计（voice-ime 产线 atempo/归一链复用 + edge-tts
二进制 subprocess）。定位：IndexTTS repo/GPU 缺失时产线不断的兜底，不作
主力（微软接口非官方承诺，见模块 README 风险节）。

流程（每窗，与 indextts_worker 同构）：
  1) edge-tts 二进制合成（晓晓 zh-CN-XiaoxiaoNeural，免鉴权；默认输出
     24kHz mp3）→ cache/tmp/<runid>/edge_<ID>.mp3
  2) ffmpeg 重采样 48k mono s16 → raw 中间物 cache/tmp/<runid>/raw_<ID>.wav
  3) atempo.apply_atempo 贴预算 → assets/voice_tts/final/<ID>.wav
  4) levelnorm 峰值归一 -6dBFS（幂等）
  5) manifest 记账（duration_factor 对 edge 恒 null——无该模型参数）

本文件用系统 python3 运行（stdlib + ffmpeg subprocess），由 tts_cli.py 平铺
import（sys.path.insert 手法，vtx 同款）。
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import atempo  # noqa: E402
import levelnorm  # noqa: E402

VOICE_DEFAULT = "zh-CN-XiaoxiaoNeural"  # 晓晓：中文女声，审听级占位默认


def _flush_manifest(manifest, rpt_dir, runid):
    """manifest 增量落盘（P2-6）：每窗成功即写，崩溃/中断后已完成窗口记账仍在盘。"""
    os.makedirs(rpt_dir, exist_ok=True)
    out = os.path.join(rpt_dir, f"tts_manifest_{runid}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)


def n_chars(t: str) -> int:
    """口播字数（空白不计，gen.py chars 同款）。"""
    return len(re.sub(r"\s", "", t or ""))


def synth_raw(text: str, out_wav: str, edge_bin: str,
              voice: str = VOICE_DEFAULT) -> str:
    """edge-tts 合成 → ffmpeg 转 48k/mono/s16 wav。返回 wav 路径。"""
    if not (text or "").strip():
        raise ValueError("✗ 文本为空（spec 的 text 需人工回填后再 gen）")
    mp3 = os.path.splitext(out_wav)[0] + ".mp3"
    os.makedirs(os.path.dirname(os.path.abspath(mp3)), exist_ok=True)
    r = subprocess.run([edge_bin, "--voice", voice, "--text", text,
                        "--write-media", mp3], capture_output=True, text=True)
    if r.returncode or not os.path.exists(mp3) or os.path.getsize(mp3) == 0:
        raise RuntimeError(f"✗ edge-tts 合成失败：{(r.stderr or r.stdout)[:300]}")
    r2 = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", mp3,
                         "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le",
                         out_wav], capture_output=True, text=True)
    if r2.returncode:
        raise RuntimeError(f"✗ edge wav 转码失败：{r2.stderr[:300]}")
    os.unlink(mp3)
    return out_wav


def finish_window(wid: str, text: str, budget_s: float, raw_wav: str,
                  final_dir: str) -> dict:
    """raw → atempo 贴预算 → final/<ID>.wav → 峰值归一 → manifest 行。

    纯 ffmpeg/本地文件操作（无网络），单测直接对本函数断言 manifest 字段
    与 final 格式（repack_voice.py 消费契约）。
    """
    raw_s = atempo.probe_duration(raw_wav)
    final = os.path.join(final_dir, f"{wid}.wav")
    final_s = atempo.apply_atempo(raw_wav, budget_s, final, raw_s=raw_s)
    levelnorm.normalize_file(final)
    peak = levelnorm.peak_db(final)
    return {"id": wid, "chars": n_chars(text), "budget_s": round(budget_s, 3),
            "raw_s": round(raw_s, 2), "duration_factor": None,
            "final_s": round(final_s, 2), "peak_db": round(peak, 1)}


def run(windows, project_dir: str, runid: str, edge_bin: str,
        voice: str = VOICE_DEFAULT, only_missing: bool = False):
    """多窗串行生成。windows=[{id,text,budget_s},...]。

    落位：raw → <项目>/cache/tmp/<runid>/；final → <项目>/assets/voice_tts/final/；
    manifest → <项目>/cache/reports/tts_manifest_<runid>.json。返回 manifest 列表。
    """
    final_dir = os.path.join(project_dir, "assets", "voice_tts", "final")
    tmp_dir = os.path.join(project_dir, "cache", "tmp", runid)
    rpt_dir = os.path.join(project_dir, "cache", "reports")
    for d in (final_dir, tmp_dir, rpt_dir):
        os.makedirs(d, exist_ok=True)

    manifest = []
    voice = voice or VOICE_DEFAULT
    for w in windows:
        wid, text, budget = w["id"], w.get("text", ""), w["budget_s"]
        final = os.path.join(final_dir, f"{wid}.wav")
        if only_missing and os.path.exists(final):
            print(f"✓ {wid}: final 已存在，跳过（--only-missing）")
            continue
        if not (text or "").strip():
            print(f"✗ {wid}: text 为空，跳过（spec 的 text 需人工回填后再 gen）")
            continue
        raw = os.path.join(tmp_dir, f"raw_{wid}.wav")
        print(f"[{wid}] {n_chars(text)}字 预算 {budget:.2f}s edge-tts 合成中…",
              flush=True)
        synth_raw(text, raw, edge_bin, voice)
        row = finish_window(wid, text, budget, raw, final_dir)
        manifest.append(row)
        _flush_manifest(manifest, rpt_dir, runid)     # 每窗即落盘（P2-6）：中途失败不丢记账
        dev = row["final_s"] / budget - 1
        print(f"[{wid}] ✅ final {row['final_s']:.2f}s（预算 {budget:.2f}s，"
              f"Δ{dev:+.1%}，peak {row['peak_db']}dB）", flush=True)

    out = os.path.join(rpt_dir, f"tts_manifest_{runid}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"\n完成 {len(manifest)} 窗，manifest → {out}")
    return manifest


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="edge-tts 兜底引擎（通常经 tts_cli.py gen --engine edge 调用）")
    ap.add_argument("--spec", required=True, help="spec.json")
    ap.add_argument("--project", required=True, help="项目目录")
    ap.add_argument("--runid", required=True)
    ap.add_argument("--edge-bin", default="edge-tts")
    ap.add_argument("--voice", default=VOICE_DEFAULT)
    ap.add_argument("--only-missing", action="store_true")
    a = ap.parse_args()
    spec = json.load(open(a.spec, encoding="utf-8"))
    run(spec["windows"], a.project, a.runid, a.edge_bin, a.voice, a.only_missing)
