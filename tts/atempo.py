#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""atempo.py · ffmpeg atempo 贴预算纯函数 + 应用器（tts/ 模块内聚，零第三方依赖）

来源：voice-ime/assets/voice_tts/ 产线内化（run_v2.py fit_robust + refit_final.py
fit_fixed 参数化重写）。方向坑警示（gen.py 前车之鉴，R8）：

  ffmpeg `atempo=X` 语义 = 播放速度 X 倍 → 输出时长 = 输入时长 / X。
  要把 raw（实测时长）贴到 budget（窗口预算），必须 **atempo = raw / budget**。
  voice-ime gen.py 首版写反成 budget/raw，27 窗 final 全部反向偏离
  （raw 9.14s 贴 7.62s 预算被拉长而非压短），2026-08-30 refit_final.py
  实测定向修正。本文件以 run_v2.py 修正版为唯一实现，golden 单测钉死：
  raw=9.14 / budget=7.62 → atempo=1.199475（= 9.14/7.62）。

域外双级链：atempo 单级有效域 [0.5, 2.0]；域外用 `atempo=√r,atempo=√r`
两级串联（refit_final.py:19-24 同款），更极端的比值依次再拆（r^(1/n)
落入域内为止，n 级全同值串联）。

注意与 duration_factor 的区别：duration_factor 是 IndexTTS2 的模型参数
（音长→传 <1 说短，音短→传 >1 放宽），方向恰好与 atempo 相反，勿混淆
（run_v2.py 修正注记照录）。
"""
import os
import subprocess

MIN_TEMPO = 0.5
MAX_TEMPO = 2.0


def atempo_chain(raw_s: float, budget_s: float) -> str:
    """纯函数：实测时长 raw_s 贴预算 budget_s 所需的 atempo 滤镜串。

    返回值口径：
      - 返回 ""（空串）= 无需滤镜——|raw/budget - 1| < 1e-9 时输出时长已等于
        预算，调用方直接跳过 -af（应用器 apply_atempo 同口径；返回空而非
        "atempo=1.0" 是为彻底不动音频流）。
      - 域内单级 "atempo=1.199475"；域外 n 级同值串联 "atempo=√r,atempo=√r"
        （更极端依次再拆，n 级全同值）。
    """
    if raw_s <= 0 or budget_s <= 0:
        raise ValueError(f"时长非法：raw={raw_s}s budget={budget_s}s")
    r = raw_s / budget_s
    if abs(r - 1.0) < 1e-9:
        return ""
    n = 1
    while True:
        seg = r ** (1.0 / n)
        if MIN_TEMPO <= seg <= MAX_TEMPO:
            one = f"atempo={seg:.6f}"
            return one if n == 1 else ",".join([one] * n)
        n += 1


def apply_atempo(raw_path: str, budget_s: float, out_path: str,
                 raw_s: float = None) -> float:
    """对 raw 音频套 atempo_chain 并落 final 约定格式（48kHz/mono/pcm_s16le）。

    raw_s 缺省现场 ffprobe 实测。返回输出时长（秒）。链无需滤镜时仍做
    重采样/单声道/s16 转码（保证 final 格式契约），音频流零变速。

    精确落点（2026-09-11 集成验收发现）：atempo 滤镜内部按帧重采样，输出
    时长有 ~0.3% 样本量化漂移（实测 7.5989s vs 7.62 预算，占位态少 1 帧
    被 vtx validate 拦）。故输出统一 `apad`（无限尾静音）+ `-t budget`：
    长了裁、短了补，输出样本数恰为 round(budget×48000)。尾部 ≤20ms 静音
    与「链帧+20ms 句尾保护」语义一致；峰值归一按 max_volume 计，静音尾巴
    不影响 levelnorm 结果（先落点后归一两可，本链保持先落点）。
    """
    if raw_s is None:
        raw_s = probe_duration(raw_path)
    chain = atempo_chain(raw_s, budget_s)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", raw_path,
           "-filter:a", (chain + ",apad") if chain else "apad",
           "-t", f"{budget_s:.6f}",
           "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"atempo 贴预算失败：{r.stderr[:300]}")
    return probe_duration(out_path)


def probe_duration(path: str) -> float:
    """ffprobe 容器时长（秒）。"""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"ffprobe 失败 {path}：{r.stderr[:200]}")
    return float(r.stdout.strip())


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="atempo 参数计算（诊断用）")
    ap.add_argument("raw_s", type=float, help="实测时长（秒）")
    ap.add_argument("budget_s", type=float, help="窗口预算（秒）")
    a = ap.parse_args()
    chain = atempo_chain(a.raw_s, a.budget_s)
    print(f"✓ r={a.raw_s / a.budget_s:.6f} → {chain or '（无需滤镜）'}")
