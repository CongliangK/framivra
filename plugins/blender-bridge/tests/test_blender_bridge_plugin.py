#!/usr/bin/env python3
"""tests/test_blender_bridge_plugin.py —— blender-bridge 插件（缝 D finish + validate）测试。

覆盖：
  ① validate 钩子：true / 合法对象 / 非法值（字符串、未知参数、out 绝对路径与
     ..、verify 非布尔）逐形状判；
  ② finish 接线：伪 blender 桩（PATH 前置可执行脚本）验证「键声明 → all 收尾
     自动导出」的子进程命令组装与产物断言；verify=true 追加校验次调用；
  ③ fail-hard：blender 不在 PATH → 错误行明确指向安装；
  ④ 无键恒等：timeline 无 blender_bridge 键 → validate/finish 全空（零行为）；
  ⑤ out 越出项目根拒绝；
  ⑥ 三分支（真 registry）：未装（mock plugin_state）→ validate 报「未安装」；
  ⑦ 缺陷2修复：DeprecationWarning 滤除（_filter_deprecation 纯函数 +
     _error_tail 回落 + _run_blender_script 集成——样本含 Blender 5.2.1 实测
     告警块/traceback/真实错误三态，退出码语义断言不变）；
  ⑧ subs_alpha 缓存：ASS 内容+project 键、ffprobe 有效性、坏 mov 重建、
     缺/坏 sidecar 重建、无字幕零行为、缺 ass 指引、生成后校验与键写入；
  ⑨ timeline symlink 保持自身项目 cache；真实错误文本含弃用告警同名词不吞。
运行：repo 根 `bash scripts/run_all_tests.sh`；本插件内独立跑：
cd plugins/blender-bridge && env python3 -m unittest discover -s tests -t . -v
（本机 ZCode 环境必须 env python3 前缀：AppImage 劫持 argv[0]，见全局 AGENTS.md）。
"""
import importlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[3]   # plugins/blender-bridge/tests → repo 根
sys.path.insert(0, str(REPO))

_bb = importlib.import_module("plugins.blender-bridge")
_health = importlib.import_module("plugins.blender-bridge.health_check")

from vtx.pluginlib import hooks as pl_hooks   # noqa: E402


class _FakeCtx:
    """validate/finish 只消费 c.tl / c.root / c.tl_path——鸭子类型上下文。"""

    def __init__(self, tl, root):
        self.tl = tl
        self.root = root
        self.tl_path = root / "timeline.json"


def _tl(root: Path, key_value=...):
    tl = {"version": 2,
          "project": {"fps": 30, "duration_frames": 90, "width": 640, "height": 360},
          "sources": [], "video_tracks": [], "subtitles": [], "audio_tracks": []}
    if key_value is not ...:
        tl["blender_bridge"] = key_value
    p = root / "timeline.json"
    p.write_text(json.dumps(tl), encoding="utf-8")
    return tl


