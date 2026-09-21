#!/usr/bin/env python3
"""tests/test_protocol_v12.py —— 插件协议 v1.2（P6，2026-09-14）内核接线回归。

覆盖（对应 P6 体检修复清单 + 两新插件接线）：
  ① root scope 扫描：顶层未知键警告 / blender_bridge 键 active 放行 /
     not_installed 报「未安装」（KERNEL_KEYS["root"] 自此不是死键集）；
  ② 缝 C probe_source 内核接线：bgm 源（音乐工程 JSON）经插件供给 facts，
     validate 全链路不再 ffprobe 误报「无音频轨」（伪 bgm CLI，真 registry）；
  ③ registry hooks 空列表 fallback manifest 声明（A-P1-3）；
  ④ _scope_items 四 scope 对齐（A-P1-2）：project 声明键进参数摘要与生效判定；
  ⑤ gc 豁免 loudness_receipts 伴生件（B-P2-1：.lock/.tmp 不再判垃圾）；
  ⑥ validate 必填键（B-P2-3）：events 缺 volume / 轨缺 events / 缺 name——
     报错不裸 KeyError；
  ⑦ 区间渲染陈旧回执（B-P2-2）：指纹失配回落 None（窗口自测路径）；
  ⑧ denoise 产物键掺代码摘要（A-P1-1）：键名尾部含 12 位 code digest。
运行：bash scripts/run_all_tests.sh（批次一）。
"""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from vtx import hygiene
from vtx.pluginlib import hooks as pl_hooks
from vtx.pluginlib import registry as pl_registry
from vtx.stages import _scan_plugin_keys, validate


def _write(root: Path, tl: dict) -> Path:
    p = root / "timeline.json"
    p.write_text(json.dumps(tl), encoding="utf-8")
    return p


def _tl(**over):
    tl = {"version": 2,
          "project": {"fps": 30, "duration_frames": 180, "width": 640, "height": 360},
          "sources": [], "video_tracks": [], "subtitles": [], "audio_tracks": []}
    tl.update(over)
    return tl


class RootScopeScan(unittest.TestCase):
    def test_unknown_root_key_warns(self):
        from vtx.infra import Ctx
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = Ctx(_write(root, _tl(mystery_top_key={"a": 1})))
            errs = []
            warns = _scan_plugin_keys(c, errs)
            self.assertFalse(errs)
            self.assertTrue(any("mystery_top_key" in w and "未知键" in w
                                for w in warns))

    def test_blender_bridge_key_active_passes(self):
        from vtx.infra import Ctx
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = Ctx(_write(root, _tl(blender_bridge=True)))
            errs = []
            warns = _scan_plugin_keys(c, errs)
            self.assertFalse(errs)
            self.assertFalse(warns)

    def test_blender_bridge_not_installed_errors(self):
        from vtx.infra import Ctx
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = Ctx(_write(root, _tl(blender_bridge=True)))
            errs = []
            with mock.patch("vtx.pluginlib.registry.plugin_state",
                            return_value="not_installed"):
                _scan_plugin_keys(c, errs)
            self.assertTrue(any("未安装" in e for e in errs))


