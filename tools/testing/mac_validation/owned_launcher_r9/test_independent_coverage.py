"""One explicitly approved ordinary mock with two candidate-coverage subcases."""
import json
from pathlib import Path
import unittest
from test_owned_launcher import LauncherResourceIdentityRaceTests

class CandidateCoverageReviewTests(unittest.TestCase):
    def test_unverified_original_branch_blocks_leaf_recovery(self):
        fixture=LauncherResourceIdentityRaceTests()
        parent_pid=876546
        root=fixture.process(fixture.ROOT,1)
        parent=fixture.process(parent_pid,fixture.ROOT,pgid=parent_pid)
        child=fixture.process(fixture.GRANDCHILD,parent_pid,pgid=fixture.GRANDCHILD,rss=256)
        leaf=fixture.process(fixture.LEAF,fixture.ROOT)
        fresh_reparented=fixture.process(fixture.GRANDCHILD,1,pgid=fixture.GRANDCHILD,rss=256)
        cases=[('original_unverified_branch',[root,leaf,parent,child],[root,fresh_reparented],1),
               ('fresh_child_of_absent_verified_parent',[root,parent,leaf],[root,child],2)]
        records=[]
        for label,original,fresh,expected_queries in cases:
            with self.subTest(lifecycle=label):
                result=fixture.run_fixture(original,fresh,
                    identities={fixture.ROOT:100,parent_pid:400,fixture.GRANDCHILD:300,fixture.LEAF:None})
                records.append(dict(label=label,status=result.status,samples=result.samples,
                    registry_processes=result.registry.processes,registry_groups=sorted(result.registry.groups),
                    query_count=len(result.calls)))
                self.assertEqual(result.status['resource_identity_recoveries'][0]['status'],'UNKNOWN',
                    'An observed candidate without verified coverage must block FRESH_COMPLETE')
                self.assertEqual(result.status['samples'],0)
                self.assertTrue(result.status['stop_reasons'])
                self.assertTrue(result.cleanup.kwargs['request_stop'])
                self.assertEqual(len(result.calls),expected_queries)
                self.assertNotIn(fixture.GRANDCHILD,result.registry.processes)
        (Path(__file__).parent/'candidate-coverage.actual.json').write_text(json.dumps(records,indent=2)+'\n')
