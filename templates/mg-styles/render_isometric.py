"""Original true-isometric miniature assembly grammar, rendered with Pillow.

World axes have equal projected length. All assembly and traffic state is derived
from integer frames; painter order comes from world depth, never input order.
"""
from pathlib import Path
import hashlib
import math
import re

PALETTE=['#faf5e8','#202730','#e66b42','#49a69d']
KINDS=('home','lab','hub','garden')


def _font(data,path=None):
    from PIL import ImageFont
    text=data['title']+data['subtitle']+''.join(n['label'] for n in data['nodes'])
    cjk=any(0x2e80<=ord(c)<=0x9fff or 0xf900<=ord(c)<=0xfaff for c in text)
    paths=[Path(path)] if path else [Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc')]
    if not path and not cjk:paths.append(Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
    for candidate in paths:
        if candidate.is_file():
            if cjk and 'DejaVu' in candidate.name:raise ValueError('CJK content requires a CJK-capable font_path')
            ImageFont.truetype(str(candidate),20)
            return candidate
    raise ValueError('provide an existing local font_path supporting the content')


def _fit(text,width,size,font,minimum=12):
    from PIL import Image,ImageDraw,ImageFont
    draw=ImageDraw.Draw(Image.new('L',(1,1)))
    while True:
        face=ImageFont.truetype(str(font),size)
        if draw.textlength(text,font=face)<=width:return face
        if size<=minimum:raise ValueError('text does not fit its reading zone at minimum size 12')
        size-=1


def validate_isometric_content(data,frames,font_path=None):
    """Validate causal assembly, closed spatial inputs and local text before writes."""
    if not isinstance(data,dict) or set(data)-{'title','subtitle','grid','nodes','flows','palette','seed'}:raise ValueError('unknown isometric content key')
    if type(frames) is not int or not 90<=frames<=18000:raise ValueError('frames must be an integer in [90,18000]')
    title,subtitle=data.get('title','Build a connected world'),data.get('subtitle','Foundation · structure · life')
    if any(not isinstance(t,str) or len(t)>120 or '\n' in t for t in (title,subtitle)):raise ValueError('title/subtitle must be a single line up to 120 characters')
    grid=data.get('grid',[3,3])
    if not isinstance(grid,list) or len(grid)!=2 or any(type(v) is not int or not 2<=v<=4 for v in grid):raise ValueError('grid must be [columns,rows] integers from 2 to 4')
    palette=data.get('palette',PALETTE)
    if not isinstance(palette,list) or len(palette)!=4 or any(not isinstance(c,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',c) for c in palette):raise ValueError('palette must have four #RRGGBB colors')
    seed=data.get('seed',0)
    if type(seed) is not int or not -(2**31)<=seed<2**31:raise ValueError('seed must be a signed 32-bit integer')
    nodes=data.get('nodes')
    if not isinstance(nodes,list) or not 2<=len(nodes)<=min(12,grid[0]*grid[1]):raise ValueError('nodes must contain 2 to 12 unique spatial objects')
    normalized=[];ids={};cells=set()
    for node in nodes:
        keys={'id','label','kind','x','y','height','base','wall','roof','activate','depends_on'}
        if not isinstance(node,dict) or not keys-{'height','depends_on'}<=set(node) or set(node)-keys:raise ValueError('node requires id,label,kind,x,y,base,wall,roof,activate; optional height,depends_on')
        identity=node['id']
        if not isinstance(identity,str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,31}',identity) or identity in ids:raise ValueError('node id must be a unique lowercase identifier')
        if not isinstance(node['label'],str) or not node['label'] or len(node['label'])>40 or '\n' in node['label']:raise ValueError('node label must contain 1 to 40 single-line characters')
        if not isinstance(node['kind'],str) or node['kind'] not in KINDS:raise ValueError('node kind must be home, lab, hub, garden')
        x,y=node['x'],node['y'];height=node.get('height',1)
        if type(x) is not int or type(y) is not int or not 0<=x<grid[0] or not 0<=y<grid[1] or (x,y) in cells:raise ValueError('node coordinates must be unique integer grid cells')
        if type(height) not in (int,float) or not math.isfinite(height) or not .35<=height<=1.8:raise ValueError('height must be finite in [.35,1.8]')
        times=[node[k] for k in ('base','wall','roof','activate')]
        if any(type(v) is not int for v in times) or not 0<=times[0]<times[1]<times[2]<times[3]<frames or times[2]-times[1]<6 or times[3]-times[2]<6:raise ValueError('assembly needs base < wall < roof < activate, six frames for walls and delayed cap')
        deps=node.get('depends_on',[])
        if not isinstance(deps,list) or any(not isinstance(d,str) for d in deps) or len(set(deps))!=len(deps):raise ValueError('depends_on must be unique node ids')
        normalized.append(dict(node,height=height,depends_on=list(deps)));ids[identity]=normalized[-1];cells.add((x,y))
    # Dependency resolution uses ids, not input ordering; timed causality rejects cycles.
    for node in normalized:
        if any(d not in ids or d==node['id'] or ids[d]['activate']>node['base'] for d in node['depends_on']):raise ValueError('dependencies must activate before the dependent foundation starts')
    flows=data.get('flows',[])
    if not isinstance(flows,list) or len(flows)>12:raise ValueError('flows must be an array with at most twelve links')
    links=[]
    for edge in flows:
        if not isinstance(edge,dict) or set(edge)!={'from','to','in','out'}:raise ValueError('flow requires from,to,in,out')
        src,dst=edge['from'],edge['to'];a,b=edge['in'],edge['out']
        if not isinstance(src,str) or not isinstance(dst,str) or src not in ids or dst not in ids or src==dst:raise ValueError('flow endpoints must name distinct existing nodes')
        if type(a) is not int or type(b) is not int or not max(ids[src]['activate'],ids[dst]['activate'])<=a<b<=frames or b-a<12:raise ValueError('flow must follow both endpoint activations for at least twelve frames')
        links.append(dict(edge))
    result=dict(title=title,subtitle=subtitle,grid=list(grid),nodes=normalized,flows=links,palette=list(palette),seed=seed)
    font=_font(result,font_path);_fit(title,3280,160,font,24);_fit(subtitle,3200,64,font,24)
    # The reading legend has fixed cells independent of the miniature's occlusion.
    for node in normalized:_fit(node['label'],3280/len(normalized)-36,60,font,24)
    return result


def project_iso(x,y,z=0,unit=120):
    """True orthographic isometric basis, 30-degree ground axes, no perspective."""
    return ((x-y)*unit*math.sqrt(3)/2,(x+y)*unit/2-z*unit)


def _ease(value):
    value=max(0,min(1,value));return 1-(1-value)**3


def isometric_state(data,frame,frames):
    if type(frame) is not int or not 0<=frame<frames:raise ValueError('frame must be an integer in [0,frames)')
    state=[]
    for node in data['nodes']:
        base=_ease((frame-node['base'])/max(1,node['wall']-node['base']))
        wall=_ease((frame-node['wall'])/max(1,node['roof']-node['wall']))
        roof=_ease((frame-node['roof']-2)/3)
        state.append(dict(node,base_progress=base,wall_progress=wall,roof_progress=roof,active=frame>=node['activate']))
    return sorted(state,key=lambda n:(n['x']+n['y'],n['x'],n['id']))


def _mix(color,factor):
    channels=[int(color[i:i+2],16) for i in (1,3,5)]
    return tuple(round(max(0,min(255,c*factor))) for c in channels)


def render_isometric_frame(data,frame,frames,font_path=None):
    from PIL import Image,ImageDraw
    data=validate_isometric_content(data,frames,font_path);nodes=isometric_state(data,frame,frames)
    font=_font(data,font_path);bg,ink,accent,secondary=data['palette'];gw,gh=data['grid']
    # Full-scene parallel slide, settled for the final reading hold. No camera rotation.
    camera=_ease(min(1,frame/max(1,frames*.78)));slide=-34+68*camera
    zoom=1-.06*_ease((frame/frames-.70)/.15)
    def point(x,y,z=0,parallax=.8):
        px,py=project_iso(x-(gw-1)/2,y-(gh-1)/2,z)
        return (round((960+px*zoom+slide*parallax)*2),round((650+py*zoom)*2))
    image=Image.new('RGB',(3840,2160),bg);draw=ImageDraw.Draw(image)
    def polygon(points,color):draw.polygon(points,fill=color)
    def face(x,y,z,w,d,color):polygon([point(x,y,z),point(x+w,y,z),point(x+w,y+d,z),point(x,y+d,z)],color)
    # Tile wave comes from Manhattan distance to the island centre.
    for y in range(gh):
        for x in range(gw):
            delay=round((abs(x-(gw-1)/2)+abs(y-(gh-1)/2))*3)
            reveal=_ease((frame-delay+3)/12)
            z=-.14-(1-reveal)*.5
            face(x-.5,y-.5,z,1,1,_mix(secondary,.78))
            if reveal>0:face(x-.48,y-.48,z+.08,.96,.96,_mix(bg,.95))
    byid={n['id']:n for n in nodes}
    # Routes are on the ground plane; later building faces correctly occlude them.
    for edge in data['flows']:
        a,b=byid[edge['from']],byid[edge['to']]
        if frame<edge['in']:continue
        points=[(a['x'],a['y']),(b['x'],a['y']),(b['x'],b['y'])]
        draw.line([point(x,y,.01) for x,y in points],fill=_mix(secondary,.78),width=10,joint='curve')
        if frame<edge['out']:
            lengths=[abs(points[i+1][0]-points[i][0])+abs(points[i+1][1]-points[i][1]) for i in (0,1)]
            total=sum(lengths)
            for packet in range(2):
                travel=(((frame-edge['in'])/36+packet*.5)%1)*total
                first=travel<=lengths[0]
                start,end=(points[0],points[1]) if first else (points[1],points[2])
                u=(travel if first else travel-lengths[0])/max(1e-9,lengths[0] if first else lengths[1])
                x=start[0]+(end[0]-start[0])*u;y=start[1]+(end[1]-start[1])*u
                px,py=point(x,y,.04);draw.ellipse((px-9,py-9,px+9,py+9),fill=accent)
    for node in nodes:
        if frame<node['base']:continue
        x,y=node['x']-.36,node['y']-.36
        progress=node['base_progress'];face(x-.06,y-.06,-.02,.84,.84,_mix(secondary,.75))
        face(x,y,.05*progress,.72,.72,_mix(secondary,.9))
        height=node['height']*node['wall_progress']
        if node['kind']=='garden':
            face(x,y,.08,.72,.72,secondary)
            for dx,dy in ((.2,.2),(.5,.45)):
                cx,cy=point(x+dx,y+dy,.12+height*.35)
                radius=round(30*node['wall_progress']*zoom)
                draw.line([point(x+dx,y+dy,.10), (cx,cy)],fill=_mix(accent,.65),width=max(1,round(7*node['wall_progress'])))
                draw.ellipse((cx-radius,cy-radius,cx+radius,cy+radius),fill=_mix(secondary,.75))
            continue
        if height>0:
            # Back faces are not drawn; the visible x/y sides use distinct illumination.
            polygon([point(x+.72,y,.05),point(x+.72,y+.72,.05),point(x+.72,y+.72,height),point(x+.72,y,height)],_mix(accent,.78))
            polygon([point(x,y+.72,.05),point(x+.72,y+.72,.05),point(x+.72,y+.72,height),point(x,y+.72,height)],_mix(accent,.93))
            if node['roof_progress']>0:
                cap_z=height+(1-node['roof_progress'])*.3
                face(x-.025,y-.025,cap_z,.77,.77,_mix(accent,1.12))
            # Windows illuminate only after this node becomes functionally available.
            if node['active']:
                levels=max(1,int(node['height']/.38))
                for level in range(levels):
                    z=.22+level*.32
                    for column in range(2):
                        wx=x+.15+column*.28;wy=y+.725
                        lit=frame>=node['activate']+level*2+column+data['seed']%3
                        color=(bg if (level+column+data['seed'])%3 else secondary) if lit else _mix(accent,.6)
                        polygon([point(wx,wy,z),point(wx+.13,wy,z),point(wx+.13,wy,z+.13),point(wx,wy,z+.13)],color)
            if node['kind']=='hub' and node['roof_progress']>=1:
                cx,cy=point(x+.36,y+.36,height+.08);draw.ellipse((cx-14,cy-14,cx+14,cy+14),fill=secondary)
            if node['kind']=='lab' and node['roof_progress']>=1:
                face(x+.14,y+.14,height+.03,.44,.44,secondary)
    # Typography is outside the miniature and remains readable throughout the slide.
    draw.text((1920,250),data['title'],font=_fit(data['title'],3280,160,font,24),fill=ink,anchor='mm')
    draw.text((1920,405),data['subtitle'],font=_fit(data['subtitle'],3200,64,font,24),fill=ink,anchor='mm')
    for i,node in enumerate(sorted(nodes,key=lambda n:n['id'])):
        cell=3280/len(nodes);x=280+(i+.5)*cell
        draw.text((round(x),1910),node['label'],font=_fit(node['label'],cell-36,60,font,24),fill=ink,anchor='mm')
        draw.ellipse((x-8,1850,x+8,1866),fill=accent if node['active'] else secondary)
    return image.resize((1920,1080),Image.Resampling.LANCZOS)


def render_isometric_frames(data,frames,output,font_path=None):
    data=validate_isometric_content(data,frames,font_path)
    output=Path(output)
    if output.exists():raise ValueError('output must be a new directory')
    output.mkdir(parents=True);digest=hashlib.sha256()
    for frame in range(frames):
        image=render_isometric_frame(data,frame,frames,font_path)
        image.save(output/f'f{frame:04d}.png');digest.update(image.tobytes())
    font=_font(data,font_path)
    return dict(frames=frames,width=1920,height=1080,pixel_sha256=digest.hexdigest(),
                nodes=data['nodes'],flows=data['flows'],projection='true-isometric-30deg',
                font_path=str(font),font_sha256=hashlib.sha256(font.read_bytes()).hexdigest())
