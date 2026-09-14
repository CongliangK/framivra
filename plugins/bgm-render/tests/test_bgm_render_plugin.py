#!/usr/bin/env python3
"""tests/test_bgm_render_plugin.py —— bgm-render 插件（缝 A prepare + 缝 C probe）测试。

覆盖：
  ① probe_source：无键 → None；有键 → facts（nb_frames=ceil(duration×fps)、
    has_audio、has_video=False）；同 Ctx memo（伪 CLI 只被调一次）；
  ② prepare_source：真 ffmpeg 转换链（44.1k 母带 → 48k stereo s16 与裸提取同
    格式）；缓存命中零渲染（伪 CLI 调用计数不增）；
  ③ 产物键身份：改 bgm 键值 / 改 $ref 拆分件内容 / 改 extra_deps 件内容 /
    改工程字节 → param_digest 翻转（自动重渲不出陈旧缓存）；
  ④ validate 钩子：path 非 .json / output 越域 / 未知参数 / extra_deps 非法；
  ⑤ contribute_keys：全部 bgm 源产物键；
  ⑥ 三分支：registry mock not_installed → stages._scan_plugin_keys 报「未安装」；
  ⑦ 真总线：hooks.run_probe_source / run_prepare_source / run_validate 分发。

伪 bgm CLI：bash 桩（show structure 打 duration、render 用 ffmpeg sine 产
mix_master/mix_premaster.wav），经 VTX_CONFIG 的 bgm.cli 注入——不依赖真
music_tools（集成验证另有 VTX_TEST_BGM_INTEG=1 门控例，本机 bgm 在盘时跑真链）。
运行：repo 根 `bash scripts/run_all_tests.sh`；本插件内独立跑：
cd plugins/bgm-render && env python3 -m unittest discover -s tests -t . -v
（本机 ZCode 环境必须 env python3 前缀：AppImage 劫持 argv[0]，见全局 AGENTS.md）。
"""
import importlib
import json
import math
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[3]   # plugins/bgm-render/tests → repo 根
sys.path.insert(0, str(REPO))

_bgm = importlib.import_module("plugins.bgm-render")

from vtx.pluginlib import hooks as pl_hooks   # noqa: E402

FAKE_BGM = r'''#!/bin/bash
echo "$@" >> "$FAKE_LOG"
cmd="$1"; shift
case "$cmd" in
  show)
    proj="${@: -1}"
    dur=$(cat "$(dirname "$proj")/duration.txt" 2>/dev/null || echo 4.0)
    printf '{\n  "duration_s": %s\n}\n' "$dur"
    ;;
  render)
    proj="$1"; out=""
    while [ $# -gt 0 ]; do
      case "$1" in -o) out="$2" ;; esac
      shift
    done
    dur=$(cat "$(dirname "$proj")/duration.txt" 2>/dev/null || echo 4.0)
    ffmpeg -v error -y -f lavfi -i "sine=frequency=440:sample_rate=44100:duration=$dur" \
      -ac 2 "$out/mix_master.wav"
    ffmpeg -v error -y -f lavfi -i "sine=frequency=330:sample_rate=44100:duration=$dur" \
      -ac 2 "$out/mix_premaster.wav"
    printf '{\n  "master": "%s/mix_master.wav",\n  "premaster": "%s/mix_premaster.wav",\n  "duration_s": %s,\n  "qc_ok": true\n}\n' \
      "$out" "$out" "$dur"
    ;;
  *) echo "unknown cmd: $cmd" >&2; exit 2 ;;
esac
'''


