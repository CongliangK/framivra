"""PR#1 合入后的缓存/回执溯源测试（适配 main 13bed7a per-产物内容寻址架构）。

PR 原版断言 cache/<input-fingerprint>/ 目录随输入变化——整仓指纹布局在合并中
被放弃（见 docs/cache-provenance.md 与 vtx/cache_state.py 模块头），等效断言
改为：gfx 内容键 / 源 stat 采样指纹 / 回执 input_fingerprint 随内容变化。
"""
import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # repo 根（v2 §1.4 包导入）
from vtx import hygiene
from vtx.cache_state import media_stat_digest, receipt_fingerprint
from vtx.infra import Ctx
from tests._support import tree_sha256


def _tl(root, **over):
    tl = {'project': {'fps': 30, 'duration_frames': 180, 'width': 1920, 'height': 1080},
          'sources': [], 'video_tracks': [], 'subtitles': [], 'audio_tracks': []}
    tl.update(over)
    p = root / 'timeline.json'
    p.write_text(json.dumps(tl))
    return p


class CacheIdentity(unittest.TestCase):
    def test_content_not_timestamp_and_declared_dependencies(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / 'a'; src.write_bytes(b'AAAA')
            css = root / 'style.css'; css.write_bytes(b'1111')
            (root / 'page.html').write_text('canvas')
            p = _tl(root, project={'fps': 30, 'duration_frames': 180, 'width': 1920,
                                   'height': 1080, 'cache_dependencies': ['style.css']},
                    sources=[{'id': 'a', 'path': 'a'}],
                    video_tracks=[{'name': 'g', 'kind': 'gfx',
                                   'clips': [{'src': 'page.html', 'dst_in': 0, 'dur': 30}]}])
            c = Ctx(p)
            # 源内容变（时间戳刻意恢复）→ 采样 stat 指纹变（PR 回归场景）
            before = media_stat_digest(src)
            stamp = src.stat(); src.write_bytes(b'BBBB')
            os.utime(src, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            self.assertNotEqual(before, media_stat_digest(src))
            # 声明依赖内容变 → gfx 缓存键变（PR 的 cache_dependencies 语义）
            k1 = hygiene.gfx_key(c, {'src': 'page.html', 'dur': 30})
            css.write_bytes(b'2222')
            self.assertNotEqual(k1, hygiene.gfx_key(c, {'src': 'page.html', 'dur': 30}))
            # 声明依赖缺失 → Ctx 构造快速失败（PR 的 fail-fast 语义）
            p2 = _tl(root, project={'fps': 30, 'duration_frames': 180, 'width': 1920,
                                    'height': 1080, 'cache_dependencies': ['missing']},
                     sources=[{'id': 'a', 'path': 'a'}], video_tracks=[])
            self.assertRaises(ValueError, Ctx, p2)

    def test_receipt_invalidation_and_graphics_identity(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'a.html').write_text('A')
            (root / 'b.html').write_text('B')
            p = _tl(root, video_tracks=[
                {'name': 'g1', 'kind': 'gfx', 'clips': [{'src': 'a.html', 'dst_in': 0, 'dur': 30}]},
                {'name': 'g2', 'kind': 'gfx', 'clips': [{'src': 'b.html', 'dst_in': 60, 'dur': 30}]}])
            c = Ctx(p)
            # 同名页隔离（PR 场景）：不同内容 → 不同 gfx 键目录；同页换时长 → 不同键
            self.assertNotEqual(hygiene.gfx_dir(c, {'src': 'a.html', 'dur': 30}),
                                hygiene.gfx_dir(c, {'src': 'b.html', 'dur': 30}))
            self.assertNotEqual(hygiene.gfx_dir(c, {'src': 'a.html', 'dur': 30}),
                                hygiene.gfx_dir(c, {'src': 'a.html', 'dur': 60}))
            for stage in ['prepare', 'audio', 'video', 'mux', 'qc']: c.receipt(stage, True)
            c.begin_stage('prepare')   # 撤销 prepare 及下游成功
            self.assertRaises(RuntimeError, c.begin_stage, 'qc')
            for stage in ['prepare', 'audio', 'video', 'mux', 'qc']: c.receipt(stage, True)
            # 输入变化（timeline 编辑）→ 回执身份变 → 旧成功不继承（PR 的整仓指纹
            # 断言在合并中等效为 input_fingerprint 断言）
            tl = json.loads(p.read_text())
            tl['subtitles'] = [{'in': 0, 'out': 30, 'text': 'new'}]
            p.write_text(json.dumps(tl))
            new = Ctx(p)
            self.assertNotEqual(c.input_fingerprint, new.input_fingerprint)
            self.assertEqual(receipt_fingerprint(new), new.input_fingerprint)
            self.assertRaises(RuntimeError, new.begin_stage, 'qc')
            new.receipt('validate', True)
            rec = json.loads((root / 'out/receipt.json').read_text())
            self.assertEqual(list(rec['stages']), ['validate'])


class GcExemptions(unittest.TestCase):
    """方案三阶段1审查 P1-2：两个新状态件（loudness_receipts.json / qc_pass_segs.json）
    不进 gc 回收清单（gc_plan 即 `vtx gc --dry-run` 的计算路径）——回执/记录被回收只是
    优雅降级（全检/重测一轮），但 gc --dry-run 持续列出会诱导误删。"""

    def test_new_state_files_exempt_from_gc_plan(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = _tl(root)
            (root / 'cache' / 'audio').mkdir(parents=True)
            (root / 'cache' / 'audio' / 'loudness_receipts.json').write_text('{"entries": {}}')
            (root / 'cache' / 'qc_pass_segs.json').write_text('{"version": 1}')
            # 对照组：audio/ 下键集外的陈旧 wav 仍须回收——豁免不得扩大到整个 audio/
            (root / 'cache' / 'audio' / 'deadbeef0000.wav').write_bytes(b'x' * 16)
            c = Ctx(p)
            c.dry = True   # nvenc_ok dry 短路（不真探测），gc_plan 纯计算无副作用
            plan = hygiene.gc_plan(c)
            listed = [str(e[0]) for items in plan.values() for e in items]
            self.assertNotIn(str(root / 'cache' / 'audio' / 'loudness_receipts.json'),
                             listed, f"loudness_receipts 被列入: {listed}")
            self.assertNotIn(str(root / 'cache' / 'qc_pass_segs.json'),
                             listed, f"qc_pass_segs 被列入: {listed}")
            self.assertIn(str(root / 'cache' / 'audio' / 'deadbeef0000.wav'), listed)

    def test_vseg_pass_exempt_from_gc_plan(self):
        """R-03.J1（⑭ 收尾）：cache/vseg_pass 整目录入 gc 白名单——gc_plan 各回收
        类别（vseg/legacy/tmp/out_intermediate/plugin_cache）零命中 vseg_pass。

        背景（R12-G1-001 同案 ⑭ gc 交互）：known 集独缺 vseg_pass 时该目录整体落
        legacy 类别随 gc 回收——pass 中间物可再生，但温态冻结语义失效、下次渲染
        全冷建（900s ≈ 3.2× 旧链式，KNOWN-ISSUES ⑭ 在案）。修复=known 集补录
        （hygiene.py :265-267），豁免语义与同族先例 preview 一致（整目录豁免）。
        c.dry=True 纯计算路径（gc_plan 即 `vtx gc --dry-run` 的计算侧，无副作用）。
        对照组：cache 根下白名单外杂散目录仍须落 legacy——豁免不得扩大到整个
        cache 根（镜像 test_new_state_files_exempt_from_gc_plan 对照组先例）。
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = _tl(root)
            (root / 'cache' / 'vseg_pass').mkdir(parents=True)
            (root / 'cache' / 'vseg_pass' / 'p0_deadbeef0000.mkv').write_bytes(b'x' * 16)
            # 对照组：白名单外杂散目录仍回收
            (root / 'cache' / 'legacy_junk').mkdir()
            (root / 'cache' / 'legacy_junk' / 'old.bin').write_bytes(b'y' * 16)
            c = Ctx(p)
            c.dry = True   # gc_plan 纯计算无副作用（:89 先例同口径）
            plan = hygiene.gc_plan(c)
            vpass = str(root / 'cache' / 'vseg_pass')
            for cat in ('vseg', 'legacy', 'tmp', 'out_intermediate', 'plugin_cache'):
                listed = [str(e[0]) for e in plan.get(cat, [])]
                hits = [x for x in listed if x == vpass or x.startswith(vpass + os.sep)]
                self.assertEqual(hits, [],
                                 f"gc_plan {cat} 类别命中 vseg_pass: {hits}")
            self.assertIn(str(root / 'cache' / 'legacy_junk'),
                          [str(e[0]) for e in plan['legacy']],
                          "对照组杂散目录未被回收——白名单误扩大")

    @staticmethod
    def _selected(plan, path):
        return any(path == entry or entry in path.parents
                   for items in plan.values() for entry, _, _ in items)

    def test_completion_markers_follow_each_cleanup_mode_owner(self):
        from vtx.cli import _cleanup_plan
        from vtx.recovery import completion_marker
        modes = [('normal', 'gc', {}, True), ('out-only', 'gc', {}, False),
                 ('deep', 'gc', {'deep': True}, True),
                 ('clean', 'clean', {}, True), ('nuke', 'clean', {'nuke': True}, True)]
        for name, command, options, with_cache in modes:
            with self.subTest(mode=name), tempfile.TemporaryDirectory() as td, \
                    mock.patch.dict(os.environ, {'VTX_SEG_NVENC': '0'}), \
                    mock.patch('subprocess.Popen', side_effect=AssertionError('no subprocess')):
                root = Path(td)
                source = root / 'source.bin'; source.write_bytes(b'input bytes, no decode')
                source_marker = root / 'source.bin.complete.json'
                source_marker.write_bytes(b'input-side historical evidence')
                clip = {'src': 's', 'src_in': 0, 'src_out': 30, 'dur': 30, 'dst_in': 0}
                c = Ctx(_tl(root, sources=[{'id': 's', 'path': 'source.bin'}],
                            video_tracks=[{'name': 'v', 'clips': [clip]}]))
                c.dry = True
                live = [c.out / leaf for leaf in ('final.mp4', 'draft_final.mp4', 'draft.mp4',
                                                 'video_noaudio.mp4', 'audio.wav', 'final_4k.mp4')]
                if with_cache:
                    _, seg, audio = hygiene._current_keys(c)
                    live += [c.cache / 'seg' / (key + '.mp4') for key, _ in seg]
                    live += [c.cache / 'audio' / (key + '.wav') for key in audio]
                for media in live:
                    media.parent.mkdir(parents=True, exist_ok=True)
                    media.write_bytes(b'owned media placeholder')
                    completion_marker(media).write_bytes(b'certificate bytes are not interpreted by GC')
                junk = [c.out / 'orphan.mp4.complete.json',
                        completion_marker(live[0]).with_name(live[0].name + '.complete.json.complete.json'),
                        c.out / (live[0].name + '.complete.json.bak')]
                if with_cache:
                    junk += [c.cache / 'audio' / 'stale.wav',
                             c.cache / 'audio' / 'stale.wav.complete.json']
                for path in junk:
                    path.write_bytes(b'garbage')
                lock = c.out / 'final.mp4.lock'; lock.write_bytes(b'')
                evidence = c.out / 'history' / 'old.mp4'
                evidence.parent.mkdir(); evidence.write_bytes(b'historical delivery')
                before = tree_sha256(root)
                plan = _cleanup_plan(c, command, **options)
                self.assertEqual(tree_sha256(root), before, 'planning must be read-only')
                retained = {'final.mp4', 'final_4k.mp4'}
                if name == 'deep': retained.add('draft_final.mp4')
                for media in live:
                    keep = name in ('normal', 'out-only') or (name != 'nuke' and media.name in retained)
                    self.assertEqual(self._selected(plan, media), not keep, str(media))
                    self.assertEqual(self._selected(plan, completion_marker(media)), not keep,
                                     str(completion_marker(media)))
                self.assertTrue(all(self._selected(plan, path) for path in junk))
                self.assertFalse(self._selected(plan, lock))
                self.assertFalse(self._selected(plan, source_marker))
                self.assertEqual(self._selected(plan, evidence), name == 'nuke')
                if name in ('normal', 'out-only'):
                    plan.execute()  # Only this new disposable project.
                    for media in live:
                        self.assertEqual(media.read_bytes(), b'owned media placeholder')
                        self.assertTrue(completion_marker(media).is_file())
                    self.assertFalse(any(path.exists() for path in junk))
                    self.assertEqual(evidence.read_bytes(), b'historical delivery')
                    self.assertEqual(source_marker.read_bytes(), b'input-side historical evidence')

    def test_completion_marker_follows_retained_media_age_not_its_own(self):
        from vtx.recovery import completion_marker
        cases = [('audio/recent.wav', False), ('seg/recent.mp4', False),
                 ('qc_clean.mp4', False), ('vseg/recent.mp4', True),
                 ('vseg_draft/recent.mp4', True), ('vseg_qc/recent.mp4', True)]
        for relative, with_lock in cases:
            with self.subTest(media=relative), tempfile.TemporaryDirectory() as td, \
                    mock.patch.dict(os.environ, {'VTX_SEG_NVENC': '0'}), \
                    mock.patch('subprocess.Popen', side_effect=AssertionError('no subprocess')):
                root = Path(td); c = Ctx(_tl(root)); c.dry = True
                recent = c.cache / relative
                directory = recent.parent; directory.mkdir(parents=True)
                old = directory / ('old' + recent.suffix)
                for media in (recent, old):
                    media.write_bytes(b'not a current key')
                    marker = completion_marker(media); marker.write_bytes(b'old certificate')
                    os.utime(marker, (1, 1))
                os.utime(old, (1, 1))
                if with_lock:
                    lock = Path(str(recent) + '.lock'); lock.write_bytes(b'')
                plan = hygiene.gc_plan(c, keep_days=7)
                self.assertFalse(self._selected(plan, recent))
                self.assertFalse(self._selected(plan, completion_marker(recent)))
                self.assertTrue(self._selected(plan, old))
                self.assertTrue(self._selected(plan, completion_marker(old)))
                if with_lock:
                    self.assertFalse(self._selected(plan, lock))
                    # Without a retained lock the directory is one old deletion
                    # candidate. Marker pairing must not rescue its contents.
                    lock.unlink(); os.utime(directory, (1, 1))
                    plan = hygiene.gc_plan(c, keep_days=7)
                    self.assertTrue(self._selected(plan, recent))
                    self.assertTrue(self._selected(plan, completion_marker(recent)))

    def test_symlink_media_does_not_claim_an_owned_completion_marker(self):
        from vtx.recovery import completion_marker
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ, {'VTX_SEG_NVENC': '0'}), \
                mock.patch('subprocess.Popen', side_effect=AssertionError('no subprocess')):
            root = Path(td); c = Ctx(_tl(root)); c.dry = True
            c.out.mkdir(); outside = root / 'source.wav'; outside.write_bytes(b'source')
            media = c.out / 'audio.wav'
            try: media.symlink_to(outside)
            except (OSError, NotImplementedError): self.skipTest('symlinks unavailable')
            marker = completion_marker(media); marker.write_bytes(b'not owned media')
            plan = hygiene.gc_plan(c)
            self.assertFalse(self._selected(plan, media))
            self.assertTrue(self._selected(plan, marker))
            self.assertEqual(outside.read_bytes(), b'source')

class StaleReceiptArchive(unittest.TestCase):
    """⑮（2026-09-14 登记+最小修复）：回执输入指纹翻转（如 validate-only 运行在
    新指纹下落笔）不再静默归零旧全量阶段记录——先轮转单槽旁证 out/receipt.prev.json。
    receipt.json 语义不变（旧成功不继承的安全不变量保留）。"""

    def test_fingerprint_flip_rotates_prev(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = _tl(root)
            c = Ctx(p)
            out = root / 'out'; out.mkdir(parents=True)
            old = {'input_fingerprint': 'OLD_FP', 'stages': {
                'validate': {'ok': True}, 'audio': {'ok': True},
                'mux': {'ok': True}, 'qc': {'ok': True, 'detail': '75/76'}}}
            (out / 'receipt.json').write_text(json.dumps(old))
            # 指纹翻转下落一笔（validate-only 场景）→ 旧记录进 prev，主回执归零重记
            c.receipt('validate', True, 'validate-only 复检')
            prev = json.loads((out / 'receipt.prev.json').read_text())
            self.assertEqual(prev['input_fingerprint'], 'OLD_FP')
            self.assertEqual(set(prev['stages']), {'validate', 'audio', 'mux', 'qc'})
            rec = json.loads((out / 'receipt.json').read_text())
            self.assertEqual(rec['input_fingerprint'], c.input_fingerprint)
            self.assertEqual(list(rec['stages']), ['validate'])

    def test_fingerprint_match_no_rotation(self):
        # 指纹一致（常规温态重跑）不产生 prev——轮转只在真翻转时发生
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = _tl(root)
            c = Ctx(p)
            out = root / 'out'; out.mkdir(parents=True)
            (out / 'receipt.json').write_text(json.dumps(
                {'input_fingerprint': c.input_fingerprint,
                 'stages': {'validate': {'ok': True}, 'qc': {'ok': True}}}))
            c.receipt('qc', True, '温态复跑')
            self.assertFalse((out / 'receipt.prev.json').exists(),
                             "指纹未翻转不应轮转旁证")
            rec = json.loads((out / 'receipt.json').read_text())
            self.assertEqual(set(rec['stages']), {'validate', 'qc'})   # 旧成功保留

            # Invalid receipt containers recover on the next actual write.
            from vtx.cache_state import load_receipt
            bad_records = [[], None, False, 0, 'receipt',
                           {'input_fingerprint': c.input_fingerprint}]
            for stages in (None, [], False, 1, 'stages'):
                bad_records.append({'input_fingerprint': c.input_fingerprint,
                                    'stages': stages})
            target = out / 'receipt.json'
            for bad in bad_records:
                with self.subTest(bad=bad):
                    target.write_text(json.dumps(bad))
                    before = target.read_bytes()
                    self.assertEqual(load_receipt(target, c.input_fingerprint),
                                     {'input_fingerprint': c.input_fingerprint, 'stages': {}})
                    self.assertEqual(target.read_bytes(), before)
                    with self.assertRaisesRegex(RuntimeError, 'prepare'):
                        c.begin_stage('audio')
                    self.assertEqual(target.read_bytes(), before)
                    c.receipt('validate', True)
                    recovered = json.loads(target.read_text())
                    self.assertEqual(set(recovered['stages']), {'validate'})
                    self.assertIs(recovered['stages']['validate']['ok'], True)
                    self.assertFalse((out / 'receipt.prev.json').exists())

            # A malformed observation is not success, but is still diagnostic
            # history. Neither rejection nor unrelated writes may erase it.
            bad_observations = [None, [], True, 1, 'prepare', {}]
            bad_observations.extend({'ok': ok} for ok in
                                    (False, None, 'false', 1, 0, [], {}))
            for observation in bad_observations:
                with self.subTest(observation=observation):
                    prior = {'validate': {'ok': True, 'detail': 'prior history'},
                             'prepare': observation, 'qc': 'diagnostic'}
                    record = {'input_fingerprint': c.input_fingerprint,
                              'stages': prior}
                    target.write_text(json.dumps(record))
                    before = target.read_bytes()
                    self.assertEqual(load_receipt(target, c.input_fingerprint), record)
                    self.assertEqual(target.read_bytes(), before)
                    with self.assertRaisesRegex(RuntimeError, 'prepare'):
                        c.begin_stage('audio')
                    self.assertEqual(target.read_bytes(), before)
                    c.begin_stage('audit')
                    self.assertEqual(json.loads(target.read_text())['stages'], prior)
                    c.receipt('audit', True)
                    after = json.loads(target.read_text())
                    self.assertEqual({name: after['stages'][name] for name in prior}, prior)
                    self.assertIs(after['stages']['audit']['ok'], True)
                    self.assertFalse((out / 'receipt.prev.json').exists())


if __name__ == '__main__':
    unittest.main()
