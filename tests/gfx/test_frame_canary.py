#!/usr/bin/env python3
"""frame_canary 单测：issue #105 r4 双轨帧同步水印协议 **v2 双像素签名**全链验收。

v2 协议（升级根因=红队 #105 r1/r2/r3 三轮 FAIL 的等价类根因：单像素下
「观测必为直向签名」不成立，陈旧预乘观感可逐字节等于他帧直向签名）：
  - clean 轨：同一扫描线双像素 P1@(0,0)（R=hi,G=lo,B=hi^lo^0x5A）+ P2@(0,1)
    （R=lo,G=hi 通道互换，B=(16R+22G+0x3C)&0xFF 独立加性校验和），A=254；
  - 诊断轨：左上 8x8=P1 公式色、右下 8x8=P2 公式色（diagnostic 键 0xA5/
    0xC3），双角同帧号才 ok；
  - 判定：S1∩S2=={f} 才阳性确认（交集空/多候选→ambiguous 宁拒不猜）；
  - 全域穷举背书（/tmp/framivra-evidence/issue-105-r4/）：PNG 精确/±1 对抗/
    物理引擎映射三模型穿透对 0；真迹 65536 帧全可确认（v1 为 12 帧不可）；
    BGRA 联合孪生 0、陈旧归因全域单例。

覆盖面（与工单 DoD 逐条对齐）：
  ① 协议纯函数：P1/P2 冻结签名公式锚点、16-bit 帧号域、四组合同帧跨模式
     互斥（P1d/P1c/P2d/P2c 全域两两不同）、双像素签名全域单射、classify 全表；
  ② 预乘原生通路：精确反像区间、双像素 65536 帧 roundtrip 全可确认（v1 的
     12 孪生帧缺口闭合）、±1 扰动匹配、accept_paint_bgra_inplace v2 闸门
     （双像素全等才 accept+原地双剥离(行1 邻域)、陈旧拒收零改动+**可靠归因**
     rendered==陈旧帧号、宿主上报原始对优先、skip-gate）；
  ③ 合成 PNG DoD：「帧 12 贴 f0013.png」→ wrong_frame_lag1 100%（0..254 全
     扫）+ 滞后/静态首帧/其他/校验和撕裂/no_canary 全表 + 撕裂两形态
     （TL/BR 双 Canary 相异〔v2=两公式色相异〕与 TL/BR 块内水平双色带）
     → torn_frame 100%（0..254 全扫）+ v2 新态：右下 P2 结构缺失 →
     ambiguous（双像素硬条件）；
  ④ CLI verify-canary：帧目录全 ok exit 0 / 混合错帧 exit 1 + 计数器与
     wrong_frame_count、单帧 --png/--frame、clean 双像素模式、缺文件/缺参
     exit 1、report JSON、七态 ambiguous_signature 单列计数；
  ⑤ Clean 双像素剥离 Tier-1（DoD）：均匀角行 1 邻域双剥离 → Tier-1 全等；
     ±1 预乘混合观感放行（含 f=65535——v1 曾拒，v2 确认）；宿主上报原始
     色对 → Tier-1；错帧签名宁拒不剥；不相容/无签名拒绝；画幅契约 ≥2x2；
  ⑥ JS 注入桥：双像素模板替换（mode/双键/双系数）、无占位符残留、ES5
     约束、双 div 位置（(0,0)+(1px,0) 同扫描线 / tl+br 双块）；
  ⑦ 真实 Chrome（env VTX_TEST_CHROME）：CDP 注入 → 双轨双像素解码 100%
     （含 v1 不可确认的孪生帧 23168 与 65535——v2 全域可确认的实证）+
     双像素剥离 Tier-1 + __vtx_render 桥接重绘换帧（#105b 陈旧色回归）；
  ⑧ 红队场景翻绿回归（r1/r2/r3 全案）：v1 全部穿透对在 v2 下「正确归因
     拒绝」——stale X 的预乘观感 verify 必 rendered==X（never ok(F))、strip
     必拒；单像素孪生等价类仍在（P1 6 对）但联合消费无恙（真迹同帧确认）。
"""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
from vtx import frame_canary as fc   # noqa: E402
from scripts.gfx import gfx_golden as gg              # noqa: E402
from PIL import Image                # noqa: E402

W, H = 64, 48

# Chrome 集成段的实证口径（帧数受控，全套 <90s）。v2 全域可确认：clean 轨
# 加入 23168（v1 预乘孪生不可确认帧）与 65535（v1 黑底塌缩不可确认帧），
# 以真 Chrome 实证 v2 的「12 帧缺口闭合 + 塌缩帧确认」。
_DIAG_FRAMES = [0, 1, 2, 3, 12, 13, 90, 255, 256, 511, 4095, 65535]
_CLEAN_FRAMES = [0, 1, 90, 4095, 23168, 65535]

# P1 预乘孪生对（红队 #105 r1 + 全域复核锁定；v2 下**单像素**等价类仍在，
# P2 孪生为空、联合孪生为空——双像素消费面无恙，见 TestPremulNative）。
_TWIN_PAIRS = ((23167, 23168), (32602, 32858), (32639, 32896),
               (32640, 32895), (32677, 32933), (42367, 42368))
_ADJACENT_TWIN_PAIRS = ((23167, 23168), (42367, 42368))   # 恰滞后 1 帧（lag 形态）

# 红队 #105 r1/r2/r3 全部穿透案（stale X 观感 == 帧 F 的 v1 单像素签名）：
# v2 下全部「正确归因拒绝」（rendered==X，never ok(F)）。
_REDTEAM_STALE_CASES = (
    (129, 128), (131, 130), (133, 132), (135, 134),      # r3 非孪生精确支样本
    (33665, 33408), (33044, 32788),                      # r3 具名远对
    (23168, 23167), (32858, 32602), (32896, 32639),      # r2 孪生方向精确支
    (23169, 23168), (33114, 32858), (33153, 32896),      # r2 漏报孪生方向
    (65535, 65278),                                      # 255→254 塌缩口径
)