class ProbeSourceIntegration(unittest.TestCase):
    """缝 C：bgm 源（工程 JSON）validate 全链——伪 bgm CLI + 真 registry 插件。"""

    FAKE = r'''#!/bin/bash
proj="${@: -1}"
dur=$(cat "$(dirname "$proj")/duration.txt" 2>/dev/null || echo 4.0)
printf '{\n  "duration_s": %s\n}\n' "$dur"
'''

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        fake = self.root / "fakebgm"
        fake.write_text(self.FAKE, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        cfg = self.root / "cfg.json"
        cfg.write_text(json.dumps({"bgm": {"cli": str(fake)}}), encoding="utf-8")
        self._old = os.environ.get("VTX_CONFIG")
        os.environ["VTX_CONFIG"] = str(cfg)
        (self.root / "bgm.json").write_text('{"name": "x"}', encoding="utf-8")
        (self.root / "duration.txt").write_text("4.0", encoding="utf-8")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("VTX_CONFIG", None)
        else:
            os.environ["VTX_CONFIG"] = self._old
        self._tmp.cleanup()

    def _ctx(self, events):
        tl = _tl(sources=[{"id": "bgm_main", "path": "bgm.json", "bgm": True}],
                 audio_tracks=[{"name": "bed", "events": events}])
        from vtx.infra import Ctx
        return Ctx(_write(self.root, tl))

    def test_validate_passes_with_virtual_source(self):
        c = self._ctx([{"src": "bgm_main", "in": 0, "out": 100, "volume": 0.9}])
        with mock.patch("sys.stdout"):   # 静音 validate 打印（receipt 写入 out/）
            validate(c)                  # 不得 sys.exit——facts 来自插件而非 ffprobe

    def test_validate_duration_gate_uses_plugin_facts(self):
        # nb_frames=120（4.0s×30fps）；事件需求 150 > 120 → 音频源不足 FAIL
        import io
        from contextlib import redirect_stdout
        c = self._ctx([{"src": "bgm_main", "in": 0, "out": 150, "volume": 0.9}])
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                validate(c)


class RegistryHooksFallback(unittest.TestCase):
    def test_empty_hooks_falls_back_to_manifest(self):
        entry_dir = REPO / "plugins" / "duck-static"
        fake_entry = {"name": "duck-static", "kind": "plugin", "source": "builtin",
                      "dir": entry_dir, "enabled": True,
                      "manifest": {"extends": {"hooks": ["validate", "audio_events"]}},
                      "manifest_errors": []}
        with mock.patch.object(pl_registry, "declared_plugins",
                               return_value={"duck-static": {"hooks": []}}), \
             mock.patch.object(pl_registry, "discover", return_value=[fake_entry]):
            got = pl_hooks._plugin_hooks_declared("duck-static")
        self.assertEqual(got, ["validate", "audio_events"],
                         "registry hooks 空列表须 fallback 实体 manifest 声明")

    def test_nonempty_registry_hooks_wins(self):
        with mock.patch.object(pl_registry, "declared_plugins",
                               return_value={"duck-static": {"hooks": ["validate"]}}):
            got = pl_hooks._plugin_hooks_declared("duck-static")
        self.assertEqual(got, ["validate"])


class ScopeItemsAlignment(unittest.TestCase):
    def test_project_scope_declared_key_activates(self):
        class _C:
            tl = _tl(project={"fps": 30, "width": 640, "height": 360,
                             "duration_frames": 180, "magic": {"x": 1}})
        kmap = {"magic": {"plugin": "demo", "path": "project"}}
        with mock.patch.object(pl_registry, "key_map", return_value=kmap):
            self.assertTrue(pl_hooks._timeline_declares(_C(), "demo"))
            d = pl_hooks._plugin_params_digest(_C(), "demo")
        self.assertNotIn("params\": {}", d, "project 声明键参数须进摘要")


class GcLoudnessSidecarExemption(unittest.TestCase):
    def test_lock_and_tmp_exempt(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "cache" / "audio").mkdir(parents=True)
            (root / "cache" / "audio" / "loudness_receipts.json").write_text("{}", encoding="utf-8")
            (root / "cache" / "audio" / "loudness_receipts.json.lock").write_text("", encoding="utf-8")
            (root / "cache" / "audio" / "loudness_receipts.json.tmp").write_text("", encoding="utf-8")
            from vtx.infra import Ctx
            c = Ctx(_write(root, _tl()))
            plan = hygiene.gc_plan(c)
            names = [p.name for p, _, _ in plan["audio"]]
            self.assertNotIn("loudness_receipts.json.lock", names,
                             "flock 锁文件被 gc 回收会破坏跨进程互斥（B-P2-1）")
            self.assertNotIn("loudness_receipts.json.tmp", names)
            self.assertNotIn("loudness_receipts.json", names)


class GcOutDeliverableExemption(unittest.TestCase):
    def test_final_4k_and_freeze_exempt(self):
        # 2026-09-14 白名单遗漏交付件修复：final_4k*.mp4（apps/publish/export_4k
        # 发布件与 x264 对照件）与 FREEZE*.json（runbook 步骤 6 冻结记录）不进
        # gc 回收清单；白名单外 out 件仍照常列为「out 一次性中间物」
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "cache").mkdir()
            out = root / "out"; out.mkdir(parents=True)
            for n in ("final_4k.mp4", "final_4k_x264_ref.mp4", "FREEZE.json",
                      "FREEZE_v47.json", "tmp_junk.bin"):
                (out / n).write_bytes(b"x" * 8)
            from vtx.infra import Ctx
            c = Ctx(_write(root, _tl()))
            plan = hygiene.gc_plan(c)
            names = [p.name for p, _, _ in plan["out_intermediate"]]
            for keep in ("final_4k.mp4", "final_4k_x264_ref.mp4",
                         "FREEZE.json", "FREEZE_v47.json"):
                self.assertNotIn(keep, names,
                                 f"{keep} 是交付件/冻结证据，不得进回收清单")
            self.assertIn("tmp_junk.bin", names,
                          "白名单外 out 中间物仍应照常列为可回收")


