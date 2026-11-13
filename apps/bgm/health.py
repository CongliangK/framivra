#!/usr/bin/env python3
"""Basic code-interface health only: no runtime, assets, GPU or service probe."""

import json
from pathlib import Path


def main():
    from bgm_gen.engines import ENGINES
    from bgm_gen.engines.base import DESCRIPTORS, DESCRIPTOR_ERRORS
    from bgm_gen.core.project import build_render_plan, load_project
    assert set(ENGINES) == {"synth", "sf2", "sfz", "sample", "master", "demucs", "ai"}
    assert set(DESCRIPTORS) == set(ENGINES) and not DESCRIPTOR_ERRORS
    root = Path(__file__).resolve().parent
    assert build_render_plan(load_project(str(root / "examples" / "synth.json")))["duration_s"] > 0
    print(json.dumps({"ok": True, "engines": sorted(ENGINES), "scope": "code interfaces only"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
