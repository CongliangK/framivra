#!/usr/bin/env python3
"""frame_canary.py — 双轨帧同步水印协议 v2（双像素签名，GFX 离屏渲染双路线
战役 Phase 0，issue #105 r4；直接支撑 #114 CEF-03a / #116 ELEC-03a 的帧握手
生死线 K1）。

解决「Diagnostic Mode 有色块能查错帧，但切换 Clean Golden Mode 关掉色块后
Worker 又失去帧同步锚点」的逻辑悖论（对齐 CEF Issue #4166 的 Marker-Sync
机制）：离屏渲染输出帧与页面帧号 f 的绑定验证走双轨水印——

  模式 A ``diagnostic_8x8``（诊断核验模式）：页面 JS 在目标帧 f 渲染就绪后，
  在左上角 (0..7, 0..7) 绘制 8x8 纯色块（附右下角 (w-8..w-1, h-8..h-1) 第二
  Canary，供水半帧/下旧帧的撕裂对账，DoD「左上+右下双 Canary」口径）：
      左上块 = P1 签名色：R=(f>>8)&0xFF；G=f&0xFF；
               B=((f>>8)^(f&0xFF)^0xA5)&0xFF（8-bit xor 校验和）；A=255。
      右下块 = P2 签名色（v2 独立第二因子，通道互换+独立校验和）：
               R=f&0xFF；G=(f>>8)&0xFF；
               B=((16*(f&0xFF) + 22*((f>>8)&0xFF) + 0xC3))&0xFF；A=255。
  消费端（scripts/gfx_golden.py verify-canary）读取落盘 PNG：左上块按 P1 公式
  解码 f_TL、右下块按 P2 公式解码 f_BR；f_TL == f_BR == 请求帧 → ok；
  f_TL ≠ f_BR（两块各自解码成功）→ torn_frame；块内水平双色带 → torn_frame；
  右下块无可解码 P2 结构 → 双像素确认不可能 → ambiguous_signature（v1 曾豁免
  foreign 右下块——v2 起双像素确认是硬条件，宁拒不猜）。

  模式 B ``clean_1px_restore``（无损生产/Golden 模式，v2=双 1px 像素）：
  页面在设置好帧 f 的 DOM/Canvas 状态并完成双重 requestAnimationFrame 后，
  将 Canary 区**同一扫描线**（行 0）的两个相邻 1px 像素设为帧签名色：
      P1 @ (0,0)：R=(f>>8)&0xFF；G=f&0xFF；
               B=((f>>8)^(f&0xFF)^0x5A)&0xFF；A=254（刻意避开 A=255/A=0）。
      P2 @ (0,1)：R=f&0xFF；G=(f>>8)&0xFF（通道互换）；
               B=(16*R + 22*G + 0x3C)&0xFF（独立加性校验和）；A=254。
  双像素同扫描线是协议设计决策：光栅撕裂按扫描线发生，行 0 相邻两像素永远
  来自同一帧的同一缓冲区（且注入桥在同一次 draw() 中原子绘制），「P1 来自
  X1、P2 来自 X2」的混合观感在本通路物理不可达。

  判定规则（v2 核心，阳性确认当且仅当双像素一致）：对两像素各自独立解码得
  候选集 S1/S2（PNG 通路=±1 立方抗舍入候选，见下；BGRA 通路=预乘精确反像）：
      S1∩S2 == {f}    → 阳性确认帧 f（ok / wrong_frame_*，归因可信）
      S1∩S2 == ∅      → 帧号不可归属（双像素观感不相容）→ 拒绝
      |S1∩S2| > 1     → ambiguous_signature（多候选，宁拒不猜）
      任一 S_i == ∅    → checksum_mismatch（该像素无签名结构）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

v2 协议保证（全域穷举背书，证据 /tmp/framivra-evidence/issue-105-r4/，独立
实现公式不 import 本模块；65536 帧号全域扫描）：

  ① 陈旧帧零伪报（PNG 通路）：陈旧帧 X 的黑底观感 obs_i = round(sig_i(X)·
  254/255)（预乘存储，通道 ≥128 各降 1）。因每通道 |sig_i(X) − obs_i| ≤ 1，
  恒有 X ∈ S1∩S2 → 单例交集只能是 {X}，**任何**陈旧帧要么被正确归因为
  wrong_frame_*（含 v1 曾穿透的 16,386 对精确支与 ±1 多候选支——129→128、
  33665→33408、23168→23167 等红队三案全数归因正确），要么 ambiguous 拒绝
  ——永不伪报 ok。全域穿透对计数 = **0**（精确模型）；±1 对抗扰动模型
  （双像素独立每通道 ±1 最坏腐蚀）穿透对同样 = **0**；物理引擎映射模型
  （round/floor 合成 × 黑/白底极值 ×16 映射）穿透对 = **0**。
  机理（P2 免疫性）：通道降 1 使 P2 校验和移动 16·dR+22·dG ∈ {−38,−22,−16,
  0}，而 premul 只能给 B 移动 {0,−1}；±1 扰动叠加的全窗口分析（候选通道
  偏移 dR,dG∈[−3,2]，校验和窗口 shift∈[−3,2]）中 (16,22) 系数使全部非零
  组合落在窗外 → S2(obs_2) 恒单例 {X}（P2 自定位）。
  ② 真迹全域可确认（PNG 精确模型）：65536 帧全部 S1∩S2=={f}（v1 有 12 帧
  孪生不可确认；v2 归零）。物理引擎映射族（round/floor 合成 × 黑/白底极值
  ×16 映射、双像素独立）下同样全域确认（穷举背书）；±1 任意腐蚀下凡 obs
  与**直向签名**每通道距离 ≤1 者 S2 恒单例 {f} → 确认。观测被腐蚀到与
  premul 签名距离 2（≥128 通道 premul 降位叠加 −1 扰动——物理引擎族不可
  达）时真迹可能被拒（可用性损失，非伪报），全域 0 伪报保持。
  ③ BGRA 原生通路：P1 预乘孪生保持 v1 的 6 对/12 帧（逐字节相同的等价类，
  物理存在），P2 预乘孪生 = 0 对（16·dR+22·dG 无法被 premul 的 {0,−1} 位移
  抵消）；**联合孪生（双像素同时逐字节相同）= 0 对** → 双像素闸门下全域
  65536 帧陈旧归因恒单例、真迹恒可接受；v1 的 ambiguous 孪生拒绝路径在 v2
  闸门下全域不再触发（保留为防御分支）。
  ④ 跨模式混注免疫：同帧号四组合（P1d/P1c、P2d/P2c、P1d/P2c、P2d/P1c）
  全域互不相同（xor 键 0xA5/0x5A、加性键 0xC3/0x3C 奇偶分离）；诊断轨注入
  永不解码为合法 clean 签名（逐位置公式失配），反之亦然。

v1→v2 升级的根因（红队 #105 r1/r2/r3 三轮 FAIL 的等价类根因）：单像素签名
下「观测必为直向签名」不成立——陈旧预乘观感可逐字节等于他帧直向签名
（16,386 对），且 ±1 容差球使陈旧观感落入请求帧候选集（59,394 对）。v2 用
第二像素的**独立通道映射+独立校验和结构**（xor ↔ 加性 (16,22)，R/G 互换）
使单一帧号 X 的观感无法同时伪装成两套公式下另一帧 F 的签名——等价类交集
为空，歧义从「枚举拒绝」变为「结构性不存在」。

剩余边界（诚实清单，非缺陷兜底）：
  ① ±1 容差是协议噪声契约：观测若被腐蚀到与 premul 签名每通道距离 ≥2
  （超出引擎舍入/合成的物理可达集），真迹可能被拒（可用性损失，非伪报）；
  恶意像素级伪造（直接画 sig(F) 于双像素）不在威胁模型内——本协议防陈旧
  帧误认，不防恶意绘制。
  ② PNG 通路 |S1∩S2|>1 或 ∅ 的观测（撕裂中点/混合观感/超容差腐蚀）一律
  ambiguous/checksum 拒绝，宁拒不剥；单例交集的归因在上述物理模型内可信
  （穷举 0 反例），wrong_frame_* 计数因此可作可靠性口径。
  ③ 预乘孪生的**单像素**等价类仍在（P1 6 对/12 帧、P2 0 对）：单读 P1 的
  消费方仍须拒绝孪生帧（premul_twin_frames_pixel）；联合消费（本模块双
  像素闸门/校验）无此约束。
  ④ 撕裂交叉对（P1 视 X1、P2 视 X2，X1≠X2）在本通路物理不可达（同扫描线
  +原子绘制）；穷举计数见证据（torn_cross_*），仅供体外混刷管道参考。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

帧号域：16-bit [0, 65535]（P1 R 高 8 位 / G 低 8 位；P2 互换）；P2 加性
校验和 (16·R + 22·G + K2)&0xFF 对 (R,G) 单射 → 双像素签名各自全域单射。

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
  - 注入桥 clean 模式以 ``rgba(...,254/255)`` 绘制两个签名像素：透明页（gfx
    叠层页常态）+ 原生 OSR ``BGRA_PREMUL`` 缓冲区里两像素各自携带协议签名
    A=254、RGB=round(C·254/255)（预乘存储，``signature_bgra_premul`` /
    ``signature2_bgra_premul``）；``_premul_cands`` 用**精确反像区间**
    （(508C+255)//510 == v）反解——朴素 floor/ceil 直除法会让约半数帧号的
    签名校验和碰撞歧义（65536 帧实测 32773 帧，#105b 复审拦截）。
  - DOM/CSS 叠层在**不透明**合成输出（CDP 截图）中 α 恒拍平为 255，且
    rgba(254/255) 签名与底层页面像素按 α 混合：对任意一致引擎（round/floor）
    与任意底色，观测与预乘存储的每通道差 ∈ {−1, 0, +1}（物理引擎映射族，
    见保证②）。故 PNG 通路校验（``verify_clean_pixels``）不查 α，按 ±1
    立方候选（整数钳位，无 mod256 回绕——0/255 边界不产生假邻域）解码两
    像素、取交集裁决；诊断轨 ``rgb()`` 不透明纯色块不受此影响（8x8 块内
    RGB 精确）。

消费面变化（v1→v2，列明）：
  - ``verify_clean_pixel``（单像素）→ ``verify_clean_pixels(px1, px2, req)``
    （双像素）；单像素入口移除——它正是 r3 穿透面。
  - ``restore_clean_1px_rgba_inplace`` → ``restore_clean_2px_rgba_inplace``：
    剥离 (0,0) 与 (0,1) 两像素；邻域剥离源从右邻 (0,1)（v1）改为**下邻行**
    (1,0)/(1,1)（(0,1) 现为 P2 签名位）；画幅须 ≥2x2。original_rgba 升级为
    双像素原始色对 ``(o1, o2)``。
  - ``accept_paint_bgra_inplace``：读 (0,0)+(0,1) 双像素（buf[0:4]/buf[4:8]），
    accept 条件=双像素与期望帧双签名逐字节全等；original_bgra 升级为
    ``(o1, o2)`` 对；邻域剥离源=行 1 的 (1,0)/(1,1)；缓冲区须 ≥2 行。
  - ``premul_twin_frames(mode, f)``：语义升级为**联合孪生**（双像素预乘签名
    均逐字节相同的他帧）——v2 全域恒空元组（gfx_golden 纯导出保留，消费方
    不再需要避开任何帧号；单像素等价类改用 ``premul_twin_frames_pixel``）。
  - gfx_golden verify-canary：clean 模式读双像素；七态计数口径不变，
    wrong_frame_* 归因升级为「双像素交集单例、物理模型内可信」。

本模块纯标准库（验证/剥离只碰 2 像素邻域 + 两个 8x8 块，无需 numpy/PIL；
PNG 解码由调用方负责——scripts/gfx_golden._decode_rgba、测试 PIL 均可）。
"""
from __future__ import annotations

