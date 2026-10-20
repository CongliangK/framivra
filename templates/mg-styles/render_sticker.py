"""Original program-drawn sticker explainer with semantic evidence and camera beats.

These are procedural cut-paper illustrations, explicitly not photographic
cutouts. Questions, decomposed parts, sourced numeric evidence and conclusions
occupy one shared wide canvas; sentence cues guide the camera before a pullout.
Default external Noto Sans CJK is SIL OFL 1.1 (notofonts/noto-cjk); not bundled.
"""
from pathlib import Path
import hashlib
import json
import math
import re

SIZE = (1920,1080)
WORLD = (5760,1080)
KINDS = ('question','decompose','evidence','conclusion')
SHAPES = ('phone','screen','battery','chip','camera','leaf','drop','gear','bottle','house')
PALETTE = ['#edf0eb','#263b40','#e77c50','#86b5ac']


def _int(value,name,low,high):
    if type(value) is not int or not low<=value<=high:
        raise ValueError(f'{name} must be an integer in [{low},{high}]')
    return value


def _string(value,name,empty=False):
    if not isinstance(value,str) or len(value)>160 or (not empty and not value.strip()):
        raise ValueError(f'{name} must be nonempty text of at most 160 characters')
    return value


def _fit(draw,text,path,face,width,maximum,minimum,lines):
    from PIL import ImageFont
    for size in range(maximum,minimum-1,-1):
        font=ImageFont.truetype(str(path),size,index=face)
        wrapped=['']
        for char in text:
            if char=='\n' or (wrapped[-1] and draw.textlength(wrapped[-1]+char,font=font)>width):
                wrapped.append('')
            if char!='\n':wrapped[-1]+=char
        if len(wrapped)<=lines:return font,wrapped
    raise ValueError('text exceeds composition region at minimum font size; shorten it')


