"""Original CPU pixel-art quest renderer; integer frames and a 320x180 lattice.

No borrowed sprites/assets. Font files are external inputs, never redistributed.
Default Noto Sans CJK: https://github.com/notofonts/noto-cjk (SIL OFL 1.1).
Local licence: /usr/share/doc/fonts-noto-cjk/copyright. The rendered, thresholded
lettering is artwork; it does not bundle or modify the font software.
"""
from pathlib import Path
import hashlib
import json
import math
import re

W, H, SCALE, GROUND = 320, 180, 6, 139
OBSTACLES = ('jump', 'gap', 'duck', 'collect')
DEFAULT_PALETTE = ['#131a31', '#f9edcc', '#f59652', '#71c6ad']
FONT_SOURCE = 'https://github.com/notofonts/noto-cjk'


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer in [{low},{high}]')
    return value


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise ValueError(f'{name} must be nonempty text of at most 160 characters')
    return value


def _prepare(data, frames, font_path):
    from PIL import ImageFont
    _integer(frames, 'frames', 2, 18000)
    if not isinstance(data, dict):
        raise ValueError('pixel data must be an object')
    mission = _text(data.get('mission'), 'mission')
    steps = data.get('steps')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 24:
        raise ValueError('steps must contain 1..24 quest obstacles')
    previous_out = 0
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError('each step must be an object')
        _text(step.get('label'), f'steps[{i}].label')
        start = _integer(step.get('in'), 'step.in', 0, frames - 2)
        end = _integer(step.get('out'), 'step.out', start + 2, frames - 1)
        if start < previous_out:
            raise ValueError('step windows must be ordered and nonoverlapping')
        if step.get('obstacle') not in OBSTACLES:
            raise ValueError(f'obstacle must be one of {OBSTACLES}')
        previous_out = end
    reward = data.get('reward')
    if not isinstance(reward, dict):
        raise ValueError('reward must contain text and an integer in frame')
    _text(reward.get('text'), 'reward.text')
    _integer(reward.get('in'), 'reward.in', previous_out, frames - 1)
    palette = data.get('palette', DEFAULT_PALETTE)
    if (not isinstance(palette, list) or len(palette) != 4
            or any(not isinstance(x, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', x) for x in palette)):
        raise ValueError('palette must contain four #RRGGBB colours')
    seed = _integer(data.get('seed', 1), 'seed', 0, 2147483647)
    path = Path(font_path) if font_path else Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc')
    if not path.is_file():
        raise ValueError('provide a separately licensed local CJK font_path')
    # Noto TTC index 2 is SC; externally supplied single fonts use their default face.
    face = 2 if path.name == 'NotoSansCJK-Bold.ttc' else 0
    return {'mission': mission, 'steps': steps, 'reward': reward, 'palette': palette,
            'seed': seed, 'font': ImageFont.truetype(str(path), 12, index=face),
            'font_path': path, 'frames': frames}


def _letters(image, text, xy, color, font, max_width, max_lines=2):
    """Rasterize once on the native grid, then threshold every text pixel."""
    from PIL import Image, ImageDraw
    mask = Image.new('L', (W, H))
    d = ImageDraw.Draw(mask)
    lines = ['']
    for char in text:
        if char == '\n' or (lines[-1] and d.textlength(lines[-1] + char, font=font) > max_width):
            lines.append('')
        if char != '\n':
            lines[-1] += char
    if len(lines) > max_lines:
        raise ValueError('text does not fit native pixel caption region')
    for i, line in enumerate(lines):
        d.text((int(xy[0]), int(xy[1]) + i * 13), line, font=font, fill=255, stroke_width=0, anchor='lt')
    mask = mask.point(lambda v: 255 if v >= 128 else 0)
    image.paste(color, (0, 0), mask)


def _sprite(d, x, y, colors, frame, crouch=False):
    # Original 8x12 bitmap; two integer-position walk poses and a duck pose.
    bitmap = ['00111100', '01111110', '11011011', '11111111', '00111100',
              '01111110', '11111111', '10111101', '00111100', '00100100',
              '01100110', '11000011']
    if crouch:
        bitmap = bitmap[:6] + ['11111111', '11000011']
    for row, bits in enumerate(bitmap):
        for col, bit in enumerate(bits):
            if bit == '1':
                xx = x + col + (1 if row >= 9 and frame // 3 % 2 else 0)
                d.rectangle((xx, y + row, xx, y + row), fill=colors[2] if row < 4 else colors[1])


def _render(prepared, frame):
    from PIL import Image, ImageDraw
    _integer(frame, 'frame', 0, prepared['frames'] - 1)
    steps, reward, palette, seed = (prepared[k] for k in ('steps', 'reward', 'palette', 'seed'))
    bg, ink, accent, second = palette
    # Resolve world position directly from the requested frame, including gaps.
    active, u, world_x = None, 0, 28
    completed = 0
    for i, step in enumerate(steps):
        if frame >= step['out']:
            world_x, completed = 28 + (i + 1) * 180, i + 1
        elif frame >= step['in']:
            active = i
            u = (frame - step['in']) / (step['out'] - step['in'] - 1)
            world_x = 28 + i * 180 + round(180 * u)
            break
        else:
            break
    reward_on = frame >= reward['in']
    if reward_on:
        world_x += min(50, (frame - reward['in']) * 3)
    camera = max(0, world_x - 75)
    image = Image.new('RGB', (W, H), bg)
    d = ImageDraw.Draw(image)
    # Parallax uses integer sample addresses and the seed only; no stateful RNG.
    for i in range(37):
        x = (i * 53 + seed * 7 - camera // 4) % W
        y = 38 + (i * 31 + seed) % 69
        d.point((x, y), fill=ink)
    for i in range(12):
        x = i * 47 - camera // 2 % 47
        height = 9 + (i * 11 + seed) % 31
        d.rectangle((x, GROUND - height, x + 35, GROUND), fill=second)
    d.rectangle((0, GROUND, W - 1, H - 1), fill=ink)
    for x in range(-camera % 8, W, 8):
        d.rectangle((x, GROUND + 4, x + 3, GROUND + 5), fill=bg)
    for i, step in enumerate(steps):
        obstacle_x = 118 + i * 180 - camera
        if obstacle_x < -40 or obstacle_x > W + 40:
            continue
        kind = step['obstacle']
        if kind == 'gap':
            d.rectangle((obstacle_x - 17, GROUND, obstacle_x + 18, H - 1), fill=bg)
            for dx in range(-13, 17, 8):
                d.polygon([(obstacle_x + dx, 170), (obstacle_x + dx + 3, 163), (obstacle_x + dx + 6, 170)], fill=accent)
        elif kind == 'jump':
            d.rectangle((obstacle_x - 7, GROUND - 13, obstacle_x + 7, GROUND - 1), fill=accent)
            d.rectangle((obstacle_x - 4, GROUND - 11, obstacle_x + 4, GROUND - 9), fill=ink)
        elif kind == 'duck':
            d.rectangle((obstacle_x - 15, GROUND - 25, obstacle_x + 17, GROUND - 10), fill=accent)
            d.rectangle((obstacle_x + 15, GROUND - 25, obstacle_x + 17, GROUND - 1), fill=accent)
        elif frame < step['out']:
            d.polygon([(obstacle_x, 112), (obstacle_x + 4, 118), (obstacle_x, 124), (obstacle_x - 4, 118)], fill=accent)
        # Numeric checkpoints are grid text, separate from narrative subtitles.
        _letters(image, str(i + 1), (obstacle_x - 3, 92), ink, prepared['font'], 20, 1)
    crouch = active is not None and steps[active]['obstacle'] == 'duck' and .22 < u < .82
    jump = 0
    if active is not None and steps[active]['obstacle'] in ('jump', 'gap', 'collect'):
        jump = round(math.sin(math.pi * u) * (35 if steps[active]['obstacle'] == 'gap' else 24))
    player_x, player_y = world_x - camera, GROUND - (8 if crouch else 12) - jump
    _sprite(d, player_x, player_y, palette, frame, crouch)
    # Independent progress UI and active-action text respond to cue semantics.
    d.rectangle((0, 0, W - 1, 31), fill=bg)
    _letters(image, prepared['mission'], (8, 2), ink, prepared['font'], 300, 2)
    if not reward_on:
        label = steps[active]['label'] if active is not None else (steps[min(completed, len(steps) - 1)]['label'])
        d.rectangle((7, 151, 312, 178), fill=bg)
        _letters(image, label, (12, 152), ink, prepared['font'], 292, 2)
    for i in range(len(steps)):
        x = 8 + i * 12
        d.rectangle((x, 34, x + 7, 36), fill=accent if i < completed else second)
    # Final scene is a reward reveal, not a new label on the obstacle scene.
    if reward_on:
        d.rectangle((25, 49, 294, 126), fill=ink)
        d.rectangle((28, 52, 291, 123), fill=bg)
        pulse = (frame - reward['in']) // 4 % 2
        for sx in (42, 275):
            d.line((sx - 4 - pulse, 77, sx + 4 + pulse, 77), fill=accent, width=1)
            d.line((sx, 73 - pulse, sx, 81 + pulse), fill=accent, width=1)
        _letters(image, reward['text'], (53, 66), ink, prepared['font'], 211, 3)
        d.rectangle((141, 108, 179, 114), fill=accent)
        d.rectangle((147, 101, 173, 108), fill=accent)
    return image.resize((1920, 1080), Image.Resampling.NEAREST)


def render_pixel_frame(data, frame, frames, font_path=None):
    """Return one RGB PIL image; seek order never changes the image."""
    return _render(_prepare(data, frames, font_path), frame)


def render_pixel_frames(data, frames, output, font_path=None):
    """Write f0000.png... and evidence.json into a new output directory.

    Schema: mission, steps[{label,in,out,obstacle}], reward{text,in}, palette,
    seed. Obstacles are jump/gap/duck/collect; windows use [in,out).
    """
    prepared = _prepare(data, frames, font_path)
    # Validate every caption's native-grid fit before creating any output.
    from PIL import Image
    scratch = Image.new('RGB', (W, H))
    _letters(scratch, prepared['mission'], (8, 2), '#ffffff', prepared['font'], 300, 2)
    for step in prepared['steps']:
        _letters(scratch, step['label'], (12, 152), '#ffffff', prepared['font'], 292, 2)
    _letters(scratch, prepared['reward']['text'], (53, 66), '#ffffff', prepared['font'], 211, 3)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for frame in range(frames):
        _render(prepared, frame).save(output / f'f{frame:04d}.png')
    font_bytes = prepared['font_path'].read_bytes()
    report = {'frames': frames, 'fps': 30, 'size': [1920, 1080], 'native_grid': [W, H],
              'scale': SCALE, 'sampling': 'nearest', 'text_threshold': 128,
              'font': str(prepared['font_path']), 'font_sha256': hashlib.sha256(font_bytes).hexdigest(),
              'font_source': FONT_SOURCE if font_path is None else 'user-supplied; verify its licence separately',
              'font_license': 'SIL OFL 1.1' if font_path is None else 'not asserted for user-supplied font',
              'external_assets': [], 'input_sha256': hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
    (output / 'evidence.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report
