#!/usr/bin/env python3
"""Replay a saved request prefix in two fresh runs and compare its final frame.

No page is edited. Both diagnostic corners are verified by render(); full-frame
RGBA and final serialized DOM are recorded independently for each arm/run.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image
from run import Shell, render, evaluate, atomic_json


def difference(reference, candidate):
    a=np.array(Image.open(reference).convert('RGBA'))
    b=np.array(Image.open(candidate).convert('RGBA'))
    d=np.abs(a.astype(np.int16)-b.astype(np.int16))
    ys,xs=np.where(np.any(d,axis=2))
    return {'different_pixels':len(xs),'max_abs':int(d.max()),
        'bbox':[int(xs.min()),int(ys.min()),int(xs.max()),int(ys.max())] if len(xs) else None,
        'coordinates':[{'x':int(x),'y':int(y),'reference':a[y,x].tolist(),
            'candidate':b[y,x].tolist()} for y,x in list(zip(ys,xs))[:16]]}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',required=True);p.add_argument('--page',type=Path,required=True)
    p.add_argument('--history',type=Path,required=True,help='saved requests.json')
    p.add_argument('--frame',type=int,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--width',type=int,default=1920);p.add_argument('--height',type=int,default=1080)
    p.add_argument('--pure-function-declared',action='store_true',required=True)
    a=p.parse_args()
    if a.output.exists():p.error('output must be a fresh path')
    history=json.loads(a.history.read_text())
    frames=[r['token']['frame'] for r in history]
    try:prefix=frames[:frames.index(a.frame)+1]+[a.frame,a.frame]
    except ValueError:p.error('requested frame is absent from saved history')
    a.output.mkdir(parents=True)
    report={'page_sha256':hashlib.sha256(a.page.read_bytes()).hexdigest(),
        'history_sha256':hashlib.sha256(a.history.read_bytes()).hexdigest(),
        'frames':prefix,'performance_verdict':None,'runs':{}}
    try:
        for name,navigation,order in [('reference',True,[a.frame]),
                ('history',False,prefix),('history-repeat',False,prefix)]:
            directory=a.output/name
            shell=Shell(a.binary,directory,a.width,a.height,navigation=navigation)
            try:
                records=render(shell,a.page,order,directory/'frames',
                               navigation=navigation,diagnostic=True)
                dom=evaluate(shell.tabs[-1],'document.documentElement.outerHTML')
                (directory/'dom.html').write_text(dom)
                report['runs'][name]={'records':records,
                    'dom_sha256':hashlib.sha256(dom.encode()).hexdigest(),
                    'environment':shell.metadata}
            finally:shell.close()
        reference=report['runs']['reference']['records'][-1]['path']
        for result in report['runs'].values():
            result['last_three_differences']=[difference(reference,r['path'])
                                             for r in result['records'][-3:]]
    finally:atomic_json(a.output/'reproduction.json',report)
    print(json.dumps({k:{'dom_sha256':v['dom_sha256'],
        'last_three_differences':v['last_three_differences']} for k,v in report['runs'].items()}))

if __name__=='__main__':main()
