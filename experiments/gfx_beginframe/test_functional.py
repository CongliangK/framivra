"""Execute the real functional entry point with an injected idle CDP screenshot.

Browser operations are replaced at the boundary; main(), PNG decoding, comparison,
report persistence and process exit are real. The negative case changes only the
returned idle PNG pixels. No browser, font installation or performance run occurs.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

DRIVER = r'''
import base64
import hashlib
import io
from pathlib import Path
import sys
from unittest.mock import patch
from PIL import Image
sys.path.insert(0,sys.argv[1])
import functional

def png(color):
    image=Image.new('RGBA',(640,360),color)
    stream=io.BytesIO();image.save(stream,format='PNG')
    return stream.getvalue(),hashlib.sha256(image.tobytes()).hexdigest()
correct,expected=png((10,20,30,255))
wrong,_=png((200,20,30,255))
idle=wrong if sys.argv[3]=='wrong' else correct

class Tab:
    def call(self,method,**params):
        assert method=='HeadlessExperimental.beginFrame'
        if 'screenshot' in params:
            return {'hasDamage':False,'screenshotData':base64.b64encode(idle).decode()}
        return {'hasDamage':False}

class Shell:
    def __init__(self,*args,**kwargs):
        self.tabs=[Tab()];self.metadata={'injected_browser_boundary':True}
    def close(self):pass

def fixture(path):
    path.write_text('<!doctype html>boundary fixture')
    return {'injected_fixture':True}

def render(shell,page,frames,output,**kwargs):
    if frames==[999]:raise RuntimeError('intentional frame error')
    return [{'token':{'frame':frame},'rgba_sha256':expected,
             'hasDamage':False,'canary':{'status':'ok'}} for frame in frames]

output=sys.argv[2]
sys.argv=['functional.py','--binary','unused-boundary-binary','--output',output,'--count','0']
with patch.object(functional,'Shell',Shell),patch.object(functional,'fixture',fixture),patch.object(functional,'render',render):
    raise SystemExit(functional.main())
'''


class IdleCaptureExit(unittest.TestCase):
    def run_entry(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            result=subprocess.run([sys.executable,'-c',DRIVER,
                str(Path(__file__).resolve().parent),directory,mode],
                capture_output=True,text=True,timeout=30)
            report=json.loads((Path(directory)/'functional.json').read_text())
        return result,report

    def test_matching_idle_capture_exits_zero(self):
        result,report=self.run_entry('correct')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(report['ok'])
        self.assertTrue(report['capture_after_idle_same_pixels'])

    def test_wrong_idle_png_makes_actual_entry_point_nonzero(self):
        result,report=self.run_entry('wrong')
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertFalse(report['ok'])
        self.assertFalse(report['capture_after_idle_same_pixels'])
        # All other paths are successful: rejection specifically comes from the
        # corrupted idle screenshot, not an exception or a different failed gate.
        self.assertTrue(all(report['full_frame_exact']))
        self.assertTrue(report['history_independent'])
        self.assertTrue(report['recovery_same_pixels'])
        self.assertEqual(report['canary_statuses'],['ok']*5)
        self.assertEqual(report['idle_no_screenshot'],[{'hasDamage':False}]*3)
        self.assertIn('intentional frame error',report['intentional_error'])

if __name__=='__main__':unittest.main()
