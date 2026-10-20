"""One explicitly approved, entirely mocked normal-lifecycle regression case."""
import json
from pathlib import Path
import unittest
from test_owned_launcher import LauncherResourceIdentityRaceTests

class IndependentResourceRaceReviewTests(unittest.TestCase):
    def test_fresh_retry_preserves_already_verified_descendant_after_parent_exit(self):
        fixture=LauncherResourceIdentityRaceTests()
        parent_pid=876546
        root=fixture.process(fixture.ROOT,1)
        parent=fixture.process(parent_pid,fixture.ROOT,pgid=parent_pid)
        child=fixture.process(fixture.GRANDCHILD,parent_pid,pgid=fixture.GRANDCHILD,rss=256)
        failed_leaf=fixture.process(fixture.LEAF,fixture.ROOT)
        fresh_child=fixture.process(fixture.GRANDCHILD,1,pgid=fixture.GRANDCHILD,rss=256)
        result=fixture.run_fixture([root,parent,child,failed_leaf],[root,fresh_child],
            identities={fixture.ROOT:100,parent_pid:400,fixture.GRANDCHILD:300,fixture.LEAF:None})
        record=dict(status=result.status,samples=result.samples,registry_processes=result.registry.processes,
                    registry_groups=sorted(result.registry.groups),query_count=len(result.calls))
        (Path(__file__).parent/'ordinary-reparent.actual.json').write_text(json.dumps(record,indent=2)+'\n')
        self.assertEqual(len(result.calls),2)
        self.assertIn(fixture.GRANDCHILD,result.samples[0]['verified_pids'])
        recovery=result.status['resource_identity_recoveries'][0]
        if recovery['status']=='FRESH_COMPLETE':
            complete=result.samples[-1]
            self.assertIn(fixture.GRANDCHILD,{p['pid'] for p in complete['processes']},
                'FRESH_COMPLETE silently omitted the already verified, still-live normally reparented child')
            self.assertEqual(complete['tree_rss_bytes'],(128+256)*1024)
            self.assertIn(fixture.GRANDCHILD,result.registry.groups)
        else:
            self.assertEqual(recovery['status'],'UNKNOWN')
            self.assertTrue(result.status['stop_reasons'])
            self.assertTrue(result.cleanup.kwargs['request_stop'])
