#!/usr/bin/env python3
"""tests/test_bgm_render_plugin.py —— bgm-render 插件（缝 A prepare + 缝 C probe）测试。

覆盖：
  ① probe_source：无键 → None；有键 → facts（nb_frames=ceil(duration×fps)、
    has_audio、has_video=False）；同 Ctx memo（伪 CLI 只被调一次）；
  ② prepare_source：真 ffmpeg 转换链（44.1k 母带 → 48k stereo s16 与裸提取同
    格式）；产物键缓存命中零渲染（伪 CLI 调用计数不增）；
  ③ 产物键身份：改 bgm 键值 / 改 $ref 拆分件内容 / 改 extra_deps 件内容 /
    改工程字节 → param_digest 翻转（自动重渲不出陈旧缓存）；
  ④ validate 钩子：path 非 .json / output 越域 / 未知参数 / extra_deps 非法；
    stem:<轨名> 值域（合法轨名通过、路径穿透/空轨名/非字符串/**尾随换行**拒绝
    ——fullmatch 修复 match+$ 的 newline 绕过）；
  ⑤ contribute_keys：全部 bgm 源产物键；
  ⑥ 三分支：registry mock not_installed → stages._scan_plugin_keys 报「未安装」；
  ⑦ 真总线：hooks.run_probe_source / run_prepare_source / run_validate 分发；
  ⑧ 共享渲染缓存（v1.1.0）：同工程多源（stem/stem、master/stem 混排）只触发
    **恰好一次** bgm render；render_digest 轨名不变性 + 工程字节翻转；分轨
    不存在 fail-hard 列可用轨名；缓存命中零渲染（产物被回收后仍零渲染）；
  ⑨ v1.2.0（R1-F 修复批）：目录型 extra_deps 递归内容签名（伴生件豁免 + 护栏
    降级 DIRL 清单签名）；marker 完好但产物残缺（缺文件/空文件）自愈覆盖重渲
    （错轨名不触发白渲）；gc 键级回收陈旧 render_digest 目录（在用保活、实删后
    重跑零渲染）；config bgm.cli 指向不存在/不可执行路径落下一通道不裸崩。

伪 bgm CLI：bash 桩（show structure 打 duration；render 用 ffmpeg sine 产
mix_master/mix_premaster.wav + tracks.txt 每轨的 stems/<轨>.bus.wav（48k f32，
对齐 music_tools mixer 总线分轨规格）+ 引擎级 stems 映射的结果 JSON），经
VTX_CONFIG 的 bgm.cli 注入——不依赖真 music_tools（集成验证另有
VTX_TEST_BGM_INTEG=1 门控例，本机 bgm 在盘时跑真链）。
运行：repo 根 `bash scripts/run_all_tests.sh`；本插件内独立跑：
cd plugins/bgm-render && env python3 -m unittest discover -s tests -t . -v
（本机 ZCode 环境必须 env python3 前缀：AppImage 劫持 argv[0]，见全局 AGENTS.md）。
"""
import contextlib
import importlib
import io
import json
import math
import os
import shlex
import shutil
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

from vtx import hygiene, recovery              # noqa: E402
from vtx.infra import Ctx                       # noqa: E402
from vtx.pluginlib import hooks as pl_hooks   # noqa: E402

FAKE_BGM = r'''import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['FAKE_LOG'], 'a', encoding='utf-8') as log:
    log.write(' '.join(args) + '\n')
command = args[0]
project = Path(args[-1] if command == 'show' else args[1])
duration_file = project.parent / 'duration.txt'
duration = float(duration_file.read_text() if duration_file.exists() else '4.0')
if command == 'show':
    print(json.dumps({'duration_s': duration}, indent=2))
elif command == 'render':
    out = Path(args[args.index('-o') + 1]); (out / 'stems').mkdir(parents=True, exist_ok=True)
    def sine(name, hz, rate=44100, codec=None):
        cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
               f'sine=frequency={hz}:sample_rate={rate}:duration={duration}', '-ac', '2']
        if codec: cmd += ['-c:a', codec]
        subprocess.run(cmd + [str(out / name)], check=True)
    sine('mix_master.wav', 440); sine('mix_premaster.wav', 330)
    stems = {}
    for track in (project.parent / 'tracks.txt').read_text().splitlines():
        if track:
            sine(f'stems/{track}.bus.wav', 880, 48000, 'pcm_f32le')
            stems[track] = {'path': str(out / f'stems/{track}.sfz.wav')}
    print(json.dumps({'out_dir': str(out), 'master': str(out / 'mix_master.wav'),
        'premaster': str(out / 'mix_premaster.wav'), 'duration_s': duration,
        'qc_ok': True, 'stems': stems}, indent=2))
else:
    sys.exit(2)
'''