# 合成帧驱动页：不透明深色底（左上角均匀 → 行 1 邻域剥离 Tier-1 可证）、随 f
# 移动的条与数字（画面内容=帧号纯函数，帧驱动铁律）
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
    """合成诊断轨 v2 帧：页面内容随帧号变化 + TL 8x8 P1 签名块（+BR 对账
    P2 签名块）。br=None 时 BR 块与 TL 同帧号（正常帧）；撕裂帧显式给 BR
    帧号。"""
    a = np.zeros((h, w, 4), np.uint8)
    a[..., 0] = (rendered * 37) % 256
    a[..., 1] = (rendered * 91) % 256
    a[..., 2] = (rendered * 151) % 256
    a[..., 3] = 255
    a[0:8, 0:8] = np.array(fc.signature_rgba(fc.MODE_DIAGNOSTIC, rendered),
                           np.uint8)
    a[h - 8:h, w - 8:w] = np.array(
        fc.signature2_rgba(fc.MODE_DIAGNOSTIC, br if br is not None else rendered),
        np.uint8)
    return a


def _clean_frame_arr(base: np.ndarray, rendered: int,
                     blend_black: bool = False) -> np.ndarray:
    """合成 clean 轨 v2 帧：base（无 Canary 基准）(0,0)←P1、(0,1)←P2 签名。

    blend_black=True 时写入 round(C·254/255)（rgba(254/255) 签名在不透明黑底
    合成通路的实际观感，±1 抗舍入靶标）。"""
    a = base.copy()
    k = 254.0 / 255.0
    r, g, b = fc.signature_rgb(fc.MODE_CLEAN, rendered)
    r2, g2, b2 = fc.signature2_rgb(fc.MODE_CLEAN, rendered)
    if blend_black:
        a[0, 0] = (int(r * k + 0.5), int(g * k + 0.5), int(b * k + 0.5), 255)
        a[0, 1] = (int(r2 * k + 0.5), int(g2 * k + 0.5), int(b2 * k + 0.5), 255)
    else:
        a[0, 0] = (r, g, b, 255)
        a[0, 1] = (r2, g2, b2, 255)
    return a


def _base_arr(w=W, h=H, corner=None) -> np.ndarray:
    """无 Canary 基准帧：内容沿 x/y 双向渐变（(0,0)≠(0,1)≠(1,0)≠(1,1)——
    v2 邻域剥离源在行 1，非均匀角边界测试需要 y 向梯度）；corner=(r,g,b)
    时左上角 8x8 同色。"""
    a = np.zeros((h, w, 4), np.uint8)
    x = np.arange(w, dtype=np.int32)
    y = np.arange(h, dtype=np.int32)
    a[..., 0] = (x * 3) % 256
    a[..., 1] = (80 + y[:, None] * 2) % 256
    a[..., 2] = 200
    a[..., 3] = 255
    if corner is not None:
        a[0:8, 0:8] = np.array(corner + (255,), np.uint8)
    return a


def _premul_obs(f: int):
    """帧 f 双像素的黑底观感（陈旧绘制的物理模型，红队 r3 同款）。"""
    k = 254.0 / 255.0
    r, g, b = fc.signature_rgb(fc.MODE_CLEAN, f)
    r2, g2, b2 = fc.signature2_rgb(fc.MODE_CLEAN, f)
    return ((int(r * k + 0.5), int(g * k + 0.5), int(b * k + 0.5), 255),
            (int(r2 * k + 0.5), int(g2 * k + 0.5), int(b2 * k + 0.5), 255))