def _make_fake_blender(d: Path, calls_file: Path):
    """伪 blender：调用记入 calls_file；对末参（json2blend 的 out.blend /
    verify_blend 的 blend 路径）写入内容模拟产物落盘。"""
    fake = d / "blender"
    fake.write_text(
        "#!/bin/bash\n"
        f"echo \"$@\" >> {calls_file}\n"
        "out=\"${@: -1}\"\n"
        "echo \"FAKE BLEND\" > \"$out\"\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake


class ValidateShape(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_key_is_noop(self):
        c = _FakeCtx(_tl(self.root), self.root)
        self.assertEqual(_bb.validate(c), [])
        self.assertEqual(_bb.finish(c), [])

    def test_true_passes(self):
        c = _FakeCtx(_tl(self.root, True), self.root)
        self.assertEqual(_bb.validate(c), [])

    def test_bad_shapes_fail(self):
        for bad in ("yes", 42, [], {"out": 1}, {"unknown": 1}, {"verify": "y"},
                    {"out": "/abs/preview.blend"}, {"out": "../esc.blend"}):
            c = _FakeCtx(_tl(self.root, bad), self.root)
            errs = _bb.validate(c)
            self.assertTrue(errs, f"期望形状报错：{bad!r}")

    def test_good_object_passes(self):
        c = _FakeCtx(_tl(self.root, {"out": "preview.blend", "verify": True}), self.root)
        self.assertEqual(_bb.validate(c), [])


class FinishWiring(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.bindir = self.root / "bin"
        self.bindir.mkdir()
        self.calls = self.root / "calls.log"
        self._old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bindir}:{self._old_path}"
        # json2blend/verify_blend 桩：模块级常量 mock（不动盘上仓件）——伪 blender
        # 只按末参落盘，桩文件只需在盘（内容不被执行）
        self._stub = _make_fake_blender(self.bindir, self.calls)
        self._j2b = _bb._JSON2BLEND
        self._ver = _bb._VERIFY
        _bb._JSON2BLEND = self.bindir / "json2blend_stub.py"
        _bb._VERIFY = self.bindir / "verify_stub.py"
        _bb._JSON2BLEND.write_text("# stub\n", encoding="utf-8")
        _bb._VERIFY.write_text("# stub\n", encoding="utf-8")

    def tearDown(self):
        os.environ["PATH"] = self._old_path
        _bb._JSON2BLEND = self._j2b
        _bb._VERIFY = self._ver
        self._tmp.cleanup()

    def test_finish_exports_blend(self):
        c = _FakeCtx(_tl(self.root, True), self.root)
        self.assertEqual(_bb.finish(c), [])
        out = self.root / "preview.blend"
        self.assertTrue(out.is_file(), "伪 blender 应已产出 .blend")
        self.assertIn("--factory-startup", self.calls.read_text())

    def test_finish_verify_flag_appends_second_call(self):
        c = _FakeCtx(_tl(self.root, {"verify": True}), self.root)
        self.assertEqual(_bb.finish(c), [])
        self.assertEqual(len(self.calls.read_text().splitlines()), 2,
                         "verify=true 应追加第二次 blender 调用")

    def test_finish_custom_out(self):
        c = _FakeCtx(_tl(self.root, {"out": "audit/ep42.blend"}), self.root)
        self.assertEqual(_bb.finish(c), [])
        self.assertTrue((self.root / "audit" / "ep42.blend").is_file())

    def test_finish_escape_rejected(self):
        c = _FakeCtx(_tl(self.root, {"out": "../outside.blend"}), self.root)
        errs = _bb.finish(c)
        self.assertTrue(errs and "越出项目根" in errs[0])

    def test_finish_no_blender_fails_hard(self):
        os.environ["PATH"] = str(self.root / "nonexistent")
        c = _FakeCtx(_tl(self.root, True), self.root)
        errs = _bb.finish(c)
        self.assertTrue(errs and "blender 不在 PATH" in errs[0])
        with mock.patch.object(_health.subprocess, "run") as run:
            self.assertEqual(_health.main(), 1)
        run.assert_not_called()

    def test_health_checks_only_version_and_reports_probe_failures(self):
        for output, code, expected in (("Blender 4.3.2\n", 0, 1),
                ("Blender 4.4.0\n", 0, 0), ("Blender 5.2.1 LTS\n", 0, 0),
                ("unrecognized", 0, 1), ("Blender 5.2.1\n", 1, 1),
                (subprocess.TimeoutExpired(["blender", "--version"], 5), 0, 1),
                (OSError("cannot execute"), 0, 1)):
            with self.subTest(output=output, code=code), \
                 mock.patch.object(_health.shutil, "which", return_value="/blender"), \
                 mock.patch.object(_health.subprocess, "run",
                    return_value=subprocess.CompletedProcess([], code, output, ""),
                    side_effect=output if isinstance(output, Exception) else None) as run, \
                 mock.patch("builtins.print") as printed:
                self.assertEqual(_health.main(), expected)
                self.assertEqual(run.call_args.args[0], ["/blender", "--version"])
                self.assertEqual(run.call_args.kwargs["timeout"], 5)
                text = "\n".join(str(call.args[0]) for call in printed.call_args_list)
                self.assertIn("MISS" if expected else "仅版本前置满足；运行时 API 未验证", text)


class BusIntegration(unittest.TestCase):
    """经 hooks 总线（真 registry active）跑 validate/finish 分发。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_run_validate_dispatch(self):
        c = _FakeCtx(_tl(self.root, "bad-shape"), self.root)
        errs = pl_hooks.run_validate(c)
        self.assertTrue(any("blender_bridge" in e for e in errs))

    def test_run_finish_dispatch_noop_without_key(self):
        c = _FakeCtx(_tl(self.root), self.root)
        self.assertEqual(pl_hooks.run_finish(c), [])

    def test_three_branch_not_installed(self):
        # 键未装 → validate FAIL（三分支：报「未安装」+ 指引）
        from vtx.pipeline.stages import _scan_plugin_keys
        c = _FakeCtx(_tl(self.root, True), self.root)
        errs = []
        with mock.patch("vtx.pluginlib.registry.plugin_state",
                        return_value="not_installed"):
            _scan_plugin_keys(c, errs)
        self.assertTrue(any("未安装" in e for e in errs))


class DeprecationFilter(unittest.TestCase):
    """缺陷2修复：stderr DeprecationWarning 滤除（_filter_deprecation/_error_tail）。"""

    def test_filter_drops_warning_blocks_keeps_errors(self):
        # 样本取自 Blender 5.2.1 实测输出（json2blend 失败场景）：
        # 告警行+缩进上下文行 ×2 块 + 真实 Traceback + 异常行
        sample = [
            "/p/json2blend.py:174: DeprecationWarning: 'ImageStrip.frame_final_start'"
            " is expected to be removed in Blender 6.0",
            "  strip.frame_final_start = clip[\"dst_in\"]",
            "/p/json2blend.py:175: DeprecationWarning: 'ImageStrip.frame_final_end'"
            " is expected to be removed in Blender 6.0",
            "  strip.frame_final_end = clip[\"dst_in\"] + clip[\"dur\"]",
            "Traceback (most recent call last):",
            '  File "/p/json2blend.py", line 286, in <module>',
            "    sys.exit(1)",
            "SystemExit: 1",
        ]
        kept, n = _bb._filter_deprecation(sample)
        self.assertEqual(kept, [
            "Traceback (most recent call last):",
            '  File "/p/json2blend.py", line 286, in <module>',
            "    sys.exit(1)",
            "SystemExit: 1",
        ])
        self.assertEqual(n, 4, "告警行+上下文行各 2 行应全部计入滤除数")

    def test_filter_multiline_warning_context(self):
        # 多行上下文（缩进连续行）整体吞；空行/非缩进行终止吞并
        sample = [
            "/p/x.py:9: DeprecationWarning: w",
            "  line a",
            "  line b",
            "",
            "real error here",
        ]
        kept, n = _bb._filter_deprecation(sample)
        self.assertEqual(kept, ["", "real error here"])
        self.assertEqual(n, 3)

    def test_filter_noop_without_warnings(self):
        sample = ["Traceback (most recent call last):", "ValueError: x"]
        self.assertEqual(_bb._filter_deprecation(sample), (sample, 0))

    def test_error_tail_falls_back_to_stdout_when_stderr_pure_warnings(self):
        # json2blend 真实 ！错误行在 stdout；旧 `stderr or stdout` 一刀切会淹死
        stderr = ("/p/j.py:8: DeprecationWarning: 'SoundStrip.frame_final_end'"
                  " is expected to be removed in Blender 6.0\n"
                  "  st.frame_final_end = min(t_out, t_in)\n")
        stdout = ("Blender 5.2.1 LTS (hash 9e2066aef7ef built x)\n"
                  "Info: Saved as \"out.blend\"\n"
                  "[json2blend] 错误 1 条:\n"
                  "  ! 缺少字幕透明轨 /p/cache/subs_alpha.mov（先跑 subs_alpha.py）\n"
                  "Blender quit\n")
        text, n = _bb._error_tail(stderr, stdout)
        self.assertIn("缺少字幕透明轨", text)
        self.assertNotIn("DeprecationWarning", text)
        self.assertNotIn("Blender quit", text)
        self.assertNotIn("(hash", text, "版本横幅不应挤占尾行")
        self.assertEqual(n, 2)

    def test_error_tail_prefers_real_stderr(self):
        # stderr 带真实 traceback（前置告警块已滤）→ 取 stderr 尾行
        stderr = ("/p/j.py:8: DeprecationWarning: w\n  ctx\n"
                  "Traceback (most recent call last):\nRuntimeError: 炸了\n")
        text, n = _bb._error_tail(stderr, "ignored stdout")
        self.assertIn("RuntimeError: 炸了", text)
        self.assertNotIn("DeprecationWarning", text)
        self.assertEqual(n, 2)

    def test_traceback_source_and_exception_mentions_are_not_warning_blocks(self):
        sample = [
            "Traceback (most recent call last):",
            '  File "/p/j.py", line 8, in <module>',
            '    raise RuntimeError("真实原因 DeprecationWarning 不得消失")',
            "RuntimeError: 真实原因 DeprecationWarning 不得消失",
        ]
        self.assertEqual(_bb._filter_deprecation(sample), (sample, 0))
        text, n = _bb._error_tail("\n".join(sample), "")
        self.assertIn(sample[2].strip(), text)
        self.assertIn(sample[3], text)
        self.assertEqual(n, 0)

    def test_warning_block_allows_spaces_in_python_path(self):
        lines = ["/project with spaces/j.py:9: DeprecationWarning: old API",
                 "  old_api()", "RuntimeError: DeprecationWarning text is a real error"]
        self.assertEqual(_bb._filter_deprecation(lines), ([lines[-1]], 2))

    def test_exception_message_containing_warning_shaped_text_is_preserved(self):
        lines = ['RuntimeError: /p/j.py:9: DeprecationWarning: true failure',
                 '    raise RuntimeError("/p/j.py:9: DeprecationWarning: true failure")']
        self.assertEqual(_bb._filter_deprecation(lines), (lines, 0))
        warning = ['C:\\project with spaces\\j.py:9: DeprecationWarning: old API',
                   '  old_api()']
        self.assertEqual(_bb._filter_deprecation(warning), ([], 2))

    def test_userwarning_cannot_hide_json2blend_stdout_error(self):
        stderr = "/p/j.py:9: UserWarning: noisy API\n  noisy()\n"
        stdout = ("[json2blend] 错误 1 条:\n  ! 真正的失败原因\n"
                  + "stdout noise\n" * 10)
        text, n = _bb._error_tail(stderr, stdout)
        self.assertTrue(text.startswith("[json2blend] 错误 1 条: | ! 真正的失败原因"), text)
        self.assertIn("UserWarning", text)
        self.assertEqual(n, 0)

    def test_both_streams_count_warnings_and_prioritize_error_markers(self):
        stderr = ("/p/j.py:9: DeprecationWarning: old\n  old()\n"
                  "[json2blend] ! stderr real error\n" + "noise\n" * 5)
        stdout = ("/p/j.py:10: DeprecationWarning: old\n  old()\n"
                  "[json2blend] 错误 1 条:\n  ! stdout real error\n" + "noise\n" * 5)
        text, n = _bb._error_tail(stderr, stdout)
        self.assertTrue(text.startswith("[json2blend] ! stderr real error"))
        self.assertIn("! stdout real error", text)
        self.assertEqual(n, 4)

    def test_run_blender_script_filters_and_keeps_exit_code(self):
        # 退出码语义不变（exit 码原样进消息），告警滤除聚合为一行计数
        done = subprocess.CompletedProcess(
            [], 1,
            stdout="[json2blend] 错误 1 条:\n  ! 视频源缺失 x: y -> z\n",
            stderr=("/p/j.py:174: DeprecationWarning: 'ImageStrip.frame_final_end'"
                    " is expected to be removed in Blender 6.0\n"
                    "  strip.frame_final_end = 1\n"))
        with mock.patch.object(_bb.subprocess, "run", return_value=done):
            cmd, err = _bb._run_blender_script("blender", "s.py", ["a"], "json2blend 导出")
        self.assertIn("exit 1", err)
        self.assertIn("视频源缺失", err)
        self.assertNotIn("DeprecationWarning:", err)
        self.assertIn("已滤除 2 行 Blender DeprecationWarning", err)
        self.assertEqual(cmd[:2], ["blender", "--background"])


class SubsAlphaAutogen(unittest.TestCase):
    """缺陷1修复：subs_alpha.mov 缺失检测 + 自动生成（_ensure_subs_alpha，mock 子进程）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self._sa = _bb._SUBS_ALPHA

    def tearDown(self):
        _bb._SUBS_ALPHA = self._sa
        self._tmp.cleanup()

    def _ctx(self, subtitles):
        tl = {"project": {"fps": 30, "duration_frames": 90, "width": 640, "height": 360},
              "sources": [], "video_tracks": [], "subtitles": subtitles,
              "audio_tracks": [], "blender_bridge": True}
        return _FakeCtx(tl, self.root)

    def test_no_subtitles_noop(self):
        c = self._ctx([])
        with mock.patch.object(_bb.subprocess, "run") as run:
            self.assertEqual(_bb._ensure_subs_alpha(c), [])
            run.assert_not_called()

    def _seed_cache(self, c):
        ass = self.cache / "subs.ass"
        ass.write_text("[Script Info]\nStyle: original\nDialogue: original", encoding="utf-8")
        (self.cache / "subs_alpha.mov").write_bytes(b"PROBEABLE_MOV")
        key = _bb._subs_alpha_key(ass, c.tl["project"])
        (self.cache / "subs_alpha.key.json").write_text(json.dumps(key), encoding="utf-8")
        return key

    def _fake_run(self, cmd, **kwargs):
        if cmd[0] == "ffprobe":
            valid = Path(cmd[-1]).read_bytes() == b"PROBEABLE_MOV"
            return subprocess.CompletedProcess(cmd, 0 if valid else 1,
                stdout=json.dumps({"streams": [{"codec_type": "video", "duration": "3"}],
                                   "format": {"duration": "3"}}) if valid else "{}",
                stderr="" if valid else "moov atom not found")
        (Path(cmd[2]).parent / "cache" / "subs_alpha.mov").write_bytes(b"PROBEABLE_MOV")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def test_existing_valid_mov_matching_key_idempotent_skip(self):
        c = self._ctx([{"in": 0, "out": 30, "text": "t"}])
        self._seed_cache(c)
        with mock.patch.object(_bb.subprocess, "run", side_effect=self._fake_run) as run:
            self.assertEqual(_bb._ensure_subs_alpha(c), [])
            run.assert_called_once()
            self.assertEqual(run.call_args[0][0][0], "ffprobe", "缓存命中也须验证媒体")

    def test_ass_text_or_style_change_regenerates_and_updates_key(self):
        for change in ("Dialogue: changed text", "Style: changed font"):
            with self.subTest(change=change):
                c = self._ctx([{"in": 0, "out": 30, "text": "t"}])
                old_key = self._seed_cache(c)
                (self.cache / "subs.ass").write_text(change, encoding="utf-8")
                with mock.patch.object(_bb.subprocess, "run", side_effect=self._fake_run) as run:
                    self.assertEqual(_bb._ensure_subs_alpha(c), [])
                    self.assertEqual(run.call_args_list[0][0][0][0], sys.executable)
                new_key = json.loads((self.cache / "subs_alpha.key.json").read_text())
                self.assertNotEqual(new_key, old_key)
                self.assertEqual(new_key, _bb._subs_alpha_key(self.cache / "subs.ass", c.tl["project"]))

    def test_each_project_parameter_change_regenerates(self):
        for field in ("width", "height", "fps", "duration_frames"):
            with self.subTest(field=field):
                c = self._ctx([{"in": 0, "out": 30, "text": "t"}])
                old_key = self._seed_cache(c)
                c.tl["project"][field] += 1
                with mock.patch.object(_bb.subprocess, "run", side_effect=self._fake_run) as run:
                    self.assertEqual(_bb._ensure_subs_alpha(c), [])
                    self.assertEqual(run.call_args_list[0][0][0][0], sys.executable)
                self.assertNotEqual(json.loads((self.cache / "subs_alpha.key.json").read_text()), old_key)

    def test_bad_mov_matching_key_regenerates(self):
        c = self._ctx([{"in": 0, "out": 30, "text": "t"}])
        self._seed_cache(c)
        (self.cache / "subs_alpha.mov").write_bytes(b"garbage" * 146)
        with mock.patch.object(_bb.subprocess, "run", side_effect=self._fake_run) as run:
            self.assertEqual(_bb._ensure_subs_alpha(c), [])
        self.assertEqual([call[0][0][0] for call in run.call_args_list],
                         ["ffprobe", sys.executable, "ffprobe"])
        self.assertEqual((self.cache / "subs_alpha.mov").read_bytes(), b"PROBEABLE_MOV")

    def test_missing_or_invalid_sidecar_regenerates(self):
        for sidecar in (None, "not json", "{}"):
            with self.subTest(sidecar=sidecar):
                c = self._ctx([{"in": 0, "out": 30, "text": "t"}])
                self._seed_cache(c)
                path = self.cache / "subs_alpha.key.json"
                if sidecar is None:
                    self.assertEqual(path.absolute(), self.cache / "subs_alpha.key.json")
                    path.unlink()
                else:
                    path.write_text(sidecar, encoding="utf-8")
                with mock.patch.object(_bb.subprocess, "run", side_effect=self._fake_run) as run:
                    self.assertEqual(_bb._ensure_subs_alpha(c), [])
                    self.assertEqual(run.call_args_list[0][0][0][0], sys.executable)

    def test_timeline_symlink_uses_own_project_cache_and_argv(self):
        a = self.root / "A"
        a.mkdir()
        _tl(a)
        timeline = self.root / "timeline.json"
        timeline.symlink_to(a / "timeline.json")
        c = self._ctx([{"in": 0, "out": 30, "text": "B"}])
        (self.cache / "subs.ass").write_text("B project ASS", encoding="utf-8")
        with mock.patch.object(_bb.subprocess, "run", side_effect=self._fake_run) as run:
            self.assertEqual(_bb._ensure_subs_alpha(c), [])
        self.assertEqual(Path(run.call_args_list[0][0][0][2]), timeline.absolute())
        self.assertTrue((self.cache / "subs_alpha.mov").is_file())
        self.assertFalse((a / "cache").exists())

    def test_missing_ass_gives_guidance_no_subprocess(self):
        c = self._ctx([{"in": 0, "out": 30, "text": "t"}])
        with mock.patch.object(_bb.subprocess, "run") as run:
            errs = _bb._ensure_subs_alpha(c)
            run.assert_not_called()
        self.assertEqual(len(errs), 1)
        self.assertIn("subs.ass", errs[0])
        self.assertIn("subs render", errs[0], "指引应给出 vtx 字幕阶段命令")

    def test_missing_script_reported(self):
        (self.cache / "subs.ass").write_text("[Script Info]", encoding="utf-8")
        _bb._SUBS_ALPHA = self.root / "nonexistent_subs_alpha.py"
        c = self._ctx([{"in": 0, "out": 30, "text": "t"}])
        errs = _bb._ensure_subs_alpha(c)
        self.assertEqual(len(errs), 1)
        self.assertIn("subs_alpha 脚本缺失", errs[0])

    def test_autogen_success_invokes_pure_python_script(self):
        (self.cache / "subs.ass").write_text("[Script Info]", encoding="utf-8")
        mov = self.cache / "subs_alpha.mov"

        c = self._ctx([{"in": 0, "out": 30, "text": "t"}])
        with mock.patch.object(_bb.subprocess, "run", side_effect=self._fake_run) as run:
            self.assertEqual(_bb._ensure_subs_alpha(c), [])
            self.assertEqual(run.call_count, 2, "生成后须再 ffprobe 验证")
            cmd = run.call_args_list[0][0][0]
        self.assertEqual(cmd[0], sys.executable, "纯 python3 脚本用当前解释器调（无 bpy）")
        self.assertEqual(Path(cmd[1]), _bb._SUBS_ALPHA, "路径由 __file__ 推导，独立于 cwd")
        self.assertEqual(Path(cmd[2]).resolve(), (self.root / "timeline.json").resolve())
        self.assertTrue(mov.is_file())

    def test_autogen_failure_reports_full_command_and_reason(self):
        (self.cache / "subs.ass").write_text("[Script Info]", encoding="utf-8")
        done = subprocess.CompletedProcess([], 1, stdout="", stderr="ffmpeg 缺失：No such file")
        c = self._ctx([{"in": 0, "out": 30, "text": "t"}])
        with mock.patch.object(_bb.subprocess, "run", return_value=done):
            errs = _bb._ensure_subs_alpha(c)
        self.assertEqual(len(errs), 1)
        self.assertIn("自动生成失败", errs[0])
        self.assertIn("ffmpeg 缺失", errs[0], "失败原因要可见")
        self.assertIn("subs_alpha.py", errs[0], "错误信息含完整命令")
        self.assertIn(sys.executable, errs[0])

    def test_autogen_zero_exit_but_bad_movie_does_not_write_key(self):
        (self.cache / "subs.ass").write_text("[Script Info]", encoding="utf-8")
        c = self._ctx([{"in": 0, "out": 30, "text": "t"}])

        def bad_generation(cmd, **kwargs):
            if cmd[0] == "ffprobe":
                return subprocess.CompletedProcess(cmd, 1, "", "moov atom not found")
            (self.cache / "subs_alpha.mov").write_bytes(b"truncated")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch.object(_bb.subprocess, "run", side_effect=bad_generation):
            errs = _bb._ensure_subs_alpha(c)
        self.assertEqual(len(errs), 1)
        self.assertIn("ffprobe", errs[0])
        self.assertFalse((self.cache / "subs_alpha.key.json").exists())

    def test_autogen_zero_exit_but_no_artifact_fails(self):
        # 子进程 exit 0 但产物未落盘 → 不吞，按失败报告
        (self.cache / "subs.ass").write_text("[Script Info]", encoding="utf-8")
        done = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        c = self._ctx([{"in": 0, "out": 30, "text": "t"}])
        with mock.patch.object(_bb.subprocess, "run", return_value=done):
            errs = _bb._ensure_subs_alpha(c)
        self.assertEqual(len(errs), 1)
        self.assertIn("未落盘", errs[0])


class SubsAlphaProbe(unittest.TestCase):
    def test_probe_requires_readable_video_and_positive_finite_duration(self):
        samples = [
            ({"streams": [{"codec_type": "video", "duration": "1"}]}, True),
            ({"streams": [{"codec_type": "video"}], "format": {"duration": "2"}}, True),
            ({"streams": [{"codec_type": "audio"}], "format": {"duration": "2"}}, False),
            ({"streams": [{"codec_type": "video", "duration": "0"}]}, False),
            ({"streams": [{"codec_type": "video", "duration": "nan"}]}, False),
            ({"streams": [{"codec_type": "video", "duration": "inf"}]}, False),
            ({"streams": [{"codec_type": "video", "duration": "N/A"}]}, False),
            ({"streams": []}, False),
        ]
        with tempfile.TemporaryDirectory() as d:
            mov = Path(d) / "subs_alpha.mov"
            mov.touch()
            for info, expected in samples:
                with self.subTest(info=info), mock.patch.object(_bb.subprocess, "run",
                        return_value=subprocess.CompletedProcess([], 0, json.dumps(info), "")):
                    self.assertEqual(_bb._valid_subs_alpha(mov), expected)
            for result in (subprocess.CompletedProcess([], 1, "{}", "moov atom not found"),
                           subprocess.CompletedProcess([], 0, "invalid json", "")):
                with mock.patch.object(_bb.subprocess, "run", return_value=result):
                    self.assertFalse(_bb._valid_subs_alpha(mov))
            for exc in (FileNotFoundError("ffprobe"), subprocess.TimeoutExpired("ffprobe", 15)):
                with mock.patch.object(_bb.subprocess, "run", side_effect=exc):
                    self.assertFalse(_bb._valid_subs_alpha(mov))

    @unittest.skipUnless(__import__("shutil").which("ffprobe"), "ffprobe required")
    def test_real_ffprobe_rejects_truncated_garbage(self):
        with tempfile.TemporaryDirectory() as d:
            mov = Path(d) / "subs_alpha.mov"
            mov.write_bytes(b"x" * 1024)
            self.assertFalse(_bb._valid_subs_alpha(mov))


if __name__ == "__main__":
    unittest.main()
