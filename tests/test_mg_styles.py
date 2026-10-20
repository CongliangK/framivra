"""Reusable MG package tests; media checks run separately with real browsers."""
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'templates/mg-styles/build.py'
STYLES = ('cel-boil', 'isometric', 'flat-vector', 'line-art', 'soft-3d',
          'morph', 'sticker', 'hud', 'collage', 'aurora-glass', 'bauhaus',
          'synthwave', 'pixel', 'liquid', 'variety-captions')


class MGStyles(unittest.TestCase):
    def command(self, *args):
        return subprocess.run([sys.executable, str(BUILD), *map(str, args)],
                              capture_output=True, text=True, cwd=ROOT)

    def test_all_styles_with_distinct_content_and_dependency_closure(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            samples = [dict(title='One idea', subtitle='Small first step',
                            items=[dict(label='Build', value=42)]),
                       dict(title='中文长标题<&>可换内容验证', subtitle='"quoted" / 特殊字符',
                            items=[dict(label='计划<&>', value=0), dict(label='完成', value=99),
                                   dict(label='复盘', value=18)])]
            for style in STYLES:
                pages = []
                for n, sample in enumerate(samples):
                    with self.subTest(style=style, sample=n):
                        content = temp / f'{style}-{n}.json'
                        content.write_text(json.dumps(sample, ensure_ascii=False))
                        output = temp / f'{style}-{n}'
                        result = self.command('--style', style, '--output', output,
                                              '--content', content, '--frames', 90)
                        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                        timeline = json.loads((output / 'timeline.json').read_text())
                        self.assertEqual(timeline['version'], 2)
                        self.assertEqual(timeline['project']['fps'], 30)
                        self.assertEqual(timeline['project']['duration_frames'], 90)
                        clips = [c for t in timeline['video_tracks'] for c in t['clips']]
                        self.assertEqual(len(clips), 1)
                        self.assertEqual(clips[0]['dur'], 90)
                        self.assertEqual(clips[0]['dst_in'], 0)
                        page = output / clips[0]['src']
                        self.assertTrue(page.is_file())
                        dependencies = (timeline['project'].get('cache_dependencies', []) +
                                        clips[0].get('cache_dependencies', []))
                        self.assertTrue(dependencies)
                        for dep in dependencies:
                            self.assertTrue((output / dep).is_file(), dep)
                        pages.append(b'\n'.join(p.read_bytes() for p in sorted(output.rglob('*'))
                                                if p.is_file()))
                if len(pages) == 2:
                    self.assertNotEqual(pages[0], pages[1], style)

    def test_invalid_frames_and_unknown_style_fail_without_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            for value in ('0', '-1', '3.5', 'true'):
                output = Path(temp) / value
                result = self.command('--style', 'flat-vector', '--output', output,
                                      '--frames', value)
                self.assertNotEqual(result.returncode, 0, value)
                self.assertFalse(output.exists(), value)
            output = Path(temp) / 'unknown'
            result = self.command('--style', 'unknown', '--output', output)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())

    @unittest.skipUnless(shutil.which('node'), 'Node is required for JS operation replay')
    def test_frame_operation_replay_is_order_independent(self):
        # This catches accumulated Canvas state, not browser pixel differences.
        script = r"""
const fs=require('fs'), vm=require('vm');
const root=process.argv[1];
for(const style of JSON.parse(process.argv[2])) {
  let operations=[], state={}, stack=[];
  const methods={canvas:{},measureText:s=>({width:String(s).length*30}),
    createLinearGradient:()=>({addColorStop(){}}),
    createRadialGradient:()=>({addColorStop(){}}),
    save:()=>stack.push({...state}), restore:()=>{state=stack.pop()||{}}};
  const ctx=new Proxy({}, {get:(o,k)=>k in state?state[k]:methods[k]||((...a)=>operations.push([k,...a])),
    set:(o,k,v)=>{state[k]=v;operations.push(['set',k,v]);return true;}});
  const sandbox={window:{},document:{createElement:()=>({getContext:()=>ctx}),getElementById:()=>({getContext:()=>ctx,toDataURL:()=>''})},
    location:{search:'?f=0'},URLSearchParams,console};
  vm.createContext(sandbox);
  for(const n of ['styles-a.js','styles-b.js','styles-c.js'])
    vm.runInContext(fs.readFileSync(root+'/'+n,'utf8'),sandbox);
  sandbox.window.MG_CONTENT={style,frames:90,title:'中文<&>内容',subtitle:'Different story',
    items:[{label:'One',value:0},{label:'Two',value:99}],
    palette:['#faf5e8','#202730','#e66b42','#49a69d'],seed:7,pace:1};
  vm.runInContext(fs.readFileSync(root+'/runtime.js','utf8'),sandbox);
  const draw=f=>{operations=[];sandbox.window.__vtx_render(f);return JSON.stringify(operations)};
  const first=draw(45); draw(89);draw(0);
  if(first!==draw(45))throw Error(style+' order-dependent operations');
  for(const frame of [0,1,9,45,80,89]) {
    const result=draw(frame);
    if(/null/.test(result))throw Error(style+' non-finite operation '+frame);
    if(stack.length)throw Error(style+' unbalanced save/restore');
  }
}
"""
        result = subprocess.run(['node', '-e', script, str(BUILD.parent), json.dumps(STYLES)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_invalid_content_is_rejected_before_creating_output(self):
        invalid = [dict(items=[]), dict(seed=True), dict(pace=0), dict(pace=True), dict(pace=float('nan')), dict(palette=['red'] * 4),
                   dict(items=[dict(label='bad', value=float('nan'))]),
                   dict(items=[dict(label='bad', value=-1)]), dict(title=4)]
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            for n, content in enumerate(invalid):
                path = temp / f'content-{n}.json'
                path.write_text(json.dumps(content))
                output = temp / f'project-{n}'
                result = self.command('--style', 'flat-vector', '--output', output,
                                      '--content', path)
                self.assertNotEqual(result.returncode, 0, content)
                self.assertFalse(output.exists(), content)

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            marker = output / 'marker'
            marker.write_text('keep')
            result = self.command('--style', 'flat-vector', '--output', output)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(marker.read_text(), 'keep')
            self.assertEqual(list(output.iterdir()), [marker])

    def test_dry_run_does_not_create_project(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'dry'
            result = self.command('--style', 'flat-vector', '--output', output, '--dry')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
