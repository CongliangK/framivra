#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tts_cli.py · TTS 口播占位语音产线总入口（系统 python3，stdlib + ffmpeg）

来源：方案 §4（2026-09-11 三大模块整合方案 v2.1）——voice-ime/assets/voice_tts/
产线（gen.py / run_v2.py / normalize.py / refit_final.py）参数化收编：
27 窗硬编码 → spec.json 驱动；产物按 apps/sync/repack_voice.py 既有消费约定
落位（assets/voice_tts/final/<ID>.wav，48kHz/mono/pcm_s16le，时长=链帧/30+20ms），
vtx prepare 与 repack_voice 均原样消费，零协议改动。

子命令：
  spec       timeline.json voice 轨 vt_<ID> 事件链 → spec.json（预算反推）
  gen        spec.json → 合成 + atempo 贴预算 + 峰值归一 → final/<ID>.wav
  normalize  final/ 批量峰值归一 -6dBFS（幂等）
  probe      资产探测打印（repo/解释器/音色参考/edge-tts/GPU）

引擎：indextts（默认，克隆本人音色，GPU+repo，由配置解释器执行
indextts_worker.py）/ edge（晓晓，免 GPU 免模型，审听级兜底）。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audio_processing  # noqa: E402
import spec as spec_mod  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(HERE))  # v2 迁移后 tts 在 apps/ 下，仓根 = 上两级
DEFAULT_REPO = os.path.expanduser("~/AI/IndexTTS/repo")
DEFAULT_TTS_PY = os.path.expanduser("~/miniconda3/envs/indextts/bin/python")
DEFAULT_VOICE_REF = os.path.expanduser("~/音乐/5_G_Resemble仅降噪.wav")
DEFAULT_EDGE_BIN = os.path.expanduser("~/miniconda3/bin/edge-tts")
EXIT_ENGINE_FAIL = 3  # indextts_worker 同款（OOM 等），触发 edge 降级提示


# ---------------------------------------------------------------- config 容错
def load_config() -> dict:
    """仓库配置（v2 §5.3 统一加载入口 vtx/pluginlib/config.py，经 spec.load_config
    委托）：VTX_CONFIG > repo config.local.json；坏 JSON 警告 + {}；路径型键
    ~ 展开。键结构与默认探测链不变，只统一加载入口（四重复制收敛之一）。"""
    return spec_mod.load_config()


def tts_cfg(key: str, default):
    """tts.<key> 容错读取（spec.cfg_get 同款，~ 展开）。"""
    v = spec_mod.cfg_get(load_config(), f"tts.{key}", default)
    return os.path.expanduser(v) if isinstance(v, str) else v


def new_runid() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def print_followups(project_dir: str):
    """repack_voice.py 同款完整交付入口（gen 完打印）。"""
    tl = os.path.join(project_dir, "timeline.json")
    print("\n正式交付后续（仓库根执行；final 已就位，会更新项目 cache/、out/）：")
    print(f'  python3 vtx/vtx.py -t "{tl}" all')
    print("  完整 all 负责当前阶段回执与启用插件的 finish；检查退出码和 QC/插件结果。")
    print("  局部音频诊断须先对当前输入 prepare，再 audio --report；最终仍运行完整 all。")
    print("  操作与前置见 docs/skill/references/runbook.md；vtx audio 已内置限幅。")
    print(f"（实录终版替换：python3 apps/sync/repack_voice.py -t {tl}）")


# ---------------------------------------------------------------- 子命令
def cmd_spec(a):
    ids = set(a.ids.split(",")) if a.ids else None
    spec = spec_mod.build_spec(a.timeline, ids=ids, cfg=load_config())  # 已打印每窗明细
    text = json.dumps(spec, ensure_ascii=False, indent=1)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"\n✓ {len(spec['windows'])} 窗 → {a.out}（text 置空，请人工校对回填后再 gen）")
    else:
        print(text)


