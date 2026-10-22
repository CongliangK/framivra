#!/usr/bin/env python3
"""tests/gfx/test_gfx_cli_fallback.py —— gfx Chrome CLI 回退路径退出码与 PNG 产物校验
（B04 / issue #28 / F-06 / #6）。

被测对象 vtx/render/gfx_cli.py：`vtx gfx` 的 CLI 回退（VTX_GFX=cli、CDP 不可用整页
回落、CDP 帧级失败补渲）原先不检查每个 chrome --headless --screenshot 子进程
的退出码、完成度靠 PNG 数量推断，损坏 PNG 会被当作缓存命中。本测试用假
chrome 可执行脚本（stub，无需真 Chrome，POSIX shebang 直 exec）覆盖五类行为：

  ① 非零退出：显式 GfxCliError，报完整命令 + 退出码 + stderr 线索；
  ② 退出码 0 但不写文件：目标帧报「缺失」，绝不计为完成；
  ③ 写零字节文件：报「零字节」；
  ④ 写非 PNG 内容：报「魔数头不符」；
  ⑤ 正常成功：写真实最小 PNG，render_cli 返回帧数、逐帧校验通过。

另覆盖：部分帧失败（只报失败帧、成功帧照常落盘）；损坏缓存帧的
verify_frames/require_frames（缺帧/零字节/损坏帧逐帧报因，绝不放行为
缓存命中）；命令构造与 cli.py 原 shot_cli 的五旗一致（防漂移）。
issue #28 R3 回归：CRC 全对、块结构完整、IDAT 为垃圾字节的攻击件——
单元级 png_invalid_reason 必须 reject，真 CLI 级（stub chrome rc0 写出
该件 + VTX_GFX=cli）非零退出、回执 ok=false、二次运行不显示缓存命中。
R3 红队复审 D1：interlace=1 + zlib.compress(垃圾) 的隔行攻击件（原实现放行
隔行即跳过总量比对）同样单元级 + 真 CLI 级双门拒绝；D2：zlib 流 eof 后仍有
尾随 IDAT 数据判损坏（spec 要求恰好一个 zlib 流）。

运行：bash scripts/run_all_tests.sh；或独立跑
  python3 -m unittest tests.gfx.test_gfx_cli_fallback
  python3 tests/gfx/test_gfx_cli_fallback.py
"""
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:  # 独立跑（python3 tests/gfx/test_gfx_cli_fallback.py）时锚定仓根
    sys.path.insert(0, str(REPO))

from tests.gfx._environment import environment_cli  # noqa: E402
from vtx.render.gfx_cli import (GfxCliError, PNG_SIGNATURE, _cli_shot_cmd,  # noqa: E402
                         png_invalid_reason, render_cli, require_frames,
                         run_cli_shot, verify_frames)


def _minimal_png() -> bytes:
    """构造 1×1 8-bit RGBA 真 PNG（IHDR/IDAT/IEND 齐、CRC 有效）。"""
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)     # 1×1，8-bit RGBA
    idat = zlib.compress(b"\x00\xff\x00\x00\xff", 9)         # filter 0 + RGBA
    return (PNG_SIGNATURE + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat)
            + chunk(b"IEND", b""))


MINIMAL_PNG = _minimal_png()


def _png_with_idat(idats, w=1, h=1, interlace=0) -> bytes:
    """构造签名/IHDR/块边界/CRC/IEND 全部合法、IDAT 为任意给定字节的 PNG。

    issue #28 R3 攻击件：旧 png_invalid_reason（纯结构校验）对 CRC 正确的
    垃圾 IDAT 返回 None 放行——真 CLI rc0、gfx ok:true、二次运行还缓存命中。
    idats 为 IDAT 块载荷列表（多块 = 一个 zlib 流拆块，覆盖流式增量解压路径）。
    """
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, interlace)  # 8-bit RGBA
    return (PNG_SIGNATURE + chunk(b"IHDR", ihdr)
            + b"".join(chunk(b"IDAT", d) for d in idats) + chunk(b"IEND", b""))

