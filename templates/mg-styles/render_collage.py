"""Original procedural cut-paper puppets; explicitly not photographic collage.

Schema: title/subtitle, parts[{id,shape,parent,x,y,scale,angle,pivot,
motion:{hinge,drift:[dx,dy]}}], beats[{mode,in,out,text,active:[part ids]}],
exposure (integer held frames), optional palette/seed/evidence[{label,value,source}].
Root x/y are normalized full-frame coordinates; child x/y are parent-scale units.
Pivot uses local scale units. Parents precede children; linked motion propagates.
All drawing, text, paper grain and transitions use one quantized frame clock.
"""
from pathlib import Path
import hashlib
import json
import math
import re

SIZE=(1920,1080)
MODES=('awaken','release','reframe','resolve')
SHAPES=('head','jaw','body','arm','hand','leaf','planet','rocket')
PALETTE=['#ede5ce','#202c31','#da6548','#779b88','#d4b546']


def _int(v,name,lo,hi):
    if type(v) is not int or not lo<=v<=hi:raise ValueError(f'{name} must be integer in [{lo},{hi}]')
    return v


def _finite(v,name,lo,hi):
    if type(v) not in (int,float) or not math.isfinite(v) or not lo<=v<=hi:raise ValueError(f'{name} must be finite in [{lo},{hi}]')
    return v


def _text(v,name,limit=120,empty=False):
    if not isinstance(v,str) or len(v)>limit or '\n' in v or '\r' in v or not empty and not v.strip():raise ValueError(f'{name} must be single-line text of at most {limit} characters')
    return v


def _rot(x,y,a):
    a=math.radians(a);return x*math.cos(a)-y*math.sin(a),x*math.sin(a)+y*math.cos(a)


def _shape(kind):
    if kind=='head':return [(math.cos(i*math.tau/32),math.sin(i*math.tau/32)*.85) for i in range(32)]
    if kind=='jaw':return [(-.95,-.12),(.8,-.12),(.95,.26),(.6,.65),(-.8,.55)]
    if kind=='body':return [(-.45,-1),(.4,-1),(.7,1),(-.7,1)]
    if kind=='arm':return [(-.18,-.15),(.18,-.15),(.25,.9),(-.15,1)]
    if kind=='hand':return [(-.5,.6),(-.7,-.2),(-.5,-.3),(-.2,.1),(-.2,-.8),(0,-.85),(.1,-.2),(.25,-.7),(.42,-.65),(.35,.15),(.65,-.3),(.8,-.1),(.55,.7)]
    if kind=='leaf':return [(-1,.4),(-.5,-.5),(.6,-.8),(1,-.4),(.7,.5),(-.3,.85)]
    if kind=='planet':return [(math.cos(i*math.tau/32),math.sin(i*math.tau/32)) for i in range(32)]
    return [(-.35,.6),(-.35,-.4),(0,-1),(.35,-.4),(.35,.6),(.7,.9),(.2,.75),(0,1),(-.2,.75),(-.7,.9)]


def _ease(u):return 1-(1-max(0,min(1,u)))**3


def _state(p,frame):
    _int(frame,'frame',0,p['frames']-1)
    held=frame//p['exposure']*p['exposure']
    beat=next(b for b in p['beats'] if b['in']<=held<b['out'])
    local=held-beat['in'];u=local/max(1,beat['out']-beat['in']-p['exposure'])
    transforms={}
    for part in p['parts']:
        parent=transforms.get(part['parent'])
        if parent:
            px,py,pa,ps=parent;ox,oy=_rot(part['x']*ps,part['y']*ps,pa)
            x,y,a=px+ox,py+oy,pa+part['angle']
        else:x,y,a=part['x']*1920,part['y']*1080,part['angle']
        if part['id'] in beat['active']:
            if beat['mode']=='awaken':a+=math.sin(local/p['exposure']*1.3)*part['motion']['hinge']
            elif beat['mode']=='release':
                q=_ease(u);x+=part['motion']['drift'][0]*q;y+=part['motion']['drift'][1]*q;a+=part['motion']['hinge']*q
            elif beat['mode']=='reframe':
                q=1-_ease(u);x+=part['motion']['drift'][0]*q;y+=part['motion']['drift'][1]*q;a+=part['motion']['hinge']*q
        # Rotate around the authored hinge rather than around every part's centre.
        pivot_x,pivot_y=part['pivot'];rx,ry=_rot(pivot_x*part['scale'],pivot_y*part['scale'],a)
        x+=pivot_x*part['scale']-rx;y+=pivot_y*part['scale']-ry
        transforms[part['id']]=(x,y,a,part['scale'])
    return held,beat,u,transforms


