#!/usr/bin/env python3
"""tests/test_denoise_v2.py —— R-20 P3 升级链(v2)集成回归用例(§7.1 #9/#10)。

P3 增量覆盖面(P2 版 test_denoise.py 之上,兼容共存零改动):
  ⑥ 链 v2 三步形态冻结面:POST 滤波串 afftdn=nr=12:nf=-25 / TRIM_48K=1200
     (25ms×48k 链内延迟补偿)/ v1 NOTCH 串(anequalizer/highpass)零残留;
  ⑦ CLI 命令面:denoise_voice.py <in> <out> argparse 化;环境缺失 fail-hard
     返回 1 且不起链;CLI 缺省 mono(chain 不带 stereo);
  ⑧ fail-hard 传播:链中任一子进程非零 → SystemExit 1,不产半链产物;
  ⑨ stereo 48k 契约:插件产物终步 -ac 2;CLI 缺省无 -ac(=mono);
  ⑩ 缓存键 2.0.0:产物键含 ::denoise:2.0.0:;plugin.json/__init__.py/
     registry.json/README 四处版本一致(#12 P3 复核机检化);
  ⑪ symlink 契约(#10):scripts/denoise_voice.py、scripts/denoise_init.sh 为
     指向 plugins/denoise/ 真身的兼容 symlink(指向不改变)。
运行:repo 根 `bash scripts/run_all_tests.sh`(批次三自动发现);本插件内独立跑:
cd plugins/denoise && env python3 -m unittest discover -s tests -t . -v
（本机纪律:env python3 前缀,AppImage argv[0] 劫持,见全局 AGENTS.md）。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[3]   # plugins/denoise/tests → repo 根
sys.path.insert(0, str(REPO))

from plugins import denoise as dn            # noqa: E402
from plugins.denoise import denoise_voice as dv  # noqa: E402

from vtx.infra import Ctx                    # noqa: E402


def _proj(root: Path, denoise_value=True):
    """最小工程(与 test_denoise.py 同构;真文件供 stat 指纹)。"""
    src = root / "voice.wav"
    src.write_bytes(b"RIFF dummy payload for stat digest" * 8)
    sources = [{"id": "v", "path": str(src)}]
    if denoise_value is not ...:
        sources[0]["denoise"] = denoise_value
    tl = {"version": 2,
          "project": {"fps": 30, "duration_frames": 90, "width": 640, "height": 360},
          "sources": sources, "video_tracks": [], "subtitles": [], "audio_tracks": []}
    p = root / "timeline.json"
    p.write_text(json.dumps(tl), encoding="utf-8")
    return p


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
            c = Ctx(_proj(root))
            p = dn.product_path(c, "v")
            calls = []

            def fake_run(cmd, **kw):
                calls.append([str(x) for x in cmd])
                if cmd[-1] == p:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(b"produced")
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


class FailHardPropagation(unittest.TestCase):
    """⑧ 链中任一子进程非零 → SystemExit 1,产物不落盘(不回落裸音频)。"""

    def test_step_failure_exits_without_product(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = Ctx(_proj(root))
            p = dn.product_path(c, "v")

            def failing_run(cmd, **kw):
                if cmd[0] == "ffmpeg" and str(cmd[-1]).endswith("normed.wav"):
                    r = mock.Mock()
                    r.returncode, r.stderr = 1, "boom at normalize"
                    return r
                raise AssertionError("首步失败后不得继续起后续子进程")

            with mock.patch("plugins.denoise.missing_env", return_value=[]), \
                 mock.patch("plugins.denoise.denoise_voice.subprocess.run",
                            side_effect=failing_run):
                with self.assertRaises(SystemExit) as cm:
                    dn.prepare_source(c, "v")
            self.assertEqual(cm.exception.code, 1)
            self.assertFalse(p.exists())     # 不产半链产物

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
        self.assertEqual(final[-3:], ["-c:a", "pcm_s16le", final[-1]])

    def test_cli_default_mono(self):
        final = self._final_cmd(stereo=False)
        self.assertNotIn("-ac", final)     # CLI 保持 mono(v1 既有语义零变化)


class CacheKeyV200(unittest.TestCase):
    """⑩ 缓存键 2.0.0 与四处版本一致(#12 P3 复核机检化)。"""

    def test_product_key_carries_v200(self):
        with tempfile.TemporaryDirectory() as d:
            c = Ctx(_proj(Path(d)))
            name = dn.product_path(c, "v").name
            self.assertIn("::denoise:2.0.0:", name)
            self.assertNotIn(":1.0.0:", name)   # v1 键形零残留

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


class SymlinkIntegrity(unittest.TestCase):
    """⑪ symlink 契约(#10):scripts/ 兼容 symlink 指向 plugins/denoise 真身。"""

    def test_scripts_symlinks_resolve_to_plugin(self):
        for name in ("denoise_voice.py", "denoise_init.sh"):
            link = REPO / "scripts" / name
            real = REPO / "plugins" / "denoise" / name
            self.assertTrue(link.is_symlink(), f"scripts/{name} 应为 symlink")
            self.assertEqual(link.resolve(), real.resolve())
            self.assertTrue(real.is_file(), f"真身 plugins/denoise/{name} 在盘")


if __name__ == "__main__":
    unittest.main()
