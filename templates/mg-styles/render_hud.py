"""Original CPU HUD: a semantic search/detect/confirm tracker, not measured telemetry.

Input: title, subtitle, target{name,points[{frame,x,y}]},
stages[{mode,in,out,text}], telemetry[{label,value,unit}], optional palette/seed.
Stage windows are contiguous [in,out); x/y are normalized full-frame coordinates
inside the central safe area x=.30..70, y=.32..72. All state is frame-derived.
Pillow and a separately licensed local font are required; no fonts are bundled.
"""
from pathlib import Path
import hashlib
import json
import math
import re

SIZE = (1920, 1080)
MODES = ('search', 'detect', 'confirm')
DEFAULT_PALETTE = ['#08171c', '#dcffff', '#40d9d4', '#ffac68']


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer in [{low},{high}]')
    return value


def _text(value, name, limit=120, empty=False):
    if (not isinstance(value, str) or len(value) > limit or '\n' in value or '\r' in value
            or not empty and not value.strip()):
        raise ValueError(f'{name} must be single-line text, at most {limit} characters')
    return value


def _font_file(strings, font_path):
    candidates = [Path(font_path)] if font_path is not None else [
        Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'),
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    from PIL import ImageFont
    for path in candidates:
        if path.is_file():
            face = 2 if path.name == 'NotoSansCJK-Bold.ttc' else 0
            font = ImageFont.truetype(str(path), 40, index=face)
            missing = font.getmask(chr(0x10ffff))
            missing_key = (missing.size, bytes(missing))
            for char in set(''.join(strings)):
                if not char.isspace():
                    glyph = font.getmask(char)
                    if (glyph.size, bytes(glyph)) == missing_key:
                        raise ValueError(f'font lacks required glyph U+{ord(char):04X}')
            return path, face
    raise ValueError('provide an existing separately licensed local font_path')


def _prepare(data, frames, font_path):
    from PIL import Image, ImageDraw, ImageFont
    _integer(frames, 'frames', 60, 18000)
    if not isinstance(data, dict) or set(data)-{'title','subtitle','target','stages','telemetry','palette','seed'}:
        raise ValueError('HUD content has unsupported fields')
    title = _text(data.get('title'), 'title')
    subtitle = _text(data.get('subtitle',''), 'subtitle', empty=True)
    target = data.get('target')
    if not isinstance(target,dict) or set(target)!={'name','points'}:
        raise ValueError('target requires name and points')
    _text(target['name'], 'target.name', 60)
    points = target['points']
    if not isinstance(points,list) or not 2 <= len(points) <= 40:
        raise ValueError('target.points requires 2..40 timed coordinates')
    previous = -1
    for point in points:
        if not isinstance(point,dict) or set(point)!={'frame','x','y'}:
            raise ValueError('each target point requires frame, x, y')
        frame = _integer(point['frame'], 'point.frame', 0, frames-1)
        if frame <= previous:
            raise ValueError('trajectory frame numbers must increase')
        previous = frame
        for key, low, high in (('x',.30,.70),('y',.32,.72)):
            value = point[key]
            if type(value) not in (int,float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f'target {key} must be finite normalized coordinates in [{low},{high}]')
    if points[0]['frame'] != 0 or points[-1]['frame'] != frames-1:
        raise ValueError('trajectory must include frame zero and the final frame')
    stages = data.get('stages')
    if not isinstance(stages,list) or len(stages)!=3:
        raise ValueError('stages require search, detect, confirm windows')
    previous = 0
    for mode, stage in zip(MODES,stages):
        if not isinstance(stage,dict) or set(stage)!={'mode','in','out','text'} or stage['mode']!=mode:
            raise ValueError('stages must be ordered search, detect, confirm with in/out/text')
        start = _integer(stage['in'], 'stage.in', previous, previous)
        end = _integer(stage['out'], 'stage.out', start+14, frames)
        _text(stage['text'], 'stage.text', 100)
        previous = end
    if previous != frames:
        raise ValueError('stages must cover all frames')
    telemetry = data.get('telemetry')
    if not isinstance(telemetry,list) or not 1 <= len(telemetry) <= 6:
        raise ValueError('telemetry requires 1..6 items')
    for item in telemetry:
        if not isinstance(item,dict) or set(item)!={'label','value','unit'}:
            raise ValueError('telemetry item requires label, value, unit')
        _text(item['label'], 'telemetry.label', 60)
        _text(item['unit'], 'telemetry.unit', 12, empty=True)
        if type(item['value']) not in (int,float) or not math.isfinite(item['value']) or abs(item['value'])>1e9:
            raise ValueError('telemetry value must be finite within +/-1e9')
    palette = data.get('palette',DEFAULT_PALETTE)
    if not isinstance(palette,list) or len(palette)!=4 or any(not isinstance(c,str) or not re.fullmatch(r'#[a-fA-F0-9]{6}',c) for c in palette):
        raise ValueError('palette requires four #RRGGBB colors')
    seed = _integer(data.get('seed',7), 'seed', -(2**31), 2**31-1)
    strings = [title, subtitle, target['name']] + [s['text'] for s in stages]
    strings += [i['label']+i['unit']+str(i['value']) for i in telemetry]
    path, face = _font_file(strings,font_path)
    probe = ImageDraw.Draw(Image.new('L',(1,1)))
    fonts = {}
    def fit(text,width,size):
        for pixels in range(size,19,-1):
            font = ImageFont.truetype(str(path),pixels,index=face)
            if probe.textlength(text,font=font)<=width:
                fonts[(text,width,size)] = font
                return
        raise ValueError('HUD text cannot fit its region at minimum font size 20')
    fit(title,1700,72); fit(subtitle,1700,30); fit(target['name'],720,34)
    for stage in stages: fit(stage['text'],1600,40)
    for item in telemetry:
        fit(item['label'],360,30)
        fit(_number(item['value'])+(' '+item['unit'] if item['unit'] else ''),380,40)
    return {'title':title,'subtitle':subtitle,'target':target,'stages':stages,
            'telemetry':telemetry,'palette':palette,'seed':seed,'frames':frames,
            'font_path':path,'font_face':face,'fonts':fonts}


def _number(value):
    return str(value) if type(value) is int else format(value,'.12g')


def validate_hud_content(data, frames, font_path=None):
    """No-write preflight: semantic windows, finite coordinates, font/fit checks."""
    prepared = _prepare(data,frames,font_path)
    return {key:prepared[key] for key in ('title','subtitle','target','stages','telemetry','palette','seed','frames')}


def _state(p,frame):
    _integer(frame,'frame',0,p['frames']-1)
    stage = next(s for s in p['stages'] if s['in']<=frame<s['out'])
    u = (frame-stage['in'])/max(1,stage['out']-stage['in']-1)
    points = p['target']['points']
    a,b = points[-2:]
    for left,right in zip(points,points[1:]):
        if left['frame']<=frame<=right['frame']:
            a,b=left,right;break
    v = (frame-a['frame'])/(b['frame']-a['frame'])
    target = ((a['x']+(b['x']-a['x'])*v)*1920,(a['y']+(b['y']-a['y'])*v)*1080)
    confirm = p['stages'][2]['in']
    values = []
    for i,item in enumerate(p['telemetry']):
        if frame>=confirm:
            values.append(_number(item['value']))
        elif frame>=confirm-5:
            values.append(str((p['seed']+frame*71+i*313)%997))
        else:
            q = min(1,frame/max(1,confirm-6))
            values.append(_number(round(item['value']*q,2)))
    return stage,u,target,values


def hud_safe_layout(tx,ty):
    """Keep the full search bracket between telemetry columns and above labels."""
    bracket_max=min(129,tx-500,1420-tx)
    name_top=max(885,math.ceil(ty+129+24))
    status_top=max(974,name_top+34+16)
    return {'bracket_max':bracket_max,'name_top':name_top,'status_top':status_top}


def _render(p,frame):
    from PIL import Image,ImageDraw,ImageFilter,ImageFont
    stage,u,(tx,ty),values = _state(p,frame)
    bg,ink,accent,alert = p['palette']; color=alert if stage['mode']=='confirm' else accent
    layout=hud_safe_layout(tx,ty)
    image = Image.new('RGB',SIZE,bg); draw=ImageDraw.Draw(image)
    def rgb(hexcolor,alpha):
        return tuple(int(hexcolor[i:i+2],16) for i in (1,3,5))+(alpha,)
    grid = Image.new('RGBA',SIZE); gd=ImageDraw.Draw(grid)
    for row in range(15):
        for col in range(24):
            x=col*88+(row%2)*44;y=row*76
            pts=[(x+51*math.cos(k*math.tau/6+math.pi/6),y+51*math.sin(k*math.tau/6+math.pi/6)) for k in range(6)]
            gd.line(pts+[pts[0]],fill=rgb(accent,22),width=1)
    image=Image.alpha_composite(image.convert('RGBA'),grid)
    field=Image.new('RGBA',SIZE); fd=ImageDraw.Draw(field)
    center=(960,560)
    # All rotations and scanning positions derive from the requested frame.
    for layer,radius in enumerate((180,244,310)):
        angle=frame*(.7 if layer%2 else -.45)+layer*35
        box=(960-radius,560-radius,960+radius,560+radius)
        fd.arc(box,angle,angle+285,fill=rgb(color,230-layer*35),width=3 if not layer else 2)
        for j in range(48):
            a=j*math.tau/48
            fd.line((960+radius*math.cos(a),560+radius*math.sin(a),960+(radius+7)*math.cos(a),560+(radius+7)*math.sin(a)),fill=rgb(color,160),width=2)
    sweep = frame*2.8%360
    fd.pieslice((650,250,1270,870),sweep-18,sweep,fill=rgb(accent,38))
    a=math.radians(sweep)
    fd.line((*center,960+310*math.cos(a),560+310*math.sin(a)),fill=rgb(accent,170),width=2)
    # The target marker follows input geometry; detection changes its semantics.
    marker_alpha=100 if stage['mode']=='search' else 255
    fd.ellipse((tx-8,ty-8,tx+8,ty+8),fill=rgb(color,marker_alpha))
    if stage['mode']!='search':
        lock=1-(1-min(1,u*2))**3 if stage['mode']=='detect' else 1
        size=44+(layout['bracket_max']-44)*(1-lock)
        for sx in (-1,1):
            for sy in (-1,1):
                x,y=tx+sx*size,ty+sy*size
                fd.line((x-sx*21,y,x,y,x,y-sy*21),fill=rgb(color,255),width=4)
        fd.line((tx-22,ty,tx+22,ty),fill=rgb(color,220),width=2)
        fd.line((tx,ty-22,tx,ty+22),fill=rgb(color,220),width=2)
    image=Image.alpha_composite(image,field.filter(ImageFilter.GaussianBlur(11)))
    image=Image.alpha_composite(image,field.filter(ImageFilter.GaussianBlur(3)))
    image=Image.alpha_composite(image,field)
    draw=ImageDraw.Draw(image)
    def text(value,xy,width,size,fill,anchor='lt',font_key=None):
        key = font_key if font_key else value
        font=p['fonts'].get((key,width,size))
        if font is None: font=ImageFont.truetype(str(p['font_path']),size,index=p['font_face'])
        draw.text(xy,value,font=font,fill=fill,anchor=anchor)
    text(p['title'],(100,72),1700,72,ink)
    text(p['subtitle'],(104,166),1700,30,ink)
    text(p['target']['name'],(960,layout['name_top']),720,34,color,anchor='mt')
    for i,(item,value) in enumerate(zip(p['telemetry'],values)):
        y=320+i*86
        text(item['label'],(100,y),360,30,color)
        final=_number(item['value'])+(' '+item['unit'] if item['unit'] else '')
        display=value+(' '+item['unit'] if item['unit'] else '')
        # Reserve for the largest possible transient, not merely the final value.
        font=p['fonts'][(final,380,40)]
        if draw.textlength(display,font=font)>380:
            while font.size>20 and draw.textlength(display,font=font)>380:
                font=ImageFont.truetype(str(p['font_path']),font.size-1,index=p['font_face'])
        draw.text((1810,y),display,font=font,fill=ink,anchor='rt')
        draw.line((1450,y+57,1810,y+57),fill=rgb(color,75),width=2)
        q=1 if stage['mode']=='confirm' else min(1,frame/max(1,p['stages'][2]['in']))
        draw.line((1450,y+57,1450+360*q,y+57),fill=color,width=3)
    # Status text is content, including the final confirmation; no English fallback.
    typed=stage['text'] if stage['mode']=='confirm' else stage['text'][:math.floor(min(1,u*3)*len(stage['text']))]
    text(typed,(960,layout['status_top']),1600,40,color,anchor='mt',font_key=stage['text'])
    scan=Image.new('RGBA',SIZE); sd=ImageDraw.Draw(scan)
    for y in range(0,1080,6): sd.line((0,y,1919,y),fill=(0,0,0,15),width=1)
    return Image.alpha_composite(image,scan).convert('RGB')


def render_hud_frame(data, frame, frames, font_path=None):
    """Return one full-resolution RGB PIL image, independent of seek order."""
    return _render(_prepare(data,frames,font_path),frame)


def render_hud_frames(data, frames, output, font_path=None):
    """Write f0000.png... and evidence.json into a new directory after preflight."""
    prepared=_prepare(data,frames,font_path)
    output=Path(output)
    if output.exists(): raise ValueError('output must be a new directory')
    output.mkdir(parents=True)
    hashes=[]
    for frame in range(frames):
        path=output/f'f{frame:04d}.png'
        _render(prepared,frame).save(path)
        hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
    report={'frames':frames,'fps':30,'width':1920,'height':1080,
            'font_path':str(prepared['font_path']),
            'font_sha256':hashlib.sha256(prepared['font_path'].read_bytes()).hexdigest(),
            'input_sha256':hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
            'stages':prepared['stages'],'target':prepared['target'],
            'telemetry':prepared['telemetry'],'final_values':_state(prepared,frames-1)[3],
            'frame_sha256':hashes,
            'limitations':['Procedural illustration; supplied telemetry is not a measured signal.',
                           'Font licensing and editorial sound design require separate verification.']}
    (output/'evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report
