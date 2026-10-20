"""Original frame-addressed liquid grammar using implicit metaball surfaces.

NumPy evaluates a scalar field; Pillow samples its contour into RGB frames.
There is no simulated mutable state and no upstream artwork or bundled font.
"""
from pathlib import Path
import hashlib
import math

DEFAULT_PALETTE = ['#faf5e8', '#202730', '#e66b42', '#49a69d']
MODES = ('gather', 'merge', 'split', 'resolve')


def _normalize(data, frames):
    if not isinstance(data, dict) or set(data)-{'title','subtitle','stages','palette','seed'}:
        raise ValueError('data must contain only title, subtitle, stages, palette, seed')
    if type(frames) is not int or not 60 <= frames <= 18000:
        raise ValueError('frames must be an integer in [60,18000]')
    title, subtitle = data.get('title','From drops to flow'), data.get('subtitle','Separate · connect · transform')
    if any(not isinstance(t,str) or len(t)>120 for t in (title,subtitle)):
        raise ValueError('title and subtitle must be text of at most 120 characters')
    palette = data.get('palette', DEFAULT_PALETTE)
    import re
    if not isinstance(palette,list) or len(palette)!=4 or any(not isinstance(c,str) or not re.fullmatch(r'#[a-fA-F0-9]{6}',c) for c in palette):
        raise ValueError('palette must contain four #RRGGBB colors')
    if type(data.get('seed',0)) is not int or not -(2**31) <= data.get('seed',0) <= 2**31-1:
        raise ValueError('seed must be an integer')
    boundaries = [0,frames//4,frames//2,frames*3//4,frames]
    stages = data.get('stages', [dict(mode=mode, label=mode.upper(), **{'in':boundaries[i], 'out':boundaries[i+1]}) for i,mode in enumerate(MODES)])
    if not isinstance(stages,list) or len(stages)!=4:
        raise ValueError('stages must contain gather, merge, split, resolve')
    previous = 0
    for i,stage in enumerate(stages):
        if not isinstance(stage,dict) or not {'in','out','mode','label'} <= set(stage) or set(stage)-{'in','out','mode','label','amount'}:
            raise ValueError('stage needs in, out, mode, label, optional amount')
        a,b = stage['in'],stage['out']
        if type(a) is not int or type(b) is not int or a!=previous or b-a<10 or b>frames or stage['mode']!=MODES[i]:
            raise ValueError('stages must be contiguous ordered modes, each at least ten integer frames')
        if not isinstance(stage['label'],str) or len(stage['label'])>80:
            raise ValueError('stage label must be text of at most 80 characters')
        amount=stage.get('amount',1)
        if type(amount) not in (int,float) or not math.isfinite(amount) or not .65 <= amount <= 1.25:
            raise ValueError('amount must be finite in [.65,1.25]')
        previous=b
    if len({float(s.get('amount',1)) for s in stages}) != 1:
        raise ValueError('all stages must use the same amount to preserve boundary continuity')
    if previous!=frames:
        raise ValueError('stages must cover all frames')
    return dict(title=title,subtitle=subtitle,palette=palette,stages=stages,seed=data.get('seed',0))


def _state(data,frame,frames):
    normalized=_normalize(data,frames)
    if type(frame) is not int or not 0<=frame<frames:
        raise ValueError('frame must be an integer in [0,frames)')
    stage=next(s for s in normalized['stages'] if s['in']<=frame<s['out'])
    u=(frame-stage['in'])/max(1,stage['out']-stage['in']-1)
    return normalized,stage,u


def _balls(stage,u,seed):
    mode=stage['mode']; amount=stage.get('amount',1)
    radius=120*amount
    if mode=='gather':
        distance=350-130*u
    elif mode=='merge':
        distance=220-150*u
    elif mode=='split':
        distance=70+280*u
    else:
        distance=350*(1-min(1,u/.55))
        radius=120*amount+55*min(1,u/.55)
    center_y=540+30*min(1,u/.55) if mode=='resolve' else 540
    tilt=12*math.sin(seed)
    balls=[(960-distance,center_y+tilt,radius),(960+distance,center_y-tilt,radius)]
    if mode=='split' and u>.57:
        # The neck pinches first. Smaller delayed droplets then detach and lag behind.
        lag=(u-.57)/.43
        tiny=radius*.24*math.sin(math.pi*lag)*(0.95+0.05*math.cos(seed))
        drift=14*math.sin(seed)
        balls.extend([(960-75*lag+drift,540+180*lag,tiny), (960+80*lag-drift,540-170*lag,tiny*.8)])
    if mode=='resolve' and u>.55:
        # A broad connected pool becomes a mask through which the resolution appears.
        amount2=(u-.55)/.45
        balls.extend([(960-220*amount2,center_y,90*amount2),(960+220*amount2,center_y,90*amount2)])
    return balls


def liquid_mask(data,frame,frames,size=(480,270)):
    """Return a binary Pillow L mask at an analysis resolution (same world bounds)."""
    import numpy as np
    from PIL import Image
    normalized,stage,u=_state(data,frame,frames)
    w,h=size
    if any(type(n) is not int or n<32 or n>1920 for n in size):
        raise ValueError('mask resolution must contain integer dimensions in [32,1920]')
    x=(np.arange(w,dtype=np.float64)+.5)*1920/w
    y=(np.arange(h,dtype=np.float64)+.5)*1080/h
    xx,yy=np.meshgrid(x,y)
    field=np.zeros((h,w),dtype=np.float64)
    for bx,by,radius in _balls(stage,u,normalized['seed']):
        field += radius*radius/np.maximum((xx-bx)**2+(yy-by)**2,1)
    return Image.fromarray((field>=1).astype('uint8')*255)


def _font_file(data,font_path=None):
    from PIL import ImageFont
    strings=[data['title'],data['subtitle']]+[s['label'] for s in data['stages']]
    needs_cjk=any(0x2e80<=ord(ch)<=0x9fff or 0xf900<=ord(ch)<=0xfaff for text in strings for ch in text)
    candidates=[Path(font_path)] if font_path is not None else [Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc')]
    if font_path is None and not needs_cjk:
        candidates.append(Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
    for path in candidates:
        if path.is_file():
            if needs_cjk and 'DejaVu' in path.name:
                raise ValueError('Chinese content requires a CJK-capable font_path')
            ImageFont.truetype(str(path),24)
            return path
    raise ValueError('provide an existing local font_path with glyphs for the content')


def validate_liquid_content(data,frames,font_path=None):
    """Validate semantic windows and usable local font without creating any output."""
    normalized=_normalize(data,frames)
    font_file=_font_file(normalized,font_path)
    _fit_font(normalized['title'],1640,86,font_file)
    _fit_font(normalized['subtitle'],1600,32,font_file)
    for stage in normalized['stages']:
        _fit_font(stage['label'],1600,52,font_file)
        if stage['mode']=='resolve':
            _fit_font(stage['label'],670,72,font_file)
    return normalized


def _font(size,font_path):
    from PIL import ImageFont
    return ImageFont.truetype(str(font_path),size)


def _fit_font(text,maxwidth,size,font_path):
    from PIL import Image,ImageDraw
    draw=ImageDraw.Draw(Image.new('L',(1,1)))
    font=_font(size,font_path)
    while draw.textlength(text,font=font)>maxwidth and size>12:
        size-=1; font=_font(size,font_path)
    if draw.textlength(text,font=font)>maxwidth:
        raise ValueError('text does not fit its layout even at minimum font size 12')
    return font


def _text(draw,text,xy,maxwidth,size,color,font_path):
    font=_fit_font(text,maxwidth,size,font_path)
    draw.text(xy,text,font=font,fill=color,anchor='mm')


def render_liquid_frame(data,frame,frames,font_path=None):
    """Return a pure RGB 1920x1080 frame, including the liquid-mask final reveal."""
    from PIL import Image,ImageDraw,ImageFilter
    normalized=validate_liquid_content(data,frames,font_path)
    normalized,stage,u=_state(normalized,frame,frames)
    font_file=_font_file(normalized,font_path)
    bg,ink,accent,secondary=normalized['palette']
    image=Image.new('RGB',(1920,1080),bg)
    draw=ImageDraw.Draw(image)
    mask=liquid_mask(data,frame,frames,size=(960,540)).filter(ImageFilter.GaussianBlur(.55)).resize((1920,1080),Image.Resampling.LANCZOS)
    shadow=Image.new('RGB',image.size,bg); shadow.paste(secondary,(0,0,1920,1080))
    image.paste(shadow,(0,12),mask.filter(ImageFilter.GaussianBlur(16)))
    fluid=Image.new('RGB',image.size,accent)
    fd=ImageDraw.Draw(fluid)
    # A contour-following highlight is itself clipped to the fluid surface.
    fd.ellipse((520,330,1370,510),fill=secondary)
    if stage['mode']=='resolve':
        _text(fd,stage['label'],(960,540),670,72,bg,font_file)
    image.paste(fluid,(0,0),mask)
    draw=ImageDraw.Draw(image)
    # Information is composited last; soft liquid shadows cannot obscure the heading.
    _text(draw,normalized['title'],(960,135),1640,86,ink,font_file)
    _text(draw,normalized['subtitle'],(960,220),1600,32,ink,font_file)
    _text(draw,stage['label'],(960,925),1600,52,ink,font_file)
    start=170
    for i,s in enumerate(normalized['stages']):
        color=accent if s['in']<=frame else secondary
        x=start+i*410
        draw.rounded_rectangle((x,1000,x+350,1009),radius=4,fill=color)
    return image


def render_liquid_frames(data,frames,output,font_path=None):
    """Write a new PNG sequence; no rendered media is stored in the repository."""
    normalized=validate_liquid_content(data,frames,font_path)
    font_file=_font_file(normalized,font_path)
    output=Path(output)
    if output.exists(): raise ValueError('output must be a new directory')
    output.mkdir(parents=True)
    digest=hashlib.sha256()
    for f in range(frames):
        image=render_liquid_frame(normalized,f,frames,font_file)
        image.save(output/f'f{f:04d}.png')
        digest.update(image.tobytes())
    return {'frames':frames,'width':1920,'height':1080,'pixel_sha256':digest.hexdigest(),
            'stages':normalized['stages'],'mask_resolution':[960,540],
            'font_path':str(font_file),'font_sha256':hashlib.sha256(font_file.read_bytes()).hexdigest()}
