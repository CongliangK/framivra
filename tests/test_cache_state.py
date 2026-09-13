"""PR#1 合入后的缓存/回执溯源测试（适配 main 13bed7a per-产物内容寻址架构）。

PR 原版断言 cache/<input-fingerprint>/ 目录随输入变化——整仓指纹布局在合并中
被放弃（见 docs/cache-provenance.md 与 vtx/cache_state.py 模块头），等效断言
改为：gfx 内容键 / 源 stat 采样指纹 / 回执 input_fingerprint 随内容变化。
"""
import json, os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vtx'))
import hygiene
from cache_state import media_stat_digest, receipt_fingerprint
from infra import Ctx


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
    不进 gc 回收清单（gc_plan 即 `vtx gc --dry` 的计算路径）——回执/记录被回收只是
    优雅降级（全检/重测一轮），但 gc --dry 持续列出会诱导误删。"""

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


if __name__ == '__main__':
    unittest.main()