class _Ctx:
    """prepare/probe 消费的最小上下文（tl/fps/src_path/cache/run/dry 鸭子类型）。"""

    def __init__(self, root: Path, tl: dict, fps: int = 30):
        self.tl = tl
        self.root = root
        self.fps = fps
        self.cache = root / "cache"
        self.dry = False
        self._paths = {s["id"]: s["path"] for s in tl.get("sources", [])}

    def src_path(self, sid):
        return self.root / self._paths[sid]

    def run(self, cmd, need=True):
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0 and need:
            raise RuntimeError((r.stderr or "")[-500:])
        return r


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "music").mkdir()
        # 伪 bgm CLI + 调用日志
        self.log = self.root / "bgm_calls.log"
        fake = self.root / "fakebgm"
        fake.write_text(FAKE_BGM, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        cfg = self.root / "vt_config.json"
        cfg.write_text(json.dumps({"bgm": {"cli": str(fake)}}), encoding="utf-8")
        self._old_env = os.environ.get("VTX_CONFIG")
        os.environ["VTX_CONFIG"] = str(cfg)
        os.environ["FAKE_LOG"] = str(self.log)
        # 音乐工程（含 $ref 拆分件）+ 时长标记
        self.ref_file = self.root / "music" / "strings_part.json"
        self.ref_file.write_text('{"notes": [1, 2, 3]}', encoding="utf-8")
        self.proj = self.root / "music" / "ep.json"
        self.proj.write_text(json.dumps({
            "name": "ep", "meta": {"spec_version": "2", "bpm": 120},
            "tracks": [{"name": "strings", "$ref": "strings_part.json"},
                       {"name": "kick", "engine": "synth"}],
        }, ensure_ascii=False), encoding="utf-8")
        (self.root / "music" / "duration.txt").write_text("4.0", encoding="utf-8")

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("VTX_CONFIG", None)
        else:
            os.environ["VTX_CONFIG"] = self._old_env
        os.environ.pop("FAKE_LOG", None)
        self._tmp.cleanup()

    def _tl(self, bgm=...):
        src = {"id": "bgm_main", "path": "music/ep.json"}
        if bgm is not ...:
            src["bgm"] = bgm
        return {"version": 2,
                "project": {"fps": 30, "duration_frames": 120,
                            "width": 640, "height": 360},
                "sources": [src], "video_tracks": [], "subtitles": [],
                "audio_tracks": []}

    def _ctx(self, bgm=...):
        return _Ctx(self.root, self._tl(bgm))

    def _calls(self):
        try:
            return self.log.read_text().splitlines()
        except OSError:
            return []


class ProbeSource(_Base):
    def test_none_without_key(self):
        self.assertIsNone(_bgm.probe_source(self._ctx(), "bgm_main"))
        self.assertEqual(self._calls(), [])

    def test_facts_with_key_and_memo(self):
        c = self._ctx(True)
        f1 = _bgm.probe_source(c, "bgm_main")
        self.assertEqual(f1["nb_frames"], 120)      # ceil(4.0 × 30)
        self.assertTrue(f1["has_audio"])
        self.assertFalse(f1["has_video"])
        f2 = _bgm.probe_source(c, "bgm_main")
        self.assertEqual(f1, f2)
        self.assertEqual(len(self._calls()), 1, "同 Ctx memo：CLI 只跑一次")

    def test_duration_respected(self):
        (self.root / "music" / "duration.txt").write_text("2.5", encoding="utf-8")
        f = _bgm.probe_source(self._ctx(True), "bgm_main")
        self.assertEqual(f["nb_frames"], math.ceil(2.5 * 30))


class PrepareSource(_Base):
    def test_render_and_cache_hit(self):
        c = self._ctx(True)
        out = _bgm.prepare_source(c, "bgm_main")
        self.assertTrue(out.is_file(), "产物应已落盘")
        self.assertEqual(len(self._calls()), 1, "首次 prepare 应触发一次 render")
        with wave.open(str(out), "rb") as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()),
                             (48000, 2, 2), "须转 48k stereo s16（裸提取同格式）")
        out2 = _bgm.prepare_source(c, "bgm_main")
        self.assertEqual(out2, out)
        self.assertEqual(len(self._calls()), 1, "缓存命中不得再渲染")

    def test_output_param_premaster(self):
        c = self._ctx({"output": "premaster"})
        out = _bgm.prepare_source(c, "bgm_main")
        self.assertTrue(out.is_file())

    def test_dry_run_no_render(self):
        c = self._ctx(True)
        c.dry = True
        out = _bgm.prepare_source(c, "bgm_main")
        self.assertFalse(out.exists())
        self.assertEqual(self._calls(), [])


