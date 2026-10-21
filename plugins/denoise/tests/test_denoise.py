#!/usr/bin/env python3
'Denoise plugin contracts: preparation, cache identity, audio chain and canonical CLI.'


# denoise contracts
# tests/test_denoise.py —— denoise 插件（缝 A，v2 §2.2）单元测试。
#
# 覆盖：
#   ① 产物路径语义（契约 v1.1）：{audio_key}__denoise_{version}_{digest} 命名 /
#      裸提取键不被污染 / 键值变 → 产物键变；
#   ② prepare_source 契约：未声明键 → None / 缓存命中 → 不再产出（环境缺失亦可
#      命中）/ 缺产物 + 环境缺失 → fail-hard（SystemExit 1，打印缺什么，不回落
#      裸音频）/ 缺产物 + 环境就绪 → 陷波→conda→48k 三步链命令序列（mock
#      subprocess，默认门）/ dry-run 不落产物；
#   ③ contribute_keys（不变量 ①）：仅声明键源的产物键；真实并入 hygiene.
#      _current_keys 且 gc_plan 不回收（budget_gate 渲染中不误杀的真实路径）；
#   ④ 缓存身份门（不变量 ②，真实插件路径非 mock 段）：denoise 键值变 →
#      active_plugins_digest 变 → receipt_fingerprint 变；无键时间线 → 摘要段
#      省略（""，v1 兼容）；
#   ⑤ 集成（VTX_TEST_DENOISE=1 门控）：真跑 conda 链 → 产物在盘且 48k stereo
#      pcm、二次调用缓存命中。
# 运行：repo 根 `bash scripts/run_all_tests.sh`（P2b 起含 plugins/*/tests 发现）；
# 本插件内独立跑：cd plugins/denoise && env python3 -m unittest discover -s tests -t . -v
# （本机 ZCode 环境必须 env python3 前缀：AppImage 劫持 argv[0]，见全局 AGENTS.md）。
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[3]   # plugins/denoise/tests → repo 根
sys.path.insert(0, str(REPO))

from plugins import denoise as dn            # noqa: E402（repo 根锚定后导入）

from vtx import hygiene                      # noqa: E402
from vtx import recovery                     # noqa: E402
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


