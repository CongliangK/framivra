#!/usr/bin/env python3
"""frame_canary.py — 双轨帧同步水印协议（GFX 离屏渲染双路线战役 Phase 0，
issue #105；直接支撑 #114 CEF-03a / #116 ELEC-03a 的帧握手生死线 K1）。

解决「Diagnostic Mode 有色块能查错帧，但切换 Clean Golden Mode 关掉色块后
Worker 又失去帧同步锚点」的逻辑悖论（对齐 CEF Issue #4166 的 Marker-Sync
机制）：离屏渲染输出帧与页面帧号 f 的绑定验证走双轨水印——

  模式 A ``diagnostic_8x8``（诊断核验模式）：页面 JS 在目标帧 f 渲染就绪后，
  在左上角 (0..7, 0..7) 绘制 8x8 纯色块（附右下角 (w-8..w-1, h-8..h-1) 第二
  Canary，供水半帧/下旧帧的撕裂对账，DoD「左上+右下双 Canary」口径）：
      R = (f >> 8) & 0xFF；G = f & 0xFF；
      B = ((f >> 8) ^ (f & 0xFF) ^ 0xA5) & 0xFF（8-bit 校验和，防撞页面背景色）；
      A = 255。
  消费端（scripts/gfx_golden.py verify-canary）读取落盘 PNG 左上 8x8 解码
  ``rendered_frame_index`` 并核验校验和；与请求帧号不符即计入
  ``wrong_frame_count``，细分「滞后 1 帧旧帧 / 静态首帧残留 / 校验和撕裂 /
  上下撕裂 torn_frame」。

  模式 B ``clean_1px_restore``（无损生产/Golden 模式）：页面在设置好帧 f 的
  DOM/Canvas 状态并完成双重 requestAnimationFrame 后，仅将单像素 (0, 0) 设为
  帧签名色：
      R = (f >> 8) & 0xFF；G = f & 0xFF；
      B = ((f >> 8) ^ (f & 0xFF) ^ 0x5A) & 0xFF；A = 254（刻意避开常见 A=255/A=0）。
  Native Worker（CEF OnPaint / Electron paint）收到 BGRA_PREMUL 缓冲区后读首
  像素：不匹配请求帧号 f → 陈旧/中间帧，立即丢弃并记
  ``rejected_stale_paint_count`` 继续等下一次 Paint；匹配 → 原地把 (0, 0)
  覆写为相邻像素 (1, 0) 的 BGRA 值（或宿主上报的原始 BGRA），再逆预乘与 PNG
  落盘——还原帧与从未注入 Canary 的原生 Golden 图 Tier-1 全等
  （max_abs_diff == 0，scripts/gfx_golden.analyze_frame_diff 口径）。

预乘塌缩孪生边界（协议物理边界，诚实声明而非兜底承诺）：A=254 单像素签名
  的预乘存储在全域有 **6 对帧号逐字节相同**（23167/23168、42367/42368 两
  相邻对 + 32602/32858、32640/32895、32639/32896、32677/32933 四远对）。当
  期望帧的签名处于该塌缩（孪生在世）且观测签名恰为塌缩签名时，**任何**比对
  ——包括与期望帧签名的精确比对——都无法区分「期望帧真迹」与「孪生陈旧
  帧」。已显式拒绝 ``ambiguous_signature``（宁拒不剥，不猜帧号；等待后续
  Paint 也不会收敛，同帧号签名恒同）的侧面：BGRA 闸门
  ``accept_paint_bgra_inplace``（含 expected=None 的 skip-gate 支，r2）与
  PNG/CDP 通路的 ±1 多候选支（r2）+ **精确解码支（r3 补齐）**——r2 的 PNG
  侧曾漏查精确支：孪生对高侧帧陈旧绘制的黑底混合观感
  round(C·254/255) 在 3 对方向上恰为低侧帧的**精确直向签名**
  （23168→23167、32858→32602、32896→32639，通道全 ≤127 时预乘恒等回落），
  从精确支伪报 ok 并照常剥离（红队 #105 r2 证据）；r3 起凡精确命中帧孪生
  在世即拒（通用规则，不硬编码 3 对；孪生直向签名全域互异，非孪生帧精确
  命中不受影响）。故这 12 个帧号在本签名方案下于**两通路均不可阳性确认**
  （含真迹同拒），是显式化的协议物理边界而非可实现性缺陷（区分需协议
  升级，如双像素签名——代价见 issue #105 r2 评审记录）。剩余物理边界如实
  清单：① PNG 通路精确命中帧 ≠ 请求帧时仍按「精确签名帧号说了算」判
  wrong_frame_*（永不放行剥离，但 rendered 归属不保证可靠——真 23168 的
  黑底观感即精确解到 23167，同 65535→65278 塌缩口径）；② ±1 唯一候选/
  非孪生多候选（如 f=4095）按抗舍入容差放行（原生通路字节可区分），撕裂
  观感由事后 Tier-1 仲裁；③ 消费方选帧可先经 ``premul_twin_frames`` 预避开
  这 12 个帧号（r3 起 gfx_golden 再导出）。

帧号域：16-bit [0, 65535]（R 高 8 位 / G 低 8 位）；0xA5/0x5A 两模式异或键
互不相同，同帧号双模式签名必然不同（模式混注不会互相误判）。

注入桥 ``get_canary_js(mode)``：对现有 HTML 页面零侵入（CDP
``Page.addScriptToEvaluateOnNewDocument`` / CEF ``CefFrame::ExecuteJavaScript``
/ Electron ``preload.js`` 均可直接注入本函数返回的 JS 片段）。JS 侧帧号来源
与页面帧驱动协议同源（``?f=`` 查询参数，skill/references/gfx.md）；绘制时机
= DOMContentLoaded 后双重 rAF（模式 B 协议要求；headless 下 rAF 由截图的
BeginFrame 驱动，消费 harness 以 throwaway 截图兜底触发）；页面若实现可选
``window.__vtx_render(f)`` 协议则透明包装（同参同返、异常照抛）在每次页内置
帧后同步重绘 Canary。协议铁律不破：Canary 状态 = f 的纯函数，属工具注入的
诊断叠加层，永不进入生产 golden 缓存（只在验证运行期存在）。

像素物理口径（重要，实现者备忘）：
  - 注入桥 clean 模式以 ``rgba(...,254/255)`` 绘制签名像素：透明页（gfx 叠层
    页常态）+ 原生 OSR ``BGRA_PREMUL`` 缓冲区里该像素携带协议签名 A=254，
    RGB=round(C·254/255)（预乘存储）；``signature_bgra_premul`` /
    ``match_bgra_premul``（±1/通道容忍）按此比对，``decode_bgra_premul``
    用**精确反像区间**（``_premul_cands``）反解——朴素 floor/ceil 直除法会让
    约半数帧号的签名校验和碰撞歧义（65536 帧实测 32773 帧，#105b 复审拦截）。
    精确反像消除数值假阳性后，残余歧义恰为上述 6 对孪生（12 帧）——真塌缩，
    非算法可修，消费面按 ``ambiguous_signature`` 显式拒绝。
  - DOM/CSS 叠层在**不透明**合成输出（CDP 截图）中 α 恒拍平为 255，且
    rgba(254/255) 签名与底层页面像素按 α 混合 → RGB 偏差 |Δ|≤1/通道。故 PNG
    通路校验（``verify_clean_pixel``）不查 α，按强度递降判定：精确解码（直向
    签名全域单射；**命中帧孪生在世且 == 请求帧 → ``ambiguous_signature``
    显式拒绝**——孪生高侧帧的黑底混合观感恰为低侧精确直向签名，r3 前此处
    曾穿透，红队 #105 r2）→ ±1 立方候选唯一解码（抗舍入策略）→ 多候选且
    含请求帧的**预乘孪生** = ``ambiguous_signature`` 显式拒绝（协议固有塌缩
    族，宁拒不猜）；多候选但仅为 ±1 舍入邻居（原生通路字节可区分）按对靶
    容差放行；不含请求帧的多候选同样 ``ambiguous_signature``（帧号不可
    归属）。诊断轨 ``rgb()`` 不透明纯色块不受此影响（8x8 块内 RGB 精确）。

本模块纯标准库（验证/剥离只碰 ≤2 像素邻域 + 8x8 块，无需 numpy/PIL；PNG
解码由调用方负责——scripts/gfx_golden._decode_rgba、测试 PIL 均可）。
"""
from __future__ import annotations

