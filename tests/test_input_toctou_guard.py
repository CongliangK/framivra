#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_input_toctou_guard.py —— 输入 TOCTOU 完成复核回归（issue #32 / B08 / P1）。

工单：每个构建任务开始时锁定输入指纹（Ctx 构造期 receipt_fingerprint 已入内存），
完成时（产物落盘、记账前）重算复核；不一致 → 任务显式失败（非零退出 + 明确报错
说明输入在渲染期间被改动），产物不记账、不标完成，绝不把新输入身份赋给旧产物。
（KNOWN-ISSUES ⑨ 登记的「渲染窗口 TOCTOU：指纹录新字节/产物用旧内存」自此关门。）

复核落点 = 三个记账入口的统一收口（cache_state.verify_input_identity 单一实现，
不重复造轮子；与 gfx verify_frames 等帧完整性门正交）：
  - infra.Ctx.receipt(ok=True)   —— 8 个阶段的成功回执
  - hygiene.manifest_record      —— gfx/prepare/video/subs 的 manifest 记账
  - hygiene.versions_record      —— mux 的 VERSIONS 版本账

时序注入手法（工单指定）：monkeypatch 在「指纹已算（Ctx 构造后）、产物已写
（stub 执行器落盘假产物）、记账前」把 timeline/资产换成新版——全程无真实
ffmpeg/Chrome：prepare 的提取经 mock run_parallel 落盘假 wav；mux 的封装经
mock Ctx.run 落盘假 final（沿用 tests/ 既有 API 级 + mock 手法，
参 test_mux_history / test_hygiene_ledger_txn）。

stub 对齐注记（issue #88 根因）：「假 wav 已写」须按 B10（ca35bf6）发布协议
落盘——recovery.audio_cache_valid 只认 .complete.json 完成证书（atomic_ffmpeg
经 _inspect 认证后写入）或全解码+时长吻合的 legacy wav；裸字节直写终路径在
B10 语义下=提取未完成，prepare 完成循环会以 incomplete extracted audio 显式
失败（对照组误红的根因）。故 stub 一律经 _publish_wav 落「媒体+证书」终态，
使每个场景只测身份门本身，不被完成性判定混入第二失败原因。

覆盖：timeline 渲染中改（prepare 阶段级 / receipt 门 / mux VERSIONS）、gfx html
资产渲染中换（manifest 门）、音频源渲染中换（manifest+receipt 门 + 新身份不
命中旧产物）；每场景带「输入不变正常记账」对照组。

