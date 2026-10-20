"""Original modular Bauhaus choreography, using a shared integer-frame beat table.

Research: mg-styles-15/09-bauhaus's published prompt, film.js and cues.json.
This independently implemented CPU renderer borrows general grid/pivot principles,
not upstream artwork, typography outlines, shader code or fixed film composition.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re

SIZE = (1920, 1080)
PALETTE = ['#f1e9da', '#111111', '#df3b30', '#f2b705', '#2454a5']
SHAPES = ('circle', 'square', 'triangle', 'semicircle', 'quarter', 'bar')
AXES = ('center', 'corner', 'frame', 'alternate')
ARENA = (96, 220, 1824, 880)


def _int(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer in [{low},{high}]')
    return value


def _text(value, name, limit=120, empty=False):
    if not isinstance(value, str) or len(value)>limit or (not empty and not value.strip()):
        raise ValueError(name+' must be bounded nonempty text')
    return value


def _rotate(point, angle):
    x,y=point
    return (x*math.cos(angle)-y*math.sin(angle), x*math.sin(angle)+y*math.cos(angle))


def _glyph(shape, side):
    r=side/2
    if shape=='square': return [(-r,-r),(r,-r),(r,r),(-r,r)]
    if shape=='triangle': return [(-r,r),(0,-r),(r,r)]
    if shape=='bar': return [(-r,-r/3),(r,-r/3),(r,r/3),(-r,r/3)]
    if shape=='quarter':
        return [(-r,r)]+[(-r+side*math.cos(-math.pi/2+i/48*math.pi/2),r+side*math.sin(-math.pi/2+i/48*math.pi/2)) for i in range(49)]
    if shape=='semicircle':
        return [(r*math.cos(i/48*math.pi),r*math.sin(i/48*math.pi)) for i in range(49)]
    return [(r*math.cos(i/96*math.tau),r*math.sin(i/96*math.tau)) for i in range(96)]


def _center(prepared, module, placement):
    size=module['span']*prepared['unit']
    return (prepared['origin'][0]+placement['cell'][0]*prepared['unit']+size/2,
            prepared['origin'][1]+placement['cell'][1]*prepared['unit']+size/2)


def _motion(prepared, module, before, after, axis):
    start,end=_center(prepared,module,before),_center(prepared,module,after)
    angle0=before['turn']*math.pi/2
    delta=((after['turn']-before['turn']+2)%4-2)*math.pi/2
    pivot=start if axis=='center' else (start[0]-module['span']*prepared['unit']/2,start[1]-module['span']*prepared['unit']/2) if axis=='corner' else prepared['frame_center']
    relative=(start[0]-pivot[0],start[1]-pivot[1])
    at_end=_rotate(relative,delta)
    correction=(end[0]-pivot[0]-at_end[0],end[1]-pivot[1]-at_end[1])
    return pivot,relative,correction,angle0,delta


def _points(prepared, module, before, after, axis, progress):
    pivot,relative,correction,angle0,delta=_motion(prepared,module,before,after,axis)
    result=[]
    for point in module['polygon']:
        local=_rotate(point,angle0)
        turned=_rotate((relative[0]+local[0],relative[1]+local[1]),delta*progress)
        result.append((pivot[0]+turned[0]+correction[0]*progress,
                       pivot[1]+turned[1]+correction[1]*progress))
    return result


def _roots(u,v,k,delta):
    radius=math.hypot(u,v)
    if abs(delta)<1e-12 or radius<1e-12 or abs(k)>radius+1e-9: return []
    alpha=math.asin(max(-1,min(1,k/radius)))
    phi=math.atan2(v,u)
    roots=[]
    for base in (alpha-phi,math.pi-alpha-phi):
        for winding in range(-3,4):
            q=(base+math.tau*winding)/delta
            if 0<q<1: roots.append(q)
    return roots


def _motion_bounds(prepared,module,before,after,axis):
    """Exact extrema of each polygon vertex's rotated-plus-linear trajectory."""
    pivot,relative,correction,angle0,delta=_motion(prepared,module,before,after,axis)
    vertices=[]
    for point in module['polygon']:
        local=_rotate(point,angle0)
        a,b=relative[0]+local[0],relative[1]+local[1]
        values=[0,1]
        if abs(delta)>1e-12:
            values+=_roots(a,b,correction[0]/delta,delta)
            values+=_roots(-b,a,-correction[1]/delta,delta)
        for q in values:
            x,y=_rotate((a,b),delta*q)
            vertices.append((pivot[0]+x+correction[0]*q,pivot[1]+y+correction[1]*q))
    return (min(x for x,y in vertices),min(y for x,y in vertices),max(x for x,y in vertices),max(y for x,y in vertices))


