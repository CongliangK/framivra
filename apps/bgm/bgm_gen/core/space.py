"""确定性厅堂空间化机制（v3.2 空间化轮）。

三件机制的家：
    1. make_hall_ir()  合成厅堂 IR（早期反射簇 + 晚期混响，Schroeder 自测 RT60）；
    2. apply_track_space()  轨道深度模型（预延迟/电平/高频滚降/宽度）；
    3. ola_convolve()  FFT/OLA 分块卷积（wet 总线用，复用 lowpass 扫描的 OLA 手法）。

确定性红线：全部 DSP 逐位可复现——随机量不走 numpy.random（Generator 流不承诺
跨 numpy 版本稳定，NEP 19 只冻结 legacy RandomState），改用 splitmix64 整数哈希
自产 uniform 噪声：纯 uint64 环绕运算，逐位确定且跨平台/跨 numpy 版本稳定。
同参数两次 make_hall_ir 输出 sha 相等（测试锁定）。

深度模型（v3.2 任务书口径；depth 0=台前 1=厅尾）：
    预延迟   depth × 24ms（≈8.2m 声程差 @343m/s）
    电平     -depth × 4.5dB
    高频滚降 一阶（6dB/oct）lowpass，截止 8000→3500Hz 线性随 depth（空气吸收）
    宽度     width 0..1，0.3=中性（side 增益 = width/0.3：0=单声道坍缩，
             1=side ×10/3）；mono 工程无宽度概念（跳过）
    湿送量   send_db 缺省 = -12 + depth × 9（越远越湿；mixer 层取用）
各算子在自身中性点（depth=0 的延迟/增益/滤波、width=0.3）逐位旁路——
depth=0+width=0.3 输出与输入逐位相等。
"""

import math

import numpy as np

# ---- 深度模型常量（mixer 与测试共用口径） -------------------------------
PREDELAY_MAX_S = 0.024        # depth=1 的预延迟（秒）
DEPTH_LEVEL_DB = 4.5          # depth=1 的电平衰减（dB）
HF_NEAR_HZ = 8000.0           # depth=0 的高频截止（此点以上才滚降）
HF_FAR_HZ = 3500.0            # depth=1 的高频截止
WIDTH_NEUTRAL = 0.3           # side 增益 = width/0.3 的中性点
SEND_BASE_DB = -12.0          # send_db 缺省公式：-12 + depth×9
SEND_DEPTH_DB = 9.0

# ---- IR 几何常量 --------------------------------------------------------
IR_DEFAULT_SIZE_M = 25.0      # 基准厅堂尺寸（早期窗 5-30ms 的缩放锚）
IR_EARLY_MIN_MS = 5.0         # 早期反射窗下限（size=25m 时）
IR_EARLY_MAX_MS = 30.0        # 早期反射窗上限
IR_LATE_ATTACK_MS = 20.0      # 晚期混响淡入（防起点咔哒，不改变衰减斜率）
RT60_TOL = 0.15               # 生成后 Schroeder 自测容差（±15%）

_MASK64 = np.uint64(0xFFFFFFFFFFFFFFFF)
_SM1 = np.uint64(0xBF58476D1CE4E5B9)
_SM2 = np.uint64(0x94D049BB133111EB)


def depth_default_send_db(depth):
    """湿送量缺省公式：-12 + depth×9 dB（越远越湿）。"""
    return SEND_BASE_DB + float(depth) * SEND_DEPTH_DB


# ------------------------------------------------------------ 确定性噪声

def _hash_uniform(seed, stream, n):
    """splitmix64 哈希均匀噪声 → float64[n] ∈ [-1, 1)。

    输入 (seed, stream, i) 三元组独立哈希（stream 区分左右声道/用途），
    末 53 bit 映射 [0,1) 再平移到 [-1,1)。种子项在 Python int 域算（任意
    精度、无 numpy 标量溢出告警），数组 uint64 环绕静默且跨平台一致。
    """
    seed64 = int(seed) & 0xFFFFFFFFFFFFFFFF
    stream64 = int(stream) & 0xFFFFFFFFFFFFFFFF
    base = (seed64 * 0x9E3779B97F4A7C15 + stream64 * 0xBB67AE8584CAA73B) \
        & 0xFFFFFFFFFFFFFFFF
    i = np.arange(n, dtype=np.uint64)
    z = (np.uint64(base) + i) & _MASK64
    z = ((z ^ (z >> np.uint64(30))) * _SM1) & _MASK64
    z = ((z ^ (z >> np.uint64(27))) * _SM2) & _MASK64
    z = z ^ (z >> np.uint64(31))
    u = (z >> np.uint64(11)).astype(np.float64) * (1.0 / 9007199254740992.0)
    return u * 2.0 - 1.0


