#!/usr/bin/env python3
"""External /proc RSS sampler: runner argv output root, then report destination.

This sums resident pages across the runner and descendants, double-counting
shared pages. A sampling peak is not an exact memory high-water mark.
"""
import json,pathlib,time,sys
needle=sys.argv[1];out=pathlib.Path(sys.argv[2]);root=None
for p in pathlib.Path('/proc').glob('[0-9]*'):
 try:
  a=(p/'cmdline').read_bytes().split(b'\0')
  if len(a)>2 and a[1].endswith(b'experiments/gfx_beginframe/strict.py') and needle.encode() in a:root=int(p.name);break
 except OSError:pass
if root is None:raise SystemExit('runner not found')
samples=[]
while pathlib.Path('/proc',str(root)).exists():
 entries={}
 for p in pathlib.Path('/proc').glob('[0-9]*'):
  try:
   status=(p/'status').read_text().splitlines();fields={l.split(':',1)[0]:l.split(':',1)[1].strip() for l in status if ':' in l};entries[int(p.name)]=(int(fields['PPid']),int(fields.get('VmRSS','0 kB').split()[0]))
  except (OSError,ValueError,KeyError):pass
 ids={root};changed=True
 while changed:
  new={pid for pid,(parent,rss) in entries.items() if parent in ids};changed=not new<=ids;ids|=new
 samples.append({'monotonic':time.monotonic(),'tree_rss_kib':sum(entries.get(pid,(0,0))[1] for pid in ids),'processes':len(ids)})
 time.sleep(.2)
if samples:out.write_text(json.dumps({'scope':'runner and descendants; RSS double-counts shared pages','sample_period_seconds':.2,'peak_tree_rss_kib':max(s['tree_rss_kib'] for s in samples),'samples':samples},indent=2))