MODE_DIAGNOSTIC = "diagnostic_8x8"
MODE_CLEAN = "clean_1px_restore"
MODES = (MODE_DIAGNOSTIC, MODE_CLEAN)

DIAG_XOR_KEY = 0xA5   # 模式 A P1 校验和异或键（issue #105 冻结值）
CLEAN_XOR_KEY = 0x5A  # 模式 B P1 校验和异或键（issue #105 冻结值）
DIAG2_ADD_KEY = 0xC3  # 模式 A P2 校验和加性键（v2 冻结值；奇——对角奇偶分离）
CLEAN2_ADD_KEY = 0x3C  # 模式 B P2 校验和加性键（v2 冻结值；偶——对角奇偶分离）
P2_MUL_R = 16         # P2 校验和 R 系数（v2 冻结值；与 P2_MUL_G 联合使全部
                      # 非零候选通道偏移 (dR,dG)∈[-3,2]² 的校验和位移落在候选
                      # 窗 [-3,2] 之外——P2 自定位的数学根，模块头「保证①」）
P2_MUL_G = 22
DIAG_ALPHA = 255      # 模式 A 签名 α（诊断色块，不透明）
CLEAN_ALPHA = 254     # 模式 B 签名 α（双像素共用；刻意避开 A=255 与 A=0）
DIAG_SIZE = 8         # 模式 A 色块边长（像素）：左上 (0..7, 0..7) + 右下对角块