# ------------------------------------------------------------ 厅堂 IR 合成

def make_hall_ir(rt60_s, size_m=IR_DEFAULT_SIZE_M, seed=0, sr=44100, channels=2):
    """合成厅堂 IR → float64 (n, channels)。

    结构（时间轴，秒）：
        [0, t_e0)            静默（直达声属 dry 路径，wet IR 不含直达——
                             湿声与干声在 mixer 求和，含直达会双声像）
        [t_e0, t_e1)         早期反射簇：8 记稀疏反射对，位置由 size_m 决定
                             （窗口随尺寸缩放：size/25 × [5ms, 30ms]），左右
                             不对称（L 主导幅 [0.8,1.0]、R 主导幅 [0.35,0.5]、
                             互渗 0.3——构造性保证早期 L/R 能量差 >3dB）
        [t_e1, t_e1+rt60]    晚期混响：两通道独立去相关均匀噪声 × 攻击淡入
                             （20ms 线性）× 指数幅包络 10^(-3t/rt60)
                             （幅 -60dB 恰落在 t=rt60 → 能量倒积分斜率自洽）

    归一：全局能量 Σh² = 1（跨通道合计）。**声道口径（v3.2 审收 M-1 订正）**：
    立体声 IR 能量 L/R 均分 0.5/0.5 → 每通道 wet RMS 恒比 send 低 3.01dB
    （mono 则精确等价 0dB）；send_db 语义仍稳定（通道内波动 <0.02dB），
    只是绝对基准按声道计数折半。RT60 斜率不受归一影响。
    自测：生成后 ir_rt60_schroeder 实测，偏差 >RT60_TOL(15%) 即 raise
    （机制自身带验收，不让坏参数静默进混音）。

    逐位确定：噪声=splitmix64 整数哈希，无 numpy.random。
    """
    rt60_s = float(rt60_s)
    size_m = float(size_m)
    if size_m < 12.0:
        # v3.2 审收 M-2：span（=size×1ms）小于固定抖动 12ms 时早期反射堆积
        # 下界/浸入晚期区（实测 size=8 病态：4/8 记撞下界）。size=12 为
        # 边界（span==抖动，可用但反射窗贴边）。产线 space_ir 闭集不暴露
        # size_m（恒默认 25），此门只护直调 API 误用。
        raise ValueError(f"size_m={size_m} 过小：早期反射几何病态区（<12m），"
                         "请用 ≥12（25 为默认推荐）")
    if not (0.1 <= rt60_s <= 8.0):
        raise ValueError(f"rt60_s={rt60_s} 越界（生成器支持 0.1..8.0；"
                         f"工程层校验门为 0.4..4.0）")
    if not (3.0 <= size_m <= 100.0):
        raise ValueError(f"size_m={size_m} 越界（3..100）")
    if channels not in (1, 2):
        raise ValueError(f"channels={channels}（仅 1/2）")

    scale = size_m / IR_DEFAULT_SIZE_M
    t_e0 = max(0.003, IR_EARLY_MIN_MS / 1000.0 * scale)
    t_e1 = min(0.060, max(t_e0 + 0.002, IR_EARLY_MAX_MS / 1000.0 * scale))
    n_total = int(round((t_e1 + rt60_s) * sr))
    ir = np.zeros((n_total, channels), dtype=np.float64)

    # ---- 早期反射簇（8 记：偶序 L 主导强、奇序 R 主导弱 → 构造性不对称）
    i0 = int(round(t_e0 * sr))
    i1 = max(i0 + 8, int(round(t_e1 * sr)))
    span = max(1, i1 - i0)
    for k in range(8):
        u = _hash_uniform(seed, 100 + k, 4)     # u[0]位置抖动 u[1]幅度 u[3]极性
        pos = i0 + int(round((k + 0.5) / 8.0 * span + u[0] * 0.012 * sr))
        pos = min(max(pos, i0), n_total - 1)
        if k % 2 == 0:                                   # L 主导 [0.8, 1.0]
            a = 0.8 + 0.2 * (0.5 * (u[1] + 1.0))
            amp_l, amp_r = a, a * 0.3
        else:                                            # R 主导 [0.35, 0.5]
            a = 0.35 + 0.15 * (0.5 * (u[1] + 1.0))
            amp_l, amp_r = a * 0.3, a
        sign = 1.0 if u[3] >= 0 else -1.0
        if channels == 2:
            ir[pos, 0] += sign * amp_l
            ir[pos, 1] += sign * amp_r
        else:
            ir[pos, 0] += sign * 0.5 * (amp_l + amp_r)

    # ---- 晚期混响（攻击淡入 × 指数衰减 × 独立去相关噪声）
    n_late0 = int(round(t_e1 * sr))
    n_late = n_total - n_late0
    if n_late > 8:
        t = np.arange(n_late) / sr
        ramp = np.minimum(1.0, t / (IR_LATE_ATTACK_MS / 1000.0))
        env = ramp * 10.0 ** (-3.0 * t / rt60_s)
        for c in range(channels):
            ir[n_late0:, c] = env * _hash_uniform(seed, c, n_late)

    # ---- 全局能量归一 + RT60 自洽自测
    energy = float((ir * ir).sum())
    if energy <= 0.0:
        raise ValueError("IR 能量为零（参数退化）")
    ir /= math.sqrt(energy)
    measured = ir_rt60_schroeder(ir, sr)
    if abs(measured - rt60_s) / rt60_s > RT60_TOL:
        raise RuntimeError(
            f"make_hall_ir RT60 自测失败：参数 {rt60_s}s，Schroeder 实测 "
            f"{measured:.3f}s（容差 ±{RT60_TOL:.0%}）")
    return ir