MODE_DIAGNOSTIC = "diagnostic_8x8"
MODE_CLEAN = "clean_1px_restore"
MODES = (MODE_DIAGNOSTIC, MODE_CLEAN)

DIAG_XOR_KEY = 0xA5   # 模式 A 校验和异或键（issue #105 冻结值）
CLEAN_XOR_KEY = 0x5A  # 模式 B 校验和异或键（issue #105 冻结值）
DIAG_ALPHA = 255      # 模式 A 签名 α（诊断色块，不透明）
CLEAN_ALPHA = 254     # 模式 B 签名 α（刻意避开常见 A=255 与 A=0）
DIAG_SIZE = 8         # 模式 A 色块边长（像素）：左上 (0..7, 0..7) + 右下对角块

FRAME_MAX = 0xFFFF    # 帧号域上限（16-bit）

# verify-canary 帧裁决状态词汇（verify_* 返回 status；wrong_frame_count 聚合口径）
ST_OK = "ok"                          # TL（及 BR）Canary 解码 == 请求帧号，校验和通过
ST_LAG1 = "wrong_frame_lag1"          # 滞后 1 帧旧帧（rendered == requested - 1）
ST_STATIC_FIRST = "wrong_frame_static_first"   # 静态首帧残留（rendered == 0 且请求非 0）
ST_OTHER = "wrong_frame_other"        # 其余有效解码但帧号不符
ST_TORN = "torn_frame"                # 上下撕裂：TL 与 BR 双 Canary 帧号相异，或 TL 块内
                                      # 呈两条均合法签名的水平色带（半帧新/半帧旧）
ST_CHECKSUM = "checksum_mismatch"     # 校验和撕裂：块结构在但 B 通道对不上
ST_NO_CANARY = "no_canary"            # 左上区域无 Canary 结构（未注入/被遮挡/尺寸不符）
ST_AMBIGUOUS = "ambiguous_signature"  # 候选解码不唯一：观测与多个合法签名相容（BGRA
                                      # 通路=预乘塌缩孪生在世；PNG 通路=±1 容差球交集
                                      # 内的撕裂/混合观测）。协议无法归属帧号 → 显式
                                      # 拒绝而非猜测（宁拒不剥，issue #105 r2）

_WRONG_STATES = (ST_LAG1, ST_STATIC_FIRST, ST_OTHER)


def _check_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"未知 canary 模式: {mode!r}（合法: {list(MODES)}）")


def _check_frame(f: int) -> int:
    f = int(f)
    if not 0 <= f <= FRAME_MAX:
        raise ValueError(f"帧号超出 16-bit 域 [0, {FRAME_MAX}]: {f}")
    return f


def xor_key(mode: str) -> int:
    """模式的校验和异或键（0xA5 / 0x5A）。"""
    _check_mode(mode)
    return DIAG_XOR_KEY if mode == MODE_DIAGNOSTIC else CLEAN_XOR_KEY