def _points(part,transform):
    x,y,a,scale=transform
    return [(x+_rot(px*scale,py*scale,a)[0],y+_rot(px*scale,py*scale,a)[1]) for px,py in _shape(part['shape'])]


def _prepare(data,frames,font_path):
    from PIL import Image,ImageDraw,ImageFont
    _int(frames,'frames',90,18000)
    keys={'title','subtitle','parts','beats','exposure','palette','seed','evidence'}
    if not isinstance(data,dict) or set(data)-keys:raise ValueError('unsupported collage content fields')
    title=_text(data.get('title'),'title');subtitle=_text(data.get('subtitle',''),'subtitle',empty=True)
    exposure=_int(data.get('exposure',3),'exposure',1,6)
    seed=_int(data.get('seed',7),'seed',-(2**31),2**31-1)
    parts=data.get('parts')
    if not isinstance(parts,list) or not 3<=len(parts)<=10:raise ValueError('parts must contain 3..10 program cutouts')
    ids=[]
    for part in parts:
        if not isinstance(part,dict) or set(part)!={'id','shape','parent','x','y','scale','angle','pivot','motion'}:raise ValueError('part needs id/shape/parent/x/y/scale/angle/pivot/motion')
        if not isinstance(part['id'],str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,24}',part['id']) or part['id'] in ids:raise ValueError('part ids must be unique lowercase names')
        if part['shape'] not in SHAPES:raise ValueError('unsupported program silhouette')
        parent=part['parent']
        if parent is not None and (not isinstance(parent,str) or parent not in ids):raise ValueError('parent must precede its child')
        _finite(part['x'],'part.x',-2 if parent else .1,2 if parent else .9)
        _finite(part['y'],'part.y',-2 if parent else .3,2 if parent else .8)
        _finite(part['scale'],'part.scale',24,220);_finite(part['angle'],'part.angle',-180,180)
        if not isinstance(part['pivot'],list) or len(part['pivot'])!=2:raise ValueError('pivot must be [x,y] in local scale units')
        for v in part['pivot']:_finite(v,'pivot',-1.5,1.5)
        motion=part['motion']
        if not isinstance(motion,dict) or set(motion)!={'hinge','drift'}:raise ValueError('motion needs hinge angle and drift vector')
        _finite(motion['hinge'],'motion.hinge',-70,70)
        if not isinstance(motion['drift'],list) or len(motion['drift'])!=2:raise ValueError('drift must contain dx/dy')
        for v in motion['drift']:_finite(v,'motion.drift',-250,250)
        ids.append(part['id'])
    beats=data.get('beats')
    if not isinstance(beats,list) or len(beats)!=4:raise ValueError('beats need awaken/release/reframe/resolve')
    previous=0
    for mode,beat in zip(MODES,beats):
        if not isinstance(beat,dict) or set(beat)!={'mode','in','out','text','active'} or beat['mode']!=mode:raise ValueError('each beat needs ordered mode,in,out,text,active')
        start=_int(beat['in'],'beat.in',previous,previous);end=_int(beat['out'],'beat.out',start+18,frames)
        if start%exposure or end!=frames and end%exposure:raise ValueError('beat boundaries must align with whole-frame exposure')
        _text(beat['text'],'beat.text',100)
        if not isinstance(beat['active'],list) or any(not isinstance(v,str) or v not in ids for v in beat['active']) or len(set(beat['active']))!=len(beat['active']):raise ValueError('active must contain distinct known part ids')
        previous=end
    if previous!=frames:raise ValueError('beats must cover all frames')
    palette=data.get('palette',PALETTE)
    if not isinstance(palette,list) or len(palette)!=5 or any(not isinstance(c,str) or not re.fullmatch(r'#[a-fA-F0-9]{6}',c) for c in palette):raise ValueError('palette needs five #RRGGBB colors')
    evidence=data.get('evidence',[])
    if not isinstance(evidence,list) or len(evidence)>2:raise ValueError('evidence allows at most two sourced statements')
    for item in evidence:
        if not isinstance(item,dict) or set(item)!={'label','value','source'}:raise ValueError('data evidence must carry label,value,source')
        for key in ('label','value','source'):_text(item[key],'evidence.'+key,120)
    paths=[Path(font_path)] if font_path is not None else [Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'),Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    path=next((v for v in paths if v.is_file()),None)
    if path is None:raise ValueError('provide a separately licensed local font_path')
    face=2 if path.name=='NotoSansCJK-Bold.ttc' else 0
    font=ImageFont.truetype(str(path),40,index=face);mask=font.getmask(chr(0x10ffff));missing=(mask.size,bytes(mask))
    strings=[title,subtitle]+[b['text'] for b in beats]+[item['label']+' '+item['value']+' / '+item['source'] for item in evidence]
    for char in set(''.join(strings)):
        if not char.isspace():
            mask=font.getmask(char)
            if (mask.size,bytes(mask))==missing:raise ValueError(f'font lacks required glyph U+{ord(char):04X}')
    probe=ImageDraw.Draw(Image.new('L',(1,1)));fonts={}
    def fit(text,width,size):
        for pixels in range(size,19,-1):
            f=ImageFont.truetype(str(path),pixels,index=face)
            if probe.textlength(text,font=f)<=width:fonts[(text,width,size)]=f;return
        raise ValueError('collage text cannot fit its region at minimum font size 20')
    fit(title,1660,80);fit(subtitle,1660,30)
    for beat in beats:fit(beat['text'],1660,46)
    for statement in strings[6:]:fit(statement,1720,22)
    p={'title':title,'subtitle':subtitle,'parts':parts,'beats':beats,'exposure':exposure,
       'palette':palette,'seed':seed,'evidence':evidence,'frames':frames,'fonts':fonts,'font_path':path}
    # Prove all held part contours stay below text and inside the frame before writes.
    for frame in range(0,frames,exposure):
        _,_,_,transforms=_state(p,frame)
        for part in parts:
            pts=_points(part,transforms[part['id']])
            if any(x<30 or x>1890 or y<310 or y>940 for x,y in pts):raise ValueError('animated cutout leaves the safe collage stage')
    return p


def validate_collage_content(data,frames,font_path=None):
    """Validate linked motion, exposure, captions, source evidence and all bounds; no writes."""
    p=_prepare(data,frames,font_path)
    return {k:p[k] for k in ('title','subtitle','parts','beats','exposure','palette','seed','evidence','frames')}


def _render(p,frame):
    from PIL import Image,ImageDraw
    held,beat,u,transforms=_state(p,frame)
    bg,ink,accent,second,tape=p['palette'];image=Image.new('RGB',SIZE,bg);d=ImageDraw.Draw(image)
    # Every fibre is fixed by seed and position, not a new RNG draw per frame.
    for i in range(1700):
        x=(i*491+p['seed']*13)%1920;y=(i*137+p['seed']*7)%1080
        d.line((x,y,x+1+i%4,y),fill='#d4c9af',width=1)
    # Newspaper-like, deliberately oversized head and small puppet body are parts,
    # not photographic claims or a grid of text cards.
    for i,part in enumerate(p['parts']):
        pts=_points(part,transforms[part['id']]);color=(accent,second,tape,ink)[(i+p['seed'])%4]
        d.polygon([(x+6,y+8) for x,y in pts],fill='#bfb49d')
        d.polygon(pts,fill=color);d.line(pts+[pts[0]],fill='#fffcef',width=8,joint='curve')
        x,y,a,scale=transforms[part['id']]
        def point(px,py):
            rx,ry=_rot(px*scale,py*scale,a);return x+rx,y+ry
        if part['shape']=='head':
            for px in (-.32,.32):
                ex,ey=point(px,-.12);r=max(3,scale*.055);d.ellipse((ex-r,ey-r,ex+r,ey+r),fill=bg)
            # Halftone is clipped to the actual head silhouette.
            mask=Image.new('L',SIZE);md=ImageDraw.Draw(mask);md.polygon(pts,fill=255)
            dots=Image.new('RGB',SIZE,color);dd=ImageDraw.Draw(dots)
            for hy in range(-4,5):
                for hx in range(-5,6):
                    dx,dy=point(hx*.16,hy*.14+.2);dd.ellipse((dx-2,dy-2,dx+2,dy+2),fill=ink)
            # Preserve facial eye marks: dots occupy the lower half only.
            md.rectangle((0,0,1919,int(y+scale*.1)),fill=0);image.paste(dots,(0,0),mask);d=ImageDraw.Draw(image)
        elif part['shape'] in ('leaf','planet'):
            q1=point(-.8,.3);q2=point(.8,-.3);d.line((*q1,*q2),fill=ink,width=4)
        elif part['shape']=='rocket':
            ex,ey=point(0,-.25);r=scale*.2;d.ellipse((ex-r,ey-r,ex+r,ey+r),fill=bg)
        # Tape is authored at the cutout hinge and inherits its parent rotation.
        if part['shape'] in ('body','arm'):
            tape_pts=[point(px,py) for px,py in ((-.25,-.18),(.25,-.18),(.25,-.02),(-.25,-.02))];d.polygon(tape_pts,fill=tape)
    # A held torn-paper front physically replaces the old layout, then uncovers it.
    if beat['mode']=='reframe' and u<.55:
        progress=min(1,u/.55);edge=1920-3840*progress
        pts=[(max(-20,edge)+(12 if j%2 else 0),300+j*30) for j in range(23)]
        d.polygon([(edge+1920,300)]+pts+[(edge+1920,960)],fill='#fffcef')
        d.line(pts,fill=ink,width=3)
    # Editorial hierarchy is independent of the moving puppet geometry.
    d.rectangle((90,55,1825,157),fill=ink)
    d.text((115,57),p['title'],font=p['fonts'][(p['title'],1660,80)],fill=bg,anchor='lt')
    d.text((110,182),p['subtitle'],font=p['fonts'][(p['subtitle'],1660,30)],fill=ink,anchor='lt')
    d.text((960,246),beat['text'],font=p['fonts'][(beat['text'],1660,46)],fill=ink,anchor='mt')
    for i,item in enumerate(p['evidence']):
        text=item['label']+' '+item['value']+' / '+item['source']
        d.text((100,982+i*34),text,font=p['fonts'][(text,1720,22)],fill=ink,anchor='lt')
    return image


def render_collage_frame(data,frame,frames,font_path=None):
    """Return a whole RGB held exposure; seeking never accumulates puppet transforms."""
    return _render(_prepare(data,frames,font_path),frame)


def render_collage_frames(data,frames,output,font_path=None):
    """Write f0000.png... and explicit program-cutout provenance to a new directory."""
    p=_prepare(data,frames,font_path);output=Path(output)
    if output.exists():raise ValueError('output must be a new directory')
    output.mkdir(parents=True);hashes=[]
    for frame in range(frames):
        path=output/f'f{frame:04d}.png';_render(p,frame).save(path);hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
    report={'frames':frames,'fps':30,'width':1920,'height':1080,'exposure':p['exposure'],
            'font_path':str(p['font_path']),'font_sha256':hashlib.sha256(p['font_path'].read_bytes()).hexdigest(),
            'input_sha256':hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
            'beats':p['beats'],'evidence':p['evidence'],'frame_sha256':hashes,
            'source_type':'original_procedural_cutouts_not_photographs',
            'limitations':['Procedural silhouettes, not photographic cutouts.',
                           'Authored evidence sources are input assertions, not independently verified research.',
                           'Paper-rustle/voice timing and final artistic review require separate validation.']}
    (output/'evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report