class TestProtocolPure(unittest.TestCase):
    """① v2 冻结签名公式与全域编解码。"""

    def test_signature_formula_frozen(self):
        # P1 工单冻结公式手算锚点：f=13 → hi=0, lo=13
        self.assertEqual(fc.signature_rgb(fc.MODE_DIAGNOSTIC, 13), (0, 13, 0 ^ 13 ^ 0xA5))
        self.assertEqual(fc.signature_rgba(fc.MODE_DIAGNOSTIC, 13)[3], 255)
        self.assertEqual(fc.signature_rgb(fc.MODE_CLEAN, 13), (0, 13, 0 ^ 13 ^ 0x5A))
        self.assertEqual(fc.signature_rgba(fc.MODE_CLEAN, 13)[3], 254)
        # f=0x0102=258 → hi=1, lo=2
        self.assertEqual(fc.signature_rgb(fc.MODE_DIAGNOSTIC, 258), (1, 2, 1 ^ 2 ^ 0xA5))
        self.assertEqual(fc.signature_bgra(fc.MODE_CLEAN, 258), (2 ^ 1 ^ 0x5A, 2, 1, 254))
        # P2 v2 冻结锚点：通道互换 + 加性校验和 (16R+22G+K2)&0xFF
        self.assertEqual(fc.signature2_rgb(fc.MODE_DIAGNOSTIC, 258),
                         (2, 1, (16 * 2 + 22 * 1 + 0xC3) & 0xFF))
        self.assertEqual(fc.signature2_rgb(fc.MODE_CLEAN, 258),
                         (2, 1, (16 * 2 + 22 * 1 + 0x3C) & 0xFF))
        self.assertEqual(fc.signature2_rgba(fc.MODE_CLEAN, 258)[3], 254)
        self.assertEqual(fc.P2_MUL_R, 16)
        self.assertEqual(fc.P2_MUL_G, 22)
        self.assertEqual(fc.CLEAN_P1_XY, (0, 0))
        self.assertEqual(fc.CLEAN_P2_XY, (1, 0))   # 同扫描线（撕裂不可拆）

    def test_domain_and_mode_validation(self):
        for bad in (-1, 65536, 1 << 20):
            self.assertRaises(ValueError, fc.signature_rgb, fc.MODE_DIAGNOSTIC, bad)
            self.assertRaises(ValueError, fc.signature2_rgb, fc.MODE_DIAGNOSTIC, bad)
            self.assertRaises(ValueError, fc.verify_clean_pixels,
                              (0, 0, 0, 255), (0, 0, 0, 255), bad)
        self.assertRaises(ValueError, fc.get_canary_js, "nonsense")
        self.assertRaises(ValueError, fc.xor_key, "")
        self.assertRaises(ValueError, fc.analyze_diag_block, [[0] * 8] * 8, pixel=3)
        self.assertEqual(fc.MODES, (fc.MODE_DIAGNOSTIC, fc.MODE_CLEAN))

    def test_cross_mode_never_collide_full_domain(self):
        """四组合同帧跨模式互斥（全域）：P1d≠P1c（xor 键）、P2d≠P2c（加性键）、
        P1d≠P2c / P2d≠P1c（对角奇偶分离：K2c 偶 / K2d 奇 + 系数和偶）。"""
        for f in range(65536):
            d1, c1 = fc.signature_rgb(fc.MODE_DIAGNOSTIC, f), fc.signature_rgb(fc.MODE_CLEAN, f)
            d2, c2 = fc.signature2_rgb(fc.MODE_DIAGNOSTIC, f), fc.signature2_rgb(fc.MODE_CLEAN, f)
            if d1 == c1 or d2 == c2 or d1 == c2 or d2 == c1:
                self.fail(f"跨模式签名撞色 @f={f}")
            self.assertEqual(fc.decode_pixel(fc.MODE_DIAGNOSTIC, *d1), (f, True))
            self.assertEqual(fc.decode_pixel(fc.MODE_CLEAN, *c1), (f, True))
            self.assertEqual(fc.decode_pixel2(fc.MODE_DIAGNOSTIC, *d2), (f, True))
            self.assertEqual(fc.decode_pixel2(fc.MODE_CLEAN, *c2), (f, True))

    def test_dual_signature_injective_full_domain(self):
        """双像素签名各自全域单射（P1/P2 颜色空间内部无碰撞）。"""
        seen1, seen2 = {}, {}
        for f in range(65536):
            c1 = fc.signature_rgb(fc.MODE_CLEAN, f)
            c2 = fc.signature2_rgb(fc.MODE_CLEAN, f)
            self.assertNotIn(c1, seen1, f"P1 撞色 {f} vs {seen1.get(c1)}")
            self.assertNotIn(c2, seen2, f"P2 撞色 {f} vs {seen2.get(c2)}")
            seen1[c1], seen2[c2] = f, f

    def test_classify_frame_table(self):
        self.assertEqual(fc.classify_frame(13, 13), fc.ST_OK)
        self.assertEqual(fc.classify_frame(12, 13), fc.ST_LAG1)
        self.assertEqual(fc.classify_frame(0, 5), fc.ST_STATIC_FIRST)
        self.assertEqual(fc.classify_frame(0, 0), fc.ST_OK)
        self.assertEqual(fc.classify_frame(40, 50), fc.ST_OTHER)
        self.assertEqual(fc.classify_frame(14, 13), fc.ST_OTHER)