def signature_rgb(mode: str, f: int) -> tuple[int, int, int]:
    """帧签名 RGB（协议冻结公式）。f 为帧号；模式决定异或键。"""
    _check_mode(mode)
    f = _check_frame(f)
    hi, lo = (f >> 8) & 0xFF, f & 0xFF
    return hi, lo, (hi ^ lo ^ xor_key(mode)) & 0xFF


def signature_rgba(mode: str, f: int) -> tuple[int, int, int, int]:
    """帧签名 RGBA（A ∈ {255 诊断, 254 clean}；PNG 通路 CDP 截图 A 实现为 255，
    见模块头「像素物理口径」——解码侧按 RGB+校验和判定，A 只在 BGRA 通路严格）。"""
    r, g, b = signature_rgb(mode, f)
    return r, g, b, DIAG_ALPHA if mode == MODE_DIAGNOSTIC else CLEAN_ALPHA


def signature_bgra(mode: str, f: int) -> tuple[int, int, int, int]:
    """帧签名 BGRA（字节序 Little-Endian 内存布局下的四元组 (B, G, R, A)；
    Native Worker 读 BGRA 缓冲区首像素与此比对。straight（非预乘）口径。"""
    r, g, b, a = signature_rgba(mode, f)
    return b, g, r, a


def decode_pixel(mode: str, r: int, g: int, b: int) -> tuple[int, bool]:
    """单像素解码：R/G 直读帧号（高/低 8 位），B 比对校验和。

    返回 (frame, checksum_ok)。checksum_ok=False 时 frame 仍给出 R/G 直读值
    （调用方据此分类 checksum_mismatch / no_canary——单像素无法区分「无
    Canary」与「校验和撕裂」，8x8 模式靠块结构区分，见 analyze_diag_block）。
    """
    _check_mode(mode)
    frame = ((int(r) & 0xFF) << 8) | (int(g) & 0xFF)
    ok = (int(b) & 0xFF) == ((int(r) ^ int(g) ^ xor_key(mode)) & 0xFF)
    return frame, ok


def classify_frame(rendered: int, requested: int) -> str:
    """解码成功后的帧号分类（requested = 请求帧号，如落盘文件名 f0013 → 13）：
    ok / wrong_frame_lag1（滞后 1 帧旧帧）/ wrong_frame_static_first（静态首帧
    残留）/ wrong_frame_other。"""
    if rendered == requested:
        return ST_OK
    if rendered == requested - 1:
        return ST_LAG1
    if rendered == 0 and requested != 0:
        return ST_STATIC_FIRST
    return ST_OTHER


# ------------------------------------------------ 右下角/块结构分析（模式 A）

def _block_rows(block) -> list[list[tuple]]:
    """8x8 块 → 8 行 × 8 像素四元组序列（接受 numpy (8,8,4) 数组或嵌套序列）。"""
    rows = []
    for y in range(DIAG_SIZE):
        row = [tuple(int(v) & 0xFF for v in block[y][x]) for x in range(DIAG_SIZE)]
        rows.append(row)
    return rows


def analyze_diag_block(block) -> dict:
    """左上/右下 8x8 块结构分析（模式 A 解码核心，纯函数）。

    block：8x8 RGBA 像素（numpy 数组或嵌套序列，忽略 α——DOM 叠层在 PNG 通路
    α 恒 255，见模块头像素物理口径）。返回：
      kind == "uniform"：整块同色且校验和通过 → {"frame": f}
      kind == "banded" ：块内两条**水平**色带（撕裂缓冲半新半旧），两色各自
                        校验和通过且各占 ≥1 整行 → {"frames": [f_new, f_old],
                        "bands": [行数, 行数]}（帧号按出现行序，上带在前）
      kind == "bad_checksum"：整块同色但校验和对不上（校验和撕裂；附直读帧号）
      kind == "foreign"：无 Canary 结构（页面原内容：杂色/非整行色带/多于两色）
    """
    rows = _block_rows(block)
    row_colors = [frozenset(row) for row in rows]
    if any(len(c) != 1 for c in row_colors):
        return {"kind": "foreign"}          # 行内杂色：绝非 8x8 纯色块
    colors = [next(iter(c)) for c in row_colors]
    uniq = []
    for c in colors:
        if not uniq or uniq[-1] != c:
            uniq.append(c)
    if len(uniq) == 1:
        r, g, b = colors[0][:3]
        f, ok = decode_pixel(MODE_DIAGNOSTIC, r, g, b)
        return ({"kind": "uniform", "frame": f} if ok
                else {"kind": "bad_checksum", "frame": f})
    if len(uniq) == 2:
        # 恰两色且各占连续整行（水平撕裂带）；两色须均为合法签名
        frames = []
        for c in uniq:
            f, ok = decode_pixel(MODE_DIAGNOSTIC, c[0], c[1], c[2])
            if not ok:
                return {"kind": "foreign"}
            frames.append(f)
        bands = [colors.count(uniq[0]), colors.count(uniq[1])]
        return {"kind": "banded", "frames": frames, "bands": bands}
    return {"kind": "foreign"}