def _write_pcm(path, samples=480):
    """Small valid output for real transaction inspection; no media tool."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(b"\0" * samples * 4)


class ProductPathSemantics(unittest.TestCase):
    def test_path_shape_and_bare_key_unpolluted(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = Ctx(_proj(root, denoise_value=True))
            p = dn.product_path(c, "v")
            from vtx.api import audio_key
            bare = c.cache / "audio" / f"{audio_key(c, 'v')}.wav"
            # P6（A-P1-1）：产物键尾部掺 12 位插件代码摘要（ vtx.api.plugin_code_digest）
            self.assertEqual(p.name,
                             f"{audio_key(c, 'v')}__denoise_{dn.VERSION}_"
                             f"{dn.value_digest(True)}_"
                             f"{dn.plugin_code_digest('denoise')}.wav")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"native-path-regression")
            self.assertIn(p.stem, dn.contribute_keys(c))
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
            dn.atomic_media(c, p, _write_pcm)
            # 环境缺失亦可命中缓存（产物已在盘，无须环境）
            with mock.patch("plugins.denoise.missing_env", return_value=["缺 X"]), \
                 mock.patch("plugins.denoise.denoise_voice.subprocess.run") as run:
                got = dn.prepare_source(c, "v")
            self.assertEqual(Path(got), p)
            run.assert_not_called()
            original = p.read_bytes()
            marker = recovery.completion_marker(p)
            certified = marker.read_bytes()
            for state in ("missing", "malformed", "changed-media"):
                with self.subTest(state=state):
                    p.write_bytes(original)
                    marker.write_bytes(certified)
                    if state == "missing":
                        marker.unlink()
                    elif state == "malformed":
                        marker.write_text("{}")
                    else:
                        _write_pcm(p, samples=240)
                    # Even finalized PCM is not completion evidence. Without a
                    # certificate the real environment gate must run, not hit.
                    with mock.patch.object(dn, "missing_env", return_value=["缺 X"]), \
                            mock.patch("subprocess.Popen", side_effect=AssertionError("unexpected child")):
                        with self.assertRaises(SystemExit) as failure:
                            dn.prepare_source(c, "v")
                    self.assertEqual(failure.exception.code, 1)
                    self.assertFalse(dn.media_cache_valid(p))

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
                if cmd[0] == "ffmpeg" and "-af" in cmd:
                    _write_pcm(cmd[-1])          # actual transaction staging
                return self._fake_result()

            with mock.patch("plugins.denoise.missing_env", return_value=[]), \
                 mock.patch("plugins.denoise.denoise_voice.subprocess.run",
                            side_effect=fake_run):
                got = dn.prepare_source(c, "v")
            self.assertEqual(Path(got), p)
            self.assertEqual(len(calls), 3)                 # 规范化 → conda → 终步
            # R-20 P2（v2 链）：v1 陷波级弃用（IIR 群延迟=分数相位失真，弃选
            # 理由在 denoise_voice.py 头注），首步改为 mono s16 规范化（无滤镜）。
            self.assertIn("ffmpeg", calls[0][0])
            self.assertNotIn("-af", calls[0])               # 首步零滤镜（v2）
            self.assertIn("-ac", calls[0])
            self.assertEqual(calls[0][calls[0].index("-ac") + 1], "1")  # mono
            self.assertEqual(str(calls[1][0]),
                             str(dn.conda_python()))        # conda denoise 解释器
            self.assertIn("resemble_enhance", calls[1][2])  # -c 推理代码
            final = calls[2]
            self.assertEqual(final[0], "ffmpeg")
            # R-20 P2（v2 链）：回 48k 并入 -af（aresample=48000），afftdn 算法延迟
            # 由链内 atrim 常量补偿（trim 须在 48k 域计数才与输入采样率无关），
            # 故 48k 契约改查 -af 串（v1 的独立 -ar 48000 元素形态废止）。
            self.assertIn("-af", final)
            af = final[final.index("-af") + 1]
            self.assertIn("aresample=48000", af)            # 回 48k（v2 形态）
            self.assertIn("atrim=start_sample=", af)        # 链内延迟补偿
            self.assertIn("-ac", final)                     # 插件产物 stereo 契约
            self.assertNotEqual(final[-1], str(p))          # 不直写最终产物
            self.assertEqual(Path(final[-1]).parent, p.parent)
            self.assertFalse(Path(final[-1]).exists())
            self.assertEqual(final[-3:-1], ["-f", "wav"])
            self.assertTrue(dn.media_cache_valid(p))

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
            self.assertIn("plugins:denoise@2.0.0:", seg1)  # R-20 P2: VERSION 2.0.0
            self.assertNotEqual(seg1, pl_hooks.active_plugins_digest(c2))
            self.assertEqual(pl_hooks.active_plugins_digest(c3), "")   # v1 形态
            fps = {receipt_fingerprint(c1), receipt_fingerprint(c2),
                   receipt_fingerprint(c3)}
            self.assertEqual(len(fps), 3)                 # 三态三指纹
            # 卸载回落（mock active→[]）：无段重算 ≠ 带段录制（旧回执被拒）
            with mock.patch("vtx.pluginlib.hooks.active_plugins", return_value=[]):
                fp_uninstalled = receipt_fingerprint(c1)
            self.assertNotEqual(fp_uninstalled, receipt_fingerprint(c1))


class StagesWiring(unittest.TestCase):
    """内核接线回归（stages 侧契约 v1.1 真插件路径，mock 链免 conda）：
    source_audio_extraction 产出侧与 _audio_graph 读取侧都解析到插件产物——
    裸提取键既不产出也不读取（内容寻址不污染；e2e 证据见本测试组注释：
    vtx prepare/audio --dry-run 的 -i 路径即插件产物）。"""

    def test_extraction_and_graph_read_plugin_product(self):
        from vtx.stages import _audio_graph, source_audio_extraction
        from vtx.api import audio_key
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = Ctx(_proj(root, denoise_value=True))
            p = dn.product_path(c, "v")

            def fake_run(cmd, **kw):
                if cmd[0] == "ffmpeg" and "-af" in cmd:
                    _write_pcm(cmd[-1])
                r = mock.Mock()
                r.returncode, r.stderr = 0, ""
                return r

            with mock.patch("plugins.denoise.missing_env", return_value=[]), \
                 mock.patch("plugins.denoise.denoise_voice.subprocess.run",
                            side_effect=fake_run):
                wav, cmd = source_audio_extraction(c, "v")
            self.assertEqual(Path(wav), p)
            self.assertIsNone(cmd)              # 插件产物 → 无裸提取命令
            # graph 读取点：事件源 -i = 插件产物路径；裸提取键不出现
            c.tl["audio_tracks"] = [{"name": "voice", "events": [
                {"src": "v", "in": 0, "out": 30, "volume": 1.0}]}]
            graph_cmd, _chains, n_ev, _duck_used = _audio_graph(c)
            joined = " ".join(map(str, graph_cmd))
            self.assertIn(str(p), joined)
            self.assertNotIn(str(c.cache / "audio" / f"{audio_key(c, 'v')}.wav"),
                             joined)
            self.assertEqual(n_ev, 1)


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


# denoise v2 contracts
# tests/test_denoise.py —— R-20 P3 升级链(v2)集成回归用例(§7.1 #9/#10)。
#
# P3 增量覆盖面(P2 版 test_denoise.py 之上,兼容共存零改动):
#   ⑥ 链 v2 三步形态冻结面:POST 滤波串 afftdn=nr=12:nf=-25 / TRIM_48K=1200
#      (25ms×48k 链内延迟补偿)/ v1 NOTCH 串(anequalizer/highpass)零残留;
#   ⑦ CLI 命令面:denoise_voice.py <in> <out> argparse 化;环境缺失 fail-hard
#      返回 1 且不起链;CLI 缺省 mono(chain 不带 stereo);
#   ⑧ fail-hard 传播:链中任一子进程非零 → SystemExit 1,不产半链产物;
#   ⑨ stereo 48k 契约:插件产物终步 -ac 2;CLI 缺省无 -ac(=mono);
#   ⑩ 缓存键 2.0.0:产物键含 __denoise_2.0.0_;plugin.json/__init__.py/
#      registry.json/README 四处版本一致(#12 P3 复核机检化);
#   ⑪ canonical 入口契约：plugins/denoise/ 内命令直接可用。
# 运行:repo 根 `bash scripts/run_all_tests.sh`(批次三自动发现);本插件内独立跑:
# cd plugins/denoise && env python3 -m unittest discover -s tests -t . -v
# （本机纪律:env python3 前缀,AppImage argv[0] 劫持,见全局 AGENTS.md）。
from plugins.denoise import denoise_voice as dv  # noqa: E402


class V2ChainFormFrozen(unittest.TestCase):
    """⑥ 链 v2 三步形态冻结面(P2 定案常数;改动即破坏 P2 判据复现基线)。"""

    def test_post_filter_string_and_trim_constant(self):
        self.assertEqual(dv.POST, "afftdn=nr=12:nf=-25")
        self.assertEqual(dv.TRIM_48K, 1200)
        # 补偿语义:25ms 算法延迟 × 48k 终步域 = 1200 样本(atrim 在 48k 域计数)
        self.assertEqual(dv.TRIM_48K, round(0.025 * 48000))

    def test_v1_notch_string_absent_from_chain(self):
        # v1 NOTCH 陷波串弃用(IIR 群延迟=分数相位失真,P2 对照实验在案):
        # AST 级断言——非 docstring 字符串常量零 NOTCH 残留(模块头注的弃用
        # 说明系文档记载,豁免;防可执行面回退)。
        import ast
        src = Path(dv.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    docstrings.add(id(node.body[0].value))
        hits = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and "anequalizer" in n.value and id(n) not in docstrings]
        self.assertEqual(hits, [],
                         f"非 docstring 面命中 anequalizer: {[h[:60] for h in hits]}")
        self.assertNotIn("anequalizer", dv.POST)

    def test_three_step_order_normalize_infer_post(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = Ctx(_proj(root, denoise_value=True))
            p = dn.product_path(c, "v")
            calls = []

            def fake_run(cmd, **kw):
                calls.append([str(x) for x in cmd])
                if cmd[0] == "ffmpeg" and "-af" in cmd:
                    _write_pcm(cmd[-1])
                r = mock.Mock()
                r.returncode, r.stderr = 0, ""
                return r

            with mock.patch("plugins.denoise.missing_env", return_value=[]), \
                 mock.patch("plugins.denoise.denoise_voice.subprocess.run",
                            side_effect=fake_run):
                dn.prepare_source(c, "v")
            self.assertEqual(len(calls), 3)
            self.assertEqual(calls[0][0], "ffmpeg")            # ① mono s16 规范化
            self.assertNotIn("-af", calls[0])
            self.assertIn("resemble_enhance", calls[1][2])     # ② conda Resemble
            self.assertIn("denoise", calls[1][2])
            af = calls[2][calls[2].index("-af") + 1]           # ③ afftdn+48k+补偿
            self.assertTrue(af.startswith(dv.POST + ",aresample=48000,"))
            self.assertIn(f"atrim=start_sample={dv.TRIM_48K}", af)
            self.assertIn("asetpts=PTS-STARTPTS", af)
            self.assertTrue(dn.media_cache_valid(p))


class CliSurface(unittest.TestCase):
    """⑦ CLI 命令面(argparse 化,RECP-20260919-002 后形态冻结)。"""

    def test_help_exit0_mentions_usage(self):
        r = mock.Mock()
        r.returncode, r.stderr = 0, ""
        parser = mock.Mock()
        with mock.patch("plugins.denoise.denoise_voice.argparse.ArgumentParser") as AP, \
             mock.patch("plugins.denoise.denoise_voice.missing_env", return_value=[]), \
             mock.patch("plugins.denoise.denoise_voice.chain") as ch:
            AP.return_value = parser
            parser.parse_args.return_value = mock.Mock(in_wav="a.wav", out_wav="b.wav")
            rc = dv.main(["a.wav", "b.wav"])
        self.assertEqual(rc, 0)
        parser.parse_args.assert_called_once_with(["a.wav", "b.wav"])  # 命令面 <in> <out>
        ch.assert_called_once_with("a.wav", "b.wav")                  # CLI 缺省 mono

    def test_missing_env_fail_hard_before_chain(self):
        with mock.patch("plugins.denoise.denoise_voice.missing_env",
                        return_value=["conda denoise 环境解释器 /x/py"]), \
             mock.patch("plugins.denoise.denoise_voice.chain") as ch, \
             mock.patch("sys.stderr"):
            rc = dv.main(["a.wav", "b.wav"])
        self.assertEqual(rc, 1)      # fail-hard:环境缺失返回 1
        ch.assert_not_called()       # 不起链、不产半链产物


class InferencePathArguments(unittest.TestCase):
    """Exercise generated code with inert modules, never model/FFmpeg execution."""

    def test_paths_remain_literal_argv_data(self):
        import contextlib
        import types

        for name in ("plain", 'double"quote', "single'quote", "line\nbreak",
                     "back\\slash", "中文 目录"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                temporary = root / ("temporary-" + name)
                weights = root / ("weights-" + name)
                calls = []
                with mock.patch.object(dv.tempfile, "TemporaryDirectory",
                                       return_value=contextlib.nullcontext(str(temporary))), \
                     mock.patch.dict(os.environ, {"RESEMBLE_WEIGHTS": str(weights)}), \
                     mock.patch.object(dv, "_sh", side_effect=calls.append), \
                     mock.patch("subprocess.Popen", side_effect=AssertionError("unexpected child")):
                    dv.chain(root / "input.wav", root / "output.wav", stereo=True)
                self.assertEqual(len(calls), 3)
                argv = calls[1]
                self.assertEqual(argv[1], "-c")
                self.assertEqual(argv[3:], [str(temporary / "normed.wav"),
                                           str(weights / "enhancer_stage2"),
                                           str(temporary / "denoised.wav")])
                self.assertEqual(calls[0][-1], argv[3])
                self.assertEqual(calls[2][calls[2].index("-i") + 1], argv[5])
                self.assertEqual(calls[2][calls[2].index("-ac") + 1], "2")
                self.assertNotIn(str(weights), argv[2])

                data, tensor, samples = object(), object(), object()
                output = mock.Mock()
                output.cpu.return_value.numpy.return_value = samples
                modules = {key: types.ModuleType(key) for key in (
                    "torch", "soundfile", "resemble_enhance",
                    "resemble_enhance.enhancer", "resemble_enhance.enhancer.inference")}
                modules["torch"].from_numpy = mock.Mock(return_value=tensor)
                sf = modules["soundfile"]
                sf.read = mock.Mock(return_value=(data, 44100))
                sf.write = mock.Mock()
                inference = modules["resemble_enhance.enhancer.inference"]
                inference.denoise = mock.Mock(return_value=(output, 44100))
                with mock.patch.dict(sys.modules, modules), \
                     mock.patch.object(sys, "argv", ["-c", *argv[3:]]):
                    exec(compile(argv[2], "<inference-path-test>", "exec"), {})
                sf.read.assert_called_once_with(argv[3], dtype="float32")
                modules["torch"].from_numpy.assert_called_once_with(data)
                inference.denoise.assert_called_once_with(
                    tensor, 44100, "cpu", run_dir=Path(argv[4]))
                sf.write.assert_called_once_with(argv[5], samples, 44100)


class FailHardPropagation(unittest.TestCase):
    """⑧ 链中任一子进程非零 → SystemExit 1,产物不落盘(不回落裸音频)。"""

    def test_step_failure_exits_without_product(self):
        for step, interrupted in ((1, False), (2, False), (3, False), (3, True)):
            with self.subTest(step=step, interrupted=interrupted), tempfile.TemporaryDirectory() as d:
                c = Ctx(_proj(Path(d), denoise_value=True))
                p = dn.product_path(c, "v")
                calls = []

                def failing_run(cmd, **kw):
                    calls.append(cmd)
                    if len(calls) == 3:
                        # Fully readable short PCM still must not be published
                        # if the final producer step reports failure/interruption.
                        _write_pcm(cmd[-1], samples=240)
                    if len(calls) == step:
                        if interrupted:
                            raise KeyboardInterrupt()
                        return subprocess.CompletedProcess(cmd, 1, "", "injected step failure")
                    self.assertLess(len(calls), step, "failure must stop the chain")
                    return subprocess.CompletedProcess(cmd, 0, "", "")

                with mock.patch.object(dn, "missing_env", return_value=[]), \
                        mock.patch("plugins.denoise.denoise_voice.subprocess.run", side_effect=failing_run), \
                        mock.patch("subprocess.Popen", side_effect=AssertionError("unexpected child")):
                    with self.assertRaises(KeyboardInterrupt if interrupted else SystemExit) as cm:
                        dn.prepare_source(c, "v")
                if not interrupted:
                    self.assertEqual(cm.exception.code, 1)
                self.assertEqual(len(calls), step)
                self.assertFalse(p.exists())
                self.assertFalse(recovery.completion_marker(p).exists())
                self.assertFalse(list(p.parent.glob(".*.tmp")))

                def repaired(cmd, **kw):
                    if cmd[0] == "ffmpeg" and "-af" in cmd:
                        _write_pcm(cmd[-1])
                    return subprocess.CompletedProcess(cmd, 0, "", "")

                with mock.patch.object(dn, "missing_env", return_value=[]), \
                        mock.patch("plugins.denoise.denoise_voice.subprocess.run", side_effect=repaired) as run:
                    self.assertEqual(dn.prepare_source(c, "v"), p)
                    self.assertEqual(run.call_count, 3)
                    self.assertEqual(dn.prepare_source(c, "v"), p)
                    self.assertEqual(run.call_count, 3)
                self.assertTrue(dn.media_cache_valid(p))

    def test_cli_step_failure_exits_nonzero(self):
        def failing_run(cmd, **kw):
            r = mock.Mock()
            r.returncode, r.stderr = 1, "boom"
            return r

        with mock.patch("plugins.denoise.denoise_voice.missing_env", return_value=[]), \
             mock.patch("plugins.denoise.denoise_voice.subprocess.run",
                        side_effect=failing_run):
            with self.assertRaises(SystemExit) as cm:
                dv.chain("/nonexistent/in.wav", "/tmp/never.wav")
        self.assertEqual(cm.exception.code, 1)


class Stereo48kContract(unittest.TestCase):
    """⑨ stereo 48k 契约:插件产物终步 -ac 2;CLI 缺省 mono(无 -ac 终步)。"""

    def _final_cmd(self, stereo):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.wav"
            calls = []

            def fake_run(cmd, **kw):
                calls.append([str(x) for x in cmd])
                r = mock.Mock()
                r.returncode, r.stderr = 0, ""
                return r

            with mock.patch("plugins.denoise.denoise_voice.subprocess.run",
                            side_effect=fake_run):
                dv.chain(Path(d) / "in.wav", out, stereo=stereo)
            return calls[-1]

    def test_plugin_product_stereo(self):
        final = self._final_cmd(stereo=True)
        self.assertIn("-ac", final)
        self.assertEqual(final[final.index("-ac") + 1], "2")
        self.assertIn("aresample=48000", final[final.index("-af") + 1])
        self.assertEqual(final[-5:], ["-c:a", "pcm_s16le", "-f", "wav", final[-1]])

    def test_cli_default_mono(self):
        final = self._final_cmd(stereo=False)
        self.assertNotIn("-ac", final)     # CLI 保持 mono(v1 既有语义零变化)


class CacheKeyV200(unittest.TestCase):
    """⑩ 缓存键 2.0.0 与四处版本一致(#12 P3 复核机检化)。"""

    def test_product_key_carries_v200(self):
        with tempfile.TemporaryDirectory() as d:
            c = Ctx(_proj(Path(d), denoise_value=True))
            name = dn.product_path(c, "v").name
            self.assertIn("__denoise_2.0.0_", name)
            self.assertNotIn("__denoise_1.0.0_", name)   # v1 键形零残留

    def test_version_consistency_four_places(self):
        # plugin.json / __init__.py / registry.json / README 四处 2.0.0;
        # 1.0.0 残留检查按修订十五豁免:registry docs {ver} 模板字面、README
        # 变更历史段 1.0.0 历史记载。
        self.assertEqual(dn.VERSION, "2.0.0")
        pj = json.loads((REPO / "plugins/denoise/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(pj["version"], "2.0.0")
        reg = json.loads((REPO / "plugins/registry.json").read_text(encoding="utf-8"))
        self.assertEqual(reg["plugins"]["denoise"]["version"], "2.0.0")
        readme = (REPO / "plugins/denoise/README.md").read_text(encoding="utf-8")
        self.assertIn("2.0.0", readme)
        for path, text in (("plugin.json", json.dumps(pj)), ("registry denoise 行",
                           json.dumps(reg["plugins"]["denoise"], ensure_ascii=False)),
                          ("__init__.py",
                           (REPO / "plugins/denoise/__init__.py").read_text(encoding="utf-8"))):
            self.assertNotIn("1.0.0", text, f"{path} 存在 1.0.0 残留")
        # 豁免面(修订十五):registry 其他插件自身版本号、README 变更历史段
        # 1.0.0 历史记载不在残留断言面(#12 口径=denoise 域四处)。


class CanonicalEntrypoints(unittest.TestCase):
    """⑪ canonical 入口位于插件目录，不依赖旧 scripts 转发器。"""

    def test_plugin_entrypoints_are_owned_files(self):
        for name in ("denoise_voice.py", "denoise_init.sh"):
            real = REPO / "plugins" / "denoise" / name
            self.assertFalse(real.is_symlink(), f"plugins/denoise/{name} owns its source")
            self.assertTrue(real.is_file(), f"真身 plugins/denoise/{name} 在盘")


if __name__ == "__main__":
    unittest.main()