class TestPremulNative(unittest.TestCase):
    """② 原生 BGRA_PREMUL 通路（CEF/Electron OnPaint 消费面，v2 双像素闸门）。"""

    def test_premul_cands_exact_preimage(self):
        # 全部 256 个直存值：反像集 == {C : round_half_up(C·254/255) == v}
        def store(c):
            return (508 * c + 255) // 510
        for v in range(256):
            exact = {c for c in range(256) if store(c) == v}
            self.assertEqual(set(fc._premul_cands(v)), exact, f"v={v}")

    def test_premul_signature_formula(self):
        b, g, r, a = fc.signature_bgra_premul(fc.MODE_CLEAN, 13)
        self.assertEqual((b, g, r, a), (int((13 ^ 0x5A) * 254 / 255 + 0.5),
                                        int(13 * 254 / 255 + 0.5), 0, 254))
        b2, g2, r2, a2 = fc.signature2_bgra_premul(fc.MODE_CLEAN, 13)
        self.assertEqual((b2, g2, r2, a2),
                         (int(((16 * 13 + 22 * 0 + 0x3C) & 0xFF) * 254 / 255 + 0.5),
                          0, int(13 * 254 / 255 + 0.5), 254))
        self.assertEqual(fc.signature_bgra_premul(fc.MODE_DIAGNOSTIC, 0)[3], 255)

    def test_dual_roundtrip_full_domain_all_confirmable(self):
        """v2 核心实证（穷举背书的测试化）：65536 帧双像素预乘签名全部可阳性
        确认——联合孪生全域空（v1 有 12 帧孪生不可确认，v2 缺口闭合）。"""
        n_ok = 0
        for f in range(65536):
            s1 = fc.signature_bgra_premul(fc.MODE_CLEAN, f)
            s2 = fc.signature2_bgra_premul(fc.MODE_CLEAN, f)
            i1 = fc._premul_decode_matches(s1, pixel=1)
            i2 = fc._premul_decode_matches(s2, pixel=2)
            self.assertEqual(i1 & i2, {f}, f"帧 {f} 双像素交集非单例")
            n_ok += 1
        self.assertEqual(n_ok, 65536)

    def test_match_perturbation(self):
        import random
        random.seed(105)
        for _ in range(20000):
            f = random.randrange(65536)
            for pixel, sig in ((1, fc.signature_bgra_premul(fc.MODE_CLEAN, f)),
                               (2, fc.signature2_bgra_premul(fc.MODE_CLEAN, f))):
                px = tuple(max(0, min(255, c + random.choice((-1, 0, 1))))
                           for c in sig[:3]) + (sig[3],)
                self.assertTrue(fc.match_bgra_premul(px, f, pixel=pixel))
                self.assertFalse(fc.match_bgra_premul(
                    px[:3] + (255,), f, pixel=pixel))   # α 破坏必拒

    def _buf(self, f, w=8, h=4):
        """整帧 BGRA_PREMUL bytearray：(0,0)=P1、(0,1)=P2 签名（预乘），
        其余=行 1 剥离源邻色（gfx 叠层页常态的透明像素）。"""
        neighbor = (17, 34, 51, 0)
        buf = bytearray(bytes(neighbor) * (w * h))
        buf[0:4] = bytes(fc.signature_bgra_premul(fc.MODE_CLEAN, f))
        buf[4:8] = bytes(fc.signature2_bgra_premul(fc.MODE_CLEAN, f))
        return buf, neighbor

    def test_accept_paint_match_and_restore(self):
        buf, neighbor = self._buf(258)
        before = bytes(buf)
        r = fc.accept_paint_bgra_inplace(buf, 8, expected_frame=258)
        self.assertTrue(r["accepted"])
        self.assertFalse(r["rejected_stale_paint"])
        self.assertEqual(r["rendered_frame"], 258)
        self.assertTrue(r["restored"])
        self.assertEqual(r["restore_source"], "neighbor_row1")
        self.assertEqual(bytes(buf[0:4]), bytes(neighbor))   # (0,0) ← (1,0)
        self.assertEqual(bytes(buf[4:8]), bytes(neighbor))   # (0,1) ← (1,1)
        self.assertEqual(bytes(buf[8:]), before[8:])         # 其余像素零改动

    def test_accept_paint_stale_rejected_reliable_attribution(self):
        """陈旧帧拒收 + **可靠归因**（v2 穷举背书：联合孪生空 → rendered 恒为
        陈旧帧自身帧号，不再是 None/猜测）——红队三案在闸门侧的翻绿。"""
        for stale in (257, 0, 65535, 129, 33665, 23168):
            buf, _ = self._buf(stale)
            before = bytes(buf)
            r = fc.accept_paint_bgra_inplace(buf, 8, expected_frame=258)
            self.assertFalse(r["accepted"], stale)
            self.assertTrue(r["rejected_stale_paint"])
            self.assertFalse(r["restored"])
            self.assertIn("stale_paint", r["reason"])
            self.assertEqual(r["rendered_frame"], stale,
                             f"陈旧 {stale} 必被正确归因（v2 可靠归因口径）")
            self.assertEqual(bytes(buf), before, "陈旧帧拒绝必须零改动缓冲区")

    def test_accept_paint_reported_original(self):
        buf, _ = self._buf(258)
        r = fc.accept_paint_bgra_inplace(
            buf, 8, expected_frame=258,
            original_bgra=((9, 8, 7, 255), (5, 4, 3, 255)))
        self.assertTrue(r["accepted"])
        self.assertEqual(r["restore_source"], "reported_original")
        self.assertEqual(bytes(buf[0:4]), bytes((9, 8, 7, 255)))
        self.assertEqual(bytes(buf[4:8]), bytes((5, 4, 3, 255)))

    def test_accept_paint_skip_gate(self):
        buf, _ = self._buf(258)
        r = fc.accept_paint_bgra_inplace(buf, 8, expected_frame=None)
        self.assertTrue(r["restored"])
        self.assertEqual(r["rendered_frame"], 258)
        buf2, _ = self._buf(257)
        r2 = fc.accept_paint_bgra_inplace(buf2, 8, expected_frame=None)
        self.assertTrue(r2["restored"])                     # 离线管道：只剥离不裁决
        garbage = bytearray((200, 100, 50, 254) * 32)
        r3 = fc.accept_paint_bgra_inplace(garbage, 8, expected_frame=None)
        self.assertFalse(r3["restored"])                    # 无签名结构宁拒不剥
        self.assertEqual(r3["reason"], "no_signature")
        with self.assertRaises(ValueError):
            fc.accept_paint_bgra_inplace(bytearray(8), 1, expected_frame=None)  # <2x2

    # -------------------------------- 孪生等价类（v2 语义） -------------------

    def test_premul_twin_inventory_locked(self):
        """孪生清单全域锁定：P1 单像素 6 对（v1 冻结清单保持）；P2 单像素
        0 对；**联合孪生全域空**（v2 闸门/校验消费面无不可确认帧）。
        diagnostic A=255 预乘恒等无孪生。"""
        from collections import defaultdict
        for pixel, pairs_expect in ((1, sorted(_TWIN_PAIRS)), (2, [])):
            groups = defaultdict(list)
            for f in range(65536):
                sig = (fc.signature_bgra_premul(fc.MODE_CLEAN, f) if pixel == 1
                       else fc.signature2_bgra_premul(fc.MODE_CLEAN, f))
                groups[sig].append(f)
            derived = sorted(tuple(v) for v in groups.values() if len(v) > 1)
            self.assertEqual(derived, pairs_expect, f"pixel={pixel}")
        for f in range(0, 65536, 997):
            self.assertEqual(fc.premul_twin_frames(fc.MODE_CLEAN, f), ())
        self.assertEqual(fc.premul_twin_frames(fc.MODE_DIAGNOSTIC, 23167), ())
        self.assertEqual(fc.premul_twin_frames_pixel(fc.MODE_DIAGNOSTIC, 23167, 1), ())

    def test_twin_frames_genuine_now_confirmable(self):
        """v1 协议物理边界闭合实证：12 个 P1 孪生帧在 v2 双像素闸门下真迹
        **全部可接受**（v1 曾一律 ambiguous 拒绝、两通路均不可阳性确认）。
        陈旧孪生冒充（stale X、expected=孪生对端）仍拒——字节相等的 P1 被
        P2 拆穿。"""
        for a, b in _TWIN_PAIRS:
            for f in (a, b):                       # 真迹：accept
                buf, _ = self._buf(f)
                r = fc.accept_paint_bgra_inplace(buf, 8, expected_frame=f)
                self.assertTrue(r["accepted"], f"孪生真迹 {f} v2 必可确认: {r}")
                self.assertEqual(r["rendered_frame"], f)
            for stale, exp in ((a, b), (b, a)):    # 冒充：拒 + 正确归因
                buf, _ = self._buf(stale)
                before = bytes(buf)
                r = fc.accept_paint_bgra_inplace(buf, 8, expected_frame=exp)
                self.assertFalse(r["accepted"], (stale, exp))
                self.assertTrue(r["rejected_stale_paint"])
                self.assertEqual(r["rendered_frame"], stale,
                                 "P1 字节相等被 P2 拆穿——归因到陈旧帧自身")
                self.assertEqual(bytes(buf), before)

    def test_accept_paint_adjacent_twin_lag_scenario(self):
        """lag 场景单列：孪生恰为相邻帧（stale == expected - 1）——CEF #4166
        软件路径同步重放的最典型形态；v2 下 P2 拆穿并正确归因 stale。"""
        for stale, exp in _ADJACENT_TWIN_PAIRS:
            self.assertEqual(exp - stale, 1)
            buf, _ = self._buf(stale)
            r = fc.accept_paint_bgra_inplace(buf, 8, expected_frame=exp)
            self.assertFalse(r["accepted"])
            self.assertTrue(r["rejected_stale_paint"])
            self.assertIn("stale_paint", r["reason"])
            self.assertEqual(r["rendered_frame"], stale)
        # 对照：非孪生相邻陈旧（257→258）同走 stale_paint 拒收
        buf, _ = self._buf(257)
        r2 = fc.accept_paint_bgra_inplace(buf, 8, expected_frame=258)
        self.assertFalse(r2["accepted"])
        self.assertEqual(r2["rendered_frame"], 257)


