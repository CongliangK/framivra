#!/usr/bin/env python3
"""Normalize an existing project out/audio.wav without rerendering its timeline.

WRITES: replaces the WAV after loudness and duration checks. --target and
--ffmpeg select the target loudness and tool. This maintenance operation does
not update/invalidate audio, mux or QC receipts; existing success receipts do
not verify the modified output. Repeated runs may change already-limited audio.
Use the normal vtx audio/all pipeline for delivered projects instead.
"""
import argparse, json, os, sys, wave
from pathlib import Path

# v2 §1.4：注入 repo 根（不再注入 repo/vtx——平铺/包双实例会让锁形同虚设）
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vtx.pipeline.stages import normalize_project_audio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', nargs='?', default='.')
    parser.add_argument('--ffmpeg', default=os.environ.get('FFMPEG', 'ffmpeg'))
    parser.add_argument('--target', type=float, default=float(os.environ.get('TARGET_LUFS', '-14')))
    args = parser.parse_args()
    try:
        print(json.dumps(normalize_project_audio(args.project, args.ffmpeg, args.target), ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, KeyError, wave.Error) as exc:
        print(f'FATAL: {exc}', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