def _context(root: Path, tl: dict):
    """Use the production writer and pinned identity, not a duplicate executor.

    Each snapshot owns its timeline file so constructing another context cannot
    silently invalidate an earlier context's still-live input snapshot.
    """
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root,
                                     prefix="timeline-", suffix=".json", delete=False) as stream:
        json.dump(tl, stream)
        timeline = Path(stream.name)
    return Ctx(timeline)


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "music").mkdir()
        # 伪 bgm CLI + 调用日志
        self.log = self.root / "bgm_calls.log"
        fake = self.root / "fakebgm.py"
        fake.write_text(FAKE_BGM, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        cfg = self.root / "vt_config.json"
        argv = [sys.executable, str(fake)]
        command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
        cfg.write_text(json.dumps({"bgm": {"cli": command}}), encoding="utf-8")
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
        # 桩 render 的分轨清单（对齐伪工程 tracks：strings/kick）
        (self.root / "music" / "tracks.txt").write_text("strings\nkick\n",
                                                        encoding="utf-8")

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
        return _context(self.root, self._tl(bgm))

    def _tl_srcs(self, *srcs):
        """多源 timeline（共享渲染缓存场景：多源可指向同一工程）。"""
        return {"version": 2,
                "project": {"fps": 30, "duration_frames": 120,
                            "width": 640, "height": 360},
                "sources": list(srcs), "video_tracks": [], "subtitles": [],
                "audio_tracks": []}

    def _render_calls(self):
        """伪 CLI 日志中的 render 调用行数（整工程渲染次数）。"""
        return sum(1 for l in self._calls() if l.split()[:1] == ["render"])

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
        self.assertTrue(recovery.media_cache_valid(out))
        self.assertEqual(len(self._calls()), 1, "首次 prepare 应触发一次 render")
        with wave.open(str(out), "rb") as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()),
                             (48000, 2, 2), "须转 48k stereo s16（裸提取同格式）")
        out2 = _bgm.prepare_source(c, "bgm_main")
        self.assertEqual(out2, out)
        self.assertEqual(len(self._calls()), 1, "缓存命中不得再渲染")
        # Missing certificates, even on readable short PCM, require derivation
        # again; the independent multi-file render cache remains reusable.
        recovery.completion_marker(out).unlink()
        with wave.open(str(out), "wb") as audio:
            audio.setnchannels(2); audio.setsampwidth(2); audio.setframerate(48000)
            audio.writeframes(b"\0" * 240 * 4)
        self.assertFalse(recovery.media_cache_valid(out))
        with mock.patch.object(c, "run", wraps=c.run) as derive:
            self.assertEqual(_bgm.prepare_source(c, "bgm_main"), out)
        self.assertEqual(derive.call_count, 1)
        self.assertTrue(recovery.media_cache_valid(out))
        self.assertEqual(len(self._calls()), 1, "重派生不得重新渲染共享工程")
        out.unlink()
        recovery.completion_marker(out).unlink()
        staged = []

        def failed_conversion(cmd, **kwargs):
            staged.append(Path(cmd[-1]))
            with wave.open(str(cmd[-1]), "wb") as audio:
                audio.setnchannels(2); audio.setsampwidth(2); audio.setframerate(48000)
                audio.writeframes(b"\0" * 240 * 4)
            return subprocess.CompletedProcess(cmd, 1, "", "failed after readable short WAV")

        with mock.patch("vtx.infra.subprocess.run", side_effect=failed_conversion) as convert, \
                mock.patch("subprocess.Popen", side_effect=AssertionError("unexpected child")):
            with self.assertRaises(SystemExit) as failure:
                _bgm.prepare_source(c, "bgm_main")
        self.assertEqual(failure.exception.code, 1)
        self.assertEqual(convert.call_count, 1)
        self.assertFalse(out.exists())
        self.assertFalse(staged[0].exists())
        self.assertFalse(recovery.completion_marker(out).exists())
        self.assertEqual(_bgm.prepare_source(c, "bgm_main"), out)
        self.assertTrue(recovery.media_cache_valid(out))
        self.assertEqual(len(self._calls()), 1, "失败派生重试仍复用同一共享工程")

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

    def test_render_digest_track_invariant(self):
        """render_digest 不含轨名（轨名只影响派生哪个 stem，不影响整工程渲染）
        ——轨名变更零重渲；但产物键含 bgm 键值 → 派生件键自然分开。"""
        d1 = _bgm.render_digest(self._ctx({"output": "stem:strings"}), "bgm_main")
        d2 = _bgm.render_digest(self._ctx({"output": "stem:kick"}), "bgm_main")
        d3 = _bgm.render_digest(self._ctx({"output": "master"}), "bgm_main")
        self.assertEqual(d1, d2, "轨名不得进 render_digest（同工程共享一次渲染）")
        self.assertEqual(d1, d3, "输出选择不得进 render_digest")
        p1 = _bgm.product_path(self._ctx({"output": "stem:strings"}), "bgm_main")
        p2 = _bgm.product_path(self._ctx({"output": "stem:kick"}), "bgm_main")
        self.assertNotEqual(p1, p2, "产物键须随轨名分开（bgm 键值在 param_digest）")

    def test_render_digest_flips_on_project_bytes(self):
        d1 = _bgm.render_digest(self._ctx(True), "bgm_main")
        self.proj.write_text(self.proj.read_text(encoding="utf-8") + "\n",
                             encoding="utf-8")
        d2 = _bgm.render_digest(self._ctx(True), "bgm_main")
        self.assertNotEqual(d1, d2, "工程字节变化须翻转 render_digest（重渲）")


