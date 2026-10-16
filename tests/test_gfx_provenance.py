"""Publication hooks and rejection rules; synthetic PNGs are not acceptance."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from vtx import events, gfx_render, gfx_provenance
from tests.test_execution_events import Buffer
from tests.test_gfx_cli_fallback import MINIMAL_PNG


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)

    def ledger(self, count=2, operations=False):
        stream = Buffer()
        execution = events.ExecutionContext(stream, lifecycle=True, lifecycle_operations=operations)
        ledger = gfx_provenance.PublicationLedger(execution, str(events.uuid.uuid4()), [
            {'src': 'page.html', 'dur': count, 'gfx_key': 'a' * 12, 'frame_dir': str(self.directory)}])
        return execution, stream, ledger

    def write(self, index, **kwargs):
        if kwargs.get('origin') == 'static_copy' and 'source_token' not in kwargs:
            kwargs['source_token'] = gfx_provenance.before_static_read(
                self.directory / f"f{kwargs['copy_head']:04d}.png")
        gfx_render._atomic_write_bytes(self.directory / f'f{index:04d}.png', MINIMAL_PNG, **kwargs)

    def test_common_sparse_bridge_records_actual_writes_without_lifecycle_operations(self):
        execution, stream, ledger = self.ledger()
        with execution.task('gfx').scope():
            self.write(0); self.write(1)
        execution.finish()
        records = ledger.completion_records(width=1, height=1, events_data=stream.getvalue())
        summary = events.lifecycle_summary(stream.getvalue())
        self.assertTrue(summary['complete'])
        self.assertEqual(summary['operations'], {})
        self.assertEqual(len(records[0]['frames']), 2)
        self.assertNotIn(str(self.directory), stream.getvalue())
        self.assertEqual(len([r for r in events.read_events(stream.getvalue())['events']
                              if r['event'] == 'frame_commit']), 2)

    def test_cached_old_file_and_touch_cannot_acquire_provenance(self):
        execution, stream, ledger = self.ledger()
        (self.directory / 'f0001.png').write_bytes(MINIMAL_PNG)
        with execution.task('gfx').scope():
            self.write(0)
        execution.finish()
        os.utime(self.directory / 'f0001.png', None)
        with self.assertRaises(ValueError):
            ledger.completion_records(width=1, height=1, events_data=stream.getvalue())

    def test_failed_atomic_write_never_records_publication(self):
        execution, stream, ledger = self.ledger(1)
        original = OSError('write failure')
        with execution.task('gfx').scope():
            with patch.object(gfx_render.fstxn, 'atomic_write', side_effect=original):
                with self.assertRaises(OSError) as caught:
                    self.write(0)
                self.assertIs(caught.exception, original)
        execution.finish()
        self.assertEqual(ledger.count, 0)
        self.assertNotIn('frame_commit', stream.getvalue())

    def test_static_requires_current_rendered_head_and_pixel_equality(self):
        execution, stream, ledger = self.ledger()
        with execution.task('gfx').scope():
            self.write(0)
            self.write(1, origin='static_copy', copy_head=0)
        execution.finish()
        frames = ledger.completion_records(width=1, height=1, events_data=stream.getvalue())[0]['frames']
        self.assertEqual(frames['0001']['static_status'], 'verified_static')
        self.assertEqual(frames['0001']['head_frame'], '0000')
        self.assertTrue(events.read_events(stream.getvalue())['complete'])

    def test_tampered_or_hardlinked_final_file_is_rejected(self):
        for mode in ('tamper', 'hardlink'):
            execution, stream, ledger = self.ledger()
            with execution.task('gfx').scope():
                self.write(0); self.write(1)
            execution.finish()
            path = self.directory / 'f0001.png'
            if mode == 'tamper':
                path.write_bytes(MINIMAL_PNG + b'external mutation')
            else:
                path.unlink(); os.link(self.directory / 'f0000.png', path)
            with self.assertRaises(ValueError):
                ledger.completion_records(width=1, height=1, events_data=stream.getvalue())
            # Restore independent regular destinations for the next fixture.
            for file in self.directory.iterdir():
                file.unlink()

    def test_record_failure_is_nonintrusive_but_refuses_completion(self):
        execution, stream, ledger = self.ledger(1)
        with execution.task('gfx').scope():
            with patch.object(ledger, 'record', side_effect=OSError('private')):
                self.write(0)
        execution.finish()
        self.assertEqual((self.directory / 'f0000.png').read_bytes(), MINIMAL_PNG)
        self.assertFalse(events.lifecycle_summary(stream.getvalue())['complete'])
        with self.assertRaises(ValueError):
            ledger.completion_records(width=1, height=1, events_data=stream.getvalue())
        self.assertNotIn('private', stream.getvalue())

    def test_cli_fallback_workers_use_common_producer(self):
        from vtx import gfx_cli
        from tests.test_gfx_cli_fallback import GfxCliFallbackTests
        case = GfxCliFallbackTests(); case.setUp()
        try:
            execution, stream, ledger = self.ledger()
            with execution.task('gfx').scope():
                count = gfx_cli.render_cli(range(2), self.directory, case.html,
                                          case._stub('ok'), width=1, height=1)
            execution.finish()
            self.assertEqual(count, 2)
            self.assertEqual(len(ledger.completion_records(width=1, height=1, events_data=stream.getvalue())[0]['frames']), 2)
            self.assertEqual(events.lifecycle_summary(stream.getvalue())['operations'], {})
        finally:
            case.tearDown(); case.doCleanups()

    def test_static_copy_from_old_head_and_all_cache_hit_are_rejected(self):
        for copied in (False, True):
            execution, stream, ledger = self.ledger(2 if copied else 1)
            (self.directory / 'f0000.png').write_bytes(MINIMAL_PNG)
            with execution.task('gfx').scope():
                if copied:
                    self.write(1, origin='static_copy', copy_head=0)
            execution.finish()
            with self.assertRaises(ValueError):
                ledger.completion_records(width=1, height=1, events_data=stream.getvalue())

    def test_v1_rejects_publication_and_metadata_cannot_carry_paths(self):
        import copy
        import json
        execution, stream, ledger = self.ledger(1)
        with execution.task('gfx').scope():
            self.write(0)
        execution.finish()
        rows = events.read_events(stream.getvalue())['events']
        changed = copy.deepcopy(rows)
        for row in changed:
            row['schema_version'] = events.SCHEMA
        with self.assertRaises(ValueError):
            events.read_events(''.join(json.dumps(row) + '\n' for row in changed))
        commit = next(row for row in rows if row['event'] == 'frame_commit')
        commit['extensions']['path'] = '/private'
        with self.assertRaises(ValueError):
            events.read_events(''.join(json.dumps(row) + '\n' for row in rows))

    def test_real_render_workers_and_deferred_copy_share_sparse_ledger(self):
        from tests.test_graphics_portability import _FakeBrowser
        execution, stream, ledger = self.ledger(6)
        page = self.directory / 'page.html'; page.write_text('<html/>')
        browser = _FakeBrowser('spans', 2, spans=[[2, 5]])
        with execution.task('gfx').scope():
            with patch.object(gfx_render, 'Browser', lambda *args, **kwargs: browser), \
                 patch.dict(os.environ, {'VTX_GFX_STATIC': '1', 'VTX_GFX_NAVFREE': '0'}):
                result = gfx_render.render_page(page, self.directory, 6, chrome_path='fake',
                                               tabs=2, width=6, height=4)
        execution.finish()
        self.assertEqual(result['failed'], [])
        records = ledger.completion_records(width=6, height=4, events_data=stream.getvalue())[0]['frames']
        self.assertEqual(len(records), 6)
        self.assertEqual(records['0003']['origin'], 'static_copy')
        self.assertEqual(records['0004']['head_frame'], '0002')
        self.assertEqual(events.lifecycle_summary(stream.getvalue())['operations'], {})

    def test_structurally_unclosed_stream_refuses_completion_records(self):
        execution, stream, ledger = self.ledger(1)
        with execution.task('gfx').scope():
            self.write(0)
            orphan = execution.task('gfx')
            execution.emit('task_start', task=orphan, started=execution.clock())
        execution.finish()
        self.assertFalse(events.read_events(stream.getvalue())['complete'])
        with self.assertRaisesRegex(ValueError, 'not successfully closed'):
            ledger.completion_records(width=1, height=1, events_data=stream.getvalue())

    def test_source_snapshot_cannot_be_repaired_by_a_later_head(self):
        execution, stream, ledger = self.ledger()
        (self.directory / 'f0000.png').write_bytes(MINIMAL_PNG)
        with execution.task('gfx').scope():
            token = gfx_provenance.before_static_read(self.directory / 'f0000.png')
            copied_bytes = (self.directory / 'f0000.png').read_bytes()
            self.write(0)  # Current head arrived only after the source was read.
            gfx_render._atomic_write_bytes(self.directory / 'f0001.png', copied_bytes,
                origin='static_copy', copy_head=0, source_token=token)
        execution.finish()
        with self.assertRaises(ValueError):
            ledger.completion_records(width=1, height=1, events_data=stream.getvalue())

    def test_copy_binds_prior_bytes_even_when_head_republished_with_same_pixels(self):
        import zlib
        from tests.test_gfx_cli_fallback import _png_with_idat
        alternate = _png_with_idat([zlib.compress(b'\x00\xff\x00\x00\xff', 0)])
        self.assertNotEqual(alternate, MINIMAL_PNG)
        execution, stream, ledger = self.ledger()
        with execution.task('gfx').scope():
            self.write(0)
            token = gfx_provenance.before_static_read(self.directory / 'f0000.png')
            copied_bytes = (self.directory / 'f0000.png').read_bytes()
            gfx_render._atomic_write_bytes(self.directory / 'f0000.png', alternate)
            gfx_render._atomic_write_bytes(self.directory / 'f0001.png', copied_bytes,
                origin='static_copy', copy_head=0, source_token=token)
        execution.finish()
        frames = ledger.completion_records(width=1, height=1, events_data=stream.getvalue())[0]['frames']
        self.assertEqual(frames['0001']['origin'], 'static_copy')
        commits = [row for row in events.read_events(stream.getvalue())['events'] if row['event'] == 'frame_commit']
        self.assertEqual(commits[-1]['extensions']['copy_source_seq'], commits[0]['seq'])
        self.assertNotEqual(commits[0]['extensions']['png_sha256'], commits[1]['extensions']['png_sha256'])