# 假 chrome：解析 --screenshot=<path> 与 ?f=<i>，行为按 MODE 烧死。
# __MODE__/__PNG__ 由 _make_stub 注入（避免 format 与脚本体花括号冲突）。
_STUB_TEMPLATE = '''#!/usr/bin/env python3
# B04 测试桩：假 chrome --headless --screenshot CLI（tests.gfx.test_gfx_cli_fallback）
import sys

MODE = __MODE__
PNG = __PNG__

target = None
frame = None
for a in sys.argv[1:]:
    if a.startswith("--screenshot="):
        target = a.split("=", 1)[1]
    elif "?f=" in a:
        frame = int(a.rsplit("?f=", 1)[1])

if MODE == "fail":
    sys.stderr.write("stub chrome: 模拟崩溃 boooom\\n")
    sys.exit(3)
if MODE == "half" and frame == 0:
    sys.stderr.write("stub chrome: 第 0 帧模拟失败\\n")
    sys.exit(3)
if target is None:
    sys.exit(0)
if MODE == "nowrite":
    pass
elif MODE == "empty":
    open(target, "wb").close()
elif MODE == "notpng":
    with open(target, "wb") as fh:
        fh.write(b"<html>definitely not a png</html>")
elif MODE == "truncated":
    with open(target, "wb") as fh:
        fh.write(PNG[:8])
elif MODE in ("ok", "half", "writefail"):
    with open(target, "wb") as fh:
        fh.write(PNG)
if MODE == "writefail":
    sys.stderr.write("stub chrome: 已写 PNG 但退出失败\\n")
    sys.exit(3)
sys.exit(0)
'''


class GfxCliFallbackTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.html = self.tmp / "page.html"
        self.html.write_text("<!doctype html><html><body>ok</body></html>",
                             encoding="utf-8")
        self.out = self.tmp / "cache" / "gfx" / "g6a_title"
        self.out.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _stub(self, mode: str, png: bytes = MINIMAL_PNG) -> Path:
        """生成可执行的假 chrome 脚本，返回其路径（png=stub 落盘的产物字节）。"""
        path = self.tmp / f"chrome_stub_{mode}"
        path.write_text(
            _STUB_TEMPLATE.replace("__MODE__", repr(mode))
                          .replace("__PNG__", repr(png)),
            encoding="utf-8")
        path.chmod(0o755)
        return path

    # ---------------------------------------------------------- ① 非零退出
    def test_nonzero_exit_fails_naming_command(self):
        stub = self._stub("fail")
        with self.assertRaises(GfxCliError) as cm:
            render_cli(range(2), self.out, self.html, stub, width=1, height=1)
        msg = str(cm.exception)
        self.assertIn("2/2 帧失败", msg)
        self.assertIn(f"退出码 3", msg)
        self.assertIn(str(stub), msg)                              # 是哪条命令
        self.assertIn("--screenshot=", msg)
        self.assertIn(f"--screenshot={self.out}/f0000.png", msg)
        self.assertIn("boooom", msg)                               # stderr 线索
        self.assertFalse(list(self.out.glob("f*.png")))            # 无产物混入完成数

    # ------------------------------------------- ② 退出码 0 但不写文件
    def test_exit_zero_but_no_file_fails(self):
        stub = self._stub("nowrite")
        with self.assertRaises(GfxCliError) as cm:
            render_cli(range(2), self.out, self.html, stub, width=1, height=1)
        msg = str(cm.exception)
        self.assertIn("2/2 帧失败", msg)
        self.assertIn("缺失", msg)
        self.assertIn("f0000.png", msg)
        self.assertIn("f0001.png", msg)
        self.assertFalse(list(self.out.glob("f*.png")))

    # ------------------------------------------------------- ③ 零字节文件
    def test_zero_byte_png_fails(self):
        stub = self._stub("empty")
        with self.assertRaises(GfxCliError) as cm:
            render_cli(range(1), self.out, self.html, stub, width=1, height=1)
        msg = str(cm.exception)
        self.assertIn("1/1 帧失败", msg)
        self.assertIn("零字节", msg)
        self.assertIn("f0000.png", msg)
        # Invalid staging is never published; the subsequent cache probe must
        # still reject it, now as missing rather than a published zero-byte PNG.
        self.assertEqual([(0, "缺失")], verify_frames(self.out, range(1)))
        self.assertFalse((self.out / 'f0000.png').exists())

    # ------------------------------------------------------ ④ 非 PNG 内容
    def test_non_png_content_fails(self):
        stub = self._stub("notpng")
        with self.assertRaises(GfxCliError) as cm:
            render_cli(range(1), self.out, self.html, stub, width=1, height=1)
        msg = str(cm.exception)
        self.assertIn("1/1 帧失败", msg)
        self.assertIn("魔数头不符", msg)
        self.assertIn(repr(b"<html>de"), msg)                     # 报出实际前字节
        # 损坏产物不得被后续当作缓存命中
        self.assertEqual(1, len(verify_frames(self.out, range(1))))

    # ---------------------------------------------------------- ⑤ 正常成功
    def test_normal_success(self):
        stub = self._stub("ok")
        n = render_cli(range(3), self.out, self.html, stub, width=1, height=1)
        self.assertEqual(n, 3)
        for i in range(3):
            p = self.out / f"f{i:04d}.png"
            self.assertTrue(p.is_file())
            self.assertEqual(p.read_bytes(), MINIMAL_PNG)
            self.assertIsNone(png_invalid_reason(p))
        self.assertEqual([], verify_frames(self.out, range(3)))
        self.assertEqual(3, require_frames(self.out, range(3)))

    # --------------------------------------------------- 部分帧失败（half）
    def test_partial_failure_reports_only_failing_frames(self):
        stub = self._stub("half")            # f=0 退出码 3；其余写真 PNG
        with self.assertRaises(GfxCliError) as cm:
            render_cli(range(3), self.out, self.html, stub, width=1, height=1)
        msg = str(cm.exception)
        self.assertIn("1/3 帧失败", msg)
        self.assertIn("[0000]", msg)                              # 只报失败帧
        self.assertIn("退出码 3", msg)
        self.assertNotIn("[0001]", msg)
        self.assertNotIn("[0002]", msg)
        # 成功帧照常有效落盘，失败帧显式缺失
        self.assertIsNone(png_invalid_reason(self.out / "f0001.png"))
        self.assertIsNone(png_invalid_reason(self.out / "f0002.png"))
        self.assertEqual([(0, "缺失")], verify_frames(self.out, range(3)))

    # --------------------------------------- 损坏缓存帧：绝不当作缓存命中
    def test_verify_frames_rejects_corrupt_cache(self):
        (self.out / "f0000.png").write_bytes(b"")                 # 零字节
        (self.out / "f0001.png").write_bytes(b"GIF89a-corrupt")   # 非 PNG
        # f0002 缺失
        bad = verify_frames(self.out, range(3))
        self.assertEqual([i for i, _ in bad], [0, 1, 2])
        self.assertIn("零字节", bad[0][1])
        self.assertIn("魔数头不符", bad[1][1])
        self.assertEqual("缺失", bad[2][1])
        with self.assertRaises(GfxCliError) as cm:
            require_frames(self.out, range(3))
        self.assertIn("3 帧缺失或损坏", str(cm.exception))

    def test_signature_only_and_truncated_chunks_are_rejected(self):
        # issue #28：红队仅写 8 字节签名；完整 IHDR 但缺 IDAT/IEND 也不放行。
        p = self.out / "f0000.png"
        for data in (PNG_SIGNATURE, MINIMAL_PNG[:32], MINIMAL_PNG[:33], MINIMAL_PNG[:-1]):
            with self.subTest(size=len(data)):
                p.write_bytes(data)
                self.assertIn("截断", png_invalid_reason(p))
                with self.assertRaises(GfxCliError):
                    require_frames(self.out, range(1))

    def test_corrupt_chunk_crc_is_rejected(self):
        p = self.out / "f0000.png"
        data = bytearray(MINIMAL_PNG)
        data[29] ^= 1
        p.write_bytes(data)
        self.assertIn("CRC", png_invalid_reason(p))

    # --------------------------- issue #28 R3：CRC 全对但 IDAT 为垃圾字节
    def test_garbage_idat_with_valid_crc_is_rejected(self):
        """红队攻击件：块结构完整、CRC 全对、IDAT 不可解码 → 必须 reject。

        三种垃圾形态：非 zlib 原始垃圾（解码报错）、zlib 打包任意 bytes
        （可解但解出量与 IHDR 尺寸不符）、zlib 流截尾（eof 不达）。
        """
        p = self.out / "f0000.png"
        stream = zlib.compress(b"\x00\xff\x00\x00\xff")      # MINIMAL_PNG 同款合法流
        payloads = {
            "非 zlib 垃圾字节": b"\xde\xad\xbe\xef\x0f\xfe" * 4,
            "zlib 打包任意 bytes": zlib.compress(b"garbage scanline payload" * 3),
            "zlib 流截尾": stream[:-2],
        }
        for name, payload in payloads.items():
            with self.subTest(garbage=name):
                p.write_bytes(_png_with_idat([payload]))
                reason = png_invalid_reason(p)
                self.assertIsNotNone(reason)
                self.assertIn("IDAT", reason)                 # 报因必须指向 IDAT 解码
        # 批量门同步拒绝：垃圾帧不得当作缓存命中/已完成帧
        bad = verify_frames(self.out, range(1))
        self.assertEqual([0], [i for i, _ in bad])
        self.assertIn("IDAT", bad[0][1])
        with self.assertRaises(GfxCliError) as cm:
            require_frames(self.out, range(1))
        self.assertIn("IDAT", str(cm.exception))

    def test_interlaced_garbage_zlib_idat_is_rejected(self):
        """issue #28 D1 攻击回归：interlace=1 + zlib.compress(垃圾) 必须拒绝。

        红队件：结构/CRC 全对、zlib 流可解码且 eof——仅因隔行标志跳过
        「解出总量与 IHDR 比对」即放行，真 CLI 级曾 rc=0、gfx ok=true、
        二次运行显示缓存命中。修复后 IHDR 处直接拒绝隔行件。
        """
        p = self.out / "f0000.png"
        p.write_bytes(_png_with_idat(
            [zlib.compress(b"interlaced garbage payload" * 3)], interlace=1))
        reason = png_invalid_reason(p)
        self.assertIsNotNone(reason)
        self.assertIn("隔行", reason)                        # 报因明确指向隔行
        # 批量门同步拒绝：隔行垃圾帧不得当作缓存命中/已完成帧
        self.assertEqual([0], [i for i, _ in verify_frames(self.out, range(1))])
        with self.assertRaises(GfxCliError):
            require_frames(self.out, range(1))

    def test_idat_trailing_data_after_zlib_eof_is_rejected(self):
        """issue #28 D2：zlib 流 eof 后仍有 IDAT 尾随数据 → 判损坏。

        PNG spec 要求全部 IDAT 恰好含一个 zlib 流。首块喂完合法流后，尾随
        的垃圾块（或再拼一个完整 zlib 流）曾因解压循环静默丢弃而放行。
        """
        p = self.out / "f0000.png"
        stream = zlib.compress(b"\x00\xff\x00\x00\xff")      # 1×1 合法流
        tails = {
            "尾随原始垃圾": b"trailing garbage after stream end",
            "尾随第二个完整 zlib 流": stream,
        }
        for name, tail in tails.items():
            with self.subTest(tail=name):
                p.write_bytes(_png_with_idat([stream, tail]))
                reason = png_invalid_reason(p)
                self.assertIsNotNone(reason)
                self.assertIn("尾随", reason)
        # 批量门同步拒绝：尾随数据件不得当作缓存命中/已完成帧
        self.assertEqual([0], [i for i, _ in verify_frames(self.out, range(1))])
        with self.assertRaises(GfxCliError):
            require_frames(self.out, range(1))

    def test_valid_png_variants_pass_idat_stream_check(self):
        """反向护栏：合法件不得被流式 IDAT 校验误杀。"""
        p = self.out / "f0000.png"
        stream = zlib.compress(b"\x00\xff\x00\x00\xff")
        # 多块 IDAT 拼一个 zlib 流（跨块增量解压）
        p.write_bytes(_png_with_idat([stream[:3], stream[3:6], stream[6:]]))
        self.assertIsNone(png_invalid_reason(p))
        # 大 IDAT（level-0 stored，块 >64 KiB）→ 分块读路径
        row = b"\x00" + b"\xaa\x55\x0f\xf0" * 600            # 600×600 RGBA
        p.write_bytes(_png_with_idat([zlib.compress(row * 600, 0)], w=600, h=600))
        self.assertIsNone(png_invalid_reason(p))
        # 高压缩比大图（单块输入解出 >1 MiB）→ 解压输出限流 drain 路径
        p.write_bytes(_png_with_idat([zlib.compress(row * 600, 9)], w=600, h=600))
        self.assertIsNone(png_invalid_reason(p))
        # 隔行标志位：直接拒绝（issue #28 D1——本管线产物均为非隔行；放行隔行
        # 即跳过总量比对，zlib 可解码的隔行垃圾件曾蒙混为缓存命中）
        p.write_bytes(_png_with_idat([stream], interlace=1))
        reason = png_invalid_reason(p)
        self.assertIsNotNone(reason)
        self.assertIn("隔行", reason)

    def test_nonzero_with_real_png_does_not_leave_cache_hit(self):
        with self.assertRaisesRegex(GfxCliError, "退出码 3"):
            run_cli_shot(0, self.out, self.html, self._stub("writefail"))
        self.assertFalse((self.out / "f0000.png").exists())

    # ------------------------------------------------- 单帧 API 与空区间
    def test_run_cli_shot_single_frame(self):
        ok, fail = self._stub("ok"), self._stub("fail")
        png = run_cli_shot(7, self.out, self.html, ok, width=1, height=1)
        self.assertEqual(png, self.out / "f0007.png")
        self.assertIsNone(png_invalid_reason(png))
        with self.assertRaises(GfxCliError) as cm:
            run_cli_shot(0, self.out, self.html, fail, width=1, height=1)
        msg = str(cm.exception)
        self.assertIn("退出码 3", msg)
        self.assertIn(str(fail), msg)
        self.assertIn("--screenshot=", msg)
        # 空区间零命令（不起任何子进程即返回）
        self.assertEqual(0, render_cli([], self.out, self.html, fail))

    # ------------------------------- 命令构造与 cli.py 原 shot_cli 一致
    def test_cli_shot_cmd_pins_visual_flags(self):
        stub = self._stub("ok")
        cmd = _cli_shot_cmd(str(stub), 3, self.out, self.html, 1920, 1080)
        self.assertEqual(cmd[1:9], ["--headless", "--disable-gpu", "--hide-scrollbars",
                                    "--force-device-scale-factor=1", "--force-color-profile=srgb",
                                    "--font-render-hinting=none", "--window-size=1920,1080",
                                    "--default-background-color=00000000"])
        self.assertEqual(cmd[0], str(stub))
        self.assertEqual(cmd[9], f"--screenshot={self.out}/f0003.png")
        self.assertTrue(cmd[10].startswith("file://"))
        self.assertTrue(cmd[10].endswith(f"{self.html.name}?f=3"))


