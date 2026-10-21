"""Original CPU cel animation: held drawings, smear accents and seeded line boil.

Exposure notation means drawings held for 1/2/3 frames, not seconds. One sampled
cel frame controls the entire stage, so holds preserve actual output pixels.
Fonts are separately installed inputs: default Noto Sans CJK (SIL OFL 1.1),
https://github.com/notofonts/noto-cjk . No third-party artwork/font is bundled.
"""
from pathlib import Path
import hashlib
import json
import math
import re

SIZE = (1920, 1080)
SHAPES = ('blob', 'cat', 'star', 'sprout')
EMOTIONS = ('joy', 'curious', 'calm', 'surprise')
ACTIONS = ('pop', 'stretch', 'hop', 'settle')
PALETTE = ['#faf2df', '#2c302b', '#ef8a66', '#95b6a3']


def _int(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer in [{low},{high}]')
    return value


def _string(value, name, empty=False):
    if not isinstance(value, str) or len(value) > 160 or (not empty and not value.strip()):
        raise ValueError(f'{name} must be text of at most 160 characters')
    return value


def _noise(seed, frame, index):
    # Integer-addressed hash has no evolving random state, including negative seeds.
    value = (seed ^ (frame * 374761393) ^ (index * 668265263)) & 0xffffffff
    value = ((value ^ (value >> 13)) * 1274126177) & 0xffffffff
    return ((value ^ (value >> 16)) & 65535) / 32767.5 - 1


def _wrap(draw, value, font, width, max_lines):
    lines = ['']
    for char in value:
        if char == '\n' or (lines[-1] and draw.textlength(lines[-1] + char, font=font) > width):
            lines.append('')
        if char != '\n':
            lines[-1] += char
    return lines if len(lines) <= max_lines else None


def _fit(draw, value, font_path, face, width, maximum, minimum, max_lines):
    from PIL import ImageFont
    for size in range(maximum, minimum - 1, -2):
        font = ImageFont.truetype(str(font_path), size, index=face)
        lines = _wrap(draw, value, font, width, max_lines)
        if lines is not None:
            return font, lines
    raise ValueError('text still overflows at minimum font size; shorten the content')


def _prepare(data, frames, font_path=None):
    from PIL import Image, ImageDraw, ImageFont
    _int(frames, 'frames', 2, 18000)
    if not isinstance(data, dict):
        raise ValueError('cel data must be an object')
    title = _string(data.get('title'), 'title')
    subtitle = _string(data.get('subtitle', ''), 'subtitle', empty=True)
    seed = _int(data.get('seed', 1), 'seed', -(2 ** 31), 2 ** 31 - 1)
    palette = data.get('palette', PALETTE)
    if (not isinstance(palette, list) or len(palette) != 4
            or any(not isinstance(x, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', x) for x in palette)):
        raise ValueError('palette must contain four #RRGGBB colours')
    characters = data.get('characters')
    if not isinstance(characters, list) or not 2 <= len(characters) <= 4:
        raise ValueError('characters must contain 2..4 actors')
    cast, ids = [], set()
    for i, actor in enumerate(characters):
        if not isinstance(actor, dict):
            raise ValueError('each character must be an object')
        name = _string(actor.get('id'), 'character.id')
        if name in ids:
            raise ValueError('character ids must be unique')
        ids.add(name)
        if actor.get('shape') not in SHAPES or actor.get('emotion', 'joy') not in EMOTIONS:
            raise ValueError(f'shape must be in {SHAPES}; emotion must be in {EMOTIONS}')
        _string(actor.get('label'), 'character.label')
        size = _int(actor.get('size', 96), 'character.size', 60, 130)
        cast.append({**actor, 'emotion': actor.get('emotion', 'joy'), 'size': size,
                     'center': (160 + 1600 / len(characters) * (i + .5), 600), 'index': i})
    exposures = data.get('exposures', {'action': 1, 'body': 2, 'hold': 3})
    if not isinstance(exposures, dict) or set(exposures) != {'action', 'body', 'hold'}:
        raise ValueError('exposures must contain action, body and hold')
    for key, value in exposures.items():
        _int(value, f'exposures.{key}', 1, 3)
    beats = data.get('beats')
    if not isinstance(beats, list) or not 2 <= len(beats) <= 16:
        raise ValueError('beats must contain 2..16 action cues')
    cues, previous_out, offsets = [], 0, {name: 0 for name in ids}
    for beat in beats:
        if not isinstance(beat, dict) or beat.get('actor') not in ids or beat.get('action') not in ACTIONS:
            raise ValueError('each beat must name an existing actor and supported action')
        start = _int(beat.get('in'), 'beat.in', 0, frames - 2)
        end = _int(beat.get('out'), 'beat.out', start + 9, frames)
        if start != previous_out:
            raise ValueError('beat windows must be continuous and begin at frame zero')
        duration = end - start
        anticipation = _int(beat.get('anticipation', max(2, duration // 6)), 'anticipation', 2, duration - 7)
        motion = _int(beat.get('motion', max(4, duration // 3)), 'motion', 4, duration - anticipation - 3)
        # Exposure sampling must retain anticipation and an interior motion pose.
        # Merely accepting an analytic trajectory is insufficient: on threes a
        # four-frame hop samples only its two grounded endpoints.
        if anticipation < exposures['body'] + 1:
            raise ValueError('anticipation needs at least body exposure + 1 frames for visible preparation')
        if motion < 2 * exposures['action'] + 1:
            raise ValueError('motion needs at least 2 * action exposure + 1 frames for an interior action drawing')
        distance = _int(beat.get('distance', 48 if beat['action'] == 'hop' else 0), 'distance', -80, 80)
        if beat['action'] != 'hop' and distance:
            raise ValueError('distance applies only to hop actions')
        offset = offsets[beat['actor']]
        offsets[beat['actor']] += distance
        cues.append({**beat, 'anticipation': anticipation, 'motion': motion,
                     'distance': distance, 'offset': offset})
        previous_out = end
    for actor in cast:
        positions = [0] + [b['offset'] + b['distance'] for b in cues if b['actor'] == actor['id']]
        radius = actor['size'] * 1.65 + 35
        if min(actor['center'][0] + x - radius for x in positions) < 80 or max(actor['center'][0] + x + radius for x in positions) > 1840:
            raise ValueError('actor motion/smear exceeds stage safe margins; reduce size or hop distance')
    font = Path(font_path) if font_path else Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc')
    if not font.is_file():
        raise ValueError('provide a separately licensed local font covering all CJK content')
    face = 2 if font.name == 'NotoSansCJK-Bold.ttc' else 0
    draw = ImageDraw.Draw(Image.new('RGB', SIZE))
    title_style = _fit(draw, title, font, face, 1640, 76, 36, 2)
    subtitle_style = _fit(draw, subtitle, font, face, 1640, 32, 24, 1)
    for actor in cast:
        actor['text_style'] = _fit(draw, actor['label'], font, face, 1440 / len(cast), 36, 24, 3)
    glyph_font = ImageFont.truetype(str(font), 28, index=face)
    tofu = glyph_font.getmask('\U0010ffff')
    missing = (tofu.size, bytes(tofu))
    for char in set(title + subtitle + ''.join(actor['label'] for actor in cast)):
        if char.isspace():
            continue
        glyph = glyph_font.getmask(char)
        if (glyph.size, bytes(glyph)) == missing:
            raise ValueError(f'font lacks glyph U+{ord(char):04X}; supply a covering font')
    return {'title': title, 'subtitle': subtitle, 'cast': cast, 'beats': cues,
            'exposures': exposures, 'seed': seed, 'palette': palette, 'frames': frames,
            'font_path': font, 'title_style': title_style, 'subtitle_style': subtitle_style}


def _sample(prepared, frame):
    """Return a real held-drawing address and its exposure sheet region."""
    beat = next((b for b in prepared['beats'] if b['in'] <= frame < b['out']), None)
    if beat is None:
        start = prepared['beats'][-1]['out']
        kind = 'hold'
    elif frame < beat['in'] + beat['anticipation']:
        start, kind = beat['in'], 'body'
    elif frame < beat['in'] + beat['anticipation'] + beat['motion']:
        start, kind = beat['in'] + beat['anticipation'], 'action'
    else:
        start, kind = beat['in'] + beat['anticipation'] + beat['motion'], 'hold'
    exposure = prepared['exposures'][kind]
    return start + (frame - start) // exposure * exposure, kind, exposure


def _pose(prepared, actor, sample):
    relevant = [b for b in prepared['beats'] if b['actor'] == actor['id'] and b['in'] <= sample]
    pose = {'x': 0, 'y': 0, 'sx': 1, 'sy': 1, 'angle': 0, 'smear': False, 'fx': 0, 'tick': 0}
    if not relevant:
        return pose
    beat = relevant[-1]
    pose['x'] = beat['offset']
    elapsed = sample - beat['in']
    ant, motion = beat['anticipation'], beat['motion']
    if elapsed < ant:
        q = elapsed / max(1, ant - 1)
        if beat['action'] == 'pop':
            pose.update(sx=1 - .7 * q, sy=1 - .7 * q, y=actor['size'] * .45 * q, angle=-.08 * q)
        else:
            pose.update(sx=1 + .18 * q, sy=1 - .2 * q, y=actor['size'] * .18 * q, angle=-.08 * q)
        return pose
    tick = elapsed - ant
    q = min(1, tick / max(1, motion - 1))
    pose['tick'] = tick
    action = beat['action']
    if action == 'hop':
        pose['x'] += beat['distance'] * q
        pose['y'] = -110 * math.sin(math.pi * q)
        pose['sx'] = 1 - .12 * math.sin(math.pi * q)
        pose['sy'] = 1 + .18 * math.sin(math.pi * q)
        pose['angle'] = .08 * math.sin(q * math.tau)
    elif action == 'stretch':
        pose['sx'] = 1 - .25 * math.sin(math.pi * q)
        pose['sy'] = 1 + .55 * math.sin(math.pi * q)
        pose['y'] = -actor['size'] * .2 * math.sin(math.pi * q)
    elif action == 'pop':
        growth = .3 + .7 * min(1, q * 2.3) + .17 * math.sin(min(1, q * 1.6) * math.pi)
        pose['sx'] = growth * (1 + .08 * math.sin(q * math.tau))
        pose['sy'] = growth * (1 - .08 * math.sin(q * math.tau))
        pose['angle'] = .1 * math.sin(q * math.tau)
        pose['y'] = actor['size'] * .45 * (1 - min(1, q * 2.3))
    else:
        decay = (1 - q) ** 2
        pose['sx'] = 1 + .2 * math.cos(q * math.pi * 3) * decay
        pose['sy'] = 1 - .18 * math.cos(q * math.pi * 3) * decay
        pose['angle'] = .1 * math.sin(q * math.pi * 3) * decay
    if 2 <= tick <= 3 and action != 'settle':
        pose['smear'] = True
        pose['sx'] *= 1.35 if action == 'hop' else .82
        pose['sy'] *= .84 if action == 'hop' else 1.3
    landing = motion - 1
    if landing <= tick < landing + 8:
        pose['fx'] = 1 - (tick - landing) / 8
    if tick >= motion:
        # The held rest frame returns to a settled design; no state carries over.
        pose.update(sx=1, sy=1, y=0, angle=0, smear=False)
    return pose


def _silhouette(shape):
    if shape == 'blob':
        return [(math.cos(i / 64 * math.tau) * (1 + .06 * math.sin(i / 64 * math.tau * 3)),
                 math.sin(i / 64 * math.tau) * .88) for i in range(64)]
    if shape == 'star':
        return [(math.cos(-math.pi / 2 + i / 10 * math.tau) * (1 if i % 2 == 0 else .49),
                 math.sin(-math.pi / 2 + i / 10 * math.tau) * (1 if i % 2 == 0 else .49)) for i in range(10)]
    if shape == 'cat':
        vertices = [(-.8,.6),(-1,0),(-.8,-.5),(-.75,-1),(-.2,-.7),(.2,-.7),(.75,-1),(.8,-.5),(1,0),(.8,.6),(.4,.85),(-.4,.85)]
    else:
        vertices = [(-.6,.7),(-.75,.2),(-.48,-.15),(-.06,-.1),(-.12,-.55),(-.65,-.55),(-.85,-.95),(-.28,-.95),(0,-.65),(.28,-.95),(.85,-.95),(.65,-.55),(.12,-.55),(.06,-.1),(.48,-.15),(.75,.2),(.6,.7)]
    points = []
    for a, b in zip(vertices, vertices[1:] + vertices[:1]):
        for i in range(5):
            q = i / 5
            points.append((a[0] + q * (b[0] - a[0]), a[1] + q * (b[1] - a[1])))
    return points


def _draw_actor(draw, actor, pose, seed, sample, palette):
    bg, ink, accent, second = palette
    cx = actor['center'][0] + pose['x']
    cy = actor['center'][1] + pose['y']
    size = actor['size']
    def pt(x, y):
        x, y = x * size * pose['sx'], y * size * pose['sy']
        a = pose['angle']
        return (round(cx + x * math.cos(a) - y * math.sin(a)), round(cy + x * math.sin(a) + y * math.cos(a)))
    points = []
    for i, (x, y) in enumerate(_silhouette(actor['shape'])):
        boil = _noise(seed + actor['index'] * 79, sample, i) * 1.6 / size
        points.append(pt(x + boil, y + boil * .7))
    color = accent if actor['index'] % 2 == 0 else second
    draw.polygon(points, fill=color)
    draw.line(points + [points[0]], fill=ink, width=5, joint='curve')
    # A second imperfect contour is a drawing mark, not stochastic frame noise.
    sketch = [(x + round(_noise(seed, sample, i + 200) * 2), y + 2) for i, (x, y) in enumerate(points)]
    draw.line(sketch + [sketch[0]], fill=ink, width=1)
    emotion = actor['emotion']
    for side in [-1, 1]:
        ex, ey = pt(side * .25, -.14)
        if emotion in ('joy', 'calm'):
            draw.line([pt(side * .25 - .09, -.12), pt(side * .25, -.18 if emotion == 'joy' else -.08), pt(side * .25 + .09, -.12)], fill=ink, width=4)
        else:
            r = 6 if emotion == 'surprise' else (7 if side == 1 else 4)
            draw.ellipse((ex-r,ey-r,ex+r,ey+r), fill=ink)
    if emotion == 'surprise':
        mx, my = pt(0, .22)
        draw.ellipse((mx-9,my-13,mx+9,my+13), outline=ink, width=4)
    else:
        draw.line([pt(-.13,.17),pt(0,.26 if emotion == 'joy' else .18),pt(.13,.17)], fill=ink, width=4)
    if pose['smear']:
        # Two deliberately drawn smear cels: elongated silhouette and short hatch trails.
        for i in range(3):
            x, y = pt(-1.2 - i * .13, -.3 + i * .3)
            draw.line((x-24-i*7,y,x-3,y-3), fill=ink, width=3)
    if pose['fx']:
        for i in range(6):
            angle = i / 6 * math.tau
            r = size * (1.12 + .22 * (1 - pose['fx']))
            x, y = cx + math.cos(angle) * r, cy + math.sin(angle) * r
            draw.line((round(x),round(y),round(x+math.cos(angle)*18*pose['fx']),round(y+math.sin(angle)*18*pose['fx'])), fill=ink, width=3)


def _draw_text(draw, style, center, y, color):
    font, lines = style
    for i, text in enumerate(lines):
        draw.text((round(center), y + i * (font.size + 10)), text, font=font, fill=color, anchor='mt')


def _render(prepared, frame):
    from PIL import Image, ImageDraw
    _int(frame, 'frame', 0, prepared['frames'] - 1)
    sample, kind, exposure = _sample(prepared, frame)
    bg, ink, accent, second = prepared['palette']
    image = Image.new('RGB', SIZE, bg)
    draw = ImageDraw.Draw(image)
    # Static paper flecks never change during a held drawing.
    for i in range(380):
        x = (_noise(prepared['seed'], 0, i) + 1) * 940 + 20
        y = (_noise(prepared['seed'], 1, i) + 1) * 500 + 40
        draw.point((round(x), round(y)), fill='#ddd5c5')
    _draw_text(draw, prepared['title_style'], 960, 90, ink)
    _draw_text(draw, prepared['subtitle_style'], 960, 988, ink)
    for actor in prepared['cast']:
        pose = _pose(prepared, actor, sample)
        _draw_actor(draw, actor, pose, prepared['seed'], sample, prepared['palette'])
        _draw_text(draw, actor['text_style'], actor['center'][0], 805, ink)
        x = round(actor['center'][0])
        draw.line((x-40,755,x+40,755), fill=ink, width=2)
    return image


def validate_cel_content(data, frames, font_path=None):
    """Pure preflight: reject unsafe content before creating output files."""
    import PIL
    prepared = _prepare(data, frames, font_path)
    font = prepared['font_path']
    return {'frames':frames,'fps':30,'size':list(SIZE),'pillow_version':PIL.__version__,
            'renderer_dependencies':[str(Path(__file__).resolve())],
            'font_path':str(font),'font_sha256':hashlib.sha256(font.read_bytes()).hexdigest(),
            'font_source':'https://github.com/notofonts/noto-cjk' if font_path is None else 'user-supplied',
            'font_license':'SIL OFL 1.1' if font_path is None else 'verify separately',
            'exposures':prepared['exposures'],'characters':[actor['shape'] for actor in prepared['cast']]}


def render_cel_frame(data, frame, frames, font_path=None):
    """Return one deterministic RGB1920x1080 PIL image, in any seek order."""
    return _render(_prepare(data, frames, font_path), frame)


def render_cel_frames(data, frames, output, font_path=None):
    """Write f0000.png... and evidence.json to a new directory; return receipt."""
    metadata = validate_cel_content(data, frames, font_path)
    prepared = _prepare(data, frames, font_path)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    hashes = []
    for frame in range(frames):
        path = output / f'f{frame:04d}.png'
        _render(prepared, frame).save(path)
        hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
    receipt = {**metadata,'frame_sha256':hashes,
               'input_sha256':hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
               'renderer_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (output/'evidence.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return receipt
