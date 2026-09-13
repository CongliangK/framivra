#!/usr/bin/env python3
"""examples/hello-plugin/tests/test_hello_plugin.py —— 音频插件纪律范本测试。

覆盖（ARCHITECTURE §4.2 / §2 协议；验收门 6 对照实现）：
  ① 三分支（§2.1 不变量 3）：VTX_CONFIG 指向临时 config 驱动 external 注册的
     enabled / 移除 / disabled 三态——validate 真跑（真 wav 源）；
  ② 缓存身份（不变量 ①②）：键值变 → 产物 digest 变；生效 → contribute_keys
     入 gc 白名单 + 指纹含插件段；卸载（external 移除）→ 键出白名单（gc_plan
     列入回收）+ 指纹段消失 + 整体指纹失配；
  ③ prepare_source 正例：真 ffmpeg 小 wav——产物在盘、48k stereo pcm、裸键不
     污染、增益实际生效（+6dB 与 -40dB 产物能量序）、未声明键 → None、
     无 ffmpeg fail-hard（mock which）；
  ④ manifest 结构过校验 + health_check.py 探针 exit 0。
external 注册经 VTX_CONFIG 临时 config——绝不读写本机 config.local.json。
运行：repo 根 `bash scripts/run_all_tests.sh`（批次四发现 examples/*/tests）；
独立跑：cd examples/hello-plugin && env python3 -m unittest discover -s tests -t . -v
（env python3 前缀为 argv[0] 劫持纪律，见全局 AGENTS.md）。
"""
import importlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[3]      # examples/hello-plugin/tests → repo 根
PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(PLUGIN_DIR.parent))      # examples/ —— 包导入锚（同 hooks 加载路径）

hello = importlib.import_module("hello-plugin")  # noqa: E402（连字符目录名经 import_module）

from vtx import hygiene                         # noqa: E402
from vtx.cache_state import receipt_fingerprint  # noqa: E402
from vtx.infra import Ctx                       # noqa: E402
from vtx.pluginlib import hooks as pl_hooks     # noqa: E402
from vtx.pluginlib.manifest import load_manifest  # noqa: E402
from vtx.stages import validate, _scan_plugin_keys  # noqa: E402

EXT_PATH = str(PLUGIN_DIR)


def _write_cfg(path: Path, entry) -> None:
    """临时 config：external_plugins = [entry]（None = 无条目 = 已卸载）。"""
    cfg = {} if entry is None else {"external_plugins": [entry]}
    path.write_text(json.dumps(cfg), encoding="utf-8")


def _make_src(root: Path, db=3) -> Path:
    """1s 440Hz wav 源（真文件——audio_key 走 stat 指纹）。"""
    src = root / "voice.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-t", "1", "-ac", "2", "-c:a", "pcm_s16le", str(src)], check=True)
    tl = {"version": 2,
          "project": {"fps": 30, "width": 1920, "height": 1080,
                      "duration_frames": 30},
          "sources": [{"id": "v", "path": str(src)}],
          "video_tracks": [],
          "subtitles": [],
          "audio_tracks": [{"name": "voice", "events": [
              {"src": "v", "src_in": 0, "in": 0, "out": 30, "volume": 1.0}]}]}
    if db is not None:
        tl["sources"][0]["hello_gain"] = {"db": db}
    p = root / "timeline.json"
    p.write_text(json.dumps(tl), encoding="utf-8")
    return p


class ExternalCfgTestBase(unittest.TestCase):
    """VTX_CONFIG 临时 config 驱动 external 注册态（隔离于本机 config.local.json）。"""

    def ctx_with(self, entry, db=3):
        cfg = self._tmp.name + "/cfg.json"
        _write_cfg(Path(cfg), entry)
        tl = _make_src(Path(self._tmp.name), db=db)
        env = mock.patch.dict(os.environ, {"VTX_CONFIG": cfg})
        env.start()
        self.addCleanup(env.stop)
        return Ctx(tl)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _validate_exits(self, c) -> str:
        out = StringIO()
        try:
            with redirect_stdout(out):
                validate(c)
        except SystemExit as e:
            self.assertEqual(e.code, 1)
            return out.getvalue()
        return out.getvalue()


