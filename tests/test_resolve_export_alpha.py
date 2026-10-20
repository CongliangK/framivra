"""Ordinary fake-Resolve checks: no Resolve, GPU, or media process is launched."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "apps/matting/resolve_matte/export_alpha.py"


def load_export(path=SCRIPT):
    spec = importlib.util.spec_from_file_location("resolve_export_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StubExport:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.resolve = mock.Mock()
        self.project = mock.Mock()
        self.timeline = mock.Mock()
        self.project.GetCurrentTimeline.return_value = self.timeline
        self.timeline.GetName.return_value = "grid9"
        self.timeline.GetUniqueId.return_value = "grid9-id"
        self.timeline.GetTrackCount.return_value = 9
        self.timeline.GetItemsInTrack.side_effect = self.items
        self.timeline.SetTrackEnable.return_value = True
        self.project.SetCurrentRenderFormatAndCodec.return_value = True
        self.project.SetRenderSettings.side_effect = self.settings
        self.project.AddRenderJob.side_effect = lambda: f"job-{self.track}"
        self.project.StartRendering.side_effect = self.render
        self.project.IsRenderingInProgress.return_value = False
        self.project.GetRenderJobStatus.return_value = {
            "JobStatus": "Complete", "CompletionPercentage": 100}
        self.project.DeleteRenderJob.return_value = True
        self.resolve.OpenPage.return_value = True
        self.track = None
        self.render_settings = []
        self.missing = set()
        self.probe_frames = {}
        self.command_failures = {}
        self.tail_values = [128]
        self.no_metadata = False
        self.controller = types.ModuleType("resolve_ctl")
        self.controller.get_resolve = lambda: self.resolve
        self.controller.ensure_project = lambda *_: self.project

    def items(self, kind, track):
        item = mock.Mock()
        item.GetName.return_value = f"clip-{track}"
        item.GetStart.return_value = 0
        item.GetEnd.return_value = 10 + track
        return {1: item}

    def settings(self, settings):
        self.render_settings.append(settings)
        self.track = int(settings["CustomName"].removeprefix("matte_V"))
        return True

    def render(self, job):
        if self.track not in self.missing:
            (self.directory / f"matte_V{self.track}.mov").write_bytes(b"x")
        return True

    def command(self, args, **kwargs):
        if args[0] == "ffprobe":
            stage = "probe"
            track = int(Path(args[-1]).stem.removeprefix("matte_V"))
            stdout = f"yuva444p12le,{self.probe_frames.get(track, 10 + track)}\n"
            stderr = ""
        else:
            stage = "tail" if "-sseof" in args else "decode"
            self.assert_alpha_command(args)
            stdout = ""
            values = self.tail_values if stage == "tail" else [128]
            stderr = "" if self.no_metadata else "\n".join(
                f"[Parsed_metadata_2] lavfi.signalstats.YMIN=0\n"
                f"[Parsed_metadata_2] lavfi.signalstats.YMAX={value}" for value in values)
        code, error = self.command_failures.get(stage, (0, stderr))
        return subprocess.CompletedProcess(args, code, stdout, error)

    @staticmethod
    def assert_alpha_command(args):
        assert "-xerror" in args
        assert "metadata=mode=print" in args[args.index("-vf") + 1]
        assert args[-3:] == ["-f", "null", "-"]

    def run(self, module):
        with mock.patch.dict(sys.modules, {"resolve_ctl": self.controller}), \
                mock.patch.object(sys, "argv", [str(SCRIPT), "test-project", str(self.directory)]), \
                mock.patch.object(sys, "path", list(sys.path)), \
                mock.patch.object(module.time, "sleep"), \
                mock.patch.object(module.subprocess, "run", side_effect=self.command):
            module.main()


def run_stub_export(directory):
    fixture = StubExport(directory)
    fixture.missing.add(1)
    fixture.run(load_export())


class ResolveExportValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = StubExport(self.temp.name)
        self.module = load_export()
        self.output = io.StringIO()

    def run_main(self):
        with contextlib.redirect_stdout(self.output):
            self.fixture.run(self.module)

    def assert_restored(self):
        self.assertEqual(self.fixture.timeline.SetTrackEnable.call_args_list[-9:],
                         [mock.call("video", i, True) for i in range(1, 10)])
        self.assertEqual(self.fixture.resolve.OpenPage.call_args_list[-1], mock.call("edit"))

    def assert_fails(self, expected):
        with self.assertRaisesRegex(RuntimeError, expected):
            self.run_main()
        self.assert_restored()

    def test_success_preserves_summary(self):
        self.run_main()
        self.assert_restored()
        self.assertEqual(len(self.fixture.render_settings), 9)
        for track, settings in enumerate(self.fixture.render_settings, 1):
            self.assertEqual(settings, {
                "SelectAllFrames": False, "MarkIn": 108000, "MarkOut": 108000 + 10 + track - 1,
                "TargetDir": self.temp.name, "CustomName": f"matte_V{track}",
                "UniqueFilenameStyle": 0, "ExportVideo": True, "ExportAudio": False,
                "ExportAlpha": True, "AlphaMode": 1})
        self.assertEqual(self.output.getvalue().count("帧数✓  尾部✓"), 9)
        self.assertEqual(self.fixture.project.DeleteRenderJob.call_count, 9)

    def test_missing_output_fails(self):
        self.fixture.missing.add(2)
        self.assert_fails("V2.*缺失")

    def test_frame_mismatch_fails(self):
        self.fixture.probe_frames[3] = 1
        self.assert_fails("V3.*帧数.*预期 13")

    def test_transparent_tail_fails(self):
        self.fixture.tail_values = [0, 0]
        self.assert_fails("尾部 alpha 全透明")

    def test_missing_alpha_metadata_fails(self):
        self.fixture.no_metadata = True
        self.assert_fails("alpha 元数据缺失")

    def test_probe_failure_preserves_stderr(self):
        self.fixture.command_failures["probe"] = (7, "probe original failure")
        self.assert_fails("ffprobe 失败.*exit 7.*probe original failure")

    def test_decode_failure_preserves_stderr(self):
        self.fixture.command_failures["decode"] = (8, "decode original failure")
        self.assert_fails("alpha 解码 失败.*exit 8.*decode original failure")

    def test_tail_decode_failure_preserves_stderr(self):
        self.fixture.command_failures["tail"] = (9, "tail original failure")
        self.assert_fails("尾部 alpha 解码 失败.*exit 9.*tail original failure")

    def test_multiple_output_failures_are_reported(self):
        self.fixture.missing.update((2, 7))
        self.fixture.probe_frames[4] = 0
        with self.assertRaises(RuntimeError) as result:
            self.run_main()
        self.assertIn("V2: 缺失", str(result.exception))
        self.assertIn("V7: 缺失", str(result.exception))
        self.assertIn("V4: 帧数", str(result.exception))
        self.assert_restored()

    def test_later_tail_content_is_accepted(self):
        self.fixture.tail_values = [0, 128]
        self.run_main()
        self.assertIn("尾部alphaMax=128", self.output.getvalue())

    def test_pipeline_propagates_export_failure(self):
        wrapper = Path(self.temp.name) / "wrapper"
        wrapper.mkdir()
        shutil.copyfile(ROOT / "apps/matting/resolve_matte/matte_pipeline.sh",
                        wrapper / "matte_pipeline.sh")
        shim = wrapper / "python3"
        shim.write_text(
            f"#!{sys.executable}\nimport sys\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "if sys.argv[1] == 'export_alpha.py':\n"
            "    from tests.test_resolve_export_alpha import run_stub_export\n"
            f"    run_stub_export({self.temp.name!r})\n", encoding="utf-8")
        shim.chmod(0o700)
        env = dict(os.environ, PATH=str(wrapper) + os.pathsep + os.environ["PATH"],
                   SKIP_GUARD="1", SKIP_OPS="1", SKIP_EXPORT="0", PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(["bash", str(wrapper / "matte_pipeline.sh"), "test-project"],
                                env=env, text=True, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("V1: 缺失", result.stderr)
        self.assertNotIn("==== 完成 ====", result.stdout)

    def test_add_render_job_failure(self):
        self.fixture.project.AddRenderJob.side_effect = None
        self.fixture.project.AddRenderJob.return_value = ""
        self.assert_fails("创建渲染任务失败")
        self.fixture.project.StartRendering.assert_not_called()

    def test_start_rendering_failure(self):
        self.fixture.project.StartRendering.side_effect = None
        self.fixture.project.StartRendering.return_value = False
        self.assert_fails("启动渲染失败")
        self.fixture.project.DeleteRenderJob.assert_called_once_with("job-1")

    def test_failed_job_rejects_stale_output(self):
        (Path(self.temp.name) / "matte_V1.mov").write_bytes(b"old")
        self.fixture.project.GetRenderJobStatus.return_value = {
            "JobStatus": "Failed", "CompletionPercentage": 100, "Error": "render original failure"}
        self.assert_fails("render original failure")

    def test_render_exception_restores_tracks(self):
        self.fixture.project.GetRenderJobStatus.side_effect = RuntimeError("render API disconnected")
        self.assert_fails("render API disconnected")
        self.fixture.project.DeleteRenderJob.assert_called_once_with("job-1")

    def test_cleanup_exception_keeps_original_reason(self):
        self.fixture.project.GetRenderJobStatus.side_effect = RuntimeError("render original failure")
        self.fixture.project.DeleteRenderJob.side_effect = RuntimeError("delete original failure")
        calls = 0

        def track_enable(*args):
            nonlocal calls
            calls += 1
            if calls in (10, 14):
                raise RuntimeError(f"restore original failure {calls}")
            return True

        self.fixture.timeline.SetTrackEnable.side_effect = track_enable
        try:
            self.run_main()
        except RuntimeError:
            error = traceback.format_exc()
        else:
            self.fail("render and cleanup errors must fail")
        for expected in ("render original failure", "delete original failure",
                         "restore original failure 10", "restore original failure 14"):
            self.assertIn(expected, error)
        self.assert_restored()

    def test_cleanup_failure_after_success_fails(self):
        self.fixture.project.DeleteRenderJob.return_value = False
        self.assert_fails("删除渲染任务失败")

    def test_unknown_job_status_fails(self):
        for status in ("Unknown localized state", "Failed", "Cancelled"):
            with self.subTest(status=status):
                self.fixture.project.GetRenderJobStatus.return_value = {
                    "JobStatus": status, "CompletionPercentage": 100}
                self.assert_fails("UNKNOWN.*" + status)

    def test_unchanged_output_fails(self):
        path = Path(self.temp.name) / "matte_V1.mov"
        path.write_bytes(b"old")
        original = path.stat()
        self.fixture.project.StartRendering.side_effect = None
        self.fixture.project.StartRendering.return_value = True
        self.assert_fails("本次渲染未更新旧输出.*job-1")
        self.assertEqual(path.read_bytes(), b"old")
        self.assertEqual(path.stat().st_mtime_ns, original.st_mtime_ns)

    def test_rewritten_output_is_accepted(self):
        path = Path(self.temp.name) / "matte_V1.mov"
        path.write_bytes(b"old output")
        self.fixture.project.GetRenderJobStatus.return_value = {
            "JobStatus": "Concluso", "CompletionPercentage": 100}
        self.run_main()
        self.assertEqual(path.read_bytes(), b"x")
        self.assert_restored()

    def test_page_switch_failure_aborts_render(self):
        for page in ("edit", "deliver"):
            for failure in (False, None):
                with self.subTest(page=page, returned=failure):
                    self.fixture = StubExport(self.temp.name)
                    fail_at = 1 if page == "edit" else 2
                    page_calls = 0

                    def open_page(requested):
                        nonlocal page_calls
                        page_calls += 1
                        if page_calls == fail_at:
                            self.assertEqual(requested, page)
                            return failure
                        return True

                    self.fixture.resolve.OpenPage.side_effect = open_page
                    with self.assertRaisesRegex(RuntimeError, f"OpenPage.*{page}.*{failure!r}"):
                        self.run_main()
                    self.fixture.project.SetRenderSettings.assert_not_called()
                    self.fixture.project.AddRenderJob.assert_not_called()
                    self.fixture.project.StartRendering.assert_not_called()
                    self.assert_restored()
                    expected_calls = 9 if page == "edit" else 18
                    self.assertEqual(self.fixture.timeline.SetTrackEnable.call_count, expected_calls)

    @staticmethod
    def timeline_proxy(name, unique_id):
        timeline = mock.Mock()
        timeline.GetName.return_value = name
        timeline.GetUniqueId.return_value = unique_id
        return timeline

    def assert_no_render_mutation(self, *timelines):
        self.fixture.resolve.OpenPage.assert_not_called()
        self.fixture.project.SetCurrentRenderFormatAndCodec.assert_not_called()
        self.fixture.project.SetRenderSettings.assert_not_called()
        self.fixture.project.AddRenderJob.assert_not_called()
        self.fixture.project.StartRendering.assert_not_called()
        for timeline in timelines:
            if timeline is not None:
                timeline.SetTrackEnable.assert_not_called()
                timeline.GetItemsInTrack.assert_not_called()

    def test_missing_grid_timeline_fails_before_mutation(self):
        for no_current in (False, True):
            with self.subTest(no_current=no_current):
                self.fixture = StubExport(self.temp.name)
                other = self.fixture.timeline
                other.GetName.return_value = "other"
                other.GetUniqueId.return_value = "other-id"
                self.fixture.project.GetCurrentTimeline.return_value = None if no_current else other
                self.fixture.project.GetTimelineCount.return_value = 1
                self.fixture.project.GetTimelineByIndex.return_value = other
                with self.assertRaisesRegex(RuntimeError, "grid9"):
                    self.run_main()
                self.fixture.project.SetCurrentTimeline.assert_not_called()
                self.assert_no_render_mutation(other, self.fixture.timeline)

    def test_timeline_switch_failure_fails_before_mutation(self):
        for result in (False, None):
            with self.subTest(returned=result):
                self.fixture = StubExport(self.temp.name)
                other = self.timeline_proxy("other", "other-id")
                self.fixture.project.GetCurrentTimeline.return_value = other
                self.fixture.project.GetTimelineCount.return_value = 1
                self.fixture.project.GetTimelineByIndex.return_value = self.fixture.timeline
                self.fixture.project.SetCurrentTimeline.return_value = result
                with self.assertRaisesRegex(RuntimeError, f"SetCurrentTimeline.*{result!r}"):
                    self.run_main()
                self.fixture.project.SetCurrentTimeline.assert_called_once_with(self.fixture.timeline)
                self.assert_no_render_mutation(other, self.fixture.timeline)

    def test_timeline_readback_mismatch_fails_before_mutation(self):
        cases = [(None, None), ("other", "other-id"), ("grid9", "different-id"),
                 ("grid9", None), ("grid9", "")]
        for name, unique_id in cases:
            with self.subTest(name=name, unique_id=unique_id):
                self.fixture = StubExport(self.temp.name)
                other = self.timeline_proxy("other", "other-id")
                readback = None if name is None else self.timeline_proxy(name, unique_id)
                self.fixture.project.GetCurrentTimeline.side_effect = [other, readback]
                self.fixture.project.GetTimelineCount.return_value = 1
                self.fixture.project.GetTimelineByIndex.return_value = self.fixture.timeline
                self.fixture.project.SetCurrentTimeline.return_value = True
                with self.assertRaisesRegex(RuntimeError, "grid9.*身份"):
                    self.run_main()
                self.assert_no_render_mutation(other, readback, self.fixture.timeline)

    def test_grid_timeline_switch_uses_verified_identity(self):
        for no_current in (False, True):
            with self.subTest(no_current=no_current):
                self.fixture = StubExport(self.temp.name)
                other = None if no_current else self.timeline_proxy("other", "other-id")
                target = self.timeline_proxy("grid9", "grid9-id")
                target.GetItemsInTrack.side_effect = self.fixture.items
                target.SetTrackEnable.return_value = True
                # Resolve may return a different Python proxy for the same timeline.
                readback = self.fixture.timeline
                self.assertIsNot(target, readback)
                self.fixture.project.GetCurrentTimeline.side_effect = [other, readback]
                self.fixture.project.GetTimelineCount.return_value = 1
                self.fixture.project.GetTimelineByIndex.return_value = target
                self.fixture.project.SetCurrentTimeline.return_value = True
                self.run_main()
                self.fixture.project.SetCurrentTimeline.assert_called_once_with(target)
                target.SetTrackEnable.assert_not_called()
                target.GetItemsInTrack.assert_not_called()
                self.assertEqual(readback.GetItemsInTrack.call_count, 9)
                self.assertEqual(self.fixture.project.AddRenderJob.call_count, 9)
                self.assert_restored()

    def test_unexpected_video_track_count_fails_before_mutation(self):
        for count in (8, 10, None):
            with self.subTest(count=count):
                self.fixture = StubExport(self.temp.name)
                self.fixture.timeline.GetTrackCount.return_value = count
                with self.assertRaisesRegex(RuntimeError, f"grid9.*9.*{count!r}"):
                    self.run_main()
                self.assert_no_render_mutation(self.fixture.timeline)