def _prepare(data,frames,font_path=None):
    from PIL import Image,ImageDraw,ImageFont
    _int(frames,'frames',40,18000)
    if not isinstance(data,dict):raise ValueError('sticker data must be an object')
    question=data.get('question')
    if not isinstance(question,dict) or question.get('shape') not in SHAPES:
        raise ValueError(f'question needs text and shape in {SHAPES}')
    _string(question.get('text'),'question.text')
    conclusion=_string(data.get('conclusion'),'conclusion')
    title=_string(data.get('title',question['text']),'title')
    seed=_int(data.get('seed',0),'seed',-(2**31),2**31-1)
    palette=data.get('palette',PALETTE)
    if (not isinstance(palette,list) or len(palette)!=4
            or any(not isinstance(c,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',c) for c in palette)):
        raise ValueError('palette must contain four #RRGGBB colours')
    parts=data.get('parts')
    evidence=data.get('evidence')
    if not isinstance(parts,list) or not 2<=len(parts)<=4:
        raise ValueError('parts must contain 2..4 decomposed objects')
    if not isinstance(evidence,list) or not 1<=len(evidence)<=4:
        raise ValueError('evidence must contain 1..4 sourced measurements')
    ids={'question','conclusion'}
    positions={'question':(720,530),'conclusion':(5040,530)}
    prepared_parts=[]
    for i,part in enumerate(parts):
        if not isinstance(part,dict) or part.get('shape') not in SHAPES:
            raise ValueError('each part needs a supported shape')
        name=_string(part.get('id'),'part.id')
        if name in ids:raise ValueError('all node ids must be unique')
        ids.add(name)
        _string(part.get('label'),'part.label')
        angle=-math.pi/2+i/len(parts)*math.tau
        center=(2160+math.cos(angle)*350,530+math.sin(angle)*150)
        positions[name]=center
        prepared_parts.append({**part,'center':center})
    prepared_evidence=[]
    bar_units=set()
    for i,item in enumerate(evidence):
        if not isinstance(item,dict):raise ValueError('each evidence item must be an object')
        name=_string(item.get('id'),'evidence.id')
        if name in ids:raise ValueError('all node ids must be unique')
        ids.add(name)
        for key in ['label','unit','source']:_string(item.get(key),f'evidence.{key}')
        value=item.get('value')
        if type(value) not in (int,float) or not math.isfinite(value) or abs(value)>1e9:
            raise ValueError('evidence.value must be finite and within +/-1e9')
        chart=item.get('chart','bar')
        if chart not in ('bar','ring'):raise ValueError('chart must be bar or ring')
        if chart=='ring' and (item['unit']!='%' or not 0<=value<=100):
            raise ValueError('ring charts require a percentage in [0,100] and unit %')
        if chart=='bar':bar_units.add(item['unit'])
        center=(3000+(i+.5)*1200/len(evidence),530)
        positions[name]=center
        prepared_evidence.append({**item,'chart':chart,'center':center})
    if len(bar_units)>1:raise ValueError('comparative bars require one common unit')
    relations=data.get('relations')
    if not isinstance(relations,list) or not 1<=len(relations)<=16:
        raise ValueError('relations must contain 1..16 semantic edges')
    graph={name:[] for name in ids}
    for relation in relations:
        if (not isinstance(relation,dict) or relation.get('from') not in ids
                or relation.get('to') not in ids or relation['from']==relation['to']):
            raise ValueError('relations must connect two different existing node ids')
        _string(relation.get('label'),'relation.label')
        graph[relation['from']].append(relation['to'])
    reachable={'question'}
    pending=['question']
    while pending:
        for target in graph[pending.pop()]:
            if target not in reachable:reachable.add(target);pending.append(target)
    if reachable!=ids:raise ValueError('every node, including conclusion, must be reachable from question')
    beats=data.get('beats')
    if not isinstance(beats,list) or len(beats)!=4:
        raise ValueError('beats must contain four sentence cues')
    previous=0
    for i,beat in enumerate(beats):
        if not isinstance(beat,dict) or beat.get('kind')!=KINDS[i]:
            raise ValueError(f'beat order must be {KINDS}')
        start=_int(beat.get('in'),'beat.in',0,frames-10)
        end=_int(beat.get('out'),'beat.out',start+10,frames)
        if start!=previous:raise ValueError('beats must be contiguous and begin at frame zero')
        _string(beat.get('caption'),'beat.caption')
        previous=end
    if previous!=frames:raise ValueError('beats must cover all frames')
    path=Path(font_path) if font_path else Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc')
    if not path.is_file():raise ValueError('provide a separately licensed local CJK-capable font_path')
    face=2 if path.name=='NotoSansCJK-Bold.ttc' else 0
    draw=ImageDraw.Draw(Image.new('RGB',(1,1)))
    fonts={'title':_fit(draw,title,path,face,1660,70,36,1),
           'question':_fit(draw,question['text'],path,face,1180,66,30,2),
           'conclusion':_fit(draw,conclusion,path,face,1160,56,30,3)}
    strings=[title,question['text'],conclusion]
    for part in prepared_parts:
        part['text_style']=_fit(draw,part['label'],path,face,360,28,24,2)
        strings.append(part['label'])
    width=1100/len(evidence)
    for item in prepared_evidence:
        item['label_style']=_fit(draw,item['label'],path,face,width,36,24,2)
        item['value_style']=_fit(draw,_number(item['value'])+item['unit'],path,face,width,56,24,1)
        item['source_style']=_fit(draw,item['source'],path,face,width,25,20,2)
        strings.extend([item['label'],_number(item['value'])+item['unit'],item['source']])
    for beat in beats:
        fonts[beat['kind']+'_caption']=_fit(draw,beat['caption'],path,face,1640,42,28,2)
        strings.append(beat['caption'])
    relation_styles=[]
    for relation in relations:
        # Relation text is rendered inside its leader path; overflow is not truncated.
        relation_text=_string(relation['label'],'relation.label')
        relation_styles.append(_fit(draw,relation_text,path,face,300,26,20,2))
        strings.append(relation_text)
    glyph_font=ImageFont.truetype(str(path),24,index=face)
    tofu=glyph_font.getmask('\U0010ffff'); missing=(tofu.size,bytes(tofu))
    for char in set(''.join(strings)):
        if char.isspace():continue
        glyph=glyph_font.getmask(char)
        if (glyph.size,bytes(glyph))==missing:
            raise ValueError(f'font lacks glyph U+{ord(char):04X}; supply a covering font')
    return {'title':title,'question':question,'parts':prepared_parts,'evidence':prepared_evidence,
            'conclusion':conclusion,'relations':relations,'positions':positions,'beats':beats,
            'palette':palette,'seed':seed,'frames':frames,'font_path':path,'fonts':fonts,
            'relation_styles':relation_styles}


def _number(value):
    return str(int(value)) if value==int(value) else f'{value:g}'


def _ease(x):
    x=max(0,min(1,x));return x*x*(3-2*x)


def _progress(beat,frame):
    return max(0,min(1,(frame-beat['in'])/max(1,beat['out']-beat['in']-1)))


def _camera(prepared,frame):
    beat=next(b for b in prepared['beats'] if b['in']<=frame<b['out'])
    i=KINDS.index(beat['kind']);q=_progress(beat,frame)
    current=720+i*1440;previous=720+max(0,i-1)*1440
    arrival=_ease(q/.34)
    center=previous+(current-previous)*arrival
    zoom=1.16
    if i==3:
        pull=_ease((q-.58)/.4)
        center=center*(1-pull)+2880*pull
        zoom=1.16*(1-pull)+pull/3
    return center,540,zoom,beat


def _text(draw,style,center,y,color):
    font,lines=style
    for i,value in enumerate(lines):
        draw.text((round(center),round(y+i*(font.size+9))),value,font=font,fill=color,anchor='mt')


def _icon(shape,size,color,ink):
    """Return an original vector-like sticker with thick white paper outline."""
    from PIL import Image,ImageDraw,ImageFilter
    n=size*2+70;center=n//2
    mask=Image.new('L',(n,n));d=ImageDraw.Draw(mask)
    def box(x1,y1,x2,y2):return tuple(round(center+v*size) for v in (x1,y1,x2,y2))
    if shape in ('phone','screen'):
        d.rounded_rectangle(box(-.54,-.9,.54,.9),radius=round(size*.12),fill=255)
    elif shape=='battery':
        d.rounded_rectangle(box(-.48,-.72,.48,.78),radius=12,fill=255);d.rectangle(box(-.2,-.85,.2,-.7),fill=255)
    elif shape=='chip':
        d.rounded_rectangle(box(-.62,-.62,.62,.62),radius=12,fill=255)
        for i in range(4):
            a=-.45+i*.3
            d.rectangle(box(a,-.84,a+.12,-.55),fill=255);d.rectangle(box(a,.55,a+.12,.84),fill=255)
            d.rectangle(box(-.84,a,-.55,a+.12),fill=255);d.rectangle(box(.55,a,.84,a+.12),fill=255)
    elif shape=='camera':
        d.rounded_rectangle(box(-.85,-.52,.85,.62),radius=16,fill=255);d.rectangle(box(-.55,-.72,-.1,-.45),fill=255)
    elif shape=='leaf':
        d.polygon([(center-size,center+round(size*.5)),(center-round(size*.6),center-round(size*.5)),(center+size,center-size),(center+round(size*.6),center+round(size*.5)),(center-round(size*.1),center+round(size*.8))],fill=255)
    elif shape=='drop':
        points=[]
        for i in range(61):
            a=i/60*math.tau;radius=.65*(1-.38*math.sin(a));points.append((round(center+math.cos(a)*size*radius),round(center+math.sin(a)*size*radius)))
        d.polygon(points,fill=255);d.polygon([(center,center-size),(center-round(size*.47),center),(center+round(size*.47),center)],fill=255)
    elif shape=='gear':
        points=[]
        for i in range(32):
            a=i/32*math.tau;r=size*(.9 if i%4 in (1,2) else .68);points.append((round(center+math.cos(a)*r),round(center+math.sin(a)*r)))
        d.polygon(points,fill=255)
    elif shape=='bottle':
        d.rounded_rectangle(box(-.42,-.55,.42,.9),radius=20,fill=255);d.rectangle(box(-.2,-.9,.2,-.45),fill=255)
    else:
        d.polygon([(center-size,center),(center,center-size),(center+size,center),(center+round(size*.76),center),(center+round(size*.76),center+size),(center-round(size*.76),center+size),(center-round(size*.76),center)],fill=255)
    result=Image.new('RGBA',(n,n))
    white=mask.filter(ImageFilter.MaxFilter(19))
    shadow=Image.new('RGBA',(n,n),(0,0,0,45));shadow.putalpha(white.filter(ImageFilter.GaussianBlur(9)).point(lambda x:x//4))
    result.alpha_composite(shadow,(0,7))
    result.paste('#ffffff',(0,0,n,n),white)
    result.paste(color,(0,0,n,n),mask)
    detail=ImageDraw.Draw(result)
    width=max(3,size//35)
    if shape in ('phone','screen'):
        detail.rounded_rectangle(box(-.39,-.67,.39,.58),radius=8,outline=ink,width=width)
        detail.line(box(-.15,-.78,.15,-.78),fill=ink,width=width)
        detail.ellipse(box(-.07,.7,.07,.84),fill=ink)
    elif shape=='battery':
        detail.line([box(-.1,-.35,.1,-.35)[:2],box(.1,-.35,.1,-.35)[:2]],fill=ink,width=width)
        detail.line((center,center-round(size*.5),center,center-round(size*.2)),fill=ink,width=width)
        detail.line((center-round(size*.17),center+round(size*.35),center+round(size*.17),center+round(size*.35)),fill=ink,width=width)
    elif shape in ('camera','gear'):
        detail.ellipse(box(-.37,-.37,.37,.37),fill=ink)
        detail.ellipse(box(-.22,-.22,.22,.22),fill=color)
    elif shape=='chip':detail.rectangle(box(-.37,-.37,.37,.37),outline=ink,width=width)
    elif shape=='leaf':detail.line((center-round(size*.65),center+round(size*.4),center+round(size*.6),center-round(size*.65)),fill=ink,width=width)
    elif shape=='bottle':detail.rectangle(box(-.32,-.1,.32,.4),outline=ink,width=width)
    return result


def _paste_icon(world,shape,center,size,color,ink,progress=1):
    if progress<=0:return
    image=_icon(shape,size,color,ink)
    if progress<1:
        from PIL import Image
        factor=.3+.7*_ease(progress)
        image=image.resize((max(1,round(image.width*factor)),max(1,round(image.height*factor))),Image.Resampling.BICUBIC)
    world.alpha_composite(image,(round(center[0]-image.width/2),round(center[1]-image.height/2)))


def _world(prepared,frame):
    from PIL import Image,ImageDraw
    bg,ink,accent,second=prepared['palette']
    world=Image.new('RGBA',WORLD,bg)
    d=ImageDraw.Draw(world)
    for i in range(650):
        value=(prepared['seed'] ^ (i*2654435761)) & 0xffffffff
        d.point((value%5760,180+(value//5760)%710),fill='#dce2dc')
    # The graph resolves during the overview, keeping closeup evidence unobscured.
    relation_alpha=_ease((_progress(prepared['beats'][3],frame)-.58)/.23) if frame>=prepared['beats'][3]['in'] else 0
    def blend(target):
        return tuple(round(int(bg[j:j+2],16)*(1-relation_alpha)+int(target[j:j+2],16)*relation_alpha) for j in (1,3,5))
    relation_color=blend(second); relation_ink=blend(ink)
    # All relation arrows are in this same world coordinate system.
    for i,relation in enumerate(prepared['relations'] if relation_alpha>0 else []):
        a=prepared['positions'][relation['from']];b=prepared['positions'][relation['to']]
        mid=((a[0]+b[0])/2,min(780,max(a[1],b[1])+140+(i%3)*35))
        points=[]
        for j in range(33):
            u=j/32
            points.append((round((1-u)**2*a[0]+2*(1-u)*u*mid[0]+u*u*b[0]),round((1-u)**2*a[1]+2*(1-u)*u*mid[1]+u*u*b[1])))
        d.line(points,fill=relation_color,width=5,joint='curve')
        tip=points[-1];angle=math.atan2(tip[1]-points[-2][1],tip[0]-points[-2][0])
        d.line((tip[0]-round(math.cos(angle-.5)*17),tip[1]-round(math.sin(angle-.5)*17),tip[0],tip[1],tip[0]-round(math.cos(angle+.5)*17),tip[1]-round(math.sin(angle+.5)*17)),fill=relation_color,width=5)
        # Relation labels are placed along the arc, not detached in a separate list.
        text_font,lines=prepared['relation_styles'][i]
        _text(d,(text_font,lines),points[16][0],points[16][1]+12,relation_ink)
    _text(d,prepared['fonts']['question'],720,180,ink)
    _paste_icon(world,prepared['question']['shape'],(720,560),205,accent,ink)
    # An exploded assembly maps labels and connectors to real interchangeable parts.
    decomp=prepared['beats'][1];p=_progress(decomp,frame)
    for i,part in enumerate(prepared['parts']):
        center=part['center'];phase=max(0,min(1,(p-i*.11)/.32)) if frame>=decomp['in'] else 0
        d.line((2160,570,round(center[0]),round(center[1])),fill=ink,width=3)
        _paste_icon(world,part['shape'],center,105,accent if i%2==0 else second,ink,phase)
        label_y=center[1]+120
        label_height=len(part['text_style'][1])*(part['text_style'][0].size+9)
        d.rectangle((round(center[0]-190),round(label_y-7),round(center[0]+190),round(label_y+label_height+7)),fill=bg)
        _text(d,part['text_style'],center[0],label_y,ink)
    d.ellipse((2146,556,2174,584),fill=ink)
    evidence_beat=prepared['beats'][2];grow=_ease(_progress(evidence_beat,frame)/.65) if frame>=evidence_beat['in'] else 0
    maximum=max([1]+[abs(item['value']) for item in prepared['evidence'] if item['chart']=='bar'])
    for item in prepared['evidence']:
        x=item['center'][0]
        _text(d,item['label_style'],x,190,ink)
        if item['chart']=='bar':
            baseline=590;bar=abs(item['value'])/maximum*200*grow
            d.line((round(x-100),baseline,round(x+100),baseline),fill=ink,width=3)
            top=baseline-bar if item['value']>=0 else baseline
            bottom=baseline if item['value']>=0 else baseline+bar
            d.rectangle((round(x-55),round(top),round(x+55),round(bottom)),fill=accent)
        else:
            box=(round(x-135),385,round(x+135),655)
            d.arc(box,-90,270,fill=second,width=22)
            if item['value']>0:d.arc(box,-90,-90+item['value']*3.6*grow,fill=accent,width=22)
        displayed=item['value'] if grow>=1 else round(item['value']*grow)
        _text(d,(item['value_style'][0],[_number(displayed)+item['unit']]),x,300 if item['chart']=='bar' else 720,ink)
        _text(d,item['source_style'],x,800,ink)
    _paste_icon(world,prepared['question']['shape'],(5040,470),180,second,ink)
    # A hand-drawn check changes the conclusion asset's visual role.
    d.line((4960,470,5025,535,5140,385),fill=ink,width=17,joint='curve')
    _text(d,prepared['fonts']['conclusion'],5040,670,ink)
    return world.convert('RGB')


def _render(prepared,frame):
    from PIL import Image,ImageDraw
    _int(frame,'frame',0,prepared['frames']-1)
    cx,cy,zoom,beat=_camera(prepared,frame)
    world=_world(prepared,frame)
    image=world.transform(SIZE,Image.Transform.AFFINE,(1/zoom,0,cx-960/zoom,0,1/zoom,cy-540/zoom),resample=Image.Resampling.BICUBIC,fillcolor=prepared['palette'][0])
    draw=ImageDraw.Draw(image)
    bg,ink,accent,second=prepared['palette']
    # Screen captions retain their reading size while the shared world pulls out.
    draw.rectangle((0,0,1920,96),fill=bg)
    draw.rectangle((0,920,1920,1080),fill=bg)
    _text(draw,prepared['fonts']['title'],960,14,ink)
    _text(draw,prepared['fonts'][beat['kind']+'_caption'],960,945,ink)
    for i,kind in enumerate(KINDS):
        x=170+i*410
        draw.rounded_rectangle((x,1050,x+350,1057),radius=3,fill=accent if i<=KINDS.index(beat['kind']) else second)
    return image


def validate_sticker_content(data,frames,font_path=None):
    """Pure schema/text/font preflight; no output directories are created."""
    import PIL
    prepared=_prepare(data,frames,font_path)
    font=prepared['font_path']
    return {'frames':frames,'fps':30,'size':list(SIZE),'world_size':list(WORLD),
            'renderer_dependencies':[str(Path(__file__).resolve())],'pillow_version':PIL.__version__,
            'font_path':str(font),'font_sha256':hashlib.sha256(font.read_bytes()).hexdigest(),
            'font_source':'https://github.com/notofonts/noto-cjk' if font_path is None else 'user-supplied',
            'font_license':'SIL OFL 1.1' if font_path is None else 'verify separately',
            'artwork':'original program-drawn stickers; no photographs',
            'evidence':[{'value':x['value'],'unit':x['unit'],'source':x['source']} for x in prepared['evidence']]}


def render_sticker_frame(data,frame,frames,font_path=None):
    """Return one pure RGB1920x1080 image at an arbitrary integer frame."""
    return _render(_prepare(data,frames,font_path),frame)


def render_sticker_frames(data,frames,output,font_path=None):
    """Write f0000.png... and evidence.json into a new directory; return receipt."""
    prepared=_prepare(data,frames,font_path)
    metadata=validate_sticker_content(data,frames,font_path)
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    hashes=[]
    for frame in range(frames):
        path=output/f'f{frame:04d}.png';_render(prepared,frame).save(path)
        hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
    receipt={**metadata,'frame_sha256':hashes,'renderer_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             'input_sha256':hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()}
    (output/'evidence.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return receipt
