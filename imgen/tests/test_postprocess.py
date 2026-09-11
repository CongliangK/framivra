#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_postprocess.py · cover-crop 参数三分支 golden + 实跑 ffmpeg 合成图断言（方案 §9.3）。

三分支：放大（2752×1536→3840×2160，bg4k）/ 需裁切（1:1 源→16:9 目标）/
缩小（4K 源→1080p）。测试输入 PNG 全部 ffmpeg 合成（KB 级，不依赖 PIL/numpy），
断言用 ffprobe（width/height/pix_fmt）。
"""
import os
import tempfile
import unittest
from pathlib import Path

import _loader

models = _loader.load("models")
postprocess = _loader.load("postprocess")


class FilterGoldenTest(unittest.TestCase):
    """滤镜串 golden 比对（§9.3 三分支：仅 W/H 不同，形式一致）。"""

    def test_golden_filter_strings(self):
        self.assertEqual(
            postprocess.build_filter(3840, 2160),
            "scale=3840:2160:force_original_aspect_ratio=increase:flags=lanczos,"
            "crop=3840:2160,format=rgb24",
        )
        self.assertEqual(
            postprocess.build_filter(1920, 1080),
            "scale=1920:1080:force_original_aspect_ratio=increase:flags=lanczos,"
            "crop=1920:1080,format=rgb24",
        )
        self.assertEqual(
            postprocess.build_filter(1080, 1920),
            "scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,"
            "crop=1080:1920,format=rgb24",
        )

    def test_golden_argv(self):
        argv = postprocess.build_cover_crop_args(Path("in.png"), Path("out.png"), 3840, 2160)
        self.assertEqual(
            argv,
            ["ffmpeg", "-y", "-i", "in.png",
             "-vf", "scale=3840:2160:force_original_aspect_ratio=increase:flags=lanczos,"
                    "crop=3840:2160,format=rgb24",
             "-frames:v", "1", "out.png"],
        )

    def test_kind_target(self):
        self.assertEqual(postprocess.kind_target("bg4k"), (3840, 2160))
        self.assertEqual(postprocess.kind_target("vertical"), (1080, 1920))
        with self.assertRaises(ValueError):
            postprocess.kind_target("raw")  # postprocess 必须 crop 目标


class RealCropTest(unittest.TestCase):
    """实跑 ffmpeg：合成源图 → cover_crop → ffprobe 精确断言（KB 级合成图）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="imgen_pp_test_")
        cls.tmpdir = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _src(self, name, w, h):
        path = self.tmpdir / name
        _loader.make_png(path, w, h)
        # 合成 PNG 保持 KB 级（不落仓、不超限）
        self.assertLess(path.stat().st_size, 512 * 1024)
        return path

    def _assert_png(self, path, w, h):
        info = postprocess.probe_image(path)
        self.assertEqual((info["width"], info["height"]), (w, h))
        self.assertEqual(info["pix_fmt"], "rgb24")  # cv2.imread 8-bit RGB 契约

    def test_branch_upscale_2752x1536_to_bg4k(self):
        # 放大分支：nano 2K 16:9 实出尺寸 → 4K（离线补救；须有插值倍数警告）
        src = self._src("src_2k16x9.png", 2752, 1536)
        dst = self.tmpdir / "bg4k_out.png"
        import io

        captured = io.StringIO()
        result = postprocess.cover_crop(src, dst, kind="bg4k", progress=captured)
        self.assertEqual(result, dst)
        self._assert_png(dst, 3840, 2160)
        warning = captured.getvalue()
        self.assertIn("放大", warning)
        self.assertIn("1.4", warning)  # 2160/1536 ≈ 1.41×（方案 §5.2 ~1.406）

    def test_branch_crop_1x1_to_16x9(self):
        # 裁切分支：1:1 源（1920×1920 ≥ 目标）→ 16:9 目标（scale 覆盖后中心 crop，零缩放）
        src = self._src("src_1x1.png", 1920, 1920)
        dst = self.tmpdir / "bg1080p_from_square.png"
        import io

        captured = io.StringIO()
        postprocess.cover_crop(src, dst, kind="bg1080p", progress=captured)
        self._assert_png(dst, 1920, 1080)
        self.assertEqual(captured.getvalue(), "")  # 源≥目标：无放大警告（纯裁切）

    def test_branch_downscale_4k_to_1080p(self):
        # 缩小分支：4K 源 → 1080p（纯降采样，常态链路）
        src = self._src("src_4k.png", 3840, 2160)
        dst = self.tmpdir / "bg1080p_from_4k.png"
        import io

        captured = io.StringIO()
        postprocess.cover_crop(src, dst, kind="bg1080p", progress=captured)
        self._assert_png(dst, 1920, 1080)
        self.assertEqual(captured.getvalue(), "")  # 源≥目标：无放大警告

    def test_vertical(self):
        # 竖版：9:16 源 → 1080×1920 精确
        src = self._src("src_v.png", 2160, 3840)
        dst = self.tmpdir / "vertical_out.png"
        postprocess.cover_crop(src, dst, kind="vertical")
        self._assert_png(dst, 1080, 1920)

    def test_verify_fail_deletes_output(self):
        # 断言不符 → 删除产物并抛错（绝不让错尺寸图进 assets/bg/）
        wrong = self._src("wrong_size.png", 640, 360)
        with self.assertRaises(RuntimeError) as ctx:
            postprocess.verify_png_size(wrong, 1920, 1080)
        self.assertIn("断言不符", str(ctx.exception))
        self.assertFalse(wrong.exists())  # 已删除

    def test_probe_invalid_file_raises(self):
        bad = self.tmpdir / "not_an_image.png"
        bad.write_text("this is not a png", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            postprocess.probe_image(bad)

    def test_cover_crop_missing_input(self):
        with self.assertRaises(RuntimeError):
            postprocess.cover_crop(self.tmpdir / "missing.png", self.tmpdir / "x.png", 1920, 1080)


if __name__ == "__main__":
    unittest.main()