FRAME_MAX = 0xFFFF    # 帧号域上限（16-bit）

# clean 轨双像素位置（(x, y)）。P2 取 (1, 0)（紧邻 P1 右侧、同一扫描线）：
# 光栅撕裂按扫描线发生 → 双像素恒同帧；注入桥单次 draw() 原子绘制。
CLEAN_P1_XY = (0, 0)
CLEAN_P2_XY = (1, 0)

# verify-canary 帧裁决状态词汇（verify_* 返回 status；wrong_frame_count 聚合口径）
ST_OK = "ok"                          # 双像素交集单例 == 请求帧号，校验和通过
ST_LAG1 = "wrong_frame_lag1"          # 滞后 1 帧旧帧（rendered == requested - 1）
ST_STATIC_FIRST = "wrong_frame_static_first"   # 静态首帧残留（rendered == 0 且请求非 0）
ST_OTHER = "wrong_frame_other"        # 其余有效解码但帧号不符
ST_TORN = "torn_frame"                # 上下撕裂：TL 与 BR 双 Canary 帧号相异，或块内
                                       # 呈两条均合法签名的水平色带（半帧新/半帧旧）
ST_CHECKSUM = "checksum_mismatch"     # 校验和撕裂：签名结构位在但 B 通道对不上
ST_NO_CANARY = "no_canary"            # 左上区域无 Canary 结构（未注入/被遮挡/尺寸不符）
ST_AMBIGUOUS = "ambiguous_signature"  # 候选解码不唯一或不可归属：双像素候选交集
                                       # 空/多候选/单像素确认不可能（v2 双像素硬条件）。
                                       # 协议无法归属帧号 → 显式拒绝而非猜测（宁拒不剥）

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
    """P1 模式的校验和异或键（0xA5 / 0x5A）。"""
    _check_mode(mode)
    return DIAG_XOR_KEY if mode == MODE_DIAGNOSTIC else CLEAN_XOR_KEY


def add_key2(mode: str) -> int:
    """P2 模式的校验和加性键（0xC3 / 0x3C，v2 冻结值）。"""
    _check_mode(mode)
    return DIAG2_ADD_KEY if mode == MODE_DIAGNOSTIC else CLEAN2_ADD_KEY


def signature_rgb(mode: str, f: int) -> tuple[int, int, int]:
    """P1 帧签名 RGB（协议冻结公式）：R=hi, G=lo, B=hi^lo^K1。"""
    _check_mode(mode)
    f = _check_frame(f)
    hi, lo = (f >> 8) & 0xFF, f & 0xFF
    return hi, lo, (hi ^ lo ^ xor_key(mode)) & 0xFF


def signature2_rgb(mode: str, f: int) -> tuple[int, int, int]:
    """P2 帧签名 RGB（v2 冻结公式）：R=lo, G=hi（通道互换），
    B=(16*R + 22*G + K2)&0xFF（独立加性校验和）。

    与 P1 的独立性（等价类交集为空的根）：通道映射互换 + xor↔加性异构 +
    系数 (16,22) 使 premul 降位（B 移动 ∈{0,−1}）无法匹配加性位移
    {0,±16,±22,±38}——单帧陈旧观感不可能同时命中两公式下他帧签名。
    """
    _check_mode(mode)
    f = _check_frame(f)
    hi, lo = (f >> 8) & 0xFF, f & 0xFF
    return lo, hi, (P2_MUL_R * lo + P2_MUL_G * hi + add_key2(mode)) & 0xFF


def signature_rgba(mode: str, f: int) -> tuple[int, int, int, int]:
    """P1 帧签名 RGBA（A ∈ {255 诊断, 254 clean}；PNG 通路 CDP 截图 A 实现为
    255，见模块头「像素物理口径」——解码侧按 RGB+校验和判定，A 只在 BGRA
    通路严格）。"""
    r, g, b = signature_rgb(mode, f)
    return r, g, b, DIAG_ALPHA if mode == MODE_DIAGNOSTIC else CLEAN_ALPHA


