"""Exercise actual Chrome CLI fallback after a deliberately failed CDP backend."""
import argparse
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from experiments.gfx_fast_png.bench import html
from vtx import cli, gfx_render
from vtx.infra import Ctx
from vtx.hygiene import gfx_dir
from vtx.gfx_cli import require_frames

p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
a.output.mkdir(parents=True,exist_ok=False)
(a.output/'page.html').write_text(html('transparent'))
clip=dict(src='page.html',dst_in=0,dur=2)
t=a.output/'timeline.json';t.write_text(json.dumps(dict(version=2,
 project=dict(fps=30,width=1920,height=1080,duration_frames=2),sources=[],
 video_tracks=[dict(name='gfx',kind='gfx',clips=[clip])],subtitles=[],audio_tracks=[])))
with patch.dict(os.environ,dict(VTX_CHROME='/usr/bin/google-chrome',VTX_GFX_CACHE_NS='issue64_cli',VTX_GFX_PAGES='1')):
 c=Ctx(t)
 with patch.object(gfx_render,'render_page',side_effect=gfx_render.GfxRenderError('deliberate CDP failure for real CLI fallback check')):
  cli.gfx(c)
 directory=gfx_dir(c,clip)
 require_frames(directory,range(2),width=1920,height=1080)
 print(json.dumps(dict(frames=2,path=str(directory),complete=(directory/'complete.json').exists())))
