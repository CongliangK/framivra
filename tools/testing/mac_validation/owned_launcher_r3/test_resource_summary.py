import unittest
from resource_summary import parse_cpu_time, summarize_samples, summarize_time_l, parse_vm_stat_snapshot, summarize_vm_delta

def sample(t=0,pid=12,start=100,cpu='0:02.50'):
    return dict(wall_seconds=t,disk_free_bytes=90000000000,tree_rss_kib=2048,
                processes=[dict(pid=pid,ppid=10,rss_kib=2048,cpu_percent=250.0,start_identity=start,cpu_time=cpu)])

class ResourceSummaryTests(unittest.TestCase):
    def test_macos_cpu_time_formats(self):
        for raw,want in [('0:00.01',.01),('123:45.67',7425.67),('1:02:03.45',3723.45),('2-01:02:03',176523)]:
            self.assertAlmostEqual(parse_cpu_time(raw),want)
        for raw in ['n/a','-0:01.00','00:60','1:60:00','2-25:00:00']:
            with self.assertRaises(ValueError): parse_cpu_time(raw)
    def test_ps_percent_not_integrated_as_exact_cpu(self):
        x=summarize_samples([sample(),sample(5,cpu='0:03.50')])
        self.assertEqual(x['tree_ps_recent_cpu_percent_sum_max'],250)
        self.assertIsNone(x['exact_cpu_seconds_from_ps_percent'])
        self.assertEqual(x['sampled_observed_cpu_seconds_lower_bound'],3.5)
    def test_missing_logical_cpu_keeps_host_percent_unknown(self):
        x=summarize_time_l('10.00 real 15.00 user 5.00 sys\n')
        self.assertEqual(x['accounted_single_core_percent'],200)
        self.assertEqual(x['accounted_cpu_seconds'],20)
        self.assertIsNone(x['host_normalized_cpu_percent'])
        self.assertEqual(summarize_time_l('10.00 real 15.00 user 5.00 sys\n',4)['host_normalized_cpu_percent'],50)
    def test_rss_kib_and_shared_mapping_label(self):
        x=summarize_samples([sample()])
        self.assertEqual(x['sampled_tree_sum_rss_max_bytes'],2097152)
        self.assertIn('shared mappings',x['rss_scope']); self.assertIn('between-sample',x['rss_scope'])
    def test_reused_pid_separate_start_identity(self):
        x=summarize_samples([sample(),sample(5,start=200,cpu='0:01.00')])
        self.assertEqual(x['sampled_observed_cpu_seconds_lower_bound'],3.5)
    def test_exited_pid_preserves_sampled_cpu_lower_bound(self):
        x=summarize_samples([sample(),sample(5,pid=20,start=200,cpu='0:01.00')])
        self.assertEqual(x['sampled_observed_cpu_seconds_lower_bound'],3.5)
        self.assertEqual(x['cumulative_cpu_coverage'],'SAMPLED_LOWER_BOUND')
        self.assertIn('trailing CPU',x['cpu_observation_scope'])
    def test_vm_counter_delta_and_reset_unknown(self):
        s='Mach Virtual Memory Statistics: (page size of 16384 bytes)\nSwapins: 20.\nSwapouts: 10.\nPages occupied by compressor: 50.\n'
        a=parse_vm_stat_snapshot(s); b=parse_vm_stat_snapshot(s.replace('Swapouts: 10.','Swapouts: 12.'))
        x=summarize_vm_delta(a,b)
        self.assertEqual(x['swapout_page_equivalent_bytes'],32768); self.assertIsNone(x['swap_occupied_bytes'])
        x=summarize_vm_delta(b,a)
        self.assertEqual(x['status'],'UNKNOWN'); self.assertIsNone(x['swapout_page_equivalent_bytes'])
    def test_missing_samples_are_not_zero(self):
        x=summarize_samples([])
        self.assertEqual(x['status'],'UNKNOWN'); self.assertIsNone(x['sampled_tree_sum_rss_max_bytes'])
        s=sample(); del s['tree_rss_kib']; x=summarize_samples([s])
        self.assertEqual(x['status'],'UNKNOWN'); self.assertIsNone(x['sampled_tree_sum_rss_max_bytes'])
        s=sample(); del s['processes'][0]['start_identity']; x=summarize_samples([s])
        self.assertIsNone(x['sampled_observed_cpu_seconds_lower_bound']); self.assertEqual(x['cumulative_cpu_coverage'],'UNKNOWN')

if __name__ == '__main__': unittest.main()
