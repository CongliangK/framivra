"""Continuous semantic line storytelling, rendered on CPU without a browser.

Every icon has a connected pen route. Adjacent semantic routes are joined by a
shared cubic bridge, and drawing advances by geometric arc length. Completed
lines age gently; the final object's existing silhouette becomes a colour hero.
Default separately installed Noto Serif CJK uses SIL OFL 1.1:
https://github.com/notofonts/noto-cjk . No fonts/assets are bundled.
"""
from pathlib import Path
import hashlib
import json
import math
import re

SIZE = (1920, 1080)
OBJECTS = ('seed', 'tree', 'book', 'house', 'network')
DEFAULT_PALETTE = ['#f6f2e9', '#26332f', '#b95e3f', '#d5b776']


def _int(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer in [{low},{high}]')
    return value


def _text(value, name, limit=160, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError(f'{name} must be text of at most {limit} characters')
    return value


def _ellipse(cx, cy, rx, ry, start=-math.pi / 2, count=80):
    return [(cx + rx * math.cos(start + i / count * math.tau),
             cy + ry * math.sin(start + i / count * math.tau)) for i in range(count + 1)]


def _route(kind):
    """Return a continuous pen route and separate honest fill silhouettes."""
    if kind == 'seed':
        body = _ellipse(0, .2, .48, .62, start=-math.pi / 2)
        stem = [(0, -.42), (.08, -.72), (.31, -.91), (.55, -.91), (.4, -.68), (.08, -.72)]
        return body + stem, [body, stem[1:]]
    if kind == 'tree':
        silhouette = [(-.14, .87), (-.14, .36), (-.7, .36), (-.38, -.05), (-.62, -.05),
                      (-.29, -.48), (-.45, -.48), (0, -1), (.45, -.48), (.29, -.48),
                      (.62, -.05), (.38, -.05), (.7, .36), (.14, .36), (.14, .87), (-.14, .87)]
        return silhouette + [(0, .87), (0, -.35)], [silhouette]
    if kind == 'book':
        silhouette = [(0, -.55), (-.28, -.75), (-.83, -.75), (-.83, .59), (-.3, .59),
                      (0, .81), (.3, .59), (.83, .59), (.83, -.75), (.28, -.75), (0, -.55)]
        route = silhouette + [(0, .81), (0, -.55), (-.28, -.75), (-.62, -.75),
                              (-.62, -.32), (-.18, -.32), (-.62, -.32), (-.62, -.04),
                              (-.18, -.04), (-.62, -.04), (-.62, .24), (-.18, .24)]
        return route, [silhouette]
    if kind == 'house':
        silhouette = [(-.75, -.08), (-.75, .81), (.75, .81), (.75, -.08),
                      (.94, -.08), (0, -.95), (-.94, -.08), (-.75, -.08)]
        route = silhouette + [(-.75, .81), (-.18, .81), (-.18, .2), (.18, .2), (.18, .81),
                              (.75, .81), (.75, -.08), (.44, -.08), (.44, .14), (.66, .14),
                              (.66, -.08), (.44, -.08)]
        return route, [silhouette]
    # Travel through all five graph nodes, tracing each node without lifting.
    centers = [(-.74, -.6), (.68, -.56), (0, .03), (-.68, .7), (.72, .7)]
    route, fills = [], []
    for x, y in centers:
        node = _ellipse(x, y, .16, .16)
        route.extend(node)
        fills.append(node)
    return route, fills


def _bridge(start, end):
    """A connected, low-curvature handoff, never a pen teleport."""
    dx = end[0] - start[0]
    control_a = (start[0] + dx * .34, start[1] + 125)
    control_b = (end[0] - dx * .34, end[1] + 125)
    points = []
    for i in range(49):
        t = i / 48
        u = 1 - t
        points.append((u ** 3 * start[0] + 3 * u * u * t * control_a[0]
                       + 3 * u * t * t * control_b[0] + t ** 3 * end[0],
                       u ** 3 * start[1] + 3 * u * u * t * control_a[1]
                       + 3 * u * t * t * control_b[1] + t ** 3 * end[1]))
    return points


def _lengths(points):
    lengths = [0.0]
    for a, b in zip(points, points[1:]):
        lengths.append(lengths[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    return lengths


def _partial(points, lengths, distance):
    """Return route up to an exact arc-length position and its pen tip."""
    distance = max(0, min(lengths[-1], distance))
    drawn = [points[0]]
    for i in range(1, len(points)):
        if lengths[i] <= distance:
            drawn.append(points[i])
            continue
        span = lengths[i] - lengths[i - 1]
        q = (distance - lengths[i - 1]) / span if span else 0
        a, b = points[i - 1], points[i]
        drawn.append((a[0] + q * (b[0] - a[0]), a[1] + q * (b[1] - a[1])))
        break
    return drawn, drawn[-1]


def _prepare(data, frames, font_path=None):
    from PIL import ImageFont
    _int(frames, 'frames', 2, 18000)
    if not isinstance(data, dict):
        raise ValueError('line-art data must be an object')
    title = _text(data.get('title'), 'title')
    subtitle = _text(data.get('subtitle', ''), 'subtitle', empty=True)
    story = data.get('story')
    if not isinstance(story, list) or not 2 <= len(story) <= 4:
        raise ValueError('story must contain 2..4 semantic objects')
    speed = _int(data.get('drawing_speed', 1), 'drawing_speed', 1, 8)
    palette = data.get('palette', DEFAULT_PALETTE)
    if (not isinstance(palette, list) or len(palette) != 4
            or any(not isinstance(x, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', x) for x in palette)):
        raise ValueError('palette must contain four #RRGGBB colours')
    previous_end, previous_tip, scenes = 0, None, []
    cell = 1640 / len(story)
    scale = min(173, cell * .36)
    for i, beat in enumerate(story):
        if not isinstance(beat, dict) or beat.get('object') not in OBJECTS:
            raise ValueError(f'each object must be one of {OBJECTS}')
        _text(beat.get('label'), 'story.label')
        start = _int(beat.get('in'), 'story.in', 0, frames - 2)
        end = _int(beat.get('out'), 'story.out', start + 2, frames)
        if start != previous_end:
            raise ValueError('story windows must be continuous and start at frame zero')
        hold = _int(beat.get('hold', max(1, (end - start) // 4)), 'story.hold', 1, end - start - 1)
        center = (140 + cell * (i + .5), 540)
        local, fill_shapes = _route(beat['object'])
        transform = lambda pt: (center[0] + pt[0] * scale, center[1] + pt[1] * scale)
        path = list(map(transform, local))
        if previous_tip is not None:
            path = _bridge(previous_tip, path[0]) + path[1:]
        lengths = _lengths(path)
        drawing_frames = max(1, math.ceil((end - start - hold) / speed))
        scenes.append({**beat, 'hold': hold, 'drawing_frames': drawing_frames,
                       'path': path, 'lengths': lengths, 'center': center,
                       'fills': [[transform(pt) for pt in shape] for shape in fill_shapes]})
        previous_tip, previous_end = path[-1], end
    font = Path(font_path) if font_path else Path('/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc')
    if not font.is_file():
        raise ValueError('provide a separately licensed local CJK font_path')
    face = 2 if font.name == 'NotoSerifCJK-Regular.ttc' else 0
    return {'title': title, 'subtitle': subtitle, 'scenes': scenes, 'frames': frames,
            'palette': palette, 'font_path': font, 'font_face': face,
            'title_font': ImageFont.truetype(str(font), 70, index=face),
            'label_font': ImageFont.truetype(str(font), 31, index=face),
            'small_font': ImageFont.truetype(str(font), 26, index=face)}


def _wrap(draw, value, font, width, max_lines):
    lines = ['']
    for char in value:
        if char == '\n' or (lines[-1] and draw.textlength(lines[-1] + char, font=font) > width):
            lines.append('')
        if char != '\n':
            lines[-1] += char
    if len(lines) > max_lines:
        raise ValueError('text exceeds the composition region; shorten it')
    return lines


def _text_at(draw, value, center_x, y, width, font, color, max_lines=2):
    for i, line in enumerate(_wrap(draw, value, font, width, max_lines)):
        draw.text((round(center_x), y + i * (font.size + 12)), line, font=font, fill=color, anchor='mt')


def _mix(bg, fg, opacity):
    return tuple(round(int(bg[j:j + 2], 16) * (1 - opacity) + int(fg[j:j + 2], 16) * opacity) for j in (1, 3, 5))


def _hero_opacity(scene, frame):
    """Use the inclusive completed-line frame as the first reveal frame.

    Windows are [in,out): a one-frame hold has only out-1 available. It must
    display the filled hero there, rather than waiting for nonexistent out.
    """
    reveal_start = scene['in'] + scene['drawing_frames']
    reveal_frames = max(1, min(24, scene['out'] - reveal_start))
    return min(1, max(0, (frame - reveal_start + 1) / reveal_frames))


def _render(prepared, frame):
    from PIL import Image, ImageDraw
    _int(frame, 'frame', 0, prepared['frames'] - 1)
    bg, ink, accent, fill = prepared['palette']
    image = Image.new('RGB', SIZE, bg)
    draw = ImageDraw.Draw(image)
    _text_at(draw, prepared['title'], 960, 92, 1640, prepared['title_font'], ink)
    _text_at(draw, prepared['subtitle'], 960, 963, 1640, prepared['small_font'], ink)
    tip, active = None, None
    for i, scene in enumerate(prepared['scenes']):
        if frame < scene['in']:
            continue
        active = i
        progress = min(1, (frame - scene['in']) / scene['drawing_frames'])
        # Keep full first frame nonempty while leaving the pen exactly at its start.
        if progress == 0:
            progress = 0.00001
        points, tip = _partial(scene['path'], scene['lengths'], scene['lengths'][-1] * progress)
        age = max(0, frame - scene['out'])
        opacity = 1 - .64 * min(1, age / 24)
        line_color = _mix(bg, ink, opacity)
        is_final = i == len(prepared['scenes']) - 1
        if is_final and progress >= 1:
            hero = _hero_opacity(scene, frame)
            for polygon in scene['fills']:
                draw.polygon([(round(x), round(y)) for x, y in polygon], fill=_mix(bg, fill, hero * .9))
            line_color = ink
        if len(points) > 1:
            draw.line([(round(x), round(y)) for x, y in points], fill=line_color, width=5, joint='curve')
        # Label enters during the latter half of its semantic route, reaching full ink near completion.
        label_alpha = min(1, max(0, (progress - .55) / .3))
        if label_alpha:
            _text_at(draw, scene['label'], scene['center'][0], 795, 1540 / len(prepared['scenes']),
                     prepared['label_font'], _mix(bg, ink, label_alpha))
    if tip is not None:
        x, y = map(round, tip)
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=accent)
    # Route progress communicates the current chapter without replacing its image.
    for i in range(len(prepared['scenes'])):
        x = 140 + i * 34
        draw.line((x, 888, x + 20, 888), fill=accent if active is not None and i <= active else _mix(bg, ink, .2), width=3)
    return image


def _validate_text(prepared):
    from PIL import Image, ImageDraw
    draw = ImageDraw.Draw(Image.new('RGB', SIZE))
    _wrap(draw, prepared['title'], prepared['title_font'], 1640, 2)
    _wrap(draw, prepared['subtitle'], prepared['small_font'], 1640, 2)
    for scene in prepared['scenes']:
        _wrap(draw, scene['label'], prepared['label_font'], 1540 / len(prepared['scenes']), 2)
    font = prepared['small_font']
    missing = font.getmask('\U0010ffff')
    missing_key = (missing.size, bytes(missing))
    strings = [prepared['title'], prepared['subtitle']] + [scene['label'] for scene in prepared['scenes']]
    for char in set(''.join(strings)):
        if char.isspace():
            continue
        glyph = font.getmask(char)
        if (glyph.size, bytes(glyph)) == missing_key:
            raise ValueError(f'font lacks glyph U+{ord(char):04X}; provide a covering separately licensed font')


def validate_line_art_content(data, frames, font_path=None):
    """Validate without filesystem writes and return dependency/font metadata."""
    import PIL
    prepared = _prepare(data, frames, font_path)
    _validate_text(prepared)
    path = prepared['font_path']
    return {'frames': frames, 'fps': 30, 'size': list(SIZE),
            'renderer_dependencies': [str(Path(__file__).resolve())],
            'font_path': str(path), 'font_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'font_source': 'https://github.com/notofonts/noto-cjk' if font_path is None else 'user-supplied',
            'font_license': 'SIL OFL 1.1' if font_path is None else 'verify separately',
            'pillow_version': PIL.__version__,
            'semantic_objects': [scene['object'] for scene in prepared['scenes']]}


def render_line_art_frame(data, frame, frames, font_path=None):
    """Seek any frame independently; return one RGB1920x1080 PIL image."""
    prepared = _prepare(data, frames, font_path)
    _validate_text(prepared)
    return _render(prepared, frame)


def render_line_art_frames(data, frames, output, font_path=None):
    """Write f0000.png... and a receipt to a new directory, returning receipt.

    story=[{object,label,in,out,hold}], windows [in,out) must be continuous.
    drawing_speed is an integer 1..8 multiplier of each beat's drawing budget;
    faster drawing becomes additional hold, and never skips route geometry.
    """
    from PIL import Image, ImageDraw
    prepared = _prepare(data, frames, font_path)
    _validate_text(prepared)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    hashes = []
    for frame in range(frames):
        file = output / f'f{frame:04d}.png'
        _render(prepared, frame).save(file)
        hashes.append(hashlib.sha256(file.read_bytes()).hexdigest())
    receipt = {'frames': frames, 'fps': 30, 'size': list(SIZE), 'renderer': 'Pillow semantic arc-length line-art',
               'semantic_objects': [s['object'] for s in prepared['scenes']],
               'routes_continuous': all(a['path'][-1] == b['path'][0] for a, b in zip(prepared['scenes'], prepared['scenes'][1:])),
               'font_path': str(prepared['font_path']),
               'font_sha256': hashlib.sha256(prepared['font_path'].read_bytes()).hexdigest(),
               'font_source': 'https://github.com/notofonts/noto-cjk' if font_path is None else 'user-supplied',
               'font_license': 'SIL OFL 1.1' if font_path is None else 'verify separately',
               'frame_sha256': hashes,
               'input_sha256': hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
    (output / 'evidence.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return receipt