def verify_diagnostic_corners(rgba_arr, requested_frame: int, h: int = None,
                              w: int = None) -> dict:
    """整帧双角校验（模式 A；verify-canary 的帧级裁决，纯函数）。

    rgba_arr：可按下标 ``arr[y][x]`` / ``arr[y, x]`` 取 RGBA 四元组的像素阵
    （numpy (h,w,4) 数组或等价嵌套序列；PIL 图像请先转 numpy）；h/w 缺省时
    按 ``rgba_arr.shape`` 推断。

    左上 (0..7, 0..7) 为主 Canary（协议要求必在）；右下
    (h-8..h-1, w-8..w-1) 为撕裂对账副 Canary——foreign（页面原内容）时豁免，
    合法解码时参与对账。返回 {"status", "rendered_frame"?, "detail"}：
      ok                   双角一致且 == requested_frame
      wrong_frame_*        classify_frame 分类（滞后 1 帧/静态首帧/其他）
      torn_frame           TL 与 BR 帧号相异，或 TL 块内双色带（半帧撕裂）
      checksum_mismatch    TL 整块同色但校验和撕裂
      no_canary            TL 无 Canary 结构
    """
    if h is None or w is None:
        try:
            h, w = rgba_arr.shape[:2]
        except AttributeError:
            h, w = rgba_arr.height, rgba_arr.width
    req = _check_frame(requested_frame)
    if h < DIAG_SIZE or w < DIAG_SIZE:
        return {"status": ST_NO_CANARY, "detail": f"画幅 {w}x{h} 不足以承载 8x8 Canary"}

    tl = analyze_diag_block(
        [[rgba_arr[y][x] for x in range(DIAG_SIZE)] for y in range(DIAG_SIZE)])
    if tl["kind"] == "foreign":
        return {"status": ST_NO_CANARY, "detail": "左上 8x8 无 Canary 结构"}
    if tl["kind"] == "bad_checksum":
        return {"status": ST_CHECKSUM, "rendered_frame": tl["frame"],
                "detail": "左上块同色但校验和撕裂（B 通道不符）"}
    if tl["kind"] == "banded":
        return {"status": ST_TORN, "rendered_frames": tl["frames"],
                "detail": f"左上块内水平双色带 {tl['bands']} 行——半帧撕裂"}

    frame = tl["frame"]
    br_block = [[rgba_arr[y][x] for x in range(w - DIAG_SIZE, w)]
                for y in range(h - DIAG_SIZE, h)]
    br = analyze_diag_block(br_block)
    if br["kind"] == "banded":
        return {"status": ST_TORN, "rendered_frame": frame,
                "rendered_frames_br": br["frames"],
                "detail": f"右下块内水平双色带 {br['bands']} 行——半帧撕裂"}
    if br["kind"] == "uniform" and br["frame"] != frame:
        return {"status": ST_TORN, "rendered_frame": frame,
                "rendered_frame_br": br["frame"],
                "detail": f"左上 {frame} vs 右下 {br['frame']}——撕裂/旧帧残留"}
    status = classify_frame(frame, req)
    return {"status": status, "rendered_frame": frame,
            "detail": "" if status == ST_OK else
            f"rendered={frame} requested={req}"}


def _clean_blend_candidates(r: int, g: int, b: int) -> set:
    """clean 签名的 PNG 通路候选解码（verify/restore 共用）：

    精确解码（RGB 直读 + 校验和）优先；不中再取 ±1 立方（27 组合）过校验和
    （rgba(254/255) 签名在不透明合成下与底层像素 ±1 混合，抗舍入策略）。
    返回候选帧号集合（可能 0/1/多个）。
    """
    frame, ok = decode_pixel(MODE_CLEAN, r, g, b)
    if ok:
        return {frame}
    cands = set()
    for rr in (r - 1, r, r + 1):
        for gg in (g - 1, g, g + 1):
            for bb in (b - 1, b, b + 1):
                f2, ok2 = decode_pixel(MODE_CLEAN, rr & 0xFF, gg & 0xFF, bb & 0xFF)
                if ok2:
                    cands.add(f2)
    return cands


def verify_clean_pixel(px, requested_frame: int) -> dict:
    """单像素校验（模式 B；px = (0,0) 像素 RGBA 四元组）。

    PNG 通路 A 实现为 255（CDP 截图不透明合成），故 α 不参与判据；注入桥以
    rgba(...,254/255) 绘制，不透明合成下 RGB 与底层页面像素按 α=254/255 混合
    （|偏差| ≤ 1/通道，抗舍入策略）。判定按强度递降（issue #105 r2；r3 补 ①
    的孪生拒绝）：
      ① 精确解码：RGB 直读帧号 + 校验和全等（两探针解算/同色底时成立，最强；
        直向签名全域单射，非孪生帧无歧义）→ 按该帧号分类。**但命中帧孪生
        在世（premul_twin_frames 非空）且 == 请求帧 → ``ambiguous_signature``
        显式拒绝**：黑底混合下孪生对高侧帧（如 23168）的陈旧绘制观感
        round(C·254/255) 恰为低侧帧（23167）的精确直向签名，真迹与陈旧观感
        不可分（红队 #105 r2 穿透主因；与 BGRA 闸门同状态词，宁拒不猜）；
        命中帧 ≠ 请求帧时按「精确签名帧号说了算」走 wrong_frame_* 拒绝（宁拒
        不剥，rendered 归属不保证可靠——同 65535→65278 塌缩口径）；
      ② ±1 立方候选唯一解码（_clean_blend_candidates）→ 按该帧号分类；
      ③ 多候选且含请求帧：**候选集含请求帧的预乘孪生**（premul_twin_frames，
        即原生通路字节也不可区分的协议固有塌缩族）→ ``ambiguous_signature``
        显式拒绝，不猜帧号；否则（候选仅为 ±1 舍入邻居——原生 BGRA 通路字
        节可区分，本通路容差系渲染舍中考量）→ ok（blend±1 对靶匹配）；
      ④ 多候选且不含请求帧 → ``ambiguous_signature``（帧号不可归属，detail
        如实标注）；
      ⑤ 全不中 → checksum_mismatch。
    返回词汇同 verify_diagnostic_corners 加 ambiguous_signature（单像素无块
    结构，不存在 no_canary 的可靠区分——模式 B 由宿主保证 (0,0) 必为签名位）。
    """
    req = _check_frame(requested_frame)
    r, g, b = int(px[0]) & 0xFF, int(px[1]) & 0xFF, int(px[2]) & 0xFF
    frame, exact = decode_pixel(MODE_CLEAN, r, g, b)
    if exact:
        twins = premul_twin_frames(MODE_CLEAN, frame)
        if frame == req and twins:
            return {"status": ST_AMBIGUOUS, "rendered_frame": None,
                    "detail": f"精确命中请求帧 {req} 直向签名但预乘孪生在世"
                              f"（twin={'|'.join(map(str, twins))}）——孪生陈旧"
                              "帧的黑底混合观感与真迹不可分，宁拒不猜"}
        return {"status": classify_frame(frame, req), "rendered_frame": frame,
                "detail": "" if frame == req else
                f"rendered={frame} requested={req}"}
    cands = _clean_blend_candidates(r, g, b)
    if len(cands) == 1:
        frame = next(iter(cands))
        return {"status": classify_frame(frame, req), "rendered_frame": frame,
                "detail": "" if frame == req else f"rendered={frame} requested={req}"}
    if cands:
        if req in cands and not (cands & set(premul_twin_frames(MODE_CLEAN, req))):
            return {"status": ST_OK, "rendered_frame": req,
                    "detail": f"blend±1 对靶匹配（{len(cands)} 候选相容，"
                              "均非预乘孪生）"}
        return {"status": ST_AMBIGUOUS, "rendered_frame": None,
                "detail": f"±1 候选不唯一（{len(cands)} 个合法签名相容，"
                          f"含请求帧={req in cands}"
                          + ("，且含预乘孪生——协议固有塌缩" if req in cands else "")
                          + "）——帧号不可归属，宁拒不猜"}
    return {"status": ST_CHECKSUM, "rendered_frame": None,
            "detail": "(0,0) 校验和不符（无签名或撕裂）"}


