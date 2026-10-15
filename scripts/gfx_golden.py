#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gfx_golden.py · gfx 帧 golden 像素基线构建/比对（vtx 导出通道改造方案 v2 阶段 0）

为「Chrome 截图导出 → canvas 批量导出」通道改造提供像素一致性硬门槛：
golden = 当前截图路径产物（cache/gfx/<12位键>/f{:04d}.png，1080p 带 alpha）的
逐帧像素 hash 快照。改造页必须逐帧零差异才算过（--full-pages 全帧 hash）；
未改造页抽检（默认 [0, dur//2, dur-1] 均匀补足 --sample 帧，含首中尾）。

hash 口径：PIL 解码 PNG → 统一 RGBA → 原始字节行主序 sha256。像素 hash 而非
文件字节 hash——PNG 编码器（压缩级别/过滤策略）可变，像素不可变。

golden JSON 结构（自包含、无歧义）：
  meta:   构建时间/chrome 版本(可空)/fps/视口/hash 算法/抽样标记/键算法说明/
          env_manifest(--env-manifest 嵌入的环境指纹，可空)
  pages:  { "<clip src 相对路径>":
              { "durs": { "<dur>": { "dur":…, "key":…, "mode":"sample|full",
                                     "frames": { "<帧号%04d>": <sha256hex> } } } } }
  同一 src 多个 clip（不同 dur）→ 不同缓存键（gfx_key 含 dur），在 durs 下并列。

用法（项目根；本机跑 python 必须加 env 前缀，见全局环境注意事项）：
  env python3 scripts/gfx_golden.py build -t timeline.json --golden gfx_golden.json \
      [--full-pages c5a_main_path,c5b_hotkey] [--sample 5] \
      [--env-manifest env_manifest.json]
  env python3 scripts/gfx_golden.py compare -t timeline.json --golden gfx_golden.json \
      [--report report.json] [--pages c5a_main_path] [--strict]
  env python3 scripts/gfx_golden.py analyze --ref <png|帧目录> --cand <png|帧目录> \
      [--heatmap-dir dir] [--report report.json]
  env python3 scripts/gfx_golden.py verify-canary --frames <f{:04d}.png 帧目录> \
      [--mode diagnostic_8x8|clean_1px_restore] [--report report.json]
  env python3 scripts/gfx_golden.py verify-canary --png f0013.png [--frame 13] \
      [--mode ...]                       # 单帧模式；--frame 缺省取文件名帧号

双轨帧同步水印校验（issue #105，vtx/frame_canary.py 协议实现）：
  verify-canary 读取落盘 PNG 的 Canary 水印解码 rendered_frame_index 并核验
  校验和；rendered_frame_index != requested_frame_index（帧目录模式
  requested = 文件名帧号）立即计入 wrong_frame_count，并区分「滞后 1 帧旧帧
  （wrong_frame_lag1）/ 静态首帧残留（wrong_frame_static_first）/ 校验和撕裂
  （checksum_mismatch）/ 上下撕裂（torn_frame：diagnostic_8x8 左上+右下双
  Canary 帧号相异或块内水平双色带）/ 其余（wrong_frame_other）/
  no_canary（左上无水印结构）」。diagnostic_8x8 读左上 8x8（附右下 8x8 撕裂
  对账）；clean_1px_restore 读单像素 (0,0)（PNG 通路 A 拍平为 255，按
  RGB+校验和判定并容忍 ±1 混合——注入桥以 rgba(...,254/255) 绘制，像素物理
  口径见 vtx/frame_canary.py 模块头）。
  退出码：全部 ok → 0；任一非 ok / 解码错误 / 帧缺失 → 1。
  clean 模式的「原地剥离还原 Tier-1」不在本子命令（剥离在渲染侧
  restore_clean_1px_rgba_inplace / Native 侧 accept_paint_bgra_inplace，
  还原帧再用 analyze 对无水印基准证 Tier-1）。

三级像素裁决（issue #104，analyze 子命令 / analyze_frame_diff）：
  golden hash 只能说「有差异」，且 compare 时点手上只有 golden 的 hash、没有
  参照像素（golden 不存图）；深度审计需要 ref/cand 两套帧产物并列，故独立成
  analyze 子命令（--ref/--cand 各取 PNG 文件或 f{:04d}.png 帧目录），把一对帧
  （如 Chrome CDP 基线 vs CEF/Electron 候选）逐像素裁决为三级——
  Tier-1 verified_exact：解码后 8-bit Straight RGBA 全画幅零差异；
  Tier-2 premul_equivalent：仅半透明区（0<A<255）Straight RGB 差异，且黑底+白底
  预乘重投影（C_comp=round((C·A+B·(255−A))/255)）残差 ≤1（逆预乘整数舍入等价，
  视为通过），单列 premul_rounding_pixel_count / max_premul_residual；
  Tier-3 visual_failure：实质失败，labels 细分（alpha_channel_diff /
  premul_double_blend_halo / opaque_color_shift / structural_or_missing_layer，
  可多标签共存）+ 兜底 unclassified_diff。
  Tier-2/3 帧可产双半幅误差热力图 diff_f{:04d}.png（左=参照帧直投灰底；右=误差
  放大 16× 伪彩，绿=Tier-2 舍入点、红=Tier-3 实质错误；tmp+replace 原子写）。
  analyze 退出码：全部 Tier-1/Tier-2 → 0；任一 Tier-3 / 解码或配对错误 → 1。

      [--report report.json] [--pages c5a_main_path] \
      [--env-manifest env_manifest.json]

跨后端环境前置门禁（issue #102）：build --env-manifest 把渲染运行产出的
env_manifest.json（vtx/env_manifest.py，不可伪造的环境指纹）嵌入 golden.meta；
compare --env-manifest 提供当前后端实测 manifest 时，先经
vtx.env_manifest.verify_manifest_compatibility 比对 Chromium 主版本/生效后端
（GPU 静默回退检出）/色彩空间/视口/字体 SHA256——任一不兼容即拒绝像素比对
（exit 1，不比错环境下的像素）。golden 含 manifest 而 compare 未提供 → 显式
警告门禁未激活（不阻断同机常规回环）；compare 提供而 golden 未嵌入 → 显式报错。

实现要点：
  - 缓存键/帧目录定位直接复用 vtx/hygiene.gfx_key（v2 §1.4 包导入，注入 repo 根）——
    golden 与渲染器永远同一套键；键逻辑若变，compare 以「目录缺失」显式失败并提示
    重建 golden，绝不静默错比。
  - 帧缺失/目录缺失在 build 即报错退出非零（golden 必须完整，宁缺毋滥）。
  - 并行：ThreadPoolExecutor（PIL C 解码释放 GIL）；GFX_GOLDEN_WORKERS 可调，
    默认 min(8, cpu)。目标：3530 帧 1080p 解码 hash ≈ 2-3 分钟内。
  - 纯标准库 + PIL；#104 深度审计另用 numpy（向量化；仓库既有依赖面——
    vtx/gfx_render 可选加速与 apps/gates 均在用，非新引入）；不改 vtx/（只读复用）。
  - #104 性能包络（1080p 分析段中位，本机 numpy 2.5；PNG 解码 ~35-55ms 属 I/O，
    不在预算内，见 decode_ms 字段）：全部 Tier-1/Tier-2 通过态路径 ≤15ms——
    Tier-1 走 memcmp 级快路径；Tier-2 先试全画幅 uint8/uint16 快速走廊（三
    条件可证 Tier-2 即免收集免重投影直裁，覆盖低 α 稠密舍入与全屏中
    α=128·ΔRGB=2 的逆预乘舍入推广场景），不适用（混合形态/需热力图）走
    xor+take 收集 + α·ΔC≤256 数学上界快速通道。α·ΔC>256 的稠密 Tier-2 与
    Tier-3 失败帧需全量精确重投影（uint16 移位除法向量化），最重 ~300ms——
    失败帧为离线诊断口径，非 #104 预算路径（超预算包络如实披露，docstring 同）。
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))   # v2 §1.4 包化：注入 repo 根 + 包导入（不再注入 repo/vtx）
from vtx.infra import Ctx   # noqa: E402
from vtx import hygiene     # noqa: E402
from vtx import env_manifest  # noqa: E402  （issue #102 跨后端环境前置门禁）
from vtx import frame_canary as fc  # noqa: E402  （issue #105 双轨帧同步水印协议）

