"""Original deterministic horizon journey, chrome lettering and event-based VHS."""
from pathlib import Path
import hashlib
import math
import re

SIZE=(1920,1080)


def _font(data,path=None):
    from PIL import ImageFont
    path=Path(path or '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc')
    if not path.is_file():raise ValueError('provide a local font covering the content')
    font=ImageFont.truetype(str(path),32)
    missing=bytes(font.getmask('\U0010ffff'))
    for text in [data['title'],data['destination']]+[p['label'] for p in data['waypoints']]:
        for char in set(text)-set(' \t'):
            mask=font.getmask(char)
            if not mask.getbbox() or bytes(mask)==missing:raise ValueError('font lacks a content glyph')
    return path


def _fit(text,width,size,path):
    from PIL import ImageFont
    for n in range(size,19,-1):
        font=ImageFont.truetype(str(path),n)
        if font.getlength(text)<=width:return font
    raise ValueError('text does not fit at minimum size 20')


def validate_synthwave_content(data,frames,font_path=None):
    if not isinstance(data,dict) or set(data)-{'title','destination','waypoints','stages','tracking','seed','palette'}:raise ValueError('unknown synthwave content')
    if type(frames) is not int or not 90<=frames<=18000:raise ValueError('frames must be integer [90,18000]')
    seed=data.get('seed',7)
    if type(seed) is not int or not -(2**31)<=seed<2**31:raise ValueError('seed must be signed32')
    for key in ('title','destination'):
        if not isinstance(data.get(key),str) or not data[key].strip() or len(data[key])>100 or any(c in data[key] for c in '\n\r\t'):raise ValueError('title/destination must be nonempty one line')
    palette=data.get('palette',['#160b32','#d936c8','#35ddf2','#ffad66'])
    if not isinstance(palette,list) or len(palette)!=4 or any(not isinstance(c,str) or not re.fullmatch('#[0-9a-fA-F]{6}',c) for c in palette):raise ValueError('palette needs four RGB colors')
    points=data.get('waypoints')
    if not isinstance(points,list) or not 2<=len(points)<=5:raise ValueError('need 2..5 waypoints')
    last=-1
    for point in points:
        if not isinstance(point,dict) or set(point)!={'frame','label','side'}:raise ValueError('waypoint requires frame,label,side')
        if type(point['frame']) is not int or not last<point['frame']<frames or point['side'] not in ('left','right'):raise ValueError('waypoints must be ordered and visible')
        if not isinstance(point['label'],str) or not point['label'].strip() or len(point['label'])>60 or any(c in point['label'] for c in '\n\r\t'):raise ValueError('waypoint needs short one-line label')
        last=point['frame']
    stages=data.get('stages');end=0
    if not isinstance(stages,list) or len(stages)!=3:raise ValueError('need approach,cross,arrive stages')
    for entry,kind in zip(stages,('approach','cross','arrive')):
        if not isinstance(entry,dict) or set(entry)!={'mode','in','out'} or entry['mode']!=kind:raise ValueError('wrong stage schema/order')
        a,b=entry['in'],entry['out']
        if type(a) is not int or type(b) is not int or a!=end or not a+20<=b<=frames:raise ValueError('stages need contiguous windows >=20frames')
        end=b
    if end!=frames or points[-1]['frame']>=stages[2]['in']:raise ValueError('journey must finish before arrival')
    events=data.get('tracking',[])
    if not isinstance(events,list):raise ValueError('tracking must be an event list')
    for event in events:
        if not isinstance(event,dict) or set(event)!={'in','out','strength'}:raise ValueError('tracking requires in,out,strength')
        a,b,s=event['in'],event['out'],event['strength']
        if type(a) is not int or type(b) is not int or not 0<=a<b<=stages[2]['in'] or type(s) is not int or not 1<=s<=20:raise ValueError('tracking events must end before readable arrival')
    result=dict(title=data['title'],destination=data['destination'],waypoints=points,stages=stages,tracking=events,seed=seed,palette=palette)
    font=_font(result,font_path)
    _fit(result['title'],1640,132,font);_fit(result['destination'],1500,86,font)
    for point in points:_fit(point['label'],400,40,font)
    return result


def journey_state(data,frame,frames):
    if type(frame) is not int or not 0<=frame<frames:raise ValueError('frame out of range')
    stage=next(s for s in data['stages'] if s['in']<=frame<s['out'])
    u=(frame-stage['in'])/max(1,stage['out']-stage['in']-1)
    # Arrival eases to a full stop and keeps its last ten frames motionless.
    arrival=data['stages'][2]['in'];stop=frames-11
    travel=frame if frame<arrival else arrival+(stop-arrival)*(1-(1-min(1,(frame-arrival)/max(1,stop-arrival)))**3)
    return {'mode':stage['mode'],'progress':u,'travel':travel,'title_reveal':min(1,frame/max(1,data['stages'][0]['out']-1))}


def _rgb(color):return tuple(int(color[i:i+2],16) for i in (1,3,5))


