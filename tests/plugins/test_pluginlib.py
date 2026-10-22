#!/usr/bin/env python3
"""tests/plugins/test_pluginlib.py —— v2 §2 协议运行时（pluginlib）+ 三不变量接线 + 两缝。

覆盖：
  ① manifest 校验正/负用例（§2.3：name 规则与目录一致/entry.cli 存在性/health
     command 根内相对（安全节拒绝对路径与 ..）/plugin extends/hooks 值域；
     损坏 manifest 收集全部错误不崩；A03 issue #22/F-02：路径符号链接
     解析后仍须在插件根内——根外符号链接与断链拒绝）；
  ② registry：plugins/registry.json 静态注册表加载（denoise/duck-static 键声明）、
     lookup_key / plugin_state（P2b 起 denoise 落地 active；未装/禁用分支 mock 驱动）；
  ③ validate 三分支（§2.1 不变量 ③）：denoise/duck_strategy 键 → 已装 active 放行
     （CLI 真跑）；mock not_installed → 报错+安装提示；mock disabled → 启用提示；
     未知键 → 警告不阻断；`_meta` 永久白名单；无插件键的 timeline → validate
     输出与 P1-1 完全一致（零 ⚠/零新行）；
  ④ 不变量 ①② 空转（无插件键时间线，生效过滤后恒空转）：contribute_keys 空集
     并入 ak（gc 键集不变）/get_cache_dirs 空集/cache 白名单不变/键级通道
     get_cache_dir_keys 恒空 dict（R1-F F2）；
     receipt_fingerprint 末尾含 active_plugins_digest 段（mock 替身验证指纹随
     插件段变化——v1 回执 v2 下失效属设计内；真实插件路径的正例见
     plugins/denoise/tests）；
  ⑤ 两缝：run_prepare_source 契约 v1.1（(c,sid)->Path|None，未受理 → None）/
     run_audio_events 恒等；hooks 声明↔代码注册对账（§2.3）；插件代码摘要
     幂等/翻转/tests 目录排除（R1-F F5：改测试 ≠ 改行为，不得翻转产物键）。
插件实体测试（denoise 键语义/产物路径/不变量真实路径）见 plugins/denoise/tests/。
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
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tests._support import full_hd_timeline as _tl

from tests._support import _make_plugin_dir

from vtx.cache import hygiene
from vtx.cache.cache_state import receipt_fingerprint
from vtx.infra import Ctx
from vtx.pluginlib import config as pl_config
from vtx.pluginlib import hooks as pl_hooks
from vtx.pluginlib import manifest as pl_manifest
from vtx.pluginlib import registry as pl_registry
from vtx.stages import _scan_plugin_keys


# ---------------------------------------------------------------- manifest


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
        for label, fields in (
                ("entry.cli", {"entry": {"cli": ["bad\0.py"]}}),
                ("health.command", {"health": {"type": "command", "command": "bad\0.py --check"}}),
                ("docs", {"docs": "bad\0.md"})):
            with self.subTest(path=label), tempfile.TemporaryDirectory() as d:
                pdir, _ = _make_plugin_dir(Path(d), **fields)
                data, errors = pl_manifest.load_manifest(pdir)
                self.assertIsNone(data)
                self.assertTrue(any(label in error and "无法安全解析" in error for error in errors))

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


# ------------------------------------- A03 符号链接逃逸（issue #22 / F-02 / #2）
@unittest.skipUnless(hasattr(os, "symlink"), "符号链接用例需要 os.symlink")
class ManifestSymlinkEscape(unittest.TestCase):
    """A03：三路径字段（health.command / entry.cli / docs）统一符号链接解析
    （resolve strict）后的真实落点必须落在插件根内——四类拒绝样本全部命中；
    合法相对路径（含指向根内文件的符号链接）正常通过。"""

    # 四类拒绝样本：类别 → (manifest 中的值, 须命中的错误文案片段)
    BAD = [
        ("absolute",    "/abs/evil.py",   "绝对路径"),   # 1 绝对路径
        ("dotdot",      "../outside.py",  ".."),         # 2 ..
        ("symlink_out", "escape.py",      "插件根外"),   # 3 根外符号链接
        ("broken",      "broken.py",      "不存在"),     # 4 断链
    ]

    def _build(self, d, **over):
        """标准插件目录 + 三件链接样本：escape→根外真文件 / broken→断链 /
        inside→根内文件（合法）；over 透传给 _make_plugin_dir 改路径字段。"""
        root = Path(d)
        (root / "outside.py").write_text("print('evil')\n", encoding="utf-8")
        pdir, _ = _make_plugin_dir(root, **over)
        os.symlink("../outside.py", pdir / "escape.py")         # 解析后出根
        os.symlink("../no_such_target.py", pdir / "broken.py")  # 断链
        os.symlink("health.py", pdir / "inside.py")             # 解析后仍在根内
        return pdir

    def _assert_rejected(self, over, label_prefix):
        for label, val, msg in self.BAD:
            with self.subTest(category=label):
                with tempfile.TemporaryDirectory() as d:
                    pdir = self._build(d, **over(val))
                    _, errors = pl_manifest.load_manifest(pdir)
                    joined = "\n".join(errors)
                    self.assertIn(label_prefix, joined)
                    self.assertIn(msg, joined)

    def test_health_command_four_categories_rejected(self):
        # 首 token = 脚本路径（可带参数）——四类样本均落在脚本路径位
        self._assert_rejected(
            lambda v: {"health": {"type": "command", "command": f"{v} --check"}},
            "health.command")

    def test_entry_cli_four_categories_rejected(self):
        self._assert_rejected(lambda v: {"entry": {"cli": [v]}}, "entry.cli")

    def test_docs_four_categories_rejected(self):
        self._assert_rejected(lambda v: {"docs": v}, "docs")

    def test_valid_relative_and_in_root_symlink_pass(self):
        # 合法相对路径放行：直连根内文件 + 根内符号链接（解析后仍在根内）
        with tempfile.TemporaryDirectory() as d:
            pdir = self._build(
                d, entry={"cli": ["cli.py", "inside.py"]},
                health={"type": "command", "command": "inside.py --flag"},
                docs="README.md")
            data, errors = pl_manifest.load_manifest(pdir)
            self.assertEqual(errors, [])
            self.assertEqual(data["name"], "demo")


# ------------------- A03 追加（工单 A03 残留洞）：manifest 文件本身出根，2026-10-13
@unittest.skipUnless(hasattr(os, "symlink"), "符号链接用例需要 os.symlink")
class ManifestFileEscape(unittest.TestCase):
    """plugin.json|app.json 本身是指向插件根外的符号链接 → load_manifest 拒绝
    （此前字段级校验只认「根内相对路径」，对 manifest 文件自身全盲——gated
    对应用例 tests/safety/test_plugin_paths_safety.py）；指向根内文件的符号
    链接仍放行。插件根相对扫描根的收口（scan_builtin）同批覆盖。"""

    def test_manifest_symlink_outside_root_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            outside = root / "outside"
            outside.mkdir()
            pdir, _ = _make_plugin_dir(root)
            (outside / "plugin.json").write_text(
                (pdir / "plugin.json").read_text(encoding="utf-8"), encoding="utf-8")
            (pdir / "plugin.json").unlink()
            (pdir / "plugin.json").symlink_to(outside / "plugin.json")
            data, errors = pl_manifest.load_manifest(pdir)
            self.assertIsNone(data)
            self.assertTrue(any("插件根外" in e for e in errors), errors)

    def test_manifest_symlink_inside_root_still_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            pdir, _ = _make_plugin_dir(Path(d))
            (pdir / "plugin.json").rename(pdir / "manifest.real.json")
            (pdir / "plugin.json").symlink_to("manifest.real.json")
            data, errors = pl_manifest.load_manifest(pdir)
            self.assertEqual(errors, [])
            self.assertEqual(data["name"], "demo")

    def test_builtin_scan_rejects_dir_symlinked_outside_scan_root(self):
        # apps//plugins/ 内指向扫描根外的目录符号链接不作内置件（errors entry）
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "plugins").mkdir()
            pdir, _ = _make_plugin_dir(root / "outside")   # 根外合法插件实体
            (root / "plugins" / "demo").symlink_to(pdir, target_is_directory=True)
            entries = pl_registry.scan_builtin(root)
            demo = [e for e in entries if e["name"] == "demo"]
            self.assertEqual(len(demo), 1)
            self.assertIsNone(demo[0]["manifest"])
            self.assertTrue(any("扫描根" in e for e in demo[0]["manifest_errors"]),
                            demo[0]["manifest_errors"])
            # Static rejection must also stop the registered runtime bus before
            # package import; a symlink cannot acquire builtin trust implicitly.
            data = json.loads((pdir / "plugin.json").read_text())
            data["extends"] = {"hooks": ["audio_events"],
                               "schema_keys": [{"path": "root", "key": "demo"}]}
            (pdir / "plugin.json").write_text(json.dumps(data))
            (root / "plugins" / "registry.json").write_text(json.dumps({"plugins": {
                "demo": {"installed": True, "enabled": True, "version": "1.0.0",
                         "hooks": ["audio_events"],
                         "keys": [{"path": "root", "key": "demo"}]}}}))
            from types import SimpleNamespace
            with mock.patch.object(pl_registry, "REPO_ROOT", root), \
                    mock.patch.object(pl_hooks.importlib, "import_module") as importer:
                self.assertEqual(pl_registry.plugin_module_dir("demo"), (None, None))
                with self.assertRaisesRegex(RuntimeError, "缺少有效 manifest 导入锚"):
                    pl_hooks.run_audio_events(SimpleNamespace(tl={"demo": True}), [])
                importer.assert_not_called()


# ---------------------------------------------------------------- registry
class RegistryTest(unittest.TestCase):
    """repo 实 registry.json（P2b 起插件实体落地：denoise installed:true）。"""

    def test_static_registry_loads(self):
        reg = pl_registry.load_static_registry(REPO)["plugins"]
        self.assertIn("denoise", reg)
        self.assertIn("duck-static", reg)
        self.assertEqual(reg["denoise"]["keys"],
                         [{"path": "sources[]", "key": "denoise"}])
        self.assertEqual(reg["duck-static"]["keys"],
                         [{"path": "audio_tracks[]", "key": "duck_strategy"}])
        self.assertTrue(reg["denoise"]["installed"])       # P2b 已落地实体
        self.assertEqual(reg["denoise"]["version"], "2.0.0")   # R-20 P2 升级连带随迁(P3:根 tests 版本钉遗漏,越界申报获批,仅字面量)
        self.assertTrue(reg["duck-static"]["installed"])
        self.assertEqual(reg["duck-static"]["version"], "1.0.0")
        self.assertEqual(reg["duck-static"]["hooks"], ["validate", "audio_events"])
        for data in ({}, [], {"plugins": None}, {"plugins": []}):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as d:
                root = Path(d); (root / "plugins").mkdir()
                (root / "plugins" / "registry.json").write_text(json.dumps(data))
                warning = io.StringIO()
                with redirect_stderr(warning):
                    self.assertEqual(pl_registry.load_static_registry(root), {"plugins": {}})
                self.assertIn("结构不符", warning.getvalue())

    def test_lookup_key(self):
        name, ki = pl_registry.lookup_key("denoise", REPO)
        self.assertEqual((name, ki["path"]), ("denoise", "sources[]"))
        name2, ki2 = pl_registry.lookup_key("duck_strategy", REPO)
        self.assertEqual((name2, ki2["path"]), ("duck-static", "audio_tracks[]"))
        self.assertEqual(pl_registry.lookup_key("no_such_key", REPO), (None, None))

    def test_plugin_state_installed_vs_declared(self):
        # P2b 两插件实体落地：registry installed:true 且 plugins/<name>/ 在 → active
        self.assertEqual(pl_registry.plugin_state("denoise", REPO), "active")
        self.assertEqual(pl_registry.plugin_state("duck-static", REPO), "active")
        self.assertEqual(pl_registry.plugin_state("unknown-thing", REPO), "not_installed")

    def test_builtin_scan_finds_valid_apps(self):
        # P2a 起 apps/ 有实体（首个为 cover）——发现件 manifest 必须全部有效
        # （P1-2 时点 apps/ 不存在、扫描为空是合规态；本断言随落地件演进）
        entries = pl_registry.scan_builtin(REPO)
        names = {(e["name"], e["kind"]) for e in entries}
        self.assertIn(("cover", "app"), names)
        for e in entries:
            self.assertEqual(e["manifest_errors"], [], f"{e['name']} manifest 无效")
            self.assertTrue(e["dir"].is_dir())


# ---------------------------------------------------------------- 三分支扫描（纯函数层）
class _FakeCtx:
    """_scan_plugin_keys 只消费 c.tl——免 ffmpeg 的鸭子类型上下文。"""

    def __init__(self, tl):
        self.tl = tl


class ThreeBranchScan(unittest.TestCase):
    def test_declared_not_installed_errors_with_hint(self):
        # P2b 起 denoise 实体落地（registry active）——未装分支改 mock 驱动
        # （真实态的正例由 test_active_branch_passes_silently / CLI 真跑覆盖）
        tl = {"project": {"fps": 30},
              "sources": [{"id": "a", "path": "a.mp4", "denoise": {"strength": 0.8}}],
              "audio_tracks": [{"name": "bed", "duck_strategy": "static", "events": []}]}
        errs, warns = [], []
        with mock.patch("vtx.pluginlib.registry.plugin_state",
                        return_value="not_installed"):
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
            # timeline 无插件键 → 生效清单空 → cache_dirs 白名单零并入（空转不变）；
            # 无参调用（机器态，不过滤 timeline）= 静态注册表全部声明（v1.1.0 起
            # bgm-render 声明 cache_dirs: ["bgm"]——首个使用该通道的官方插件）。
            self.assertEqual(pl_hooks.get_cache_dirs(c), set())
            self.assertEqual(pl_hooks.get_cache_dirs(), {"bgm"})
            # 键级通道（v1.2.0 R1-F F2）同空转：无插件键时间线恒空 dict
            self.assertEqual(pl_hooks.get_cache_dir_keys(c), {})
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
            # 无插件键时间线（生效过滤）→ 缝 A 返回 None（prepare 直抽裸 wav）
            self.assertIsNone(pl_hooks.run_prepare_source(c, "a"))
            self.assertIsNone(pl_hooks.resolve_source_wav(c, "a"))
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

    def test_denoise_key_active_passes_validate(self):
        # 语法差异门真实跑（P2b 后 denoise active）：denoise 键 → validate 全绿
        # 放行（分支 1；未装分支报错正例见 ThreeBranchScan mock 驱动）
        p = self._proj(self.root, denoise={"strength": 0.8})
        r = self._run(p)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("✓ 校验通过", r.stdout)
        self.assertNotIn("⚠", r.stdout)
        self.assertNotIn("未安装", r.stdout)

    def test_duck_strategy_key_active_passes_validate(self):
        # 语法差异门真实跑（P2b 后 duck-static active）：duck_strategy 键 →
        # validate 全绿放行（未装分支报错正例见 ThreeBranchScan mock 驱动）
        p = self._proj(self.root)
        tl = json.loads(p.read_text(encoding="utf-8"))
        tl["audio_tracks"][0]["duck_strategy"] = {"mode": "static"}
        p.write_text(json.dumps(tl), encoding="utf-8")
        r = self._run(p)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("✓ 校验通过", r.stdout)
        self.assertNotIn("⚠", r.stdout)

    def test_duck_mutual_exclusion_and_meta_double_apply_fail(self):
        # validate 钩子（协议 v1.1）真实跑：互斥 / _meta 双施 → exit 1 带指向文案
        p = self._proj(self.root)
        tl = json.loads(p.read_text(encoding="utf-8"))
        tl["audio_tracks"][0]["duck_strategy"] = {"mode": "static"}
        tl["audio_tracks"][0]["duck"] = True
        p.write_text(json.dumps(tl), encoding="utf-8")
        r = self._run(p)
        self.assertEqual(r.returncode, 1)
        self.assertIn("互斥", r.stdout)
        p2 = self._proj(self.root)
        tl2 = json.loads(p2.read_text(encoding="utf-8"))
        tl2["_meta"] = {"bed_static_duck": {"script": "scripts/bed_static_duck.py"}}
        tl2["audio_tracks"][0]["duck_strategy"] = {"mode": "static"}
        p2.write_text(json.dumps(tl2), encoding="utf-8")
        r2 = self._run(p2)
        self.assertEqual(r2.returncode, 1)
        self.assertIn("_meta.bed_static_duck", r2.stdout)
        self.assertIn("pre-duck", r2.stdout)

    def test_both_plugin_keys_active_passes_validate(self):
        # 语法差异门预演（真实跑）：denoise + duck_strategy 键同 timeline、
        # 两插件 active → validate 全绿放行、零 ⚠ 零提示（三分支分支 1）
        p = self._proj(self.root, denoise=True)
        tl = json.loads(p.read_text(encoding="utf-8"))
        tl["audio_tracks"].append(
            {"name": "bed", "duck_strategy": {"mode": "static"},
             "events": [{"src": "a", "in": 0, "out": 60, "volume": 0.5}]})
        p.write_text(json.dumps(tl), encoding="utf-8")
        r = self._run(p)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("✓ 校验通过：1 源 / 1 视频轨 1 clips / 0 字幕 / 2 音轨 2 事件", r.stdout)
        self.assertNotIn("⚠", r.stdout)
        self.assertNotIn("未安装", r.stdout)
        self.assertNotIn("禁用", r.stdout)

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
    """P1 对抗审查 F3：假插件模块验证总线正例（受理短路 / 链式变换 / 键并集）
    与加载失败 fail-hard（F4）+ hooks 声明↔注册对账（§2.3，P2b 起）。"""

    def setUp(self):
        import types
        pkg = types.ModuleType("plugins")
        mod = types.ModuleType("plugins.fakeplug")

        def prepare_source(c, sid):          # 契约 v1.1：(c, sid) -> Path | None
            return Path(c.cache) / "fake.wav" if sid == "src1" else None

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
        self._manifest_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._manifest_tmp.cleanup)
        fixture_dir, _ = _make_plugin_dir(Path(self._manifest_tmp.name))
        self._patches = [
            mock.patch("vtx.pluginlib.registry.plugin_module_dir",
                       return_value=(fixture_dir, "builtin")),
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
            self.assertEqual(pl_hooks.run_prepare_source(c, "src1"),
                             Path(c.cache) / "fake.wav")
            self.assertIsNone(pl_hooks.run_prepare_source(c, "src2"))
            # 读取点同一解析（graph 与 prepare 恒一致）
            self.assertEqual(pl_hooks.resolve_source_wav(c, "src1"),
                             Path(c.cache) / "fake.wav")

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
                    pl_hooks.run_prepare_source(c, "src1")

    def test_hooks_declared_not_registered_rejected(self):
        """§2.3 单一事实源对账：声明了没注册 / 注册了没声明都报错。"""
        import types
        # 声明了 prepare_source 但模块只注册 audio_events
        mod = types.ModuleType("plugins.fakeplug2")
        mod.audio_events = lambda c, tracks: tracks
        sys.modules["plugins.fakeplug2"] = mod
        reg = {"fakeplug2": {"version": "0.1.0", "hooks": ["prepare_source"]}}
        self.addCleanup(sys.modules.pop, "plugins.fakeplug2", None)
        with mock.patch("vtx.pluginlib.registry.declared_plugins",
                        return_value=reg), \
             mock.patch("vtx.pluginlib.registry.plugin_state",
                        return_value="active"):
            with self.assertRaises(RuntimeError) as cm:
                pl_hooks._load_plugin_module("fakeplug2")
        self.assertIn("声明了没注册", str(cm.exception))

    def test_hooks_registered_not_declared_rejected(self):
        import types
        mod = types.ModuleType("plugins.fakeplug3")
        mod.prepare_source = lambda c, sid: None
        mod.audio_events = lambda c, tracks: tracks   # 注册了但 registry 未声明
        sys.modules["plugins.fakeplug3"] = mod
        reg = {"fakeplug3": {"version": "0.1.0", "hooks": ["prepare_source"]}}
        self.addCleanup(sys.modules.pop, "plugins.fakeplug3", None)
        with mock.patch("vtx.pluginlib.registry.declared_plugins",
                        return_value=reg), \
             mock.patch("vtx.pluginlib.registry.plugin_state",
                        return_value="active"):
            with self.assertRaises(RuntimeError) as cm:
                pl_hooks._load_plugin_module("fakeplug3")
        self.assertIn("注册了没声明", str(cm.exception))

    def test_plugin_code_change_flips_digest(self):
        """红队终审 P1-1 回归：插件代码内容变（不 bump version / 不改键值）
        → 摘要必变——改 DEFAULTS 盲区与无键插件盲区的共同兜底。"""
        with tempfile.TemporaryDirectory() as d:
            import shutil as _sh
            root = Path(d)
            src = Path("plugins/duck-static/__init__.py").read_text(encoding="utf-8")
            fake = root / "duck-static"
            fake.mkdir()
            (fake / "__init__.py").write_text(src, encoding="utf-8")
            c = Ctx(_tl(root))
            with mock.patch("vtx.pluginlib.registry.plugin_module_dir",
                            return_value=(fake, "builtin")):
                d1 = pl_hooks.plugin_code_digest("duck-static")
                d2 = pl_hooks.plugin_code_digest("duck-static")
                (fake / "__init__.py").write_text(
                    src + "\n# 改默认参（不 bump version 的代码变更）\n", encoding="utf-8")
                d3 = pl_hooks.plugin_code_digest("duck-static")
            self.assertEqual(d1, d2)      # 同码同摘要（幂等）
            self.assertNotEqual(d2, d3)   # 改 DEFAULTS → 摘要变

    def test_plugin_code_digest_excludes_tests_dir(self):
        """R1-F F5：tests/ 子目录不进代码摘要——改一行测试 ≠ 插件行为变化，
        掺入会使产物键/回执无谓翻转（真工程整轮重渲）；本体代码变化仍须翻转。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fake = root / "duck-static"
            fake.mkdir()
            (fake / "__init__.py").write_text("X = 1\n", encoding="utf-8")
            (fake / "tests").mkdir()
            (fake / "tests" / "test_x.py").write_text("# v1\n", encoding="utf-8")
            with mock.patch("vtx.pluginlib.registry.plugin_module_dir",
                            return_value=(fake, "builtin")):
                d1 = pl_hooks.plugin_code_digest("duck-static")
                (fake / "tests" / "test_x.py").write_text(
                    "# v2 改一行测试（不 bump version 不改行为）\n", encoding="utf-8")
                (fake / "tests" / "test_new.py").write_text("# 新增测试文件\n",
                                                            encoding="utf-8")
                d2 = pl_hooks.plugin_code_digest("duck-static")
                (fake / "__init__.py").write_text("X = 2\n", encoding="utf-8")
                d3 = pl_hooks.plugin_code_digest("duck-static")
            self.assertEqual(d1, d2, "tests/ 内容变化不得翻转代码摘要")
            self.assertNotEqual(d2, d3, "插件本体代码变化仍须翻转摘要")

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
