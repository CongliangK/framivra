#!/usr/bin/env python3
"""frame_canary 单测：issue #105 双轨帧同步水印协议（Diagnostic 8x8 Canary /
Clean 1px 原地无损剥离）全链验收。

覆盖面（与工单 DoD 逐条对齐）：
  ① 协议纯函数：冻结签名公式（R=hi,G=lo,B=hi^lo^键；A=255/254）、16-bit 帧号
     域、双模式键互斥（同帧号两轨签名必不同）、decode/classify 全域正确性；
  ② 预乘原生通路（CEF/Electron OnPaint BGRA_PREMUL）：精确反像区间、65536 帧
     roundtrip（歧义恰为预乘塌缩孪生 6 对/12 帧——真塌缩，任何比对不可区分，
     闸门按 ambiguous_signature 显式拒绝；match_bgra_premul 仅为宿主 ±1 校准
     API，非闸门兜底）、±1 扰动匹配、
     accept_paint_bgra_inplace 闸门（匹配接受+原地剥离(1,0)、陈旧帧拒收不改
     缓冲区、宿主上报原始值优先、rejected_stale_paint 旗标）；
  ③ 合成 PNG DoD：「帧 12 的图像贴在 f0013.png」→ wrong_frame_lag1 100%
     （0..254 全扫）+ 滞后/静态首帧/其他/校验和撕裂/no_canary 分类全表 +
     「上半帧 13、下半帧 12」撕裂（TL/BR 双 Canary 相异 与 TL 块内水平双色带
     两形态 + BR 块内双色带）→ torn_frame 100%（0..254 全扫）；
  ④ CLI verify-canary：帧目录全 ok exit 0 / 混合错帧 exit 1 + 计数器与
     wrong_frame_count、单帧 --png/--frame、clean 模式、缺文件/缺参 exit 1、
     report JSON 落盘、七态 ambiguous_signature 单列计数（帧目录模式帧号
     空洞≠缺失——与单帧模式「帧缺失→1」的契约差异由 gfx_golden 模块头显式
     声明，抽样目录合法）；
  ⑤ Clean 剥离 Tier-1（DoD）：合成基准帧（角均匀）邻域 (1,0) 剥离 →
     analyze_frame_diff Tier-1 max_abs_diff==0；预乘混合 ±1 像素同样过闸；
     角非均匀 + 宿主上报原始色 → Tier-1；错帧签名 → 宁拒不错；邻域剥离在
     非均匀角的协议边界（1px 差异，非 Tier-1）如实断言；
  ⑥ JS 注入桥：模板替换（mode/键）、无占位符残留、ES5 约束（无箭头/let/
     const/模板字面量）、未知 mode 拒绝；
  ⑦ 真实 Chrome（env VTX_TEST_CHROME 门控，ci_checks chrome 清单在册）：
     CDP addScriptToEvaluateOnNewDocument 注入 → 合成帧驱动页 + 仓内真实页
     gfxA.html 双轨解码 100%；clean 1px 剥离后与无注入基准 Tier-1 全等；
     __vtx_render(f) 桥接重绘换帧签名（#105b 陈旧色回归）。
  ⑧ 孪生帧穿透回归（红队 #105 r1 FAIL 主因，r2 修复锁）：预乘塌缩孪生 6 对
     全测（双向陈旧伪装必拒 + 真迹同拒——协议物理边界显式化而非可实现性
     缺陷）、相邻对 lag 场景单列（stale==expected-1，CEF #4166 典型形态）、
     skip-gate 与 PNG 剥离侧同规则宁拒不剥、孪生撕裂中点 → 七态
     ambiguous_signature（CLI 单列计数、不进 wrong_frame_count）。
"""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
from vtx import frame_canary as fc   # noqa: E402
import gfx_golden as gg              # noqa: E402
from PIL import Image                # noqa: E402

W, H = 64, 48

# Chrome 集成段的实证口径（帧数受控，全套 <90s）。clean 轨剔除 G/B≥250 的帧：
# 不透明合成把 rgba(254/255) 签名与底色混合，255→254 的塌缩恰好落进相邻帧的
# 合法签名（审计侧 verify 保守判 other，见 TestCleanRestoreTier1 的边界用例）
_DIAG_FRAMES = [0, 1, 2, 3, 12, 13, 90, 255, 256, 511, 4095, 65535]
_CLEAN_FRAMES = [0, 1, 90, 4095]

# 预乘塌缩孪生对（红队 #105 r1 证据 + 全域数值复核锁定）：signature_bgra_premul
# 逐字节相同的帧号对，clean 模式全域恰此 6 对/12 帧。协议物理边界——单像素
# A=254 签名下不可区分，闸门/剥离侧一律 ambiguous_signature 显式拒绝。
_TWIN_PAIRS = ((23167, 23168), (32602, 32858), (32639, 32896),
               (32640, 32895), (32677, 32933), (42367, 42368))
_ADJACENT_TWIN_PAIRS = ((23167, 23168), (42367, 42368))   # 恰滞后 1 帧（lag 形态）

