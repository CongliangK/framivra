"""Original flat-vector explainer: seed -> causal graph -> arranged outcomes.

Schema: title/subtitle, origin{label,detail}, cards[{id,label,value,detail}],
links[{from,to}] (from may be 'origin'), final_order (all card ids),
stages[{mode:question|build|resolve,in,out,text}], optional palette/seed.
All windows are contiguous integer [in,out). Content is single-line CJK-capable
text. No external artwork, runtime clock, browser or persistent animation state.
"""
from pathlib import Path
import hashlib
import json
import math
import re

SIZE=(1920,1080)
S=2
PALETTE=['#fff4dc','#242d35','#f5684f','#48b4a0','#f9c44b','#5574d9']
MODES=('question','build','resolve')


def _int(value,name,low,high):
    if type(value) is not int or not low<=value<=high:
        raise ValueError(f'{name} must be an integer in [{low},{high}]')
    return value


def _text(value,name,limit=100,empty=False):
    if not isinstance(value,str) or len(value)>limit or '\n' in value or '\r' in value or not empty and not value.strip():
        raise ValueError(f'{name} must be single-line text of at most {limit} characters')
    return value


def _number(value):
    return str(value) if type(value) is int else format(value,'.10g')


# Shared authored timing: all cards settle before six complete readable frames.
BUILD_LEAD = 12
BUILD_STAGGER = 7
BUILD_ATTACK = 16
BUILD_HOLD = 6


def _minimum_build_frames(count):
    return BUILD_LEAD + (count-1)*BUILD_STAGGER + BUILD_ATTACK + BUILD_HOLD


def _build_progress(local,index):
    return _clamp((local-BUILD_LEAD-index*BUILD_STAGGER)/BUILD_ATTACK)


