#!/usr/bin/env python3
"""Compatibility entry point; implementation stays in the owning component."""
import runpy
import sys
from pathlib import Path

target = (Path(__file__).resolve().parent / '../apps/research/score_topic.py').resolve()
sys.path.insert(0, str(target.parent))
if sys.platform == 'win32' and __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
globals().update(runpy.run_path(str(target), run_name=__name__))