def render_synthwave_frame(data,frame,frames,font_path=None):
    import numpy as np
    from PIL import Image,ImageDraw,ImageFont,ImageFilter,ImageChops
    data=validate_synthwave_content(data,frames,font_path);state=journey_state(data,frame,frames);font=_font(data,font_path)
    bg,pink,cyan,orange=map(_rgb,data['palette']);horizon=580
    y=np.linspace(0,1,1080)[:,None,None];top=np.array(bg)[None,None,:];bottom=np.array((65,18,75))[None,None,:]
    sky=np.repeat((top*(1-y)+bottom*y).astype('uint8'),1920,axis=1)
    image=Image.fromarray(sky);draw=ImageDraw.Draw(image)
    # The striped disc belongs behind terrain and the road vanishing point.
    for row in range(310,650):
        dy=row-480;r=170
        if abs(dy)<r and (row<500 or row%22<13):
            dx=math.sqrt(r*r-dy*dy);draw.line((960-dx,row,960+dx,row),fill=orange,width=1)
    for layer in range(2):
        shift=state['travel']*(.25+.35*layer)
        points=[(0,horizon)]
        for x in range(-120,2100,80):
            px=x-shift%80;peak=500-60*layer-65*math.sin(x*.006+data['seed']+layer)
            points.append((px,peak))
        draw.polygon(points+[(1920,horizon)],fill=(24+layer*8,13,42+layer*8))
    draw.rectangle((0,horizon,1920,1080),fill=(11,8,31))
    glow=Image.new('RGB',SIZE);gd=ImageDraw.Draw(glow)
    for i in range(-12,13):gd.line((960,horizon,960+i*190,1080),fill=cyan,width=2)
    phase=(state['travel']*.013)%1
    for i in range(1,22):
        z=(i-phase)/22;row=horizon+(1080-horizon)*z*z
        gd.line((0,row,1920,row),fill=pink,width=2)
    for point in data['waypoints']:
        # Project a world marker; a finite visibility range prevents infinity at the camera.
        distance=(point['frame']-state['travel'])/45
        if -.25<=distance<=4:
            scale=1/(1+max(-.25,distance));side=-1 if point['side']=='left' else 1
            x=960+side*620*scale;bottom_y=horizon+330*scale;w=140*scale;height=210*scale
            gd.line((x-w,bottom_y,x-w,bottom_y-height,x+w,bottom_y-height,x+w,bottom_y),fill=cyan,width=4)
            gd.line((x-w,bottom_y-height*.65,x+w,bottom_y-height*.65),fill=pink,width=3)
            if 210<x<1710:
                f=_fit(point['label'],400,round(25+14*scale),font)
                gd.text((x,bottom_y-height-30),point['label'],font=f,fill=cyan,anchor='mm')
    image=ImageChops.add(image,glow.filter(ImageFilter.GaussianBlur(9)))
    image=ImageChops.add(image,glow);draw=ImageDraw.Draw(image)
    mask=Image.new('L',SIZE);md=ImageDraw.Draw(mask);f=_fit(data['title'],1640,132,font)
    md.text((960,180),data['title'],font=f,fill=255,anchor='mm',stroke_width=1)
    reveal=Image.new('L',SIZE);ImageDraw.Draw(reveal).rectangle((0,0,1920*state['title_reveal'],1080),fill=255);mask=ImageChops.multiply(mask,reveal)
    chrome=Image.new('RGB',SIZE);cd=ImageDraw.Draw(chrome)
    for row in range(80,285):
        t=(row-80)/205;color=tuple(round(a*(1-t)+b*t) for a,b in zip(cyan,pink)) if row>180 else tuple(round(245*(1-t)+v*t) for v in cyan)
        cd.line((0,row,1920,row),fill=color)
    halo=Image.new('RGB',SIZE);halo.paste(pink,(0,0,1920,1080));halo=ImageChops.multiply(halo,Image.merge('RGB',(mask,mask,mask))).filter(ImageFilter.GaussianBlur(12));image=ImageChops.add(image,halo);image.paste(chrome,(0,0),mask)
    draw=ImageDraw.Draw(image)
    if state['mode']=='arrive':
        draw.rounded_rectangle((170,810,1750,1010),32,fill=(15,12,35),outline=cyan,width=3)
        draw.text((960,910),data['destination'],font=_fit(data['destination'],1500,86,font),fill=cyan,anchor='mm')
    # Scan lines and gated deterministic tracking distort image, never timing or source media.
    scan=Image.new('RGBA',SIZE,(0,0,0,0));scan_draw=ImageDraw.Draw(scan)
    for row in range(0,1080,4):scan_draw.line((0,row,1920,row),fill=(8,6,22,38),width=1)
    image=Image.alpha_composite(image.convert('RGBA'),scan).convert('RGB')
    for event in data['tracking']:
        if event['in']<=frame<event['out']:
            row=280+(frame*37+data['seed'])%420;band=image.crop((0,row,1920,row+36));band=ImageChops.offset(band,event['strength']*(1 if frame%2 else -1),0);r,g,b=band.split();band=Image.merge('RGB',(ImageChops.offset(r,3,0),g,ImageChops.offset(b,-3,0)));image.paste(band,(0,row))
    return image


def render_synthwave_frames(data,frames,output,font_path=None):
    data=validate_synthwave_content(data,frames,font_path);output=Path(output)
    output.mkdir(parents=True,exist_ok=False);digest=hashlib.sha256()
    for frame in range(frames):
        image=render_synthwave_frame(data,frame,frames,font_path);image.save(output/f'f{frame:04d}.png');digest.update(image.tobytes())
    font=_font(data,font_path)
    return {'frames':frames,'size':list(SIZE),'pixel_sha256':digest.hexdigest(),'font_path':str(font),'font_sha256':hashlib.sha256(font.read_bytes()).hexdigest(),'font_license':'verify separately' if font_path else 'SIL OFL 1.1','renderer_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
