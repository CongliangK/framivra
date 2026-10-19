"""Explicit offline FFmpeg fixture, not a CI gate or real-Demo qualification.

Writes only NEWDIR. No browser, audio, old caches or actual project media.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time
import numpy as np
from vtx.gfx_render import png_encode_rgba
from .quality import dense_n1_commands,png_comparison
from .runner import fresh,write_json
from .contract import file_hash,require


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True)
    a=p.parse_args(argv);root=fresh(a.output);seqs={};report={'scope':'2x2 synthetic CPU command mechanism only','cases':[]}
    # Wing is empty here; body/group vary per dense frame, so stale/shifted output
    # has an observable failure. This tests neither real T08 overlap nor Chrome.
    pixels=[]
    for i in range(4):
        image=np.zeros((2,2,4),dtype=np.uint8);image[:]=[30+20*i,40,150,40+50*i];pixels.append(image)
    for arm,entry in [('baseline','T08.01'),('baseline','T07.01'),('candidate','T08_T07')]:
        directory=root/arm/entry;directory.mkdir(parents=True);seqs[(arm,entry)]=directory
        for i,image in enumerate(pixels):
            (directory/f'f{i:04d}.png').write_bytes(png_encode_rgba(np.zeros_like(image) if entry=='T08.01' else image))
    try:
        for background in ('black','white','transparent'):
            commands=dense_n1_commands(seqs,root,background,4,2,2)
            for command in commands:
                target=Path(command['output']);target.mkdir()
                with open(target/'stdout.log','wb') as out,open(target/'stderr.log','wb') as err:
                    before=time.monotonic();r=subprocess.run(command['argv'],stdout=out,stderr=err,timeout=20)
                command.update(returncode=r.returncode,wall_seconds=time.monotonic()-before)
                report['cases'].append(command);write_json(root/'result.json',report)
                require(r.returncode==0,'Synthetic FFmpeg failed')
                require(len(list(target.glob('f*.png')))==4,'Wrong synthetic frame count')
            compared=[png_comparison(Path(commands[0]['output'])/f'f{i:04d}.png',
                                     Path(commands[1]['output'])/f'f{i:04d}.png',2,2) for i in range(4)]
            require(all(x['equal'] for x in compared),'Synthetic reference mismatch')
            require(len({x['left']['rgba_sha256'] for x in compared})==4,'Synthetic output froze or repeated')
            report[background]={'equal':True,'frames':compared}
        report['status']='PASS_SYNTHETIC_MECHANISM_ONLY'
    except BaseException as exc:
        report.update(status='FAILED',error=type(exc).__name__+': '+str(exc));raise
    finally:write_json(root/'result.json',report)
    print(json.dumps({'status':report['status'],'report':str(root/'result.json')}))

if __name__=='__main__':main()
