"""Ordinary JSON/list/mock regressions only; no links, path attacks or browser."""
import contextlib
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from . import contract as c, group_contract as g, runner


class GroupContractTests(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        self.temp=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.plan_file=self.temp/'group-plan.json';self.project=self.temp/'project'
        self.repo=Path(c.__file__).resolve().parents[2];self.original=Path('/mock-bound')
        self.members=[
            {'entryId':'T08.01','layerId':'T08','role':'bird-wings','src':'gfx/T08_0018_3600.html',
             'windowStart':18,'duration':3582,'paintIndex':6},
            {'entryId':'T07.01','layerId':'T07','role':'bird-body-head','src':'gfx/T07_0000_3600.html',
             'windowStart':0,'duration':3600,'paintIndex':7}]
        paths=['entry-manifest.json','runtime/shared.js',*[m['src'] for m in self.members]]
        paths += ['fixture/input%03d.json'%n for n in range(105)]
        self.inputs={name:json.dumps({'ordinary_mock':name}).encode() for name in paths}
        input_rows=[{'path':name,'sha256':c.digest(data)} for name,data in self.inputs.items()]
        self.bound={'root':str(self.original),'inventory_path':'mock-inventory.json',
                    'payload':{r['path']:r['sha256'] for r in input_rows},'entries':self.members}
        self.implementation={name:('mock implementation '+name).encode() for name in g.IMPLEMENTATION}
        self.implementation[g.SPEC]=json.dumps({'source_commit':c.BASE_COMMIT,'source_tree':c.BASE_TREE,
                                              'bound_payload':input_rows}).encode()
        self.pins={name:c.digest(data) for name,data in self.implementation.items()}
        self.html=b'<html>known pure generator output</html>'
        self.generated={g.PAGE:self.html,g.ADAPTER:self.implementation[g.SOURCE_ADAPTER]}
        self.options={'fixture':'original bound options'}
        self.generator=SimpleNamespace(PAGE=g.PAGE,ADAPTER=g.ADAPTER,MEMBERS=self.members,
            original_options=lambda payload:(self.options,'mock original css'),
            make_page=lambda options,css:self.html)
        self.files={str(self.original):dict(self.inputs),str(self.project):{**self.inputs,**self.generated},
                    str(self.repo):dict(self.implementation)}
        def read(root,name):
            try:return self.files[str(root)][name]
            except KeyError:raise FileNotFoundError('ordinary mocked missing file: '+name)
        self.stack.enter_context(patch.object(g,'IMPLEMENTATION',self.pins))
        self.stack.enter_context(patch.object(g,'read_file',side_effect=read))
        self.stack.enter_context(patch.object(g,'load_generator',return_value=self.generator))
        content={'source_commit':c.BASE_COMMIT,'source_tree':c.BASE_TREE,'group_id':'T08_T07',
                 'ordered_members':copy.deepcopy(self.members),'runtime_options':copy.deepcopy(self.options),
                 'width':c.WIDTH,'height':c.HEIGHT,'fps':c.FPS,'window_start':0,'duration':3600,
                 'input_dependencies':input_rows,
                 'generated_dependencies':[{'path':name,'sha256':c.digest(data)} for name,data in sorted(self.generated.items())],
                 'implementation':[{'path':name,'sha256':sha} for name,sha in self.pins.items()]}
        self.gp={'schema':'promo-gfx-grouping/1','project':'project','page':g.PAGE,
                 'source_commit':c.BASE_COMMIT,'source_tree':c.BASE_TREE,'group_id':'T08_T07',
                 'width':c.WIDTH,'height':c.HEIGHT,'fps':c.FPS,'window_start':0,'duration':3600,
                 'ordered_members':[dict(m,local_frame_offset=-m['windowStart']) for m in self.members],
                 'original_entry_manifest_sha256':self.bound['payload']['entry-manifest.json'],'content':content}
        self.write(self.gp)

    def write(self,gp):
        gp['content_identity']=c.digest(g.json_bytes(gp['content']))
        self.plan_file.write_text(json.dumps(gp))

    def build(self):
        return c.build_plan(self.bound,'group',self.plan_file)

    def test_complete_known_sets_positive_and_frozen_summary(self):
        plan=self.build();verified=plan['group_verified']
        self.assertEqual(verified['generator_commit'],g.GROUP_COMMIT)
        self.assertEqual(verified['generator_tree'],g.GROUP_TREE)
        self.assertEqual(verified['generated'],{name:c.digest(data) for name,data in self.generated.items()})
        self.assertEqual(verified['implementation'],self.pins)
        self.assertEqual(verified['bound_payload_count'],109)
        self.assertEqual(set(verified['generated']),{g.PAGE,g.ADAPTER})
        self.assertEqual(len(verified['implementation']),6)
        self.assertTrue(all(not p.startswith('experiments/render_pilot_runner/') for p in self.pins))
        with patch.object(runner,'read_bound',return_value=self.bound):runner.verify_plan(plan)

    def test_fast_png_plan_remains_independent(self):
        with patch.object(g,'validate_group') as validate:
            plan=c.build_plan(self.bound,'fast-png')
        validate.assert_not_called()
        self.assertIsNone(plan['group_verified']);self.assertEqual(len(plan['jobs']),8)

    def test_missing_duplicate_extra_reordered_and_bad_hash_lists_rejected(self):
        for field in ('input_dependencies','generated_dependencies','implementation'):
            for change in ('missing','empty','duplicate','extra','reverse','hash'):
                gp=copy.deepcopy(self.gp);rows=gp['content'][field]
                if change=='missing':rows.pop(0)
                elif change=='empty':rows.clear()
                elif change=='duplicate':rows.append(copy.deepcopy(rows[0]))
                elif change=='extra':rows.append({'path':'ordinary-extra.json','sha256':'0'*64})
                elif change=='reverse':rows.reverse()
                elif change=='hash':rows[0]['sha256']='0'*64
                self.write(gp)  # malicious omission can still have a valid self hash
                with self.subTest(field=field,change=change),self.assertRaisesRegex(ValueError,'complete reviewed'):
                    self.build()

    def test_self_consistent_changed_generated_bytes_rejected(self):
        for name in self.generated:
            with self.subTest(name=name):
                changed=b'changed ordinary fixture bytes'
                gp=copy.deepcopy(self.gp)
                next(r for r in gp['content']['generated_dependencies'] if r['path']==name)['sha256']=c.digest(changed)
                self.files[str(self.project)][name]=changed;self.write(gp)
                with self.assertRaisesRegex(ValueError,'complete reviewed'):self.build()
                self.files[str(self.project)][name]=self.generated[name]

    def test_actual_derived_change_after_freeze_rejected_by_verify_plan(self):
        plan=self.build()
        for name in self.generated:
            with self.subTest(name=name):
                self.files[str(self.project)][name]=b'changed after frozen plan'
                with patch.object(runner,'read_bound',return_value=self.bound):
                    with self.assertRaisesRegex(ValueError,'generated bytes'):runner.verify_plan(plan)
                self.files[str(self.project)][name]=self.generated[name]

    def test_actual_implementation_change_not_authorized_by_self_hash(self):
        for name in self.pins:
            with self.subTest(name=name):
                gp=copy.deepcopy(self.gp);changed=b'changed implementation'
                next(r for r in gp['content']['implementation'] if r['path']==name)['sha256']=c.digest(changed)
                self.files[str(self.repo)][name]=changed;self.write(gp)
                with self.assertRaisesRegex(ValueError,'Reviewed group implementation changed'):self.build()
                self.files[str(self.repo)][name]=self.implementation[name]

    def test_missing_actual_file_and_source_mapping_rejected(self):
        for name in self.generated:
            with self.subTest(name=name):
                saved=self.files[str(self.project)].pop(name)
                with self.assertRaises(FileNotFoundError):self.build()
                self.files[str(self.project)][name]=saved
        altered=copy.deepcopy(self.bound);altered['payload']['runtime/shared.js']='0'*64
        with self.assertRaisesRegex(ValueError,'source mapping'):
            c.build_plan(altered,'group',self.plan_file)

    def test_metadata_and_frame_offset_disagreement_rejected(self):
        for field,value in [('runtime_options',{}),('source_tree','0'*40),('group_id','other')]:
            gp=copy.deepcopy(self.gp);gp['content'][field]=value;self.write(gp)
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'complete reviewed'):self.build()
        gp=copy.deepcopy(self.gp);gp['ordered_members'][0]['local_frame_offset']=0;self.write(gp)
        with self.assertRaisesRegex(ValueError,'local-frame'):self.build()


if __name__=='__main__':unittest.main()
