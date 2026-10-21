#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_models.py · 端点常量 / payload 构建 / kind 联动矩阵单测（方案 §9.3）。

重点防回归：gpt 端点 /api/async/image_gpt 无 `_image` 后缀变体（红队 P0-1）；
参考图 14 张上限；nano 与 gpt 参数差异（size 语义 / aspectRatio 交不交）。
"""
import os
import sys
import unittest
from unittest import mock

from . import _loader   # v2 P2a：tests 成包后相对导入

models = _loader.load("models")


class EndpointConstantsTest(unittest.TestCase):
    def test_endpoints(self):
        # 上游 core/models.py:10/11/36/43 权威定义（gpt 无 _image 后缀）
        self.assertEqual(models.BASE_URL, "https://api.wuyinkeji.com")
        self.assertEqual(models.NANO_SUBMIT_PATH, "/api/async/image_nanoBanana2")
        self.assertEqual(models.GPT_SUBMIT_PATH, "/api/async/image_gpt")
        self.assertEqual(models.DETAIL_PATH, "/api/async/detail")
        self.assertNotIn("image_gpt_image", models.GPT_SUBMIT_PATH)

        # Upload constants bind only current names at import; no upload is invoked.
        fields = ("SSH_TARGET", "REMOTE_DIR", "BASE_URL")
        defaults = ("", "/var/www/sc-img", "")
        current = ("fixture-host", "/fixture/current", "https://current.invalid/images")
        obsolete = ("obsolete-host", "/fixture/obsolete", "https://obsolete.invalid/images")
        cases = ((None, None, None), ("", "", ""), current, (" ", " ", " "),
                 (current[0], None, None), (None, current[1], None),
                 (None, None, current[2]))
        for values in cases:
            expected = tuple(value or default for value, default in zip(values, defaults))
            for old_values in ((None, None, None), ("", "", ""), obsolete):
                env = {prefix + field: value
                       for prefix, configured in (("VT_OWN_UPLOAD_", values), ("SC_IMG_", old_values))
                       for field, value in zip(fields, configured) if value is not None}
                with self.subTest(current=values, obsolete=old_values), \
                        mock.patch.dict(os.environ, env, clear=True), \
                        mock.patch.dict(sys.modules, {"requests": mock.Mock(spec=())}), \
                        mock.patch("subprocess.Popen", side_effect=AssertionError("unexpected subprocess")):
                    sys.modules.pop("reference_upload", None)
                    upload = _loader.load("reference_upload")
                    self.assertEqual(tuple(getattr(upload, "OWN_SERVER_" + field) for field in fields),
                                     expected)
                    os.environ.update({"VT_OWN_UPLOAD_" + field: "changed-after-import" for field in fields})
                    self.assertEqual(tuple(getattr(upload, "OWN_SERVER_" + field) for field in fields),
                                     expected)

    def test_model_registry_uses_constants(self):
        self.assertFalse(hasattr(models, "GPT_SIZE_CHOICES"))
        self.assertEqual(models.MODELS["nano"].submit_path, models.NANO_SUBMIT_PATH)
        self.assertEqual(models.MODELS["gpt-image"].submit_path, models.GPT_SUBMIT_PATH)
        self.assertTrue(models.MODELS["nano"].supports_aspect_ratio)
        self.assertFalse(models.MODELS["gpt-image"].supports_aspect_ratio)


class BuildPayloadTest(unittest.TestCase):
    def test_nano_size_and_ratio(self):
        payload = models.build_payload("测试提示词", "nano", "4K", "16:9")
        self.assertEqual(payload["prompt"], "测试提示词")
        self.assertEqual(payload["size"], "4K")
        self.assertEqual(payload["aspectRatio"], "16:9")

    def test_nano_auto_ratio_omitted(self):
        payload = models.build_payload("p", "nano", "2K", "auto")
        self.assertNotIn("aspectRatio", payload)
        self.assertEqual(payload["size"], "2K")

    def test_nano_default_size_1k(self):
        payload = models.build_payload("p", "nano", None, "auto")
        self.assertEqual(payload["size"], "1K")

    def test_nano_invalid_size(self):
        with self.assertRaises(ValueError):
            models.build_payload("p", "nano", "8K", "auto")

    def test_nano_invalid_ratio(self):
        with self.assertRaises(ValueError):
            models.build_payload("p", "nano", "2K", "7:3")

    def test_gpt_size_is_ratio_no_aspect(self):
        payload = models.build_payload("p", "gpt-image", "16:9", "auto")
        self.assertEqual(payload["size"], "16:9")
        self.assertNotIn("aspectRatio", payload)

    def test_gpt_auto_size_omitted(self):
        payload = models.build_payload("p", "gpt-image", "auto", "auto")
        self.assertNotIn("size", payload)
        self.assertNotIn("aspectRatio", payload)

    def test_gpt_invalid_ratio(self):
        with self.assertRaises(ValueError):
            models.build_payload("p", "gpt-image", "5:4", "auto")

    def test_gpt_data_url_rejected(self):
        with self.assertRaises(ValueError):
            models.build_payload("p", "gpt-image", "auto", "auto", ["data:image/png;base64,xxxx"])

    def test_empty_prompt_rejected(self):
        with self.assertRaises(ValueError):
            models.build_payload("   ", "nano")

    def test_ref_limit_14(self):
        urls = [f"https://example.com/r{i}.png" for i in range(15)]
        with self.assertRaises(ValueError) as ctx:
            models.build_payload("p", "nano", "1K", "auto", urls)
        self.assertIn("14", str(ctx.exception))
        ok = models.build_payload("p", "nano", "1K", "auto", urls[:14])
        self.assertEqual(len(ok["urls"]), 14)


class KindMatrixTest(unittest.TestCase):
    def test_matrix_complete(self):
        self.assertEqual(set(models.KIND_TARGETS), {"bg4k", "bg1080p", "vertical"})
        self.assertEqual(models.KIND_TARGETS["bg4k"], (3840, 2160))
        self.assertEqual(models.KIND_TARGETS["bg1080p"], (1920, 1080))
        self.assertEqual(models.KIND_TARGETS["vertical"], (1080, 1920))
        self.assertEqual(models.KIND_LINKAGE["bg4k"], {"model": "nano", "size": "4K", "ratio": "16:9"})
        self.assertEqual(models.KIND_LINKAGE["bg1080p"], {"model": "nano", "size": "2K", "ratio": "16:9"})
        self.assertEqual(models.KIND_LINKAGE["vertical"], {"model": "nano", "size": "4K", "ratio": "9:16"})
        self.assertNotIn("raw", models.KIND_LINKAGE)  # raw 不联动
        self.assertEqual(models.KIND_FILENAME["bg4k"], "background_4k.png")

    def test_estimate_nano_anchor(self):
        # 2K 16:9 实证锚点（方案 §5.2：实出 ~2752×1536）
        self.assertEqual(models.estimate_nano_output("2K", "16:9"), (2752, 1536))

    def test_upscale_factor(self):
        factor = models.upscale_factor((2752, 1536), (3840, 2160))
        self.assertAlmostEqual(factor, 2160 / 1536, places=4)  # ≈1.406（方案 §5.2 数字）
        self.assertLessEqual(models.upscale_factor((3840, 2160), (1920, 1080)), 1.0)


class Gpt25FamilyTest(unittest.TestCase):
    """/doc/78/79/80 三个 GPT-Image-2.5 端点：路径、单价、参数差异。"""

    def test_endpoints_and_prices(self):
        self.assertEqual(models.MODELS["gpt-2.5"].submit_path, "/api/async/image_gpt_2.5")
        self.assertEqual(models.MODELS["gpt-2.5-sunburst"].submit_path,
                         "/api/async/image_gpt_2.5_sunburst")
        self.assertEqual(models.MODELS["gpt-2.5-flare"].submit_path,
                         "/api/async/image_gpt_2.5_flare")
        self.assertEqual(models.model_price("gpt-2.5"), 0.1)
        self.assertEqual(models.model_price("gpt-2.5-sunburst"), 0.3)
        self.assertEqual(models.model_price("gpt-2.5-flare"), 0.3)
        self.assertEqual(models.estimate_cost(3, "gpt-2.5-sunburst"), 0.9)

    def test_quality_choices_per_model(self):
        self.assertEqual(models.MODELS["gpt-2.5"].quality_choices, ())
        self.assertEqual(models.MODELS["gpt-2.5-sunburst"].quality_choices,
                         ("low", "medium", "high", "xhigh", "max"))
        self.assertEqual(models.MODELS["gpt-2.5-flare"].quality_choices,
                         ("low", "medium", "high"))

    def test_payload_ratio_and_quality(self):
        payload = models.build_payload("p", "gpt-2.5", None, "16:9")
        self.assertEqual(payload["aspectRatio"], "16:9")
        self.assertNotIn("size", payload)
        payload = models.build_payload("p", "gpt-2.5-sunburst", None, "auto", quality="max")
        self.assertNotIn("aspectRatio", payload)
        self.assertEqual(payload["quality"], "max")

    def test_payload_wxh_background_mask(self):
        payload = models.build_payload("p", "gpt-2.5-sunburst", None, "3840x2160",
                                       background="transparent", mask="https://x.com/m.png")
        self.assertEqual(payload["aspectRatio"], "3840x2160")
        self.assertEqual(payload["background"], "transparent")
        self.assertEqual(payload["mask"], "https://x.com/m.png")

    def test_base_version_rejects_sunburst_only_params(self):
        with self.assertRaises(ValueError):
            models.build_payload("p", "gpt-2.5", None, "auto", quality="high")
        with self.assertRaises(ValueError):
            models.build_payload("p", "gpt-2.5", None, "auto", background="transparent")
        with self.assertRaises(ValueError):
            models.build_payload("p", "gpt-2.5", None, "auto", mask="https://x.com/m.png")

    def test_quality_validation(self):
        with self.assertRaises(ValueError):  # flare 不收 xhigh/max
            models.build_payload("p", "gpt-2.5-flare", None, "auto", quality="xhigh")
        with self.assertRaises(ValueError):
            models.build_payload("p", "gpt-2.5-sunburst", None, "auto", quality="ultra")
        with self.assertRaises(ValueError):
            models.build_payload("p", "gpt-2.5-sunburst", None, "auto", background="black")

    def test_aspect_ratio_validation(self):
        with self.assertRaises(ValueError):
            models.build_payload("p", "gpt-2.5", None, "7:3")

    def test_estimate_gpt25_output(self):
        # WxH 精确；比例按 /doc/78 1K 档锚点
        self.assertEqual(models.estimate_gpt25_output("3840x2160"), (3840, 2160))
        self.assertEqual(models.estimate_gpt25_output("16:9"), (1280, 720))
        self.assertEqual(models.estimate_gpt25_output(None), (1024, 1024))

    def test_data_url_rejected(self):
        with self.assertRaises(ValueError):
            models.build_payload("p", "gpt-2.5", None, "auto", ["data:image/png;base64,xx"])


class CostTest(unittest.TestCase):
    def test_estimate_cost(self):
        self.assertEqual(models.estimate_cost(1), 0.1)
        self.assertEqual(models.estimate_cost(3), 0.3)
        self.assertEqual(models.estimate_cost(0), 0.0)


if __name__ == "__main__":
    unittest.main()
