#!/usr/bin/env python
"""MatAnyone v1 许可阻断入口（保留旧调用路径）
基于 works/MatAnyone/inference_matanyone.py（本地权重版）
"""
import sys

# Upstream model import/construction can fetch an independently licensed ResNet.
# Stop this unreviewed legacy chain before third-party imports or output writes.
import importlib.util
from pathlib import Path
_policy_path = Path(__file__).resolve().parents[2] / "scripts" / "model_license_policy.py"
_policy_spec = importlib.util.spec_from_file_location("framivra_matting_license_policy", _policy_path)
_policy = importlib.util.module_from_spec(_policy_spec)
_policy_spec.loader.exec_module(_policy)
try:
    _policy.block_legacy_download("matanyone-runtime")
except _policy.LicensePolicyError as error:
    print("model-license policy: " + str(error), file=sys.stderr)
    raise SystemExit(4)
