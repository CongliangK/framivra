#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""indextts_worker.py · IndexTTS-2.5 旧运行链的许可拒绝入口。

tts_cli.py gen --engine indextts 仍用配置解释器调用此入口。
保留原 CLI 参数，真实许可政策在读取资产、创建产物或导入上游前拒绝该链。
恢复运行需补足具体证据并评审受控适配器；见 docs/third-party-licenses.md。

来源：voice-ime/assets/voice_tts/ gen.py + run_v2.py 内化（参数化重写）。
"""
import argparse
import os
import sys

DEFAULT_REPO = os.path.expanduser("~/AI/IndexTTS/repo")


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

    # Check before importing upstream code, reading private assets or making any
    # project directories: its constructor downloads independently licensed auxiliaries.
    import importlib.util
    from pathlib import Path
    policy_path = Path(__file__).resolve().parents[2] / "scripts" / "model_license_policy.py"
    policy_spec = importlib.util.spec_from_file_location("framivra_model_license_policy", policy_path)
    policy = importlib.util.module_from_spec(policy_spec)
    policy_spec.loader.exec_module(policy)
    try:
        policy.block_legacy_download("indextts")
    except policy.LicensePolicyError as error:
        print("✗ " + str(error), file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
