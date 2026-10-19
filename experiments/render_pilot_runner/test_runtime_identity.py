"""Finite source/code checks on ordinary fixtures; never launch browser/worker."""
import contextlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from . import contract, runner, runtime_identity as r
from .test_continuous300 import plan_fixture


class RuntimeIdentityTests(unittest.TestCase):
    def module(self,root,source):
        path=root/'ordinary_fixture.py';path.write_text(source)
        module=types.ModuleType('ordinary_fixture');module.__file__=str(path)
        exec(compile(source,str(path),'exec'),module.__dict__)
        return module,path

    def test_full_code_nested_constant_and_default_not_just_bytecode(self):
        source='def entry(value=3):\n    def nested():\n        return 17\n    return nested(), value\n'
        with tempfile.TemporaryDirectory() as tmp:
            module,path=self.module(Path(tmp),source)
            self.assertEqual(r.check_callable(module.entry,module,source)['status'],'MATCH_SAME_INTERPRETER')
            original=module.entry
            changed={};exec(compile(source.replace('return 17','return 19'),str(path),'exec'),changed)
            self.assertEqual(changed['entry'].__code__.co_code,original.__code__.co_code)
            saved=original.__code__
            try:
                original.__code__=changed['entry'].__code__
                with self.assertRaisesRegex(ValueError,'complete code'):r.check_callable(original,module,source)
            finally:
                original.__code__=saved
            with patch.object(original,'__defaults__',(4,)),self.assertRaisesRegex(ValueError,'defaults'):
                r.check_callable(original,module,source)
            alien=types.FunctionType(original.__code__,dict(module.__dict__),'entry',(3,))
            with self.assertRaisesRegex(ValueError,'globals/module'):r.check_callable(alien,module,source)

    def test_known_contextmanager_wrapper_is_checked_and_unknown_wrapper_rejected(self):
        source='import contextlib\n@contextlib.contextmanager\ndef entry():\n    yield 1\n'
        with tempfile.TemporaryDirectory() as tmp:
            module,path=self.module(Path(tmp),source)
            self.assertEqual(r.check_callable(module.entry,module,source)['status'],'MATCH_SAME_INTERPRETER')
            def fake():return 1
            fake.__wrapped__=module.entry.__wrapped__
            with self.assertRaisesRegex(ValueError,'Unknown callable wrapper'):r.check_callable(fake,module,source)

    def test_current_worker_sources_runtime_original_delegation_and_restoration(self):
        from experiments.fast_png_pilot import adapter
        from vtx import gfx_render
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'spec.json'
            spec={'plan':plan_fixture(),'namespace':'offline-identity-fixture','output':tmp,
                  'implementation':{'commit':'UNQUALIFIED_OFFLINE_FIXTURE','tree':'UNQUALIFIED_OFFLINE_FIXTURE'}}
            path.write_text(json.dumps(spec))
            binding=r.WorkerBinding(spec,path,'baseline',runner)
            binding.restored()
            self.assertEqual(binding.report['restoration'],'MATCH_FROZEN_REFERENCES_AND_SOURCE')
            self.assertEqual(binding.report['actual_runtime_image']['sha256'],
                             contract.file_hash(binding.report['actual_runtime_image']['resolved']))
            self.assertEqual(len(binding.report['modules']),6)
            target=next(m for m in binding.report['modules'] if m['module']=='vtx.gfx_render')
            self.assertIn('render_page',[f['qualname'] for f in target['callables']])
            tab,browser=adapter._classes(object(),gfx_render.Tab,gfx_render.Browser)
            def wrap(original):
                def observed(*args,**kwargs):return original(*args,**kwargs)
                return observed
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(gfx_render,'Tab',tab))
                stack.enter_context(patch.object(gfx_render,'Browser',browser))
                for name,original in binding.original_helpers.items():stack.enter_context(patch.object(gfx_render,name,wrap(original)))
                self.assertEqual(binding.installed()['status'],'BOUND_ORIGINAL_DELEGATES')
                with self.assertRaisesRegex(ValueError,'restoration'):binding.restored()
            binding.restored()
            with patch.object(gfx_render.Tab.navigate,'__defaults__',(6.0,)),self.assertRaisesRegex(ValueError,'defaults'):
                binding.restored()

    def test_parent_binding_rejects_wrong_namespace_or_unowned_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec={'plan':plan_fixture(),'namespace':'current','output':tmp,'implementation':{'commit':'a','tree':'b'}}
            path=Path(tmp)/'spec.json';path.write_text(json.dumps(spec))
            expected={'pid':123,'pgid':123,'supervisor_starttime':777}
            identity={'status':'BOUND_CURRENT_WORKER','restoration':'MATCH_FROZEN_REFERENCES_AND_SOURCE',
                      **expected,'namespace':'current','arm':'baseline','run':tmp,'implementation':spec['implementation'],
                      'plan_sha256':contract.digest(contract.canonical(spec['plan'])),'spec_sha256':contract.file_hash(path)}
            runner.verify_worker_identity({'runtime_identity':identity},{'worker_identity':expected},spec,'baseline')
            for key,value in [('pid',124),('namespace','other'),('status','UNKNOWN'),('restoration','UNKNOWN')]:
                with self.subTest(key=key),self.assertRaises(ValueError):
                    runner.verify_worker_identity({'runtime_identity':dict(identity,**{key:value})},
                                                  {'worker_identity':expected},spec,'baseline')


if __name__=='__main__':unittest.main()
