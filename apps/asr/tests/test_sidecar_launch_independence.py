#!/usr/bin/env python3
"""sidecar 直接启动入口的路径独立性对抗测试（issue #141 红队 F1）。

红队实测缺陷：`python3 apps/asr/sidecar_server.py --port 19001 --model <IME 锚点路径>`
曾可成功启动——直接启动入口对 --model / --aligner / --models-root 零校验。
本测试断言：任何 IME 锚定路径（含 symlink 逃逸、遗留注入面）在绑定/加载之前
被拒绝（exit 非零、中文诊断），零监听零加载；干净路径不误伤。

铁律：只用假 HOME 下的假 IME 目录（锚点在 $HOME 内展开），绝不触碰真实输入法、
绝不向 18081 发任何字节（本组用例全部使用非 18081 的临时端口且拒绝发生在
绑定之前）。
"""
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ASR_DIR = Path(__file__).resolve().parent.parent
SIDECAR = ASR_DIR / "sidecar_server.py"


class SidecarLaunchIndependenceTest(unittest.TestCase):
    """公共脚手架：假 HOME + 假 IME 锚点 + 子进程直启 sidecar。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vtx-asr-sidecar-launch-")
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        # 假 IME 仓（默认锚点 ~/文档/01_活跃项目/type/ibus-voice-ime-github 在
        # 假 HOME 内展开——绝不触碰真实 ~/文档）
        self.ime = self.home / "文档/01_活跃项目/type/ibus-voice-ime-github"
        self.ime_model = self.ime / "vendor/models/qwen3-asr/Qwen3-ASR-1.7B"
        self.ime_model.mkdir(parents=True)
        (self.ime_model / "model.safetensors").write_bytes(b"FAKE-IME-WEIGHT")
        (self.ime_model / "config.json").write_text("{}", encoding="utf-8")
        self.env = dict(os.environ)
        self.env["HOME"] = str(self.home)
        self.env.pop("VT_ASR_IME_ROOT", None)
        self.env.pop("VTX_CONFIG", None)
        for var in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
                    "PYTHONUSERBASE", "PYTHONNOUSERSITE"):
            self.env.pop(var, None)

    def tearDown(self):
        self.tmp.cleanup()

    def _free_port(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _launch(self, *extra, isolated=False):
        argv = [sys.executable] + (["-I"] if isolated else []) + [str(SIDECAR)]
        return subprocess.run(argv + list(extra), env=self.env,
                              capture_output=True, text=True, timeout=60)

    def _assert_rejected(self, r, what):
        self.assertNotEqual(r.returncode, 0, "%s 未被拒绝（exit 0）" % what)
        self.assertIn("已拒绝", (r.stderr + r.stdout), "%s 缺中文拒绝诊断" % what)
        self.assertIn("语音输入法", (r.stderr + r.stdout), "%s 诊断未指向输入法" % what)

    # ---------------------------------------------------------------- F1 负例

    def test_direct_launch_model_ime_anchor_rejected(self):
        """红队最小复现：--model IME 锚点路径直启 → exit 非零 fail-closed。"""
        r = self._launch("--port", str(self._free_port()), "--model", str(self.ime_model))
        self._assert_rejected(r, "--model IME 锚点")
        self.assertEqual(r.returncode, 2)

    def test_direct_launch_aligner_ime_anchor_rejected(self):
        r = self._launch("--port", str(self._free_port()), "--model", "none",
                         "--aligner", str(self.ime_model))
        self._assert_rejected(r, "--aligner IME 锚点")

    def test_direct_launch_models_root_ime_anchor_rejected(self):
        r = self._launch("--port", str(self._free_port()),
                         "--models-root", str(self.ime / "vendor/models"))
        self._assert_rejected(r, "--models-root IME 锚点")

    def test_direct_launch_symlink_escape_rejected(self):
        """看似中立的目录 symlink 指进 IME → resolve 追真身后拒绝。"""
        link = Path(self.tmp.name) / "looks-clean"
        link.symlink_to(self.ime_model, target_is_directory=True)
        r = self._launch("--port", str(self._free_port()), "--model", str(link))
        self._assert_rejected(r, "--model symlink 逃逸")

    def test_direct_launch_resolved_alias_symlink_rejected(self):
        """--models-root 干净根内 Qwen3-ASR-1.7B 目录为指向 IME 的 symlink：
        别名解析后的路径同样过独立性校验。"""
        root = Path(self.tmp.name) / "clean-root"
        (root / "Qwen3-ASR-0.6B").mkdir(parents=True)
        (root / "Qwen3-ASR-0.6B/config.json").write_text("{}", encoding="utf-8")
        (root / "Qwen3-ASR-1.7B").symlink_to(self.ime_model, target_is_directory=True)
        r = self._launch("--port", str(self._free_port()),
                         "--models-root", str(root), "--model", "1.7b")
        self._assert_rejected(r, "解析后 symlink 逃逸")

    def test_direct_launch_isolated_mode_rejected(self):
        """init.sh/client 以 `python -I` 拉起（隔离模式无脚本目录入 sys.path）：
        independence 按文件路径加载，拒绝仍生效。"""
        r = self._launch("--port", str(self._free_port()), "--model", str(self.ime_model),
                         isolated=True)
        self._assert_rejected(r, "-I 隔离模式 --model IME 锚点")

    # ---------------------------------------------------------------- 红队二轮 D1/D2 负例

    def test_direct_launch_hardlink_borrowed_weights_rejected(self):
        """红队 D1：把 IME 模型权重文件硬链接（ln）进"干净"目录后直启——
        路径锚点查不到（st_nlink=2 同 inode），必须查链接关系拒绝。"""
        clean = Path(self.tmp.name) / "clean-hl/Qwen3-ASR-1.7B"
        clean.mkdir(parents=True)
        os.link(self.ime_model / "model.safetensors", clean / "model.safetensors")
        os.link(self.ime_model / "config.json", clean / "config.json")
        self.assertEqual((clean / "model.safetensors").stat().st_nlink, 2)
        r = self._launch("--port", str(self._free_port()), "--model", str(clean),
                         "--no-aligner", "--once-health")
        self.assertNotEqual(r.returncode, 0, "硬链接借用 IME 权重未被拒绝（exit 0）")
        out = r.stderr + r.stdout
        self.assertIn("硬链接数 2>1", out)
        self.assertIn("已拒绝", out)

    def test_direct_launch_hardlink_aligner_weights_rejected(self):
        """红队 D1（aligner 面）：--aligner 目录内权重文件硬链接自 IME 同样拒绝。"""
        clean_model = self.home / ".local/share/framivra/asr/models/Qwen3-ASR-1.7B"
        clean_model.mkdir(parents=True)
        (clean_model / "config.json").write_text("{}", encoding="utf-8")
        (clean_model / "model.safetensors").write_bytes(b"CLEAN")
        clean_aligner = Path(self.tmp.name) / "clean-hl-aligner/Qwen3-ForcedAligner-0.6B"
        clean_aligner.mkdir(parents=True)
        os.link(self.ime_model / "model.safetensors", clean_aligner / "model.safetensors")
        (clean_aligner / "config.json").write_text("{}", encoding="utf-8")
        r = self._launch("--port", str(self._free_port()), "--model", str(clean_model),
                         "--aligner", str(clean_aligner), "--once-health")
        self.assertNotEqual(r.returncode, 0, "aligner 硬链接借用 IME 权重未被拒绝")
        self.assertIn("硬链接数 2>1", r.stderr + r.stdout)

    # ---------------------------------------------------------------- 红队四轮 N1/N1b/N4/N3 负例

    def test_direct_launch_n1_shard_symlink_rejected(self):
        """红队 N1：分片模型 index.json weight_map 指向的分片是 symlink→IME
        ——文件名白名单查不到，目录级审计必须拒。"""
        import json as _json
        d = Path(self.tmp.name) / "n1-shard"
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}", encoding="utf-8")
        (d / "model.safetensors.index.json").write_text(_json.dumps(
            {"metadata": {"total_size": 8},
             "weight_map": {"model": "model-00001-of-00001.safetensors"}}),
            encoding="utf-8")
        (d / "model-00001-of-00001.safetensors").symlink_to(
            self.ime_model / "model.safetensors")
        r = self._launch("--port", str(self._free_port()), "--model", str(d),
                         "--no-aligner", "--once-health")
        self._assert_rejected(r, "N1 分片 symlink→IME")
        self.assertEqual(r.returncode, 2)

    def test_direct_launch_n1b_mixed_shard_symlink_rejected(self):
        """红队 N1b：干净单文件 + 分片 symlink 混放 → 拒。"""
        import json as _json
        d = Path(self.tmp.name) / "n1b-mixed"
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}", encoding="utf-8")
        (d / "model.safetensors").write_bytes(b"CLEAN")
        (d / "model.safetensors.index.json").write_text(_json.dumps(
            {"weight_map": {"a": "model-00001-of-00002.safetensors",
                            "b": "model-00002-of-00002.safetensors"}}),
            encoding="utf-8")
        (d / "model-00001-of-00002.safetensors").write_bytes(b"CLEAN")
        (d / "model-00002-of-00002.safetensors").symlink_to(
            self.ime_model / "model.safetensors")
        r = self._launch("--port", str(self._free_port()), "--model", str(d),
                         "--no-aligner", "--once-health")
        self._assert_rejected(r, "N1b 干净+分片 symlink 混放")
        self.assertEqual(r.returncode, 2)

    def test_direct_launch_n4_aligner_alias_symlink_rejected(self):
        """红队 N4：aligner 权重别名（pytorch_model.bin）symlink→IME → 拒。"""
        clean_model = self.home / ".local/share/framivra/asr/models/Qwen3-ASR-1.7B"
        clean_model.mkdir(parents=True)
        (clean_model / "config.json").write_text("{}", encoding="utf-8")
        (clean_model / "model.safetensors").write_bytes(b"CLEAN")
        d = Path(self.tmp.name) / "n4-aligner"
        d.mkdir(parents=True)
        (d / "pytorch_model.bin").symlink_to(self.ime_model / "model.safetensors")
        r = self._launch("--port", str(self._free_port()), "--model", str(clean_model),
                         "--aligner", str(d), "--once-health")
        self._assert_rejected(r, "N4 aligner pytorch_model.bin symlink→IME")
        self.assertEqual(r.returncode, 2)

    def test_direct_launch_n3_fifo_weight_rejected(self):
        """红队 N3 观察项收口：FIFO 权重文件 → 拒。"""
        d = Path(self.tmp.name) / "n3-fifo"
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}", encoding="utf-8")
        os.mkfifo(d / "model.safetensors")
        r = self._launch("--port", str(self._free_port()), "--model", str(d),
                         "--no-aligner", "--once-health")
        self.assertNotEqual(r.returncode, 0, "FIFO 权重未被拒绝")
        self.assertIn("特殊文件", r.stderr + r.stdout)

    def test_direct_launch_fake_independence_module_shadow_rejected(self):
        """红队 D2：PYTHONPATH 放假 independence.py + `python3 -P` 直启
        （-P 不把脚本目录放 sys.path 首位 → PYTHONPATH 遮蔽生效）——
        _independence 必须恒按 __file__ 同目录文件路径加载真模块，拒绝仍生效。"""
        if sys.version_info < (3, 11):
            raise unittest.SkipTest(
                "python3 -P 于 3.11 引入；3.10 下脚本目录恒为 sys.path[0]，"
                "该遮蔽向量本就不可构造，产品面由其余用例覆盖")
        fake = Path(self.tmp.name) / "fakemod"
        fake.mkdir()
        (fake / "independence.py").write_text(
            "class AsrIndependenceError(Exception): pass\n"
            "def check_path_independent(path, what): return path\n"
            "def verify_model_dir(path, what='x', check_weights=False): return path\n"
            "def ime_anchor_roots(): return []\n"
            "WEIGHT_FILES = ()\n", encoding="utf-8")
        env = dict(self.env)
        env["PYTHONPATH"] = str(fake)
        argv = [sys.executable, "-P", str(SIDECAR)]
        r = subprocess.run(
            argv + ["--port", str(self._free_port()), "--model", str(self.ime_model),
                    "--no-aligner", "--once-health"],
            env=env, capture_output=True, text=True, timeout=60)
        self._assert_rejected(r, "PYTHONPATH 假 independence 遮蔽（-P）")
        self.assertEqual(r.returncode, 2)

    # ---------------------------------------------------------------- 正例（不误伤）

    def test_direct_launch_clean_model_once_health_ok(self):
        """干净模型目录（假 HOME 内，非 IME）+ --once-health → 正常起停（exit 0），
        证明拒绝只针对 IME 路径而非启动入口本身。"""
        clean = self.home / ".local/share/framivra/asr/models/Qwen3-ASR-1.7B"
        clean.mkdir(parents=True)
        (clean / "config.json").write_text("{}", encoding="utf-8")
        (clean / "model.safetensors").write_bytes(b"CLEAN")
        r = self._launch("--port", str(self._free_port()),
                         "--model", "1.7b", "--no-aligner", "--once-health")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("/health 自检通过", r.stdout)

    def test_direct_launch_clean_sharded_model_with_aligner_ok(self):
        """正例：干净自建分片模型（index.json+合法分片）+ 干净 aligner
        + --once-health → exit 0，不误伤。"""
        import json as _json
        clean = self.home / ".local/share/framivra/asr/models/Qwen3-ASR-1.7B"
        clean.mkdir(parents=True)
        (clean / "config.json").write_text("{}", encoding="utf-8")
        (clean / "model.safetensors.index.json").write_text(_json.dumps(
            {"weight_map": {"a": "model-00001-of-00002.safetensors",
                            "b": "model-00002-of-00002.safetensors"}}),
            encoding="utf-8")
        (clean / "model-00001-of-00002.safetensors").write_bytes(b"AAA")
        (clean / "model-00002-of-00002.safetensors").write_bytes(b"BBB")
        aligner = self.home / ".local/share/framivra/asr/models/Qwen3-ForcedAligner-0.6B"
        aligner.mkdir(parents=True)
        (aligner / "config.json").write_text("{}", encoding="utf-8")
        (aligner / "pytorch_model.bin").write_bytes(b"CLEAN-ALIGNER")
        r = self._launch("--port", str(self._free_port()),
                         "--model", str(clean), "--aligner", str(aligner),
                         "--once-health")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("/health 自检通过", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
