"""Prepare real-source strict fixtures; never launches Chrome or downloads.

Call prepare(destination, actual_font_path) from the authorized hosted harness.
The supplied font must be an actual font installation/file, not a version stub.
"""
import hashlib
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.gfx.test_gfx_cli_fallback import MINIMAL_PNG


def prepare(destination, font):
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    font = Path(font).resolve(strict=True)
    if font.suffix.lower() not in ('.woff', '.woff2', '.ttf', '.otf'):
        raise ValueError('an actual font file is required')
    font_name = 'fixture-font' + font.suffix.lower()
    shutil.copyfile(font, root / font_name)
    (root / 'image a.png').write_bytes(MINIMAL_PNG)
    (root / 'shared.css').write_text('body {margin:0;font-family:Fixture} img {width:32px;height:32px}')
    clips = []
    for index, page in enumerate(('a', 'b')):
        (root / (page + '.html')).write_text(
            '<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="' + page + '.css">'
            '<body><p>Strict dependency ' + page + '</p><img src="image%20a.png"></body>')
        (root / (page + '.css')).write_text('@import "shared.css";@import "font.css";'
                                           'body {background:' + ('red' if index == 0 else 'blue') + '}')
        clips.append({'src': page + '.html', 'dst_in': index * 2, 'dur': 2,
                      'cache_dependencies': [page + '.css', 'font.css', font_name, 'image a.png']})
    (root / 'font.css').write_text('@font-face {font-family:Fixture;src:url("' + font_name + '")}')
    timeline = {'version': 2, 'project': {'fps': 30, 'width': 320, 'height': 180,
                'duration_frames': 4, 'gfx_dependency_policy': 'strict',
                'cache_dependencies': ['shared.css']}, 'sources': [], 'subtitles': [],
                'audio_tracks': [], 'video_tracks': [{'kind': 'gfx', 'name': 'gfx', 'clips': clips}]}
    (root / 'timeline.json').write_text(json.dumps(timeline, indent=2))
    # Retain preparation identity; this is input evidence, not a render result.
    manifest = {'schema': 1, 'status': 'prepared-not-rendered',
                'font_source': str(font), 'files': {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(root.iterdir()) if p.is_file()}}
    (root / 'fixture-inputs.json').write_text(json.dumps(manifest, indent=2))
    return root / 'timeline.json'
