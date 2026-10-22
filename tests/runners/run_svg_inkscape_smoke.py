#!/usr/bin/env python3
"""Explicit two-frame real-engine smoke. Missing Inkscape/fonts is a failure.

Not part of the portable offline suite. Writes only a caller-owned evidence
project; no browser, downloads, generator execution, or platform fallback.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from vtx.render import gfx_svg
from vtx.cli.commands import _gfx
from vtx.render.frame_source import png_source
from vtx.render.gfx_cache_reader import resolve_frames
from vtx.pipeline.infra import Ctx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Resolve the locally installed font, then render with only its copied bytes.
    match = subprocess.run(['fc-match', '-f', '%{file}\n', 'Noto Sans CJK SC'],
                           check=True, capture_output=True, text=True)
    font = Path(match.stdout.strip())
    if not font.is_file() or font.suffix.lower() != '.ttc':
        raise RuntimeError('real smoke requires installed Noto Sans CJK TTC')
    target = root / 'fonts' / font.name
    target.parent.mkdir(exist_ok=True)
    shutil.copyfile(font, target)
    config = root / 'fonts.conf'
    config.write_text('<?xml version="1.0"?><fontconfig><dir>' + escape(str(target.parent)) +
                      '</dir><cachedir>' + escape(str(root / 'font-cache')) + '</cachedir></fontconfig>')
    env = dict(os.environ, FONTCONFIG_FILE=str(config), FONTCONFIG_PATH=str(root))
    env.pop('FONTCONFIG_SYSROOT', None)
    face = subprocess.run(['fc-match', '-f', '%{file}|%{family}|%{index}\n', 'Noto Sans CJK SC'],
                          check=True, capture_output=True, text=True, env=env).stdout.strip()
    if str(target) not in face or 'Noto Sans CJK SC' not in face or not face.endswith('|2'):
        raise RuntimeError(f'isolated font selection did not select declared SC face 2: {face}')
    frames = []
    for i in range(2):
        name = f'frame{i}.svg'; frames.append(name)
        (root / name).write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">'
            f'<rect x="{80+i*100}" y="40" width="200" height="100" fill="#ff0000" fill-opacity="0.5"/>'
            '<text x="40" y="300" font-family="Noto Sans CJK SC" font-size="72" fill="#00ffff">'
            f'你好 SVG {i}</text></svg>', encoding='utf-8')
    manifest = dict(format=gfx_svg.FORMAT,fps=30,width=1920,height=1080,frame_count=2,
                    frames=frames,fonts=[str(target.relative_to(root))])
    (root / 'manifest.json').write_text(json.dumps(manifest))
    clip = dict(format='svg-sequence',src='manifest.json',dst_in=0,dur=2)
    timeline = dict(version=2,project=dict(fps=30,width=1920,height=1080,duration_frames=2),
                    sources=[],video_tracks=[dict(name='svg',kind='gfx',clips=[clip])],subtitles=[],audio_tracks=[])
    (root / 'timeline.json').write_text(json.dumps(timeline))
    c = Ctx(root / 'timeline.json')
    _gfx(c)
    source = png_source(c,clip); source.require_ready()
    first = (source.directory / gfx_svg.COMPLETE_FILE).read_bytes()
    _gfx(c)
    source.require_ready()
    assert first == (source.directory / gfx_svg.COMPLETE_FILE).read_bytes(), 'warm completion changed'
    assert resolve_frames(c,clip) == [source.frame_path(0),source.frame_path(1)]
    from PIL import Image
    images = [Image.open(source.frame_path(i)).convert('RGBA') for i in range(2)]
    for i, image in enumerate(images):
        assert image.size == (1920,1080)
        assert image.getpixel((1910,1070))[3] == 0, 'transparent background lost'
        assert image.getpixel((100+i*100,60)) == (255,0,0,128), 'shape/alpha differs'
        assert image.crop((30,210,600,320)).getbbox(), 'declared CJK text missing'
    assert images[0].tobytes() != images[1].tobytes(), 'local frames were duplicated'
    marker=json.loads(first)
    report=dict(status='PASS',frames=2,engine=gfx_svg.engine_identity(),font_face=face,
                cache=str(source.directory),sha256=marker['sha256'],checks=[
                    'cold completion','warm unchanged marker','independent SVG completion reader',
                    '1920x1080','transparent and half-alpha pixels','visible CJK text',
                    'declared TTC face 2','clip-local frame difference'])
    (root/'smoke-result.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,ensure_ascii=False))

if __name__ == '__main__': main()
