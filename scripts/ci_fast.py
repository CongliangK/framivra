#!/usr/bin/env python3
"""Small deterministic CI gate; full discovery remains manual/local.

Whole modules only, with the existing strict lifecycle and zero-skip runner.
No media, subprocess safety stress, installation or interpreter matrix coverage.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.ci_pr import run_curated

FAST_MODULES = (
    'tests.test_ci_checks',
    'tests.test_ci_pr',
    'tests.test_graph_digest',
    'tests.test_plugin_code_digest',
)

if __name__ == '__main__':
    sys.exit(run_curated(FAST_MODULES))