class ThreeBranchSemantics(ExternalCfgTestBase):
    """§2.1 不变量 3：装/未装/禁用三分支报错文案（validate 真跑）。"""

    ENABLED = {"path": EXT_PATH, "enabled": True}

    def test_branch_active_passes_validate(self):
        c = self.ctx_with(self.ENABLED)
        out = self._validate_exits(c)
        self.assertIn("校验通过", out)           # 分支 1：已注册 + 已装启用 = 合法生效
        self.assertIn("hello-plugin", [p["name"] for p in pl_hooks.active_plugins(c)])

    def test_branch_not_installed_errors_with_hint(self):
        # external 插件的「注册面」= config 条目（键经 manifest 可反查）；安装态由
        # plugin_state 判定——未装分支按任务口径 mock registry 状态驱动（真实
        # 「条目整个移除」= 键退化为未知键警告，见下一用例）
        c = self.ctx_with(self.ENABLED)
        out = self._validate_exits(c)
        with mock.patch("vtx.pluginlib.registry.plugin_state",
                        return_value="not_installed"), \
                redirect_stdout(io.StringIO()):
            errs = []
            _scan_plugin_keys(c, errs)
        joined = "\n".join(errs)
        self.assertIn("hello_gain", joined)
        self.assertIn("未安装", joined)
        self.assertIn("vt plugins list", joined)  # 安装指引而非裸 Unknown key

    def test_entry_removed_degrades_to_unknown_key_warning(self):
        # 条目整个移除 = 未注册：键不认识 → 警告不阻断（分支 4；渲染走裸音频，
        # 指纹段消失强制重渲——缓存失效见 CacheIdentity）
        c = self.ctx_with(None)
        out = self._validate_exits(c)
        self.assertIn("校验通过", out)
        self.assertIn("未知键 'hello_gain'", out)

    def test_branch_disabled_errors_with_hint(self):
        c = self.ctx_with({"path": EXT_PATH, "enabled": False})
        out = self._validate_exits(c)
        self.assertIn("hello_gain", out)
        self.assertIn("禁用", out)               # 分支 3：绝不静默忽略


class CacheIdentity(ExternalCfgTestBase):
    """不变量 ①②：缓存键白名单 / 回执指纹段 / 卸载失效（验收门 6 对照）。"""

    ENABLED = {"path": EXT_PATH, "enabled": True}

    def test_key_value_changes_product_digest(self):
        c3 = self.ctx_with(self.ENABLED, db=3)
        c6 = self.ctx_with(self.ENABLED, db=6)
        p3, p6 = hello.product_path(c3, "v"), hello.product_path(c6, "v")
        self.assertNotEqual(p3, p6)
        self.assertIn("::hello:1.0.0:", p3.name)
        self.assertNotEqual(hello.value_digest({"db": 3}), hello.value_digest({"db": 6}))
        self.assertEqual(hello.value_digest({"db": 3}), hello.value_digest({"db": 3}))

    def test_enabled_whitelists_keys_and_fingerprints_plugin(self):
        c = self.ctx_with(self.ENABLED)
        stem = hello.product_path(c, "v").stem
        # 不变量 ①：产物键入 gc 存留白名单（budget_gate 渲染中不误杀）
        self.assertEqual(pl_hooks.contribute_keys(c), {stem})
        _, _, ak = hygiene._current_keys(c)
        self.assertIn(stem, ak)
        # 不变量 ②：回执指纹掺入插件段（同输入仅插件段有无 → 指纹必变；receipt_
        # fingerprint 为 sha256 摘要，段是哈希成分而非子串）
        d = pl_hooks.active_plugins_digest(c)
        self.assertTrue(d.startswith("plugins:hello-plugin@1.0.0:"), d)
        with mock.patch("vtx.pluginlib.hooks.active_plugins_digest", return_value=""):
            fp_without_segment = receipt_fingerprint(c)
        self.assertNotEqual(receipt_fingerprint(c), fp_without_segment)

    def test_uninstall_keys_leave_whitelist_and_fingerprint_changes(self):
        c_on = self.ctx_with(self.ENABLED)
        product = hello.product_path(c_on, "v")
        product.parent.mkdir(parents=True, exist_ok=True)
        product.write_bytes(b"RIFF placeholder")     # 模拟已产出的插件缓存
        # 录制侧指纹在卸载前定格（回执的 input_fingerprint 语义——Ctx 构造期算）
        fp_on = receipt_fingerprint(c_on)
        # 卸载：external 条目移除 → 新 Ctx 重算
        c_off = self.ctx_with(None)
        # ① 键出白名单 → gc 可回收
        self.assertEqual(pl_hooks.contribute_keys(c_off), set())
        plan = hygiene.gc_plan(c_off)
        reclaimed = [p for p, _, _ in plan["audio"]]
        self.assertTrue(any(p == product for p in reclaimed),
                        f"插件产物应进 gc 回收清单：{reclaimed}")
        # ② 指纹段消失 → 装卸两侧互失配（旧回执不放行陈旧产物）
        self.assertEqual(pl_hooks.active_plugins_digest(c_off), "")
        self.assertNotEqual(fp_on, receipt_fingerprint(c_off))


