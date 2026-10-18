"""Editable full-promo source contract. No browser, engine render or audio approval."""
import hashlib,importlib.util,json,shutil,subprocess,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace
from vtx.gfx_dependencies import resolve, identity
ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT/'templates/promo-demo/v1/production'
# Expose the existing decoder class to ordinary and partition discovery.
NativePostmixTests=importlib.import_module(
 'templates.promo-demo.v1.production.tests.test_native_postmix_preflight').NativePostmixTests
class ProductionSourceContracts(unittest.TestCase):
 def test_schema_and_exact_source_bindings(self):
  import jsonschema
  timeline=json.loads((PACKAGE/'timeline.json').read_text())
  jsonschema.validate(timeline,json.loads((ROOT/'vtx/timeline.schema.json').read_text()))
  record=json.loads((PACKAGE/'production/source-bindings.actual.json').read_text())
  self.assertEqual(record['timeline_sha256'],hashlib.sha256((PACKAGE/'timeline.json').read_bytes()).hexdigest())
  self.assertEqual((timeline['project']['duration_frames'],len(timeline['video_tracks']),len(timeline['audio_tracks'])),(3600,15,4))
  for item in record['items']:
   tracks=[t for t in timeline['video_tracks'] if t['name']==item['track_name']];self.assertEqual(len(tracks),1)
   clips=[c for c in tracks[0]['clips'] if c['src']==item['clip_src']];self.assertEqual(len(clips),1)
   for field,value in item['fields'].items():self.assertEqual(clips[0][field],value)
 def test_actual_dependency_identity_changes_without_renderer(self):
  with tempfile.TemporaryDirectory() as tmp:
   tmp=Path(tmp);timeline=json.loads((PACKAGE/'timeline.json').read_text())
   clip=next(c for t in timeline['video_tracks'] if t['name']=='bird-body-head' for c in t['clips'])
   paths=set(timeline['project']['cache_dependencies']+[clip['src']])
   for name in paths:
    (tmp/name).parent.mkdir(parents=True,exist_ok=True);shutil.copy2(PACKAGE/name,tmp/name)
   c=SimpleNamespace(root=tmp,tl=timeline)
   base=identity(resolve(c,clip));self.assertEqual(base,identity(resolve(c,{**clip,'dst_in':99})))
   p=tmp/'runtime/effects.json';original=p.read_bytes();p.write_bytes(original+b'\n')
   self.assertNotEqual(base,identity(resolve(c,clip)));p.write_bytes(original)
   p=tmp/'runtime/shared.js';p.write_bytes(p.read_bytes()+b'\n// identity probe\n')
   self.assertNotEqual(base,identity(resolve(c,clip)))
 def test_generated_dependency_hashes(self):
  for entry in json.loads((PACKAGE/'runtime-dependencies.json').read_text()):
   self.assertEqual(hashlib.sha256((PACKAGE/entry['path']).read_bytes()).hexdigest(),entry['sha256'],entry['path'])
 def preflight_module(self):
  spec=importlib.util.spec_from_file_location('promo_preflight',PACKAGE/'scripts/preflight.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
 def test_required_paper_recipe_and_missing_media_are_explicit_errors(self):
  module=self.preflight_module();paper=PACKAGE/'media/paper-background.mp4'
  recipe=json.loads((PACKAGE/'media/paper-recipe.json').read_text())
  self.assertEqual(recipe['working_directory'],'templates/promo-demo/v1/production/media')
  self.assertEqual(recipe['sha256'],'fc42e42426548fcde8b296c1844a36da8daf63fd5a66224ef6dca5fe16155664')
  original=Path.is_file
  with patch.object(Path,'is_file',lambda p:False if p==paper else original(p)):
   result=module.check()
  self.assertIn('missing required visual source: media/paper-background.mp4',result['source_errors'])
 def test_git_source_payload_excludes_generated_media_and_large_files(self):
  prefix='templates/promo-demo/v1/production/'
  files=subprocess.run(['git','ls-files','-z','--',prefix],cwd=ROOT,capture_output=True,text=True,check=True).stdout.split('\0')
  forbidden={'.flac','.wav','.mp4','.mov','.webm','.m4a','.aac','.mp3','.ogg','.zip','.tar','.gz'}
  self.assertTrue(any(files))
  for name in filter(None,files):
   p=ROOT/name
   self.assertNotIn(p.suffix.lower(),forbidden,name)
   self.assertFalse(p.is_symlink(),name)
   self.assertLessEqual(p.stat().st_size,1024*1024,name)
  for relative in ['audio/T17.flac','audio/T18.flac','audio/T19.flac','audio/T20.flac','media/paper-background.mp4']:
   result=subprocess.run(['git','check-ignore','--',prefix+relative],cwd=ROOT,capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
 def test_missing_audio_does_not_report_source_integrity_success(self):
  module=self.preflight_module();missing={PACKAGE/'audio'/f'T{i}.flac' for i in range(17,21)}
  original=Path.is_file
  with patch.object(Path,'is_file',lambda p:False if p in missing else original(p)):
   result=module.check()
  for ident in range(17,21):
   self.assertIn(f'missing required audio source: audio/T{ident}.flac',result['source_errors'])
  self.assertFalse(result['production_ready'])
 def test_audio_projection_requires_exact_commit_and_new_destination(self):
  script=PACKAGE/'scripts/run_native_audio_projection.py'
  with tempfile.TemporaryDirectory() as tmp:
   out=Path(tmp)/'new-output'
   args=[__import__('sys').executable,str(script),'--repo',str(ROOT),'--project',str(PACKAGE),'--out',str(out),'--expected-commit','0'*40]
   result=subprocess.run(args,capture_output=True,text=True,timeout=30)
   self.assertNotEqual(result.returncode,0)
   self.assertIn('Checkout differs from the explicit reviewed commit',result.stderr)
   self.assertFalse(out.exists())
   help_result=subprocess.run([__import__('sys').executable,str(script),'--help'],capture_output=True,text=True,timeout=30)
   self.assertEqual(help_result.returncode,0)
   self.assertIn('--expected-commit',help_result.stdout)
   project=Path(tmp)/'project';(project/'audio').mkdir(parents=True)
   shutil.copy2(PACKAGE/'timeline.json',project/'timeline.json')
   shutil.copy2(PACKAGE/'audio/stems.json',project/'audio/stems.json')
   commit=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
   args=[__import__('sys').executable,str(script),'--repo',str(ROOT),'--project',str(project),'--out',str(out),'--expected-commit',commit]
   result=subprocess.run(args,capture_output=True,text=True,timeout=30)
   self.assertNotEqual(result.returncode,0)
   self.assertIn('Missing runtime audio:',result.stderr)
   self.assertFalse(out.exists())
 def test_present_truncated_media_and_malformed_waveform_fail(self):
  module=self.preflight_module()
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   for source in PACKAGE.iterdir():
    if source.name!='audio':(root/source.name).symlink_to(source,target_is_directory=source.is_dir())
   (root/'audio').mkdir();manifest=json.loads((PACKAGE/'audio/stems.json').read_text())
   for item in manifest['stems']:
    dest=root/item['path'];dest.write_bytes(b'fLaC'+b'\0'*60);item['sha256']=hashlib.sha256(dest.read_bytes()).hexdigest()
   (root/'audio/stems.json').write_text(json.dumps(manifest));(root/'audio/waveform-S5.json').write_text('{"samples":[]}')
   module.ROOT=root;result=module.check()
   self.assertTrue(any('FLAC/PCM verification' in x for x in result['source_errors']),result)
   self.assertTrue(any('waveform' in x for x in result['source_errors']),result)
 def test_waveform_validator_rejects_missing_and_bad_sample_contracts(self):
  module=self.preflight_module();self.assertTrue(module.waveform_errors({'samples':[]},{}))
  value=json.loads((PACKAGE/'audio/waveform-S5.json').read_text());binding=json.loads((PACKAGE/'audio/native-postmix.json').read_text())
  self.assertEqual(module.waveform_errors(value,binding),[])
  for mutate in [lambda x:x['samples'][1].update(frame=1680),lambda x:x['samples'][4].update(value=float('nan')),lambda x:x['samples'].pop(),lambda x:x['provenance'].update(sha256='invalid')]:
   fixture=json.loads(json.dumps(value));mutate(fixture);self.assertTrue(module.waveform_errors(fixture,binding))
 def test_portable_audio_provenance_cannot_claim_execution_receipt(self):
  module=self.preflight_module()
  value=json.loads((PACKAGE/'audio/native-audio-provenance.json').read_text())
  binding=json.loads((PACKAGE/'audio/native-postmix.json').read_text())
  relevant=binding['audio_relevant_inputs']
  self.assertEqual(module.portable_provenance_errors(value,binding,relevant),[])
  for mutate in [lambda x:x.update(full_execution_proof_included=True),lambda x:x.update(full_project_pass=True),lambda x:x.update(commands=[]),lambda x:x.update(scope='EXECUTION_RECEIPT'),lambda x:x['source_sha256'].update(T17='0'*64),lambda x:x['reference_execution_summary'].update(audio_graph_equivalence_recorded=False),lambda x:x['audio_relevant_inputs']['project'].update(duration_frames=1),lambda x:x['declared_toolchain'].update(audio_profile='/'+'home/example/private')]:
   fixture=json.loads(json.dumps(value));mutate(fixture)
   self.assertTrue(module.portable_provenance_errors(fixture,binding,relevant),fixture)
  self.assertFalse((PACKAGE/'audio/projection-proof.json').exists())
  self.assertFalse((PACKAGE/'audio/native-receipt.json').exists())
 def test_public_source_has_no_author_workspace_metadata(self):
  import re
  files=subprocess.run(['git','ls-files','-z','--','templates/promo-demo/v1/production'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.split('\0')
  patterns=[r'/workspace/scratch/[a-z0-9]+',r'/Users/[^/\s]+/',r'/home/[^/\s]+/',r'/opt/codex/',r'outputs/mac-promo-[0-9]+/',r'Sentinel_',r'sediment://',r'library_file_id',r'/root/',r'[?&](?:sig|token|access_token)=',r'transcript_evidence']
  for name in filter(None,files):
   data=(ROOT/name).read_text()
   for pattern in patterns:self.assertIsNone(re.search(pattern,data),name)
   if name.endswith('.json'):
    for key in ['initial_mem_available_bytes','min_mem_available_bytes','peak_tree_rss_bytes','elapsed_s','remaining_owned_pids']:
     self.assertNotIn('\"'+key+'\"',data,name)
  for name in ['music/validation/resource-summary.json','inputs/story-review-result.json']:
   self.assertFalse((PACKAGE/name).exists(),name)
 def test_actual_source_only_asset_importer_groups(self):
  r=subprocess.run([__import__('sys').executable,'-m','unittest','discover','-s',str(PACKAGE/'tests'),'-p','test_prepare_assets.py'],capture_output=True,text=True,timeout=120)
  self.assertEqual(r.returncode,0,r.stdout+r.stderr)
  self.assertIn('Ran 27 tests',r.stderr)
 def test_native_postmix_binding_and_paired_preflight_groups(self):
  r=subprocess.run([__import__('sys').executable,'-m','unittest','discover','-s',str(PACKAGE/'tests'),'-p','test_*postmix*.py'],capture_output=True,text=True,timeout=180)
  self.assertEqual(r.returncode,0,r.stdout+r.stderr)
  self.assertNotIn('Ran 0 tests',r.stderr)
 @unittest.skipUnless(shutil.which('node'),'Node unavailable: actual source JavaScript checks skipped')
 def test_actual_node_source_groups(self):
  r=subprocess.run(['node','--test',str(PACKAGE/'tests/runtime.test.js'),str(PACKAGE/'tests/integration.test.js')],capture_output=True,text=True,timeout=60)
  self.assertEqual(r.returncode,0,r.stdout+r.stderr)
if __name__=='__main__':unittest.main()
