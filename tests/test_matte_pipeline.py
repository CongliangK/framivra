"""Shell argument/stage contract only: never execute Resolve or media tools."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "apps/matting/resolve_matte/matte_pipeline.sh"
BASH = shutil.which("bash")
DEFAULT_SUFFIX = "文档/04_bilibili视频/voice-ime/cache/bg_gen/centered9"
PROJECT = "测试 工程"

# This recorder receives script names as data. It never imports or executes them.
FAKE_PYTHON = '''import json
import os
import sys

with open(os.environ["MATTE_TEST_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"argv": sys.argv[1:],
                          "env": {key: os.environ.get(key) for key in
                                  ("HOME", "VIDEOS", "MATTE_BASE")}},
                         ensure_ascii=False) + "\\n")
if len(sys.argv) < 2 or sys.argv[1] not in (
        "vram_guard.py", "prep.py", "gen_sequence.py", "ops_runner.py",
        "export_alpha.py"):
    sys.exit(97)
if sys.argv[1] == os.environ.get("MATTE_TEST_FAIL_STAGE"):
    sys.exit(int(os.environ["MATTE_TEST_FAIL_CODE"]))
'''


@unittest.skipUnless(os.name == "posix" and BASH, "requires POSIX bash")
class MattingPipelineArguments(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="matte-pipeline-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "测试 HOME"
        self.home.mkdir()
        self.pipeline_dir = self.root / "流水线 fixture"
        self.pipeline_dir.mkdir()
        self.pipeline = self.pipeline_dir / "matte_pipeline.sh"
        # Only the shell is copied: real stage files are not present in the fixture.
        self.pipeline.write_bytes(PIPELINE.read_bytes())
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "python3"
        descriptor = os.open(fake, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("#!" + sys.executable + " -I\n" + FAKE_PYTHON)
        self.env = {"HOME": str(self.home),
                    "PATH": str(bin_dir) + os.pathsep + os.defpath,
                    "LC_ALL": "C.UTF-8"}
        self.receipts = []

    def video_dir(self, name):
        path = self.root / name
        path.mkdir(exist_ok=True)
        return str(path)

    def expected_calls(self, videos, skip=()):
        calls = []
        if "guard" not in skip:
            calls.append(["vram_guard.py", "--project", PROJECT])
        calls.append(["prep.py", PROJECT, videos])
        if "ops" not in skip:
            calls.extend([["gen_sequence.py"], ["ops_runner.py", "op_sequence.json"]])
        if "export" not in skip:
            calls.append(["export_alpha.py", PROJECT])
        return calls

    def run_pipeline(self, label, arguments, overrides, expected, exit_code=0):
        log = self.root / ("calls-%d.jsonl" % len(self.receipts))
        env = dict(self.env, MATTE_TEST_LOG=str(log))
        env.update(overrides)
        command = [BASH, "--noprofile", "--norc", str(self.pipeline)] + arguments
        result = subprocess.run(command, cwd=self.root, env=env,
                                capture_output=True, text=True, timeout=10)
        calls = ([json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
                 if log.exists() else [])
        self.receipts.append({"label": label, "command": command, "env": env,
                              "exit": result.returncode, "calls": calls,
                              "stdout": result.stdout, "stderr": result.stderr})
        self.assertEqual(result.returncode, exit_code, result.stderr)
        self.assertEqual([call["argv"] for call in calls], expected)
        expected_env = {key: env.get(key) for key in ("HOME", "VIDEOS", "MATTE_BASE")}
        if "VIDEOS" in env:
            # Shell assignment preserves an inherited variable's export attribute.
            expected_env["VIDEOS"] = next(call[2] for call in expected if call[0] == "prep.py")
        for call in calls:
            self.assertEqual(call["env"], expected_env)
        self.assertEqual("==== 完成 ====" in result.stdout, exit_code == 0)

    def test_matte_r4_01_cli_wins_with_spaces_and_chinese(self):
        cli = self.video_dir("CLI 视频 素材")
        self.run_pipeline("MATTE-R4-01", [PROJECT, cli],
                          {"VIDEOS": self.video_dir("VIDEOS 素材"),
                           "MATTE_BASE": self.video_dir("MATTE_BASE 素材")},
                          self.expected_calls(cli))

    def test_matte_r4_02_matte_base_without_videos(self):
        base = self.video_dir("MATTE_BASE 视频 素材")
        self.run_pipeline("MATTE-R4-02", [PROJECT], {"MATTE_BASE": base},
                          self.expected_calls(base))

    def test_matte_r4_03_videos_wins_over_matte_base(self):
        videos = self.video_dir("VIDEOS 视频 素材")
        self.run_pipeline("MATTE-R4-03", [PROJECT],
                          {"VIDEOS": videos, "MATTE_BASE": self.video_dir("备用 视频")},
                          self.expected_calls(videos))

    def test_matte_r4_04_empty_values_fall_back(self):
        default = str(self.home / DEFAULT_SUFFIX)
        videos = self.video_dir("环境 视频")
        base = self.video_dir("备用 视频")
        cases = [
            ("default", [PROJECT], {}, default),
            ("empty-cli", [PROJECT, ""], {"VIDEOS": videos, "MATTE_BASE": base}, videos),
            ("empty-videos", [PROJECT, ""], {"VIDEOS": "", "MATTE_BASE": base}, base),
            ("empty-base", [PROJECT], {"MATTE_BASE": ""}, default),
            ("all-empty", [PROJECT, ""], {"VIDEOS": "", "MATTE_BASE": ""}, default),
        ]
        for label, arguments, overrides, expected in cases:
            with self.subTest(label=label):
                self.run_pipeline("MATTE-R4-04/" + label, arguments, overrides,
                                  self.expected_calls(expected))

    def test_matte_r4_05_prep_failure_stops_downstream(self):
        videos = self.video_dir("失败 视频")
        self.run_pipeline("MATTE-R4-05", [PROJECT, videos],
                          {"MATTE_TEST_FAIL_STAGE": "prep.py", "MATTE_TEST_FAIL_CODE": "23"},
                          self.expected_calls(videos)[:2], exit_code=23)

    def test_matte_r4_06_success_preserves_stage_order_and_arguments(self):
        videos = self.video_dir("顺序 视频")
        self.run_pipeline("MATTE-R4-06", [PROJECT, videos], {}, self.expected_calls(videos))

    def test_matte_r4_07_skip_flags_preserve_prep(self):
        videos = self.video_dir("跳过 视频")
        for skipped in [("guard",), ("ops",), ("export",), ("guard", "ops", "export")]:
            with self.subTest(skipped=skipped):
                self.run_pipeline("MATTE-R4-07/" + "+".join(skipped), [PROJECT, videos],
                                  {"SKIP_" + stage.upper(): "1" for stage in skipped},
                                  self.expected_calls(videos, skip=skipped))

    def test_matte_r4_08_bash_syntax(self):
        command = [BASH, "--noprofile", "--norc", "-n", str(self.pipeline)]
        result = subprocess.run(command, cwd=self.root, env=self.env,
                                capture_output=True, text=True, timeout=10)
        self.receipts.append({"label": "MATTE-R4-08", "command": command,
                              "exit": result.returncode, "calls": [],
                              "stdout": result.stdout, "stderr": result.stderr})
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
