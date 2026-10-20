#!/usr/bin/env python3
"""Build optional real 3D, pixel-grid or video-reaction source projects.

Source PNG encoding is asset creation; editing, mixing and delivery use vtx.
No browser startup, package installation or permission changes are performed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from build import DEFAULT, build

ROOT = Path(__file__).resolve().parent
STYLES = ('soft-3d', 'pixel', 'variety-captions')


def run(command, root, name):
    (root/(name+'-command.json')).write_text(json.dumps([str(x) for x in command],indent=2)+'\n')
    with (root/(name+'.log')).open('wb') as log:
        result = subprocess.run([str(x) for x in command],stdout=log,stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'{name} exited {result.returncode}; see {root/(name+".log")}')


def encode(sequence, pattern, destination, root, alpha=False):
    command=['ffmpeg','-v','warning','-nostdin','-n','-framerate','30','-start_number','0','-i',sequence/pattern,'-i',root/'cue.wav','-map','0:v:0','-map','1:a:0']
    if alpha:
        command += ['-c:v','prores_ks','-profile:v','4','-pix_fmt','yuva444p10le','-c:a','pcm_s16le']
    else:
        command += ['-c:v','libx264','-crf','16','-preset','medium','-pix_fmt','yuv420p','-c:a','aac']
    run(command+[destination],root,'encode-'+destination.stem)


def crop_caption_asset(sequence, destination):
    """Use the real alpha support as the independent video-layer geometry."""
    from PIL import Image
    paths=sorted(sequence.glob('f*.png'));bounds=None
    for path in paths:
        with Image.open(path) as image: box=image.getchannel('A').getbbox()
        if box:
            bounds=box if bounds is None else (min(bounds[0],box[0]),min(bounds[1],box[1]),max(bounds[2],box[2]),max(bounds[3],box[3]))
    if bounds is None: raise ValueError('reaction layer contains no visible glyphs')
    # Include interpolation/outline margin and keep ProRes dimensions even.
    left=max(0,(bounds[0]-8)//2*2);top=max(0,(bounds[1]-8)//2*2)
    right=min(1920,((bounds[2]+9)//2)*2);bottom=min(1080,((bounds[3]+9)//2)*2)
    destination.mkdir()
    for path in paths:
        with Image.open(path) as image: image.crop((left,top,right,bottom)).save(destination/path.name)
    return [left,top,right,bottom]


def assemble(style, output, content, frames=90, background=None, samples=16, dry=False):
    if style not in STYLES: raise ValueError('unknown media style')
    if type(samples) is not int or not 1 <= samples <= 256: raise ValueError('samples must be an integer in [1,256]')
    if not isinstance(content,dict): raise ValueError('content must be an object')
    basic={key:content[key] for key in DEFAULT if key in content}
    build(style,output,basic,frames,True)  # validates shared fields and output protection
    if style=='variety-captions':
        if background is None or not Path(background).is_file(): raise ValueError('variety-captions needs a local --background video')
        if not content.get('reactions'): raise ValueError('variety-captions needs nonempty reactions in the content JSON')
    if dry:
        return {'style':style,'frames':frames,'source_renderer':{'soft-3d':'Blender Cycles CPU','pixel':'Pillow integer grid','variety-captions':'Pillow captions over local video'}[style], 'output':str(output),'background':str(background) if background else None}
    for tool in (('blender','ffmpeg') if style=='soft-3d' else ('ffmpeg',)):
        if not shutil.which(tool): raise ValueError(tool+' is required for selected media path')
    timeline=build(style,output,basic,frames)
    output=Path(output);media=output/'media';media.mkdir()
    build_snapshot=output/'build_media.snapshot.py';shutil.copyfile(ROOT/'build_media.py',build_snapshot)
    data=dict(DEFAULT);data.update(content)
    (output/'creative.json').write_text(json.dumps(data,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf-8')
    def clip(source,**extra): return dict(src=source,src_in=0,src_out=frames,dst_in=0,dur=frames,**extra)
    timeline['video_tracks']=[]
    if style=='soft-3d':
        render=output/'blender-render'
        snapshot=output/'render_3d.py';shutil.copyfile(ROOT/'render_3d.py',snapshot)
        command=['blender','--background','--python',snapshot,'--','--content',output/'creative.json','--output',render,'--frames',frames,'--width','1920','--height','1080','--samples',samples]
        if data.get('font_path'): command += ['--font',Path(data['font_path']).resolve()]
        run(command,output,'blender')
        source=media/'scene.mp4';encode(render/'frames','f%06d.png',source,output)
        timeline['sources'].append({'id':'scene','path':'media/scene.mp4'})
        timeline['video_tracks'].append({'name':'Material and light scene','kind':'broll','clips':[clip('scene')]})
    elif style=='pixel':
        from render_pixel import render_pixel_frames
        render=output/'pixel-frames';render_pixel_frames(data,frames,render,data.get('font_path'))
        source=media/'pixel.mp4';encode(render,'f%04d.png',source,output)
        timeline['sources'].append({'id':'pixel','path':'media/pixel.mp4'})
        timeline['video_tracks'].append({'name':'Pixel mission','kind':'broll','clips':[clip('pixel')]})
    else:
        from captions import render_caption_frames
        background=Path(background).resolve()
        copied=media/('background'+background.suffix.lower());shutil.copyfile(background,copied)
        render=output/'caption-frames'
        provenance=render_caption_frames(data,data['reactions'],frames,render,1920,1080,data.get('font_path'))
        cropped=output/'caption-asset-frames';bounds=crop_caption_asset(render,cropped)
        provenance['asset_bounds']=bounds
        source=media/'captions.mov';encode(cropped,'f%04d.png',source,output,True)
        timeline['sources'] += [{'id':'background','path':str(copied.relative_to(output))},{'id':'captions','path':'media/captions.mov'}]
        timeline['video_tracks'] += [dict(name='Real background video',kind='broll',clips=[clip('background')]),dict(name='Reaction captions',kind='broll',clips=[clip('captions',x=bounds[0],y=bounds[1],fade_in_frames=1,fade_out_frames=1)])]
        (output/'caption-provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    (output/'timeline.json').write_text(json.dumps(timeline,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    renderer=output/'render_3d.py' if style=='soft-3d' else ROOT/('render_pixel.py' if style=='pixel' else 'captions.py')
    receipt={'style':style,'frames':frames,'renderer_sources':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (build_snapshot,renderer)},'creative_sha256':hashlib.sha256((output/'creative.json').read_bytes()).hexdigest(),'state':'sources-created; vtx all and artistic acceptance pending'}
    (output/'source-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return timeline


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--style',choices=STYLES,required=True)
    parser.add_argument('--content',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--background',type=Path)
    parser.add_argument('--frames',type=int,default=90)
    parser.add_argument('--samples',type=int,default=16)
    parser.add_argument('--dry',action='store_true')
    args=parser.parse_args()
    try:
        result=assemble(args.style,args.output,json.loads(args.content.read_text(encoding='utf-8')),args.frames,args.background,args.samples,args.dry)
    except (ValueError,OSError,RuntimeError,ImportError) as error: parser.error(str(error))
    if args.dry: print(json.dumps(result,indent=2))
if __name__=='__main__':main()