class TestSyntheticDiagnostic(unittest.TestCase):
    """③ 合成 PNG 的 DoD 对抗集（诊断轨 v2：TL=P1 公式、BR=P2 公式）。"""

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
        1 帧形态）100%。0..254 全扫：f 的画面贴 f+1 文件名（双角同帧号）。"""
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
        → torn_frame 100%。0..254 全扫（TL=P1(f+1) 上半 / BR=P2(f) 下半）。"""
        for f in range(255):
            r = fc.verify_diagnostic_corners(_diag_frame_arr(f + 1, br=f), f + 1)
            self.assertEqual(r["status"], fc.ST_TORN, f"f={f}: {r}")
            self.assertEqual(r["rendered_frame"], f + 1)
            self.assertEqual(r["rendered_frame_br"], f)
        # 具名锚点：上 13 下 12
        r = fc.verify_diagnostic_corners(_diag_frame_arr(13, br=12), 13)
        self.assertEqual(r["status"], fc.ST_TORN)

    def test_dod_torn_tl_banded_and_br_banded(self):
        """块内水平双色带两形态：TL 块半新半旧（P1 色带）与 BR 块内撕裂
        （P2 色带）——两像素/两块的撕裂形态都测。"""
        a = _diag_frame_arr(13)
        a[4:8, 0:8] = np.array(fc.signature_rgba(fc.MODE_DIAGNOSTIC, 12), np.uint8)
        r = fc.verify_diagnostic_corners(a, 13)
        self.assertEqual(r["status"], fc.ST_TORN)
        self.assertEqual(r["rendered_frames"], [13, 12])
        # BR 块内双色带（TL 一致、撕裂只落在右下；带色=P2 公式）
        b = _diag_frame_arr(13)
        b[H - 4:H, W - 8:W] = np.array(
            fc.signature2_rgba(fc.MODE_DIAGNOSTIC, 12), np.uint8)
        r2 = fc.verify_diagnostic_corners(b, 13)
        self.assertEqual(r2["status"], fc.ST_TORN)
        self.assertEqual(r2["rendered_frames_br"], [13, 12])
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

    def test_br_p2_missing_is_ambiguous_v2(self):
        """v2 新态：右下 P2 结构缺失（页面内容遮挡/未注入第二因子）→ 双像素
        确认不可能 → ambiguous_signature（v1 曾豁免 foreign 右下块；v2 宁拒
        不猜——单像素 P1 正是 r3 穿透面）。"""
        a = _diag_frame_arr(13)
        a[H - 8:H, W - 8:W] = np.array((37, 91, 151, 255), np.uint8)  # 杂色 BR
        r = fc.verify_diagnostic_corners(a, 13)
        self.assertEqual(r["status"], fc.ST_AMBIGUOUS)
        self.assertIsNone(r["rendered_frame"])
        # BR 是合法 P2 色但被当作 P1 解（公式失配）→ bad_checksum → 同拒
        a2 = _diag_frame_arr(13)
        a2[H - 8:H, W - 8:W] = np.array(
            fc.signature_rgba(fc.MODE_DIAGNOSTIC, 13), np.uint8)  # P1 色放 BR 位
        r2 = fc.verify_diagnostic_corners(a2, 13)
        self.assertEqual(r2["status"], fc.ST_AMBIGUOUS)

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
        # 混合错帧目录：lag1 + static_first + other + torn + no_canary + ambiguous
        mix = self.d / "mix"
        mix.mkdir()
        _arr_to_png(_diag_frame_arr(12), mix / "f0013.png")   # lag1（DoD 场景）
        _arr_to_png(_diag_frame_arr(0), mix / "f0005.png")    # 静态首帧
        _arr_to_png(_diag_frame_arr(40), mix / "f0050.png")   # other
        _arr_to_png(_diag_frame_arr(13, br=12), mix / "f0014.png")  # torn
        _arr_to_png(_base_arr(), mix / "f0020.png")           # no_canary
        a = _diag_frame_arr(13)
        a[H - 8:H, W - 8:W] = np.array((37, 91, 151, 255), np.uint8)
        _arr_to_png(a, mix / "f0021.png")                     # ambiguous（BR 缺失）
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
        self.assertEqual(c["ambiguous_signature"], 1)
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

    def test_cli_clean_mode_dual_pixels(self):
        base = _base_arr(corner=(16, 20, 24))
        d = self.d / "clean"
        d.mkdir()
        for f in (0, 1, 90, 23168, 65535):     # v2：孪生帧/塌缩帧同可确认
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
    """⑤ Clean 双像素原地剥离 → 与无 Canary 基准 Tier-1 全等（DoD）。"""

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
        """均匀角（行 0 == 行 1）：行 1 邻域双剥离 → Tier-1 全等（v2 邻域源
        从 v1 的右邻 (0,1) 改为行 1 的 (1,0)/(1,1)——(0,1) 现为 P2 签名位）。"""
        base = _base_arr(corner=(16, 20, 24))
        for f in (0, 1, 13, 90, 4095, 23168, 65535):
            cand = _clean_frame_arr(base, f)
            r = fc.restore_clean_2px_rgba_inplace(cand, expected_frame=f)
            self.assertTrue(r["restored"], f)
            self.assertEqual(r["restore_source"], "neighbor_row1")
            self._tier1(base, cand, f"nb{f}")

    def test_blend_pixel_still_restores(self):
        """±1 预乘混合观感双像素（不透明黑底合成）剥离放行 → Tier-1。

        v2 边界翻转锚点：f=65535 的 255→254 塌缩在 v1 单像素曾判 other 拒剥
        （65278 精确签名陷阱）；v2 双像素 S2 自定位 → 确认 65535 真迹。"""
        base = _base_arr(corner=(0, 0, 0))
        for f in (90, 200, 4095, 23168, 65535):
            cand = _clean_frame_arr(base, f, blend_black=True)
            r = fc.restore_clean_2px_rgba_inplace(cand, expected_frame=f)
            self.assertTrue(r["restored"], f"f={f}: {r}")
            self.assertEqual(r["rendered_frame"], f)
            self._tier1(base, cand, f"bl{f}")

    def test_reported_original_tier1_nonuniform_corner(self):
        """非均匀角：宿主上报原始色对（双像素）剥离 → Tier-1。"""
        base = _base_arr()                       # (0,0)≠(1,0) 渐变角
        o1 = tuple(int(v) for v in base[0, 0])
        o2 = tuple(int(v) for v in base[0, 1])
        self.assertNotEqual(o1, tuple(int(v) for v in base[1, 0]))
        for f in (13, 258, 65535):
            cand = _clean_frame_arr(base, f)
            r = fc.restore_clean_2px_rgba_inplace(
                cand, expected_frame=f, original_rgba=(o1, o2))
            self.assertTrue(r["restored"])
            self.assertEqual(r["restore_source"], "reported_original")
            self._tier1(base, cand, f"ro{f}")

    def test_neighbor_restore_boundary_nonuniform_corner(self):
        """协议边界如实记录：非均匀角 + 邻域剥离 → 恰 2px 差异，Tier-1 不
        成立（宿主上报原始色通道存在的理由；口径非缺陷）。"""
        base = _base_arr()
        cand = _clean_frame_arr(base, 13)
        r = fc.restore_clean_2px_rgba_inplace(cand, expected_frame=13)
        self.assertTrue(r["restored"])
        ref = self.d / "b_ref.png"
        cnd = self.d / "b_cand.png"
        _arr_to_png(base, ref)
        _arr_to_png(cand, cnd)
        a = gg.analyze_frame_diff(ref, cnd)
        self.assertNotEqual(a["tier"], gg.TIER_EXACT)
        self.assertEqual(a["n_diff_pixels"], 2)

    def test_refuse_restore_on_wrong_or_absent_signature(self):
        base = _base_arr(corner=(16, 20, 24))
        cand = _clean_frame_arr(base, 12)
        r = fc.restore_clean_2px_rgba_inplace(cand, expected_frame=13)
        self.assertFalse(r["restored"])                       # 宁拒不剥
        self.assertEqual(r["status"], fc.ST_LAG1)
        self.assertEqual(r["rendered_frame"], 12)             # v2 可靠归因
        r2 = fc.restore_clean_2px_rgba_inplace(base.copy(), expected_frame=13)
        self.assertFalse(r2["restored"])
        self.assertEqual(r2["status"], fc.ST_CHECKSUM)
        with self.assertRaises(ValueError):
            fc.restore_clean_2px_rgba_inplace(_base_arr(w=2, h=1), expected_frame=0)

    def test_refuse_restore_on_inconsistent_pixels(self):
        """双像素观感不相容（P1=帧 13 色值 + P2=帧 14 色值——体外混刷/撕裂
        形态；本通路物理不可达〔同扫描线〕，防御性拒绝）→ ambiguous 宁拒不
        剥 + 零改动。"""
        base = _base_arr(corner=(16, 20, 24))
        cand = _clean_frame_arr(base, 13)
        r2, g2, b2 = fc.signature2_rgb(fc.MODE_CLEAN, 14)
        cand[0, 1] = (r2, g2, b2, 255)
        before = cand[0, 0].copy(), cand[0, 1].copy()
        r = fc.restore_clean_2px_rgba_inplace(cand, expected_frame=13)
        self.assertFalse(r["restored"])
        self.assertEqual(r["status"], fc.ST_AMBIGUOUS)
        self.assertEqual(tuple(int(x) for x in cand[0, 0]), tuple(before[0]))
        self.assertEqual(tuple(int(x) for x in cand[0, 1]), tuple(before[1]))
        v = fc.verify_clean_pixels(tuple(before[0]), tuple(before[1]), 13)
        self.assertEqual(v["status"], fc.ST_AMBIGUOUS)

    def test_skip_gate_and_ambiguous_midpoint(self):
        """expected=None 离线管道：合法双像素唯一归因 → 剥离；不相容/无签名
        → 宁拒不剥（no_signature/ambiguous 如实区分）。"""
        base = _base_arr(corner=(16, 20, 24))
        cand = _clean_frame_arr(base, 258)
        r = fc.restore_clean_2px_rgba_inplace(cand)
        self.assertTrue(r["restored"])
        self.assertEqual(r["rendered_frame"], 258)
        cand2 = _clean_frame_arr(base, 13)
        r2, g2, b2 = fc.signature2_rgb(fc.MODE_CLEAN, 14)
        cand2[0, 1] = (r2, g2, b2, 255)
        self.assertEqual(fc.restore_clean_2px_rgba_inplace(cand2)["status"],
                         fc.ST_AMBIGUOUS)
        self.assertEqual(fc.restore_clean_2px_rgba_inplace(
            base.copy())["status"], fc.ST_CHECKSUM)

    def test_cli_clean_ambiguous_seventh_state(self):
        """CLI 七态：双像素不相容 → ambiguous_signature 单列计数（不进
        wrong_frame_count）、exit 1。目录模式帧号域 f%04d=0..9999。"""
        base = _base_arr(corner=(16, 20, 24))
        d = self.d / "amb"
        d.mkdir()
        a = _clean_frame_arr(base, 0)
        r2, g2, b2 = fc.signature2_rgb(fc.MODE_CLEAN, 50)
        a[0, 1] = (r2, g2, b2, 255)                      # P1=帧0、P2=帧50 → 不相容
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