class PrepareSourceContract(ExternalCfgTestBase):
    """缝 A 契约 v1.1：真实 ffmpeg 路径 + fail-hard。"""

    def _ctx(self, db=3, declare=True):
        cfg = self._tmp.name + "/cfg.json"
        _write_cfg(Path(cfg), {"path": EXT_PATH, "enabled": True})
        tl = _make_src(Path(self._tmp.name), db=db if declare else None)
        env = mock.patch.dict(os.environ, {"VTX_CONFIG": cfg})
        env.start()
        self.addCleanup(env.stop)
        return Ctx(tl)

    @staticmethod
    def _mean_db(path: Path) -> float:
        r = subprocess.run(["ffmpeg", "-i", str(path), "-af", "volumedetect",
                            "-f", "null", "-"], capture_output=True, text=True)
        m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
        return float(m.group(1))

    def test_real_ffmpeg_product_and_raw_key_untouched(self):
        c = self._ctx(db=6)
        out = hello.prepare_source(c, "v")
        self.assertIsNotNone(out)
        self.assertTrue(out.is_file(), "产物应在盘")
        # 缓存命中：同 ctx 二次调用直返不重产（不触环境探测）
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(hello.prepare_source(c, "v"), out)
        r = subprocess.run(["ffprobe", "-v", "error", "-show_streams",
                            "-of", "json", str(out)], capture_output=True, text=True)
        s = json.loads(r.stdout)["streams"][0]
        self.assertEqual((s["sample_rate"], s["channels"]), ("48000", 2))
        self.assertEqual(s["codec_name"], "pcm_s16le")
        # 裸键不污染：prepare_source 直产插件键，裸提取键未被创建/写入
        from vtx.api import audio_key
        self.assertFalse((c.cache / "audio" / f"{audio_key(c, 'v')}.wav").exists())
        # 增益真实生效：+6dB 产物能量 > -40dB 产物能量（独立 ctx——重生成源会换
        # audio_key，属内容寻址语义，两产物各自有效即可）
        loud = self._mean_db(out)
        c2 = self._ctx(db=-40)
        quiet = self._mean_db(hello.prepare_source(c2, "v"))
        self.assertGreater(loud, quiet + 30)

    def test_no_key_returns_none(self):
        c = self._ctx(declare=False)
        self.assertIsNone(hello.prepare_source(c, "v"))

    def test_missing_ffmpeg_fails_hard(self):
        c = self._ctx(db=3)
        err = StringIO()
        with mock.patch("shutil.which", return_value=None), \
                redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                hello.prepare_source(c, "v")
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("fail-hard", err.getvalue())   # 打印缺什么，不回落裸音频


class ManifestAndHealth(unittest.TestCase):
    def test_manifest_validates_and_health_exits_zero(self):
        data, errors = load_manifest(PLUGIN_DIR)
        self.assertEqual(errors, [], errors)
        self.assertEqual(data["extends"]["hooks"], ["prepare_source"])
        r = subprocess.run(["env", "python3", str(PLUGIN_DIR / "health_check.py")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("healthy", r.stdout)


if __name__ == "__main__":
    unittest.main()