def signature2_rgba(mode: str, f: int) -> tuple[int, int, int, int]:
    """P2 帧签名 RGBA（α 同 P1：诊断 255 / clean 254）。"""
    r, g, b = signature2_rgb(mode, f)
    return r, g, b, DIAG_ALPHA if mode == MODE_DIAGNOSTIC else CLEAN_ALPHA


def signature_bgra(mode: str, f: int) -> tuple[int, int, int, int]:
    """P1 帧签名 BGRA（字节序 Little-Endian 内存布局下的四元组 (B, G, R, A)）。"""
    r, g, b, a = signature_rgba(mode, f)
    return b, g, r, a


def signature2_bgra(mode: str, f: int) -> tuple[int, int, int, int]:
    """P2 帧签名 BGRA（(B, G, R, A) 内存布局）。"""
    r, g, b, a = signature2_rgba(mode, f)
    return b, g, r, a


def decode_pixel(mode: str, r: int, g: int, b: int) -> tuple[int, bool]:
    """P1 单像素解码：R/G 直读帧号（高/低 8 位），B 比对 xor 校验和。

    返回 (frame, checksum_ok)。checksum_ok=False 时 frame 仍给出 R/G 直读值
    （调用方据此分类 checksum_mismatch / no_canary——单像素无法区分「无
    Canary」与「校验和撕裂」，8x8 模式靠块结构区分，见 analyze_diag_block）。
    """
    _check_mode(mode)
    frame = ((int(r) & 0xFF) << 8) | (int(g) & 0xFF)
    ok = (int(b) & 0xFF) == ((int(r) ^ int(g) ^ xor_key(mode)) & 0xFF)
    return frame, ok


def decode_pixel2(mode: str, r: int, g: int, b: int) -> tuple[int, bool]:
    """P2 单像素解码：R 直读帧号低 8 位、G 直读高 8 位（通道互换），
    B 比对加性校验和 (16R + 22G + K2)&0xFF。"""
    _check_mode(mode)
    frame = ((int(g) & 0xFF) << 8) | (int(r) & 0xFF)
    ok = (int(b) & 0xFF) == \
        ((P2_MUL_R * (int(r) & 0xFF) + P2_MUL_G * (int(g) & 0xFF)
          + add_key2(mode)) & 0xFF)
    return frame, ok


def classify_frame(rendered: int, requested: int) -> str:
    """解码成功后的帧号分类（requested = 请求帧号，如落盘文件名 f0013 → 13）：
    ok / wrong_frame_lag1（滞后 1 帧旧帧）/ wrong_frame_static_first（静态首帧
    残留）/ wrong_frame_other。v2 双像素单例归因在物理模型内可信（模块头
    保证②），wrong_frame_* 可作可靠性口径。"""
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


def analyze_diag_block(block, pixel: int = 1) -> dict:
    """左上/右下 8x8 块结构分析（模式 A 解码核心，纯函数）。

    pixel=1 按左上 P1 公式解码，pixel=2 按右下 P2 公式解码（v2：两角签名
    结构彼此独立）。block：8x8 RGBA 像素（numpy 数组或嵌套序列，忽略 α）：
      kind == "uniform"：整块同色且校验和通过 → {"frame": f}
      kind == "banded"  ：块内两条**水平**色带（撕裂缓冲半新半旧），两色各自
                          校验和通过且各占 ≥1 整行 → {"frames": [f_new, f_old],
                          "bands": [行数, 行数]}（帧号按出现行序，上带在前）
      kind == "bad_checksum"：整块同色但校验和对不上（校验和撕裂；附直读帧号）
      kind == "foreign"：无 Canary 结构（页面原内容：杂色/非整行色带/多于两色）
    """
    if pixel not in (1, 2):
        raise ValueError(f"pixel 须为 1 或 2: {pixel!r}")
    dec = decode_pixel if pixel == 1 else decode_pixel2
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
        f, ok = dec(MODE_DIAGNOSTIC, r, g, b)
        return ({"kind": "uniform", "frame": f} if ok
                else {"kind": "bad_checksum", "frame": f})
    if len(uniq) == 2:
        # 恰两色且各占连续整行（水平撕裂带）；两色须均为合法签名
        frames = []
        for c in uniq:
            f, ok = dec(MODE_DIAGNOSTIC, c[0], c[1], c[2])
            if not ok:
                return {"kind": "foreign"}
            frames.append(f)
        bands = [colors.count(uniq[0]), colors.count(uniq[1])]
        return {"kind": "banded", "frames": frames, "bands": bands}
    return {"kind": "foreign"}