class TestRedteamRegression(unittest.TestCase):
    """⑧ 红队 #105 r1/r2/r3 全案翻绿（v2 归因正确性回归锁）。

    v1 三轮 FAIL 的等价类（单像素精确支穿透 16,386 对 + ±1 多候选 59,394
    对 + BGRA 孪生 6 对）在 v2 下全部「正确归因拒绝」：stale X 的黑底观感
    （P1 像素逐字节等于 v1 意义上的帧 F 签名——红队复现前提）经双像素验证
    必 rendered==X（never ok(F)），strip 必拒且零改动。"""

    def setUp(self):
        self.base = _base_arr(corner=(16, 20, 24))

    def test_redteam_stale_cases_never_ok(self):
        for stale, f_v1 in _REDTEAM_STALE_CASES:
            o1, o2 = _premul_obs(stale)
            # 前提锚定：P1 观感确为 v1 穿透形态（P1 单像素候选含 F）
            self.assertIn(f_v1, fc.clean_candidates_p1(o1), (stale, f_v1))
            v = fc.verify_clean_pixels(o1, o2, f_v1)
            self.assertNotEqual(v["status"], fc.ST_OK, (stale, f_v1, v))
            self.assertEqual(v["rendered_frame"], stale,
                             f"stale {stale} 必被归因为自身（红队案 {stale}→{f_v1}）")
            cand = self.base.copy()
            cand[0, 0], cand[0, 1] = o1, o2
            before = cand[0, 0].copy(), cand[0, 1].copy()
            r = fc.restore_clean_2px_rgba_inplace(cand, expected_frame=f_v1)
            self.assertFalse(r["restored"], (stale, f_v1))
            self.assertEqual(r["rendered_frame"], stale)
            self.assertEqual(tuple(int(x) for x in cand[0, 0]), tuple(before[0]))

    def test_redteam_full_domain_stale_never_ok(self):
        """全域扫描（穷举背书的测试化）：65536 帧任一 stale X 的黑底观感在
        任一请求帧号下永不 ok；rendered 恒 == X（S1∩S2 恒含 X 且 P2 自定位
        单例）。req 取 X、X+1、X-1 三种典型。"""
        for X in range(65536):
            o1, o2 = _premul_obs(X)
            for req in {X, (X + 1) % 65536, (X - 1) % 65536}:
                v = fc.verify_clean_pixels(o1, o2, req)
                if req == X:
                    self.assertEqual(v["status"], fc.ST_OK, (X, v))
                    self.assertEqual(v["rendered_frame"], X)
                else:
                    self.assertNotEqual(v["status"], fc.ST_OK, (X, req, v))
                    self.assertEqual(v["rendered_frame"], X, (X, req, v))

    def test_genuine_twin_and_collapse_frames_confirmable(self):
        """v1 不可确认帧在 v2 PNG 通路下真迹全部确认（12 帧孪生 + 65535
        塌缩）——协议物理边界闭合。"""
        for f in (23167, 23168, 32602, 32858, 32639, 32896,
                  32640, 32895, 32677, 32933, 42367, 42368, 65535):
            o1, o2 = _premul_obs(f)
            v = fc.verify_clean_pixels(o1, o2, f)
            self.assertEqual(v["status"], fc.ST_OK, (f, v))
            self.assertEqual(v["rendered_frame"], f)
            cand = self.base.copy()
            cand[0, 0], cand[0, 1] = o1, o2
            self.assertTrue(fc.restore_clean_2px_rgba_inplace(
                cand, expected_frame=f)["restored"], f)


