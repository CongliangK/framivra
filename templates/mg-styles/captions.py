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
        if stagger and (len(cue['text']) + 2) * stagger + 10 > end-start:
            raise ValueError('cue is too short for stagger reveal plus six readable frames')
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
        layer = Image.new('RGBA', (zone_w, zone_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        color, symbol = EMOTIONS[cue['emotion']]
        text = symbol + '  ' + cue['text']
        size = base_size
        while True:
            font = ImageFont.truetype(str(font_file), size)
            lines = _wrap(draw, text, font, zone_w * .88)
            line_h = round(size * 1.32)
            if len(lines) * line_h < zone_h * .82 or size <= 12:
                break
            size -= 1
        # Thick dark outline, white inner outline, offset shadow: classic reaction type.
        y = (zone_h - len(lines) * line_h) // 2
        glyphs = []
        for line in lines:
            x = (zone_w - draw.textlength(line, font=font)) // 2
            draw.text((x + 5, y + 8), line, font=font, fill='#111111',
                      stroke_width=max(2, size // 11), stroke_fill='#111111')
            draw.text((x, y), line, font=font, fill=color,
                      stroke_width=max(2, size // 12), stroke_fill='#171923')
            draw.text((x, y), line, font=font, fill=color,
                      stroke_width=max(1, size // 40), stroke_fill='#ffffff')
            cursor = x
            for char in line:
                advance = draw.textlength(char, font=font)
                glyph = Image.new('RGBA', (max(1, math.ceil(advance)+32), line_h+32), (0,0,0,0))
                gd = ImageDraw.Draw(glyph)
                gd.text((21,24), char, font=font, fill='#111111', stroke_width=max(2,size//11), stroke_fill='#111111')
                gd.text((16,16), char, font=font, fill=color, stroke_width=max(2,size//12), stroke_fill='#171923')
                gd.text((16,16), char, font=font, fill=color, stroke_width=max(1,size//40), stroke_fill='#ffffff')
                glyphs.append((glyph, round(cursor-16), y-16))
                cursor += advance
            y += line_h
        layers.append((cue, layer, glyphs))
    return cues, font_file, layers, band, rect


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
            attack = min(1, local / max(1, min(8, (cue['out']-cue['in']) / 3)))
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