# ------------------------------------------------ Native BGRA 通路（模式 B）

def signature_bgra_premul(mode: str, f: int) -> tuple[int, int, int, int]:
    """BGRA_PREMUL 存储口径的期望签名：RGB = round(C·A/255)，A = 254/255。

    预乘是「合成器数学事实」（Skia/CEF/Electron 缓冲区皆预乘），round 为
    半向上舍入（与 gfx_golden._rdiv255 同式）。宿主比对请配 ±1 容忍
    （match_bgra_premul）——不同引擎的预乘舍入实现存在 ±1 差异可能。
    """
    b, g, r, a = signature_bgra(mode, f)
    k = a / 255.0
    return (int(b * k + 0.5), int(g * k + 0.5), int(r * k + 0.5), a)


def match_bgra_premul(px_bgra, expected_frame: int, mode: str = MODE_CLEAN,
                      tol: int = 1) -> bool:
    """预乘缓冲区首像素与期望帧签名的比对（±1/通道容忍）。

    px_bgra = (B, G, R, A) 四元组；tol=0 退化为精确匹配（同引擎同实现时）。
    """
    exp = signature_bgra_premul(mode, expected_frame)
    if int(px_bgra[3]) != exp[3]:
        return False
    return all(abs(int(px_bgra[i]) - exp[i]) <= tol for i in range(3))