def _fit(text,font_path,face,size,width,lines,minimum=24):
    from PIL import ImageFont
    for pixels in range(size,minimum-1,-2):
        font=ImageFont.truetype(str(font_path),pixels,index=face)
        result=['']
        for char in text:
            if char=='\n' or (result[-1] and font.getlength(result[-1]+char)>width): result.append('')
            if char!='\n': result[-1]+=char
        if len(result)<=lines:return font,result
    raise ValueError('text cannot fit its fixed typography region; shorten it')


def _prepare(data,frames,font_path=None):
    _int(frames,'frames',30,18000)
    if not isinstance(data,dict):raise ValueError('bauhaus content must be an object')
    try:json.dumps(data,sort_keys=True,allow_nan=False)
    except (TypeError,ValueError) as error:raise ValueError('content must be finite JSON data') from error
    data=deepcopy(data)
    title=_text(data.get('title'),'title')
    subtitle=_text(data.get('subtitle',''),'subtitle',empty=True)
    seed=_int(data.get('seed',0),'seed',-(2**31),2**31-1)
    palette=data.get('palette',PALETTE)
    if not isinstance(palette,list) or len(palette)!=5 or any(not isinstance(c,str) or not re.fullmatch(r'#[a-fA-F0-9]{6}',c) for c in palette):raise ValueError('palette needs paper, ink, red, yellow, blue as five #RRGGBB colours')
    grid=data.get('grid',{'module':120,'columns':12,'rows':5,'origin':[240,240]})
    if not isinstance(grid,dict) or set(grid)!={'module','columns','rows','origin'}:raise ValueError('grid needs module, columns, rows, origin')
    unit=_int(grid['module'],'grid.module',40,200)
    columns=_int(grid['columns'],'grid.columns',2,32);rows=_int(grid['rows'],'grid.rows',2,16)
    origin=grid['origin']
    if not isinstance(origin,list) or len(origin)!=2:raise ValueError('grid.origin needs two integer pixels')
    for value in origin:_int(value,'grid.origin',0,1920)
    if not (ARENA[0]<=origin[0] and origin[0]+columns*unit<=ARENA[2] and ARENA[1]<=origin[1] and origin[1]+rows*unit<=ARENA[3]):raise ValueError('grid must remain inside the protected composition area')
    modules=data.get('modules')
    if not isinstance(modules,list) or not 2<=len(modules)<=20:raise ValueError('modules needs 2..20 geometric modules')
    ids=[]
    for module in modules:
        if not isinstance(module,dict) or set(module)!={'id','shape','span','color'}:raise ValueError('module needs id, shape, span, color')
        _text(module['id'],'module.id',32)
        if module['id'] in ids:raise ValueError('module ids must be unique')
        ids.append(module['id'])
        if module['shape'] not in SHAPES:raise ValueError('unsupported module shape')
        _int(module['span'],'module.span',1,3);_int(module['color'],'module.color',1,4)
        module['polygon']=_glyph(module['shape'],module['span']*unit)
    layouts=data.get('layouts')
    if not isinstance(layouts,dict) or not 2<=len(layouts)<=8:raise ValueError('layouts needs 2..8 named targets')
    normalized_layouts={}
    for name,placements in layouts.items():
        _text(name,'layout name',32)
        if not isinstance(placements,list) or len(placements)!=len(modules):raise ValueError('every layout must place every module exactly once')
        layout={}
        for placement in placements:
            if not isinstance(placement,dict) or set(placement)!={'id','cell','turn'} or placement['id'] not in ids or placement['id'] in layout:raise ValueError('placement needs a unique module id, cell and turn')
            cell=placement['cell'];module=modules[ids.index(placement['id'])]
            if not isinstance(cell,list) or len(cell)!=2:raise ValueError('cell needs two integer module coordinates')
            _int(cell[0],'cell.x',0,columns-module['span']);_int(cell[1],'cell.y',0,rows-module['span'])
            _int(placement['turn'],'turn',0,3);layout[placement['id']]=placement
        normalized_layouts[name]=layout
    initial=data.get('initial_layout')
    if not isinstance(initial,str) or initial not in layouts:raise ValueError('initial_layout must name a layout')
    tick=_int(data.get('tick_frames',5),'tick_frames',1,60)
    beats=data.get('beats')
    if not isinstance(beats,list) or not 2<=len(beats)<=8:raise ValueError('beats needs 2..8 shared windows')
    previous=0
    for beat in beats:
        if not isinstance(beat,dict) or set(beat)!={'in','out','label','target','axis','move_frames','cascade_frames'}:raise ValueError('beat needs in/out/label/target/axis/move_frames/cascade_frames')
        a=_int(beat['in'],'beat.in',0,frames-1);b=_int(beat['out'],'beat.out',a+1,frames)
        if a!=previous or a%tick or b%tick:raise ValueError('beats must cover contiguous shared tick boundaries')
        _text(beat['label'],'beat.label',64)
        if not isinstance(beat['target'],str) or beat['target'] not in layouts or beat['axis'] not in AXES:raise ValueError('unknown target or rotation axis')
        move=_int(beat['move_frames'],'move_frames',1,b-a-1);cascade=_int(beat['cascade_frames'],'cascade_frames',0,b-a-1)
        if move%tick or cascade%tick or move+cascade*(len(modules)-1)>=b-a:raise ValueError('cascaded moves must land on shared ticks and leave a settled frame')
        previous=b
    if previous!=frames:raise ValueError('beats must cover the complete clip')
    prepared={'title':title,'subtitle':subtitle,'palette':palette,'seed':seed,'unit':unit,'origin':origin,'columns':columns,'rows':rows,'modules':modules,'layouts':normalized_layouts,'initial':initial,'beats':beats,'tick':tick,'frames':frames,'frame_center':(origin[0]+columns*unit/2,origin[1]+rows*unit/2)}
    before=normalized_layouts[initial]
    for beat in beats:
        after=normalized_layouts[beat['target']]
        for index,module in enumerate(modules):
            axis=AXES[index%3] if beat['axis']=='alternate' else beat['axis']
            bounds=_motion_bounds(prepared,module,before[module['id']],after[module['id']],axis)
            if bounds[0]<ARENA[0]+2 or bounds[1]<ARENA[1]+2 or bounds[2]>ARENA[2]-2 or bounds[3]>ARENA[3]-2:raise ValueError('rotation trajectory leaves protected composition area: '+module['id']+' / '+beat['target']+' / '+str(bounds))
        before=after
    requested_font=font_path or data.get('font_path') or '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'
    if not isinstance(requested_font,(str,Path)):raise ValueError('font_path must be a local path')
    font=Path(requested_font)
    if not font.is_file():raise ValueError('provide a separately licensed local covering font')
    face=2 if font.name=='NotoSansCJK-Bold.ttc' else 0
    title_font,title_lines=_fit(title,font,face,72,1680,2,48)
    subtitle_font,subtitle_lines=_fit(subtitle,font,face,28,1680,1)
    labels=[_fit(beat['label'],font,face,40,1680,1) for beat in beats]
    missing=subtitle_font.getmask('\U0010ffff');notdef=(missing.size,bytes(missing))
    for char in set(title+subtitle+''.join(beat['label'] for beat in beats)):
        if char.isspace():continue
        glyph=subtitle_font.getmask(char)
        if (glyph.size,bytes(glyph))==notdef:raise ValueError(f'font lacks glyph U+{ord(char):04X}')
    prepared.update(font_path=font,font_is_default=font_path is None and not data.get('font_path'),font_face=face,title_font=title_font,title_lines=title_lines,subtitle_font=subtitle_font,subtitle_lines=subtitle_lines,labels=labels)
    return prepared


