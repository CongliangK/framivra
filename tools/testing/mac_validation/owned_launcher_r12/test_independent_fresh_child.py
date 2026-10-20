"""One approved ordinary mock of non-atomic ps observations during normal exit."""
import json
from pathlib import Path
import unittest
from test_owned_launcher import LauncherResourceIdentityRaceTests

class FreshChildReviewTests(unittest.TestCase):
    def test_fresh_visible_child_of_disappeared_leaf_blocks_recovery(self):
        fixture=LauncherResourceIdentityRaceTests()
        root=fixture.process(fixture.ROOT,1)
        leaf=fixture.process(fixture.LEAF,fixture.ROOT)
        fresh_child=fixture.process(fixture.GRANDCHILD,fixture.LEAF,pgid=fixture.GRANDCHILD,rss=256)
        result=fixture.run_fixture([root,leaf],[root,fresh_child])
        record=dict(status=result.status,samples=result.samples,registry_processes=result.registry.processes,
                    registry_groups=sorted(result.registry.groups),query_count=len(result.calls))
        (Path(__file__).parent/'fresh-visible-child.actual.json').write_text(json.dumps(record,indent=2)+'\n')
        self.assertEqual(len(result.calls),2)
        self.assertNotIn(fixture.GRANDCHILD,result.registry.processes)
        self.assertEqual(result.status['resource_identity_recoveries'][0]['status'],'UNKNOWN',
            'A fresh visible child linked to the disappeared failed PID prevents complete coverage')
        self.assertEqual(result.status['samples'],0)
        self.assertTrue(result.status['stop_reasons'])
        self.assertTrue(result.cleanup.kwargs['request_stop'])
