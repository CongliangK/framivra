#!/usr/bin/env python3
"""tests/test_pluginlib.py —— v2 §2 协议运行时（pluginlib）+ 三不变量接线 + 两缝。

覆盖：
  ① manifest 校验正/负用例（§2.3：name 规则与目录一致/entry.cli 存在性/health
     command 根内相对（安全节拒绝对路径与 ..）/plugin extends/hooks 值域；
     损坏 manifest 收集全部错误不崩）；
  ② registry：plugins/registry.json 静态注册表加载（denoise/duck-static 键声明）、
     lookup_key / plugin_state（本轮全部 not_installed）；
  ③ validate 三分支（§2.1 不变量 ③）：denoise/duck_strategy 键 → 报错带安装提示
     （vt plugins list）；未知键 → 警告不阻断；`_meta` 永久白名单；无插件键的
     timeline → validate 输出与 P1-1 完全一致（零 ⚠/零新行）；
  ④ 不变量 ①② 空转：contribute_keys 空集并入 ak（gc 键集不变）/get_cache_dirs
     空集/cache 白名单不变；receipt_fingerprint 末尾含 active_plugins_digest 段
     （mock 替身验证指纹随插件段变化——v1 回执 v2 下失效属设计内）；
  ⑤ 两缝空转：run_prepare_source 恒 False（prepare 直抽）/run_audio_events 恒等。
运行：bash scripts/run_all_tests.sh（统一入口）。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from vtx import hygiene
from vtx.cache_state import receipt_fingerprint
from vtx.infra import Ctx
from vtx.pluginlib import config as pl_config
from vtx.pluginlib import hooks as pl_hooks
from vtx.pluginlib import manifest as pl_manifest
from vtx.pluginlib import registry as pl_registry
from vtx.stages import _scan_plugin_keys


def _tl(root: Path, **over):
    tl = {"version": 2,
          "project": {"fps": 30, "duration_frames": 180, "width": 1920, "height": 1080},
          "sources": [], "video_tracks": [], "subtitles": [], "audio_tracks": []}
    tl.update(over)
    p = root / "timeline.json"
    p.write_text(json.dumps(tl), encoding="utf-8")
    return p


# ---------------------------------------------------------------- manifest
def _make_plugin_dir(root: Path, name="demo", **over):
    d = root / name
    d.mkdir(parents=True)
    (d / "cli.py").write_text("print('ok')\n", encoding="utf-8")
    (d / "health.py").write_text("print('healthy')\n", encoding="utf-8")
    (d / "README.md").write_text("# demo\n", encoding="utf-8")
    m = {"name": name, "version": "1.0.0", "kind": "plugin",
         "entry": {"cli": ["cli.py"]},
         "health": {"type": "command", "command": "health.py"},
         "requires": {"core": ">=2.0.0", "binaries": [], "python_packages": [], "platforms": []},
         "config_keys": [],
         "docs": "README.md",
         "extends": {"schema_keys": [{"path": "sources[]", "key": "demo"}],
                     "hooks": ["prepare_source"]}}
    m.update(over)
    (d / "plugin.json").write_text(json.dumps(m), encoding="utf-8")
    return d, m


class ManifestValidation(unittest.TestCase):
    def test_valid_manifest_passes(self):
        with tempfile.TemporaryDirectory() as d:
            pdir, m = _make_plugin_dir(Path(d))
            data, errors = pl_manifest.load_manifest(pdir)
            self.assertEqual(errors, [])
            self.assertEqual(data["name"], "demo")

    def test_errors_collected_not_crashed(self):
        with tempfile.TemporaryDirectory() as d:
            pdir, _ = _make_plugin_dir(Path(d), name="demo",
                                       version=123,
                                       entry={"cli": ["/abs/path.py"]},
                                       health={"type": "command", "command": "/abs/evil.py"},
                                       docs="missing.md",
                                       extends={"schema_keys": ["bad"], "hooks": ["nonexistent_hook"]})
            data, errors = pl_manifest.load_manifest(pdir)
            self.assertIsNone(data)
            joined = "\n".join(errors)
            self.assertIn("version", joined)
            self.assertIn("绝对路径", joined)          # entry.cli 拒绝对路径
            self.assertIn("绝对路径", joined)          # health.command 拒绝对路径
            self.assertIn("不存在", joined)            # docs 缺失
            self.assertIn("nonexistent_hook", joined)  # hooks 值域（已冻结扩展点）
            self.assertIn("{path, key}", joined)       # schema_keys 形状
            self.assertGreater(len(errors), 5)         # 收集全部错误不崩（非首错即止）

    def test_bad_kind_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            pdir, _ = _make_plugin_dir(Path(d), name="demo", kind="widget")
            _, errors = pl_manifest.load_manifest(pdir)
            self.assertTrue(any("kind" in e for e in errors))

    def test_health_command_dotdot_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            pdir, _ = _make_plugin_dir(Path(d), name="demo",
                                       health={"type": "command", "command": "../evil.py"})
            _, errors = pl_manifest.load_manifest(pdir)
            self.assertTrue(any(".." in e for e in errors))
            self.assertTrue(any("health.command" in e for e in errors))

    def test_name_mismatch_dir_and_bad_name(self):
        with tempfile.TemporaryDirectory() as d:
            # 大写不合规：name 合法性独立报错（目录名一致，不触发 mismatch）
            pdir, _ = _make_plugin_dir(Path(d), name="Demo")
            _, errors = pl_manifest.load_manifest(pdir)
            self.assertTrue(any("不合规则" in e for e in errors))
            # 合法名但与目录名不一致：mismatch 独立报错
            pdir2, _ = _make_plugin_dir(Path(d), name="demo")
            m = json.loads((pdir2 / "plugin.json").read_text(encoding="utf-8"))
            m["name"] = "other-name"
            (pdir2 / "plugin.json").write_text(json.dumps(m), encoding="utf-8")
            _, errors2 = pl_manifest.load_manifest(pdir2)
            self.assertTrue(any("目录名" in e for e in errors2))

    def test_broken_json_collects_error(self):
        with tempfile.TemporaryDirectory() as d:
            pdir = Path(d) / "broken"
            pdir.mkdir()
            (pdir / "plugin.json").write_text("{oops", encoding="utf-8")
            data, errors = pl_manifest.load_manifest(pdir)
            self.assertIsNone(data)
            self.assertTrue(any("解析失败" in e for e in errors))

    def test_missing_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            data, errors = pl_manifest.load_manifest(Path(d) / "empty")
            self.assertIsNone(data)
            self.assertTrue(any("缺少 manifest" in e for e in errors))


# ---------------------------------------------------------------- registry
class RegistryTest(unittest.TestCase):
    """repo 实 registry.json（本轮创建：denoise/duck-static 两规划插件声明）。"""

    def test_static_registry_loads(self):
        reg = pl_registry.load_static_registry(REPO)["plugins"]
        self.assertIn("denoise", reg)
        self.assertIn("duck-static", reg)
        self.assertEqual(reg["denoise"]["keys"],
                         [{"path": "sources[]", "key": "denoise"}])
        self.assertEqual(reg["duck-static"]["keys"],
                         [{"path": "audio_tracks[]", "key": "duck_strategy"}])
        self.assertFalse(reg["denoise"]["installed"])     # P2b 落地时置 true

    def test_lookup_key(self):
        name, ki = pl_registry.lookup_key("denoise", REPO)
        self.assertEqual((name, ki["path"]), ("denoise", "sources[]"))
        name2, ki2 = pl_registry.lookup_key("duck_strategy", REPO)
        self.assertEqual((name2, ki2["path"]), ("duck-static", "audio_tracks[]"))
        self.assertEqual(pl_registry.lookup_key("no_such_key", REPO), (None, None))

    def test_plugin_state_not_installed(self):
        self.assertEqual(pl_registry.plugin_state("denoise", REPO), "not_installed")
        self.assertEqual(pl_registry.plugin_state("duck-static", REPO), "not_installed")
        self.assertEqual(pl_registry.plugin_state("unknown-thing", REPO), "not_installed")

    def test_builtin_scan_empty_ok(self):
        entries = pl_registry.scan_builtin(REPO)   # apps/ 不存在、plugins/ 只有 registry.json
        self.assertEqual(entries, [])


# ---------------------------------------------------------------- 三分支扫描（纯函数层）
class _FakeCtx:
    """_scan_plugin_keys 只消费 c.tl——免 ffmpeg 的鸭子类型上下文。"""

    def __init__(self, tl):
        self.tl = tl


class ThreeBranchScan(unittest.TestCase):
    def test_declared_not_installed_errors_with_hint(self):
        tl = {"project": {"fps": 30},
              "sources": [{"id": "a", "path": "a.mp4", "denoise": {"strength": 0.8}}],
              "audio_tracks": [{"name": "bed", "duck_strategy": "static", "events": []}]}
        errs, warns = [], []
        with redirect_stdout(io.StringIO()):
            warns = _scan_plugin_keys(_FakeCtx(tl), errs)
        joined = "\n".join(errs)
        self.assertEqual(len(errs), 2)
        self.assertIn("由 plugins/denoise 提供，未安装", joined)
        self.assertIn("由 plugins/duck-static 提供，未安装", joined)
        self.assertIn("vt plugins list 查看", joined)
        self.assertEqual(warns, [])   # 已声明键不算未知键

    def test_unknown_key_warns_not_blocks(self):
        tl = {"project": {"fps": 30, "shiny_new_knob": 1},
              "sources": [{"id": "a", "path": "a.mp4", "whatever": True}],
              "audio_tracks": []}
        errs, warns = [], []
        with redirect_stdout(io.StringIO()):
            warns = _scan_plugin_keys(_FakeCtx(tl), errs)
        self.assertEqual(errs, [])                    # 警告不阻断
        self.assertEqual(len(warns), 2)
        self.assertTrue(any("shiny_new_knob" in w for w in warns))
        self.assertTrue(any("whatever" in w for w in warns))

    def test_meta_permanent_whitelist(self):
        tl = {"project": {"fps": 30, "_meta": {"script": "bed_static_duck.py"}},
              "sources": [{"id": "a", "path": "a.mp4", "_meta": {"audio_report": 1}}],
              "audio_tracks": [{"name": "bed", "_meta": {"x": 1},
                                "events": [{"src": "a", "in": 0, "out": 1, "volume": 1,
                                            "_meta": {"y": 2}}]}]}
        errs, warns = [], []
        with redirect_stdout(io.StringIO()):
            warns = _scan_plugin_keys(_FakeCtx(tl), errs)
        self.assertEqual(errs, [])
        self.assertEqual(warns, [])                   # _meta 各层永久白名单

    def test_kernel_keys_pass_silently(self):
        tl = {"project": {"fps": 30, "width": 1920, "height": 1080,
                          "duration_frames": 90, "cache_dependencies": []},
              "sources": [{"id": "a", "path": "a.mp4", "transform": "crop_center_scale"}],
              "audio_tracks": [{"name": "voice", "duck": True, "duck_by": ["voice"],
                                "duck_params": {"ratio": 4},
                                "events": [{"src": "a", "src_in": 0, "in": 0, "out": 30,
                                            "volume": 1, "pan": 0, "loop": False,
                                            "fade_in_frames": 3, "fade_out_frames": 3}]}]}
        errs, warns = [], []
        with redirect_stdout(io.StringIO()):
            warns = _scan_plugin_keys(_FakeCtx(tl), errs)
        self.assertEqual(errs, [])
        self.assertEqual(warns, [])                   # 全部内核已知键 → 零输出


# ---------------------------------------------------------------- 三不变量空转
class InvariantsIdle(unittest.TestCase):
    def test_contribute_keys_and_cache_dirs_empty(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = Ctx(_tl(root))
            self.assertEqual(pl_hooks.contribute_keys(c), set())
            self.assertEqual(pl_hooks.get_cache_dirs(), set())
            # 空生效清单返回 ""（指纹侧省略段）——v1 回执兼容的前提
            self.assertEqual(pl_hooks.active_plugins_digest(c), "")
            # ak 并入空集 → _current_keys 与 P1-1 等价（sources 空 → ak 空）
            gfx, seg, ak = hygiene._current_keys(c)
            self.assertEqual(ak, set())

    def test_cache_root_whitelist_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = Ctx(_tl(root))
            (root / "cache").mkdir()
            (root / "cache" / "mystery").mkdir()      # 无插件声明 → 仍判 legacy 垃圾
            plan = hygiene.gc_plan(c)
            listed = [str(e[0]) for items in plan.values() for e in items]
            self.assertIn(str(root / "cache" / "mystery"), listed)

    def test_receipt_fingerprint_contains_plugin_segment(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = Ctx(_tl(root))
            fp1 = receipt_fingerprint(c)
            # mock 生效插件段 → 指纹必须变（不变量 ② 的机制验证；真实插件 P2b 落地）
            with mock.patch("vtx.pluginlib.hooks.active_plugins_digest",
                            return_value="plugins:denoise@0.1.0:abc123"):
                fp2 = receipt_fingerprint(c)
            self.assertNotEqual(fp1, fp2)
            # 空转稳定（幂等）
            self.assertEqual(fp1, receipt_fingerprint(c))
            # 空段显式省略：digest="" 时指纹与空转逐字节一致——v1 回执在 v2 下
            # 继续有效（无插件时间线指纹与 v1 相同），影子基线温态复验依赖此。
            with mock.patch("vtx.pluginlib.hooks.active_plugins_digest",
                            return_value=""):
                self.assertEqual(receipt_fingerprint(c), fp1)
            # 卸载回落语义：带段录制的指纹 ≠ 无段重算（旧回执同样被拒绝）
            with mock.patch("vtx.pluginlib.hooks.active_plugins_digest",
                            return_value="plugins:denoise@0.1.0:abc123"):
                fp_with_seg = receipt_fingerprint(c)
            self.assertNotEqual(fp_with_seg, fp1)

    def test_seams_idle(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = Ctx(_tl(root))
            self.assertFalse(pl_hooks.run_prepare_source(c, "a", root / "x.wav"))
            tracks = [{"name": "voice", "events": []}]
            self.assertIs(pl_hooks.run_audio_events(c, tracks), tracks)


# ---------------------------------------------------------------- CLI 三分支（端到端）
def _synth_assets(root: Path):
    ff = shutil.which("ffmpeg")
    if not ff:
        return None
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    p = assets / "a.mp4"
    subprocess.run([ff, "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=2",
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(p)], check=True)
    return assets


@unittest.skipUnless(shutil.which("ffmpeg"), "CLI 三分支需要 ffmpeg（合成媒体）")
class ThreeBranchCli(unittest.TestCase):
    def _run(self, tl_path):
        return subprocess.run(
            ["env", "python3", str(REPO / "vtx" / "vtx.py"), "-t", str(tl_path), "validate"],
            capture_output=True, text=True, cwd=str(REPO))

    def _proj(self, root: Path, **src_over):
        tl = {"version": 2,
              "project": {"fps": 30, "width": 640, "height": 360, "duration_frames": 60},
              "sources": [{"id": "a", "path": str(root / "assets" / "a.mp4"), **src_over}],
              "video_tracks": [{"name": "base", "kind": "aroll", "clips": [
                  {"src": "a", "src_in": 0, "src_out": 60, "dst_in": 0, "dur": 60}]}],
              "subtitles": [],
              "audio_tracks": [{"name": "voice", "events": [
                  {"src": "a", "in": 0, "out": 60, "volume": 0.8}]}]}
        p = root / "timeline.json"
        p.write_text(json.dumps(tl), encoding="utf-8")
        return p

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _synth_assets(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_denoise_key_fails_with_install_hint(self):
        p = self._proj(self.root, denoise={"strength": 0.8})
        r = self._run(p)
        self.assertEqual(r.returncode, 1)
        self.assertIn("由 plugins/denoise 提供，未安装", r.stdout)
        self.assertIn("vt plugins list", r.stdout)

    def test_duck_strategy_key_fails_with_install_hint(self):
        p = self._proj(self.root)
        tl = json.loads(p.read_text(encoding="utf-8"))
        tl["audio_tracks"][0]["duck_strategy"] = "static"
        p.write_text(json.dumps(tl), encoding="utf-8")
        r = self._run(p)
        self.assertEqual(r.returncode, 1)
        self.assertIn("由 plugins/duck-static 提供，未安装", r.stdout)

    def test_clean_timeline_output_identical_to_p1_1(self):
        # 无插件键/未知键：validate 成功行与 P1-1 逐字一致（零 ⚠ 零新行）
        p = self._proj(self.root)
        r = self._run(p)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("✓ 校验通过：1 源 / 1 视频轨 1 clips / 0 字幕 / 1 音轨 1 事件", r.stdout)
        self.assertNotIn("⚠", r.stdout)
        self.assertNotIn("未知键", r.stdout)

    def test_unknown_key_warns_but_passes(self):
        p = self._proj(self.root, shiny_knob=1)
        r = self._run(p)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)   # 警告不阻断
        self.assertIn("⚠ sources[0]: 未知键 'shiny_knob'", r.stdout)

    def test_meta_key_silent(self):
        p = self._proj(self.root)
        tl = json.loads(p.read_text(encoding="utf-8"))
        tl["_meta"] = {"bed_static_duck": {"script": "scripts/bed_static_duck.py"}}
        p.write_text(json.dumps(tl), encoding="utf-8")
        r = self._run(p)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("⚠", r.stdout)


# --------------------------------------- 三分支补全 + 假插件总线正例（P1 审查 F3）
class ThreeBranchRest(unittest.TestCase):
    """P1 对抗审查 F3：active 放行 / disabled 报错 / path 不符三分支此前零覆盖。"""

    def _scan(self, tl):
        errs, warns = [], []
        with redirect_stdout(io.StringIO()):
            warns = _scan_plugin_keys(_FakeCtx(tl), errs)
        return errs, warns

    def test_active_branch_passes_silently(self):
        tl = {"project": {"fps": 30},
              "sources": [{"id": "a", "path": "a.mp4", "denoise": {"strength": 0.8}}],
              "audio_tracks": [{"name": "bed", "duck_strategy": "static", "events": []}]}
        with mock.patch("vtx.pluginlib.registry.plugin_state", return_value="active"):
            errs, warns = self._scan(tl)
        self.assertEqual(errs, [])    # 分支 1：已装已启用 → 合法放行
        self.assertEqual(warns, [])

    def test_disabled_branch_errors_with_enable_hint(self):
        tl = {"project": {"fps": 30},
              "sources": [{"id": "a", "path": "a.mp4", "denoise": {}}],
              "audio_tracks": []}
        with mock.patch("vtx.pluginlib.registry.plugin_state", return_value="disabled"):
            errs, warns = self._scan(tl)
        self.assertEqual(len(errs), 1)
        self.assertIn("已安装但被禁用", errs[0])
        self.assertIn("registry.json 启用", errs[0])
        self.assertEqual(warns, [])

    def test_declared_key_wrong_path_warns_as_unknown(self):
        tl = {"project": {"fps": 30},
              "sources": [{"id": "a", "path": "a.mp4"}],
              # duck_strategy 声明于 audio_tracks[] 却写在 project → 位置不符
              "audio_tracks": []}
        tl["project"]["duck_strategy"] = "static"
        errs, warns = self._scan(tl)
        self.assertEqual(errs, [])
        self.assertEqual(len(warns), 1)
        self.assertIn("位置不符", warns[0])


class FakePluginBus(unittest.TestCase):
    """P1 对抗审查 F3：假插件模块验证总线正例（短路 True / 链式变换 / 键并集）
    与加载失败 fail-hard（F4）。"""

    def setUp(self):
        import types
        pkg = types.ModuleType("plugins")
        mod = types.ModuleType("plugins.fakeplug")

        def prepare_source(c, sid, wav):
            return sid == "src1"          # 只受理 src1，其余回落直抽

        def audio_events(c, tracks):
            out = []
            for tr in tracks:
                tr = dict(tr)
                if tr.get("name") == "bed":
                    tr["events"] = [dict(e, volume=e["volume"] * 0.5)
                                    for e in tr.get("events", [])]
                out.append(tr)
            return out

        def contribute_keys(c):
            return {"fakeplug:abc123"}
        mod.prepare_source = prepare_source
        mod.audio_events = audio_events
        mod.contribute_keys = contribute_keys
        sys.modules["plugins"] = pkg
        sys.modules["plugins.fakeplug"] = mod
        self._fake_mod = mod
        reg = {"fakeplug": {"version": "0.1.0",
                            "hooks": ["prepare_source", "audio_events"]}}
        self._patches = [
            mock.patch("vtx.pluginlib.registry.declared_plugins", return_value=reg),
            mock.patch("vtx.pluginlib.registry.plugin_state", return_value="active"),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(self._teardown)

    def _teardown(self):
        for p in self._patches:
            p.stop()
        sys.modules.pop("plugins.fakeplug", None)
        sys.modules.pop("plugins", None)

    def test_prepare_source_short_circuits_for_handled_source(self):
        with tempfile.TemporaryDirectory() as d:
            c = Ctx(_tl(Path(d)))
            self.assertTrue(pl_hooks.run_prepare_source(c, "src1", Path(d) / "x.wav"))
            self.assertFalse(pl_hooks.run_prepare_source(c, "src2", Path(d) / "y.wav"))

    def test_audio_events_transforms_tracks(self):
        with tempfile.TemporaryDirectory() as d:
            c = Ctx(_tl(Path(d)))
            tracks = [{"name": "bed", "events": [{"in": 0, "out": 30, "volume": 1.0}]},
                      {"name": "voice", "events": [{"in": 0, "out": 30, "volume": 0.9}]}]
            out = pl_hooks.run_audio_events(c, tracks)
            self.assertEqual(out[0]["events"][0]["volume"], 0.5)   # bed 被变换
            self.assertEqual(out[1]["events"][0]["volume"], 0.9)   # voice 原样
            self.assertEqual(tracks[0]["events"][0]["volume"], 1.0)  # 纯内存不改入参

    def test_contribute_keys_unions_plugin_keys(self):
        with tempfile.TemporaryDirectory() as d:
            c = Ctx(_tl(Path(d)))
            self.assertEqual(pl_hooks.contribute_keys(c), {"fakeplug:abc123"})

    def test_active_plugin_module_load_failure_fails_hard(self):
        with mock.patch.object(pl_hooks, "_load_plugin_module",
                               side_effect=RuntimeError("安装破损")):
            with tempfile.TemporaryDirectory() as d:
                c = Ctx(_tl(Path(d)))
                with self.assertRaises(RuntimeError):
                    pl_hooks.run_prepare_source(c, "src1", Path(d) / "x.wav")

    def test_params_digest_collects_all_instances(self):
        """P1 对抗审查 F2：多实例参数全收集——改第二例参数 digest 必须变。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tl1 = _tl(root, sources=[{"id": "a", "path": "a.mp4", "denoise": {"x": 0.2}},
                                     {"id": "b", "path": "b.mp4", "denoise": {"x": 0.2}}])
            c1 = Ctx(tl1)
            # registry_key_map 真实读取（denoise→sources[]）；手动喂两实例再改后例
            tl2 = _tl(root, sources=[{"id": "a", "path": "a.mp4", "denoise": {"x": 0.2}},
                                     {"id": "b", "path": "b.mp4", "denoise": {"x": 0.9}}])
            c2 = Ctx(tl2)
            d1 = pl_hooks._plugin_params_digest(c1, "denoise")
            d2 = pl_hooks._plugin_params_digest(c2, "denoise")
            self.assertNotEqual(d1, d2, "第二实例参数变化必须使 digest 失效")


if __name__ == "__main__":
    unittest.main()