def verify_diagnostic_corners(rgba_arr, requested_frame: int, h: int = None,
                              w: int = None) -> dict:
    """整帧双角校验（模式 A v2；verify-canary 的帧级裁决，纯函数）。

    rgba_arr：可按下标 ``arr[y][x]`` / ``arr[y, x]`` 取 RGBA 四元组的像素阵
    （numpy (h,w,4) 数组或等价嵌套序列；PIL 图像请先转 numpy）；h/w 缺省时
    按 ``rgba_arr.shape`` 推断。

    左上 (0..7, 0..7) 为 P1 Canary（按 P1 公式解码 f_TL）；右下
    (h-8..h-1, w-8..w-1) 为 P2 Canary（按 P2 公式解码 f_BR）——v2 双像素
    确认是硬条件。返回 {"status", "rendered_frame"?, "detail"}：
      ok                f_TL == f_BR == requested
      wrong_frame_*     f_TL == f_BR == g ≠ requested（classify_frame 分类）
      torn_frame        f_TL ≠ f_BR（两角各自解码成功），或任一块内水平双色带
      ambiguous_signature 右下块无 P2 结构（foreign/bad_checksum）——双像素
                        确认不可能（v1 曾豁免 foreign 右下块；v2 起宁拒不猜）
      checksum_mismatch 左上块同色但校验和撕裂
      no_canary         左上块无 Canary 结构
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
        [[rgba_arr[y][x] for x in range(DIAG_SIZE)] for y in range(DIAG_SIZE)],
        pixel=1)
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
    br = analyze_diag_block(br_block, pixel=2)
    if br["kind"] == "banded":
        return {"status": ST_TORN, "rendered_frame": frame,
                "rendered_frames_br": br["frames"],
                "detail": f"右下块内水平双色带 {br['bands']} 行——半帧撕裂"}
    if br["kind"] == "uniform" and br["frame"] != frame:
        return {"status": ST_TORN, "rendered_frame": frame,
                "rendered_frame_br": br["frame"],
                "detail": f"左上 {frame} vs 右下 {br['frame']}——撕裂/旧帧残留"}
    if br["kind"] != "uniform":
        # foreign / bad_checksum：右下 P2 结构缺失或撕裂 → 双像素确认不可能
        return {"status": ST_AMBIGUOUS, "rendered_frame": None,
                "detail": f"右下 P2 Canary 不可解码（{br['kind']}）——"
                          "双像素确认不可能，宁拒不猜（v2 硬条件）"}
    status = classify_frame(frame, req)
    return {"status": status, "rendered_frame": frame,
            "detail": "" if status == ST_OK else
            f"rendered={frame} requested={req}"}


# ------------------------------------------------ Clean 双像素（PNG/Raster 通路）

def _cube_values(v: int) -> tuple:
    """通道值的 ±1 邻域（整数钳位 [0,255]，无 mod256 回绕——0/255 边界不产生
    假邻域，v1 的 ``& 0xFF`` 回绕在边界处多放候选）。"""
    return tuple(c for c in (v - 1, v, v + 1) if 0 <= c <= 255)


def clean_candidates_p1(px) -> set:
    """P1 像素 ±1 立方候选帧集（verify/restore 共用；px = RGB(A) 序列，
    忽略 α）。物理口径：任意一致引擎（round/floor 合成）+ 任意底色下观测与
    premul 签名每通道差 ∈ {−1,0,+1}（模块头「像素物理口径」）。"""
    r, g, b = int(px[0]), int(px[1]), int(px[2])
    out = set()
    for rr in _cube_values(r):
        for gg in _cube_values(g):
            for bb in _cube_values(b):
                f, ok = decode_pixel(MODE_CLEAN, rr, gg, bb)
                if ok:
                    out.add(f)
    return out


def clean_candidates_p2(px) -> set:
    """P2 像素 ±1 立方候选帧集（同上，P2 公式）。"""
    r, g, b = int(px[0]), int(px[1]), int(px[2])
    out = set()
    for rr in _cube_values(r):
        for gg in _cube_values(g):
            for bb in _cube_values(b):
                f, ok = decode_pixel2(MODE_CLEAN, rr, gg, bb)
                if ok:
                    out.add(f)
    return out


def _dual_pixels_verdict(px1, px2) -> tuple[set, set, set, str]:
    """双像素候选交集裁决（verify/restore/闸门共用的纯函数核心）。

    返回 (S1, S2, S1∩S2, 失败状态或 "")：
      任一 S_i 空 → (S1, S2, ∅, ST_CHECKSUM)（该像素无签名结构）；
      其余情况失败状态为 ""（交集空/多候选由调用方按 ambiguous 处理）。
    """
    s1 = clean_candidates_p1(px1)
    s2 = clean_candidates_p2(px2)
    if not s1 or not s2:
        miss = (("P1" if not s1 else "") + ("+" if not s1 and not s2 else "")
                + ("P2" if not s2 else ""))
        return s1, s2, set(), f"{ST_CHECKSUM}:{miss}"
    return s1, s2, s1 & s2, ""


def verify_clean_pixels(px1, px2, requested_frame: int) -> dict:
    """双像素校验（模式 B v2；px1 = (0,0)、px2 = (0,1) 像素 RGBA 四元组，
    α 不参与判据——CDP 截图不透明合成拍平为 255）。

    裁决（模块头「判定规则」，阳性确认当且仅当双像素一致）：
      S1∩S2 == {g} 单例 → classify_frame(g, requested)：ok / wrong_frame_*
        （v2 归因可信：穷举 0 反例——陈旧帧观感恒含 X 于交集，单例只能是
        真实绘制帧；wrong_frame_* 是可靠性口径，不是猜测）；
      |S1∩S2| > 1 → ambiguous_signature（多候选，宁拒不猜）；
      S1∩S2 == ∅（双像素各自有签名结构但不相容）→ ambiguous_signature
        （撕裂/混合观感，帧号不可归属）；
      任一像素无签名结构 → checksum_mismatch。
    返回词汇同 verify_diagnostic_corners（clean 轨无 no_canary——单像素无块
    结构，模式 B 由宿主保证签名位；torn 物理不可达——双像素同扫描线）。
    """
    req = _check_frame(requested_frame)
    s1, s2, inter, fail = _dual_pixels_verdict(px1, px2)
    if fail:
        which = fail.split(":", 1)[1]
        return {"status": ST_CHECKSUM, "rendered_frame": None,
                "detail": f"像素 {which} 无合法签名结构（±1 容差内无解）"}
    if len(inter) == 1:
        g = next(iter(inter))
        status = classify_frame(g, req)
        return {"status": status, "rendered_frame": g,
                "detail": "" if status == ST_OK else
                f"rendered={g} requested={req}"}
    if not inter:
        return {"status": ST_AMBIGUOUS, "rendered_frame": None,
                "detail": f"双像素候选不相容（|S1|={len(s1)} |S2|={len(s2)} "
                          "S1∩S2=∅——撕裂/混合观感，帧号不可归属，宁拒不猜）"}
    return {"status": ST_AMBIGUOUS, "rendered_frame": None,
            "detail": f"双像素候选交集多候选（{sorted(inter)}）——帧号不可"
                      "归属，宁拒不猜"}


# ------------------------------------------------ Native BGRA 通路（模式 B）

def _premul_bgra(rgba_sig: tuple) -> tuple:
    """直向 BGRA 四元组 (B,G,R,A) → 预乘存储口径 (round(C·A/255), A=254)。"""
    b, g, r, a = rgba_sig
    k = a / 255.0
    return (int(b * k + 0.5), int(g * k + 0.5), int(r * k + 0.5), a)


def signature_bgra_premul(mode: str, f: int) -> tuple[int, int, int, int]:
    """P1 BGRA_PREMUL 存储口径的期望签名：RGB = round(C·254/255)，A = 254。

    预乘是「合成器数学事实」（Skia/CEF/Electron 缓冲区皆预乘），round 为
    半向上舍入（与 gfx_golden._rdiv255 同式）。宿主比对请配 ±1 容忍
    （match_bgra_premul）——不同引擎的预乘舍入实现存在 ±1 差异可能。
    """
    return _premul_bgra(signature_bgra(mode, f))


def signature2_bgra_premul(mode: str, f: int) -> tuple[int, int, int, int]:
    """P2 BGRA_PREMUL 存储口径的期望签名（同式预乘，P2 直向签名输入）。"""
    return _premul_bgra(signature2_bgra(mode, f))


def match_bgra_premul(px_bgra, expected_frame: int, mode: str = MODE_CLEAN,
                      tol: int = 1, pixel: int = 1) -> bool:
    """预乘缓冲区签名像素与期望帧签名的比对（宿主 ±1 校准 API，非闸门判据；
    pixel=1/2 选 P1/P2）。tol=0 退化为精确匹配（同引擎同实现时）。"""
    exp = (signature_bgra_premul(mode, expected_frame) if pixel == 1
           else signature2_bgra_premul(mode, expected_frame))
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


def _premul_decode_matches(px_bgra, pixel: int = 1) -> set:
    """预乘签名像素反解的完整候选帧集合（decode_bgra_premul 底层；纯函数）。

    pixel=1/2 选 P1/P2 公式。三通道直存值各取精确反像集（每通道 1-2 个 C）
    做笛卡尔组合，校验和过滤。返回候选帧号集合（v2 下 P2 恒单例——模块头
    保证③；P1 的多候选=预乘孪生等价类，premul_twin_frames_pixel）。
    """
    if pixel not in (1, 2):
        raise ValueError(f"pixel 须为 1 或 2: {pixel!r}")
    dec = decode_pixel if pixel == 1 else decode_pixel2
    matches = set()
    for r in _premul_cands(int(px_bgra[2])):
        for g in _premul_cands(int(px_bgra[1])):
            for b in _premul_cands(int(px_bgra[0])):
                f, ok = dec(MODE_CLEAN, r, g, b)
                if ok:
                    matches.add(f)
    return matches


def premul_twin_frames_pixel(mode: str, f: int, pixel: int = 1) -> tuple:
    """帧 f 的**单像素**预乘孪生（与 f 的第 pixel 号签名预乘存储逐字节相同
    的其他帧，升序元组）。

    P1（clean）：全域 6 对/12 帧（23167/23168、42367/42368 相邻对 +
    32602/32858、32640/32895、32639/32896、32677/32933 远对）；P2（clean）：
    全域 0 对（(16,22) 加性位移无法被 premul 的 {0,−1} 抵消）；diagnostic
    A=255 预乘恒等，无孪生。单读单像素的消费方须据此拒绝孪生帧；双像素
    联合消费（本模块闸门/校验）无此约束（联合孪生全域空）。
    """
    _check_mode(mode)
    f = _check_frame(f)
    if mode == MODE_DIAGNOSTIC:
        return ()
    sig = (signature_bgra_premul(mode, f) if pixel == 1
           else signature2_bgra_premul(mode, f))
    return tuple(sorted(_premul_decode_matches(sig, pixel=pixel) - {f}))


def premul_twin_frames(mode: str, f: int) -> tuple:
    """帧 f 的预乘孪生帧号（v2 = **联合孪生**：P1 与 P2 预乘签名**同时**逐
    字节相同的其他帧，升序元组）。

    v2 语义：clean 全域**恒空元组**（P1 孪生 6 对与 P2 孪生 0 对无公共对，
    穷举背书——模块头保证③）；diagnostic A=255 预乘恒等，无孪生。
    消费面变化：v1 消费方（选帧/抽样管道经 gfx_golden 纯导出）曾用本函数
    预避开 12 个不可确认帧号——v2 起全域可确认，**无需避开任何帧号**；
    导出保留（gfx_golden.premul_twin_frames）以稳定调用面，语义升级为联合。
    单像素等价类请改用 premul_twin_frames_pixel。
    """
    t1 = set(premul_twin_frames_pixel(mode, f, pixel=1))
    if not t1:
        return ()
    t2 = set(premul_twin_frames_pixel(mode, f, pixel=2))
    return tuple(sorted(t1 & t2))


def decode_bgra_premul(px_bgra, pixel: int = 1) -> tuple[int, bool]:
    """预乘签名像素反解帧号（唯一候选时 (frame, True)，否则 (0, False)）。

    ok=False 有两种诚实战况，调用方须区分处理而非「换个比对兜底」：
      零候选 = 无合法签名结构；
      多候选 = 该像素的预乘孪生等价类在世（P1 6 对/12 帧；P2 无）——观测
      签名与多个帧号的签名逐字节相同。双像素消费面（accept_paint_bgra_
      inplace）取两像素候选交集裁决，单像素多候选不再是拒绝理由。
    """
    matches = _premul_decode_matches(px_bgra, pixel=pixel)
    if len(matches) != 1:
        return 0, False
    return matches.pop(), True


def accept_paint_bgra_inplace(buf: bytearray, width: int, expected_frame=None,
                              original_bgra=None) -> dict:
    """Native Worker 的 OnPaint/paint 帧闸门（模式 B v2 核心消费面）。

    buf：整帧 BGRA_PREMUL 缓冲区（bytearray，行主序）：(0,0)=buf[0:4] 为 P1
    签名位、(0,1)=buf[4:8] 为 P2 签名位（同扫描线，v2 双像素）；(1,0)/
    (1,1)（行 1）为各自的邻域剥离源。width 须 ≥2 且缓冲区须 ≥2 行
    （v1 用 (0,1) 作剥离源——该位现为 P2 签名位，v2 改用行 1，消费面变化）。

    判定（三态语义保持，判据升级为双像素）：
      双像素与期望帧 f 的预乘签名**同时逐字节相等** → 匹配：原地把 (0,0)/
        (0,1) 覆写为宿主上报原始值（original_bgra=(o1, o2) 对，优先）或
        行 1 邻色 (1,0)/(1,1)，返回 accepted=True + rendered_frame=f；
      不全等 → 陈旧/中间帧（如 CEF #4166 软件路径同步重放帧），**不改动
        buf**，返回 rejected_stale_paint=True；rendered = 双像素候选交集
        单例（v2 归因可信——联合孪生全域空，陈旧帧恒归因为自身帧号），
        交集不单例时 None 并附 status=ambiguous_signature；
      expected_frame=None（skip-gate 离线管道）：不比对期望帧，但要求双像素
        候选交集单例才剥离（空=no_signature / 多候选或不相容=ambiguous_
        signature 宁拒不剥）。
    v1 的「期望帧孪生在世 → ambiguous 拒绝」分支在 v2 全域不触发（联合孪生
    恒空）——保留为防御分支（穷举若被未来系数变更打破时兜底）。
    """
    if width < 2:
        raise ValueError(f"画幅宽 {width} < 2：v2 双像素闸门需要 (0,0)+(0,1)")
    if len(buf) < 8 * width:
        raise ValueError(f"缓冲区 {len(buf)} 字节不足两行（{width}px 宽）——"
                         "v2 邻域剥离源在行 1")
    px1 = tuple(buf[0:4])
    px2 = tuple(buf[4:8])
    s1 = _premul_decode_matches(px1, pixel=1)
    s2 = _premul_decode_matches(px2, pixel=2)
    inter = s1 & s2
    rendered = next(iter(inter)) if len(inter) == 1 else None
    if expected_frame is not None:
        exp = _check_frame(expected_frame)
        exp1 = signature_bgra_premul(MODE_CLEAN, exp)
        exp2 = signature2_bgra_premul(MODE_CLEAN, exp)
        if px1 != exp1 or px2 != exp2:
            if not s1 or not s2:
                reason = "stale_paint:no_signature"
            elif len(inter) == 1:
                reason = f"stale_paint:rendered={rendered}"
            elif not inter:
                reason = "stale_paint:attribution_incompatible"
            else:
                reason = "stale_paint:attribution_ambiguous"
            return {"accepted": False, "rejected_stale_paint": True,
                    "reason": reason,
                    "status": ST_AMBIGUOUS if (s1 and s2 and len(inter) != 1)
                    else None,
                    "rendered_frame": rendered, "restored": False}
        if len(inter) > 1:
            # 防御分支：联合孪生全域空（穷举背书），触发即协议常数被破坏
            return {"accepted": False, "rejected_stale_paint": True,
                    "reason": "ambiguous_signature:twin=" +
                              "|".join(map(str, sorted(inter - {exp}))),
                    "status": ST_AMBIGUOUS, "rendered_frame": None,
                    "restored": False,
                    "twin_frames": sorted(inter - {exp})}
        rendered = exp
    else:
        if not s1 or not s2:
            return {"accepted": False, "rejected_stale_paint": False,
                    "reason": "no_signature", "rendered_frame": None,
                    "restored": False}
        if len(inter) != 1:
            return {"accepted": False, "rejected_stale_paint": False,
                    "reason": ("ambiguous_signature:S1∩S2=" +
                               ("∅" if not inter
                                else "|".join(map(str, sorted(inter))))),
                    "status": ST_AMBIGUOUS, "rendered_frame": None,
                    "restored": False}
    if original_bgra is not None:
        src1, src2 = tuple(original_bgra[0]), tuple(original_bgra[1])
        source = "reported_original"
    else:
        src1 = tuple(buf[4 * width:4 * width + 4])        # (1,0)
        src2 = tuple(buf[4 * width + 4:4 * width + 8])    # (1,1)
        source = "neighbor_row1"
    buf[0:4] = bytes(src1)
    buf[4:8] = bytes(src2)
    return {"accepted": True, "rejected_stale_paint": False, "reason": "ok",
            "rendered_frame": rendered,
            "restored": True, "restore_source": source}


def restore_clean_2px_rgba_inplace(arr, expected_frame=None,
                                   original_rgba=None) -> dict:
    """PNG/Raster 通路的原地剥离（模式 B v2；arr 为可写 (h,w,4) uint8 数组，
    如 gfx_golden._decode_rgba 的返回或 PIL 转 numpy 的帧）。画幅须 ≥2x2。

    先双像素校验（verify_clean_pixels 同一裁决）：expected_frame 给出时须
    S1∩S2 == {expected} 才放行（单例他帧 → 按其 verdict 拒绝剥离，宁拒不
    错；v2 归因可信）；expected_frame=None 时须交集单例才剥离（空=no_
    signature/不相容、多候选=ambiguous——均宁拒不剥）。随后 (0,0)/(0,1) ←
    original_rgba=(o1, o2)（宿主上报原始色对，优先）或行 1 邻色 (1,0)/(1,1)
    （v1 的右邻 (0,1) 源已废弃——该位现为 P2 签名位，消费面变化）。
    返回 restored/rendered_frame/restore_source/status。
    剥离后帧与无 Canary 原生 Golden 的 Tier-1 全等由调用方用
    gfx_golden.analyze_frame_diff 验证（DoD 口径）。
    """
    h, w = arr.shape[0], arr.shape[1]
    if h < 2 or w < 2:
        raise ValueError(f"画幅过小 {w}x{h}：v2 双像素剥离需要 ≥2x2"
                         "（签名位 (0,0)+(0,1)，邻域源在行 1）")
    px1 = tuple(int(v) for v in arr[0][0])
    px2 = tuple(int(v) for v in arr[0][1])
    s1, s2, inter, fail = _dual_pixels_verdict(px1, px2)
    if fail:
        return {"restored": False, "status": ST_CHECKSUM,
                "rendered_frame": None}
    if expected_frame is not None:
        exp = _check_frame(expected_frame)
        if inter != {exp}:
            v = verify_clean_pixels(px1, px2, exp)
            return {"restored": False, "status": v["status"],
                    "rendered_frame": v.get("rendered_frame")}
        frame = exp
    else:
        if len(inter) != 1:
            return {"restored": False, "status": ST_AMBIGUOUS,
                    "rendered_frame": None}
        frame = next(iter(inter))
    if original_rgba is not None:
        src1 = tuple(int(v) for v in original_rgba[0])
        src2 = tuple(int(v) for v in original_rgba[1])
        source = "reported_original"
    else:
        src1 = tuple(int(v) for v in arr[1][0])   # (1,0)
        src2 = tuple(int(v) for v in arr[1][1])   # (1,1)
        source = "neighbor_row1"
    arr[0][0] = src1
    arr[0][1] = src2
    return {"restored": True, "status": ST_OK, "rendered_frame": frame,
            "restore_source": source}


# ------------------------------------------------ JS 注入桥（零侵入叠加层）

_JS_TEMPLATE = r"""(function () {
  "use strict";
  // vtx/frame_canary.py 注入桥（issue #105 v2 双像素签名）：?f= 帧驱动页面的
  // 零侵入帧同步水印。帧号与页面协议同源（?f=）；Canary 状态 = f 的纯函数
  // （诊断叠加层，非页面动画）。
  var MODE = "__VTX_CANARY_MODE__";
  var KEY1 = __VTX_CANARY_KEY1__;      // P1 xor 键（0xA5 诊断 / 0x5A clean）
  var KEY2 = __VTX_CANARY_KEY2__;      // P2 加性键（0xC3 诊断 / 0x3C clean）
  var MR = 16, MG = 22;                // P2 校验和系数（v2 冻结值）
  var SIZE = 8;                        // diagnostic_8x8 块边长；clean 模式用 1px
  var raw = new URLSearchParams(location.search).get("f");
  var f = parseInt(raw || "0", 10);
  if (!isFinite(f) || f < 0) { f = 0; }
  f &= 0xFFFF;                         // 16-bit 帧号域（与协议一致）
  // 签名色必须按「本次绘制的帧号」现算（不能在注入时对 ?f= 闭包一次）：
  // __vtx_render(f2) 桥接重绘时帧号已换，闭包旧色 = 陈旧签名（#105b 修复）。
  // clean 模式用 rgba(...,254/255)：透明页（gfx 叠层页常态）+ 原生 OSR
  // BGRA_PREMUL 缓冲区里两像素才各带 A=254 协议签名；不透明合成通路（CDP
  // 截图）下 α 被拍平为 255、RGB 与底层像素 ±1 混合——消费端 verify/decode
  // 的 ±1 抗舍入策略即为此设计（模块头「像素物理口径」）。
  function colorP1(frame) {
    var hi = (frame >> 8) & 255, lo = frame & 255;
    var rgb = hi + "," + lo + "," + ((hi ^ lo ^ KEY1) & 255);
    return (MODE === "clean_1px_restore")
      ? "rgba(" + rgb + ",0.996078431372549)"   // 254/255，A=254 签名
      : "rgb(" + rgb + ")";                     // 诊断轨不透明 A=255
  }
  function colorP2(frame) {
    var hi = (frame >> 8) & 255, lo = frame & 255;
    var b = (MR * lo + MG * hi + KEY2) & 255;
    var rgb = lo + "," + hi + "," + b;          // 通道互换 + 独立加性校验和
    return (MODE === "clean_1px_restore")
      ? "rgba(" + rgb + ",0.996078431372549)"
      : "rgb(" + rgb + ")";
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
    // v2 双像素：clean 轨 P1@(0,0) + P2@(1px,0) 同扫描线原子绘制；诊断轨
    // 左上块 = P1 签名色、右下块 = P2 签名色（双公式独立）。
    if (MODE === "diagnostic_8x8") {
      root.appendChild(make("tl", "left:0;top:0;", SIZE, colorP1(frame)));
      root.appendChild(make("br", "right:0;bottom:0;", SIZE, colorP2(frame)));
    } else {
      root.appendChild(make("p1", "left:0;top:0;", 1, colorP1(frame)));
      root.appendChild(make("p2", "left:1px;top:0;", 1, colorP2(frame)));
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
    """注入桥：返回 mode 的 JS 注入片段（v2 双像素签名，零侵入）。

    消费方式（issue #105）：
      CDP      Page.addScriptToEvaluateOnNewDocument(source=get_canary_js(mode))
      CEF      CefFrame::ExecuteJavaScript(js, url, start_line)
      Electron preload.js 里 process.on/IPC 首帧前 webFrame 顶层执行
    片段自包含（IIFE、ES5、无外部依赖）；页面侧可观测面仅
    ``window.__vtxCanary``（状态探针）与 ``[data-vtx-canary]`` 叠层节点
    （clean 轨 p1/p2 两像素；诊断轨 tl/br 两块）。
    """
    _check_mode(mode)
    return (_JS_TEMPLATE
            .replace("__VTX_CANARY_MODE__", mode)
            .replace("__VTX_CANARY_KEY1__", str(xor_key(mode)))
            .replace("__VTX_CANARY_KEY2__", str(add_key2(mode))))