class TestJsBridge(unittest.TestCase):
    """⑥ get_canary_js 注入桥静态契约（v2 双像素）。"""

    def test_template_substitution(self):
        for mode in fc.MODES:
            js = fc.get_canary_js(mode)
            self.assertIn(f'"{mode}"', js)
            self.assertIn(f"var KEY1 = {fc.xor_key(mode)};", js)
            self.assertIn(f"var KEY2 = {fc.add_key2(mode)};", js)
            self.assertIn("var MR = 16, MG = 22;", js)
            self.assertNotIn("__VTX_CANARY", js)     # 占位符零残留
            self.assertIn("data-vtx-canary", js)
            self.assertIn("requestAnimationFrame", js)
            self.assertIn("colorP1", js)
            self.assertIn("colorP2", js)

    def test_es5_no_modern_syntax(self):
        js = fc.get_canary_js(fc.MODE_CLEAN)
        for bad in ("=>", "let ", "const ", "`", "async ", "class "):
            self.assertNotIn(bad, js, bad)

    def test_dual_pixel_layout(self):
        """clean 轨双像素同扫描线 (0,0)+(1px,0)；诊断轨 tl+br 双块。"""
        js = fc.get_canary_js(fc.MODE_CLEAN)
        self.assertIn('"p1", "left:0;top:0;", 1', js)
        self.assertIn('"p2", "left:1px;top:0;", 1', js)
        jsd = fc.get_canary_js(fc.MODE_DIAGNOSTIC)
        self.assertIn('"tl", "left:0;top:0;"', jsd)
        self.assertIn('"br", "right:0;bottom:0;"', jsd)

    def test_page_surface_minimal(self):
        js = fc.get_canary_js(fc.MODE_DIAGNOSTIC)
        self.assertIn("pointer-events:none", js)      # 零交互侵入
        self.assertIn("__vtx_render", js)             # 可选协议桥


