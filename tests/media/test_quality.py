"""Quality labels cannot silently upgrade technical QC into visual evidence."""
import json
import itertools
from pathlib import Path
import tempfile
import unittest

from vtx.runtime.quality import classify, qc_quality, receipt_quality
from vtx.pipeline.infra import Ctx


class Quality(unittest.TestCase):
    def test_four_labels_and_priority(self):
        self.assertEqual(classify(scope='x', preview=True)['display'], 'Preview')
        e = {'production_gates': 'passed', 'visual_content': 'passed'}
        self.assertEqual(classify(scope='x', evidence=e)['display'], 'Production')
        e.update(content_identity='passed', environment_identity='passed', repeat_comparison='passed')
        q = classify(scope='x', evidence=e)
        self.assertEqual(q['display'], 'Reproducible')
        self.assertEqual(q['purpose'], 'Production')
        e['strict_exact'] = 'passed'
        self.assertEqual(classify(scope='x', evidence=e)['display'], 'Verified Exact')

    def test_all_status_combinations_have_one_deterministic_display(self):
        from vtx.runtime.quality import FIELDS, STATUSES
        for values in itertools.product(sorted(STATUSES), repeat=len(FIELDS)):
            evidence = dict(zip(FIELDS, values))
            q = classify(scope='fixture', evidence=evidence)
            self.assertEqual(q, classify(scope='fixture', evidence=evidence))
            expected = ('Verified Exact' if q['verified_exact'] else
                        'Reproducible' if q['reproducible'] else q['purpose'])
            self.assertEqual(q['display'], expected)

    def test_missing_and_invalid_evidence_never_upgrades(self):
        for e in ({}, {'strict_exact': 'passed'}, {'content_identity': 'passed',
                  'environment_identity': 'passed'}, {'strict_exact': True},
                  {'visual_content': ['passed']}, {'repeat_comparison': 'skipped'}):
            self.assertEqual(classify(scope='x', evidence=e)['display'], 'unknown')
        for state in ('failed', 'partial', 'unknown'):
            q = classify(scope='x', evidence={'content_identity': 'passed',
                'environment_identity': 'passed', 'repeat_comparison': state})
            self.assertFalse(q['reproducible'])

    def test_qc_eight_passes_is_not_visual_acceptance(self):
        q = qc_quality({'checks': [{'pass': True}] * 8})
        self.assertEqual(q['display'], 'unknown')
        self.assertEqual(q['evidence']['production_gates'], 'passed')
        self.assertEqual(q['evidence']['visual_content'], 'unknown')
        self.assertEqual(qc_quality({'checks': [{'pass': True}], 'skipped_probes': ['cached']})['evidence']['production_gates'], 'partial')
        self.assertEqual(qc_quality({'checks': []})['evidence']['production_gates'], 'unknown')
        self.assertEqual(qc_quality({'checks': [{'pass': False}]})['evidence']['production_gates'], 'failed')

    def test_receipt_mutations_refresh_annotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / 'timeline.json'
            p.write_text(json.dumps({'project': {'fps': 30, 'width': 64, 'height': 48,
                'duration_frames': 4}, 'sources': [], 'video_tracks': []}))
            c = Ctx(p)
            c.receipt('video_draft', True)
            rec = lambda: json.loads((root / 'out/receipt.json').read_text())
            self.assertEqual(rec()['quality']['display'], 'Preview')
            c.receipt('mux', True)
            c.receipt('qc', True, '8/8')
            self.assertEqual(rec()['quality'], receipt_quality(rec()))
            self.assertEqual(rec()['quality']['display'], 'unknown')
            c.begin_stage('qc')
            self.assertEqual(rec()['quality']['evidence']['production_gates'], 'unknown')
            c.receipt('qc', False)
            self.assertEqual(rec()['quality']['evidence']['production_gates'], 'failed')

    def test_changed_inputs_do_not_retain_draft_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / 'timeline.json'
            timeline = {'project': {'fps': 30, 'width': 64, 'height': 48,
                'duration_frames': 4}, 'sources': [], 'video_tracks': []}
            p.write_text(json.dumps(timeline))
            Ctx(p).receipt('video_draft', True)
            timeline['project']['duration_frames'] = 5
            p.write_text(json.dumps(timeline))
            Ctx(p).receipt('validate', True)
            record = json.loads((root / 'out/receipt.json').read_text())
            self.assertEqual(record['quality']['display'], 'unknown')
            self.assertNotIn('video_draft', record['stages'])

    def test_malformed_prior_observations_do_not_break_receipt_mutations(self):
        # The twelve malformed cases also run against unchanged base externally.
        missing = object()
        for stage, value, operation in itertools.product(
                ('qc', 'video_draft', 'mux_draft'),
                (None, [], 'corrupt', 1, {}, missing), ('receipt', 'begin_stage')):
            with self.subTest(stage=stage, value=value, operation=operation):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    p = root / 'timeline.json'
                    p.write_text(json.dumps({'project': {'fps': 30, 'width': 64,
                        'height': 48, 'duration_frames': 4},
                        'sources': [], 'video_tracks': []}))
                    c = Ctx(p)
                    c.receipt('validate', True)
                    rp = root / 'out/receipt.json'
                    record = json.loads(rp.read_text())
                    if value is not missing:
                        record['stages'][stage] = value
                    rp.write_text(json.dumps(record))
                    if operation == 'receipt':
                        c.receipt('validate', True)
                    else:
                        c.begin_stage('audit')
                    after = json.loads(rp.read_text())
                    self.assertEqual(after['quality']['display'], 'unknown')
                    self.assertEqual(after['quality']['evidence']['production_gates'], 'unknown')
                    if value is not missing:
                        self.assertEqual(after['stages'].get(stage), value)
                    self.assertTrue(after['stages']['validate']['ok'])
