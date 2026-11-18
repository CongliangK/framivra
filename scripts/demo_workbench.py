#!/usr/bin/env python3
"""Prepare the editable Framivra Demo from its shared, checked-in artwork."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

if __package__:
    from . import demo
else:
    import demo


ROOT = Path(__file__).resolve().parents[1]
GRAPHICS = ROOT / 'templates/demo-workbench/graphics.js'
CARULLI = GRAPHICS.with_name('carulli-andantino.json')
SOUNDTRACKS = ('carulli', 'portable')
PROJECT = {'fps': 30, 'width': 1920, 'height': 1080, 'duration_frames': 900}
MEDIA_IN, MEDIA_OUT = 300, 379
CUE_AT, CUE_LEN = 462, 34
RENDERER = ('renderer.js', 'motion-plan.json')
LICENSES = ('licenses/Noto-CJK-copyright.txt', 'licenses/DejaVu-copyright.txt')


def dump(path, value):
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def run(args):
    subprocess.run([str(value) for value in args], check=True, cwd=ROOT)


def graphics_spec():
    return {
        'opening': {'start': 0, 'frames': MEDIA_IN, 'role': 'promo'},
        'ending': {'start': MEDIA_OUT, 'frames': PROJECT['duration_frames'] - MEDIA_OUT, 'role': 'promo'},
        'cue': {'start': 0, 'frames': CUE_LEN, 'role': 'cue'},
    }


def timeline(format_name, font_paths):
    spec = graphics_spec()
    html = format_name == 'html'
    common_deps = [*RENDERER, *font_paths]

    def gfx(key, at):
        return {'format': 'html' if html else 'svg-sequence',
                'src': f'gfx/{key}' + ('.html' if html else '.frames.json'),
                'dst_in': at, 'dur': spec[key]['frames'],
                'cache_dependencies': common_deps if key != 'cue' else []}

    return {
        'version': 2, 'project': PROJECT,
        'sources': [
            {'id': 'rearrangement', 'path': 'assets/rearrangement.mp4'},
            {'id': 'demo_bed', 'path': 'assets/audio/bed-premaster.wav'},
            {'id': 'demo_hit', 'path': 'assets/audio/content-cue.wav'},
        ],
        'video_tracks': [
            {'name': '素材 · 双段剪辑', 'kind': 'broll', 'clips': [
                {'src': 'rearrangement', 'src_in': 8, 'src_out': 47, 'dst_in': MEDIA_IN, 'dur': 39},
                {'src': 'rearrangement', 'src_in': 55, 'src_out': 95, 'dst_in': MEDIA_IN + 39, 'dur': 40},
            ]},
            {'name': '主画面', 'kind': 'gfx', 'clips': [gfx('opening', 0), gfx('ending', MEDIA_OUT)]},
            {'name': '可移动提示', 'kind': 'gfx', 'clips': [gfx('cue', CUE_AT)]},
        ],
        'subtitles': [
            {'in': 120, 'out': 187, 'text': '把画面、字幕与声音放上时间线。'},
            {'in': 304, 'out': 375, 'text': '同一段素材，重新安排剪辑点。'},
            {'in': 448, 'out': 519, 'text': '把提示和音效放在同一帧。'},
            {'in': 614, 'out': 681, 'text': '修改时间线，再验证成片。'},
        ],
        'audio_tracks': [
            {'name': 'bed', 'events': [{'src': 'demo_bed', 'in': 0, 'out': 900, 'volume': 1}]},
            {'name': '素材底声', 'events': [
                {'src': 'rearrangement', 'src_in': src_in, 'in': begin, 'out': end, 'volume': 0.08,
                 'fade_in_frames': 4, 'fade_out_frames': 4}
                for src_in, begin, end in ((8, MEDIA_IN, MEDIA_IN + 39), (55, MEDIA_IN + 39, MEDIA_OUT))]},
            {'name': 'sfx', 'events': [{'src': 'demo_hit', 'src_in': 0, 'in': at, 'out': at + CUE_LEN, 'volume': 1}
                                       for at in (228, CUE_AT)]},
        ],
    }


def make_media(output):
    # Original software-generated source footage, not a pre-render of the demo timeline.
    # RGB is authored directly and converted once to legal YUV before encoding.
    from PIL import Image, ImageDraw

    argv = ['ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', '960x540',
            '-framerate', '30', '-i', '-', '-f', 'lavfi', '-i', 'sine=frequency=98:sample_rate=48000',
            '-c:v', 'libx264', '-preset', 'fast', '-pix_fmt', 'yuv420p',
            '-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
            '-vf', 'scale=out_color_matrix=bt709', '-c:a', 'aac', '-ac', '2', '-shortest', str(output)]
    with subprocess.Popen(argv, stdin=subprocess.PIPE, cwd=ROOT) as process:
        with process.stdin as stream:
            for f in range(100):
                image = Image.new('RGB', (960, 540), '#20282D')
                draw = ImageDraw.Draw(image)
                draw.rounded_rectangle((70, 68, 890, 424), radius=22, fill='#2A343A', outline='#717975', width=2)
                for x in range(124, 851, 66):
                    draw.line((x, 85, x, 395), fill='#3B474A', width=1)
                for y in (155, 232, 309, 386):
                    draw.line((124, y, 850, y), fill='#596463', width=2)
                offset = max(0, min(f - 47, 24)) * 9
                for x, y, length, color in ((190, 120, 265, '#F26145'), (190, 197, 210, '#F6F1E6'),
                                            (190, 274, 165, '#7691F4')):
                    draw.rounded_rectangle((x + offset, y, x + offset + length, y + 26), radius=9, fill=color)
                cursor = 140 + 7 * f
                draw.line((cursor, 103, cursor, 395), fill='#BEDBCB', width=3)
                draw.polygon(((cursor - 8, 92), (cursor + 8, 92), (cursor, 103)), fill='#BEDBCB')
                stream.write(image.tobytes())
        if process.wait() != 0:
            raise demo.DemoError('ordinary source encoding failed')


def prepare_audio(project, audio_lock, soundtrack):
    # The short cue is still the locked integer source, never part of the score.
    staging = project / 'audio-build'
    run([sys.executable, demo.PACKAGE / 'audio/build.py', '--out', staging])
    native = staging / 'native-inputs'
    demo.check_audio(native, audio_lock, transfer=True)
    audio = project / 'assets/audio'
    shutil.copyfile(native / 'content-cue.wav', audio / 'content-cue.wav')
    bed = audio / 'bed-premaster.wav'
    if soundtrack == 'portable':
        shutil.copyfile(native / bed.name, bed)
        return

    score = audio / CARULLI.name
    shutil.copyfile(CARULLI, score)
    music = staging / 'carulli'
    run([sys.executable, ROOT / 'apps/bgm/bgm.py', 'render', score, '-o', music])
    if demo.load_json_file_strict(music / 'qc.json').get('ok') is not True:
        raise demo.DemoError('Carulli music QC failed; inspect audio-build/carulli/qc.json')
    # Match the approved audition: premaster, not an already loudness-normalized
    # music master. Leave final normalization/limiting to the video audio stage.
    run(['ffmpeg', '-v', 'error', '-nostdin', '-n', '-i', music / 'mix_premaster.wav',
         '-af', 'volume=7dB,afade=t=in:ss=0:ns=400,afade=t=out:ss=1424640:ns=12000,'
                'atrim=end_sample=1440000', '-c:a', 'pcm_s24le', bed])


def prepare(args):
    if args.format == 'svg' and (reason := demo.unsupported_prepare_platform()):
        raise demo.DemoError(reason)
    if demo.tool_identity('node') is None:
        raise demo.DemoError('Node.js 18+ required')
    project = demo.check_output_path(args.output)
    fonts_dir = Path(args.fonts_dir).expanduser().absolute()
    baseline = demo.load_baseline()
    files = demo.source_files(baseline)
    audio_lock = demo.load_input_lock(baseline)
    fonts = demo.check_fonts(files, fonts_dir)
    project.mkdir()
    (project / 'gfx').mkdir()
    (project / 'assets/fonts').mkdir(parents=True)
    (project / 'assets/audio').mkdir()
    (project / 'licenses').mkdir()
    for filename in (*RENDERER, *LICENSES, 'fonts.lock.json'):
        (project / filename).write_bytes(files[filename][0])
    for font in fonts:
        target = project / font['path']
        shutil.copyfile(fonts_dir / target.name, target)
    demo.check_fonts(files, project / 'assets/fonts')
    prepare_audio(project, audio_lock, args.soundtrack)
    make_media(project / 'assets/rearrangement.mp4')
    dump(project / 'gfx/spec.json', graphics_spec())
    run(['node', GRAPHICS, project, args.format])
    dump(project / 'timeline.json', timeline(args.format, [font['path'] for font in fonts]))
    run([sys.executable, '-m', 'vtx', '-t', project / 'timeline.json', 'validate'])
    print(project / 'timeline.json')


def add_soundtrack_option(parser):
    parser.add_argument('--soundtrack', choices=SOUNDTRACKS, default='carulli',
                        help='render the Carulli JSON score (default) or use the portable synthetic bed')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    cmd = subs.add_parser('prepare', help='write a fresh project and run product validation')
    cmd.add_argument('--output', required=True)
    cmd.add_argument('--format', choices=('html', 'svg'), default='html')
    cmd.add_argument('--fonts-dir', required=True, help='directory containing the four locked Demo font files')
    add_soundtrack_option(cmd)
    args = parser.parse_args(argv)
    try:
        prepare(args)
    except (demo.DemoError, OSError, ImportError, ValueError, subprocess.CalledProcessError) as error:
        print(f'demo-workbench: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