def _prepare(data,frames,font_path):
    from PIL import Image,ImageDraw,ImageFont
    _int(frames,'frames',90,18000)
    keys={'title','subtitle','origin','cards','links','final_order','stages','palette','seed'}
    if not isinstance(data,dict) or set(data)-keys:
        raise ValueError('flat-vector content contains unsupported fields')
    title=_text(data.get('title'),'title'); subtitle=_text(data.get('subtitle',''),'subtitle',empty=True)
    origin=data.get('origin')
    if not isinstance(origin,dict) or set(origin)!={'label','detail'}:
        raise ValueError('origin requires label and detail')
    _text(origin['label'],'origin.label',50);_text(origin['detail'],'origin.detail',80,empty=True)
    cards=data.get('cards')
    if not isinstance(cards,list) or not 1<=len(cards)<=6:
        raise ValueError('cards require 1..6 entries')
    ids=[]
    for card in cards:
        if not isinstance(card,dict) or set(card)!={'id','label','value','detail'}:
            raise ValueError('card requires id, label, value, detail')
        if not isinstance(card['id'],str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,24}',card['id']) or card['id']=='origin' or card['id'] in ids:
            raise ValueError('card ids must be unique lowercase names other than origin')
        ids.append(card['id'])
        _text(card['label'],'card.label',50);_text(card['detail'],'card.detail',80,empty=True)
        if type(card['value']) not in (int,float) or not math.isfinite(card['value']) or abs(card['value'])>1e9:
            raise ValueError('card value must be finite within +/-1e9')
    order=data.get('final_order')
    if not isinstance(order,list) or len(order)!=len(ids) or any(not isinstance(x,str) for x in order) or set(order)!=set(ids):
        raise ValueError('final_order must contain every card id exactly once')
    links=data.get('links')
    if not isinstance(links,list) or not 1<=len(links)<=12:
        raise ValueError('links require 1..12 causal arrows')
    seen=set()
    for link in links:
        if not isinstance(link,dict) or set(link)!={'from','to'} or not isinstance(link['from'],str) or not isinstance(link['to'],str):
            raise ValueError('link requires from/to ids')
        pair=(link['from'],link['to'])
        if pair[0] not in ['origin']+ids or pair[1] not in ids or pair[0]==pair[1] or pair in seen:
            raise ValueError('link ids must exist and arrows must be distinct, without self-loops')
        if pair[0]!='origin' and ids.index(pair[0])>=ids.index(pair[1]):
            raise ValueError('causal arrows must point forward in card reveal order')
        seen.add(pair)
    if any(not any(link['to']==key for link in links) for key in ids):
        raise ValueError('every card must have an incoming causal arrow')
    stages=data.get('stages')
    if not isinstance(stages,list) or len(stages)!=3:
        raise ValueError('stages require question, build and resolve')
    previous=0
    for mode,stage,minimum in zip(MODES,stages,(14,_minimum_build_frames(len(cards)),38)):
        if not isinstance(stage,dict) or set(stage)!={'mode','in','out','text'} or stage['mode']!=mode:
            raise ValueError('each stage requires ordered mode, in, out and text')
        start=_int(stage['in'],'stage.in',previous,previous)
        previous=_int(stage['out'],'stage.out',start+minimum,frames)
        _text(stage['text'],'stage.text')
    if previous!=frames:raise ValueError('stages must cover all frames')
    palette=data.get('palette',PALETTE)
    if not isinstance(palette,list) or len(palette)!=6 or any(not isinstance(x,str) or not re.fullmatch(r'#[a-fA-F0-9]{6}',x) for x in palette):
        raise ValueError('palette requires six #RRGGBB colors')
    seed=_int(data.get('seed',7),'seed',-(2**31),2**31-1)
    paths=[Path(font_path)] if font_path is not None else [Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'),Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    path=next((p for p in paths if p.is_file()),None)
    if path is None:raise ValueError('provide an existing separately licensed local font_path')
    face=2 if path.name=='NotoSansCJK-Bold.ttc' else 0
    font=ImageFont.truetype(str(path),40,index=face);missing=font.getmask(chr(0x10ffff));missing=(missing.size,bytes(missing))
    strings=[title,subtitle,origin['label'],origin['detail']]+[s['text'] for s in stages]
    strings += [c['label']+c['detail']+_number(c['value']) for c in cards]
    for char in set(''.join(strings)):
        if not char.isspace():
            mask=font.getmask(char)
            if (mask.size,bytes(mask))==missing:raise ValueError(f'font lacks required glyph U+{ord(char):04X}')
    probe=ImageDraw.Draw(Image.new('L',(1,1)));fonts={}
    def fit(text,width,size):
        for pixels in range(size,19,-1):
            f=ImageFont.truetype(str(path),pixels*S,index=face)
            if probe.textlength(text,font=f)<=width*S:
                fonts[(text,width,size)]=f;return
        raise ValueError('flat-vector text cannot fit its safe region at minimum font size 20')
    fit(title,1700,74);fit(subtitle,1700,30)
    fit(origin['label'],450,54);fit(origin['detail'],450,28)
    for stage in stages:fit(stage['text'],1650,42)
    for card in cards:
        fit(card['label'],360,36);fit(card['detail'],360,26);fit(_number(card['value']),360,68)
    return {'title':title,'subtitle':subtitle,'origin':origin,'cards':cards,'links':links,
            'final_order':order,'stages':stages,'palette':palette,'seed':seed,'frames':frames,
            'font_path':path,'font_face':face,'fonts':fonts}


def validate_flat_vector_content(data,frames,font_path=None):
    """Return normalized semantic content after no-write text/geometry preflight."""
    p=_prepare(data,frames,font_path)
    return {k:p[k] for k in ('title','subtitle','origin','cards','links','final_order','stages','palette','seed','frames')}


def _clamp(x):return max(0,min(1,x))
def _ease(x):return 1-(1-_clamp(x))**3
def _back(x):
    u=_clamp(x)-1
    return 1+2.70158*u**3+1.70158*u*u


def _positions(p):
    n=len(p['cards']);cols=min(3,n);rows=math.ceil(n/cols)
    build={};final={}
    # Keep independent causal chains on separate rows rather than implying
    # that an arrow passes through an unrelated intermediate card.
    depths={}
    for card in p['cards']:
        incoming=[link['from'] for link in p['links'] if link['to']==card['id']]
        depths[card['id']]=1+max((depths.get(key,0) for key in incoming),default=0)
    groups={level:[card['id'] for card in p['cards'] if depths[card['id']]==level]
            for level in set(depths.values())}
    layered=max(depths.values())<=3 and max(map(len,groups.values()))<=2
    for i,card in enumerate(p['cards']):
        if layered:
            group=groups[depths[card['id']]];row=group.index(card['id'])
            build[card['id']]=(740+(depths[card['id']]-1)*420,
                               470+row*290 if len(group)==2 else 600)
        else:
            build[card['id']]=(740+i%cols*420,470+math.floor(i/cols)*290 if rows==2 else 600)
    for i,key in enumerate(p['final_order']):
        final[key]=(960+(i%cols-(cols-1)/2)*540,470+math.floor(i/cols)*290 if rows==2 else 600)
    return build,final


def _render(p,frame):
    from PIL import Image,ImageDraw
    _int(frame,'frame',0,p['frames']-1)
    stage=next(s for s in p['stages'] if s['in']<=frame<s['out'])
    local=frame-stage['in'];bg,ink,*accents=p['palette']
    image=Image.new('RGB',(1920*S,1080*S),bg);d=ImageDraw.Draw(image)
    def box(coords,fill,radius=0):
        coords=tuple(round(v*S) for v in coords)
        if radius:d.rounded_rectangle(coords,radius=round(radius*S),fill=fill)
        else:d.rectangle(coords,fill=fill)
    def ellipse(coords,fill):d.ellipse(tuple(round(v*S) for v in coords),fill=fill)
    def poly(points,fill):d.polygon([(round(x*S),round(y*S)) for x,y in points],fill=fill)
    def text(value,xy,width,size,fill,anchor='mt'):
        d.text((round(xy[0]*S),round(xy[1]*S)),value,font=p['fonts'][(value,width,size)],fill=fill,anchor=anchor)
    build,final=_positions(p)
    resolve=stage['mode']=='resolve';question=stage['mode']=='question'
    build_stage=p['stages'][1]
    build_local=(build_stage['out']-build_stage['in']-1) if resolve else frame-build_stage['in']
    reflow=_ease((local-24)/max(1,min(18,stage['out']-stage['in']-31))) if resolve else 0
    origin_x=960 if question else 960-660*_ease(build_local/16)
    origin_y=560
    # An original shape rig anticipates, stretches, overshoots, then settles.
    u=_clamp(frame/24);anticipate=math.sin(math.pi*_clamp(frame/5))
    pop=.62+.38*_back((frame-5)/19)
    sx=pop*(1+.14*anticipate-.08*math.sin(u*math.pi))
    sy=pop*(1-.16*anticipate+.10*math.sin(u*math.pi))
    r=175 if question else 118
    if not resolve or reflow<1:
        alpha_scale=1-.7*reflow
        ellipse((origin_x-r*sx*alpha_scale,origin_y-r*sy*alpha_scale,origin_x+r*sx*alpha_scale,origin_y+r*sy*alpha_scale),accents[0])
        if reflow<.5:
            ellipse((origin_x-38,origin_y-35,origin_x-24,origin_y-21),ink)
            ellipse((origin_x+24,origin_y-35,origin_x+38,origin_y-21),ink)
            box((origin_x-30,origin_y+25,origin_x+30,origin_y+37),ink,6)
            text(p['origin']['label'],(origin_x,795 if question else 750),450,54,ink)
            text(p['origin']['detail'],(origin_x,865 if question else 820),450,28,ink)
    positions={}
    amounts={}
    for i,card in enumerate(p['cards']):
        progress=_build_progress(build_local,i)
        bx,by=build[card['id']];fx,fy=final[card['id']]
        positions[card['id']]=(bx+(fx-bx)*reflow,by+(fy-by)*reflow)
        amounts[card['id']]=progress if not question else 0
    if not question and reflow<1:
        for link in p['links']:
            q=_ease(amounts[link['to']]);a=(origin_x,origin_y) if link['from']=='origin' else positions[link['from']]
            b=positions[link['to']];vx,vy=b[0]-a[0],b[1]-a[1];length=max(1,math.hypot(vx,vy));ux,uy=vx/length,vy/length
            start=(a[0]+ux*(120 if link['from']=='origin' else 165),a[1]+uy*(100 if link['from']=='origin' else 90))
            end=(b[0]-ux*175,b[1]-uy*95)
            tip=(start[0]+(end[0]-start[0])*q,start[1]+(end[1]-start[1])*q)
            if q>0:
                d.line(tuple(round(v*S) for v in (*start,*tip)),fill=accents[2],width=8*S)
                if q>.9:poly([tip,(tip[0]-ux*18-uy*10,tip[1]-uy*18+ux*10),(tip[0]-ux*18+uy*10,tip[1]-uy*18-ux*10)],accents[2])
    for i,card in enumerate(p['cards']):
        q=amounts[card['id']]
        if not q:continue
        x,y=positions[card['id']];scale=max(.01,_back(q));w=380*scale;ht=215*scale
        box((x-w/2,y-ht/2,x+w/2,y+ht/2),accents[(i+p['seed'])%len(accents)],20*scale)
        # Typography stays in stable safe regions after the brief shape overshoot.
        if q>=.8:
            text(card['label'],(x,y-78),360,36,ink)
            text(_number(card['value']),(x,y-25),360,68,ink)
            text(card['detail'],(x,y+62),360,26,ink)
    text(p['title'],(100,65),1700,74,ink,anchor='lt')
    text(p['subtitle'],(104,163),1700,30,ink,anchor='lt')
    text(stage['text'],(960,240),1650,42,ink)
    # A seed-coloured shape carries the cut; it reveals a new arranged argument.
    if resolve and local<24:
        if local<12:
            radius=2300*_ease((local+1)/12)
            ellipse((300-radius,560-radius,300+radius,560+radius),accents[0])
        else:
            mask=Image.new('L',image.size,255);md=ImageDraw.Draw(mask)
            radius=2300*_ease((local-11)/12)
            md.ellipse(tuple(round(v*S) for v in (960-radius,560-radius,960+radius,560+radius)),fill=0)
            image.paste(accents[0],(0,0,image.width,image.height),mask)
    # Seeded small footer modules align to a common grid and remain static at end.
    d=ImageDraw.Draw(image)
    for i in range(3):box((100+i*70,990,145+i*70,1004),accents[(i+p['seed'])%len(accents)],4)
    return image.resize(SIZE,Image.Resampling.LANCZOS)


def render_flat_vector_frame(data,frame,frames,font_path=None):
    """Return a real antialiased RGB frame with no seek-order dependency."""
    return _render(_prepare(data,frames,font_path),frame)


def render_flat_vector_frames(data,frames,output,font_path=None):
    """Write f0000.png... plus evidence into a new directory after preflight."""
    p=_prepare(data,frames,font_path);output=Path(output)
    if output.exists():raise ValueError('output must be a new directory')
    output.mkdir(parents=True);hashes=[]
    for frame in range(frames):
        path=output/f'f{frame:04d}.png';_render(p,frame).save(path)
        hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
    report={'frames':frames,'fps':30,'width':1920,'height':1080,'supersampling':S,
            'font_path':str(p['font_path']),'font_sha256':hashlib.sha256(p['font_path'].read_bytes()).hexdigest(),
            'input_sha256':hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
            'stages':p['stages'],'final_order':p['final_order'],'frame_sha256':hashes,
            'limitations':['Procedural flat vector illustrations, not borrowed character artwork.',
                           'Editorial reading time and soundtrack require separate review.']}
    (output/'evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report