运行：env python3 -m unittest tests.test_input_toctou_guard -v（本机 ZCode 环境
必须 env python3 前缀：AppImage 劫持 argv[0]，见全局 AGENTS.md）。
"""
import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))   # repo 根注入（v2 §1.4 包导入）

from vtx import hygiene, stages         # noqa: E402
from vtx.cache_state import load_receipt  # noqa: E402
from vtx.infra import Ctx               # noqa: E402


def _tl(root: Path, **over) -> Path:
    """最小合法 timeline（fps 30 契约内）。"""
    tl = {"project": {"fps": 30, "duration_frames": 90, "width": 640, "height": 360},
          "sources": [], "video_tracks": [], "subtitles": [], "audio_tracks": []}
    tl.update(over)
    p = root / "timeline.json"
    p.write_text(json.dumps(tl, ensure_ascii=False), encoding="utf-8")
    return p


def _swap_timeline(p: Path):
    """渲染期间改 timeline：追加一条字幕（字节级变化 → 回执指纹翻转）。"""
    tl = json.loads(p.read_text(encoding="utf-8"))
    tl["subtitles"] = [{"in": 0, "out": 30, "text": "中途新字幕"}]
    p.write_text(json.dumps(tl, ensure_ascii=False), encoding="utf-8")


def _publish_wav(wav: Path, payload: bytes = b"FAKE-WAV"):
    """stub 执行器的「提取成功」终态 = B10（ca35bf6）发布协议对齐（issue #88）。

    模拟 recovery.atomic_ffmpeg 成功路径的落盘终态：媒体原子落位 + .complete.json
    完成证书（文件名后缀与 recovery._marker 一致；字段 schema/sha256/frames 与
    atomic_ffmpeg 经 _inspect 认证后写入的 wav 形态一致——frames=None、
    sort_keys 序）。有了证书，prepare 完成循环的 audio_cache_valid 即认可提取
    完成，对照组恢复「输入不变 → 正常记账」；无证书的裸字节会被判「未完成
    提取」而显式失败，与本文件要测的身份门无关。"""
    wav.parent.mkdir(parents=True, exist_ok=True)
    wav.write_bytes(payload)
    wav.with_name(wav.name + ".complete.json").write_text(
        json.dumps({"frames": None, "schema": 1,
                    "sha256": hashlib.sha256(payload).hexdigest()},
                   sort_keys=True), encoding="utf-8")


class _StageNotOkMixin:
    """receipt「该阶段非 ok」断言：文件缺失（begin_stage 已撤销/从未写）或
    ok=False（显式失败回执）都算非 ok——工单验收两态并列。"""

    def assert_stage_not_ok(self, root, stage):
        f = root / "out" / "receipt.json"
        if not f.exists():
            return   # 回执未落 = 阶段未被标完成
        rec = json.loads(f.read_text(encoding="utf-8"))
        entry = rec.get("stages", {}).get(stage)
        self.assertNotEqual(bool(entry and entry.get("ok")), True,
                            f"阶段 {stage} 不得标 ok：{entry}")


# ---------------------------------------------------------------- ① prepare 阶段级（timeline）
class PrepareTimelineSwap(_StageNotOkMixin, unittest.TestCase):
    """渲染期间改 timeline（注入点：mock run_parallel 内「产物已写、记账前」）：
    prepare 显式失败非零、manifest 无该笔、receipt prepare 非 ok；
    对照组输入不变 → 正常记账（manifest 的 tl 字段 = 开始时身份）。"""

    def _project(self, root: Path):
        (root / "a.src").write_bytes(b"A" * 4096)
        return _tl(root, sources=[{"id": "a", "path": "a.src"}])

    def _run_prepare(self, root: Path, mutate):
        p = self._project(root)
        c = Ctx(p)
        fp0, tl_md5_0 = c.input_fingerprint, hashlib.md5(p.read_bytes()).hexdigest()

        def fake_run_parallel(ctx, tasks, what):
            for _label, cmd in tasks:
                # 产物已发布（旧输入身份的假 wav，B10 证书协议终态）
                _publish_wav(Path(str(cmd[-1])))
            mutate(p)                              # ← 注入点：指纹已算、产物已写、记账前

        err = io.StringIO()
        code = None
        with mock.patch.object(stages, "run_parallel", fake_run_parallel), \
                contextlib.redirect_stderr(err):
            try:
                stages.prepare(c)
            except SystemExit as e:
                code = e.code
        return c, p, fp0, tl_md5_0, code, err.getvalue()

    def test_timeline_swapped_mid_render_fails(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c, p, fp0, _md5_0, code, err = self._run_prepare(root, _swap_timeline)
            # 任务显式失败：非零退出 + 明确报错（issue #32 + 渲染期间被改）
            self.assertEqual(code, 1)
            self.assertIn("issue #32", err)
            # 产物不记账：manifest 无该笔（复核在 fstxn 落笔前拦截）
            self.assertFalse((root / "cache" / "manifest.json").exists(),
                             "输入被改后 manifest 不得记账")
            # receipt 该阶段非 ok
            self.assert_stage_not_ok(root, "prepare")
            # 不把新输入身份赋给旧产物：新 Ctx 指纹翻转 + 旧回执/旧账本对新身份零继承
            c2 = Ctx(p)
            self.assertNotEqual(c2.input_fingerprint, fp0)
            self.assertEqual(load_receipt(root / "out" / "receipt.json",
                                          c2.input_fingerprint)["stages"], {})

    def test_control_unchanged_inputs_account_normally(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c, _p, fp0, tl_md5_0, code, err = self._run_prepare(
                root, lambda p: None)
            self.assertIsNone(code, f"对照组不应退出：{err}")
            # manifest 正常记账，且 tl 字段 = 任务开始时的 timeline 身份
            entries = json.loads((root / "cache" / "manifest.json")
                                 .read_text(encoding="utf-8"))["entries"]
            self.assertTrue(any(e["stage"] == "prepare" for e in entries))
            self.assertTrue(all(e["tl"] == tl_md5_0 for e in entries))
            # receipt 正常标 ok 且绑定开始时指纹
            rec = json.loads((root / "out" / "receipt.json").read_text(encoding="utf-8"))
            self.assertIs(rec["stages"]["prepare"]["ok"], True)
            self.assertEqual(rec["input_fingerprint"], fp0)


# ---------------------------------------------------------------- ② receipt 门（API 级）
class ReceiptGate(_StageNotOkMixin, unittest.TestCase):
    """Ctx.receipt(ok=True) 记账前复核：输入被改 → 成功回执不写（改失败回执）+
    非零退出；失败回执（ok=False）路径零复核（issue #28 语义不变）。"""

    def test_ok_receipt_blocked_and_failure_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = _tl(root)
            c = Ctx(p)
            c.receipt("validate", True, "3 源 ok")   # 先落一笔成功（输入未变，通过）
            _swap_timeline(p)                         # ← 渲染期间改 timeline
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as cm:
                    c.receipt("audio", True, "2 轨混音 ok")
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("issue #32", err.getvalue())
            rec = json.loads((root / "out" / "receipt.json").read_text(encoding="utf-8"))
            # 该阶段非 ok：显式失败回执（说明输入被改），旧成功（validate）不动
            self.assertIs(rec["stages"]["audio"]["ok"], False)
            self.assertIn("issue #32", rec["stages"]["audio"]["detail"])
            self.assertIs(rec["stages"]["validate"]["ok"], True)
            # 新身份不继承旧回执（下一轮对改动后 timeline 从零开始）
            c2 = Ctx(p)
            self.assertEqual(load_receipt(root / "out" / "receipt.json",
                                          c2.input_fingerprint)["stages"], {})

    def test_failure_receipt_path_not_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = _tl(root)
            c = Ctx(p)
            _swap_timeline(p)
            with contextlib.redirect_stderr(io.StringIO()):
                c.receipt("validate", False, "3 errors")   # 失败记账不复核、不退出
            rec = json.loads((root / "out" / "receipt.json").read_text(encoding="utf-8"))
            self.assertIs(rec["stages"]["validate"]["ok"], False)
            self.assertEqual(rec["stages"]["validate"]["detail"], "3 errors")

    def test_control_ok_receipt_written(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = _tl(root)
            c = Ctx(p)
            with contextlib.redirect_stderr(io.StringIO()) as err:
                c.receipt("audio", True, "2 轨混音 ok")   # 输入未变 → 正常记账
            self.assertEqual(err.getvalue(), "")
            rec = json.loads((root / "out" / "receipt.json").read_text(encoding="utf-8"))
            self.assertIs(rec["stages"]["audio"]["ok"], True)
            self.assertEqual(rec["input_fingerprint"], c.input_fingerprint)


# ---------------------------------------------------------------- ③ manifest 门（gfx html 资产）
class ManifestGateAssetSwap(_StageNotOkMixin, unittest.TestCase):
    """gfx html 资产在「键已算、帧已写、记账前」被换：manifest_record 拒绝记账；
    新身份的 gfx 键自然分键（旧目录对新 timeline 不是合法缓存命中）。"""

    CL = {"src": "page.html", "dst_in": 0, "dur": 30}

    def _project(self, root: Path):
        (root / "page.html").write_text("canvas-A")
        return _tl(root, video_tracks=[
            {"name": "g", "kind": "gfx", "clips": [dict(self.CL)]}])

    def test_html_swapped_manifest_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = self._project(root)
            c = Ctx(p)
            key = hygiene.gfx_key(c, self.CL)
            d_frames = hygiene.gfx_dir(c, self.CL)
            d_frames.mkdir(parents=True)
            (d_frames / "f0000.png").write_bytes(b"PNG-A")   # 产物已写（旧 html 身份）
            (root / "page.html").write_text("canvas-B")       # ← 渲染期间换资产
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as cm:
                    hygiene.manifest_record(c, d_frames, "gfx", key)
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("issue #32", err.getvalue())
            self.assertFalse((root / "cache" / "manifest.json").exists(),
                             "资产被换后 manifest 不得记账")
            # 产物不被当作新身份缓存命中：html 内容入键 → 新 Ctx 键变、自然分键
            c2 = Ctx(p)
            self.assertNotEqual(hygiene.gfx_key(c2, self.CL), key)
            self.assertNotEqual(c2.input_fingerprint, c.input_fingerprint)

    def test_control_manifest_records(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._project(root)
            c = Ctx(root / "timeline.json")
            key = hygiene.gfx_key(c, self.CL)
            d_frames = hygiene.gfx_dir(c, self.CL)
            d_frames.mkdir(parents=True)
            (d_frames / "f0000.png").write_bytes(b"PNG-A")
            with contextlib.redirect_stderr(io.StringIO()) as err:
                hygiene.manifest_record(c, d_frames, "gfx", key)   # 输入未变 → 记账
            self.assertEqual(err.getvalue(), "")
            entries = json.loads((root / "cache" / "manifest.json")
                                 .read_text(encoding="utf-8"))["entries"]
            self.assertEqual(entries[-1]["stage"], "gfx")
            self.assertEqual(entries[-1]["key"], key)


# ---------------------------------------------------------------- ④ mux VERSIONS 账（timeline）
class MuxVersionsIdentity(_StageNotOkMixin, unittest.TestCase):
    """封装期间改 timeline（注入点：mock Ctx.run 内「tmp 产物已写、versions_record
    前」）：VERSIONS 不记账（timeline_md5 不得把旧输入的 final.mp4 记成新版本）、
    非零退出、receipt mux 非 ok；对照组 → 记账且 timeline_md5 = 开始时身份。"""

    def _run_mux(self, root: Path, mutate):
        p = _tl(root)
        c = Ctx(p)
        out = root / "out"
        out.mkdir(parents=True, exist_ok=True)
        (out / "video_noaudio.mp4").write_bytes(b"V")
        (out / "audio.wav").write_bytes(b"A")
        tl_md5_0 = hashlib.md5(p.read_bytes()).hexdigest()

        def fake_run(self, cmd, need=True):
            Path(str(cmd[-1])).write_bytes(b"FAKE-FINAL")   # 产物已写（tmp 落盘）
            mutate(p)                                        # ← 记账前注入
            return "ok"

        def fake_probe_encoders(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        err = io.StringIO()
        code = None
        with mock.patch.object(Ctx, "run", fake_run), \
                mock.patch("vtx.stages.subprocess.run", fake_probe_encoders), \
                contextlib.redirect_stderr(err):
            try:
                stages.mux(c)
            except SystemExit as e:
                code = e.code
        return c, tl_md5_0, code, err.getvalue()

    def test_timeline_swapped_versions_not_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _c, _tl_md5_0, code, err = self._run_mux(root, _swap_timeline)
            self.assertEqual(code, 1)
            self.assertIn("issue #32", err)   # _run_mux 返回的已是 getvalue() 字符串
            # final.mp4 已落盘（旧输入身份产物）但 VERSIONS 不记账——新身份不赋旧产物
            self.assertEqual((root / "out" / "final.mp4").read_bytes(), b"FAKE-FINAL")
            self.assertFalse((root / "out" / "VERSIONS.json").exists(),
                             "输入被改后 VERSIONS 不得记账")
            self.assert_stage_not_ok(root, "mux")

    def test_control_versions_recorded_with_start_identity(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _c, tl_md5_0, code, err = self._run_mux(root, lambda p: None)
            self.assertIsNone(code, f"对照组不应退出：{err}")
            versions = json.loads((root / "out" / "VERSIONS.json")
                                  .read_text(encoding="utf-8"))["versions"]
            self.assertEqual(len(versions), 1)
            self.assertEqual(versions[0]["timeline_md5"], tl_md5_0)
            rec = json.loads((root / "out" / "receipt.json").read_text(encoding="utf-8"))
            self.assertIs(rec["stages"]["mux"]["ok"], True)


# ---------------------------------------------------------------- ⑤ 音频源渲染中被换（新身份不命中旧产物）
class SourceSwapNoFalseHit(_StageNotOkMixin, unittest.TestCase):
    """音频源在「提取产物已写、记账前」被同路径替换：prepare 显式失败；旧 wav 留在
    旧键路径，新源身份的键路径无产物——下一轮对新身份无从命中旧 wav（issue #32
    的「timeline 之外的输入同受保护」面）。"""

    def test_source_replaced_mid_prepare(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "a.src"
            src.write_bytes(b"A" * 4096)
            p = _tl(root, sources=[{"id": "a", "path": "a.src"}])
            c = Ctx(p)
            old_key = hygiene.audio_key(c, "a")

            def fake_run_parallel(ctx, tasks, what):
                for _label, cmd in tasks:
                    _publish_wav(Path(str(cmd[-1])), b"OLD-SOURCE-WAV")
                                                  # 旧源身份的 wav 已发布（B10 终态）
                src.write_bytes(b"B" * 4096)        # ← 同路径换源（渲染期间）

            err = io.StringIO()
            code = None
            with mock.patch.object(stages, "run_parallel", fake_run_parallel), \
                    contextlib.redirect_stderr(err):
                try:
                    stages.prepare(c)
                except SystemExit as e:
                    code = e.code
            self.assertEqual(code, 1)
            self.assertIn("issue #32", err.getvalue())
            self.assertFalse((root / "cache" / "manifest.json").exists(),
                             "源被换后 manifest 不得记账")
            # 失败回执显式落盘（prepare 走 receipt 门失败路径）
            rec = json.loads((root / "out" / "receipt.json").read_text(encoding="utf-8"))
            self.assertIs(rec["stages"]["prepare"]["ok"], False)
            self.assertIn("issue #32", rec["stages"]["prepare"]["detail"])
            # 新输入身份不得命中旧产物：新键分键、新键路径无 wav、旧 wav 留旧键待 gc
            c2 = Ctx(p)
            new_key = hygiene.audio_key(c2, "a")
            self.assertNotEqual(new_key, old_key)
            self.assertFalse((c2.cache / "audio" / f"{new_key}.wav").exists())
            self.assertTrue((c2.cache / "audio" / f"{old_key}.wav").exists())


if __name__ == "__main__":
    unittest.main()