@unittest.skipUnless(os.environ.get("VTX_TEST_CHROME"),
                     "set VTX_TEST_CHROME for real canary rendering")
class TestChromeCanary(unittest.TestCase):
    """⑦ 真实 Chrome CDP：注入 → 截图 → 双像素解码 100% / 双剥离 Tier-1。

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
        cls.real_page = REPO / "templates" / "skill-demo" / "gfxA.html"
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

    def _verdict(self, arr, f, mode):
        if mode == fc.MODE_DIAGNOSTIC:
            return fc.verify_diagnostic_corners(arr, f)
        return fc.verify_clean_pixels(arr[0][0], arr[0][1], f)

    def _shot_verified(self, tab, url, f, mode):
        """导航 + 截图收敛循环：直到 Canary 绘制探针为真且帧级裁决通过。"""
        arr = last = None
        for _ in range(8):
            tab.navigate(url)
            arr = np.array(Image.open(io.BytesIO(tab.shot())).convert("RGBA"))
            drawn = tab.eval("(window.__vtxCanary&&window.__vtxCanary.drawn)||false")
            last = self._verdict(arr, f, mode)
            if drawn and last["status"] == fc.ST_OK:
                return arr, last
        return arr, last

    def _shot_converge(self, tab, url, f, mode):
        """不重新导航的纯截图收敛（页内置帧后：__vtx_render 桥接场景）。"""
        arr = last = None
        for _ in range(8):
            arr = np.array(Image.open(io.BytesIO(tab.shot())).convert("RGBA"))
            last = self._verdict(arr, f, mode)
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
        """__vtx_render(f2) 桥接后双像素签名色随新帧号重绘（#105b 陈旧色
        回归锁：P1/P2 两套色都须换）。"""
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

    # ---- Clean 轨（v2 双像素） ----------------------------------------------

    def test_d_clean_verify_and_tier1_restore(self):
        """clean 轨：双像素解码 ok + 双剥离 → 与无注入基准 Tier-1 全等（DoD）。
        帧集含 23168（v1 预乘孪生不可确认）与 65535（v1 黑底塌缩不可确认）
        ——v2 全域可确认的真 Chrome 实证。"""
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
            self.assertEqual(r["rendered_frame"], f)
            corner_uniform = bool((base[0, 0] == base[1, 0]).all()
                                  and (base[0, 1] == base[1, 1]).all())
            res = fc.restore_clean_2px_rgba_inplace(
                arr, expected_frame=f,
                original_rgba=None if corner_uniform
                else (tuple(int(v) for v in base[0, 0]),
                      tuple(int(v) for v in base[0, 1])))
            self.assertTrue(res["restored"], f"f={f}: {res}")
            if corner_uniform:
                self.assertEqual(res["restore_source"], "neighbor_row1")
            self._assert_tier1(base, arr, f"chrome_ref_{f}.png",
                               f"chrome_restored_{f}.png")

    def test_e_clean_zero_intrusion_real_page(self):
        """真实页 gfxA.html：clean 双像素解码 ok + 上报原始色对剥离 → Tier-1。"""
        f = 12
        url = f"{self.real_page.resolve().as_uri()}?f={f}"
        self.tab_base.navigate(url)
        base = np.array(
            Image.open(io.BytesIO(self.tab_base.shot())).convert("RGBA"))
        arr, r = self._shot_verified(self.tab_clean, url, f, fc.MODE_CLEAN)
        self.assertEqual(r["status"], fc.ST_OK, r)
        res = fc.restore_clean_2px_rgba_inplace(
            arr, expected_frame=f,
            original_rgba=(tuple(int(v) for v in base[0, 0]),
                           tuple(int(v) for v in base[0, 1])))
        self.assertTrue(res["restored"], res)
        self.assertEqual(res["restore_source"], "reported_original")
        self._assert_tier1(base, arr, "gfxA_ref.png", "gfxA_restored.png")


if __name__ == "__main__":
    unittest.main()