def ir_rt60_schroeder(ir, sr):
    """Schroeder 倒积分测 RT60（生成自测与测试共用同一把尺）。

    E(t)=Σ_{τ≥t} h²（跨通道合计）→ dB → 取 -5..-35dB 段最小二乘斜率 →
    外推 -60dB。早期簇能量占比 ~1%（构造性小于 5%），-5dB 点落在晚期衰减区。
    """
    e = (np.asarray(ir, dtype=np.float64) ** 2).sum(axis=1)
    back = np.cumsum(e[::-1])[::-1]
    db = 10.0 * np.log10(np.maximum(back / back[0], 1e-30))
    idx = np.arange(len(db))
    lo_i = int(np.argmax(db <= -5.0))                    # 首个 ≤-5dB 的样本
    hi_i = int(np.argmax(db <= -35.0))
    if db[-1] > -35.0 or hi_i <= lo_i + 4:
        raise ValueError("Schroeder 积分未达 -35dB（信号过短或无衰减）")
    sel = idx[lo_i:hi_i + 1]
    slope = np.polyfit(sel / sr, db[lo_i:hi_i + 1], 1)[0]
    if slope >= 0:
        raise ValueError(f"倒积分斜率非负（{slope}）——非衰减信号")
    return 60.0 / (-slope)


# ------------------------------------------------------------ OLA DSP

def ola_convolve(x, ir, block=65536):
    """FFT/OLA 分块线性卷积（wet 总线用）。

    x:(n,ch) × ir:(m,ch) 逐通道 → (n+m-1, ch) full 卷积。分块大小 block，
    FFT 长 N = 下一个 2 幂(≥ block+m-1)：整段单发 FFT 在 300s 级工程上
    要 2^24 点（峰值 RSS 数百 MB），分块把峰值钉在 O(N)（几 MB）——
    手法同 mixer._apply_lowpass_sweep 的 OLA（纯 numpy float64 → 逐位确定）。
    """
    x = np.asarray(x, dtype=np.float64)
    ir = np.asarray(ir, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    if ir.ndim == 1:
        ir = ir[:, None]
    if x.shape[1] != ir.shape[1]:
        raise ValueError(f"通道数不符：x={x.shape[1]} ir={ir.shape[1]}")
    n, m = x.shape[0], ir.shape[0]
    nfft = 1 << (block + m - 1 - 1).bit_length()
    IR = np.fft.rfft(ir, n=nfft, axis=0)
    out = np.zeros((n + m - 1, x.shape[1]), dtype=np.float64)
    for start in range(0, n, block):
        seg = x[start:start + block]
        y = np.fft.irfft(np.fft.rfft(seg, n=nfft, axis=0) * IR, n=nfft, axis=0)
        L = seg.shape[0] + m - 1
        out[start:start + L] += y[:L]
    return out


def _ola_static_filter(x, sr, fc, N=4096):
    """静态截止频率的一阶（6dB/oct）零相位 lowpass，OLA 分块施加。

    |H| = 1/sqrt(1+(f/fc)^2)（RC 一阶幅频）。块结构逐行复用
    mixer._apply_lowpass_sweep 的实测口径（周期 Hann 分析窗 + 50% 跳步 +
    COLA 重建；两端各补 N 零）。刻意不复用/不改动那份函数本体——它喂
    现行位级锚点（test_cli mix.flac sha），本模块零接触。
    """
    n, ch = x.shape
    hop = N // 2
    w = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N) / N)
    freqs = np.fft.rfftfreq(N, 1 / sr)
    H = 1.0 / np.sqrt(1.0 + (freqs / fc) ** 2)
    padded = np.vstack([np.zeros((N, ch)), x, np.zeros((N, ch))])
    out = np.zeros_like(padded)
    for start in range(0, padded.shape[0] - N + 1, hop):
        blk = padded[start:start + N] * w[:, None]
        out[start:start + N] += np.fft.irfft(
            np.fft.rfft(blk, axis=0) * H[:, None], n=N, axis=0)
    return out[N:N + n]