class SharedRenderCache(_Base):
    """共享渲染缓存（v1.1.0）：一工程一渲染，多源派生。"""

    def test_two_stem_sources_render_exactly_once(self):
        """核心断言：同工程两 stem 源 → bgm render 恰好 1 次，两源各自 48k s16。"""
        tl = self._tl_srcs(
            {"id": "s1", "path": "music/ep.json", "bgm": {"output": "stem:strings"}},
            {"id": "s2", "path": "music/ep.json", "bgm": {"output": "stem:kick"}})
        c = _context(self.root, tl)
        o1 = _bgm.prepare_source(c, "s1")
        o2 = _bgm.prepare_source(c, "s2")
        self.assertEqual(self._render_calls(), 1,
                         "同工程两 stem 源必须恰好一次整工程渲染")
        for o in (o1, o2):
            self.assertTrue(o.is_file())
            with wave.open(str(o), "rb") as w:
                self.assertEqual((w.getframerate(), w.getnchannels(),
                                  w.getsampwidth()), (48000, 2, 2),
                                 "分轨派生须转 48k stereo s16（裸提取同格式）")
        # 共享缓存目录 + 完整标记 + bus stem 产物在盘
        shared = _bgm.render_cache_dir(c, "s1")
        self.assertEqual(shared, _bgm.render_cache_dir(c, "s2"),
                         "同工程各源共享同一 render_digest 目录")
        self.assertTrue((shared / "_complete.json").is_file())
        self.assertTrue((shared / "stems" / "strings.bus.wav").is_file())
        self.assertTrue((shared / "stems" / "kick.bus.wav").is_file())

    def test_fake_renderer_preserves_tracks_with_stdin_consuming_ffmpeg(self):
        """FFmpeg stdin polling must not eat the next track in the shell read loop."""
        real_ffmpeg = shutil.which("ffmpeg")
        self.assertIsNotNone(real_ffmpeg, "fake renderer requires real FFmpeg")
        tools = self.root / "stdin-polling-tools"
        tools.mkdir()
        wrapper = tools / "ffmpeg"
        # Make the older FFmpeg interactive-stdin behaviour deterministic even
        # on versions that do not poll stdin during these short fixture renders.
        wrapper.write_text(
            '#!/bin/bash\n'
            'case " $* " in\n'
            '  *" -nostdin "*) ;;\n'
            '  *) dd bs=1 count=1 of=/dev/null 2>/dev/null ;;\n'
            'esac\n'
            f'exec {shlex.quote(real_ffmpeg)} "$@"\n', encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
        with mock.patch.dict(os.environ, {"PATH": str(tools) + os.pathsep +
                                         os.environ.get("PATH", "")}):
            self.test_two_stem_sources_render_exactly_once()

    def test_master_and_stem_share_single_render(self):
        """master 与 stem 输出混排同工程：仍恰好一次渲染。"""
        tl = self._tl_srcs(
            {"id": "m", "path": "music/ep.json", "bgm": True},
            {"id": "s", "path": "music/ep.json",
             "bgm": {"output": "stem:strings"}})
        c = _context(self.root, tl)
        self.assertTrue(_bgm.prepare_source(c, "m").is_file())
        self.assertTrue(_bgm.prepare_source(c, "s").is_file())
        self.assertEqual(self._render_calls(), 1)

    def test_shared_cache_hit_zero_render_after_product_gc(self):
        """共享缓存命中零渲染：产物 wav 被 gc 回收后重派生，不再渲染。"""
        tl = self._tl_srcs(
            {"id": "s1", "path": "music/ep.json", "bgm": {"output": "stem:strings"}},
            {"id": "s2", "path": "music/ep.json", "bgm": {"output": "stem:kick"}})
        c = _context(self.root, tl)
        o1 = _bgm.prepare_source(c, "s1")
        _bgm.prepare_source(c, "s2")
        self.assertEqual(self._render_calls(), 1)
        o1.unlink()                                  # 模拟 gc 回收派生件
        o1b = _bgm.prepare_source(c, "s1")
        self.assertEqual(o1b, o1)
        self.assertTrue(o1b.is_file())
        self.assertEqual(self._render_calls(), 1, "共享缓存命中不得再渲染")

    def test_missing_stem_fails_hard_with_track_names(self):
        """stem 不存在 → fail-hard，报错列出可用轨名帮助排错（R1-F F3/F7 后：
        错轨名是工程侧问题，不得触发自愈白渲一次）。"""
        tl = self._tl_srcs({"id": "s1", "path": "music/ep.json",
                            "bgm": {"output": "stem:nope"}})
        c = _context(self.root, tl)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            with self.assertRaises(SystemExit) as cm:
                _bgm.prepare_source(c, "s1")
        self.assertEqual(cm.exception.code, 1)
        err = buf.getvalue()
        self.assertIn("nope", err)
        self.assertIn("strings", err, "报错须列出可用轨名")
        self.assertIn("kick", err)
        self.assertEqual(self._render_calls(), 1, "首渲一次即失败，错轨名不触发二次渲染")

    def test_marker_intact_but_stem_missing_self_heals(self):
        """R1-F F7 自愈：_complete.json 完好但目标 bus wav 被删 → 自动判共享缓存
        不完整并覆盖重渲一次（不再 fail-hard 要人手删目录）。"""
        tl = self._tl_srcs({"id": "s1", "path": "music/ep.json",
                            "bgm": {"output": "stem:strings"}})
        c = _context(self.root, tl)
        o1 = _bgm.prepare_source(c, "s1")
        self.assertEqual(self._render_calls(), 1)
        shared = _bgm.render_cache_dir(c, "s1")
        self.assertTrue((shared / "_complete.json").is_file())
        o1.unlink()                                    # 派生件先回收（触发重派生）
        (shared / "stems" / "strings.bus.wav").unlink()  # marker 在、目标 stem 缺
        o2 = _bgm.prepare_source(c, "s1")
        self.assertEqual(self._render_calls(), 2, "残缺须自动覆盖重渲（自愈）")
        self.assertTrue(o2.is_file())
        self.assertTrue((shared / "stems" / "strings.bus.wav").is_file())

    def test_marker_intact_but_stem_empty_self_heals(self):
        """空文件（size=0）同判残缺：截断为 0 → 自愈重渲（下游读空 wav 必炸）。"""
        tl = self._tl_srcs({"id": "s1", "path": "music/ep.json",
                            "bgm": {"output": "stem:strings"}})
        c = _context(self.root, tl)
        o1 = _bgm.prepare_source(c, "s1")
        shared = _bgm.render_cache_dir(c, "s1")
        o1.unlink()
        (shared / "stems" / "strings.bus.wav").write_bytes(b"")   # 空 = 残缺
        o2 = _bgm.prepare_source(c, "s1")
        self.assertEqual(self._render_calls(), 2)
        self.assertGreater(o2.stat().st_size, 0)

    def test_marker_intact_but_master_missing_self_heals(self):
        """master 产物残缺同走自愈（结果 JSON 映射的产物被外部删除）。"""
        tl = self._tl_srcs({"id": "m", "path": "music/ep.json", "bgm": True})
        c = _context(self.root, tl)
        o1 = _bgm.prepare_source(c, "m")
        shared = _bgm.render_cache_dir(c, "m")
        o1.unlink()
        (shared / "mix_master.wav").unlink()
        self.assertTrue(_bgm.prepare_source(c, "m").is_file())
        self.assertEqual(self._render_calls(), 2)

    def test_locate_render_wav_mapping(self):
        """定位口径：master/premaster 走结果 JSON 映射；stem 按命名约定取
        stems/<轨>.bus.wav（结果 JSON 的 stems 映射是引擎级 stem，非 bus）。"""
        shared = self.root / "shared"
        (shared / "stems").mkdir(parents=True)
        (shared / "stems" / "strings.bus.wav").write_bytes(b"bus")
        (shared / "stems" / "strings.sfz.wav").write_bytes(b"engine")
        (shared / "mix_master.wav").write_bytes(b"master")
        (shared / "mix_premaster.wav").write_bytes(b"premaster")
        result = {"master": str(shared / "mix_master.wav"),
                  "premaster": str(shared / "mix_premaster.wav"),
                  "stems": {"strings": {"path": str(shared / "stems" /
                                                   "strings.sfz.wav")},
                            "kick": {"path": str(shared / "stems" /
                                                "kick.sfz.wav")}}}
        p = _bgm._locate_render_wav(shared, result, "stem:strings", "s1")
        self.assertEqual(p, shared / "stems" / "strings.bus.wav")
        self.assertEqual(
            _bgm._locate_render_wav(shared, result, "master", "s1"),
            Path(result["master"]))

    def test_contribute_keys_covers_all_bgm_sources(self):
        tl = self._tl_srcs(
            {"id": "s1", "path": "music/ep.json", "bgm": {"output": "stem:strings"}},
            {"id": "s2", "path": "music/ep.json", "bgm": {"output": "stem:kick"}})
        keys = _bgm.contribute_keys(_context(self.root, tl))
        self.assertEqual(len(keys), 2, "每个 bgm 源一个产物键（白名单保活）")
        # v1.2.0 键级白名单（R1-F F2）：同 timeline 的 render_digest 集——gc 据此
        # 保活在用共享渲染目录、回收陈旧目录
        dkeys = _bgm.cache_dir_keys(_context(self.root, tl))
        self.assertEqual(dkeys, {"bgm": {_bgm.render_digest(_context(self.root, tl), "s1")}})


class DirExtraDepsSig(_Base):
    """目录型 extra_deps 递归内容签名（R1-F F1）：目录不再等价 MISSING。"""

    def _dep_ctx(self):
        dep = self.root / "music" / "sample_lib"
        (dep / "sub").mkdir(parents=True)
        (dep / "sub" / "a.sfz").write_bytes(b"AAA")
        (dep / "b.bin").write_bytes(b"BBB")
        return dep, self._ctx({"extra_deps": ["sample_lib"]})

    def test_dir_sig_not_missing_flips_on_content_change(self):
        dep, c = self._dep_ctx()
        sig = _bgm._file_sig(dep)
        self.assertNotEqual(sig, "MISSING", "目录不得再与不存在路径同签名")
        self.assertTrue(sig.startswith("DIRC:"))
        d1 = _bgm.param_digest(c, "bgm_main")
        (dep / "b.bin").write_bytes(b"XXX")            # 改文件内容
        self.assertNotEqual(_bgm._file_sig(dep), sig)
        self.assertNotEqual(_bgm.param_digest(c, "bgm_main"), d1,
                            "目录内改文件内容必须翻转产物键")

    def test_dir_sig_flips_on_add_remove(self):
        dep, c = self._dep_ctx()
        d1 = _bgm.param_digest(c, "bgm_main")
        (dep / "c.txt").write_bytes(b"CCC")            # 增文件
        d2 = _bgm.param_digest(c, "bgm_main")
        self.assertNotEqual(d1, d2)
        (dep / "c.txt").unlink()                       # 删文件 → 回到原签名
        self.assertEqual(_bgm.param_digest(c, "bgm_main"), d1)

    def test_dir_sig_skips_companion_files(self):
        """伴生件豁免（对齐 gc 惯例）：.lock/.tmp/__pycache__ 不影响签名。"""
        dep, c = self._dep_ctx()
        d1 = _bgm.param_digest(c, "bgm_main")
        (dep / "x.lock").write_text("", encoding="utf-8")
        (dep / "x.tmp").write_text("", encoding="utf-8")
        pycache = dep / "__pycache__"
        pycache.mkdir()
        (pycache / "m.cpython-311.pyc").write_bytes(b"\x00\x01")
        self.assertEqual(_bgm.param_digest(c, "bgm_main"), d1,
                         "伴生件/字节码不得使摘要无意义抖动")

    def test_dir_sig_guardrail_degrades_to_listing(self):
        """护栏（monkeypatch 小阈值）：超限降级清单签名，DIRL 前缀=可观测标记。"""
        dep, c = self._dep_ctx()
        with mock.patch.object(_bgm, "_DIR_SIG_MAX_FILES", 1):
            sig = _bgm._file_sig(dep)
            self.assertTrue(sig.startswith("DIRL:"), "护栏触发须降级清单签名")
            d1 = _bgm.param_digest(c, "bgm_main")
            (dep / "d.txt").write_bytes(b"DDD")        # 清单签名对增删文件仍敏感
            self.assertNotEqual(_bgm.param_digest(c, "bgm_main"), d1)
        with mock.patch.object(_bgm, "_DIR_SIG_MAX_BYTES", 1):
            self.assertTrue(_bgm._file_sig(dep).startswith("DIRL:"))
        self.assertTrue(_bgm._file_sig(dep).startswith("DIRC:"), "阈值恢复 → 内容签名")


class StaleDigestGc(_Base):
    """gc 键级回收陈旧 render_digest 目录（R1-F F2）：--dry-run 只列陈旧、在用保活；
    实删陈旧后在用产物不受影响、重跑 prepare 零渲染。"""

    def test_gc_lists_and_recycles_stale_digest_only(self):
        os.environ["VTX_SEG_NVENC"] = "0"    # gc_plan 纯计算（seg 编码器免探测）
        self.addCleanup(os.environ.pop, "VTX_SEG_NVENC", None)
        tl = self._tl_srcs({"id": "s1", "path": "music/ep.json",
                            "bgm": {"output": "stem:strings"}})
        c = _context(self.root, tl)
        _bgm.prepare_source(c, "s1")
        stale = _bgm.render_cache_dir(c, "s1")
        self.assertEqual(self._render_calls(), 1)
        self.proj.write_text(self.proj.read_text(encoding="utf-8") + "\n",
                             encoding="utf-8")          # 工程字节变 → 新 digest 目录
        c = _context(self.root, tl)                       # new pinned input identity
        _bgm.prepare_source(c, "s1")
        live = _bgm.render_cache_dir(c, "s1")
        self.assertNotEqual(stale.name, live.name)
        self.assertTrue(stale.is_dir() and live.is_dir())
        # gc --dry-run：陈旧目录进清单、在用目录不在
        plan = hygiene.gc_plan(c)
        listed = {str(p) for p, _, _ in plan["plugin_cache"]}
        self.assertIn(str(stale), listed, "陈旧 render_digest 目录须可被 gc 观测")
        self.assertNotIn(str(live), listed, "在用 render_digest 目录须保活")
        # gc 实删：陈旧目录回收，在用共享缓存/派生件不受影响
        with contextlib.redirect_stdout(io.StringIO()):
            hygiene.gc(c, do=True)
        self.assertFalse(stale.is_dir())
        self.assertTrue(live.is_dir() and (live / "_complete.json").is_file())
        _bgm.prepare_source(c, "s1")
        self.assertEqual(self._render_calls(), 2, "回收后重跑 prepare 零渲染")


class CliResolution(_Base):
    """CLI 三通道解析（R1-F F4）：config 通道指向不存在/不可执行路径 = 该通道
    不可用落下一通道；三通道全败文案列出具体坏路径（不裸栈崩）。"""

    def _cfg(self, **bgm):
        cfg = self.root / "cfg_case.json"
        cfg.write_text(json.dumps({"bgm": bgm}), encoding="utf-8")
        os.environ["VTX_CONFIG"] = str(cfg)

    def test_config_cli_nonexistent_falls_through_and_reports(self):
        self._cfg(cli="/nonexistent/fakebgm", repo="/nonexistent/repo")
        fakehome = self.root / "home"                # 无 ~/.local/bin/bgm
        fakehome.mkdir()
        with mock.patch("pathlib.Path.home", return_value=fakehome):
            cmd, why = _bgm._bgm_cmd()
            self.assertIsNone(cmd)
            self.assertIn("config bgm.cli 指向不存在", why,
                          "全败文案须列 config 通道具体原因")
            self.assertIn("/nonexistent/fakebgm", why)
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                with self.assertRaises(SystemExit) as cm:
                    _bgm._run_bgm(None, ["show"], "探测")
        self.assertEqual(cm.exception.code, 1, "全败 fail-hard 退出码 1（非 traceback）")
        self.assertIn("config bgm.cli 指向不存在", buf.getvalue())

    def test_config_cli_nonexecutable_falls_through(self):
        plain = self.root / "plain.txt"             # 在盘但不可执行
        plain.write_text("not executable", encoding="utf-8")
        self._cfg(cli=str(plain), repo="/nonexistent/repo")
        fakehome = self.root / "home"
        fakehome.mkdir()
        with mock.patch("pathlib.Path.home", return_value=fakehome):
            cmd, why = _bgm._bgm_cmd()
        self.assertIsNone(cmd)
        self.assertIn("不可执行", why)

    def test_config_cli_nonexistent_falls_to_wrapper(self):
        self._cfg(cli="/nonexistent/fakebgm")       # 不写 repo：wrapper 通道优先接住
        fakehome = self.root / "home"
        (fakehome / ".local" / "bin").mkdir(parents=True)
        wrapper = fakehome / ".local" / "bin" / "bgm"
        wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
        with mock.patch("pathlib.Path.home", return_value=fakehome):
            cmd, env = _bgm._bgm_cmd()
        self.assertEqual(cmd, [str(wrapper)], "坏 config 通道须落 ~/.local/bin/bgm")


class ValidateHook(_Base):
    def test_clean_passes(self):
        for good in (True, {}, {"output": "master"},
                     {"output": "premaster", "extra_deps": ["a.json"]}):
            self.assertEqual(_bgm.validate(_context(self.root, self._tl(good))), [])

    def test_bad_shapes_fail(self):
        cases = ["yes", 42,
                 {"output": "loud"},            # output 越域
                 {"unknown": 1},                # 未知参数
                 {"extra_deps": "a.json"}]      # extra_deps 非列表
        for bad in cases:
            errs = _bgm.validate(_context(self.root, self._tl(bad)))
            self.assertTrue(errs, f"期望报错：{bad!r}")

    def test_stem_output_valid(self):
        """stem:<轨名> 值域：合法轨名（字母数字/_./-，字母数字开头）通过。"""
        for good in ({"output": "stem:piano-sal"}, {"output": "stem:bgm-drums"},
                     {"output": "stem:a1_b.c-d"}, {"output": "stem:vln2"}):
            self.assertEqual(_bgm.validate(_context(self.root, self._tl(good))), [],
                             f"期望通过：{good!r}")

    def test_stem_output_invalid(self):
        """stem 轨名防御：路径穿透/空轨名/首字符点/空白/非字符串/**尾随换行**
        一律拒绝（R1-F F3：re 的 $ 允许尾随换行，match+$ 曾放行 "abc\\n"）。"""
        cases = [{"output": "stem:../x"},         # 路径穿透
                 {"output": "stem:a/b"},          # 路径分隔符
                 {"output": "stem:a\\b"},         # 反斜杠分隔符
                 {"output": "stem:"},             # 空轨名
                 {"output": "stem:.."},           # 上跳
                 {"output": "stem:.hidden"},      # 首字符非字母数字
                 {"output": "stem:x y"},          # 空白
                 {"output": "stem:abc\n"},        # 尾随换行（match+$ 绕过回归）
                 {"output": "stem:abc\ndef"},     # 换行中缀
                 {"output": 42},                  # 非字符串
                 {"output": ["stem:strings"]}]    # 非字符串（列表）
        for bad in cases:
            errs = _bgm.validate(_context(self.root, self._tl(bad)))
            self.assertTrue(errs, f"期望报错：{bad!r}")

    def test_path_must_be_json(self):
        tl = self._tl(True)
        tl["sources"][0]["path"] = "music/final.wav"
        errs = _bgm.validate(_context(self.root, tl))
        self.assertTrue(any(".json" in e for e in errs))


class InvariantsAndBranches(_Base):
    def test_contribute_keys(self):
        c = self._ctx({"output": "master"})
        keys = _bgm.contribute_keys(c)
        self.assertEqual(len(keys), 1)
        (key,) = keys
        self.assertIn("__bgm_", key)

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
        c = _context(self.root, tl)
        f = _bgm.probe_source(c, "bgm_main")
        self.assertEqual(f["nb_frames"], 120)     # minimal duration 4.0s × 30fps
        out = _bgm.prepare_source(c, "bgm_main")
        self.assertTrue(out.is_file())
        with wave.open(str(out), "rb") as w:
            self.assertEqual((w.getframerate(), w.getnchannels()), (48000, 2))

    def test_real_stem_end_to_end(self):
        """真链分轨：minimal 工程首轨 stem → bus.wav 派生 48k s16（VTX_TEST_BGM_INTEG=1）。"""
        if os.environ.get("VTX_TEST_BGM_INTEG") != "1":
            self.skipTest("VTX_TEST_BGM_INTEG=1 门控（真 bgm render，需 music_tools）")
        src = Path("~/文档/01_活跃项目/music_tools/examples/minimal.json").expanduser()
        if not src.is_file():
            self.skipTest(f"minimal 示例不在盘：{src}")
        os.environ.pop("VTX_CONFIG", None)
        proj = self.root / "minimal.json"
        proj.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        first_track = next(t["name"] for t in json.loads(
            src.read_text(encoding="utf-8"))["tracks"] if isinstance(t, dict))
        tl = {"version": 2,
              "project": {"fps": 30, "duration_frames": 120,
                          "width": 640, "height": 360},
              "sources": [{"id": "bgm_stem", "path": "minimal.json",
                           "bgm": {"output": f"stem:{first_track}"}}],
              "video_tracks": [], "subtitles": [], "audio_tracks": []}
        c = _context(self.root, tl)
        f = _bgm.probe_source(c, "bgm_stem")
        self.assertEqual(f["nb_frames"], 120)     # 分轨时长 = 工程时长（缝 C 口径不变）
        out = _bgm.prepare_source(c, "bgm_stem")
        self.assertTrue(out.is_file())
        with wave.open(str(out), "rb") as w:
            self.assertEqual((w.getframerate(), w.getnchannels(),
                              w.getsampwidth()), (48000, 2, 2))
        shared = _bgm.render_cache_dir(c, "bgm_stem")
        self.assertTrue((shared / "stems" / f"{first_track}.bus.wav").is_file(),
                        "总线分轨须落共享渲染缓存")


if __name__ == "__main__":
    unittest.main()
