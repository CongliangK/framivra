"""Deterministic reaction-caption PNG layers for a real video background.

Pillow is optional until this renderer is selected. Cue windows are [in, out).
Caption bands reserve an explicit normalized subject rectangle.
"""
from pathlib import Path
import hashlib
import math

EMOTIONS = {
    'surprise': ('#fff34a', '!?'),
    'question': ('#a8ecff', '?'),
    'joy': ('#ffafcf', '♪'),
    'emphasis': ('#ffffff', '!'),
}


def _font_path(font_path):
    if font_path is not None:
        path = Path(font_path)
        if not path.is_file():
            raise ValueError('font_path must name an existing font file')
        return path
    candidates = (
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    )
    for name in candidates:
        if Path(name).is_file():
            return Path(name)
    raise ValueError('provide a local font_path; no bundled font is installed')


def _wrap(draw, text, font, max_width):
    lines = ['']
    for char in text:
        if char == '\n':
            lines.append('')
        elif draw.textlength(lines[-1] + char, font=font) > max_width and lines[-1]:
            lines.append(char)
        else:
            lines[-1] += char
    return lines


def _prepare(content, reactions, frames, width=1920,
             height=1080, font_path=None):
    """Validate cues and prepare font-dependent caption artwork.

    content is the same content mapping as build.py (title is optional here).
    Each reaction has integer in/out, text, and one emotion from EMOTIONS.
    Overlapping cues are rejected rather than silently masking each other.
    output must be new; text is fitted inside the lower caption zone.
    """
    from PIL import Image, ImageDraw, ImageFont
    if not isinstance(content, dict):
        raise ValueError('content must be an object')
    if type(frames) is not int or not 1 <= frames <= 18000:
        raise ValueError('frames must be an integer in [1,18000]')
    if any(type(n) is not int or n < 320 or n > 7680 for n in (width, height)):
        raise ValueError('width and height must be integers in [320,7680]')
    band = content.get('caption_band', 'lower')
    if band not in ('upper', 'lower'):
        raise ValueError('caption_band must be upper or lower')
    rect = content.get('subject_rect', [.3, .6, .7, .95] if band == 'upper' else [.3, .2, .7, .72])
    if (not isinstance(rect, (list, tuple)) or len(rect) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in rect)
            or not 0 <= rect[0] < rect[2] <= 1
            or not 0 <= rect[1] < rect[3] <= 1):
        raise ValueError('subject_rect must be normalized [left,top,right,bottom]')
    band_top, band_bottom = (.27, .5) if band == 'upper' else (.75, 1)
    if rect[0] < .97 and rect[2] > .03 and rect[1] < band_bottom and rect[3] > band_top:
        raise ValueError('subject_rect intersects the caption band')
    if not isinstance(reactions, list):
        raise ValueError('reactions must be an array')
    cues = []
    for cue in reactions:
        if (not isinstance(cue, dict) or not {'in', 'out', 'text', 'emotion'} <= set(cue)
                or set(cue) - {'in', 'out', 'text', 'emotion', 'punchFrame', 'stagger'}):
            raise ValueError('reaction requires in, out, text, emotion; optional punchFrame')
        start, end = cue['in'], cue['out']
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= frames:
            raise ValueError('cue window must use integer frames within [0,frames)')
        if not isinstance(cue['text'], str) or not cue['text'].strip() or len(cue['text']) > 120:
            raise ValueError('reaction text must contain 1 to 120 characters')
        stagger = cue.get('stagger', 0)
        if type(stagger) is not int or not 0 <= stagger <= 5:
            raise ValueError('stagger must be integer frames in [0,5]')
        if cue['text'].count('\n') > 2:
            raise ValueError('reaction text supports at most three explicit lines')
        punch = cue.get('punchFrame', start)
        if type(punch) is not int or not start <= punch < end:
            raise ValueError('punchFrame must be an integer inside its cue window')
        if not isinstance(cue['emotion'], str) or cue['emotion'] not in EMOTIONS:
            raise ValueError('unsupported emotion')
        cues.append(dict(cue))
    cues.sort(key=lambda cue: cue['in'])
    if any(a['out'] > b['in'] for a, b in zip(cues, cues[1:])):
        raise ValueError('reaction cue windows must not overlap')
    font_file = _font_path(font_path)
    # DejaVu cannot support Chinese. Explicit failure is preferable to tofu glyphs.
    if 'DejaVu' in font_file.name and any(ord(ch) > 0x2fff for c in cues for ch in c['text']):
        raise ValueError('CJK reactions require a CJK-capable local font_path')
    zone_w, zone_h = round(width * .9), round(height * .22)
    base_size = max(18, round(height * .075))
    layers = []
    for cue in cues:
        color, symbol = EMOTIONS[cue['emotion']]
        text = symbol + '  ' + cue['text']
        probe = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
        fitted = None
        for size in range(base_size, 11, -1):
            font = ImageFont.truetype(str(font_file), size)
            lines = _wrap(probe, text, font, zone_w * .88)
            if len(lines) > 3:
                continue
            line_h = round(size * 1.5)
            raw = []
            for row, line in enumerate(lines):
                cursor = 0
                for char in line:
                    # Bound the independent glyph, including outer stroke and offset
                    # shadow, before allocating its bitmap. Negative bearings survive.
                    strokes = ((5, 8, max(2, size // 11), '#111111', '#111111'),
                               (0, 0, max(2, size // 12), color, '#171923'),
                               (0, 0, max(1, size // 40), color, '#ffffff'))
                    boxes = [probe.textbbox((dx, dy), char, font=font,
                                            stroke_width=sw, anchor='lt')
                             for dx, dy, sw, _, _ in strokes]
                    left = min(box[0] for box in boxes)
                    top = min(box[1] for box in boxes)
                    right = max(box[2] for box in boxes)
                    bottom = max(box[3] for box in boxes)
                    glyph = Image.new('RGBA', (max(1, right-left+4), max(1, bottom-top+4)))
                    gd = ImageDraw.Draw(glyph)
                    for dx, dy, sw, fill, stroke in strokes:
                        gd.text((dx-left+2, dy-top+2), char, font=font,
                                fill=fill, stroke_width=sw, stroke_fill=stroke, anchor='lt')
                    box = glyph.getbbox()
                    if box and (box[0] <= 0 or box[1] <= 0 or box[2] >= glyph.width or box[3] >= glyph.height):
                        raise ValueError('independent caption glyph would be clipped')
                    raw.append((glyph, round(cursor)+left-2, row*line_h+top-2))
                    cursor += probe.textlength(char, font=font)
            left = min(x for _, x, _ in raw)
            top = min(y for _, _, y in raw)
            right = max(x+g.width for g, x, _ in raw)
            bottom = max(y+g.height for g, _, y in raw)
            # Reserve downward reveal motion, all bitmap padding, and outer margins.
            if right-left <= zone_w-32 and bottom-top+12 <= zone_h-16:
                fitted = raw, left, top, right, bottom
                break
        if fitted is None:
            raise ValueError('reaction text does not fit caption zone at minimum font size 12')
        raw, left, top, right, bottom = fitted
        dx = (zone_w-(right-left))//2-left
        dy = (zone_h-(bottom-top+12))//2-top
        glyphs = [(g, x+dx, y+dy) for g, x, y in raw]
        count = len(glyphs)  # Includes the actual emotion prefix and two spaces.
        reveal_end = (count-1)*cue.get('stagger', 0)+3 if cue.get('stagger', 0) else 0
        minimum = max(8, reveal_end)+6
        if cue['out']-cue['in'] < minimum:
            raise ValueError(f'cue needs at least {minimum} frames for prepared glyph reveal, attack and six readable frames')
        layer = Image.new('RGBA', (zone_w, zone_h), (0, 0, 0, 0))
        for glyph, gx, gy in glyphs:
            if gx < 0 or gy < 0 or gx+glyph.width > zone_w or gy+glyph.height+12 > zone_h:
                raise ValueError('prepared caption glyph exceeds caption bounds')
            layer.alpha_composite(glyph, (gx, gy))
        layers.append((cue, layer, glyphs))
    return cues, font_file, layers, band, rect


def validate_caption_content(content, reactions, frames, width=1920,
                             height=1080, font_path=None):
    """Validate without writing files; return lightweight preflight metadata.

    Reactions: integer [in,out), text (up to three explicit lines), emotion,
    optional punchFrame and stagger (0..5). Prepared glyphs include the emotion
    symbol and two spaces. Every cue reserves eight attack frames and at least
    six fully revealed frames; longer editorial reading holds remain the caller's
    responsibility. Unfittable text and clipped glyphs are rejected before output.
    """
    cues, font, layers, band, rect = _prepare(content, reactions, frames, width, height, font_path)
    return {'frames': frames, 'width': width, 'height': height,
            'font_path': str(font), 'caption_band': band, 'protected_subject': list(rect),
            'cues': [{'in': cue['in'], 'out': cue['out'], 'glyph_count': len(glyphs),
                      'minimum_frames': max(8, (len(glyphs)-1)*cue.get('stagger',0)+3
                                            if cue.get('stagger',0) else 0)+6}
                     for cue, _, glyphs in layers]}


def render_caption_frame(content, reactions, frame, frames, width=1920,
                         height=1080, font_path=None):
    """Return one RGBA Pillow image, independent of all prior frame requests."""
    from PIL import Image
    if type(frame) is not int or not 0 <= frame < frames:
        raise ValueError('frame must be an integer in [0,frames)')
    _, _, layers, band, _ = _prepare(content, reactions, frames, width, height, font_path)
    return _draw_frame(frame, width, height, layers, Image, band)


def _draw_frame(frame, width, height, layers, Image, band='lower'):
    image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    for cue, layer, glyphs in layers:
        if cue['in'] <= frame < cue['out']:
            local = frame - cue['in']
            stagger = cue.get('stagger', 0)
            if stagger:
                revealed = Image.new('RGBA', layer.size, (0,0,0,0))
                for index, (glyph, gx, gy) in enumerate(glyphs):
                    amount = max(0, min(1, (local-index*stagger+1)/4))
                    if amount:
                        cell = glyph.copy()
                        if amount < 1:
                            cell.putalpha(cell.getchannel('A').point(lambda alpha: round(alpha*amount)))
                        revealed.alpha_composite(cell, (gx, gy+round(12*(1-amount))))
                layer = revealed
            attack = min(1, local / 8)
            scale = .88 + .12 * attack + .08 * math.sin(attack * math.pi)
            # The semantic impact frame is absolute within the source video.
            punch = cue.get('punchFrame', cue['in'])
            scale += .08 * max(0, 1 - abs(frame-punch)/5)
            scale = min(1.04, scale)
            w, h = round(layer.width * scale), round(layer.height * scale)
            resized = layer.resize((w, h), Image.Resampling.LANCZOS)
            image.alpha_composite(resized, ((width-w)//2, round(height*(.27 if band == 'upper' else .75))))
            break
    return image


def render_caption_frames(content, reactions, frames, output, width=1920,
                          height=1080, font_path=None):
    """Write new f0000.png layers; return cue and local font provenance."""
    from PIL import Image
    cues, font_file, layers, band, rect = _prepare(content, reactions, frames, width, height, font_path)
    output = Path(output)
    if output.exists():
        raise ValueError('output must be a new directory')
    output.mkdir(parents=True)
    nonempty = 0
    for frame in range(frames):
        image = _draw_frame(frame, width, height, layers, Image, band)
        nonempty += image.getbbox() is not None
        image.save(output / f'f{frame:04d}.png', optimize=False)
    return {'frames': frames, 'caption_frames': nonempty, 'width': width,
            'height': height, 'font_path': str(font_file),
            'font_sha256': hashlib.sha256(font_file.read_bytes()).hexdigest(),
            'reactions': cues, 'protected_subject': list(rect), 'caption_band': band}
