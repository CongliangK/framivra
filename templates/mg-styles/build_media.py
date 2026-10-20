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
import types
import importlib.metadata


ROOT = Path(__file__).resolve().parent
STYLES = ('soft-3d', 'pixel', 'variety-captions', 'line-art', 'liquid')
SELF_SOURCE = Path(__file__).read_bytes()


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


def frozen_module(name, source, bindings=None):
    """Execute exactly the captured local renderer bytes, without live re-import."""
    module=types.ModuleType('_mg_'+name.replace('.','_'))
    module.__file__=str(ROOT/name)
    module.__dict__.update(bindings or {})
    exec(compile(source,module.__file__,'exec'),module.__dict__)
    return module


def assemble(style, output, content, frames=90, background=None, samples=16,
             dry=False, three_d_mode='full'):
    if style not in STYLES: raise ValueError('unknown media style')
    if type(samples) is not int or not 1 <= samples <= 256: raise ValueError('samples must be an integer in [1,256]')
    if three_d_mode not in ('full','storyboard'): raise ValueError('unknown three_d_mode')
    if not isinstance(content,dict): raise ValueError('content must be an object')
    names={'soft-3d':'render_3d.py','pixel':'render_pixel.py','variety-captions':'captions.py','line-art':'render_line_art.py','liquid':'render_liquid.py'}
    renderer_name=names[style]
    sources={'build.py':(ROOT/'build.py').read_bytes(),renderer_name:(ROOT/renderer_name).read_bytes(), 'build_media.snapshot.py':SELF_SOURCE}
    builder=frozen_module('build.py',sources['build.py'])
    renderer=frozen_module(renderer_name,sources[renderer_name])
    basic={key:content[key] for key in builder.DEFAULT if key in content}
    builder.build(style,output,basic,frames,True)
    data=dict(content);font_path=data.pop('font_path',None)
    if font_path is not None and not Path(font_path).is_file(): raise ValueError('font_path must name an existing local font')
    if style=='soft-3d':
        preflight=renderer.validate_3d_content(data,frames,samples)
        if three_d_mode=='storyboard':
            sources['compose_3d_story.py']=(ROOT/'compose_3d_story.py').read_bytes()
            composer=frozen_module('compose_3d_story.py',sources['compose_3d_story.py'],{'_RENDERER':renderer,'_RENDERER_SHA256':hashlib.sha256(sources[renderer_name]).hexdigest()})
            plate_indices=composer.story_plate_indices(data,frames)
    elif style=='pixel': preflight=renderer.validate_pixel_content(data,frames,font_path)
    elif style=='line-art': preflight=renderer.validate_line_art_content(data,frames,font_path)
    elif style=='liquid': preflight=renderer.validate_liquid_content(data,frames,font_path)
    else:
        if background is None or not Path(background).is_file(): raise ValueError('variety-captions needs a local --background video')
        if not data.get('reactions'): raise ValueError('variety-captions needs nonempty reactions')
        preflight=renderer.validate_caption_content(data,data['reactions'],frames,1920,1080,font_path)
    if dry:
        return {'style':style,'frames':frames,'mode':three_d_mode if style=='soft-3d' else 'source-frames','output':str(output),'preflight':preflight}
    for tool in (('blender','ffmpeg') if style=='soft-3d' else ('ffmpeg',)):
        if not shutil.which(tool): raise ValueError(tool+' is required for selected media path')
    timeline=builder.build(style,output,basic,frames)
    output=Path(output);media=output/'media';media.mkdir()
    for name,source in sources.items(): (output/name).write_bytes(source)
    renderer.__file__=str(output/renderer_name)
    if style=='soft-3d' and three_d_mode=='storyboard': composer.__file__=str(output/'compose_3d_story.py')
    (output/'creative.json').write_text(json.dumps(data,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf-8')
    (output/'preflight.json').write_text(json.dumps(preflight,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    def clip(source,**extra): return dict(src=source,src_in=0,src_out=frames,dst_in=0,dur=frames,**extra)
    timeline['video_tracks']=[]
    if style=='soft-3d':
        render=output/'blender-render'
        command=['blender','--background','--python',output/renderer_name,'--','--content',output/'creative.json','--output',render,'--frames',frames,'--width','1920','--height','1080','--samples',samples]
        if font_path: command += ['--font',Path(font_path).resolve()]
        if three_d_mode=='storyboard': command += ['--render-frames',','.join(map(str,plate_indices))]
        run(command,output,'blender')
        sequence=render/'frames';pattern='f%06d.png'
        if three_d_mode=='storyboard':
            story_root=output/'story-composite'
            composer.compose_3d_story(data,frames,render,story_root)
            sequence=story_root/'frames';pattern='f%06d.png'
        source=media/'scene.mp4';encode(sequence,pattern,source,output)
        timeline['sources'].append({'id':'scene','path':'media/scene.mp4'})
        timeline['video_tracks'].append({'name':'Material and light story','kind':'broll','clips':[clip('scene')]})
    elif style in ('pixel','line-art','liquid'):
        render=output/'source-frames'
        function=getattr(renderer,{'pixel':'render_pixel_frames','line-art':'render_line_art_frames','liquid':'render_liquid_frames'}[style])
        provenance=function(data,frames,render,font_path)
        source=media/'composition.mp4';encode(render,'f%04d.png',source,output)
        timeline['sources'].append({'id':'composition','path':'media/composition.mp4'})
        timeline['video_tracks'].append({'name':style+' composition','kind':'broll','clips':[clip('composition')]})
        (output/'renderer-evidence.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+'\n')
    else:
        background=Path(background).resolve()
        copied=media/('background'+background.suffix.lower());shutil.copyfile(background,copied)
        render=output/'caption-frames'
        provenance=renderer.render_caption_frames(data,data['reactions'],frames,render,1920,1080,font_path)
        cropped=output/'caption-asset-frames';bounds=crop_caption_asset(render,cropped)
        provenance['asset_bounds']=bounds
        source=media/'captions.mov';encode(cropped,'f%04d.png',source,output,True)
        timeline['sources'] += [{'id':'background','path':str(copied.relative_to(output))},{'id':'captions','path':'media/captions.mov'}]
        timeline['video_tracks'] += [dict(name='Real background video',kind='broll',clips=[clip('background')]),dict(name='Reaction captions',kind='broll',clips=[clip('captions',x=bounds[0],y=bounds[1],fade_in_frames=1,fade_out_frames=1)])]
        (output/'caption-provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    (output/'timeline.json').write_text(json.dumps(timeline,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    versions={}
    for package in ('Pillow','numpy'):
        try: versions[package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError: versions[package]=None
    versions['ffmpeg']=subprocess.check_output(['ffmpeg','-version'],text=True).splitlines()[0]
    receipt={'style':style,'frames':frames,'mode':three_d_mode if style=='soft-3d' else 'source-frames','renderer_sources':{name:hashlib.sha256(source).hexdigest() for name,source in sources.items()},'versions':versions,'creative_sha256':hashlib.sha256((output/'creative.json').read_bytes()).hexdigest(),'state':'sources-created; vtx all and artistic acceptance pending'}
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
    parser.add_argument('--three-d-mode',choices=('full','storyboard'),default='full')
    args=parser.parse_args()
    try:
        result=assemble(args.style,args.output,json.loads(args.content.read_text(encoding='utf-8')),args.frames,args.background,args.samples,args.dry,args.three_d_mode)
    except (ValueError,OSError,RuntimeError,ImportError) as error: parser.error(str(error))
    if args.dry: print(json.dumps(result,indent=2))
if __name__=='__main__':main()
