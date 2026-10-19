#!/usr/bin/env python3
"""Explicit ordinary filesystem/contract tests. No browser or media process.

Run directly with --bound-project pointing to the reviewed native snapshot.
These tests are not registered with the production CI selection inventory.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).absolute().parents[1]
_spec = importlib.util.spec_from_file_location('group_prepare',HERE/'prepare.py')
PREP = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PREP)
BOUND = None


class PreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if BOUND is None:
            raise RuntimeError('Run this explicit experiment test with --bound-project; no automatic CI coverage is claimed')
        cls.spec,cls.payload,cls.manifest = PREP.verify_payload(BOUND)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='promo-group-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make_bound(self):
        project=self.root/'bound'
        project.mkdir()
        for rel,data in self.payload.items():
            path=project/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        return project

    def test_exact_bound_and_all_original_entries_preserved(self):
        output=self.root/'prepared'
        plan=PREP.prepare(BOUND,output)
        for relative,data in self.payload.items():
            self.assertEqual((output/'project'/relative).read_bytes(),data,relative)
        self.assertEqual(len(list((output/'project/gfx').glob('*.html'))),20)
        self.assertEqual((output/'project/timeline.json').read_bytes(),self.payload['timeline.json'])
        self.assertEqual(plan['ordered_members'][0]['local_frame_offset'],-18)
        self.assertEqual(plan['ordered_members'][1]['local_frame_offset'],0)
        self.assertEqual(plan['comparison']['rgba'],'UNRUN')
        self.assertFalse(plan['production_ready'])
        for path in self.spec['excluded_runtime_media']:
            self.assertFalse((output/'project'/path).exists())
        page=(output/'project'/PREP.PAGE).read_text()
        self.assertLess(page.index('id="group-T08"'),page.index('id="group-T07"'))
        self.assertIn('viewBox="0 0 1280 720"',page)
        self.assertIn('#group-failure',page)
        self.assertNotIn('__vtx_render',page)

    def test_old_unbound_cloud_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'reviewed native snapshot'):
            PREP.prepare(PREP.PRODUCTION,self.root/'never-created')
        self.assertFalse((self.root/'never-created').exists())

    def test_each_of_seven_native_binding_changes_is_required(self):
        project=self.make_bound()
        for relative in PREP.BINDER.DERIVED:
            with self.subTest(path=relative):
                target=project/relative
                target.write_bytes((PREP.PRODUCTION/relative).read_bytes())
                with self.assertRaisesRegex(ValueError,'reviewed native snapshot'):
                    PREP.prepare(project,self.root/'rejected')
                self.assertFalse((self.root/'rejected').exists())
                target.write_bytes(self.payload[relative])

    def test_missing_or_changed_payload_is_rejected_before_output(self):
        project=self.make_bound()
        target=project/'src/runtime/runtime.js'
        target.write_bytes(target.read_bytes()+b'\n// drift\n')
        with self.assertRaisesRegex(ValueError,'reviewed native snapshot'):
            PREP.prepare(project,self.root/'changed')
        target.unlink()
        with self.assertRaises(OSError):PREP.prepare(project,self.root/'missing')
        self.assertFalse((self.root/'changed').exists())
        self.assertFalse((self.root/'missing').exists())

    def test_symlinked_input_and_output_are_rejected(self):
        project=self.make_bound()
        target=project/'src/runtime/runtime.js';target.unlink()
        target.symlink_to(BOUND/'src/runtime/runtime.js')
        with self.assertRaises(OSError):PREP.prepare(project,self.root/'input-link')
        target=self.root/'output-link';target.symlink_to(self.root/'absent')
        with self.assertRaises(OSError):PREP.prepare(BOUND,target)
        self.assertFalse((self.root/'absent').exists())

    def test_existing_output_and_input_nested_output_are_rejected(self):
        output=self.root/'existing';output.mkdir();(output/'sentinel').write_text('keep')
        with self.assertRaises(OSError):PREP.prepare(BOUND,output)
        self.assertEqual((output/'sentinel').read_text(),'keep')
        with self.assertRaisesRegex(ValueError,'outside the original project'):
            PREP.prepare(BOUND,BOUND/'nested-experiment')

    def test_failed_output_never_publishes_plan(self):
        output=self.root/'failed'
        original=PREP.BINDER.OwnedOutput.write
        def fail(owned,relative,data,mode=0o644):
            if relative=='project/'+PREP.ADAPTER:raise OSError('injected write failure')
            return original(owned,relative,data,mode)
        with mock.patch.object(PREP.BINDER.OwnedOutput,'write',fail):
            with self.assertRaisesRegex(OSError,'injected write failure'):
                PREP.prepare(BOUND,output)
        self.assertFalse((output/'group-plan.json').exists())
        self.assertFalse(output.exists())

    def test_cache_requires_environment_and_binds_all_content(self):
        plan=PREP.prepare(BOUND,self.root/'prepared')
        first=PREP.cache_identity(plan,'a'*64)
        self.assertNotEqual(first,PREP.cache_identity(plan,'b'*64))
        for bad in ['',None,'a'*63,'unknown']:
            with self.assertRaises(ValueError):PREP.cache_identity(plan,bad)
        plan['ordered_members'][0]['local_frame_offset']=-17
        with self.assertRaisesRegex(ValueError,'frame mapping differs'):
            PREP.cache_identity(plan,'a'*64)
        plan['ordered_members'][0]['local_frame_offset']=-18
        plan['content']['ordered_members'][0]['windowStart']=17
        with self.assertRaisesRegex(ValueError,'content identity mismatch'):
            PREP.cache_identity(plan,'a'*64)

    def test_output_is_deterministic_and_separate(self):
        a=PREP.prepare(BOUND,self.root/'a');b=PREP.prepare(BOUND,self.root/'b')
        self.assertEqual(a,b)
        self.assertEqual((self.root/'a/group-plan.json').read_bytes(),(self.root/'b/group-plan.json').read_bytes())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bound-project',required=True,type=Path)
    args,rest=parser.parse_known_args()
    BOUND=args.bound_project.absolute()
    unittest.main(argv=[__file__,*rest])
