#!/usr/bin/env python3
"""tests/test_denoise.py —— denoise 插件（缝 A，v2 §2.2）单元测试。

覆盖：
  ① 产物路径语义（契约 v1.1）：{audio_key}::denoise:{version}:{digest} 命名 /
     裸提取键不被污染 / 键值变 → 产物键变；
  ② prepare_source 契约：未声明键 → None / 缓存命中 → 不再产出（环境缺失亦可
     命中）/ 缺产物 + 环境缺失 → fail-hard（SystemExit 1，打印缺什么，不回落
     裸音频）/ 缺产物 + 环境就绪 → 陷波→conda→48k 三步链命令序列（mock
     subprocess，默认门）/ dry-run 不落产物；
  ③ contribute_keys（不变量 ①）：仅声明键源的产物键；真实并入 hygiene.
     _current_keys 且 gc_plan 不回收（budget_gate 渲染中不误杀的真实路径）；
  ④ 缓存身份门（不变量 ②，真实插件路径非 mock 段）：denoise 键值变 →
     active_plugins_digest 变 → receipt_fingerprint 变；无键时间线 → 摘要段
     省略（""，v1 兼容）；
  ⑤ 集成（VTX_TEST_DENOISE=1 门控）：真跑 conda 链 → 产物在盘且 48k stereo
     pcm、二次调用缓存命中。
运行：repo 根 `bash scripts/run_all_tests.sh`（P2b 起含 plugins/*/tests 发现）；
本插件内独立跑：cd plugins/denoise && env python3 -m unittest discover -s tests -t . -v
（本机 ZCode 环境必须 env python3 前缀：AppImage 劫持 argv[0]，见全局 AGENTS.md）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[3]   # plugins/denoise/tests → repo 根
sys.path.insert(0, str(REPO))

from plugins import denoise as dn            # noqa: E402（repo 根锚定后导入）

from vtx import hygiene                      # noqa: E402
from vtx.cache_state import receipt_fingerprint   # noqa: E402
from vtx.infra import Ctx                    # noqa: E402
from vtx.pluginlib import hooks as pl_hooks  # noqa: E402


def _proj(root: Path, denoise_value=..., second_declaring=False):
    """最小工程：一源（真文件——audio_key 走 stat 指纹）+ 可选第二声明源。"""
    src = root / "voice.wav"
    src.write_bytes(b"RIFF dummy payload for stat digest" * 8)
    sources = [{"id": "v", "path": str(src)}]
    if denoise_value is not ...:
        sources[0]["denoise"] = denoise_value
    if second_declaring:
        src2 = root / "voice2.wav"
        src2.write_bytes(b"second source payload" * 8)
        sources.append({"id": "v2", "path": str(src2), "denoise": True})
    tl = {"version": 2,
          "project": {"fps": 30, "duration_frames": 90, "width": 640, "height": 360},
          "sources": sources, "video_tracks": [], "subtitles": [], "audio_tracks": []}
    p = root / "timeline.json"
    p.write_text(json.dumps(tl), encoding="utf-8")
    return p


class ProductPathSemantics(unittest.TestCase):
    def test_path_shape_and_bare_key_unpolluted(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = Ctx(_proj(root, denoise_value=True))
            p = dn.product_path(c, "v")
            from vtx.api import audio_key
            bare = c.cache / "audio" / f"{audio_key(c, 'v')}.wav"
            self.assertEqual(p.name,
                             f"{audio_key(c, 'v')}::denoise:{dn.VERSION}:"
                             f"{dn.value_digest(True)}.wav")
            self.assertNotEqual(p, bare)   # 产物键 ≠ 裸提取键（内容寻址不污染）

    def test_value_change_changes_path(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c1 = Ctx(_proj(root, denoise_value={"strength": 0.5}))
            c2 = Ctx(_proj(root, denoise_value={"strength": 0.9}))
            self.assertNotEqual(dn.product_path(c1, "v").name,
                                dn.product_path(c2, "v").name)
            # 值参与参数摘要（hooks._plugin_params_digest 同源收集）
            self.assertNotEqual(pl_hooks._plugin_params_digest(c1, "denoise"),
                                pl_hooks._plugin_params_digest(c2, "denoise"))


class PrepareSourceContract(unittest.TestCase):
    def _fake_result(self):
        r = mock.Mock()
        r.returncode = 0
        r.stderr = ""
        return r

    def test_undeclared_source_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            c = Ctx(_proj(Path(d)))          # 不写 denoise 键
            self.assertIsNone(dn.prepare_source(c, "v"))
            self.assertIsNone(dn.prepare_source(c, "no-such-sid"))

    def test_cache_hit_returns_without_chain_or_env(self):
        with tempfile.TemporaryDirectory() as d:
            c = Ctx(_proj(Path(d), denoise_value=True))
            p = dn.product_path(c, "v")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"cached")
            # 环境缺失亦可命中缓存（产物已在盘，无须环境）
            with mock.patch("plugins.denoise.missing_env", return_value=["缺 X"]), \
                 mock.patch("plugins.denoise.denoise_voice.subprocess.run") as run:
                got = dn.prepare_source(c, "v")
            self.assertEqual(Path(got), p)
            run.assert_not_called()

    def test_missing_env_fails_hard(self):
        with tempfile.TemporaryDirectory() as d:
            c = Ctx(_proj(Path(d), denoise_value=True))
            with mock.patch("plugins.denoise.missing_env",
                            return_value=["conda denoise 环境解释器 /x/py（修复：init）"]), \
                 mock.patch("plugins.denoise.denoise_voice.subprocess.run") as run:
                with self.assertRaises(SystemExit) as cm:
                    dn.prepare_source(c, "v")
            self.assertEqual(cm.exception.code, 1)   # fail-hard：不回落裸音频
            run.assert_not_called()

    def test_production_runs_three_step_chain(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = Ctx(_proj(root, denoise_value={"notch": "v1"}))
            p = dn.product_path(c, "v")
            calls = []

            def fake_run(cmd, **kw):
                calls.append(cmd)
                if str(cmd[-1]) == str(p):       # 终步 ffmpeg 落产物
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(b"produced")
                return self._fake_result()

            with mock.patch("plugins.denoise.missing_env", return_value=[]), \
                 mock.patch("plugins.denoise.denoise_voice.subprocess.run",
                            side_effect=fake_run):
                got = dn.prepare_source(c, "v")
            self.assertEqual(Path(got), p)
            self.assertEqual(len(calls), 3)                 # 陷波 → conda → 回 48k
            notch = " ".join(calls[0])
            self.assertIn("ffmpeg", calls[0][0])
            self.assertIn("highpass=f=80", notch)           # 陷波滤波链
            self.assertIn("-ac", calls[0])                  # 陷波步 mono
            self.assertIn("-af", calls[0])
            self.assertEqual(str(calls[1][0]),
                             str(dn.conda_python()))        # conda denoise 解释器
            self.assertIn("resemble_enhance", calls[1][2])  # -c 推理代码
            final = calls[2]
            self.assertEqual(final[0], "ffmpeg")
            self.assertIn("48000", final)                   # 回 48k
            self.assertIn("-ac", final)                     # 插件产物 stereo 契约
            self.assertEqual(final[-1], str(p))             # 直接落产物键

    def test_dry_run_no_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            c = Ctx(_proj(Path(d), denoise_value=True))
            c.dry = True
            with mock.patch("plugins.denoise.missing_env", return_value=[]), \
                 mock.patch("plugins.denoise.denoise_voice.subprocess.run") as run:
                got = dn.prepare_source(c, "v")
            self.assertEqual(Path(got), dn.product_path(c, "v"))
            self.assertFalse(dn.product_path(c, "v").exists())
            run.assert_not_called()


class InvariantOneKeys(unittest.TestCase):
    """不变量 ① 真实路径：contribute_keys → _current_keys 并入 → gc_plan 不回收。"""

    def test_contribute_keys_only_declaring_sources(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = Ctx(_proj(root, denoise_value=True, second_declaring=True))
            keys = dn.contribute_keys(c)
            self.assertEqual(keys, {dn.product_path(c, "v").stem,
                                    dn.product_path(c, "v2").stem})

    def test_product_whitelisted_in_gc_plan(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = Ctx(_proj(root, denoise_value=True))
            p = dn.product_path(c, "v")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"denoised product")
            stale = p.parent / "deadbeefcafe.wav"
            stale.write_bytes(b"stale")
            _gfx, _seg, ak = hygiene._current_keys(c)
            self.assertIn(p.stem, ak)                     # 真实并入 ak 集合
            plan = hygiene.gc_plan(c)
            audio_kills = [str(x[0]) for x in plan["audio"]]
            self.assertNotIn(str(p), audio_kills)        # budget_gate 不误杀
            self.assertIn(str(stale), audio_kills)       # 键外旧产物照常回收


class InvariantTwoFingerprint(unittest.TestCase):
    """不变量 ② 真实路径：键值变 → 摘要段变 → 回执指纹变；无键 → 段省略。"""

    def test_digest_and_fingerprint_follow_value(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c1 = Ctx(_proj(root, denoise_value={"a": 1}))
            c2 = Ctx(_proj(root, denoise_value={"a": 2}))
            c3 = Ctx(_proj(root))                         # 无插件键
            seg1 = pl_hooks.active_plugins_digest(c1)
            self.assertIn("plugins:denoise@1.0.0:", seg1)
            self.assertNotEqual(seg1, pl_hooks.active_plugins_digest(c2))
            self.assertEqual(pl_hooks.active_plugins_digest(c3), "")   # v1 形态
            fps = {receipt_fingerprint(c1), receipt_fingerprint(c2),
                   receipt_fingerprint(c3)}
            self.assertEqual(len(fps), 3)                 # 三态三指纹
            # 卸载回落（mock active→[]）：无段重算 ≠ 带段录制（旧回执被拒）
            with mock.patch("vtx.pluginlib.hooks.active_plugins", return_value=[]):
                fp_uninstalled = receipt_fingerprint(c1)
            self.assertNotEqual(fp_uninstalled, receipt_fingerprint(c1))


@unittest.skipUnless(os.environ.get("VTX_TEST_DENOISE") == "1",
                     "可选集成测试：VTX_TEST_DENOISE=1 且需 conda denoise 环境 + "
                     "Resemble 权重（真实推理链，约数十秒）")
class DenoiseChainIntegration(unittest.TestCase):
    def test_real_chain_produces_48k_stereo(self):
        ff = shutil.which("ffmpeg")
        assert ff
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "voice.wav"                     # 0.5s 正弦（48k stereo）
            subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=0.5", "-ar", "48000",
                            "-ac", "2", "-c:a", "pcm_s16le", str(src)], check=True)
            tl = {"version": 2,
                  "project": {"fps": 30, "duration_frames": 15,
                              "width": 640, "height": 360},
                  "sources": [{"id": "v", "path": str(src), "denoise": True}],
                  "video_tracks": [], "subtitles": [], "audio_tracks": []}
            p = root / "timeline.json"
            p.write_text(json.dumps(tl), encoding="utf-8")
            c = Ctx(p)
            self.assertEqual(dn.missing_env(), [])
            got = Path(dn.prepare_source(c, "v"))
            self.assertTrue(got.is_file())
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                 str(got)], capture_output=True, text=True)
            st = json.loads(probe.stdout)["streams"][0]
            self.assertEqual(st["sample_rate"], "48000")
            self.assertEqual(st["channels"], 2)
            self.assertEqual(st["codec_name"], "pcm_s16le")
            # 二次调用 = 缓存命中（不再起子进程）
            with mock.patch("plugins.denoise.denoise_voice.subprocess.run") as run:
                again = dn.prepare_source(c, "v")
            self.assertEqual(Path(again), got)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