class GfxCliEntryTests(unittest.TestCase):

    """issue #28：真实 CLI 接线与失败回执，防止 helper 单测绿而入口仍漏检。"""
    def setUp(self):
        GfxCliFallbackTests.setUp(self)
        from tests.gfx._environment import bind_environment
        bind_environment(self)
    tearDown = GfxCliFallbackTests.tearDown
    _stub = GfxCliFallbackTests._stub

    def context(self, n=1):
        from vtx.pipeline.infra import Ctx
        timeline = self.tmp / "timeline.json"
        timeline.write_text(json.dumps({
            "version": 2,
            "project": {"fps": 30, "width": 1, "height": 1, "duration_frames": n},
            "sources": [], "subtitles": [], "audio_tracks": [],
            "video_tracks": [{"name": "gfx", "kind": "gfx", "clips": [
                {"src": self.html.name, "dst_in": 0, "dur": n}]}],
        }))
        c = Ctx(timeline)
        c.out.mkdir(exist_ok=True)
        return c

    def test_cli_attack_modes_fail_with_false_receipt(self):
        c = self.context()
        for mode, reason in (("empty", "零字节"), ("notpng", "魔数头不符"),
                             ("writefail", "退出码 3"), ("nowrite", "缺失"),
                             ("truncated", "截断")):
            with self.subTest(mode=mode):
                result = subprocess.run(
                    [*environment_cli(), "-t", str(c.tl_path), "gfx"],
                    cwd=REPO, env=dict(os.environ, VTX_GFX="cli", VTX_CHROME=str(self._stub(mode))),
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(reason, result.stdout)
                rec = json.loads((c.out / "receipt.json").read_text())
                self.assertIs(rec["stages"]["gfx"]["ok"], False)
                self.assertNotIn("渲染 1 帧（", result.stdout)

    def test_cli_wrong_frame_name_is_not_a_cache_hit(self):
        from vtx.cache.hygiene import gfx_dir
        from vtx.render.gfx_cli import ENV_PENDING_FILE
        stub = self._stub("nowrite")
        env = dict(os.environ, VTX_GFX="cli", VTX_CHROME=str(stub), VTX_GFX_CACHE_NS="")
        with mock.patch.dict(os.environ, env, clear=True):
            c = self.context()
            d = gfx_dir(c, c.tl["video_tracks"][0]["clips"][0])
            d.mkdir(parents=True)
            (d / "f9999.png").write_bytes(MINIMAL_PNG)
            result = subprocess.run(
                [*environment_cli(), "-t", str(c.tl_path), "gfx"], cwd=REPO, env=env,
                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn("帧已缓存", result.stdout)
        self.assertIs(json.loads((c.out / "receipt.json").read_text())["stages"]["gfx"]["ok"], False)
        self.assertEqual(json.loads((d / ENV_PENDING_FILE).read_text())["key"], d.name)
        self.assertEqual((d / "f9999.png").read_bytes(), MINIMAL_PNG)
        self.assertFalse((d / "f0000.png").exists())

    def test_cli_garbage_idat_png_fails_and_never_cache_hits(self):
        """issue #28 R3 真 CLI 级：CRC 全对、结构完整、IDAT 垃圾的件不得蒙混。

        stub chrome rc0 写出攻击 PNG → gfx 非零退出、回执 ok=false、报因指向
        IDAT；二次运行（垃圾帧残留盘上）不得显示缓存命中，仍显式失败。
        """
        c = self.context()
        attack = _png_with_idat([zlib.compress(b"garbage idat payload" * 3)])
        stub = self._stub("ok", png=attack)
        for run in (1, 2):
            with self.subTest(run=run):
                result = subprocess.run(
                    [*environment_cli(), "-t", str(c.tl_path), "gfx"],
                    cwd=REPO, env=dict(os.environ, VTX_GFX="cli", VTX_CHROME=str(stub)),
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("IDAT", result.stdout)          # 报因指向 IDAT 解码
                self.assertNotIn("帧已缓存", result.stdout)   # 二次运行不是缓存命中
                self.assertNotIn("渲染 1 帧（", result.stdout)
                rec = json.loads((c.out / "receipt.json").read_text())
                self.assertIs(rec["stages"]["gfx"]["ok"], False)

    def test_cli_interlaced_attack_png_fails_and_never_cache_hits(self):
        """issue #28 D1 真 CLI 级：interlace=1 + zlib 垃圾 IDAT 不得蒙混。

        红队复现路径：stub chrome rc0 写出该攻击件 + VTX_GFX=cli——原缺陷
        rc=0、gfx ok=true、二次运行「1 帧已缓存」。修复后两次运行均非零
        退出、回执 ok=false、报因指向隔行、无缓存命中。
        """
        c = self.context()
        attack = _png_with_idat(
            [zlib.compress(b"interlaced garbage payload" * 3)], interlace=1)
        stub = self._stub("ok", png=attack)
        for run in (1, 2):
            with self.subTest(run=run):
                result = subprocess.run(
                    [*environment_cli(), "-t", str(c.tl_path), "gfx"],
                    cwd=REPO, env=dict(os.environ, VTX_GFX="cli", VTX_CHROME=str(stub)),
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("隔行", result.stdout)          # 报因指向隔行拒绝
                self.assertNotIn("帧已缓存", result.stdout)   # 二次运行不是缓存命中
                self.assertNotIn("渲染 1 帧（", result.stdout)
                rec = json.loads((c.out / "receipt.json").read_text())
                self.assertIs(rec["stages"]["gfx"]["ok"], False)

    def test_cdp_whole_page_and_frame_fallback_fail_with_false_receipt(self):
        from vtx.cli import commands as cli
        c = self.context()
        for whole_page in (True, False):
            with self.subTest(whole_page=whole_page):
                renderer = mock.Mock(side_effect=RuntimeError("CDP boom")) if whole_page else mock.Mock(
                    return_value={"failed": [0], "path_counts": {}})
                fake = SimpleNamespace(available=lambda: True, render_page=renderer)
                with mock.patch.dict(sys.modules, {"vtx.render.gfx_render": fake}), \
                     mock.patch("vtx.render.gfx_render", fake, create=True), \
                     mock.patch.object(cli, "find_chrome", return_value=str(self._stub("writefail"))), \
                     mock.patch.dict(os.environ, {"VTX_GFX": "cdp"}):
                    with self.assertRaises(SystemExit) as cm:
                        cli.gfx(c)
                self.assertEqual(cm.exception.code, 1)
                self.assertEqual(renderer.call_count, 1)
                self.assertIs(json.loads((c.out / "receipt.json").read_text())["stages"]["gfx"]["ok"], False)

    def test_cdp_split_final_gate_rejects_invalid_frames(self):
        from vtx.cli import commands as cli
        c = self.context(2)
        def render(html, d, n, **kwargs):
            for i in kwargs["frames"]:
                (d / f"f{i:04d}.png").write_bytes(MINIMAL_PNG if i == 0 else PNG_SIGNATURE)
            return {"failed": [], "rendered": 1, "ms_total": 1, "tabs": 1,
                    "path_counts": {}, "timings_ms": {}}
        fake = SimpleNamespace(available=lambda: True, render_page=render)
        with mock.patch.dict(sys.modules, {"vtx.render.gfx_render": fake}), \
             mock.patch("vtx.render.gfx_render", fake, create=True), \
             mock.patch.object(cli, "find_chrome", return_value="unused"), \
             mock.patch.dict(os.environ, {"VTX_GFX": "cdp", "VTX_GFX_SPLIT_FRAMES": "1"}):
            with self.assertRaises(SystemExit) as cm:
                cli.gfx(c)
        self.assertEqual(cm.exception.code, 1)
        self.assertIs(json.loads((c.out / "receipt.json").read_text())["stages"]["gfx"]["ok"], False)


if __name__ == "__main__":
    unittest.main()
