"""Reviewable fixture-only repair. Never applies edits to user timelines."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

FIXTURE = Path(__file__).parents[1]/'fixtures/agent-diagnostics/timeline.invalid.json'


def run_fixture():
    """Repair only a freshly created copy of the exact checked-in synthetic fixture."""
    with tempfile.TemporaryDirectory(prefix='vtx-diagnostics-fixture-') as directory:
        root = Path(directory)
        path = root/'timeline.json'
        shutil.copyfile(FIXTURE,path)
        def validate():
            result=subprocess.run([sys.executable,'-m','vtx.agent_diagnostics','-t',str(path),
                                   'validate','--format','agent-json'],capture_output=True,text=True)
            report=json.loads(result.stdout)
            assert report['exit_code']==result.returncode
            return report
        before=validate()
        assert before['exit_code']==1
        data=json.loads(path.read_text())
        # No generic JSON patch interpreter or inference about creative intent.
        # Every permitted repair below is explicit, tied to this synthetic asset.
        allowed={
          ('restore_resource','$.sources[0].path'),
          ('add_clip','$.video_tracks[0].clips'),
          ('adjust_interval','$.video_tracks[1].clips[0]'),
          ('restore_resource','$.video_tracks[1].clips[0].src'),
          ('adjust_interval','$.subtitles[0]'),
          ('provide_field','$.audio_tracks[0].events[0]["volume"]'),
          ('choose_source','$.audio_tracks[0].events[0].src'),
        }
        applied=[]
        for diagnostic in before['diagnostics']:
            action=diagnostic['suggested_actions'][0]
            pair=(action['op'], action['params']['json_path'])
            assert pair in allowed, pair
            if pair==('restore_resource','$.sources[0].path'):
                with wave.open(str(root/'missing.wav'),'wb') as wav:
                    wav.setnchannels(2);wav.setsampwidth(2);wav.setframerate(48000)
                    wav.writeframes(b'\x00'*(48000*3*4))
            elif pair[0]=='add_clip':
                data['video_tracks'][0]['clips']=[{'src':'missing.html','dst_in':0,'dur':90}]
            elif pair==('adjust_interval','$.video_tracks[1].clips[0]'):
                data['video_tracks'][1]['clips'][0]['dst_in']=0
            elif pair[0]=='restore_resource':
                (root/'missing.html').write_text('<!doctype html><title>Diagnostic fixture</title><p>Fixture</p>')
            elif pair==('adjust_interval','$.subtitles[0]'):
                data['subtitles'][0]['in']=0
            elif pair[0]=='provide_field':
                data['audio_tracks'][0]['events'][0]['volume']=1
            elif pair[0]=='choose_source':
                data['audio_tracks'][0]['events'][0]['src']='fixture_audio'
            applied.append({'rule_id':diagnostic['rule_id'],'action':action})
        assert set((a['action']['op'],a['action']['params']['json_path']) for a in applied)==allowed
        path.write_text(json.dumps(data,indent=2)+'\n')
        after=validate()
        assert after['exit_code']==0, after
        assert not any(x['severity']=='error' for x in after['diagnostics'])
        return {'before':before,'applied':applied,'repaired_timeline':data,'after':after}


class FixtureRepair(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffprobe'),'requires CPU ffprobe')
    def test_seven_rule_repair_reaches_actual_validate_green(self):
        report=run_fixture()
        self.assertGreaterEqual(len({x['rule_id'] for x in report['before']['diagnostics']}),5)
        self.assertIn('validate.source_exists',{x['rule_id'] for x in report['before']['diagnostics']})


if __name__=='__main__':
    print(json.dumps(run_fixture(),ensure_ascii=False,indent=2,allow_nan=False))
