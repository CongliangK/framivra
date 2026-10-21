"""Every bundled validator branch preserves its message and source evidence."""
import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from vtx.diagnostics import DiagnosticMessage
from vtx.pluginlib import hooks


class Plugins(unittest.TestCase):
    def check(self, plugin, timeline, expected):
        module=importlib.import_module('plugins.'+plugin)
        records=module.validate(SimpleNamespace(tl=timeline,diagnostics=[]))
        by_rule={m.metadata['rule_id']:m for m in records}
        self.assertTrue(set(expected) <= set(by_rule),(expected,by_rule))
        for message in records:
            self.assertIsInstance(message,str)
            self.assertTrue(message.metadata['json_path'].startswith('$'))
            self.assertIn('measured',message.metadata)
            self.assertIn('threshold',message.metadata)
            self.assertTrue(message.metadata['suggested_actions'])
        return by_rule

    def test_bgm_all_branches(self):
        module=importlib.import_module('plugins.bgm-render'); key=module.KEY
        self.check('bgm-render',{'sources':[{'path':'song.json',key:False}]},['plugin.bgm.type'])
        self.check('bgm-render',{'sources':[{'path':'song.wav',key:{'oops':1,'output':'../x','extra_deps':True}}]},
                   ['plugin.bgm.path','plugin.bgm.keys','plugin.bgm.output','plugin.bgm.dependencies'])

    def test_blender_all_branches(self):
        module=importlib.import_module('plugins.blender-bridge'); key=module.KEY
        self.check('blender-bridge',{key:False},['plugin.blender.type'])
        self.check('blender-bridge',{key:{'out':'../x','oops':1,'verify':1}},
                   ['plugin.blender.output_path','plugin.blender.keys','plugin.blender.verify'])
        self.check('blender-bridge',{key:{'out':None}},['plugin.blender.output_type'])

    def test_duck_all_branches(self):
        module=importlib.import_module('plugins.duck-static'); key=module.KEY
        self.check('duck-static',{'_meta':{'bed_static_duck':True},'audio_tracks':[
            {'name':'bed','duck':True,key:False}]},
            ['plugin.duck.double_application','plugin.duck.exclusive','plugin.duck.type'])
        value={'mode':'bad','voice_track':'missing'}
        value.update({k:True for k in module._NUMERIC + module._INTLIKE})
        self.check('duck-static',{'audio_tracks':[{'name':'bed',key:value}]},
                   ['plugin.duck.mode','plugin.duck.voice_track','plugin.duck.numeric','plugin.duck.frames'])

    def test_hook_keeps_metadata(self):
        message=DiagnosticMessage('original',rule_id='plugin.precise',json_path='$.x')
        module=SimpleNamespace(validate=lambda c:[message,'opaque'])
        with patch.object(hooks,'active_plugins',return_value=[{'name':'fixture'}]), \
             patch.object(hooks,'_plugin_hooks_declared',return_value=['validate']), \
             patch.object(hooks,'_load_plugin_module',return_value=module):
            result=hooks.run_validate(SimpleNamespace())
        self.assertIs(result[0],message)
        self.assertEqual(result[1],'opaque')


if __name__=='__main__':unittest.main()
