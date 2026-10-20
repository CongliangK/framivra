"""Analytic aurora, projected micro-UI cards and a dispersive refractive lens.

This is a deterministic image-space optical approximation, not path tracing.
The lens resamples the already-composited scene through a nonuniform normal
field; backdrop blur is used for frosted cards, never as a lens substitute.
"""
from pathlib import Path
import hashlib
import math
import re

MODES=('chat','voice','focus')
PALETTE=['#111226','#f3f0ff','#7161d9','#46aabf']


def _font(data,path=None):
    from PIL import ImageFont
    strings=[data['title'],data['subtitle']]+[c[k] for c in data['chapters'] for k in ('label','detail','unit')]
    text=''.join(strings)+'% 0123456789'
    candidates=[Path(path)] if path else [Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'),Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    for candidate in candidates:
        if candidate.is_file():
            face=ImageFont.truetype(str(candidate),40)
            missing=face.getmask(chr(0x10ffff));sentinel=(missing.size,bytes(missing))
            for char in set(text):
                if not char.isspace():
                    glyph=face.getmask(char)
                    if (glyph.size,bytes(glyph))==sentinel:raise ValueError(f'font lacks required glyph U+{ord(char):04X}')
            return candidate
    raise ValueError('provide a separately licensed local font_path supporting all content')


def _fit(text,width,size,font,minimum=14):
    from PIL import Image,ImageDraw,ImageFont
    draw=ImageDraw.Draw(Image.new('L',(1,1)))
    while True:
        face=ImageFont.truetype(str(font),size)
        if draw.textlength(text,font=face)<=width:return face
        if size<=minimum:raise ValueError('text does not fit its reading zone at minimum font size')
        size-=1


def validate_aurora_glass_content(data,frames,font_path=None):
    """Preflight all semantic windows, optics, glyph coverage and text without writes."""
    import colorsys
    if not isinstance(data,dict) or set(data)-{'title','subtitle','chapters','palette','seed','lens'}:raise ValueError('unknown aurora-glass content key')
    if type(frames) is not int or not 90<=frames<=18000:raise ValueError('frames must be integer in [90,18000]')
    title,subtitle=data.get('title','Clarity in motion'),data.get('subtitle','A little space for your next idea')
    if any(not isinstance(t,str) or len(t)>120 or '\n' in t or '\r' in t for t in (title,subtitle)):raise ValueError('title/subtitle must be single-line text up to 120 characters')
    palette=data.get('palette',PALETTE)
    if not isinstance(palette,list) or len(palette)!=4 or any(not isinstance(c,str) or not re.fullmatch(r'#[a-fA-F0-9]{6}',c) for c in palette):raise ValueError('palette must contain four #RRGGBB colors')
    a,b=[colorsys.rgb_to_hsv(*(int(c[i:i+2],16)/255 for i in (1,3,5)))[0] for c in palette[2:]]
    if min(abs(a-b),1-abs(a-b))>1/3:raise ValueError('aurora glow colors must use adjacent hues within 120 degrees')
    seed=data.get('seed',0)
    if type(seed) is not int or not -(2**31)<=seed<2**31:raise ValueError('seed must be a signed 32-bit integer')
    lens=data.get('lens',{})
    if not isinstance(lens,dict) or set(lens)-{'strength','dispersion','radius'}:raise ValueError('lens supports strength,dispersion,radius')
    optics=dict(strength=lens.get('strength',.24),dispersion=lens.get('dispersion',.7),radius=lens.get('radius',165))
    for key,lo,hi in (('strength',.05,.4),('dispersion',.1,1.5),('radius',100,210)):
        value=optics[key]
        if type(value) not in (int,float) or not math.isfinite(value) or not lo<=value<=hi:raise ValueError(f'lens {key} must be finite in [{lo},{hi}]')
    chapters=data.get('chapters')
    if not isinstance(chapters,list) or not 2<=len(chapters)<=4:raise ValueError('chapters must contain two to four semantic micro-UI scenes')
    normalized=[];previous=0
    for chapter in chapters:
        if not isinstance(chapter,dict) or set(chapter)!={'in','out','mode','label','detail','value','unit'}:raise ValueError('chapter needs in,out,mode,label,detail,value,unit')
        a,b=chapter['in'],chapter['out']
        if type(a) is not int or type(b) is not int or a!=previous or not a+30<=b<=frames:raise ValueError('chapters must be contiguous integer windows of at least thirty frames')
        if not isinstance(chapter['mode'],str) or chapter['mode'] not in MODES:raise ValueError('micro-UI mode must be chat, voice or focus')
        for key,limit in (('label',40),('detail',70),('unit',12)):
            text=chapter[key]
            if not isinstance(text,str) or not text.strip() or len(text)>limit or '\n' in text or '\r' in text:raise ValueError(f'chapter {key} must be nonempty single-line text up to {limit} characters')
        value=chapter['value']
        if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=100:raise ValueError('chapter value must be finite in [0,100]')
        normalized.append(dict(chapter));previous=b
    if previous!=frames:raise ValueError('chapters must cover all frames')
    result=dict(title=title,subtitle=subtitle,palette=list(palette),seed=seed,lens=optics,chapters=normalized)
    font=_font(result,font_path);_fit(title,1620,80,font);_fit(subtitle,1600,31,font)
    for chapter in normalized:
        _fit(chapter['label'],570,38,font);_fit(chapter['detail'],570,27,font)
        _fit(str(round(chapter['value']))+' '+chapter['unit'],520,48,font)
        # Far cards are independently readable, not clipped miniature copies.
        _fit(chapter['label'],290,28,font);_fit(chapter['detail'],290,20,font)
    return result


def _rgb(color):return tuple(int(color[i:i+2],16) for i in (1,3,5))


def aurora_field(data,frame,frames,size=(960,540)):
    """Evaluate smooth Gaussian light blobs and deterministic fixed grain."""
    import numpy as np
    from PIL import Image
    w,h=size;yy,xx=np.mgrid[0:h,0:w].astype('float32');x=xx/w;y=yy/h
    phase=frame/max(1,frames-1);bias=(data['seed']%97)/97*math.tau
    base=np.array(_rgb(data['palette'][0]),dtype='float32');glows=[np.array(_rgb(c),dtype='float32') for c in data['palette'][2:]]
    image=np.broadcast_to(base,(h,w,3)).copy()
    for i in range(4):
        cx=.2+.6*(.5+.5*math.sin(phase*1.4+bias+i*1.9))
        cy=.25+.5*(.5+.5*math.cos(phase*1.1+bias*.6+i*2.1))
        radius=.19+i*.025
        intensity=np.exp(-((x-cx)**2+(y-cy)**2)/(2*radius*radius))*.44
        image+=intensity[:,:,None]*(glows[i%2]-base)*.85
    # Stable spatial dither; never a frame-random flicker texture.
    hashed=(xx.astype('uint32')*73856093)^(yy.astype('uint32')*19349663)^np.uint32(data['seed']&0xffffffff)
    hashed^=hashed>>13;noise=(hashed%257).astype('float32')/256-.5
    image+=noise[:,:,None]*5
    return Image.fromarray(np.clip(image,0,255).astype('uint8')).resize((1920,1080),Image.Resampling.BICUBIC)


def card_corners(cx,cy,width,height,yaw,pitch,depth=0):
    """Perspective projection of a real tilted plane, not an affine skew."""
    corners=[];focal=1200
    for x,y in ((-width/2,-height/2),(width/2,-height/2),(width/2,height/2),(-width/2,height/2)):
        rx=x*math.cos(yaw);rz=-x*math.sin(yaw)
        ry=y*math.cos(pitch)-rz*math.sin(pitch);rz=y*math.sin(pitch)+rz*math.cos(pitch)+depth
        factor=focal/(focal+rz)
        corners.append((cx+rx*factor,cy+ry*factor))
    return corners


def _warp_card(card,corners):
    import numpy as np
    from PIL import Image
    x0=max(0,math.floor(min(x for x,y in corners))-2);y0=max(0,math.floor(min(y for x,y in corners))-2)
    x1=min(1920,math.ceil(max(x for x,y in corners))+2);y1=min(1080,math.ceil(max(y for x,y in corners))+2)
    dst=[(x-x0,y-y0) for x,y in corners];src=[(0,0),(card.width,0),(card.width,card.height),(0,card.height)]
    matrix=[];target=[]
    for (x,y),(u,v) in zip(dst,src):
        matrix.extend([[x,y,1,0,0,0,-u*x,-u*y],[0,0,0,x,y,1,-v*x,-v*y]]);target.extend([u,v])
    coeff=np.linalg.solve(np.array(matrix),np.array(target))
    warped=card.transform((x1-x0,y1-y0),Image.Transform.PERSPECTIVE,coeff,Image.Resampling.BICUBIC)
    return warped,(x0,y0)


def micro_ui_state(chapter,frame):
    """Finish counting/toggles before the exclusive end, with a readable tail hold."""
    local=max(0,min(chapter['out']-chapter['in']-1,frame-chapter['in']))
    duration=chapter['out']-chapter['in']
    complete=max(1,duration-1-6)
    count_end=min(30,complete)
    return dict(entry=min(1,local/min(14,max(1,duration//3))),
                displayed=round(chapter['value']*min(1,local/count_end)),
                enabled=[local>=max(1,round(complete*(i+1)/3)) for i in range(3)])


def _card(scene,chapter,frame,frames,font,center,width,height,yaw,pitch,depth,small=False):
    from PIL import Image,ImageDraw,ImageFilter
    cx,cy=center
    # Frosted backdrop plus white scatter on a rounded slab.
    crop=scene.crop((round(cx-width/2),round(cy-height/2),round(cx+width/2),round(cy+height/2))).resize((width,height)).filter(ImageFilter.GaussianBlur(22))
    card=crop.convert('RGBA');tint=Image.new('RGBA',card.size,(255,255,255,20));card=Image.alpha_composite(card,tint)
    mask=Image.new('L',card.size,0);md=ImageDraw.Draw(mask);md.rounded_rectangle((1,1,width-2,height-2),radius=30,fill=232)
    card.putalpha(mask);draw=ImageDraw.Draw(card);draw.rounded_rectangle((1,1,width-2,height-2),radius=30,outline=(255,255,255,100),width=2)
    text_color='#fffaff';pad=30 if small else 45
    draw.text((pad,30),chapter['label'],font=_fit(chapter['label'],width-2*pad,28 if small else 38,font),fill=text_color)
    if small:
        draw.text((pad,90),chapter['detail'],font=_fit(chapter['detail'],width-2*pad,20,font),fill=text_color)
        draw.line((pad,height-55,width-pad,height-55),fill=(255,255,255,80),width=2)
    else:
        local=frame-chapter['in'];state=micro_ui_state(chapter,frame);entry=state['entry']
        ui=Image.new('RGBA',card.size,(0,0,0,0));d=ImageDraw.Draw(ui);rise=round(8*(1-entry));mode=chapter['mode']
        d.text((pad,95+rise),chapter['detail'],font=_fit(chapter['detail'],width-2*pad,27,font),fill=text_color)
        if mode=='chat':
            d.rounded_rectangle((pad,155+rise,width-pad-55,245+rise),radius=22,fill=(255,255,255,30),outline=(255,255,255,65),width=1)
            d.text((pad+24,170+rise),str(state['displayed'])+' '+chapter['unit'],font=_fit(str(round(chapter['value']))+' '+chapter['unit'],width-2*pad-90,43,font),fill=text_color)
            for i in range(3):
                bounce=round(3*math.sin(frame*.09-i*.7));x=width-150+i*24
                d.ellipse((x,285+bounce,x+9,294+bounce),fill=(255,255,255,200))
        elif mode=='voice':
            for i in range(45):
                x=pad+i*(width-2*pad)/44
                wave=(.25+.75*abs(math.sin(i*.63+frame*.07)))*chapter['value']/100
                amp=12+55*wave
                d.rounded_rectangle((round(x),round(220-amp+rise),round(x+5),round(220+amp+rise)),radius=2,fill=(235,241,255,215))
            d.text((pad,310),str(round(chapter['value']))+' '+chapter['unit'],font=_fit(str(round(chapter['value']))+' '+chapter['unit'],width-2*pad,35,font),fill=text_color)
        else:
            for i in range(3):
                y=155+i*67+rise;enabled=state['enabled'][i]
                d.rounded_rectangle((pad,y,width-pad,y+44),radius=14,fill=(255,255,255,18))
                d.line((pad+18,y+22,pad+180,y+22),fill=(255,255,255,140),width=4)
                d.rounded_rectangle((width-pad-84,y+8,width-pad-16,y+36),radius=14,fill=(170,235,224,160) if enabled else (255,255,255,45))
                px=width-pad-(32 if enabled else 68);d.ellipse((px-9,y+13,px+9,y+31),fill=(255,255,255,235))
            d.text((pad,375),str(round(chapter['value']))+' '+chapter['unit'],font=_fit(str(round(chapter['value']))+' '+chapter['unit'],width-2*pad,30,font),fill=text_color)
        ui.putalpha(ui.getchannel('A').point(lambda v:round(v*entry)));card=Image.alpha_composite(card,ui)
    warped,position=_warp_card(card,card_corners(cx,cy,width,height,yaw,pitch,depth));scene.paste(warped,position,warped)


def lens_coordinates(width,height,center,radius,strength,dispersion,phase=0):
    """Return per-channel refractive coordinates and coverage for the lens patch."""
    import numpy as np
    cx,cy=center;x0=max(0,math.floor(cx-radius-2));x1=min(width,math.ceil(cx+radius+2));y0=max(0,math.floor(cy-radius-2));y1=min(height,math.ceil(cy+radius+2))
    yy,xx=np.mgrid[y0:y1,x0:x1].astype('float32');u=(xx-cx)/radius;v=(yy-cy)/radius;r2=u*u+v*v
    coverage=np.clip((1-np.sqrt(r2))*radius/1.5,0,1)
    radial=np.sqrt(np.maximum(0,1-r2))
    # Analytic normals: curved dome plus a small asymmetric ripple in its thickness.
    nx=u*radial+.055*np.sin(v*4+phase)*radial;ny=v*radial+.04*np.sin(u*5-phase)*radial
    bend_x=-strength*radius*nx;bend_y=-strength*radius*ny
    rim=np.exp(-((np.sqrt(r2)-.88)/.12)**2)*dispersion*radius*.026
    coords=[]
    for wavelength in (1,0,-1):
        coords.append((np.clip(xx+bend_x+wavelength*rim*u,0,width-1),np.clip(yy+bend_y+wavelength*rim*v,0,height-1)))
    return (x0,y0,x1,y1),coords,coverage,(u,v,r2)


def apply_lens(image,center,radius,strength,dispersion,phase=0):
    import numpy as np
    from PIL import Image
    source=np.asarray(image,dtype='float32');height,width=source.shape[:2]
    box,coords,coverage,geometry=lens_coordinates(width,height,center,radius,strength,dispersion,phase)
    x0,y0,x1,y1=box;sample=np.empty((y1-y0,x1-x0,3),dtype='float32')
    for channel,(x,y) in enumerate(coords):
        ix=x.astype('int32');iy=y.astype('int32');fx=x-ix;fy=y-iy
        jx=np.minimum(ix+1,width-1);jy=np.minimum(iy+1,height-1)
        sample[:,:,channel]=(source[iy,ix,channel]*(1-fx)*(1-fy)+source[iy,jx,channel]*fx*(1-fy)+source[jy,ix,channel]*(1-fx)*fy+source[jy,jx,channel]*fx*fy)
    u,v,r2=geometry;spec=np.exp(-((np.sqrt(r2)-.965)/.022)**2)*np.clip(.7-u*.4-v*.6,0,1)*80
    sample+=spec[:,:,None]
    output=source.copy();base=source[y0:y1,x0:x1]
    output[y0:y1,x0:x1]=base*(1-coverage[:,:,None])+sample*coverage[:,:,None]
    return Image.fromarray(np.clip(output,0,255).astype('uint8'))


def render_aurora_glass_frame(data,frame,frames,font_path=None):
    from PIL import ImageDraw
    data=validate_aurora_glass_content(data,frames,font_path)
    if type(frame) is not int or not 0<=frame<frames:raise ValueError('frame must be integer in [0,frames)')
    font=_font(data,font_path);phase=frame/max(1,frames-1)
    index=next(i for i,c in enumerate(data['chapters']) if c['in']<=frame<c['out']);chapter=data['chapters'][index]
    scene=aurora_field(data,frame,frames);drift=math.sin(phase*math.pi)*28
    left=data['chapters'][max(0,index-1)];right=data['chapters'][min(len(data['chapters'])-1,index+1)]
    _card(scene,left,frame,frames,font,(470+drift*.55,610),360,235,-.18,.10,140,True)
    _card(scene,chapter,frame,frames,font,(960+drift*.8,575),700,440,.10*math.sin(phase*2.2+.3),-.055,0)
    _card(scene,right,frame,frames,font,(1490+drift,660),350,230,.20,-.08,-45,True)
    lens=data['lens'];cx=530+910*(.5-.5*math.cos(math.pi*phase));cy=590+85*math.sin(phase*math.pi*1.4)
    scene=apply_lens(scene,(cx,cy),lens['radius'],lens['strength'],lens['dispersion'],phase*2)
    draw=ImageDraw.Draw(scene);ink=data['palette'][1]
    draw.text((960,125),data['title'],font=_fit(data['title'],1620,80,font),fill=ink,anchor='mm')
    draw.text((960,210),data['subtitle'],font=_fit(data['subtitle'],1600,31,font),fill=ink,anchor='mm')
    draw.text((960,940),chapter['label'],font=_fit(chapter['label'],1400,42,font),fill=ink,anchor='mm')
    for i in range(len(data['chapters'])):
        x=960+(i-(len(data['chapters'])-1)/2)*110;draw.rounded_rectangle((x-25,1000,x+25,1004),radius=2,fill=ink if i==index else data['palette'][2])
    return scene


def render_aurora_glass_frames(data,frames,output,font_path=None):
    data=validate_aurora_glass_content(data,frames,font_path)
    output=Path(output)
    if output.exists():raise ValueError('output must be a new directory')
    output.mkdir(parents=True);digest=hashlib.sha256()
    for frame in range(frames):
        image=render_aurora_glass_frame(data,frame,frames,font_path);image.save(output/f'f{frame:04d}.png');digest.update(image.tobytes())
    font=_font(data,font_path)
    return dict(frames=frames,width=1920,height=1080,pixel_sha256=digest.hexdigest(),chapters=data['chapters'],optics='analytic-nonuniform-image-space-refraction-with-dispersion',font_path=str(font),font_sha256=hashlib.sha256(font.read_bytes()).hexdigest())
