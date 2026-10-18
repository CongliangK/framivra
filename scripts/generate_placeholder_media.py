#!/usr/bin/env python3
"""Generate black/silent fixtures without external input. Default prints a zero-write plan.

--write replaces repository fixtures. Encoded bytes depend on the FFmpeg build;
review inventory hashes after regeneration. Script and fixtures use root LICENSE.
"""
import argparse
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / 'templates/tech-talk/assets/presets/placeholders'


def commands(destination=DESTINATION):
    base = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y']
    return [base + ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=mono', '-t', '0.5',
                    '-map_metadata', '-1', '-c:a', 'pcm_s16le', str(destination / 'placeholder_audio.wav')],
            base + ['-f', 'lavfi', '-i', 'color=c=black:s=320x180:r=30:d=1',
                    '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=mono', '-t', '1',
                    '-map_metadata', '-1', '-c:v', 'libx264', '-threads', '1',
                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart',
                    str(destination / 'placeholder_av.mp4')]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='WRITES: replace repository fixtures')
    args = parser.parse_args()
    for command in commands():
        print(shlex.join(command), flush=True)
        if args.write:
            subprocess.run(command, check=True)


if __name__ == '__main__':
    main()