def load_spec(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        s = json.load(f)
    for w in s.get("windows", []):
        if "budget_s" not in w or "id" not in w:
            raise SystemExit(f"✗ spec 窗口缺 id/budget_s：{json.dumps(w, ensure_ascii=False)}")
    return s


def cmd_gen(a):
    spec = load_spec(a.spec)
    engine = a.engine or spec.get("engine", "indextts")
    ids = set(a.ids.split(",")) if a.ids else None
    windows = spec["windows"]
    if ids:
        windows = [w for w in windows if w["id"] in ids]
    empty = [w["id"] for w in windows if not (w.get("text") or "").strip()]
    if not a.dry_run and empty:
        # text 空窗在 worker 内逐窗跳过并告警；这里先汇总提示
        print(f"⚠ {len(empty)} 窗 text 为空将被跳过：{','.join(empty)}"
              f"（spec 的 text 需人工回填）")

    if a.dry_run:
        print(f"[dry-run] 引擎 {engine}，{len(windows)} 窗：")
        for w in windows:
            print(f"  {w['id']}: {w['budget_s']:.2f}s "
                  f"{len((w.get('text') or '').replace(' ', ''))}字 "
                  f"{'（text 空，将跳过）' if not (w.get('text') or '').strip() else ''}")
        return

    project = os.path.abspath(a.project)
    runid = new_runid()

    if engine == "edge":
        import edge_worker
        edge_bin = tts_cfg("edge_bin", DEFAULT_EDGE_BIN)
        if not os.path.exists(edge_bin) and shutil.which("edge-tts"):
            edge_bin = shutil.which("edge-tts")
        if not os.path.exists(edge_bin):
            raise SystemExit(f"✗ edge-tts 未找到（config tts.edge_bin / PATH），"
                             f"跑 bash apps/tts/init.sh")
        edge_worker.run(windows, project, runid, edge_bin,
                        voice=a.edge_voice, only_missing=a.only_missing)
        print_followups(project)
    elif engine == "indextts":
        py = tts_cfg("python", DEFAULT_TTS_PY)
        if not os.path.exists(py):
            alt = os.path.join(tts_cfg("repo", DEFAULT_REPO), ".venv/bin/python")
            py = alt if os.path.exists(alt) else py
        if not os.path.exists(py):
            raise SystemExit("✗ indextts 解释器未找到（config tts.python / "
                             "conda env / repo/.venv），跑 bash apps/tts/init.sh；"
                             "或先兜底：--engine edge")
        cmd = [py, os.path.join(HERE, "indextts_worker.py"),
               "--spec", a.spec, "--project", project, "--runid", runid,
               "--voice-ref", tts_cfg("voice_ref", DEFAULT_VOICE_REF),
               "--repo", tts_cfg("repo", DEFAULT_REPO)]
        if a.ids:
            cmd += ["--ids", a.ids]
        if a.only_missing:
            cmd += ["--only-missing"]
        r = subprocess.run(cmd)
        if r.returncode == EXIT_ENGINE_FAIL:
            sys.exit(EXIT_ENGINE_FAIL)  # 已打印降级提示
        sys.exit(r.returncode)
    else:
        raise SystemExit(f"✗ 未知引擎：{engine}（indextts | edge）")


def cmd_normalize(a):
    final_dir = os.path.join(os.path.abspath(a.project),
                             "assets", "voice_tts", "final")
    if not os.path.isdir(final_dir):
        raise SystemExit(f"✗ 目录不存在：{final_dir}")
    ids = set(a.ids.split(",")) if a.ids else None
    audio_processing.normalize_dir(final_dir, ids=ids)


def cmd_probe(a):
    checks = []

    def rec(name, ok, detail):
        checks.append(ok)
        print(f"  {'✓' if ok else '✗'} {name} — {detail}")

    print("=== tts 资产探测 ===")
    repo = tts_cfg("repo", DEFAULT_REPO)
    ck = os.path.join(repo, "checkpoints")
    need = ["config.yaml", "gpt.pth", "qwen0.6bemo4-merge", "codec.pth", "s2mel.pth"]
    missing = [n for n in need
               if not os.path.exists(os.path.join(ck, n))]
    rec("repo", not missing,
        f"{repo}" if not missing else f"checkpoints 缺 {missing}（跑 bash apps/tts/init.sh）")

    py = tts_cfg("python", DEFAULT_TTS_PY)
    if not os.path.exists(py):
        alt = os.path.join(repo, ".venv/bin/python")
        py = f"{py} → {alt}" if os.path.exists(alt) else py
    rec("解释器", os.path.exists(py), py if os.path.exists(py)
        else f"{py} 不存在（跑 bash apps/tts/init.sh）")

    vr = tts_cfg("voice_ref", DEFAULT_VOICE_REF)
    rec("音色参考", os.path.exists(vr),
        vr if os.path.exists(vr) else f"{vr} 缺（放任意 5-10s 干声，改 config tts.voice_ref）")

    eb = tts_cfg("edge_bin", DEFAULT_EDGE_BIN)
    if not os.path.exists(eb) and shutil.which("edge-tts"):
        eb = shutil.which("edge-tts")
    rec("edge-tts", os.path.exists(eb),
        eb if os.path.exists(eb) else "PATH 亦无（pip install -i https://pypi.tuna.tsinghua.edu.cn/simple edge-tts）")

    gpu = shutil.which("nvidia-smi")
    ok_gpu = False
    if gpu:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total",
                            "--format=csv,noheader"], capture_output=True, text=True)
        ok_gpu = r.returncode == 0
        rec("GPU", ok_gpu, r.stdout.strip()[:60])
    else:
        rec("GPU", False, "无 nvidia-smi → 建议 --engine edge")
    n_ok = sum(1 for x in checks if x)
    print(f"--- 结果：{n_ok}/{len(checks)} 项就绪"
          f"{'（indextts 可用）' if all(checks[:3]) and checks[-1] else '（缺项见上；edge 兜底随时可用）'} ---")


def main():
    ap = argparse.ArgumentParser(description="TTS 口播占位语音产线（IndexTTS-2.5 / edge-tts）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("spec", help="timeline voice 轨 → spec.json")
    sp.add_argument("-t", "--timeline", required=True)
    sp.add_argument("-o", "--out", default=None)
    sp.add_argument("--ids", default=None)
    sp.set_defaults(fn=cmd_spec)

    gp = sub.add_parser("gen", help="spec.json → final/<ID>.wav")
    gp.add_argument("spec", help="spec.json 路径")
    gp.add_argument("--project", required=True, help="项目目录")
    gp.add_argument("--engine", choices=["indextts", "edge"], default=None,
                    help="缺省取 spec.engine（再缺省 indextts）")
    gp.add_argument("--ids", default=None)
    gp.add_argument("--dry-run", action="store_true")
    gp.add_argument("--only-missing", action="store_true", help="final 已在的窗跳过（断点续跑）")
    gp.add_argument("--edge-voice", default=None, help="edge 引擎音色（缺省晓晓）")
    gp.set_defaults(fn=cmd_gen)

    np_ = sub.add_parser("normalize", help="final/ 批量峰值归一 -6dBFS（幂等）")
    np_.add_argument("--project", required=True)
    np_.add_argument("--ids", default=None)
    np_.set_defaults(fn=cmd_normalize)

    pp = sub.add_parser("probe", help="资产探测打印")
    pp.set_defaults(fn=cmd_probe)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
