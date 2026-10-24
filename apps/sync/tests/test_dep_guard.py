"""sync 依赖缺失指引单测（A4 缺陷2）。

- sync_audio.py --help：零第三方依赖也退出码 0（app.json health.command 依赖此性质）；
- 缺 scipy（sync_audio）/ 缺 numpy（repack_voice）：中文指引、退出码 3、无 traceback。

跨机确定性复现：PYTHONPATH 前置 stub 包（import 即 ImportError）屏蔽真实第三方库，
不依赖本机是否恰好装了 scipy/numpy。参数一律 list 形式进 subprocess，无 shell 注入面。
运行：cd apps/sync && env python3 -m unittest discover -s tests -t .
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
EXIT_MISSING_DEP = 3  # 与 sync_audio.py / repack_voice.py 的 EXIT_MISSING_DEP 对齐


def stub_dir(pkgs):
    """建临时目录，内放同名 stub 包（import 即 ImportError）用于 PYTHONPATH 屏蔽。"""
    td = Path(tempfile.mkdtemp(prefix="a4_stub_"))
    for p in pkgs:
        (td / p).mkdir()
        (td / p / "__init__.py").write_text(
            f'raise ImportError("stub: {p} hidden by A4 test")\n', encoding="utf-8")
    return td


def run_cli(args, hide=(), env_overrides=None):
    stub = stub_dir(hide)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(stub)  # 覆盖式前置：stub 先于 site-packages 生效
    for key, value in (env_overrides or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = str(value)
    try:
        return subprocess.run([sys.executable, *args], capture_output=True,
                              encoding="utf-8", errors="replace",
                              cwd=str(APP_DIR), env=env, timeout=60)
    finally:
        shutil.rmtree(stub, ignore_errors=True)


class SyncHelpTests(unittest.TestCase):
    def test_help_works_without_any_third_party_dep(self):
        r = run_cli(["sync_audio.py", "--help"], hide=("numpy", "scipy", "soundfile", "soxr"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("usage:", r.stdout)

    def test_repack_help_works_without_numpy(self):
        r = run_cli(["repack_voice.py", "--help"], hide=("numpy",))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("usage:", r.stdout)


class MissingDepGuidanceTests(unittest.TestCase):
    def test_missing_scipy_chinese_guidance_exit_3(self):
        r = run_cli(["sync_audio.py", "/tmp/x.wav", "/tmp/y.wav", "/tmp/z.wav"], hide=("scipy",))
        self.assertEqual(r.returncode, EXIT_MISSING_DEP, r.stdout + r.stderr)
        combined = r.stdout + r.stderr
        self.assertNotIn("Traceback", combined)
        self.assertIn("scipy", combined)       # 点名缺失包
        self.assertIn("denoise", combined)     # conda 解释器指引
        self.assertIn("pip install", combined) # pip 备选

    def test_missing_numpy_sync_audio_guidance_exit_3(self):
        r = run_cli(["sync_audio.py", "/tmp/x.wav", "/tmp/y.wav", "/tmp/z.wav"], hide=("numpy",))
        self.assertEqual(r.returncode, EXIT_MISSING_DEP)
        self.assertNotIn("Traceback", r.stdout + r.stderr)
        self.assertIn("numpy", r.stdout + r.stderr)

    def test_repack_missing_numpy_guidance_exit_3(self):
        r = run_cli(["repack_voice.py", "-t", "/nonexistent/timeline.json"], hide=("numpy",))
        self.assertEqual(r.returncode, EXIT_MISSING_DEP, r.stdout + r.stderr)
        combined = r.stdout + r.stderr
        self.assertNotIn("Traceback", combined)
        self.assertIn("numpy", combined)
        self.assertIn("pip install", combined)


class RootConfigGuidanceTests(unittest.TestCase):
    """临时 repo/apps/sync 布局：不依赖真实配置、用户 HOME 或 denoise 安装态。"""

    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="f3_config_", dir=APP_DIR)
        self.addCleanup(td.cleanup)
        self.repo = Path(td.name) / "repo"
        self.script = self.repo / "apps" / "sync" / "sync_audio.py"
        self.script.parent.mkdir(parents=True)
        shutil.copyfile(APP_DIR / "sync_audio.py", self.script)
        self.home = Path(td.name) / "home"
        self.home.mkdir()
        self.python = self.repo / "fake-denoise-python"
        self.python.touch()  # 只作为存在的配置路径，绝不执行假解释器
        self.config = self.repo / "config.local.json"
        self.write_config(self.config, self.python)
        self.env = {"VTX_CONFIG": None, "HOME": self.home}

    @staticmethod
    def write_config(config, python):
        config.write_text(json.dumps({"denoise": {"python": str(python)}}), encoding="utf-8")

    def run_sync(self, hide=("scipy",)):
        return run_cli([str(self.script), "camera.wav", "mic.wav", "out.wav"],
                       hide=hide, env_overrides=self.env)

    def assert_missing(self, r):
        self.assertEqual(r.returncode, EXIT_MISSING_DEP, r.stdout + r.stderr)
        self.assertIn("scipy", r.stderr)
        self.assertNotIn("Traceback", r.stdout + r.stderr)

    def test_default_config_reads_repo_root_not_apps(self):
        wrong_python = self.repo / "wrong-apps-python"
        wrong_python.touch()
        self.write_config(self.repo / "apps" / "config.local.json", wrong_python)
        r = self.run_sync()
        self.assert_missing(r)
        self.assertIn(str(self.python), r.stderr)
        self.assertIn("config.local.json denoise.python 已配置", r.stderr)
        self.assertNotIn(str(wrong_python), r.stderr)

    def test_vtx_config_override_has_priority(self):
        override_python = self.repo / "override-python"
        override_python.touch()
        override_config = self.repo / "override.json"
        self.write_config(override_config, override_python)
        self.env["VTX_CONFIG"] = override_config
        r = self.run_sync()
        self.assert_missing(r)
        self.assertIn(str(override_python), r.stderr)
        self.assertNotIn(str(self.python), r.stderr)

    def test_nonexistent_configured_python_falls_back_to_installed_default(self):
        self.write_config(self.config, self.repo / "nonexistent-python")
        default = self.home / "miniconda3" / "envs" / "denoise" / "bin" / "python"
        default.parent.mkdir(parents=True)
        default.touch()
        r = self.run_sync()
        self.assert_missing(r)
        self.assertIn(str(default), r.stderr)
        self.assertIn("本机已安装 denoise 环境", r.stderr)
        self.assertNotIn("config.local.json denoise.python 已配置", r.stderr)

    def test_nonexistent_interpreters_keep_generic_fallback(self):
        self.write_config(self.config, self.repo / "nonexistent-python")
        r = self.run_sync()
        self.assert_missing(r)
        self.assertIn("~/miniconda3/envs/denoise/bin/python", r.stderr)
        self.assertIn("需先创建 denoise 环境", r.stderr)
        self.assertNotIn("config.local.json denoise.python 已配置", r.stderr)

    def test_config_shapes_keep_missing_dependency_guidance(self):
        cpu = self.python
        gpu = self.home / "gpu" / "python"
        gpu.parent.mkdir()
        gpu.touch()  # 提示路径夹具，不执行解释器或加载模型。
        default = self.home / "miniconda3" / "envs" / "denoise" / "bin" / "python"
        default.parent.mkdir(parents=True)
        self.env.update(VTX_CONFIG=self.config, USERPROFILE=self.home,
                        PATH=str(self.repo / "no-external-tools"))
        cases = [
            ("top-list", "[1]", None),
            ("top-bool", "true", None),
            ("top-number", "1", None),
            ("top-string", '"invalid"', None),
            ("denoise-list", '{"denoise": [1]}', None),
            ("denoise-bool", '{"denoise": true}', None),
            ("denoise-number", '{"denoise": 1}', None),
            ("denoise-string", '{"denoise": "invalid"}', None),
            ("empty-object", "{}", None),
            ("null", "null", None),
            ("empty-list", "[]", None),
            ("false", "false", None),
            ("zero", "0", None),
            ("empty-string", '""', None),
            ("python-non-string", '{"denoise": {"python": true}}', None),
            ("python-blank", '{"denoise": {"python": "  "}}', None),
            ("missing-file", None, None),
            ("empty-file", "", None),
            ("bad-json", "{", None),
            ("configured-cpu", json.dumps({"denoise": {"python": f"  {cpu}  "}}), cpu),
            ("configured-gpu", json.dumps({"denoise": {"python": " ~/gpu/python "}}), gpu),
        ]
        example = "apps/sync/sync_audio.py <camera.(wav|mp4|mov)> <mic.wav> <out.wav>"
        for installed_default in (False, True):
            if installed_default:
                default.touch()
            for name, payload, configured in cases:
                with self.subTest(config=name, installed_default=installed_default):
                    if payload is None:
                        self.config.unlink()
                    else:
                        self.config.write_text(payload, encoding="utf-8")
                    r = self.run_sync(hide=("numpy", "scipy", "soundfile", "soxr"))
                    self.assert_missing(r)
                    self.assertEqual(r.stdout, "")
                    if configured:
                        hint = f"  {configured} {example}   # config.local.json denoise.python 已配置的解释器"
                    elif installed_default:
                        hint = f"  {default} {example}   # 本机已安装 denoise 环境"
                    else:
                        hint = (f"  ~/miniconda3/envs/denoise/bin/python {example}   "
                                "# 默认约定路径（本机未检测到，需先创建 denoise 环境）")
                    self.assertEqual(r.stderr.splitlines(), [
                        "sync_audio: 缺少第三方依赖：numpy, scipy, soundfile, soxr",
                        "本工具须在 conda denoise 环境（numpy/scipy/soundfile/soxr）中运行，推荐：",
                        hint,
                        f"或给当前解释器补装：{sys.executable} -m pip install numpy scipy soundfile soxr",
                    ])

    @unittest.skipIf(importlib.util.find_spec("scipy") is not None,
                     "当前解释器已装 scipy，无法复现缺依赖路径")
    def test_real_base_python_uses_temporary_repo_root_config(self):
        wavs = [self.repo / name for name in ("camera.wav", "mic.wav", "out.wav")]
        for path in wavs:
            with wave.open(str(path), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(48000)
                f.writeframes(b"\x00\x00" * 480)
        before = [path.read_bytes() for path in wavs]
        # 基础 python3，三个真实 WAV 参数，不注入缺库 stub。
        r = run_cli([str(self.script), *(str(path) for path in wavs)],
                    env_overrides=self.env)
        self.assert_missing(r)
        self.assertIn(str(self.python), r.stderr)
        self.assertIn("config.local.json denoise.python 已配置", r.stderr)
        self.assertEqual([path.read_bytes() for path in wavs], before)


class RealEnvTests(unittest.TestCase):
    """不屏蔽、按当前解释器真实依赖面跑（本机基础 python3 无 scipy → 实证缺陷2路径）。"""

    @unittest.skipIf(importlib.util.find_spec("scipy") is not None,
                     "当前解释器已装 scipy，无法复现缺依赖路径")
    def test_real_base_python_missing_scipy(self):
        r = subprocess.run([sys.executable, "sync_audio.py", "/tmp/x.wav", "/tmp/y.wav", "/tmp/z.wav"],
                           capture_output=True, encoding="utf-8", errors="replace",
                           cwd=str(APP_DIR), timeout=60)
        self.assertEqual(r.returncode, EXIT_MISSING_DEP, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stdout + r.stderr)
        self.assertIn("scipy", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