def apply_track_space(x, sr, depth, width=WIDTH_NEUTRAL):
    """轨道深度模型（原地语义的纯函数版）：x:(n,ch) float → 处理后副本。

    施加序：预延迟 → 电平 → 高频滚降 → 宽度（M/S side 增益=width/0.3）。
    各算子在自身中性点逐位旁路（depth=0 的三项、width=0.3、mono 的宽度），
    即 depth=0+width=0.3 输出与输入逐位相等。
    """
    x = np.asarray(x, dtype=np.float64)
    y = x.copy()
    # 预延迟：右移 depth×24ms（前端补零、末端截 24ms——bus 已是工程全长窗）
    shift = int(round(float(depth) * PREDELAY_MAX_S * sr))
    if shift > 0:
        y = np.vstack([np.zeros((min(shift, y.shape[0]), y.shape[1])),
                       y[:max(0, y.shape[0] - shift)]])
    # 电平：-depth×4.5dB
    gain = 10.0 ** (-float(depth) * DEPTH_LEVEL_DB / 20.0)
    if abs(gain - 1.0) > 1e-12:
        y *= gain
    # 高频滚降：一阶 lowpass 8000→3500Hz（depth=0 恒等旁路，8kHz 才起作用）
    if float(depth) > 0.0:
        fc = HF_NEAR_HZ + float(depth) * (HF_FAR_HZ - HF_NEAR_HZ)
        y = _ola_static_filter(y, sr, fc)
    # 宽度：M/S side 增益（0.3=中性；mono 无概念跳过）
    if y.shape[1] == 2 and abs(float(width) - WIDTH_NEUTRAL) > 1e-12:
        side_gain = float(width) / WIDTH_NEUTRAL
        mid = 0.5 * (y[:, 0] + y[:, 1])
        side = 0.5 * (y[:, 0] - y[:, 1]) * side_gain
        y = np.stack([mid + side, mid - side], axis=1)
    return y


# ------------------------------------------------------------ wav 写出

def write_f32_wav(path, audio, sr):
    """audio[n,ch] → RIFF IEEE-float wav（wave 模块只写 PCM，手写头）。

    mixer._write_f32_wav 的唯一实现体（mixer 经别名导入——本模块先于
    mixer 需要落盘 IR，且 mixer 反向 import 本模块，实现在此避免环）。
    """
    a = np.ascontiguousarray(audio, dtype="<f4")
    ch = a.shape[1]
    data = a.tobytes()
    byterate = sr * ch * 4
    hdr = (b"RIFF" + (36 + len(data)).to_bytes(4, "little") + b"WAVE"
           + b"fmt " + (16).to_bytes(4, "little")
           + (3).to_bytes(2, "little") + ch.to_bytes(2, "little")   # 3 = IEEE float
           + sr.to_bytes(4, "little") + byterate.to_bytes(4, "little")
           + (ch * 4).to_bytes(2, "little") + (32).to_bytes(2, "little")
           + b"data" + len(data).to_bytes(4, "little"))
    with open(path, "wb") as f:
        f.write(hdr + data)
    return path


def write_ir_wav(path, ir, sr):
    """IR 数组落盘便捷入口（float32 wav；同参数同字节，可 diff——BG-D2）。"""
    return write_f32_wav(path, np.asarray(ir, dtype=np.float64), sr)