# 合成帧驱动页：不透明深色底（左上角均匀 → 邻域剥离 Tier-1 可证）、随 f 移动
# 的条与数字（画面内容=帧号纯函数，帧驱动铁律）
_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;padding:0;background:#101418;overflow:hidden;}
#bar{position:absolute;left:0;top:20%;width:6px;height:8%;background:#e8e8e8;}
#t{position:absolute;right:4px;bottom:4px;color:#9fb0c0;font:12px monospace;}
</style></head><body><div id="bar"></div><div id="t"></div><script>
var f=parseInt(new URLSearchParams(location.search).get("f")||"0",10);
document.getElementById("bar").style.left=(f*3)%600+"px";
document.getElementById("bar").style.top=(20+(f*7)%60)+"%";
document.getElementById("t").textContent="f="+f;
</script></body></html>"""


def _arr_to_png(arr, path: Path):
    Image.fromarray(arr.astype(np.uint8), "RGBA").save(path)


def _diag_frame_arr(rendered: int, w=W, h=H, br: int = None) -> np.ndarray:
    """合成诊断轨帧：页面内容随帧号变化 + TL 8x8 Canary（+BR 对账块）。

    br=None 时 BR 块与 TL 同帧号（正常帧）；撕裂帧显式给 BR 帧号。"""
    a = np.zeros((h, w, 4), np.uint8)
    a[..., 0] = (rendered * 37) % 256
    a[..., 1] = (rendered * 91) % 256
    a[..., 2] = (rendered * 151) % 256
    a[..., 3] = 255
    sig = np.array(fc.signature_rgba(fc.MODE_DIAGNOSTIC, rendered), np.uint8)
    a[0:8, 0:8] = sig
    a[h - 8:h, w - 8:w] = np.array(
        fc.signature_rgba(fc.MODE_DIAGNOSTIC, br if br is not None else rendered),
        np.uint8)
    return a


def _clean_frame_arr(base: np.ndarray, rendered: int,
                     blend_black: bool = False) -> np.ndarray:
    """合成 clean 轨帧：base（无 Canary 基准）(0,0) ← 帧签名像素。

    blend_black=True 时写入 round(C·254/255)（rgba(254/255) 签名在不透明黑底
    合成通路的实际观感，±1 抗舍入靶标）。"""
    a = base.copy()
    r, g, b = fc.signature_rgb(fc.MODE_CLEAN, rendered)
    if blend_black:
        k = 254.0 / 255.0
        a[0, 0] = (int(r * k + 0.5), int(g * k + 0.5), int(b * k + 0.5), 255)
    else:
        a[0, 0] = (r, g, b, 255)
    return a


def _base_arr(w=W, h=H, corner=None) -> np.ndarray:
    """无 Canary 基准帧：内容渐变；corner=(r,g,b) 时左上角 8x8 同色。"""
    a = np.zeros((h, w, 4), np.uint8)
    x = np.arange(w, dtype=np.int32)
    a[..., 0] = (x * 3) % 256
    a[..., 1] = 80
    a[..., 2] = 200
    a[..., 3] = 255
    if corner is not None:
        a[0:8, 0:8] = np.array(corner + (255,), np.uint8)
    return a


class TestProtocolPure(unittest.TestCase):
    """① 冻结签名公式与全域编解码。"""

    def test_signature_formula_frozen(self):
        # 工单冻结公式手算锚点：f=13 → hi=0, lo=13
        self.assertEqual(fc.signature_rgb(fc.MODE_DIAGNOSTIC, 13), (0, 13, 0 ^ 13 ^ 0xA5))
        self.assertEqual(fc.signature_rgba(fc.MODE_DIAGNOSTIC, 13)[3], 255)
        self.assertEqual(fc.signature_rgb(fc.MODE_CLEAN, 13), (0, 13, 0 ^ 13 ^ 0x5A))
        self.assertEqual(fc.signature_rgba(fc.MODE_CLEAN, 13)[3], 254)
        # f=0x0102=258 → hi=1, lo=2
        self.assertEqual(fc.signature_rgb(fc.MODE_DIAGNOSTIC, 258), (1, 2, 1 ^ 2 ^ 0xA5))
        self.assertEqual(fc.signature_bgra(fc.MODE_CLEAN, 258), (2 ^ 1 ^ 0x5A, 2, 1, 254))

    def test_domain_and_mode_validation(self):
        for bad in (-1, 65536, 1 << 20):
            self.assertRaises(ValueError, fc.signature_rgb, fc.MODE_DIAGNOSTIC, bad)
            self.assertRaises(ValueError, fc.verify_clean_pixel, (0, 0, 0, 255), bad)
        self.assertRaises(ValueError, fc.get_canary_js, "nonsense")
        self.assertRaises(ValueError, fc.xor_key, "")
        self.assertEqual(fc.MODES, (fc.MODE_DIAGNOSTIC, fc.MODE_CLEAN))

    def test_modes_never_collide_full_domain(self):
        for f in range(65536):
            d = fc.signature_rgb(fc.MODE_DIAGNOSTIC, f)
            c = fc.signature_rgb(fc.MODE_CLEAN, f)
            if d == c:
                self.fail(f"双模式签名撞色 @f={f}: {d}")
            self.assertEqual(fc.decode_pixel(fc.MODE_DIAGNOSTIC, *d), (f, True))
            self.assertEqual(fc.decode_pixel(fc.MODE_CLEAN, *c), (f, True))

    def test_classify_frame_table(self):
        self.assertEqual(fc.classify_frame(13, 13), fc.ST_OK)
        self.assertEqual(fc.classify_frame(12, 13), fc.ST_LAG1)
        self.assertEqual(fc.classify_frame(0, 5), fc.ST_STATIC_FIRST)
        self.assertEqual(fc.classify_frame(0, 0), fc.ST_OK)
        self.assertEqual(fc.classify_frame(40, 50), fc.ST_OTHER)
        self.assertEqual(fc.classify_frame(14, 13), fc.ST_OTHER)


class TestPremulNative(unittest.TestCase):
    """② 原生 BGRA_PREMUL 通路（CEF/Electron OnPaint 消费面）。"""

    def test_premul_cands_exact_preimage(self):
        # 全部 256 个直存值：反像集 == {C : round_half_up(C·254/255) == v}（暴力对账）
        def store(c):
            return (508 * c + 255) // 510
        for v in range(256):
            exact = {c for c in range(256) if store(c) == v}
            self.assertEqual(set(fc._premul_cands(v)), exact, f"v={v}")

    def test_premul_signature_formula(self):
        b, g, r, a = fc.signature_bgra_premul(fc.MODE_CLEAN, 13)
        self.assertEqual((b, g, r, a), (int((13 ^ 0x5A) * 254 / 255 + 0.5),
                                        int(13 * 254 / 255 + 0.5), 0, 254))
        self.assertEqual(fc.signature_bgra_premul(fc.MODE_DIAGNOSTIC, 0)[3], 255)

    def test_decode_roundtrip_full_domain(self):
        """65536 帧 roundtrip：唯一反解 65524 帧；歧义帧恰为预乘塌缩孪生
        6 对/12 帧（真塌缩——孪生签名逐字节相同，**任何**比对不可区分）。
        闸门对此按 ambiguous_signature 显式拒绝（test_accept_paint_twin_*），
        无「期望帧精确比对」伪兜底；match_bgra_premul 仅为宿主 ±1 校准 API。"""
        ambiguous = []
        for f in range(65536):
            px = fc.signature_bgra_premul(fc.MODE_CLEAN, f)
            got, ok = fc.decode_bgra_premul(px)
            if not ok or got != f:
                ambiguous.append(f)
                # ±1 校准 API 自洽性（非兜底）：帧自身签名必在 ±1 球内
                self.assertTrue(fc.match_bgra_premul(px, f), f"match 自洽失败 @f={f}")
        self.assertEqual(sorted(ambiguous),
                         sorted({f for pair in _TWIN_PAIRS for f in pair}),
                         "歧义帧清单必须恰为孪生 6 对/12 帧（协议物理边界锁定）")

    def test_match_perturbation(self):
        import random
        random.seed(105)
        for _ in range(20000):
            f = random.randrange(65536)
            b, g, r, a = fc.signature_bgra_premul(fc.MODE_CLEAN, f)
            px = tuple(max(0, min(255, c + random.choice((-1, 0, 1))))
                       for c in (b, g, r)) + (a,)
            self.assertTrue(fc.match_bgra_premul(px, f))
            px2 = (b, g, r, 255)   # α 破坏必须拒
            self.assertFalse(fc.match_bgra_premul(px2, f))

    def _buf(self, f, w=8, h=4):
        """整帧 BGRA_PREMUL bytearray：(0,0)=clean 签名（预乘），其余=(1,0) 邻色。"""
        neighbor = (17, 34, 51, 0)     # 透明邻像素（gfx 叠层页常态）
        sig = bytes(fc.signature_bgra_premul(fc.MODE_CLEAN, f))
        buf = bytearray(bytes(neighbor) * (w * h))
        buf[0:4] = sig
        return buf, neighbor

    def test_accept_paint_match_and_restore(self):
        buf, neighbor = self._buf(258)
        before = bytes(buf)
        r = fc.accept_paint_bgra_inplace(buf, 8, expected_frame=258)
        self.assertTrue(r["accepted"])
        self.assertFalse(r["rejected_stale_paint"])
        self.assertEqual(r["rendered_frame"], 258)
        self.assertTrue(r["restored"])
        self.assertEqual(r["restore_source"], "neighbor_1_0")
        self.assertEqual(bytes(buf[0:4]), bytes(neighbor))
        self.assertEqual(bytes(buf[4:]), before[4:])   # 其余像素零改动（原地 1px）

    def test_accept_paint_stale_rejected_untouched(self):
        for stale_sig_frame in (257, 0, 65535):   # 滞后/首帧/远端陈旧
            buf, _ = self._buf(stale_sig_frame)
            before = bytes(buf)
            r = fc.accept_paint_bgra_inplace(buf, 8, expected_frame=258)
            self.assertFalse(r["accepted"], stale_sig_frame)
            self.assertTrue(r["rejected_stale_paint"])
            self.assertFalse(r["restored"])
            self.assertIn("stale_paint", r["reason"])
            self.assertEqual(bytes(buf), before, "陈旧帧拒绝必须零改动缓冲区")

    def test_accept_paint_reported_original(self):
        buf, _ = self._buf(258)
        r = fc.accept_paint_bgra_inplace(buf, 8, expected_frame=258,
                                         original_bgra=(9, 8, 7, 255))
        self.assertTrue(r["accepted"])
        self.assertEqual(r["restore_source"], "reported_original")
        self.assertEqual(bytes(buf[0:4]), bytes((9, 8, 7, 255)))

    def test_accept_paint_skip_gate(self):
        buf, _ = self._buf(258)
        r = fc.accept_paint_bgra_inplace(buf, 8, expected_frame=None)
        self.assertTrue(r["restored"])
        self.assertEqual(r["rendered_frame"], 258)
        buf2, _ = self._buf(257)
        r2 = fc.accept_paint_bgra_inplace(buf2, 8, expected_frame=None)
        self.assertTrue(r2["restored"])   # 离线管道：只剥离不裁决
        garbage = bytearray((200, 100, 50, 254) * 32)
        r3 = fc.accept_paint_bgra_inplace(garbage, 8, expected_frame=None)
        self.assertFalse(r3["restored"])  # 无签名结构宁拒不剥

    # -------------------------------- 孪生帧穿透回归（红队 #105 r1 → r2）

    def test_premul_twin_inventory_locked(self):
        """孪生清单全域锁定：预乘签名分组恰为红队证实的 6 对；
        premul_twin_frames 双向互指、逐字节相同、反解必不唯一；直向（PNG
        通路）签名全域单射无孪生；diagnostic A=255 预乘恒等无孪生。"""
        from collections import defaultdict
        groups = defaultdict(list)
        for f in range(65536):
            groups[fc.signature_bgra_premul(fc.MODE_CLEAN, f)].append(f)
        derived = sorted(tuple(v) for v in groups.values() if len(v) > 1)
        self.assertEqual(derived, sorted(_TWIN_PAIRS))
        for a, b in _TWIN_PAIRS:
            self.assertEqual(fc.premul_twin_frames(fc.MODE_CLEAN, a), (b,))
            self.assertEqual(fc.premul_twin_frames(fc.MODE_CLEAN, b), (a,))
            _, ok = fc.decode_bgra_premul(fc.signature_bgra_premul(fc.MODE_CLEAN, a))
            self.assertFalse(ok)
            self.assertNotEqual(fc.signature_rgb(fc.MODE_CLEAN, a),
                                fc.signature_rgb(fc.MODE_CLEAN, b))
        for f in (0, 12, 258, 32601, 32603, 42366):   # 孪生近邻也非孪生
            self.assertEqual(fc.premul_twin_frames(fc.MODE_CLEAN, f), ())
        self.assertEqual(fc.premul_twin_frames(fc.MODE_DIAGNOSTIC, 23167), ())

    def test_accept_paint_twin_stale_rejected_all_six_pairs(self):
        """红队 #105 r1 复现锁（FAIL 主因）：陈旧缓冲区装孪生帧签名、期望帧
        为其孪生（字节完全相同）→ 必须拒绝（ambiguous_signature），不伪报
        expected 帧号；6 对双向全测，缓冲区零改动（宁拒不剥）。"""
        for a, b in _TWIN_PAIRS:
            for stale, exp in ((a, b), (b, a)):
                buf, _ = self._buf(stale)
                before = bytes(buf)
                r = fc.accept_paint_bgra_inplace(buf, 8, expected_frame=exp)
                self.assertFalse(r["accepted"], f"孪生穿透 @stale={stale} exp={exp}")
                self.assertEqual(r["status"], fc.ST_AMBIGUOUS)
                self.assertIn("ambiguous_signature", r["reason"])
                self.assertTrue(r["rejected_stale_paint"])
                self.assertIsNone(r["rendered_frame"])   # 帧号不可归属，不猜
                self.assertEqual(r["twin_frames"], [stale])   # 歧义源=孪生帧
                self.assertEqual(bytes(buf), before)

    def test_accept_paint_twin_expected_genuine_also_rejected(self):
        """协议物理边界（诚实断言）：期望帧孪生在世时其**真迹**同样无法阳性
        确认——闸门一律 ambiguous_signature 拒绝。等待后续 Paint 不收敛（同
        帧号签名恒同），调用方须换帧号重渲/邻帧佐证；这是显式化的物理边界，
        非可实现性缺陷（区分需双像素签名等协议升级）。"""
        for f in (23167, 23168, 32858, 42368):
            buf, _ = self._buf(f)
            before = bytes(buf)
            r = fc.accept_paint_bgra_inplace(buf, 8, expected_frame=f)
            self.assertFalse(r["accepted"], f)
            self.assertEqual(r["status"], fc.ST_AMBIGUOUS)
            self.assertEqual(bytes(buf), before)

    def test_accept_paint_adjacent_twin_lag_scenario(self):
        """lag 场景单列：孪生恰为相邻帧（stale == expected - 1）——CEF #4166
        软件路径同步重放的最典型形态，恰是「精确比对」最危险的伪匹配区。"""
        for stale, exp in _ADJACENT_TWIN_PAIRS:
            self.assertEqual(exp - stale, 1)
            buf, _ = self._buf(stale)
            r = fc.accept_paint_bgra_inplace(buf, 8, expected_frame=exp)
            self.assertFalse(r["accepted"])
            self.assertTrue(r["rejected_stale_paint"])   # 仍计入拒收计数
            self.assertEqual(r["status"], fc.ST_AMBIGUOUS)
        # 对照：非孪生相邻陈旧（257→258）仍走 stale_paint 拒收
        buf, _ = self._buf(257)
        r2 = fc.accept_paint_bgra_inplace(buf, 8, expected_frame=258)
        self.assertFalse(r2["accepted"])
        self.assertIn("stale_paint", r2["reason"])
        self.assertEqual(r2["rendered_frame"], 257)

    def test_accept_paint_skip_gate_twin_ambiguous(self):
        """expected=None（离线剥离管道）：孪生签名歧义 → 宁拒不剥并如实上报
        ambiguous_signature（区别于 no_signature——签名结构在，帧号不可归属）。"""
        for f in (23167, 23168, 32858):
            buf, _ = self._buf(f)
            before = bytes(buf)
            r = fc.accept_paint_bgra_inplace(buf, 8, expected_frame=None)
            self.assertFalse(r["accepted"])
            self.assertFalse(r["restored"])
            self.assertEqual(r["status"], fc.ST_AMBIGUOUS)
            self.assertIn("ambiguous_signature", r["reason"])
            self.assertEqual(bytes(buf), before)


class TestSyntheticDiagnostic(unittest.TestCase):
    """③ 合成 PNG 的 DoD 对抗集（诊断轨）。"""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="fc_diag_"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def _png(self, name: str, arr) -> Path:
        p = self.d / name
        _arr_to_png(arr, p)
        return p

    def test_dod_frame12_on_f0013_lag1_100pct(self):
        """工单 DoD 原文场景：「帧 12 的图像贴在 f0013.png」→ wrong_frame（滞后
        1 帧形态）100%。0..254 全扫：f 的画面贴 f+1 文件名。"""
        for f in range(255):
            r = fc.verify_diagnostic_corners(_diag_frame_arr(f), f + 1)
            self.assertEqual(r["status"], fc.ST_LAG1, f"f={f}: {r}")
            self.assertEqual(r["rendered_frame"], f)
        # DoD 具名锚点：f0013.png 内容为帧 12
        p = self._png("f0013.png", _diag_frame_arr(12))
        r = fc.verify_diagnostic_corners(gg._decode_rgba(p), 13)
        self.assertEqual(r["status"], fc.ST_LAG1)
        self.assertEqual(r["rendered_frame"], 12)

    def test_dod_torn_horizontal_split_100pct(self):
        """工单 DoD：「上半帧 13、下半帧 12 的水平撕裂（左上+右下双 Canary）」
        → torn_frame 100%。0..254 全扫（TL=f+1 上半 / BR=f 下半）。"""
        for f in range(255):
            r = fc.verify_diagnostic_corners(_diag_frame_arr(f + 1, br=f), f + 1)
            self.assertEqual(r["status"], fc.ST_TORN, f"f={f}: {r}")
            self.assertEqual(r["rendered_frame"], f + 1)
            self.assertEqual(r["rendered_frame_br"], f)
        # 具名锚点：上 13 下 12
        r = fc.verify_diagnostic_corners(_diag_frame_arr(13, br=12), 13)
        self.assertEqual(r["status"], fc.ST_TORN)

    def test_dod_torn_tl_banded_and_br_banded(self):
        """块内水平双色带两形态：TL 块半新半旧（扫描线在块内）与 BR 块内撕裂。"""
        a = _diag_frame_arr(13)
        a[4:8, 0:8] = np.array(fc.signature_rgba(fc.MODE_DIAGNOSTIC, 12), np.uint8)
        r = fc.verify_diagnostic_corners(a, 13)
        self.assertEqual(r["status"], fc.ST_TORN)
        self.assertEqual(r["rendered_frames"], [13, 12])
        # BR 块内双色带（TL 一致、撕裂只落在右下）
        b = _diag_frame_arr(13)
        b[H - 4:H, W - 8:W] = np.array(
            fc.signature_rgba(fc.MODE_DIAGNOSTIC, 12), np.uint8)
        r2 = fc.verify_diagnostic_corners(b, 13)
        self.assertEqual(r2["status"], fc.ST_TORN)
        # 交替三段色（非连续整行带）不算撕裂 → foreign 家族，不误报
        c = _diag_frame_arr(13)
        c[2:4, 0:8] = np.array(fc.signature_rgba(fc.MODE_DIAGNOSTIC, 12), np.uint8)
        self.assertEqual(fc.analyze_diag_block(c[0:8, 0:8])["kind"], "foreign")

    def test_static_first_other_checksum_nocanary(self):
        r = fc.verify_diagnostic_corners(_diag_frame_arr(0), 5)
        self.assertEqual(r["status"], fc.ST_STATIC_FIRST)
        r = fc.verify_diagnostic_corners(_diag_frame_arr(40), 50)
        self.assertEqual(r["status"], fc.ST_OTHER)
        # 校验和撕裂：整块同色但 B 通道被改
        a = _diag_frame_arr(13)
        a[0:8, 0:8, 2] = (a[0:8, 0:8, 2].astype(np.uint16) + 1).astype(np.uint8)
        r = fc.verify_diagnostic_corners(a, 13)
        self.assertEqual(r["status"], fc.ST_CHECKSUM)
        # 无 Canary（页面原内容）
        r = fc.verify_diagnostic_corners(_base_arr(), 13)
        self.assertEqual(r["status"], fc.ST_NO_CANARY)
        # 画幅过小
        r = fc.verify_diagnostic_corners(_base_arr(w=4, h=4), 13)
        self.assertEqual(r["status"], fc.ST_NO_CANARY)

    def test_cli_directory_all_ok_and_mixed(self):
        okdir = self.d / "ok"
        okdir.mkdir()
        for f in (0, 1, 2, 13, 90):
            _arr_to_png(_diag_frame_arr(f), okdir / f"f{f:04d}.png")
        rep = self.d / "ok.json"
        rc = gg.main(["verify-canary", "--frames", str(okdir),
                      "--report", str(rep)])
        self.assertEqual(rc, 0)
        r = json.loads(rep.read_text(encoding="utf-8"))
        self.assertTrue(r["ok"])
        self.assertEqual(r["counters"]["ok"], 5)
        self.assertEqual(r["counters"]["wrong_frame_count"], 0)
        # 混合错帧目录：lag1 + static_first + other + torn + no_canary
        mix = self.d / "mix"
        mix.mkdir()
        _arr_to_png(_diag_frame_arr(12), mix / "f0013.png")   # lag1（DoD 场景）
        _arr_to_png(_diag_frame_arr(0), mix / "f0005.png")    # 静态首帧
        _arr_to_png(_diag_frame_arr(40), mix / "f0050.png")   # other
        _arr_to_png(_diag_frame_arr(13, br=12), mix / "f0014.png")  # torn
        _arr_to_png(_base_arr(), mix / "f0020.png")           # no_canary
        rep2 = self.d / "mix.json"
        rc = gg.main(["verify-canary", "--frames", str(mix), "--report", str(rep2)])
        self.assertEqual(rc, 1)
        r2 = json.loads(rep2.read_text(encoding="utf-8"))
        c = r2["counters"]
        self.assertFalse(r2["ok"])
        self.assertEqual(c["ok"], 0)
        self.assertEqual(c["wrong_frame_lag1"], 1)
        self.assertEqual(c["wrong_frame_static_first"], 1)
        self.assertEqual(c["wrong_frame_other"], 1)
        self.assertEqual(c["torn_frame"], 1)
        self.assertEqual(c["no_canary"], 1)
        self.assertEqual(c["wrong_frame_count"], 3)

    def test_cli_single_png_and_errors(self):
        p = self._png("f0013.png", _diag_frame_arr(13))
        self.assertEqual(gg.main(["verify-canary", "--png", str(p)]), 0)
        p2 = self._png("f0013.png", _diag_frame_arr(12))
        self.assertEqual(gg.main(["verify-canary", "--png", str(p2)]), 1)
        # 非 f 命名 + 显式 --frame
        p3 = self._png("shot.png", _diag_frame_arr(13))
        self.assertEqual(gg.main(["verify-canary", "--png", str(p3), "--frame", "13"]), 0)
        self.assertEqual(gg.main(["verify-canary", "--png", str(p3)]), 1)   # 无 --frame
        self.assertEqual(gg.main(["verify-canary", "--png",
                                  str(self.d / "none.png")]), 1)
        self.assertEqual(gg.main(["verify-canary"]), 1)   # --frames/--png 全缺
        self.assertEqual(gg.main(["verify-canary", "--frames",
                                  str(self.d / "nodir")]), 1)

    def test_cli_clean_mode(self):
        base = _base_arr(corner=(16, 20, 24))
        d = self.d / "clean"
        d.mkdir()
        for f in (0, 1, 90):
            _arr_to_png(_clean_frame_arr(base, f), d / f"f{f:04d}.png")
        self.assertEqual(gg.main(["verify-canary", "--frames", str(d),
                                  "--mode", "clean_1px_restore"]), 0)
        _arr_to_png(_clean_frame_arr(base, 12), d / "f0013.png")   # lag1
        rc = gg.main(["verify-canary", "--frames", str(d),
                      "--mode", "clean_1px_restore",
                      "--report", str(self.d / "c.json")])
        self.assertEqual(rc, 1)
        c = json.loads((self.d / "c.json").read_text(encoding="utf-8"))["counters"]
        self.assertEqual(c["wrong_frame_lag1"], 1)
        # 无签名 → checksum_mismatch
        d2 = self.d / "clean2"
        d2.mkdir()
        _arr_to_png(base, d2 / "f0001.png")
        rc = gg.main(["verify-canary", "--frames", str(d2),
                      "--mode", "clean_1px_restore"])
        self.assertEqual(rc, 1)


class TestCleanRestoreTier1(unittest.TestCase):
    """⑤ Clean 1px 原地剥离 → 与无 Canary 基准 Tier-1 全等（DoD）。"""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="fc_clean_"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def _tier1(self, base: np.ndarray, cand: np.ndarray, tag: str):
        ref = self.d / f"ref_{tag}.png"
        cnd = self.d / f"cand_{tag}.png"
        _arr_to_png(base, ref)
        _arr_to_png(cand, cnd)
        r = gg.analyze_frame_diff(ref, cnd)
        self.assertEqual(r["tier"], gg.TIER_EXACT, f"{tag}: {r}")
        self.assertEqual(r["max_abs_diff"], 0, f"{tag}: {r}")
        return r

    def test_neighbor_restore_tier1_uniform_corner(self):
        """均匀角（(0,0)==(1,0)）：邻域 (1,0) 剥离 → Tier-1 全等。"""
        base = _base_arr(corner=(16, 20, 24))
        for f in (0, 1, 13, 90, 4095, 65535):
            cand = _clean_frame_arr(base, f)
            r = fc.restore_clean_1px_rgba_inplace(cand, expected_frame=f)
            self.assertTrue(r["restored"], f)
            self.assertEqual(r["restore_source"], "neighbor_1_0")
            self._tier1(base, cand, f"nb{f}")

    def test_blend_pixel_still_restores(self):
        """±1 预乘混合观感像素（不透明黑底合成）剥离放行 → Tier-1。

        陈旧帧鉴别边界（保守）：f=65535 的 255→254 塌缩恰好构成帧 65278 的
        合法精确签名（校验和同值）——按「精确签名帧号说了算」判 wrong_frame_
        other 并拒绝剥离（宁拒不错；该帧号在不透明单截通路不可阳性确认，
        原生预乘通路无此歧义，见 vtx/frame_canary 模块头）。"""
        base = _base_arr(corner=(0, 0, 0))
        for f in (90, 200, 4095):
            cand = _clean_frame_arr(base, f, blend_black=True)
            r = fc.restore_clean_1px_rgba_inplace(cand, expected_frame=f)
            self.assertTrue(r["restored"], f"f={f}: {r}")
            self._tier1(base, cand, f"bl{f}")
        cand = _clean_frame_arr(base, 65535, blend_black=True)
        r = fc.restore_clean_1px_rgba_inplace(cand, expected_frame=65535)
        self.assertFalse(r["restored"])
        self.assertEqual(r["status"], fc.ST_OTHER)
        self.assertEqual(r["rendered_frame"], 65278)
        obs = tuple(int(v) for v in cand[0, 0])
        self.assertEqual(fc.verify_clean_pixel(obs, 65535)["status"], fc.ST_OTHER)

    def test_reported_original_tier1_nonuniform_corner(self):
        """非均匀角：宿主上报原始色剥离 → Tier-1（issue「或上报的原始 BGRA」）。"""
        base = _base_arr()                       # (0,0)≠(1,0) 渐变角
        original = tuple(int(v) for v in base[0, 0])
        self.assertNotEqual(original, tuple(int(v) for v in base[0, 1]))
        for f in (13, 258, 65535):
            cand = _clean_frame_arr(base, f)
            r = fc.restore_clean_1px_rgba_inplace(cand, expected_frame=f,
                                                  original_rgba=original)
            self.assertTrue(r["restored"])
            self.assertEqual(r["restore_source"], "reported_original")
            self._tier1(base, cand, f"ro{f}")

    def test_neighbor_restore_boundary_nonuniform_corner(self):
        """协议边界如实记录：非均匀角 + 邻域剥离 → 恰 1px 差异，Tier-1 不成立
        （这正是 issue 提供宿主上报原始色通道的原因；不是缺陷是口径）。"""
        base = _base_arr()
        cand = _clean_frame_arr(base, 13)
        r = fc.restore_clean_1px_rgba_inplace(cand, expected_frame=13)
        self.assertTrue(r["restored"])
        ref = self.d / "b_ref.png"
        cnd = self.d / "b_cand.png"
        _arr_to_png(base, ref)
        _arr_to_png(cand, cnd)
        a = gg.analyze_frame_diff(ref, cnd)
        self.assertNotEqual(a["tier"], gg.TIER_EXACT)
        self.assertEqual(a["n_diff_pixels"], 1)
        self.assertLessEqual(a["max_abs_diff"], 255)

    def test_refuse_restore_on_wrong_or_absent_signature(self):
        base = _base_arr(corner=(16, 20, 24))
        cand = _clean_frame_arr(base, 12)
        r = fc.restore_clean_1px_rgba_inplace(cand, expected_frame=13)
        self.assertFalse(r["restored"])                       # 宁拒不错
        self.assertEqual(r["status"], fc.ST_LAG1)
        r2 = fc.restore_clean_1px_rgba_inplace(base.copy(), expected_frame=13)
        self.assertFalse(r2["restored"])
        self.assertEqual(r2["status"], fc.ST_CHECKSUM)
        with self.assertRaises(ValueError):
            fc.restore_clean_1px_rgba_inplace(_base_arr(w=1, h=1), expected_frame=0)

    def test_verify_and_restore_reject_blend_ambiguous_midpoint(self):
        """孪生对撕裂中点（观测同时落在两帧合法签名的 ±1 容差球内，且非任何
        帧的精确签名）：verify → 七态 ambiguous_signature（不猜帧号，无论请求
        帧是否在候选集）；剥离 → 宁拒不剥 + 零改动（与原生闸门同规则的 PNG
        通路投影，issue #105 r2）。"""
        base = _base_arr(corner=(16, 20, 24))
        for obs, exp in (((90, 128, 127), 23168),    # 23167/23168 撕裂中点
                         ((127, 90, 128), 32858)):    # 32602/32858 撕裂中点
            v = fc.verify_clean_pixel(obs + (255,), exp)
            self.assertEqual(v["status"], fc.ST_AMBIGUOUS, (obs, exp, v))
            self.assertIsNone(v["rendered_frame"])
            cand = base.copy()
            cand[0, 0] = np.array(obs + (255,), np.uint8)
            r = fc.restore_clean_1px_rgba_inplace(cand, expected_frame=exp)
            self.assertFalse(r["restored"])
            self.assertEqual(r["status"], fc.ST_AMBIGUOUS)
            self.assertEqual(tuple(int(x) for x in cand[0, 0]), obs + (255,))
        # 请求帧不在候选集的多候选观测：帧号同样不可归属 → ambiguous（detail
        # 如实标注「含请求帧=False」，不武断报 other）
        v2 = fc.verify_clean_pixel((90, 128, 127, 255), 50)
        self.assertEqual(v2["status"], fc.ST_AMBIGUOUS)
        self.assertIn("False", v2["detail"])

    def test_cli_clean_ambiguous_seventh_state(self):
        """CLI 七态：歧义观测 → ambiguous_signature 单列计数（不进
        wrong_frame_count）、exit 1。目录模式帧号域 f%04d=0..9999，孪生帧号
        (≥23167) 只能经单帧 --frame 进入——两形态各测：①目录模式多候选不含
        请求帧（帧号不可归属，宁拒不猜 other）；②单帧模式孪生撕裂中点（含
        请求帧且候选含其预乘孪生——协议固有塌缩）。附带锁定目录模式帧号
        空洞语义：f0000/f0050 之间全缺 ≠ 缺失（本例因 ambiguous 判 1；全 ok
        的空洞目录 exit 0 见 test_cli_directory_all_ok_and_mixed 的抽样洞）。"""
        base = _base_arr(corner=(16, 20, 24))
        d = self.d / "amb"
        d.mkdir()
        a = base.copy()
        a[0, 0] = np.array((90, 128, 127, 255), np.uint8)   # ±1 邻域 4 候选
        _arr_to_png(a, d / "f0050.png")
        _arr_to_png(_clean_frame_arr(base, 0), d / "f0000.png")
        rep = self.d / "amb.json"
        rc = gg.main(["verify-canary", "--frames", str(d),
                      "--mode", "clean_1px_restore", "--report", str(rep)])
        self.assertEqual(rc, 1)
        r = json.loads(rep.read_text(encoding="utf-8"))
        c = r["counters"]
        self.assertFalse(r["ok"])
        self.assertEqual(c["ok"], 1)
        self.assertEqual(c["ambiguous_signature"], 1)
        self.assertEqual(c["wrong_frame_count"], 0)
        # 单帧模式：孪生撕裂中点 + 显式 --frame 23168 → ambiguous（含孪生）
        p = self.d / "twinmid.png"
        _arr_to_png(a, p)
        rep2 = self.d / "amb2.json"
        rc2 = gg.main(["verify-canary", "--png", str(p), "--frame", "23168",
                       "--mode", "clean_1px_restore", "--report", str(rep2)])
        self.assertEqual(rc2, 1)
        r2 = json.loads(rep2.read_text(encoding="utf-8"))
        self.assertEqual(r2["counters"]["ambiguous_signature"], 1)
        self.assertIn("预乘孪生", r2["frames"][0]["detail"])