HASH_DESC = "sha256(PIL 解码 → RGBA 原始字节, 行主序) 每帧 64 hex"


def make_ctx(tl_path: Path) -> Ctx:
    """与 vtx 主入口同一套 Ctx（fps 契约/声明依赖快速失败全继承）。"""
    return Ctx(tl_path.resolve())


def gfx_clips(c: Ctx):
    """timeline 全部 gfx clip 的 (src, dur) 去重序列（保持出现序）。

    同页同 dur 的重复 clip 指向同一缓存目录（gfx_key 只含 src/dur/fps/视口/内容），
    golden 只记一份；同页不同 dur → 不同键，golden 中并列。
    """
    seen, out = set(), []
    for tr in c.tl.get("video_tracks", []):
        if tr.get("kind") != "gfx":
            continue
        for cl in tr.get("clips", []):
            k = (cl["src"], int(cl["dur"]))
            if k not in seen:
                seen.add(k)
                out.append(k)
    return out


def _match(name: str, src: str) -> bool:
    """名单匹配：src 相对路径全等 / src 文件名 stem 全等 / 子串包含，任一命中。"""
    return name == src or name == Path(src).stem or name in src


def _split_names(s: str):
    return [x.strip() for x in s.split(",") if x.strip()]


def sample_frames(dur: int, sample: int):
    """抽样帧号：[0, dur//2, dur-1] + 均匀补足到 ≥sample（含首中尾），升序去重。"""
    idx = {0, dur // 2, dur - 1}
    if sample > len(idx) and dur > 1:
        idx.update(round(k * (dur - 1) / (sample - 1)) for k in range(sample))
    return sorted(i for i in idx if 0 <= i < dur)


def workers_n() -> int:
    v = os.environ.get("GFX_GOLDEN_WORKERS", "")
    if v.isdigit() and int(v) > 0:
        return int(v)
    return max(1, min(8, os.cpu_count() or 8))


def hash_frame(path: Path) -> str:
    """单帧像素 hash：PNG → RGBA → 行主序原始字节 sha256。"""
    from PIL import Image
    with Image.open(path) as im:
        im.load()
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        return hashlib.sha256(im.tobytes()).hexdigest()


def hash_frames(tasks, what: str):
    """并行 hash。tasks=[(label, path)]，label 任意可哈希元组。

    返回 ({label: hex}, [(label, path, exc)])；调用方决定失败语义。进度每 200 帧
    打一行（3530 帧 × 1080p 不刷屏）。
    """
    out, fails = {}, []
    n = len(tasks)
    done = 0
    with ThreadPoolExecutor(workers_n()) as ex:
        futs = {ex.submit(hash_frame, p): (label, p) for label, p in tasks}
        for fu in as_completed(futs):
            label, p = futs[fu]
            done += 1
            try:
                out[label] = fu.result()
            except Exception as e:   # 解码失败/文件损坏：交调用方定语义
                fails.append((label, p, e))
            if done % 200 == 0 or done == n:
                print(f"  {what} {done}/{n}（{workers_n()} 线程）")
    return out, fails


def chrome_version():
    """构建环境佐证（可空）：best-effort 探测 chrome --version，失败不阻塞。"""
    cands = [os.environ.get("VTX_CHROME"), "google-chrome", "google-chrome-stable",
             "chromium", "chromium-browser"]
    for name in cands:
        if not name:
            continue
        exe = shutil.which(name)
        if not exe:
            continue
        try:
            r = subprocess.run([exe, "--version"], capture_output=True, text=True,
                               timeout=15)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
    return None


# ------------------------------------------------- 深度像素审计（issue #104：三级裁决 + 热力图）

# Tier 词汇（跨后端 Golden 像素审计的裁决契约；下游 #107 基线 / #118 #119 审计消费）
TIER_EXACT = "verified_exact"       # Tier-1：解码后 8-bit Straight RGBA 全画幅零差异
TIER_PREMUL = "premul_equivalent"   # Tier-2：仅半透明区逆预乘整数舍入，重投影残差 ≤1
TIER_FAIL = "visual_failure"        # Tier-3：实质视觉失败（labels 细分，可多标签）

L_ALPHA = "alpha_channel_diff"            # α 通道有差（max|ΔA|>0）
L_HALO = "premul_double_blend_halo"       # 半透明区二次预乘发黑（黑边签名）
L_OPAQUE = "opaque_color_shift"           # 双方 A=255 区 RGB 色阶偏移（≥1）
L_STRUCT = "structural_or_missing_layer"  # 大幅色差（max|Δ|>5：撕裂/丢层）
L_UNCLASS = "unclassified_diff"           # 兜底：失败但上列四标签全未命中（保证 labels
                                          # 恒非空；典型=全透明区 RGB 差异 / ≤5 色阶且
                                          # 无预乘签名的半透明漂移——合成上近隐形但存储
                                          # 语义确有差异，交人工裁决而非静默归零）

_HEATMAP_GRAY_BG = 128   # 热力图左半幅投影底色（C_comp 公式的 B 值）
_HEATMAP_AMP = 16        # 热力图右半幅误差放大倍数
_STRUCT_MAX_ABS = 5      # structural 标签阈值：全画幅任一 RGBA 通道 max|Δ| > 5

# 快速通道数学上界（#104 返工，255→256 收紧）：α·max|ΔRGB| ≤ 256 ⟹ 黑/白底预乘
# 重投影残差 ≤1。证明要点：f(C)=round(C·A/255) 单调增，残差 ≥2 ⟺ q=C·A 轴开区间
# (q_lo, q_hi] 内含 ≥2 个阶梯跳变（跳变位于 q≡128 (mod 255)，间距恒 510），两个
# 跳变需区间长 2·(A·ΔC) ≥ 511；A·ΔC ≤ 255 时不可能，=256 时唯一形态解
# C_lo = 255 − ΔC/2 恒出 C 域 [0, 255−ΔC]（ΔC≥2；A·ΔC=256 ⟹ A | 256）。
# 全域穷举复核（红队同强度）：A∈[1,254] × C_r × C_c 共 16,646,144 组零违例；
# 界最紧不可再宽：积 257 无整数分解，258 即有反例（A=2, C 63→192；A=129,
# C 84→86，残差均 2）。
_QUICK_PROD_BOUND = 256

# 走廊中间缓冲池（按像素数缓存）：analyze 为单线程顺序调用（CLI/测试均串行），
# 17MB 级临时若每次调用重新分配，glibc 混合尺寸 mmap/munmap 逐次触发 ~4.5k 次
# 缺页（1080p 实测 +6ms）；复用后稳态零分配零缺页。
_CORRIDOR_SCRATCH = {}


def _corridor_scratch(np, n):
    sc = _CORRIDOR_SCRATCH.get(n)
    if sc is None:
        sc = _CORRIDOR_SCRATCH[n] = {
            "rgb16": np.empty(n, np.int16),   # 每像素 RGB 最大 |Δ|
            "t16": np.empty(n, np.int16),     # 逐通道 |Δ| / 边缘乘积临时
            "u8": np.empty(n, np.uint8),      # α 回绕差 / semi 构造临时
            "semi": np.empty(n, bool),        # 半透明掩码（原地翻转复用）
            "prod": np.empty(n, np.uint16),   # α·max|ΔRGB|
        }
    return sc


def _decode_rgba(path: Path):
    """PNG → (h, w, 4) uint8（非 RGBA 模式统一 convert，与 hash_frame 同口径）。

    解码失败抛 ValueError（CLI 归入 errors 列表而非崩栈）。
    """
    import numpy as np
    from PIL import Image
    try:
        with Image.open(path) as im:
            im.load()
            if im.mode != "RGBA":
                im = im.convert("RGBA")
            return np.array(im, dtype=np.uint8)   # 显式拷贝：脱离 PIL 图像生命周期
    except Exception as e:
        raise ValueError(f"PNG 解码失败: {path}: {e}") from e


def _rdiv255(q):
    """round(q / 255)（半向上；纯整数向量化，q 为非负 int 数组，无浮点）。"""
    return (2 * q + 255) // 510


def _dense_pass_corridor(ref, cand):
    """全画幅通过态快速走廊（#104 性能收口）：三条件全真时直接裁决 Tier-2。

    免差异收集（uint32 xor + take）与子集预乘重投影。布局与数值口径注意：
      - numpy 对 (N,3)/(N,4) 末轴 3/4 元素的运算/归约是标量慢循环（1080p 实测
        ~12/33ms），全部改为 (N,) 平面运算 + 链式列 maximum（~0.1-0.7ms/步）；
      - |Δ| 必须经 int16：uint8 回绕 min(d, −d) 在 |Δ|∈[129,255] 丢符号
        （如 0↔129 回绕得 127），会低报 mad/prod 致 ③ 误放行（对抗验证拦截）；
      - 中间缓冲走 _CORRIDOR_SCRATCH 复用（见其注释），稳态零分配。
    三条件均可全画幅一次验证：
      ① α 通道全等（⟹ alpha_identical 真，且白底残差≡黑底残差为数学恒等，
         一般路径同此省略白底补算）；
      ② 不透明/全透明区零 RGB 差异（⟹ n_opq==n_tr==0，差异像素全落半透明区，
         且 n_semi==n_diff）；
      ③ max(每像素 RGB 最大直存差 × α) ≤ _QUICK_PROD_BOUND（数学充分条件：
         半透明差异像素预乘重投影残差全 ≤1）。
    三条件成立 ⟹ Tier-2 判据全真，且输出与一般路径 n_exact==0 分支逐项一致
    （premul_rounding_pixel_count=n_semi / max_premul_residual=1 保守上界口径 /
    labels 恒空 / halo=0）。任一条件不满足返回 None，交一般路径（语义不变）。
    需热力图时不走本走廊（热力图要差异索引与逐点误差，一般路径本就计算）。
    """
    import numpy as np
    n = ref.shape[0] * ref.shape[1]
    sc = _corridor_scratch(np, n)
    rgb16, t16, u8, semi, prod = (sc[k] for k in ("rgb16", "t16", "u8", "semi", "prod"))
    rf, cf = ref.reshape(-1, 4), cand.reshape(-1, 4)   # 零拷贝平面化视图
    np.subtract(rf[:, 3], cf[:, 3], out=u8)            # uint8 回绕：非零 ⟺ α 有差
    if u8.any():                                       # ① α 有差 → 必 Tier-3
        return None
    for k in (0, 1, 2):                                # 每像素 RGB 最大直存差
        np.subtract(rf[:, k], cf[:, k], dtype=np.int16, out=t16)
        np.abs(t16, out=t16)
        if k:
            np.maximum(rgb16, t16, out=rgb16)
        else:
            rgb16[...] = t16
    mad_rgb = int(rgb16.max())
    if mad_rgb == 0:                                   # 防御（array_equal 已拦截）
        return None
    a_r = rf[:, 3]
    np.subtract(a_r, np.uint8(1), out=u8)
    np.less(u8, np.uint8(254), out=semi)               # 半透明 0<A<255（回绕单比较）
    su8 = semi.view(np.uint8)
    np.bitwise_xor(su8, np.uint8(1), out=su8)          # 翻转为非半透明指示（原地）
    np.multiply(rgb16, su8, dtype=np.int16, out=t16)
    if int(t16.max()):                                 # ② α∈{0,255} 区有 RGB 差
        return None
    np.multiply(rgb16.view(np.uint16), a_r, dtype=np.uint16, out=prod)  # ≤255·254
    if int(prod.max()) > _QUICK_PROD_BOUND:            # ③ 残差 ≤1 充分条件全域成立
        return None
    n_diff = int(np.count_nonzero(rgb16))              # ② ⟹ 差异像素全半透明，
    h, w = ref.shape[:2]                               # 故 n_semi == n_diff
    return {"tier": TIER_PREMUL, "labels": [], "w": w, "h": h,
            "max_abs_diff": mad_rgb, "max_abs_diff_rgb": mad_rgb,
            "max_abs_diff_alpha": 0, "n_diff_pixels": n_diff,
            "semi_diff_pixels": n_diff, "opaque_diff_pixels": 0,
            "transparent_diff_pixels": 0,
            "premul_rounding_pixel_count": n_diff,
            "max_premul_residual": 1,
            "halo_signature_pixels": 0, "heatmap": None}


def _analyze_arrays(ref, cand, heatmap_out=None) -> dict:
    """核心裁决（numpy 向量化；ref/cand 为同形 (h,w,4) uint8 Straight RGBA）。

    性能设计（#104 预算 ≤15ms 为分析段中位，五路径性能门见
    tests/test_gfx_golden.py::AnalyzePerf；PNG 解码 ~35-55ms 属 I/O 不在预算内，
    见 analyze_frame_diff 的 decode_ms 字段）：
    Tier-1 走 np.array_equal SIMD 快路径；Tier-2 通过态先试 _dense_pass_corridor
    全画幅走廊（三条件可证 Tier-2 即免差异收集、免重投影——覆盖低 α 舍入渐变、
    全屏中 α=128·ΔRGB=2 逆预乘舍入推广等稠密与稀疏场景），不适用（如需热力图、
    α 有差或 α·ΔRGB>256 混合形态）走 uint32 视图 xor + take 收集；
    半透明差异像素先过 α·max|ΔRGB| ≤ 256 数学上界快速通道（免精确
    重投影，见 _QUICK_PROD_BOUND 证明），其余走 uint16 移位除法精确重投影
    （除法→移位恒等式 (q+127)//255 = ((q+128+((q+128)>>8))>>8)，全域验证）。
    实测 1080p 参考值（本机 numpy 2.5）：全等 ~0.4ms / 走廊通过态（稀疏差异、
    30%/100% 稠密差与全屏中 α）~5-7ms。超预算包络如实披露：
    α·ΔRGB > 256 的稠密 Tier-2（罕见构造，需全量精确重投影）与全帧 Tier-3
    失败帧同量级，最重 ~300ms（失败帧离线诊断口径，非 #104 预算路径）。
    返回 dict 字段契约见 analyze_frame_diff 文档。
    """
    import numpy as np
    t0 = time.perf_counter()
    h, w = ref.shape[:2]
    ref = np.ascontiguousarray(ref)
    cand = np.ascontiguousarray(cand)

    if np.array_equal(ref, cand):                      # Tier-1 快路径（SIMD 级比较）
        return {"tier": TIER_EXACT, "labels": [], "w": w, "h": h,
                "max_abs_diff": 0, "max_abs_diff_rgb": 0, "max_abs_diff_alpha": 0,
                "n_diff_pixels": 0, "semi_diff_pixels": 0, "opaque_diff_pixels": 0,
                "transparent_diff_pixels": 0, "premul_rounding_pixel_count": 0,
                "max_premul_residual": 0, "halo_signature_pixels": 0,
                "heatmap": None,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)}

    if heatmap_out is None:                            # 热力图需差异索引 → 走一般路径
        cor = _dense_pass_corridor(ref, cand)
        if cor is not None:
            cor["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            return cor

    # 差异像素稀疏化：uint32 视图 xor（4 字节/像素）+ take 收集（比逐行 fancy
    # index 快一个量级）；视图不合法（对齐/步长异常）回落逐通道路径。
    try:
        x = np.bitwise_xor(ref.view(np.uint32), cand.view(np.uint32))
        idx = np.flatnonzero(x.ravel())
        rp8 = (ref.view(np.uint32).ravel().take(idx)
               .view(np.uint8).reshape(-1, 4))         # (n,4) uint8 免 int32 膨胀
        cp8 = (cand.view(np.uint32).ravel().take(idx)
               .view(np.uint8).reshape(-1, 4))
    except ValueError:
        idx = np.flatnonzero(np.bitwise_xor(ref, cand).any(axis=2).ravel())
        rp8 = ref.reshape(-1, 4)[idx]
        cp8 = cand.reshape(-1, 4)[idx]
    n_diff = int(idx.size)
    dpx = rp8.astype(np.int16)
    dpx -= cp8
    np.abs(dpx, out=dpx)                              # (n,4) 差异像素 |Δ|
    a_r, a_c = rp8[:, 3], cp8[:, 3]
    cmax = [int(dpx[:, k].max()) for k in range(4)]   # 列向归约；末轴 3/4 元素的行向
    mad = max(cmax)                                   # 归约是 numpy 标量慢循环，
    mad_rgb, mad_a = max(cmax[:3]), cmax[3]           # 大差异集上需避开（走廊同因）
    alpha_identical = mad_a == 0

    # 差异像素按 ref α 分区（α 全等时 ref/cand 分区同义；α 有差时 alpha 标签先兜底）
    # semi 单比较合成：(a_r−1) 在 uint8 回绕下恰为 [1,254] → 0..253，0/255 → 254/255
    semi = (a_r - np.uint8(1)) < np.uint8(254)
    opaque = a_r == 255
    n_semi, n_opq, n_tr = (int(np.count_nonzero(v)) for v in
                           (semi, opaque, a_r == 0))

    # 半透明差异像素：双方 Straight RGB 重投射预乘合成空间
    #   黑底 B=0：C_comp = round(C·A/255)；白底 B=255：C_comp = round(C·A/255)+(255−A)
    #   （round(x+k)=round(x)+k 对整数 k 成立 → α 相等的像素白底残差 ≡ 黑底残差，
    #   故白底只在 α 不同处补算——数学等价，省一半残差链）
    # 快速通道（数学上界）：α 相等且 α·max|ΔRGB| ≤ _QUICK_PROD_BOUND(256) 时双方
    # 黑底/白底重投影残差 ≤1 恒成立（证明与全域穷举见 _QUICK_PROD_BOUND 注释）→
    # 免精确重投影。低 α 大面积逆预乘舍入（跨后端 Tier-2 稠密场景主力）与全屏中 α
    # （如 α=128、直存差恰 2 级：128·2=256）全部命中此通道；其真实残差 ≤1 但未必
    # 为 0，max_premul_residual 按保守上界 1 计（Tier-2 语义不受影响）。
    # （乘积用 uint16：255·255=65025 < 65536 恰不溢出，比 int32 省一半带宽）
    alpha_eq = a_r == a_c
    if n_semi:
        t = np.maximum(dpx[:, 0], dpx[:, 1])          # 每像素 RGB 最大直存差 ≤255
        np.maximum(t, dpx[:, 2], out=t)               # （链式列运算，行向归约慢）
        drgb8 = t.astype(np.uint8)
        quick = np.multiply(drgb8, a_r, dtype=np.uint16) <= _QUICK_PROD_BOUND
        quick &= semi
        quick &= alpha_eq
        exact = semi & ~quick                         # 新数组：勿原地改 semi
    else:
        exact = semi                                  # 空集（无半透明差异像素）
    n_exact = int(np.count_nonzero(exact))
    res_full = None                                   # 对齐 idx 的每像素最大残差
    halo_px = 0
    premul_ok = 0
    if n_exact:
        rgbs, cgbs = rp8[exact][:, :3], cp8[exact][:, :3]   # (m,3) uint8
        ar = a_r[exact]                                 # uint8（u16 乘法直用，省转型）
        ac2 = a_c[exact]

        def _premul(rgb8, a):                         # round(C·A/255)，全 uint16 SIMD
            # 除法→移位恒等式（全域 q∈[0,65025] 验证）：
            #   (q+127)//255 == ((q+128+((q+128)>>8))>>8)，q+128≤65153 无 u16 溢出
            q = np.multiply(rgb8, a[:, None], dtype=np.uint16)  # ≤65025，转型×乘法融合
            q += np.uint16(128)
            q += q >> np.uint16(8)
            q >>= np.uint16(8)
            return q

        cb_ref = _premul(rgbs, ar)                    # round(C_ref·A_ref/255)
        cb_cnd = _premul(cgbs, ac2)
        dev = np.subtract(cb_cnd, cb_ref, dtype=np.int16)     # 转型×减法单趟融合
        np.abs(dev, out=dev)
        res = dev.max(axis=1).astype(np.int32)        # 黑底残差
        neq = ar != ac2
        if neq.any():                                 # α 不同处补白底残差
            wr = cb_ref[neq].astype(np.int16) + (255 - ar[neq])[:, None]
            wc = cb_cnd[neq].astype(np.int16) + (255 - ac2[neq])[:, None]
            res[neq] = np.maximum(res[neq], np.abs(wc - wr).max(axis=1))
        res_full = np.zeros(n_diff, dtype=np.int32)
        res_full[exact] = res
        bad = res > 1
        if bad.any():   # 二次预乘签名：C_cand ≈ round(C_ref·A_ref/255)（±1/通道）
            sig = np.abs(cgbs[bad].astype(np.int16)
                         - cb_ref[bad].astype(np.int16)).max(axis=1) <= 1
            halo_px = int(np.count_nonzero(sig))
        max_premul_res = int(res_full.max())
        if max_premul_res < 1 and quick.any():
            max_premul_res = 1                        # 快速通道像素按上界 1 计
        premul_ok = int(np.count_nonzero((res_full <= 1) & semi & alpha_eq))
    else:
        max_premul_res = 1 if n_semi else 0
        premul_ok = n_semi                            # semi 全部命中快速通道（α 等价）

    # ---- 三级裁决（labels 仅 Tier-3 填充；Tier-2 是通过态，labels 恒空）
    tier2 = (alpha_identical and n_opq == 0 and n_tr == 0 and max_premul_res <= 1)
    tier = TIER_PREMUL if tier2 else TIER_FAIL
    labels = []
    if tier == TIER_FAIL:
        if not alpha_identical:
            labels.append(L_ALPHA)
        if n_semi and halo_px and halo_px == int((res_full[semi] > 1).sum()):
            labels.append(L_HALO)   # 残差>1 的半透明差异像素全部命中二次预乘签名
        if int((opaque & (a_c == 255)).sum()):   # 双方均不透明的差异像素必有 RGB 差 ≥1
            labels.append(L_OPAQUE)
        if mad > _STRUCT_MAX_ABS:
            labels.append(L_STRUCT)
        if not labels:
            labels.append(L_UNCLASS)

    out = {"tier": tier, "labels": labels, "w": w, "h": h,
           "max_abs_diff": mad, "max_abs_diff_rgb": mad_rgb,
           "max_abs_diff_alpha": mad_a, "n_diff_pixels": n_diff,
           "semi_diff_pixels": n_semi, "opaque_diff_pixels": n_opq,
           "transparent_diff_pixels": n_tr,
           "premul_rounding_pixel_count": premul_ok,
           "max_premul_residual": max_premul_res,
           "halo_signature_pixels": halo_px, "heatmap": None}
    if heatmap_out is not None and tier != TIER_EXACT:
        if res_full is None:                          # 全部命中快速通道：semi 即绿点
            t2point = semi & alpha_eq
        else:
            t2point = semi & alpha_eq & (res_full <= 1)   # Tier-2 逆预乘舍入点
        _write_heatmap(ref, idx, dpx, t2point, Path(heatmap_out))
        out["heatmap"] = str(heatmap_out)
    out["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return out


def _write_heatmap(ref, idx, dpx, t2point, path: Path):
    """误差热力图（RGB PNG，宽 2W，高 H；tmp+os.replace 原子写，同 golden 落盘纪律）。

    左半幅：参照帧按 C_comp 公式直投灰底 B=128（半透明区显灰、全透明区显底色，
    α 结构一眼可辨）；右半幅：误差放大 16× 伪彩——绿=Tier-2 逆预乘舍入点，
    红=Tier-3 实质错误像素，亮度 ∝ min(255, 16·该像素 max|Δ|)。
    """
    import numpy as np
    from PIL import Image
    h, w = ref.shape[:2]
    canvas = np.zeros((h, 2 * w, 3), dtype=np.uint8)
    a = ref[:, :, 3:4].astype(np.int32)
    numer = ref[:, :, :3].astype(np.int32) * a + _HEATMAP_GRAY_BG * (255 - a)
    canvas[:, :w] = _rdiv255(numer).astype(np.uint8)
    amp = np.minimum(255, _HEATMAP_AMP * dpx.max(axis=1)).astype(np.uint8)  # (n,)
    flat = canvas.reshape(-1, 3)
    ridx = idx + w * (idx // w + 1)     # 左帧线性位置 → 右半幅对应线性位置
    flat[ridx[~t2point], 0] = amp[~t2point]
    flat[ridx[t2point], 1] = amp[t2point]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    Image.fromarray(canvas, "RGB").save(str(tmp), format="PNG", compress_level=3)
    os.replace(tmp, path)


def analyze_frame_diff(ref_path, cand_path, heatmap_out=None) -> dict:
    """三级像素比对裁决（issue #104；跨后端 Chrome CDP / CEF / Electron 审计底座）。

    对一对已落盘 PNG（解码口径与 hash_frame 一致：统一 RGBA 8-bit Straight）：

      Tier-1 verified_exact：全画幅 max_abs_diff == 0。
      Tier-2 premul_equivalent：不满足 Tier-1 时，α 通道全等（max|ΔA|==0）、差异
          像素全部位于半透明区（0<A<255，等价于 A=0/A=255 区零差异），且双方
          Straight RGB 重投射回黑底与白底预乘合成空间
          C_comp(C,A,B)=round((C·A+B·(255−A))/255) 后两种底色下
          max|ΔC_comp| ≤ 1 → 判「逆预乘整数舍入等价」（通过）。
      Tier-3 visual_failure：其余一律失败；labels 可多标签共存（按 issue 顺序）：
          alpha_channel_diff / premul_double_blend_halo（半透明区 Δ_premul>1 且
          这些像素全部满足 C_cand ≈ round(C_ref·A_ref/255)，±1/通道——把已预乘
          BGRA 当非预乘直接存 PNG 的发黑签名）/ opaque_color_shift（双方 A=255
          差异像素，RGB 差 ≥1）/ structural_or_missing_layer（max|Δ|>5）。四标签
          全未命中时补 unclassified_diff 兜底，保证失败帧 labels 恒非空。

    heatmap_out 给定且落入 Tier-2/Tier-3 时原子写热力图（diff_f{:04d}.png 由 CLI
    命名）并回填 heatmap 字段；Tier-1 恒不产热力图。

    返回 dict（JSON 可序列化）：tier / labels / w / h / max_abs_diff(_rgb,_alpha) /
    n_diff_pixels / semi|opaque|transparent_diff_pixels（差异像素按 ref α 分区）/
    premul_rounding_pixel_count / max_premul_residual（半透明差异像素的黑底+白底
    重投影最大残差；Tier-2 定义上 ≤1）/ halo_signature_pixels / heatmap /
    elapsed_ms（分析段，含热力图生成）/ decode_ms（PNG 解码段；1080p 实测
    ~35-55ms 属 I/O，issue #104 的 15ms 预算只约束分析段）。

    尺寸不一致或解码失败抛 ValueError（CLI 归入 errors，不崩栈）。
    """
    t0 = time.perf_counter()
    ref = _decode_rgba(Path(ref_path))
    cand = _decode_rgba(Path(cand_path))
    t1 = time.perf_counter()
    if ref.shape != cand.shape:
        raise ValueError(f"帧尺寸不一致: ref {ref.shape[1]}x{ref.shape[0]} vs "
                         f"cand {cand.shape[1]}x{cand.shape[0]}（{cand_path}）")
    out = _analyze_arrays(ref, cand, heatmap_out=heatmap_out)
    out["decode_ms"] = round((t1 - t0) * 1000, 2)
    return out

def load_env_manifest(path_str: str):
    """env manifest JSON 读取（issue #102 门禁入参）。空参 → (None, None)；
    文件缺失/损坏 JSON/内容为 null → (None, 错误句)——门禁入参宁拒收不静默。"""
    if not path_str:
        return None, None
    p = Path(path_str)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"env manifest 读取失败: {p}（{e}）"
    if not isinstance(data, dict):
        return None, f"env manifest 不是 JSON 对象: {p}（得到 {type(data).__name__}）"
    return data, None


def env_manifest_gate(meta: dict, path_str: str) -> dict:
    """跨后端环境前置门禁（issue #102）。返回 state 机：

      error → 入参不可用（manifest 读不到 / 提供了当前 manifest 但 golden 未
              嵌入基线 / golden 内嵌基线非 JSON 对象）→ compare exit 1，
              绝不比错环境下的像素；
      fail  → verify_manifest_compatibility 不兼容项非空 → exit 1 列全项；
      pass  → 环境一致（Chromium 主版本/生效后端/色彩空间/视口/字体 SHA256）；
      warn  → golden 嵌有 manifest 但本次未提供（同机常规回环不阻断，显式警告
              门禁未激活）；off → 双方均无 manifest（存量 golden，行为同旧版）。
    """
    cur, err = load_env_manifest(path_str)
    if err:
        return {"state": "error", "error": err, "items": []}
    if cur is None:
        if meta.get("env_manifest"):
            return {"state": "warn", "error": None, "items": []}
        return {"state": "off", "error": None, "items": []}
    ref = meta.get("env_manifest")
    if ref is not None and not isinstance(ref, dict):
        return {"state": "error",
                "error": f"golden.meta.env_manifest 不是 JSON 对象（得到 "
                         f"{type(ref).__name__}）——golden 损坏或被手改，环境门禁"
                         "基线不可信，拒绝像素比对",
                "items": []}
    if not ref:
        return {"state": "error",
                "error": "golden 未嵌入环境 manifest（build 时未提供 --env-manifest），"
                         "无法做跨后端环境门禁；请以 --env-manifest 重建 golden",
                "items": []}
    items = env_manifest.verify_manifest_compatibility(ref, cur)
    state = "fail" if items else "pass"
    return {"state": state, "error": None, "items": items}


# ---------------------------------------------------------------- build

def cmd_build(args) -> int:
    c = make_ctx(Path(args.timeline))
    clips = gfx_clips(c)
    if not clips:
        print("✗ timeline 中没有 gfx 轨 clip（video_tracks kind==gfx），无需 golden")
        return 1
    full_names = _split_names(args.full_pages)
    sample = max(1, args.sample)

    # ---- 环境指纹（issue #102）：嵌入 golden 供 compare 跨后端门禁（可空）
    env_m, env_err = load_env_manifest(args.env_manifest)
    if env_err:
        print(f"✗ {env_err}")
        return 1

    # ---- 名单防线（B2）：--full-pages 逐项必须至少命中一个 gfx 页，防拼错静默降级抽检
    unmatched = [n for n in full_names if not any(_match(n, src) for src, _ in clips)]
    if unmatched:
        print(f"✗ --full-pages 名单逐项零匹配: {unmatched}"
              f"（timeline 共 {len(clips)} 个 gfx 实例，检查拼写/是否已渲）")
        return 1

    # ---- 定位 + 完整性预检：任一目录/帧缺失 → 列全问题后退出非零（golden 必须完整）
    errors, plan = [], []
    for src, dur in clips:
        html = c.root / src
        if not html.exists():
            errors.append(f"html 缺失: {src}")
            continue
        key = hygiene.gfx_key(c, {"src": src, "dur": dur})
        d = c.cache / "gfx" / key
        if not d.is_dir():
            errors.append(f"帧目录缺失: {src} dur={dur} → cache/gfx/{key}（先完成 gfx 渲染）")
            continue
        mode = "full" if any(_match(n, src) for n in full_names) else "sample"
        frames = list(range(dur)) if mode == "full" else sample_frames(dur, sample)
        missing = [i for i in frames if not (d / f"f{i:04d}.png").is_file()]
        if missing:
            errors.append(f"帧缺失: {src} dur={dur} 缺 {len(missing)} 帧"
                          f"（如 f{missing[0]:04d}.png；mode={mode}）")
            continue
        plan.append((src, dur, key, mode, frames))
    if errors:
        print(f"✗ build 预检失败 {len(errors)} 项（golden 必须完整，不落半份）：")
        for e in errors:
            print(f"  - {e}")
        return 1

    # ---- 并行像素 hash
    tasks = []
    for src, dur, key, mode, frames in plan:
        d = c.cache / "gfx" / key
        for i in frames:
            tasks.append(((src, dur, i), d / f"f{i:04d}.png"))
    t0 = time.time()
    hashes, fails = hash_frames(tasks, "hash")
    if fails:
        print(f"✗ {len(fails)} 帧解码失败：")
        for label, p, e in fails[:10]:
            print(f"  - {label[0]} dur={label[1]} f{label[2]:04d}: {e}")
        return 1

    pages = {}
    for src, dur, key, mode, frames in plan:
        inst = {"dur": dur, "key": key, "mode": mode,
                "frames": {f"{i:04d}": hashes[(src, dur, i)] for i in frames}}
        pages.setdefault(src, {"durs": {}})["durs"][str(dur)] = inst
    golden = {
        "meta": {
            "tool": "gfx_golden",
            "built": time.strftime("%F %T"),
            "chrome": chrome_version(),
            "fps": c.fps,
            "viewport": f"{c.w}x{c.h}",
            "hash": HASH_DESC,
            "frame_naming": "f{:04d}.png（帧号从 0）",
            "key_algo": "cache/gfx/<键> 键 = vtx/hygiene.gfx_key = "
                        "sha1(html字节+显式依赖+fps+视口+时长)[:12]",
            "sample": sample,
            "full_pages": full_names,
            "pages": len(pages),
            "instances": len(plan),
            "frames_hashed": len(hashes),
            "elapsed_s": round(time.time() - t0, 1),
            "env_manifest": env_m,   # None=构建时未提供（compare 门禁据此显形区分）
        },
        "pages": pages,
    }
    out = Path(args.golden)
    # 落盘原子化（B4）：同目录 tmp + os.replace，杜绝半份 golden 被下次 compare 读到
    tmp = out.parent / (out.name + ".tmp")
    tmp.write_text(json.dumps(golden, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, out)
    n_full = sum(1 for *_, m, _f in plan if m == "full")
    print(f"✓ golden 已写入 {out}：{len(pages)} 页 / {len(plan)} 个实例"
          f"（full {n_full}），{len(hashes)} 帧，{time.time() - t0:.0f}s")
    return 0


# ---------------------------------------------------------------- compare

def cmd_compare(args) -> int:
    c = make_ctx(Path(args.timeline))
    gp = Path(args.golden)
    if not gp.exists():
        print(f"✗ golden 不存在: {gp}（先 build）")
        return 1
    g = json.loads(gp.read_text(encoding="utf-8"))
    meta = g.get("meta", {})
    # ---- 跨后端环境前置门禁（issue #102）：先证环境一致，再比像素。
    # 任一不兼容（字体 SHA256/GPU 静默回退/Chromium 主版本/色彩空间/视口）即拒比。
    gate = env_manifest_gate(meta, args.env_manifest)
    if gate["state"] == "error":
        print(f"✗ {gate['error']}")
        return 1
    if gate["state"] == "fail":
        for it in gate["items"]:
            print(f"  ✗ 环境门禁 {it}")
        print(f"✗ 跨后端环境前置门禁未通过：{len(gate['items'])} 项不兼容，"
              "拒绝像素比对（vtx/env_manifest，issue #102）")
        return 1
    if gate["state"] == "pass":
        print("✓ 环境前置门禁通过：Chromium 主版本/生效后端/色彩空间/视口/"
              "字体 SHA256 一致（vtx/env_manifest）")
    elif gate["state"] == "warn":
        print("⚠ golden 含环境 manifest 但未提供 --env-manifest：跨后端环境"
              "一致性门禁未激活（跨后端比对务必提供）")
    # 基线同一性：fps/视口不一致说明 golden 不是本项目的（缓存键也必然对不上）
    if (str(meta.get("fps")) != str(c.fps)
            or str(meta.get("viewport")) != f"{c.w}x{c.h}"):
        print(f"✗ golden 与当前 timeline 项目参数不符：golden fps={meta.get('fps')} "
              f"viewport={meta.get('viewport')}，当前 fps={c.fps} {c.w}x{c.h}")
        return 1

    names = _split_names(args.pages)
    # 名单防线（B2）：--pages 逐项必须至少命中一个 golden 页，防拼错时「其余名单」
    # 静默通过让人误以为全量比对
    unmatched = [n for n in names if not any(_match(n, src) for src in g.get("pages", {}))]
    if unmatched:
        print(f"✗ --pages 名单在 golden 中逐项零匹配: {unmatched}"
              f"（golden 共 {len(g.get('pages', {}))} 页）")
        return 1
    sel = {src: v for src, v in g.get("pages", {}).items()
           if not names or any(_match(n, src) for n in names)}
    if not sel:
        print(f"✗ --pages 名单在 golden 中无匹配页（golden 共 "
              f"{len(g.get('pages', {}))} 页）")
        return 1

    # ---- 交叉核对 timeline 页集（B3）：timeline 有而 golden 未记录的 gfx 页 → 警告列出
    unrecorded = []
    for src, dur in gfx_clips(c):
        pv = g.get("pages", {}).get(src)
        if pv is None or str(dur) not in pv.get("durs", {}):
            unrecorded.append({"src": src, "dur": dur})
    if unrecorded:
        print(f"  ⚠ timeline 中有 {len(unrecorded)} 个 gfx 实例未记录在 golden（不在本次比对"
              f"范围，需要覆盖请重建 golden）：")
        for u in unrecorded[:20]:
            print(f"    - {u['src']} dur={u['dur']}")
        if len(unrecorded) > 20:
            print(f"    ... 另有 {len(unrecorded) - 20} 个")

    # ---- 收集待比任务（抽检页只比 golden 已记录帧；full 页=全帧）
    tasks, dir_missing, unreadable = [], [], []
    for src, pv in sel.items():
        for durs, inst in pv.get("durs", {}).items():
            dur = int(inst["dur"])
            d = c.cache / "gfx" / hygiene.gfx_key(c, {"src": src, "dur": dur})
            if not d.is_dir():
                dir_missing.append({"src": src, "dur": dur,
                                    "key": hygiene.gfx_key(c, {"src": src, "dur": dur})})
                continue
            for fkey in inst.get("frames", {}):
                p = d / f"f{int(fkey):04d}.png"
                if not p.is_file():
                    unreadable.append({"src": src, "dur": dur, "frame": fkey,
                                       "why": "帧文件缺失"})
                else:
                    tasks.append(((src, dur, fkey), p))

    cur, fails = hash_frames(tasks, "比对")
    for label, _p, e in fails:
        unreadable.append({"src": label[0], "dur": label[1], "frame": label[2],
                           "why": f"解码失败: {e}"})

    # ---- 新鲜度防线（B1）：被比帧 mtime 全部 ≤ golden 构建时间 → 缓存自 golden 构建
    # 后未重渲，比对恒等、无意义。默认显式警告不阻断；--strict 判失败（验收口径）。
    # 容差 2s：meta.built 字符串秒级截断，帧可能写在同一秒内但 mtime 略晚。
    stale = False
    try:
        built = time.mktime(time.strptime(meta.get("built", ""), "%Y-%m-%d %H:%M:%S"))
        newest = max(p.stat().st_mtime for _l, p in tasks)
        stale = bool(tasks) and newest <= built + 2
    except (ValueError, TypeError, OSError):
        stale = False    # built 缺失/不可解析或 stat 失败：无法判定，不拦截

    # ---- 逐实例比对
    identical, differ = [], []
    for src, pv in sel.items():
        for _durs, inst in sorted(pv.get("durs", {}).items()):
            dur = int(inst["dur"])
            if any(m["src"] == src and m["dur"] == dur for m in dir_missing):
                continue
            diffs = [(fk, inst["frames"][fk], cur[(src, dur, fk)])
                     for fk in sorted(inst.get("frames", {}), key=int)
                     if (src, dur, fk) in cur and cur[(src, dur, fk)] != inst["frames"][fk]]
            if diffs:
                differ.append({"src": src, "dur": dur, "mode": inst.get("mode"),
                               "frame": diffs[0][0], "golden": diffs[0][1],
                               "current": diffs[0][2], "n_diff_frames": len(diffs)})
            else:
                identical.append({"src": src, "dur": dur, "mode": inst.get("mode"),
                                  "n_frames": len(inst.get("frames", {}))})

    ok = not (differ or dir_missing or unreadable)
    n_full = sum(1 for x in identical if x.get("mode") == "full")
    n_cmp = sum(x["n_frames"] for x in identical) + sum(x["n_diff_frames"] for x in differ)
    print(f"== gfx golden 比对（{len(sel)} 页，{n_cmp} 帧，golden 构建于 "
          f"{meta.get('built', '?')}）==")
    for x in identical:
        print(f"  ✓ 全零差异  {x['src']} dur={x['dur']}（{x['mode']}，{x['n_frames']} 帧）")
    for x in differ:
        print(f"  ✗ 像素差异  {x['src']} dur={x['dur']} 首个差异帧 f{x['frame']}.png "
              f"golden={x['golden'][:12]} current={x['current'][:12]}"
              f"（共 {x['n_diff_frames']} 帧不同）")
    for m in dir_missing:
        print(f"  ✗ 目录缺失  {m['src']} dur={m['dur']} → cache/gfx/{m['key']}"
              f"（内容/键已变？重建 golden 或先渲染）")
    for u in unreadable:
        print(f"  ✗ 帧缺失/损坏  {u['src']} dur={u['dur']} f{u['frame']}.png（{u['why']}）")
    if stale and args.strict:
        ok = False    # 新鲜度防线在 --strict 下判失败（验收口径）
    if ok and stale:
        print("⚠ 新鲜度防线：被比帧 mtime 全部 ≤ golden 构建时间——缓存未重渲，比对无意义"
              "（仅警告，验收请加 --strict）")
    if ok:
        print(f"✓ 通过：{len(identical)} 个实例全零差异")
    else:
        print(f"✗ 未通过：差异 {len(differ)} / 目录缺失 {len(dir_missing)} / "
              f"帧缺失损坏 {len(unreadable)}"
              + ("；缓存未重渲（--strict）" if stale else ""))
    if args.report:
        report = {"ok": ok, "stale": stale, "golden": str(gp), "timeline": str(Path(args.timeline)),
                  "compared_frames": n_cmp, "identical": identical, "differ": differ,
                  "dir_missing": dir_missing, "missing_or_unreadable": unreadable,
                  "unrecorded": unrecorded, "env_gate": gate}
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
        print(f"  报告已写入 {args.report}")
    return 0 if ok else 1


# ---------------------------------------------------------------- analyze（#104 深度像素审计）

def _frame_files(d: Path):
    """目录内 f%04d.png 帧名集合（严格 f+4 位数字，防 f12a3.png/f0000x.png 混入）。"""
    return {p.name for p in d.iterdir() if p.is_file() and len(p.name) == 9
            and p.name[0] == "f" and p.name[1:5].isdigit() and p.name[5:] == ".png"}


def _frame_no(stem: str) -> int:
    """帧号：f0007 → 7；非帧命名 stem → 0（单文件模式热力图记 diff_f0000.png）。"""
    return int(stem[1:]) if len(stem) == 5 and stem[0] == "f" and stem[1:].isdigit() else 0


def cmd_analyze(args) -> int:
    ref, cand = Path(args.ref), Path(args.cand)
    for p, side in ((ref, "--ref"), (cand, "--cand")):
        if not p.exists():
            print(f"✗ {side} 路径不存在: {p}")
            return 1
    if ref.is_dir() != cand.is_dir():
        print(f"✗ --ref 与 --cand 必须同为 PNG 文件或同为 f%04d.png 帧目录"
              f"（ref={'目录' if ref.is_dir() else '文件'}，"
              f"cand={'目录' if cand.is_dir() else '文件'}）")
        return 1
    hm_dir = Path(args.heatmap_dir) if args.heatmap_dir else None

    if ref.is_file():
        pairs = [(_frame_no(ref.stem), ref, cand)]
        errors = []
    else:
        rn, cn = _frame_files(ref), _frame_files(cand)
        pairs = [(int(n[1:5]), ref / n, cand / n) for n in sorted(rn & cn)]
        errors = [{"frame": n[1:5], "ref": str(ref / n), "cand": "",
                   "why": "cand 侧缺帧（不配对、不静默跳过）"} for n in sorted(rn - cn)]
        errors += [{"frame": n[1:5], "ref": "", "cand": str(cand / n),
                    "why": "ref 侧缺帧（不配对、不静默跳过）"} for n in sorted(cn - rn)]
        if not pairs and not errors:
            errors.append({"frame": "", "ref": str(ref), "cand": str(cand),
                           "why": "两目录无同名 f%04d.png 帧对"})

    frames = []
    for num, rp, cp in pairs:
        try:
            r = analyze_frame_diff(
                rp, cp, heatmap_out=(hm_dir / f"diff_f{num:04d}.png" if hm_dir else None))
            frames.append({"frame": f"{num:04d}", **r})
        except ValueError as e:
            errors.append({"frame": f"{num:04d}", "ref": str(rp), "cand": str(cp),
                           "why": str(e)})

    n_t1 = sum(1 for f in frames if f["tier"] == TIER_EXACT)
    n_t2 = sum(1 for f in frames if f["tier"] == TIER_PREMUL)
    n_t3 = sum(1 for f in frames if f["tier"] == TIER_FAIL)
    ok = not errors and n_t3 == 0 and bool(frames)

    print(f"== gfx 帧深度审计（ref={ref} cand={cand}，{len(frames)} 对帧）==")
    for f in frames:
        hm = f" → {f['heatmap']}" if f["heatmap"] else ""
        if f["tier"] == TIER_EXACT:
            print(f"  ✓ Tier-1 f{f['frame']} 全画幅零差异")
        elif f["tier"] == TIER_PREMUL:
            print(f"  ≈ Tier-2 f{f['frame']} 逆预乘舍入 "
                  f"{f['premul_rounding_pixel_count']} 点"
                  f"（残差 ≤{f['max_premul_residual']}）{hm}")
        else:
            print(f"  ✗ Tier-3 f{f['frame']} {f['labels']} "
                  f"max|Δ|={f['max_abs_diff']} 差异像素 {f['n_diff_pixels']}{hm}")
    for e in errors:
        print(f"  ✗ 帧错误  {e['frame']}: {e['why']}")
    print(("✓ 通过" if ok else "✗ 未通过") +
          f"：Tier-1 {n_t1} / Tier-2 {n_t2} / Tier-3 {n_t3} / 错误 {len(errors)}"
          + ("（Tier-1/Tier-2 均为通过）" if ok else "（任一 Tier-3 或错误即失败）"))
    if args.report:
        report = {"ok": ok, "ref": str(ref), "cand": str(cand),
                  "compared": len(frames),
                  "tiers": {TIER_EXACT: n_t1, TIER_PREMUL: n_t2, TIER_FAIL: n_t3},
                  "frames": frames, "errors": errors}
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
        print(f"  报告已写入 {args.report}")
    return 0 if ok else 1


# ------------------------------------------------ verify-canary（issue #105 帧同步水印校验）

def _verify_one_canary(path: Path, requested: int, mode: str) -> dict:
    """单帧 Canary 校验（PNG 解码 → vtx/frame_canary 帧级裁决）。

    返回 {"frame": "0013", "requested": 13, **裁决dict}；解码失败抛 ValueError
    （CLI 归入 errors，不崩栈）。"""
    arr = _decode_rgba(path)   # 与 analyze 同解码口径：统一 RGBA 8-bit
    if mode == fc.MODE_DIAGNOSTIC:
        r = fc.verify_diagnostic_corners(arr, requested)
    else:
        r = fc.verify_clean_pixel(arr[0][0], requested)
    return {"frame": f"{requested:04d}", "requested": requested, "path": str(path), **r}


def cmd_verify_canary(args) -> int:
    mode = args.mode
    if mode not in fc.MODES:
        print(f"✗ 未知 --mode: {mode!r}（合法: {list(fc.MODES)}）")
        return 1
    if args.frames:
        d = Path(args.frames)
        if not d.is_dir():
            print(f"✗ --frames 目录不存在: {d}")
            return 1
        names = _frame_files(d)
        if not names:
            print(f"✗ 目录内无 f%04d.png 帧文件: {d}")
            return 1
        jobs = [(int(n[1:5]), d / n) for n in sorted(names)]
    else:
        p = Path(args.png)
        if not p.is_file():
            print(f"✗ --png 文件不存在: {p}")
            return 1
        requested = args.frame
        if requested is None:
            stem = p.stem
            if len(stem) == 5 and stem[0] == "f" and stem[1:].isdigit():
                requested = int(stem[1:])
            else:
                print(f"✗ 文件名 {p.name} 非帧命名且未提供 --frame")
                return 1
        jobs = [(int(requested), p)]

    frames, errors = [], []
    for requested, p in jobs:
        try:
            frames.append(_verify_one_canary(p, requested, mode))
        except ValueError as e:
            errors.append({"frame": f"{requested:04d}", "path": str(p), "why": str(e)})

    n = len(frames)
    buckets = {s: 0 for s in (fc.ST_OK, fc.ST_LAG1, fc.ST_STATIC_FIRST, fc.ST_OTHER,
                              fc.ST_TORN, fc.ST_CHECKSUM, fc.ST_NO_CANARY)}
    for f in frames:
        buckets[f["status"]] += 1
    wrong_frame_count = buckets[fc.ST_LAG1] + buckets[fc.ST_STATIC_FIRST] + buckets[fc.ST_OTHER]
    ok = not errors and n > 0 and all(v == 0 for s, v in buckets.items() if s != fc.ST_OK)

    print(f"== gfx 帧同步水印校验（mode={mode}，{n} 帧"
          f"{'，目录 ' + str(args.frames) if args.frames else '，单帧 ' + str(args.png)}）==")
    for f in frames:
        if f["status"] == fc.ST_OK:
            print(f"  ✓ f{f['frame']} rendered==requested=={f['requested']}")
        else:
            extra = f" rendered={f.get('rendered_frame')}" if f.get("rendered_frame") is not None else ""
            extra += f" br={f.get('rendered_frame_br')}" if f.get("rendered_frame_br") is not None else ""
            print(f"  ✗ f{f['frame']} {f['status']}{extra} {f.get('detail', '')}".rstrip())
    for e in errors:
        print(f"  ✗ 帧错误  {e['frame']}: {e['why']}")
    print(("✓ 通过" if ok else "✗ 未通过") +
          f"：ok {buckets[fc.ST_OK]} / wrong_frame {wrong_frame_count}"
          f"（lag1 {buckets[fc.ST_LAG1]} / static_first {buckets[fc.ST_STATIC_FIRST]}"
          f" / other {buckets[fc.ST_OTHER]}）/ torn {buckets[fc.ST_TORN]}"
          f" / checksum {buckets[fc.ST_CHECKSUM]} / no_canary {buckets[fc.ST_NO_CANARY]}"
          f" / 错误 {len(errors)}")
    if args.report:
        report = {"ok": ok, "mode": mode, "frames": frames, "errors": errors,
                  "counters": {"total": n, "ok": buckets[fc.ST_OK],
                               "wrong_frame_count": wrong_frame_count,
                               "wrong_frame_lag1": buckets[fc.ST_LAG1],
                               "wrong_frame_static_first": buckets[fc.ST_STATIC_FIRST],
                               "wrong_frame_other": buckets[fc.ST_OTHER],
                               "torn_frame": buckets[fc.ST_TORN],
                               "checksum_mismatch": buckets[fc.ST_CHECKSUM],
                               "no_canary": buckets[fc.ST_NO_CANARY],
                               "errors": len(errors)}}
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
        print(f"  报告已写入 {args.report}")
    return 0 if ok else 1


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="gfx 帧 golden 像素基线构建/比对 + 三级像素深度审计"
                    "（Tier-1 Exact / Tier-2 Premul-Equivalent / Tier-3 Failure+热力图）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="从现有 cache/gfx 帧产物构建 golden 像素基线")
    b.add_argument("-t", "--timeline", required=True, help="timeline.json 路径")
    b.add_argument("--golden", required=True, help="输出 golden JSON 路径")
    b.add_argument("--full-pages", default="",
                   help="全帧 hash 页名单（逗号分隔，按 src 相对路径/stem/子串匹配）；"
                        "名单外页抽检")
    b.add_argument("--sample", type=int, default=5,
                   help="抽检页每 clip 帧数下限（默认 5；[0, dur//2, dur-1]+均匀补足）")
    b.add_argument("--env-manifest", default="",
                   help="环境指纹 manifest JSON（渲染运行产出，vtx/env_manifest.py 采集）；"
                        "嵌入 golden.meta 供 compare 跨后端前置门禁（issue #102，可空）")

    p = sub.add_parser("compare", help="对当前 cache 重算 hash 并与 golden 逐帧比对")
    p.add_argument("-t", "--timeline", required=True, help="timeline.json 路径")
    p.add_argument("--golden", required=True, help="golden JSON 路径")
    p.add_argument("--report", default="", help="比对结果 JSON 输出路径（可选）")
    p.add_argument("--pages", default="",
                   help="只比名单内页（逗号分隔，匹配规则同 --full-pages；缺省=golden 全部）")
    p.add_argument("--env-manifest", default="",
                   help="当前后端实测 env manifest JSON：与 golden 内嵌基线先过环境一致性"
                        "门禁（任一不兼容拒绝像素比对，issue #102）")
    p.add_argument("--strict", action="store_true",
                   help="新鲜度防线判失败：被比帧 mtime 全部 ≤ golden 构建时间（缓存未重渲）"
                        "时 exit 1（默认仅显式警告）")

    a = sub.add_parser("analyze",
                       help="深度像素审计：三级裁决 Tier-1/2/3 + 误差热力图（跨后端帧对帧比对）")
    a.add_argument("--ref", required=True,
                   help="参照帧：PNG 文件或 f{:04d}.png 帧目录（golden 基线侧）")
    a.add_argument("--cand", required=True,
                   help="候选帧：PNG 文件或帧目录（须与 --ref 同类型；被审后端侧）")
    a.add_argument("--heatmap-dir", default="",
                   help="Tier-2/Tier-3 帧热力图输出目录（diff_f{:04d}.png，tmp+replace 原子写）")
    a.add_argument("--report", default="", help="审计结果 JSON 输出路径（可选）")

    v = sub.add_parser("verify-canary",
                       help="帧同步水印校验（issue #105）：解码 Canary 帧号并分类错帧")
    v.add_argument("--frames", default="",
                   help="f{:04d}.png 帧目录（requested = 文件名帧号）")
    v.add_argument("--png", default="",
                   help="单帧 PNG（与 --frames 互斥；--frame 缺省取文件名帧号）")
    v.add_argument("--frame", type=int, default=None,
                   help="单帧模式的请求帧号（缺省取 f{:04d}.png 文件名）")
    v.add_argument("--mode", default=fc.MODE_DIAGNOSTIC,
                   choices=list(fc.MODES),
                   help="水印模式：diagnostic_8x8（左上 8x8+右下对账块）/ "
                        "clean_1px_restore（单像素 (0,0)）")
    v.add_argument("--report", default="", help="校验结果 JSON 输出路径（可选）")

    args = ap.parse_args(argv)
    if args.cmd == "analyze":
        return cmd_analyze(args)
    if args.cmd == "verify-canary":
        return cmd_verify_canary(args)
    return cmd_build(args) if args.cmd == "build" else cmd_compare(args)


if __name__ == "__main__":
    sys.exit(main())
