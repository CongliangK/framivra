"""Original semantic contour morphs: equal topology, a circle mediator, no fades."""
from pathlib import Path
import hashlib
import math
import re

SHAPES=('circle','leaf','star','arrow','drop')
DEFAULT_PALETTE=['#faf5e8','#202730','#e66b42','#49a69d']


def _font_file(data,font_path=None):
    from PIL import ImageFont
    text=data['title']+''.join(s['label'] for s in data['shapes'])
    cjk=any(0x2e80<=ord(ch)<=0x9fff or 0xf900<=ord(ch)<=0xfaff for ch in text)
    paths=[Path(font_path)] if font_path else [Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc')]
    if not font_path and not cjk:paths.append(Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
    for path in paths:
        if path.is_file():
            if cjk and 'DejaVu' in path.name:raise ValueError('CJK text requires a CJK-capable font_path')
            ImageFont.truetype(str(path),20)
            return path
    raise ValueError('provide an existing local font_path supporting the content')


def _fit(text,width,size,font_path):
    from PIL import Image,ImageDraw,ImageFont
    draw=ImageDraw.Draw(Image.new('L',(1,1)))
    while True:
        font=ImageFont.truetype(str(font_path),size)
        if draw.textlength(text,font=font)<=width:return font
        if size<=12:raise ValueError('text does not fit at minimum font size 12')
        size-=1


def validate_morph_content(data,frames,font_path=None):
    """Return normalized content after all schema/font/fit checks, without writes."""
    if not isinstance(data,dict) or set(data)-{'title','shapes','palette'}:
        raise ValueError('content supports title, shapes, palette')
    if type(frames) is not int or not 60<=frames<=18000:raise ValueError('frames must be an integer in [60,18000]')
    title=data.get('title','One form, new possibilities')
    if not isinstance(title,str) or len(title)>120 or '\n' in title:raise ValueError('title must be one line of at most 120 characters')
    palette=data.get('palette',DEFAULT_PALETTE)
    if not isinstance(palette,list) or len(palette)!=4 or any(not isinstance(x,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',x) for x in palette):raise ValueError('palette must have four #RRGGBB colors')
    shapes=data.get('shapes')
    if not isinstance(shapes,list) or not 2<=len(shapes)<=5:raise ValueError('shapes must contain 2 to 5 semantic objects')
    previous=0;normalized=[]
    for i,entry in enumerate(shapes):
        if not isinstance(entry,dict) or not {'shape','label','in','out'}<=set(entry) or set(entry)-{'shape','label','in','out','transition','x','y','scale','color'}:raise ValueError('shape needs shape,label,in,out; optional transition,x,y,scale,color')
        if not isinstance(entry['shape'],str) or entry['shape'] not in SHAPES:raise ValueError('unknown semantic shape')
        a,b=entry['in'],entry['out']
        if type(a) is not int or type(b) is not int or a!=previous or not a<b<=frames:raise ValueError('shape windows must be contiguous integer [in,out) windows')
        transition=entry.get('transition',0 if i==0 else 18)
        if type(transition) is not int or (i==0 and transition!=0) or (i>0 and not 8<=transition<=b-a-10) or b-a-transition<10:raise ValueError('each shape needs a readable hold of ten frames after its transition')
        label=entry['label']
        if not isinstance(label,str) or len(label)>100 or '\n' in label:raise ValueError('label must be one line of at most 100 characters')
        x,y,scale=entry.get('x',960),entry.get('y',550),entry.get('scale',1)
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (x,y,scale)) or not 400<=x<=1520 or not 400<=y<=680 or not .5<=scale<=1.1:raise ValueError('finite coordinates must stay in x[400,1520],y[400,680],scale[.5,1.1]')
        if y-240*scale<270 or y+240*scale>850:
            raise ValueError('shape bounds must leave title and footer reading zones clear')
        color=entry.get('color',palette[2+i%2])
        if not isinstance(color,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',color):raise ValueError('color must be #RRGGBB')
        normalized.append(dict(shape=entry['shape'],label=label,**{'in':a,'out':b},transition=transition,x=x,y=y,scale=scale,color=color))
        previous=b
    if previous!=frames:raise ValueError('shape windows must cover all frames')
    result=dict(title=title,palette=palette,shapes=normalized)
    font=_font_file(result,font_path)
    _fit(title,1640,84,font)
    for i,entry in enumerate(normalized):
        _fit(entry['label'],1500,58,font)
        if i:_fit(normalized[i-1]['label']+' → '+entry['label'],1500,46,font)
    return result


def _resample(points,count=128):
    # Equal arc-length sampling gives every independently authored outline equal topology.
    loop=points+[points[0]]
    lengths=[math.hypot(b[0]-a[0],b[1]-a[1]) for a,b in zip(loop,loop[1:])]
    total=sum(lengths);result=[];edge=0;passed=0
    for i in range(count):
        distance=i/count*total
        while edge<len(lengths)-1 and passed+lengths[edge]<distance:passed+=lengths[edge];edge+=1
        t=(distance-passed)/max(lengths[edge],1e-12);a,b=loop[edge],loop[edge+1]
        result.append((a[0]*(1-t)+b[0]*t,a[1]*(1-t)+b[1]*t))
    return result


def shape_contour(name,count=128):
    """Return explicit semantic outlines, normalized around the same origin."""
    if name not in SHAPES:raise ValueError('unknown shape')
    if name=='arrow':
        points=[(0,-.38),(.35,-.38),(.35,-.85),(1,0),(.35,.85),(.35,.38),(-1,.38),(-1,-.38)]
    else:
        points=[]
        for i in range(256):
            a=-math.pi/2+2*math.pi*i/256
            if name=='circle':x,y=math.cos(a),math.sin(a)
            elif name=='star':
                r=.68+.32*math.cos(5*(a+math.pi/2));x,y=r*math.cos(a),r*math.sin(a)
            elif name=='leaf':
                x=math.cos(a)*abs(math.cos(a))**.5*.73;y=math.sin(a)
            else:
                # A pointed upper tip and weighted bulb distinguish a drop from a circle.
                r=.72+.28*math.sin(a);x,y=r*math.cos(a),r*math.sin(a)
                y-=.28*max(0,-math.sin(a))**8
            points.append((x,y))
    return _resample(points,count)


def morph_state(data,frame,frames):
    """Compute all geometry directly from f; transition travels through a round mediator."""
    if type(frame) is not int or not 0<=frame<frames:raise ValueError('frame must be integer in [0,frames)')
    index=next(i for i,s in enumerate(data['shapes']) if s['in']<=frame<s['out'])
    current=data['shapes'][index];previous=data['shapes'][max(0,index-1)]
    t=1 if not current['transition'] else min(1,(frame-current['in'])/current['transition'])
    eased=t*t*(3-2*t)
    old,new=shape_contour(previous['shape']),shape_contour(current['shape'])
    circle=shape_contour('circle')
    if t<.5:
        u=(2*t)**2*(3-4*t);contour=[(a[0]*(1-u)+b[0]*u,a[1]*(1-u)+b[1]*u) for a,b in zip(old,circle)]
    else:
        u=(2*t-1)**2*(3-2*(2*t-1));contour=[(a[0]*(1-u)+b[0]*u,a[1]*(1-u)+b[1]*u) for a,b in zip(circle,new)]
    # A small compression at the mediator turns the deformation into the transition.
    squash=1-.10*math.sin(math.pi*t)
    contour=[(x/squash,y*squash) for x,y in contour]
    x=previous['x']*(1-eased)+current['x']*eased;y=previous['y']*(1-eased)+current['y']*eased
    scale=previous['scale']*(1-eased)+current['scale']*eased
    def rgb(color):return tuple(int(color[i:i+2],16) for i in (1,3,5))
    a,b=rgb(previous['color']),rgb(current['color'])
    color=tuple(round(v*(1-eased)+w*eased) for v,w in zip(a,b))
    return dict(points=[(x+px*240*scale,y+py*240*scale) for px,py in contour],color=color,
                label=current['label'] if t>=1 or index==0 else previous['label']+' → '+current['label'],progress=t,index=index)


def render_morph_frame(data,frame,frames,font_path=None):
    from PIL import Image,ImageDraw
    data=validate_morph_content(data,frames,font_path);state=morph_state(data,frame,frames)
    font=_font_file(data,font_path);bg,ink,accent,secondary=data['palette']
    image=Image.new('RGB',(1920,1080),bg)
    # Draw geometry at double resolution, then downsample; no cross-fading layers.
    shape=Image.new('RGBA',(3840,2160),(0,0,0,0));sd=ImageDraw.Draw(shape)
    points=[(round(x*2),round(y*2)) for x,y in state['points']]
    sd.polygon(points,fill=state['color']+(255,))
    sd.line(points+[points[0]],fill=ink,width=5,joint='curve')
    shape=shape.resize(image.size,Image.Resampling.LANCZOS)
    image.paste(shape,(0,0),shape)
    draw=ImageDraw.Draw(image)
    draw.text((960,140),data['title'],font=_fit(data['title'],1640,84,font),fill=ink,anchor='mm')
    draw.text((960,925),state['label'],font=_fit(state['label'],1500,58 if state['progress']>=1 else 46,font),fill=ink,anchor='mm')
    # Semantic checkpoints belong to the story, rather than anonymous cycling items.
    for i,entry in enumerate(data['shapes']):
        x=960+(i-(len(data['shapes'])-1)/2)*200
        draw.ellipse((x-8,1012,x+8,1028),fill=accent if i<=state['index'] else secondary)
    return image


def render_morph_frames(data,frames,output,font_path=None):
    data=validate_morph_content(data,frames,font_path)
    output=Path(output)
    if output.exists():raise ValueError('output must be a new directory')
    output.mkdir(parents=True);digest=hashlib.sha256()
    for frame in range(frames):
        image=render_morph_frame(data,frame,frames,font_path)
        image.save(output/f'f{frame:04d}.png');digest.update(image.tobytes())
    font=_font_file(data,font_path)
    return dict(frames=frames,width=1920,height=1080,pixel_sha256=digest.hexdigest(),
                shapes=data['shapes'],font_path=str(font),font_sha256=hashlib.sha256(font.read_bytes()).hexdigest())