class TestJsBridge(unittest.TestCase):
    """⑥ get_canary_js 注入桥静态契约。"""

    def test_template_substitution(self):
        for mode in fc.MODES:
            js = fc.get_canary_js(mode)
            self.assertIn(f'"{mode}"', js)
            self.assertIn(str(fc.xor_key(mode)), js)
            self.assertNotIn("__VTX_CANARY", js)     # 占位符零残留
            self.assertIn("data-vtx-canary", js)
            self.assertIn("requestAnimationFrame", js)

    def test_es5_no_modern_syntax(self):
        js = fc.get_canary_js(fc.MODE_CLEAN)
        for bad in ("=>", "let ", "const ", "`", "async ", "class "):
            self.assertNotIn(bad, js, bad)

    def test_page_surface_minimal(self):
        js = fc.get_canary_js(fc.MODE_DIAGNOSTIC)
        self.assertIn("pointer-events:none", js)      # 零交互侵入
        self.assertIn("__vtx_render", js)             # 可选协议桥


@unittest.skipUnless(os.environ.get("VTX_TEST_CHROME"),
                     "set VTX_TEST_CHROME for real canary rendering")
class TestChromeCanary(unittest.TestCase):
    """⑦ 真实 Chrome CDP：注入 → 截图 → 解码 100% / 剥离 Tier-1。

    三 tab 分离（addScriptToEvaluateOnNewDocument 逐 tab 注入且会累积——
    同 tab 混注双模式会叠层互污）：tab_base 无注入出基准帧、tab_diag 注
    诊断轨、tab_clean 注 clean 轨。headless 下 rAF 由截图的 BeginFrame 驱动，
    故每帧走「throwaway 截图兜底 + drawn 探针 + 解码裁决」的收敛循环。"""

    VW, VH = 800, 450

    @classmethod
    def setUpClass(cls):
        from vtx import gfx_render
        cls.d = Path(tempfile.mkdtemp(prefix="fc_chrome_"))
        cls.page = cls.d / "page.html"
        cls.page.write_text(_PAGE, encoding="utf-8")
        cls.real_page = REPO / "examples" / "skill-demo" / "gfxA.html"
        cls.browser = gfx_render.Browser(os.environ["VTX_TEST_CHROME"], tabs=3,
                                         width=cls.VW, height=cls.VH)
        cls.browser.start()
        cls.tab_base, cls.tab_diag, cls.tab_clean = cls.browser.open_tabs()
        cls.tab_diag.call("Page.addScriptToEvaluateOnNewDocument",
                          source=fc.get_canary_js(fc.MODE_DIAGNOSTIC))
        cls.tab_clean.call("Page.addScriptToEvaluateOnNewDocument",
                           source=fc.get_canary_js(fc.MODE_CLEAN))

    @classmethod
    def tearDownClass(cls):
        cls.browser.stop()
        import shutil
        shutil.rmtree(cls.d, ignore_errors=True)

    # ---- 基础设施 ----------------------------------------------------------

    def _url(self, f):
        return f"{self.page.resolve().as_uri()}?f={f}"

    def _shot_verified(self, tab, url, f, mode):
        """导航 + 截图收敛循环：直到 Canary 绘制探针为真且帧级裁决通过。"""
        arr = last = None
        for _ in range(8):
            tab.navigate(url)
            arr = np.array(Image.open(io.BytesIO(tab.shot())).convert("RGBA"))
            drawn = tab.eval("(window.__vtxCanary&&window.__vtxCanary.drawn)||false")
            last = (fc.verify_diagnostic_corners(arr, f) if mode == fc.MODE_DIAGNOSTIC
                    else fc.verify_clean_pixel(arr[0][0], f))
            if drawn and last["status"] == fc.ST_OK:
                return arr, last
        return arr, last

    def _shot_converge(self, tab, url, f, mode):
        """不重新导航的纯截图收敛（页内置帧后：__vtx_render 桥接场景）。"""
        arr = last = None
        for _ in range(8):
            arr = np.array(Image.open(io.BytesIO(tab.shot())).convert("RGBA"))
            last = (fc.verify_diagnostic_corners(arr, f) if mode == fc.MODE_DIAGNOSTIC
                    else fc.verify_clean_pixel(arr[0][0], f))
            if last["status"] == fc.ST_OK:
                return arr, last
        return arr, last

    def _assert_tier1(self, base, cand, ref_name, cand_name):
        ref = self.d / ref_name
        cnd = self.d / cand_name
        _arr_to_png(base, ref)
        _arr_to_png(cand, cnd)
        a = gg.analyze_frame_diff(ref, cnd)
        self.assertEqual(a["tier"], gg.TIER_EXACT, a)
        self.assertEqual(a["max_abs_diff"], 0, a)
        return a

    # ---- 诊断轨 ------------------------------------------------------------

    def test_a_diagnostic_decode_100pct_synthetic(self):
        n_ok = 0
        for f in _DIAG_FRAMES:
            _, r = self._shot_verified(self.tab_diag, self._url(f), f,
                                       fc.MODE_DIAGNOSTIC)
            self.assertEqual(r["status"], fc.ST_OK, f"f={f}: {r}")
            self.assertEqual(r["rendered_frame"], f)
            n_ok += 1
        self.assertEqual(n_ok, len(_DIAG_FRAMES))    # 解码正确率 100%

    def test_b_diagnostic_zero_intrusion_real_page(self):
        url = f"{self.real_page.resolve().as_uri()}?f={{f}}"
        for f in (0, 12, 47):
            _, r = self._shot_verified(self.tab_diag, url.format(f=f), f,
                                       fc.MODE_DIAGNOSTIC)
            self.assertEqual(r["status"], fc.ST_OK, f"f={f}: {r}")

    def test_c_vtx_render_bridge_recolors(self):
        """__vtx_render(f2) 桥接后签名色随新帧号重绘（#105b 陈旧色回归锁）。"""
        _, r = self._shot_verified(self.tab_diag, self._url(3), 3,
                                   fc.MODE_DIAGNOSTIC)
        self.assertEqual(r["status"], fc.ST_OK)
        self.tab_diag.eval(
            "window.__vtx_render = function(n){"
            "document.getElementById('t').textContent='f='+n;return n;};"
            "window.__vtx_render(200);")
        _, r2 = self._shot_converge(self.tab_diag, self._url(3), 200,
                                    fc.MODE_DIAGNOSTIC)
        self.assertEqual(r2["status"], fc.ST_OK, r2)
        self.assertEqual(
            self.tab_diag.eval("(window.__vtxCanary&&window.__vtxCanary.frame)||-1"),
            200)

    # ---- Clean 轨 ------------------------------------------------------------

    def test_d_clean_verify_and_tier1_restore(self):
        """clean 轨：解码 ok + 剥离 → 与无注入基准 Tier-1 全等（DoD）。

        合成页左上角均匀（(0,0)==(1,0)）→ 协议首选的邻域 (1,0) 剥离即达
        Tier-1（零宿主信息路径）；角不均匀时自动改用上报原始色路径。"""
        for f in _CLEAN_FRAMES:
            self.tab_base.navigate(self._url(f))
            base2 = np.array(
                Image.open(io.BytesIO(self.tab_base.shot())).convert("RGBA"))
            base = np.array(
                Image.open(io.BytesIO(self.tab_base.shot())).convert("RGBA"))
            self.assertTrue((base == base2).all(),
                            "基准 tab 两次截图必须逐像素稳定")
            arr, r = self._shot_verified(self.tab_clean, self._url(f), f,
                                         fc.MODE_CLEAN)
            self.assertEqual(r["status"], fc.ST_OK, f"f={f}: {r}")
            corner_uniform = bool((base[0, 0] == base[0, 1]).all())
            res = fc.restore_clean_1px_rgba_inplace(
                arr, expected_frame=f,
                original_rgba=None if corner_uniform
                else tuple(int(v) for v in base[0, 0]))
            self.assertTrue(res["restored"], f"f={f}: {res}")
            if corner_uniform:
                self.assertEqual(res["restore_source"], "neighbor_1_0")
            self._assert_tier1(base, arr, f"chrome_ref_{f}.png",
                               f"chrome_restored_{f}.png")

    def test_e_clean_zero_intrusion_real_page(self):
        """真实页 gfxA.html：clean 解码 ok + 上报原始色剥离 → Tier-1。"""
        f = 12
        url = f"{self.real_page.resolve().as_uri()}?f={f}"
        self.tab_base.navigate(url)
        base = np.array(
            Image.open(io.BytesIO(self.tab_base.shot())).convert("RGBA"))
        arr, r = self._shot_verified(self.tab_clean, url, f, fc.MODE_CLEAN)
        self.assertEqual(r["status"], fc.ST_OK, r)
        res = fc.restore_clean_1px_rgba_inplace(
            arr, expected_frame=f,
            original_rgba=tuple(int(v) for v in base[0, 0]))
        self.assertTrue(res["restored"], res)
        self.assertEqual(res["restore_source"], "reported_original")
        self._assert_tier1(base, arr, "gfxA_ref.png", "gfxA_restored.png")


if __name__ == "__main__":
    unittest.main()
