"""CPU-only adversarial checks; real integration requires an explicit binary."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).parent))
import run


class FakeTab:
    def __init__(self,result):self.result=result
    def call(self,*args,**kwargs):return self.result


class ProtocolTests(unittest.TestCase):
    def test_evaluation_exception_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError,'page evaluation failed'):
            run.evaluate(FakeTab({'exceptionDetails':{'text':'boom'}}),'x')

    def test_error_remote_object_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError,'page returned error'):
            run.evaluate(FakeTab({'result':{'subtype':'error'}}),'x')

    def test_checkpoint_binds_full_identity_and_awaits_resources(self):
        token={'run':'new','generation':3,'request':14,'frame':22}
        with patch.object(run,'evaluate',return_value={'token':token}) as spy:
            self.assertEqual(run.checkpoint(None,token,invoke=True),{'token':token})
            script=spy.call_args.args[1]
        for clause in ('await window.__vtx_render(token.frame)','await document.fonts.ready',
                       'await i.decode()', 'getBoundingClientRect',json.dumps(token)):
            self.assertIn(clause,script)
        self.assertNotIn('requestAnimationFrame',script)
        self.assertNotIn('setTimeout',script)

    def test_navigation_checkpoint_does_not_call_render(self):
        with patch.object(run,'evaluate') as spy:run.checkpoint(None,{},invoke=False)
        self.assertNotIn('await window.__vtx_render(token.frame)',spy.call_args.args[1])

    def capture(self, *, screenshot=True, stale=False, damage=False):
        import base64
        import io
        import tempfile
        from types import SimpleNamespace
        from PIL import Image
        buf=io.BytesIO();Image.new('RGBA',(16,16),(10,20,30,120)).save(buf,format='PNG')
        data=base64.b64encode(buf.getvalue()).decode()
        tab=FakeTab({})
        def call(method,**kwargs):
            if method=='HeadlessExperimental.beginFrame':
                return {'hasDamage':damage,**({'screenshotData':data} if screenshot else {})}
            return {}
        tab.call=call
        shell=SimpleNamespace(target=lambda:(tab,'target-a'),width=16,height=16)
        identity={}
        def ready(tab,token,**kwargs):
            identity.update(token);return {'token':token}
        def evaluate(tab,script):
            return {'run':'stale'} if stale and script=='window.__beginframe_ack' else (identity if script=='window.__beginframe_ack' else True)
        with tempfile.TemporaryDirectory() as d:
            with patch.object(run,'checkpoint',side_effect=ready),patch.object(run,'evaluate',side_effect=evaluate):
                return run.render(shell,Path(__file__),[22],Path(d)/'frames')

    def test_no_damage_still_requires_own_screenshot(self):
        records=self.capture(damage=False)
        self.assertFalse(records[0]['hasDamage'])
        self.assertEqual(records[0]['token']['frame'],22)

    def test_missing_capture_is_failure(self):
        with self.assertRaisesRegex(RuntimeError,'capture missing'):
            self.capture(screenshot=False)

    def test_late_identity_is_failure(self):
        with self.assertRaisesRegex(RuntimeError,'stale generation'):
            self.capture(stale=True)

    def test_binary_lock_fails_before_launch(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with patch.object(run.subprocess,'check_output',return_value='fake 149.0.7827.55'):
                with patch.object(run.subprocess,'Popen') as launch:
                    with self.assertRaisesRegex(ValueError,'binary differs'):
                        run.Shell(__file__,Path(d)/'out',64,64)
                    launch.assert_not_called()

if __name__=='__main__':unittest.main()
