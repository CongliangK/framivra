#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spec.py · timeline.json voice 轨 vt_<ID> 事件链 → spec.json（口播占位预算反推）

来源：voice-ime 产线参数化收编（run_v2.py 的「T3 实排窗口帧数 = BUDGETS」硬
编码表 → 从 timeline voice 轨自动反推）。预算口径与 repack_voice.py 既有消费
约定严格同源（零协议改动）：

  链帧 chain_f = max(每个事件的 src_in + (out - in))    ← repack_voice.py:118 同款
  budget_s    = chain_f / fps + 0.02                    ← 链帧/30 + 20ms 句尾保护
                                                         （TAIL_MS 同款；run_v2 的
                                                          +0.02 防取整少 1 帧余量）

窗口 ID = 源 id 去 `vt_` 前缀，与 repack_voice 的 take/final 文件名
（<项目>/assets/voice_tts/final/<ID>.wav）直接对应。

spec.json 的 text 字段由本工具置空、**人工校对后回填**（timeline voice 轨
事件不含文本；口播逐字稿在脚本/字幕侧）。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FPS_DEFAULT = 30
TAIL_S = 0.02  # 句尾保护余量（repack_voice TAIL_MS 同款）

DEFAULT_REPO_ROOT = os.path.dirname(os.path.dirname(HERE))  # 仓库根（v2 迁移后 tts 在 apps/ 下，= 上两级）


def cfg_get(cfg: dict, path: str, default):
    """cfg_get(cfg, "tts.repo", 默认)——逐级 get，缺键/None→default（方案 §7.2）。"""
    cur = cfg
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    if cur is None:
        return default
    if isinstance(default, bool):
        if isinstance(cur, str):
            return cur.strip().lower() not in ("0", "false", "no", "off")
        return bool(cur)
    if isinstance(default, str) and not isinstance(cur, str):
        return default      # 类型不符（如 python=99）→ 默认（P2-3）
    if isinstance(cur, bool):
        return default      # bool 污染非布尔键 → 默认
    return cur


def _pluginlib_config():
    """vtx.pluginlib.config（v2 §5.3 统一加载）——repo 根追加进 sys.path 后导入
    （本模块平铺布局，脚本目录不含 repo 根；append 不抢占既有解析序）。"""
    if DEFAULT_REPO_ROOT not in sys.path:
        sys.path.append(DEFAULT_REPO_ROOT)
    from vtx.pluginlib import config as _cfg
    return _cfg


def load_config(root: str = None) -> dict:
    """仓库配置（v2 §5.3 统一加载入口 vtx/pluginlib/config.py）：
    VTX_CONFIG 环境变量 > repo 根 config.local.json；坏 JSON 一律警告 + 空 {}
    继续（全局化语义——原静默 {} 收敛）；路径型键（tts.repo/python/voice_ref/
    edge_bin）自动 ~ 展开。键结构与默认探测链不变，只统一加载入口。"""
    return _pluginlib_config().load_config(root or DEFAULT_REPO_ROOT)


def expanduser(p: str) -> str:
    return os.path.expanduser(p) if isinstance(p, str) else p


def build_spec(timeline_path: str, ids=None, cfg: dict = None) -> dict:
    """timeline.json → spec dict。非 vt_ 前缀源跳过并警告（print，随仓风格）。"""
    cfg = cfg if cfg is not None else load_config()
    with open(timeline_path, encoding="utf-8") as f:
        tl = json.load(f)
    fps = FPS_DEFAULT
    proj = tl.get("project")
    if isinstance(proj, dict) and isinstance(proj.get("fps"), (int, float)) \
            and proj["fps"] > 0:
        fps = proj["fps"]

    vtrack = None
    for tr in tl.get("audio_tracks", []):
        if tr.get("name") == "voice":
            vtrack = tr
            break
    if vtrack is None:
        raise RuntimeError(f"✗ timeline 无 voice 音轨：{timeline_path}")

    by_src = {}
    for e in vtrack.get("events", []):
        by_src.setdefault(e["src"], []).append(e)

    windows, skipped = [], []
    for src, evs in sorted(by_src.items()):
        if not src.startswith("vt_"):
            skipped.append(src)
            print(f"⚠ 跳过非 vt_ 前缀源：{src}（voice 轨应只放 TTS 占位源）")
            continue
        wid = src[3:]
        if ids and wid not in ids:
            continue
        chain_f = max(e.get("src_in", 0) + (e["out"] - e["in"]) for e in evs)
        budget_s = round(chain_f / fps + TAIL_S, 3)
        windows.append({"id": wid, "text": "", "budget_s": budget_s,
                        "n_events": len(evs)})
        print(f"✓ {wid}: {len(evs)} 片事件 链 {chain_f} 帧 → 预算 {budget_s}s")

    return {
        "version": 1,
        "engine": cfg_get(cfg, "tts.engine", "indextts"),
        "voice_ref": expanduser(cfg_get(
            cfg, "tts.voice_ref", "~/音乐/5_G_Resemble仅降噪.wav")),
        "fps": fps,
        "windows": windows,
    }


def main():
    ap = argparse.ArgumentParser(
        description="timeline.json voice 轨 → TTS spec.json（预算=事件链帧/30+20ms）")
    ap.add_argument("-t", "--timeline", required=True, help="timeline.json 路径")
    ap.add_argument("-o", "--out", default=None,
                    help="spec.json 输出路径（缺省打印到 stdout）")
    ap.add_argument("--ids", default=None,
                    help="只处理指定窗口 ID（逗号分隔，如 A1,VO-P0-1）")
    a = ap.parse_args()
    ids = set(a.ids.split(",")) if a.ids else None
    spec = build_spec(a.timeline, ids=ids)
    n = len(spec["windows"])
    text = json.dumps(spec, ensure_ascii=False, indent=1)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"\n✓ {n} 窗 → {a.out}（text 置空，请人工校对回填后再 gen）")
    else:
        print(text)
        print(f"（{n} 窗；text 置空，请人工校对回填后再 gen）", file=sys.stderr)


if __name__ == "__main__":
    main()
