"""Small test-only HTML/subtitle/media fixture, never a public Demo or benchmark.

Two fresh ordinary sources and two independent 60-frame browser pages preserve
capabilities absent from the graphics-only public work. No checked-in media.
"""
import argparse
import json
from pathlib import Path
import subprocess

FRAMES = 180
FPS = 30
PAGES = ('canvas', 'dom')
WORKLOADS = {'full': (('promo', 900), ('capability', FRAMES)), 'capability': (('capability', FRAMES),)}
# Frame-space events: source splice/loop at 90, browser pages [15,75), [105,165),
# subtitles [10,30), [40,115), [125,175), second audio [60,150).
# Also probe the ACTUAL segment cuts from the renderer log, not a requested knob.
BOUNDARIES = (0, 9, 10, 14, 15, 29, 30, 39, 40, 44, 59, 60, 74, 75,
              89, 90, 104, 105, 114, 115, 124, 125, 134, 149, 150,
              164, 165, 174, 175, 179)


def create(project, ffmpeg, run):
    """Create new inputs; caller supplies a checked command/logging function."""
    project = Path(project)
    project.mkdir(parents=False, exist_ok=False)
    (project / 'gfx').mkdir()
    for name, hue, frequency in (('left', 25, 392), ('right', 155, 659)):
        run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
             f'testsrc2=s=1920x1080:r=30:d=3,hue=h={hue}',
             '-f', 'lavfi', '-i', f'sine=frequency={frequency}:sample_rate=48000:duration=3',
             '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
             '-c:a', 'aac', '-ac', '2', '-ar', '48000', '-shortest',
             project / f'source-{name}.mp4'])
    (project / 'gfx/canvas.html').write_text('''<!doctype html><style>html,body{margin:0;background:transparent}</style>
<canvas id="probe" width="1920" height="1080"></canvas><script>
const frame=Number(new URLSearchParams(location.search).get('f'));
const canvas=document.getElementById('probe'), ctx=canvas.getContext('2d');
ctx.fillStyle='#2abfa0';ctx.globalAlpha=.8;ctx.fillRect(220+frame*4,220,800,420);
ctx.globalAlpha=1;ctx.fillStyle='white';ctx.font='64px sans-serif';ctx.fillText('FRAME '+frame,260,430);
window.__vtx_export_png=()=>canvas.toDataURL('image/png');
</script>''', encoding='utf-8')
    (project / 'gfx/dom.html').write_text('''<!doctype html><style>html,body{margin:0;background:transparent}
div{position:absolute;left:420px;top:240px;width:1000px;height:480px;box-sizing:border-box;
border:32px solid #cb5fce;background:rgba(30,25,50,.7);color:white;font:72px sans-serif;padding:80px}</style>
<div>DOM capability</div>''', encoding='utf-8')
    timeline = {'version': 2, 'project': {'fps': FPS, 'width': 1920, 'height': 1080, 'duration_frames': FRAMES},
        'sources': [{'id': name, 'path': f'source-{name}.mp4'} for name in ('left', 'right')],
        'video_tracks': [{'name': 'ordinary-media', 'kind': 'aroll', 'clips': [
            {'src': name, 'src_in': 0, 'src_out': 90, 'dst_in': i * 90, 'dur': 90}
            for i, name in enumerate(('left', 'right'))]},
            {'name': 'browser-capability', 'kind': 'gfx', 'clips': [
                {'src': f'gfx/{name}.html', 'dst_in': start, 'dur': 60}
                for name, start in zip(PAGES, (15, 105))]}],
        'subtitles': [{'in': start, 'out': end, 'text': text} for start, end, text in (
            (10, 30, 'Opening subtitle'), (40, 115, 'Across segment and source splice'),
            (125, 175, 'Closing subtitle'))],
        'audio_tracks': [{'name': 'loop', 'events': [{'src': 'left', 'in': 0, 'out': FRAMES, 'loop': True, 'volume': .3}]},
                         {'name': 'overlap', 'events': [{'src': 'right', 'in': 60, 'out': 150, 'volume': .25}]}]}
    path = project / 'timeline.json'
    path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    create(args.output, args.ffmpeg, lambda argv: subprocess.run([str(x) for x in argv], check=True))
