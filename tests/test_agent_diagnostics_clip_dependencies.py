"""Integration contracts for clip dependencies and opt-in diagnostics."""
import contextlib, io, json, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from vtx import cache_state, stages
from vtx.infra import Ctx
from vtx.agent_diagnostics import main

class ClipDependencies(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        (self.root/'exists').write_text('dependency');(self.root/'page.html').write_text('<p>fixture</p>')
    def context(self,deps,kind='gfx'):
        return SimpleNamespace(root=self.root,diagnostics=[],tl={
            'version':2,'project':{'fps':30,'width':64,'height':64,'duration_frames':30},
            'sources':[],'video_tracks':[{'kind':kind,'clips':[{'src':'page.html','dst_in':0,'dur':30,'cache_dependencies':deps}]}],
            'audio_tracks':[],'subtitles':[]})
    def test_invalid_clip_paths_precise_native_message(self):
        for deps,suffix in ((None,''),('exists',''),(['exists',None],'[1]'),([' '],'[0]'),(['x\0y'],'[0]')):
            c=self.context(deps)
            with self.subTest(deps=deps),self.assertRaisesRegex(ValueError,'^clip.cache_dependencies must be a list of nonempty file paths$'):
                cache_state.validate_cache_dependencies(c)
            self.assertEqual(len(c.diagnostics),1)
            self.assertEqual(c.diagnostics[0]['json_path'],'$.video_tracks[0].clips[0].cache_dependencies'+suffix)
    def test_non_gfx_precedes_invalid_list(self):
        c=self.context(None,'aroll')
        with self.assertRaisesRegex(ValueError,'only valid on gfx tracks'):cache_state.validate_cache_dependencies(c)
        self.assertEqual(len(c.diagnostics),1);self.assertEqual(c.diagnostics[0]['threshold'],{'track_kind':'gfx'})
    def test_duplicate_first_origin_and_read_count(self):
        c=self.context(['exists','missing','missing']);c.tl['project']['cache_dependencies']=['exists']
        reads=[];original=Path.is_file
        def check(p):reads.append(p.name);return original(p)
        with patch.object(Path,'is_file',check),self.assertRaises(ValueError):cache_state.validate_cache_dependencies(c)
        self.assertEqual(reads,['exists','missing'])
        self.assertEqual(c.diagnostics[0]['json_path'],'$.video_tracks[0].clips[0].cache_dependencies[1]')
    def test_constructor_and_actual_agent_entrypoint(self):
        for deps in (None,['missing'],['']):
            c=self.context(deps);path=self.root/'timeline.json';path.write_text(json.dumps(c.tl));records=[]
            with self.assertRaises(ValueError):Ctx(path,diagnostics=records)
            self.assertEqual(len(records),1)
            with contextlib.redirect_stdout(io.StringIO()) as output:code=main(['-t',str(path),'validate','--format','agent-json'])
            document=json.loads(output.getvalue());self.assertEqual(code,1)
            self.assertEqual(len(document['diagnostics']),1)
            self.assertEqual(document['diagnostics'][0]['json_path'],records[0]['json_path'])
            self.assertTrue(records[0]['json_path'].startswith('$.video_tracks[0].clips[0].cache_dependencies'))
    def test_validate_recheck_without_duplicate(self):
        c=self.context(['exists']);path=self.root/'timeline.json';path.write_text(json.dumps(c.tl));records=[];ctx=Ctx(path,diagnostics=records)
        (self.root/'exists').unlink()
        with contextlib.redirect_stdout(io.StringIO()),self.assertRaises(SystemExit):stages.validate(ctx)
        found=[r for r in records if r['rule_id']=='load.cache_dependency'];self.assertEqual(len(found),1)
        self.assertEqual(found[0]['json_path'],'$.video_tracks[0].clips[0].cache_dependencies[0]')
    def test_iteration_and_length_hint_order(self):
        calls=[]
        class Values(list):
            def __iter__(self):calls.append('iter');return super().__iter__()
            def __len__(self):calls.append('len');return super().__len__()
        c=self.context(Values(['exists']));cache_state.validate_cache_dependencies(c)
        self.assertEqual(calls,['iter','iter','len']);self.assertEqual(c.diagnostics,[])
    def test_custom_callback_opaque_not_replayed(self):
        error=ValueError('opaque strip callback');calls=[]
        class Value(str):
            def strip(self):calls.append('strip');raise error
        c=self.context([Value('exists')])
        with self.assertRaises(ValueError) as caught:cache_state.validate_cache_dependencies(c)
        self.assertIs(caught.exception,error);self.assertEqual(calls,['strip']);self.assertEqual(c.diagnostics,[])
    def test_public_clip_no_annotation_scan(self):
        c=self.context([])
        class Timeline(dict):
            def get(self,key,*args):
                if key!='project':raise AssertionError('annotation-only scan')
                return super().get(key,*args)
        c.tl=Timeline(c.tl)
        with self.assertRaises(ValueError):cache_state.declared_dependencies(c,{'cache_dependencies':None})
        self.assertEqual(c.diagnostics[0]['json_path'],'$')
if __name__=='__main__':unittest.main()