def _premul_cands(v: int) -> list[int]:
    """预乘存储值 v（round(C·254/255)，半向上）的**精确反像**：
    {C ∈ [0,255] : (508·C+255)//510 == v}，整数界闭区间（v 是 8-bit 直存值）。

    精确性是硬要求：朴素 floor/ceil(v·255/254) 会把「round 后不落 v」的相邻
    C 也算作候选（如 v=0 时 C=1、v=90 时 C=91），三通道组合后约半数帧号的
    签名出现校验和碰撞假阳性 → 反解不唯一（65536 帧实测 32773 帧歧义，
    本仓 #105b 复审拦截）。反像区间数学：C·254/255+0.5 = (508C+255)/510，
    落入 [v, v+1) ⟺ 510v−255 ≤ 508C < 510v+255。
    """
    lo = -((255 - 510 * v) // 508)          # ceil((510v-255)/508)
    hi = -((-510 * v - 255) // 508) - 1     # ceil((510v+255)/508) - 1
    return [c for c in range(max(0, lo), min(255, hi) + 1)]


def _premul_decode_matches(px_bgra) -> set:
    """预乘首像素反解的完整候选帧集合（decode_bgra_premul 底层；纯函数）。

    三通道直存值各取精确反像集（每通道 1-2 个 C）做笛卡尔组合，校验和过滤。
    返回候选帧号集合：空 = 无合法签名结构；1 个 = 可唯一归属；≥2 个 = 真歧义
    （预乘塌缩孪生在世：观测与多个帧号的签名逐字节相容）。
    """
    matches = set()
    for r in _premul_cands(int(px_bgra[2])):
        for g in _premul_cands(int(px_bgra[1])):
            for b in _premul_cands(int(px_bgra[0])):
                f, ok = decode_pixel(MODE_CLEAN, r, g, b)
                if ok:
                    matches.add(f)
    return matches


def premul_twin_frames(mode: str, f: int) -> tuple:
    """帧 f 的预乘孪生帧号（与 f 的 signature_bgra_premul **逐字节相同**的
    其他帧，升序元组）。

    clean 模式全域恰 6 对/12 帧（模块头「预乘塌缩孪生边界」：23167/23168、
    42367/42368 相邻对 + 32602/32858、32640/32895、32639/32896、32677/32933
    远对）；diagnostic 模式 A=255 预乘为恒等变换，无孪生。消费面在期望帧
    孪生在世时必须按 ``ambiguous_signature`` 显式拒绝——孪生签名不可区分是
    单像素签名方案的协议物理边界。
    """
    _check_mode(mode)
    f = _check_frame(f)
    if mode == MODE_DIAGNOSTIC:
        return ()     # A=255：预乘恒等，签名无塌缩
    return tuple(sorted(_premul_decode_matches(signature_bgra_premul(mode, f))
                        - {f}))


def decode_bgra_premul(px_bgra) -> tuple[int, bool]:
    """预乘首像素反解帧号（唯一候选时 (frame, True)，否则 (0, False)）。

    ok=False 有两种诚实战况，调用方须区分处理而非「换个比对兜底」：
      零候选 = 无合法签名结构；
      多候选 = 预乘塌缩孪生在世（全域恰 6 对/12 帧，见模块头）——观测签名与
      多个帧号的签名**逐字节相同**，任何比对（包括与期望帧签名的精确比对）
      都无法区分期望帧真迹与孪生陈旧帧。accept_paint_bgra_inplace 闸门对此
      显式拒绝（``ambiguous_signature``，宁拒不剥），不做帧号猜测——曾有的
      「以期望帧精确比对为准」说法对孪生帧是伪兜底（红队 #105 r1 复现：
      陈旧帧 23167 签名冒充 expected=23168 被放行），r2 起废除。
    """
    matches = _premul_decode_matches(px_bgra)
    if len(matches) != 1:
        return 0, False
    return matches.pop(), True


def accept_paint_bgra_inplace(buf: bytearray, width: int, expected_frame: int,
                              original_bgra=None) -> dict:
    """Native Worker 的 OnPaint/paint 帧闸门（模式 B 核心消费面，issue 原文语义）。

    buf：整帧 BGRA_PREMUL 缓冲区（bytearray，行主序，首像素 (0,0) 在
    buf[0:4]；buf[4:8] 为 (1,0)）。三态语义：
      首像素不匹配请求帧号 f → 判陈旧/中间帧（如 CEF #4166 软件路径同步重放
        帧），**不改动 buf**，返回 {"accepted": False, "reason":
        "stale_paint:rendered=N", "rejected_stale_paint": True}——调用方丢弃
        本缓冲区继续等下一次 Paint，并自行累加 rejected_stale_paint_count
        （rendered 为反解唯一帧号，零候选/歧义时 None）；
      首像素 == 期望帧的预乘签名，但期望帧**孪生在世**（premul_twin_frames
        非空——观测与孪生帧签名逐字节相同）→ **同样不改动 buf**，返回
        {"accepted": False, "reason": "ambiguous_signature:twin=…",
        "status": "ambiguous_signature", "rejected_stale_paint": True,
        "twin_frames": […]}。协议物理边界：该观测永远无法区分期望帧真迹与
        孪生陈旧帧，等待后续 Paint 也不收敛（同帧号签名恒同），调用方须按
        歧义处置（换帧号重渲/邻帧佐证/上报），**不得猜测帧号**（红队 #105
        r1：陈旧 23167 签名冒充 expected=23168 曾被「精确比对」放行）；
      匹配（精确逐字节相等，含 A=254）→ 原地把 (0,0) 覆写为 (1,0) 的 BGRA
        值（original_bgra 四元组给出时优先用宿主上报原始值），返回
        accepted=True + rendered_frame，调用方随后执行逆预乘与 PNG 编码落盘。
    匹配主判据 = 精确逐字节相等 ``px == signature_bgra_premul(clean, f)``：
    陈旧帧鉴别是本闸门的生死线（CEF #4166 场景＝恰好滞后 1 帧的旧帧，±1
    球面容忍会把相邻帧签名 G/B ±1 互相放行）。诚实说明（r2）：``_premul_cands``
    是「round(C·254/255)==v」的精确等价类，**不存在**「字节不同却反解到期望
    帧」的等价存储——他引擎预乘舍入约定不同时字节必不匹配，宿主须先校准
    （match_bgra_premul(±1) 保留为显式 opt-in API，供宿主引擎校准后使用，
    本闸门不默认采用）。
    expected_frame 为 None 时跳过帧号闸门但仍要求 (0,0) 是可唯一反解的合法
    签名（无签名结构或孪生歧义均宁拒不剥，后者上报 ambiguous_signature）。
    """
    px = tuple(buf[0:4])
    matches = _premul_decode_matches(px)
    ok = len(matches) == 1
    rendered = next(iter(matches)) if ok else None
    if expected_frame is not None:
        exp = _check_frame(expected_frame)
        exp_px = signature_bgra_premul(MODE_CLEAN, exp)
        if px != exp_px:
            return {"accepted": False, "rejected_stale_paint": True,
                    "reason": f"stale_paint:rendered={rendered}",
                    "rendered_frame": rendered, "restored": False}
        twins = premul_twin_frames(MODE_CLEAN, exp)
        if twins:
            return {"accepted": False, "rejected_stale_paint": True,
                    "reason": "ambiguous_signature:twin=" + "|".join(map(str, twins)),
                    "status": ST_AMBIGUOUS, "rendered_frame": None,
                    "restored": False, "twin_frames": list(twins)}
        rendered = exp
    elif not ok:
        if matches:
            twins = sorted(matches)
            return {"accepted": False, "rejected_stale_paint": False,
                    "reason": "ambiguous_signature:twin=" + "|".join(map(str, twins)),
                    "status": ST_AMBIGUOUS, "rendered_frame": None,
                    "restored": False, "twin_frames": twins}
        return {"accepted": False, "rejected_stale_paint": False,
                "reason": "no_signature", "rendered_frame": None,
                "restored": False}
    src = tuple(original_bgra) if original_bgra is not None else tuple(buf[4:8])
    buf[0:4] = bytes(src)
    return {"accepted": True, "rejected_stale_paint": False, "reason": "ok",
            "rendered_frame": rendered,
            "restored": True, "restore_source":
            "reported_original" if original_bgra is not None else "neighbor_1_0"}


def _clean_matches_expected(px, exp: int) -> bool:
    """剥离侧对靶判定（「宁拒不剥」双战线：陈旧帧鉴别保守 + 孪生歧义拒绝）：

      ① obs 是某帧的**精确合法签名** → 仅 exp == 该帧号**且该帧孪生不在世**
         （premul_twin_frames(exp) 为空）才放行：孪生在世时观测也可能来自其
         孪生帧的黑底混合（round(C·254/255) 在 3 对方向上恰为 exp 的精确直向
         签名，如 stale 23168 → expected 23167——红队 #105 r2 穿透，r3 拒），
         真迹与陈旧观感不可分 → 拒绝（verify 侧上报 ambiguous_signature，
         宁拒不剥；陈旧帧鉴别是生死线——CEF #4166 场景＝旧帧签名原样在缓冲
         区里，±1 放行会静默把旧帧当新帧剥离落盘，Tier-1 之前无人兜底）；
         非孪生帧直向签名全域单射，精确命中即无歧义；
      ② obs 非精确签名（混合/撕裂观感）→ exp ∈ ±1 立方候选才可能放行，且
         候选集**不得含 exp 的预乘孪生**（premul_twin_frames——原生 BGRA
         通路字节也不可区分的协议固有塌缩族，与闸门 ambiguous_signature 同
         规则的 PNG 通路投影，宁拒不剥）；候选仅为 ±1 舍入邻居（原生通路
         字节可区分）时按抗舍入容差放行，事后 Tier-1 仲裁。

    数学注：|px.rgb − sig(exp)|∞ ≤ 1 ⟺ sig(exp) ∈ px 的 ±1 立方候选（签名
    校验和恒真），故旧「对靶 ±1 匹配」路径被②完全包含——多候选时只在无孪生
    在世的容差语义下放行（issue #105 r2）。与审计侧 verify_clean_pixel 的
    输出语义互补：verify 判状态分类（孪生在世 → ambiguous_signature），
    本函数只答放行与否。
    """
    r, g, b = int(px[0]) & 0xFF, int(px[1]) & 0xFF, int(px[2]) & 0xFF
    frame, ok = decode_pixel(MODE_CLEAN, r, g, b)
    if ok:
        # r3：精确直向命中且该帧孪生在世 → 同闸门 ambiguous 规则的 PNG 投影
        #（孪生高侧帧的黑底混合观感恰为低侧精确签名），宁拒不剥。
        return frame == exp and not premul_twin_frames(MODE_CLEAN, exp)
    cands = _clean_blend_candidates(r, g, b)
    if exp not in cands:
        return False
    return not (cands & set(premul_twin_frames(MODE_CLEAN, exp)))


def restore_clean_1px_rgba_inplace(arr, expected_frame=None,
                                   original_rgba=None) -> dict:
    """PNG/Raster 通路的原地剥离（模式 B；arr 为可写 (h,w,4) uint8 数组，如
    gfx_golden._decode_rgba 的返回或 PIL 转 numpy 的帧）。

    先校验 (0,0)（expected_frame 给出且连宽松对靶都不中、或观测处于多候选
    歧义（孪生撕裂中点/孪生精确直向命中——后者观测也可能来自孪生帧的黑底
    混合，r3）→ 拒绝剥离，返回 {"restored": False, ...}，宁拒不
    错，status=ambiguous_signature 时帧号不可归属；expected_frame=None 时
    要求候选解码唯一，且唯一候选系孪生帧的精确直向签名时同样宁拒不剥——
    与 accept_paint_bgra_inplace 的 skip-gate 同规则，帧号不可归属不猜测）；
    随后 (0,0) ← original_rgba（宿主上报原始色，优先）或相邻像素 (1,0)。
    返回 restored/rendered_frame/restore_source/status。
    剥离后帧与无 Canary 原生 Golden 的 Tier-1 全等由调用方用
    gfx_golden.analyze_frame_diff 验证（DoD 口径）。
    """
    h, w = arr.shape[0], arr.shape[1]
    if h < 1 or w < 2:
        raise ValueError(f"画幅过小 {w}x{h}：clean 1px 剥离需要 (1,0) 邻像素")
    px = tuple(int(v) for v in arr[0][0])
    cands = _clean_blend_candidates(px[0], px[1], px[2])
    if expected_frame is not None:
        exp = _check_frame(expected_frame)
        if not _clean_matches_expected(px, exp):
            r = verify_clean_pixel(px, exp)
            return {"restored": False, "status": r["status"],
                    "rendered_frame": r.get("rendered_frame")}
        frame = exp
    else:
        if len(cands) != 1:
            return {"restored": False, "status": ST_CHECKSUM,
                    "rendered_frame": None}
        frame = next(iter(cands))
        # r3：唯一候选系精确直向签名且该帧孪生在世 → 帧号不可归属（观测
        # 也可能来自孪生帧的黑底混合），skip 管道同规则宁拒不剥。
        if (decode_pixel(MODE_CLEAN, px[0] & 0xFF, px[1] & 0xFF,
                         px[2] & 0xFF)[1]
                and premul_twin_frames(MODE_CLEAN, frame)):
            return {"restored": False, "status": ST_AMBIGUOUS,
                    "rendered_frame": None}
    src = tuple(int(v) for v in original_rgba) if original_rgba is not None \
        else tuple(int(v) for v in arr[0][1])
    arr[0][0] = src
    return {"restored": True, "status": ST_OK, "rendered_frame": frame,
            "restore_source": "reported_original" if original_rgba is not None
            else "neighbor_1_0"}


# ------------------------------------------------ JS 注入桥（零侵入叠加层）

_JS_TEMPLATE = r"""(function () {
  "use strict";
  // vtx/frame_canary.py 注入桥（issue #105）：?f= 帧驱动页面的零侵入帧同步水印。
  // 帧号与页面协议同源（?f=）；Canary 状态 = f 的纯函数（诊断叠加层，非页面动画）。
  var MODE = "__VTX_CANARY_MODE__";
  var KEY = __VTX_CANARY_KEY__;
  var SIZE = 8;                       // diagnostic_8x8 块边长；clean 模式用 1px
  var raw = new URLSearchParams(location.search).get("f");
  var f = parseInt(raw || "0", 10);
  if (!isFinite(f) || f < 0) { f = 0; }
  f &= 0xFFFF;                        // 16-bit 帧号域（与协议一致）
  // 签名色必须按「本次绘制的帧号」现算（不能在注入时对 ?f= 闭包一次）：
  // __vtx_render(f2) 桥接重绘时帧号已换，闭包旧色 = 陈旧签名（#105b 复审修复）。
  // clean 模式用 rgba(...,254/255)：透明页（gfx 叠层页的常态）+ 原生 OSR
  // BGRA_PREMUL 缓冲区里该像素才带 A=254 协议签名；不透明合成通路（CDP 截图）
  // 下 α 被拍平为 255、RGB 与底层像素 ±1 混合——消费端 verify/decode 的 ±1
  // 抗舍入策略即为此设计（模块头「像素物理口径」）。
  function cssColorFor(frame) {
    var hi = (frame >> 8) & 255, lo = frame & 255;
    var rgb = hi + "," + lo + "," + ((hi ^ lo ^ KEY) & 255);
    return (MODE === "clean_1px_restore")
      ? "rgba(" + rgb + ",0.996078431372549)"   // 254/255，A=254 签名
      : "rgb(" + rgb + ")";                     // 诊断轨不透明 A=255
  }
  var BASE = "position:fixed;margin:0;padding:0;border:0;outline:0;" +
    "z-index:2147483647;pointer-events:none;opacity:1 !important;" +
    "visibility:visible !important;display:block !important;" +
    "transform:none !important;filter:none !important;" +
    "mix-blend-mode:normal !important;";
  function make(id, pos, size, color) {
    var d = document.createElement("div");
    d.setAttribute("data-vtx-canary", id);
    d.style.cssText = BASE + pos + "width:" + size + "px;height:" + size +
      "px;background:" + color + ";";
    return d;
  }
  function clear() {
    try {
      var ns = document.querySelectorAll("[data-vtx-canary]");
      for (var i = 0; i < ns.length; i++) {
        if (ns[i].parentNode) { ns[i].parentNode.removeChild(ns[i]); }
      }
    } catch (e) {}
  }
  function draw(frame) {
    var root = document.documentElement;
    if (!root || !root.appendChild) { return false; }
    clear();
    // 挂 documentElement（html）而非 body：body 级 opacity/mask（如 S4 拍平
    // 形态页）不作用于 fixed 叠层；协议假定 html 根未被整树滤镜/变换劫持。
    var color = cssColorFor(frame);
    if (MODE === "diagnostic_8x8") {
      root.appendChild(make("tl", "left:0;top:0;", SIZE, color));
      root.appendChild(make("br", "right:0;bottom:0;", SIZE, color));
    } else {
      root.appendChild(make("px", "left:0;top:0;", 1, color));
    }
    window.__vtxCanary = {mode: MODE, frame: frame, drawn: true};
    return true;
  }
  function schedule() {
    // 协议绘制时机：DOMContentLoaded 后双重 rAF（clean 模式契约）；headless
    // 下 rAF 由 BeginFrame 驱动，消费 harness 以 throwaway 截图兜底触发。
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { draw(f); });
    });
  }
  window.__vtxCanary = {mode: MODE, frame: f, drawn: false};
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", schedule);
  } else { schedule(); }
  // 可选 __vtx_render(f) 协议桥：页面实现页内置帧时透明包装（同参同返、异常
  // 照抛），置帧后同步重绘 Canary。页面未实现时零开销、零可观测差异。
  var origRender = null;
  try {
    Object.defineProperty(window, "__vtx_render", {
      configurable: true,
      get: function () {
        if (!origRender) { return undefined; }
        return function (f2) {
          var r = origRender(f2);
          draw(isFinite(f2) ? (f2 & 0xFFFF) : f);
          return r;
        };
      },
      set: function (v) { origRender = (typeof v === "function") ? v : null; }
    });
  } catch (e) { /* 环境限制（只读属性等）：放弃包装，?f= 帧号仍有效 */ }
})();"""


def get_canary_js(mode: str) -> str:
    """注入桥：返回 mode 的 JS 注入片段（零侵入，不改任何页面文件）。

    消费方式（issue #105）：
      CDP      Page.addScriptToEvaluateOnNewDocument(source=get_canary_js(mode))
      CEF      CefFrame::ExecuteJavaScript(js, url, start_line)
      Electron preload.js 里 process.on/IPC 首帧前 webFrame 顶层执行
    片段自包含（IIFE、ES5、无外部依赖）；页面侧可观测面仅
    ``window.__vtxCanary``（状态探针）与 ``[data-vtx-canary]`` 叠层节点。
    """
    _check_mode(mode)
    return (_JS_TEMPLATE
            .replace("__VTX_CANARY_MODE__", mode)
            .replace("__VTX_CANARY_KEY__", str(xor_key(mode))))