def _state(prepared,frame):
    _int(frame,'frame',0,prepared['frames']-1)
    index=next(i for i,b in enumerate(prepared['beats']) if b['in']<=frame<b['out'])
    beat=prepared['beats'][index]
    before=prepared['layouts'][prepared['initial'] if index==0 else prepared['beats'][index-1]['target']]
    after=prepared['layouts'][beat['target']]
    states=[]
    for rank,module in enumerate(prepared['modules']):
        u=max(0,min(1,(frame-beat['in']-rank*beat['cascade_frames'])/beat['move_frames']))
        q=2*u*u if u<.5 else 1-(-2*u+2)**2/2
        axis=AXES[rank%3] if beat['axis']=='alternate' else beat['axis']
        states.append({'id':module['id'],'axis':axis,'progress':q,'points':_points(prepared,module,before[module['id']],after[module['id']],axis,q),'color':module['color']})
    return index,states


def _rgb_mix(a,b,amount):
    return tuple(round(int(a[i:i+2],16)*(1-amount)+int(b[i:i+2],16)*amount) for i in (1,3,5))


def _render(prepared,frame):
    from PIL import Image,ImageDraw
    index,states=_state(prepared,frame)
    paper,ink,*_=prepared['palette']
    image=Image.new('RGB',SIZE,paper);draw=ImageDraw.Draw(image)
    # A static, seeded paper tooth; seed never changes clocks or module positions.
    seed=prepared['seed']&0xffffffff
    for _ in range(100):
        seed=(1664525*seed+1013904223)&0xffffffff;x=96+seed%1728
        seed=(1664525*seed+1013904223)&0xffffffff;y=60+seed%960
        draw.point((x,y),fill=_rgb_mix(paper,ink,.06))
    x0,y0=prepared['origin'];unit=prepared['unit']
    width,height=unit*prepared['columns'],unit*prepared['rows']
    progress=min(1,(frame+1)/prepared['tick'])
    grid_color=_rgb_mix(paper,ink,.12)
    for col in range(prepared['columns']+1):draw.line((x0+col*unit,y0,x0+col*unit,y0+height*progress),fill=grid_color,width=1)
    for row in range(prepared['rows']+1):draw.line((x0,y0+row*unit,x0+width*progress,y0+row*unit),fill=grid_color,width=1)
    geometry=Image.new('RGBA',(3840,2160),(0,0,0,0));pen=ImageDraw.Draw(geometry)
    for state in states:pen.polygon([(round(x*2),round(y*2)) for x,y in state['points']],fill=prepared['palette'][state['color']])
    geometry=geometry.resize(SIZE,Image.Resampling.LANCZOS)
    image.paste(geometry,(0,0),geometry)
    draw=ImageDraw.Draw(image)
    for i,line in enumerate(prepared['title_lines']):draw.text((120,64+i*(prepared['title_font'].size+8)),line,font=prepared['title_font'],fill=ink,anchor='lt')
    font,lines=prepared['labels'][index]
    draw.text((120,916),lines[0],font=font,fill=ink,anchor='lt')
    if prepared['subtitle_lines'][0]:draw.text((120,1000),prepared['subtitle_lines'][0],font=prepared['subtitle_font'],fill=ink,anchor='lt')
    # The ticks share exactly the same table that drives every mechanical module.
    for tick in range(prepared['frames']//prepared['tick']):
        x=120+tick*min(18,1640/max(1,prepared['frames']//prepared['tick']))
        draw.line((round(x),888,round(x),898),fill=ink if tick<=frame//prepared['tick'] else grid_color,width=2)
    return image


def _receipt(prepared,font_path):
    import PIL
    cue_frames=sorted({beat['in']+rank*beat['cascade_frames']+beat['move_frames'] for beat in prepared['beats'] for rank in range(len(prepared['modules']))})
    return {'frames':prepared['frames'],'fps':30,'size':list(SIZE),'renderer':'Pillow strict modular Bauhaus',
            'pillow_version':PIL.__version__,'title_font_pixels':prepared['title_font'].size,
            'module_pixels':prepared['unit'],'grid_origin':prepared['origin'],'tick_frames':prepared['tick'],
            'beat_table':prepared['beats'],'cue_frames':cue_frames,'trajectory_bounds_verified':True,
            'font_path':str(prepared['font_path']),'font_sha256':hashlib.sha256(prepared['font_path'].read_bytes()).hexdigest(),
            'font_source':'https://github.com/notofonts/noto-cjk' if prepared['font_is_default'] else 'user-supplied',
            'font_license':'SIL OFL 1.1' if prepared['font_is_default'] else 'verify separately',
            'source_sha256':globals().get('_CAPTURED_SOURCE_SHA256') or hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'inspiration':'https://github.com/Vincentwei1021/mg-styles-15/tree/49052d80bfaf9fa8412cc9710530e5e95a3b8d8c/demos/09-bauhaus',
            'implementation':'Independent geometry and choreography; no upstream assets or source copied'}


def validate_bauhaus_content(data,frames,font_path=None):
    return _receipt(_prepare(data,frames,font_path),font_path)


def render_bauhaus_frame(data,frame,frames,font_path=None):
    return _render(_prepare(data,frames,font_path),frame)


def render_bauhaus_frames(data,frames,output,font_path=None):
    prepared=_prepare(data,frames,font_path)
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    hashes=[]
    for frame in range(frames):
        path=output/f'f{frame:04d}.png';_render(prepared,frame).save(path)
        hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
    receipt=_receipt(prepared,font_path)
    receipt.update(frame_sha256=hashes,input_sha256=hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest())
    (output/'evidence.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    return receipt