class DigestIdentity(_Base):
    def test_digest_flips_on_key_value(self):
        d1 = _bgm.param_digest(self._ctx({"output": "master"}), "bgm_main")
        d2 = _bgm.param_digest(self._ctx({"output": "premaster"}), "bgm_main")
        self.assertNotEqual(d1, d2)

    def test_digest_flips_on_ref_content(self):
        d1 = _bgm.param_digest(self._ctx(True), "bgm_main")
        self.ref_file.write_text('{"notes": [1, 2, 3, 4]}', encoding="utf-8")
        d2 = _bgm.param_digest(self._ctx(True), "bgm_main")
        self.assertNotEqual(d1, d2, "$ref 件内容必须翻转产物键")

    def test_digest_flips_on_extra_deps(self):
        dep = self.root / "music" / "sample_pack.bin"
        dep.write_bytes(b"v1")
        c1 = self._ctx({"extra_deps": ["sample_pack.bin"]})
        d1 = _bgm.param_digest(c1, "bgm_main")
        dep.write_bytes(b"v2")
        d2 = _bgm.param_digest(c1, "bgm_main")
        self.assertNotEqual(d1, d2)
        # 工程字节本身变化 → audio_key 侧翻转（内核 media_stat_digest），另测键名变化
        p = _bgm.product_path(self._ctx(True), "bgm_main")
        self.proj.write_text(self.proj.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        p2 = _bgm.product_path(self._ctx(True), "bgm_main")
        self.assertNotEqual(p, p2, "工程字节变化须翻转 audio_key（内容寻址）")


class ValidateHook(_Base):
    def test_clean_passes(self):
        for good in (True, {}, {"output": "master"},
                     {"output": "premaster", "extra_deps": ["a.json"]}):
            self.assertEqual(_bgm.validate(_Ctx(self.root, self._tl(good))), [])

    def test_bad_shapes_fail(self):
        cases = ["yes", 42,
                 {"output": "loud"},            # output 越域
                 {"unknown": 1},                # 未知参数
                 {"extra_deps": "a.json"}]      # extra_deps 非列表
        for bad in cases:
            errs = _bgm.validate(_Ctx(self.root, self._tl(bad)))
            self.assertTrue(errs, f"期望报错：{bad!r}")

    def test_path_must_be_json(self):
        tl = self._tl(True)
        tl["sources"][0]["path"] = "music/final.wav"
        errs = _bgm.validate(_Ctx(self.root, tl))
        self.assertTrue(any(".json" in e for e in errs))


class InvariantsAndBranches(_Base):
    def test_contribute_keys(self):
        c = self._ctx({"output": "master"})
        keys = _bgm.contribute_keys(c)
        self.assertEqual(len(keys), 1)
        (key,) = keys
        self.assertIn("::bgm:", key)

    def test_three_branch_not_installed(self):
        from vtx.stages import _scan_plugin_keys
        c = self._ctx(True)
        errs = []
        with mock.patch("vtx.pluginlib.registry.plugin_state",
                        return_value="not_installed"):
            _scan_plugin_keys(c, errs)
        self.assertTrue(any("未安装" in e and "bgm" in e for e in errs))

    def test_bus_dispatch_probe_prepare(self):
        c = self._ctx(True)
        f = pl_hooks.run_probe_source(c, "bgm_main")
        self.assertIsNotNone(f)
        out = pl_hooks.run_prepare_source(c, "bgm_main")
        self.assertIsNotNone(out)
        self.assertTrue(Path(out).is_file())


class RealChainIntegration(_Base):
    """真 music_tools 链（本机 bgm CLI 在盘时跑；否则 skip）。"""

    def test_real_minimal_project_end_to_end(self):
        if os.environ.get("VTX_TEST_BGM_INTEG") != "1":
            self.skipTest("VTX_TEST_BGM_INTEG=1 门控（真 bgm render，需 music_tools）")
        src = Path("~/文档/01_活跃项目/music_tools/examples/minimal.json").expanduser()
        if not src.is_file():
            self.skipTest(f"minimal 示例不在盘：{src}")
        os.environ.pop("VTX_CONFIG", None)
        proj = self.root / "minimal.json"
        proj.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        tl = {"version": 2,
              "project": {"fps": 30, "duration_frames": 120,
                          "width": 640, "height": 360},
              "sources": [{"id": "bgm_main", "path": "minimal.json", "bgm": True}],
              "video_tracks": [], "subtitles": [], "audio_tracks": []}
        c = _Ctx(self.root, tl)
        f = _bgm.probe_source(c, "bgm_main")
        self.assertEqual(f["nb_frames"], 120)     # minimal duration 4.0s × 30fps
        out = _bgm.prepare_source(c, "bgm_main")
        self.assertTrue(out.is_file())
        with wave.open(str(out), "rb") as w:
            self.assertEqual((w.getframerate(), w.getnchannels()), (48000, 2))


if __name__ == "__main__":
    unittest.main()
