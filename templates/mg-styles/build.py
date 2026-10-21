#!/usr/bin/env python3
"""Build an independent frame-driven MG project; never overwrites a project."""
import argparse
import json
from pathlib import Path
import re
import shutil
import math
import struct
import wave

STYLES = ('cel-boil','isometric','flat-vector','line-art','soft-3d','morph','sticker','hud','collage','aurora-glass','bauhaus','synthwave','pixel','liquid','variety-captions')
ROOT = Path(__file__).resolve().parent
DEFAULT = dict(title='Ideas become motion', subtitle='Observe · connect · transform', items=[dict(label='Discover',value=24),dict(label='Build',value=56),dict(label='Share',value=88)], palette=['#faf5e8','#202730','#e66b42','#49a69d'],seed=7,pace=1.0,hold_frames=12)

def build(style, output, content=None, frames=90, dry=False, *, scaffold=True):
    if style not in STYLES: raise ValueError('unknown style')
    if type(frames) is not int or frames < 30 or frames > 18000: raise ValueError('frames must be an integer between 30 and 18000')
    data = dict(DEFAULT)
    if content is not None:
        if not isinstance(content,dict): raise ValueError('content must be an object')
        if set(content)-set(DEFAULT): raise ValueError('unknown content keys')
        data.update(content)
    for key in ('title','subtitle'):
        if not isinstance(data[key],str) or len(data[key])>180: raise ValueError(key+' must be text of at most 180 characters')
    items=data['items']
    if not isinstance(items,list) or not 1<=len(items)<=6: raise ValueError('items must contain 1 to 6 entries')
    for item in items:
        if not isinstance(item,dict) or set(item)!={'label','value'} or not isinstance(item['label'],str) or len(item['label'])>80: raise ValueError('each item needs label text and value')
        if type(item['value']) not in (int,float) or not 0<=item['value']<=100: raise ValueError('item value must be finite in [0,100]')
    if not isinstance(data['palette'],list) or len(data['palette'])!=4 or any(not isinstance(c,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',c) for c in data['palette']): raise ValueError('palette needs four #RRGGBB colors')
    if type(data['pace']) not in (int,float) or not 0.5 <= data['pace'] <= 2: raise ValueError('pace must be finite in [0.5,2]')
    if type(data['seed']) is not int or not -(2**31) <= data['seed'] <= 2**31-1: raise ValueError('seed must be a signed 32-bit integer')
    if type(data['hold_frames']) is not int or not 0 <= data['hold_frames'] < frames/2: raise ValueError('hold_frames must be integer in [0,frames/2)')
    data.update(style=style,frames=frames)
    output=Path(output)
    if output.exists(): raise ValueError('output must be a new directory')
    deps=['gfx/'+s for s in ('content.js','runtime.js','prototype-helpers.js','prototype.js')]
    timeline={'version':2,'project':{'fps':30,'width':1920,'height':1080,'duration_frames':frames},'sources':[{'id':'cue','path':'cue.wav'}], 'video_tracks':[{'name':'MG composition','kind':'gfx','clips':[{'src':'gfx/index.html','dst_in':0,'dur':frames,'cache_dependencies':deps}]}], 'audio_tracks':[{'name':'procedural cues','events':[{'src':'cue','in':0,'out':frames,'src_in':0,'volume':1.0}]}],'subtitles':[]}
    if not scaffold: timeline['video_tracks']=[]
    if dry: return timeline
    output.mkdir(parents=True)
    # Original, deterministic reference cue track. Replace with editorial audio.
    with wave.open(str(output/'cue.wav'),'wb') as wav:
        wav.setparams((2,2,48000,0,'NONE','not compressed'))
        chunk=bytearray()
        for sample in range(frames*1600):
            t=sample/48000
            beat=t%0.5
            envelope=math.exp(-beat*12)*min(1,beat*200)
            note=(220,277.1826,329.6276,440)[int(t/0.5)%4]
            value=int(3400*envelope*math.sin(2*math.pi*note*t))
            chunk.extend(struct.pack('<hh',value,value))
            if len(chunk)>=65536: wav.writeframesraw(chunk);chunk.clear()
        if chunk: wav.writeframesraw(chunk)
    if scaffold:
        gfx=output/'gfx';gfx.mkdir()
        for name in ('runtime.js','prototype-helpers.js'): shutil.copyfile(ROOT/name,gfx/name)
        shutil.copyfile(ROOT/'styles'/style/'scripts'/'prototype.js',gfx/'prototype.js')
        (gfx/'content.js').write_text('window.MG_CONTENT='+json.dumps(data,ensure_ascii=True,allow_nan=False)+';\n',encoding='utf-8')
        html='<!doctype html><meta charset="utf-8"><title>MG composition</title><style>html,body{margin:0;width:1920px;height:1080px;overflow:hidden}canvas{display:block}</style><canvas id="stage" width="1920" height="1080"></canvas>'
        html+=''.join('<script src="'+s+'"></script>' for s in ('content.js','prototype-helpers.js','prototype.js','runtime.js'))
        (gfx/'index.html').write_text(html,encoding='utf-8')
    (output/'timeline.json').write_text(json.dumps(timeline,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return timeline

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--style',choices=STYLES,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--content',type=Path);p.add_argument('--frames',type=int,default=90);p.add_argument('--dry',action='store_true')
    a=p.parse_args()
    try: result=build(a.style,a.output,json.loads(a.content.read_text(encoding='utf-8')) if a.content else None,a.frames,a.dry)
    except (ValueError,OSError) as e: p.error(str(e))
    if a.dry: print(json.dumps(result,indent=2))
if __name__=='__main__':main()