class ValidateRequiredKeys(unittest.TestCase):
    def _run(self, tl):
        import io
        from contextlib import redirect_stdout
        from vtx.infra import Ctx
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = Ctx(_write(root, tl))
            buf = io.StringIO()
            with redirect_stdout(buf):
                try:
                    validate(c)
                    return [], buf.getvalue()
                except SystemExit:
                    return ["<exit1>"], buf.getvalue()

    def test_event_missing_volume_reported_not_crash(self):
        tl = _tl(audio_tracks=[{"name": "bed",
                                "events": [{"src": "x", "in": 0, "out": 30}]}])
        errs, out = self._run(tl)
        self.assertTrue(any("volume" in e for e in errs) or "volume" in out)

    def test_track_missing_events_reported_not_crash(self):
        tl = _tl(audio_tracks=[{"name": "bed"}])
        errs, out = self._run(tl)
        self.assertIn("events", out)

    def test_track_missing_name_no_crash(self):
        tl = _tl(audio_tracks=[{"events": []}])
        errs, out = self._run(tl)   # 不得裸 KeyError（此前 tr['name'] 崩栈）


class RangeStaleReceipt(unittest.TestCase):
    def test_stale_fingerprint_returns_none(self):
        from vtx.range import _receipt_audio_gain
        from vtx.infra import Ctx
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "out").mkdir()
            c = Ctx(_write(root, _tl()))
            (root / "out" / "receipt.json").write_text(json.dumps(
                {"input_fingerprint": "STALE",
                 "stages": {"audio": {"gain_db": 2.5, "ok": True}}}), encoding="utf-8")
            self.assertIsNone(_receipt_audio_gain(c),
                              "指纹失配的陈旧回执不得供给区间增益（B-P2-2）")

    def test_fresh_receipt_supplies_gain(self):
        from vtx.range import _receipt_audio_gain
        from vtx.infra import Ctx
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "out").mkdir()
            c = Ctx(_write(root, _tl()))
            (root / "out" / "receipt.json").write_text(json.dumps(
                {"input_fingerprint": c.input_fingerprint,
                 "stages": {"audio": {"gain_db": 2.5, "ok": True}}}), encoding="utf-8")
            self.assertEqual(_receipt_audio_gain(c), 2.5)


class DenoiseProductKeyDigest(unittest.TestCase):
    def test_product_path_carries_code_digest(self):
        import importlib
        den = importlib.import_module("plugins.denoise")

        class _C:
            tl = _tl(sources=[{"id": "s1", "path": "a.wav", "denoise": True}])
            root = Path("/nonexistent-proj")
            cache = Path("/nonexistent-proj/cache")
        # audio_key 需要源文件 stat——mock 插件模块内已绑定的引用（import 时绑定）
        with mock.patch.object(den, "_audio_key", return_value="ak123"):
            p = den.product_path(_C(), "s1")
        name = p.name
        self.assertIn("::denoise:2.0.0:", name)   # R-20 P2 升级连带随迁(P3:根 tests 版本钉遗漏,越界申报获批,仅字面量)
        parts = name[:-4].split(":")
        self.assertEqual(len(parts[-1]), 12,
                         f"产物键尾部须为 12 位代码摘要（A-P1-1）：{name}")


if __name__ == "__main__":
    unittest.main()
