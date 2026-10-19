#!/usr/bin/env python3
"""synthlib — 迷你"程序员的合成器机架"（纯 numpy，44.1kHz）

减法/调频合成 + 程序鼓机 + FFT 域滤波 + FIR 梳状混响。
每一个单元都是 10~30 行的可读 DSP，对应硬件合成器面板上的一个模块：
振荡器(osc) / 包络(env) / 滤波器(band) / 调频(fm_ep) / 鼓(kick,snare,hat) / 混响(reverb)。
"""
import numpy as np

SR = 44100
NOTE_SEMIS = {"C": -9, "D": -7, "E": -5, "F": -4, "G": -2, "A": 0, "B": 2}

def hz(note: str) -> float:
    """'C#3' / 'Bb2' → 频率（A4=440）"""
    name, octv = note[:-1], int(note[-1])
    semis = NOTE_SEMIS[name[0]] + (1 if "#" in name else -1 if "b" in name else 0)
    return 440.0 * 2 ** ((semis + (octv - 4) * 12) / 12)

def t_axis(dur: float) -> np.ndarray:
    return np.arange(int(dur * SR)) / SR

def osc(f0: float, dur: float, wave: str = "sine") -> np.ndarray:
    t = t_axis(dur)
    ph = 2 * np.pi * f0 * t
    return {"sine": np.sin, "saw": lambda p: 2 * (p / (2 * np.pi) % 1) - 1,
            "square": lambda p: np.sign(np.sin(p)) * 0.9,
            "tri": lambda p: 2 * np.abs(2 * (p / (2 * np.pi) % 1) - 1) - 1,
            "noise": lambda p: np.random.default_rng(int(f0)).standard_normal(len(p))}[wave](ph)

def env(dur: float, a=0.005, d=None, s=1.0, r=0.05) -> np.ndarray:
    """ADSR：线性 attack → 指数 decay 至 sustain → 线性 release（percussive 用 s=0）"""
    n = int(dur * SR)
    na, nd, nr = int(a * SR), int((d or 0) * SR), int(r * SR)
    e = np.ones(n) * s
    if na: e[:na] = np.linspace(0, 1, na)
    if nd: e[na:na + nd] = (1 - s) * np.exp(-np.arange(nd) / (nd / 4)) + s
    if nr and nr < n: e[-nr:] *= np.linspace(1, 0, nr)
    return e

def perc(dur: float, decay: float, attack=0.002) -> np.ndarray:
    """打击乐包络：快起 + 指数衰减"""
    t = t_axis(dur)
    return np.minimum(t / attack, 1.0) * np.exp(-t / decay)

def band(x: np.ndarray, lo=None, hi=None) -> np.ndarray:
    """FFT 域带通/低通/高通（软边过渡）"""
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    m = np.ones_like(f)
    if lo: m *= 1 / (1 + np.exp(-(f - lo) / (lo * 0.2)))
    if hi: m *= 1 / (1 + np.exp((f - hi) / (hi * 0.2)))
    return np.fft.irfft(X * m, len(x))

# ── 鼓机（808 血统：每个鼓都是"包络拉着的振荡器或噪声"）────────────
def kick(dur=0.35) -> np.ndarray:
    t = t_axis(dur)
    f = 45 + 110 * np.exp(-t / 0.03)                      # 音高从 155Hz 下扫到 45Hz
    ph = 2 * np.pi * np.cumsum(f) / SR
    return np.tanh(1.8 * np.sin(ph) * perc(dur, 0.16)) * 0.9

def snare(dur=0.25) -> np.ndarray:
    t = t_axis(dur)
    body = (np.sin(2 * np.pi * 185 * t) + 0.6 * np.sin(2 * np.pi * 330 * t)) * perc(dur, 0.05)
    snap = band(osc(1, dur, "noise"), 1200, 6000) * perc(dur, 0.08)
    return (0.5 * body + 0.8 * snap) * 0.8

def hat(dur=0.09, decay=0.03) -> np.ndarray:
    return band(osc(2, dur, "noise"), 7000, None) * perc(dur, decay) * 0.5

# ── 调频电钢琴（DX7 tine 的两算子近似：亮度随调制指数指数衰减）─────
def fm_ep(f0: float, dur: float, ratio=1.0, idx=2.5, bright_decay=0.18) -> np.ndarray:
    t = t_axis(dur)
    i_env = idx * np.exp(-t / bright_decay)
    carrier = np.sin(2 * np.pi * f0 * t + i_env * np.sin(2 * np.pi * f0 * ratio * t))
    partial2 = 0.12 * np.sin(2 * np.pi * f0 * 2 * t) * np.exp(-t / 0.1)   # 起音"叮"的泛音
    return (carrier + partial2) * env(dur, a=0.004, s=0.0, d=dur * 0.8) * np.exp(-t / 1.3)

def bass(f0: float, dur: float) -> np.ndarray:
    t = t_axis(dur)
    x = np.sin(2 * np.pi * f0 * t) + 0.25 * np.sin(2 * np.pi * f0 * 2 * t)
    return np.tanh(1.3 * x) * env(dur, a=0.006, s=0.7, r=0.08) * 0.9

def lead(f0: float, dur: float) -> np.ndarray:
    """双振荡器微失谐 + 迟到的颤音 = 便宜的独唱歌声"""
    t = t_axis(dur)
    vib = 0.003 * np.clip((t - 0.25) / 0.3, 0, 1) * np.sin(2 * np.pi * 5.2 * t)
    x = np.sin(2 * np.pi * f0 * 1.003 * (1 + vib) * t) + 0.6 * np.sin(2 * np.pi * f0 * 0.997 * t)
    return x * env(dur, a=0.02, s=0.8, r=0.12) * 0.5

# ── 效果器 ─────────────────────────────────────────────────────────
def reverb(x: np.ndarray, mix=0.25, tone=4500.0) -> np.ndarray:
    """4 个 FIR 梳 + 暗色尾巴（Schroeder 的非递归近似，FFT 免疫循环）"""
    y = np.zeros_like(x)
    for d_ms, g in [(29.7, 0.55), (37.1, 0.5), (41.1, 0.45), (43.7, 0.4)]:
        d = int(d_ms * SR / 1000)
        for k in range(1, 7):                              # g^k 逐圈衰减的前 6 个回声
            y[k * d:k * d + len(x)] += (g ** k) * x[:len(y) - k * d or None]
    return x + mix * band(y, None, tone)

def vinyl(dur: float, seed=7) -> np.ndarray:
    """底噪 + 稀疏爆点：lo-fi 的'菜'"""
    rng = np.random.default_rng(seed)
    t = t_axis(dur)
    x = band(rng.standard_normal(len(t)), 800, 5000) * 0.04
    for _ in range(int(dur * 9)):
        p = rng.integers(0, len(t) - 300)
        x[p:p + 300] += rng.standard_normal(300) * np.exp(-np.arange(300) / 60) * rng.uniform(0.3, 1)
    return x

def softclip(x: np.ndarray, drive=1.2) -> np.ndarray:
    return np.tanh(drive * x) / np.tanh(drive)


# ── 尼龙弦吉他（Karplus-Strong 物理模型；2026-08-25 guitar_test30 轮新增，
#    MF 盲听两轮定档件——第一轮点名 click/hiss 两瑕后激励整形修复，
#    第二轮零瑕疵并居首，证据 out/guitar_test30/_audition + LISTEN.md）──
def _sm64(seed: int, n: int) -> np.ndarray:
    """splitmix64 整数哈希白噪流 → [-1,1)（BG-D16 纪律：不走 numpy.random，
    逐位确定且跨 numpy 版本/平台稳定；uint64 回绕为算法本义）。
    陷阱注记：输出取 z 顶 53 位 /2^53——曾误写 /2^21（uint64 当 uint32 使），
    产出 ±4e9 满幅噪声且被 MF 盲听幻觉为"完美吉他"，客观量具门拦截后修复
    （2026-08-25 guitar_test30 轮，教训入 LISTEN.md 坑表）。"""
    out = np.empty(n, dtype=np.float64)
    x = np.uint64(seed & 0xFFFFFFFFFFFFFFFF)
    with np.errstate(over="ignore"):
        m1, m2, m3 = (np.uint64(0x9E3779B97F4A7C15),
                      np.uint64(0xBF58476D1CE4E5B9),
                      np.uint64(0x94D049BB133111EB))
        s30, s27, s31 = np.uint64(30), np.uint64(27), np.uint64(31)
        sc = float(1 << 53)
        for i in range(n):
            x = x + m1
            z = (x ^ (x >> s30)) * m2
            z = (z ^ (z >> s27)) * m3
            out[i] = ((z ^ (z >> s31)) >> np.uint64(11)) / sc * 2.0 - 1.0
    return out


def nylon(f0: float, dur: float, bright=0.55, pick=0.26, body=1.0,
          decay_ref=6.0) -> np.ndarray:
    """尼龙弦吉他单音拨弦（Karplus-Strong：延迟线=弦，均值低通环=高频先衰，
    梳状激励=拨弦位置，FFT 琴体响应=空气腔/面板共振）。

    params（synth 引擎透传）：
        bright    激励亮度 0-1（贴弦指甲程度：激励低通截止 2.2k+2.3k×bright Hz）
        pick      拨弦位置 0.1-0.5（弦长分数；梳状滤除其倍频处的泛音）
        body      琴体共鸣量 0-1（102/232/405Hz 三共振峰并联，0=纯弦）
        decay_ref 参考音 G2(43) 的 T60 秒；高音按 -6dB/倍频程斜率缩短
                  （t60 = decay_ref·2^(-(pitch-43)/24)，pitch 由 f0 反推）
    时值语义（拨弦无踏板）：dur=谱面时值窗，输出 = dur + 0.9s 自然延音，
    末 80ms 升余弦释音（无截断 click）。重复同音同波形（无 round-robin），
    时序解耦由上游 humanize/warp 层承担——已知语义，描述符 gaps 注记。
    """
    pitch = 69 + 12 * np.log2(max(f0, 1e-6) / 440.0)
    n0 = int(round(pitch))
    N = max(4, int(round(SR / f0)))
    t60 = decay_ref * 2 ** (-(n0 - 43) / 24.0)
    rho = 0.001 ** ((N / SR) / max(t60, 0.05))     # 每周期环增益（T60 定标）
    seed = (0xC0FFEE ^ (int(n0) * 7919)
            ^ (int(bright * 1021) << 8) ^ (int(pick * 517) << 20)) \
        & 0xFFFFFFFFFFFFFFFF
    raw = _sm64(seed, N + 8)
    # 激励：白噪 → 拨弦位置梳（强 0.92）→ 一阶低通（bright 定截止）→ 音头软窗
    pp = max(2, int(N * min(max(pick, 0.1), 0.5)))
    exc = raw[:N].copy()
    exc[pp:] -= raw[:N - pp] * 0.92
    exc *= 0.42                                     # 单音峰校准（8 音重叠不顶 softclip）
    a_lp = np.exp(-2 * np.pi * (2200.0 + 2300.0 * min(max(bright, 0.0), 1.0)) / SR)
    acc = 0.0
    for i in range(N):
        acc = (1 - a_lp) * exc[i] + a_lp * acc
        exc[i] = acc
    na = max(4, N // 12)
    exc[:na] *= np.sin(np.linspace(0, np.pi / 2, na)) ** 2
    # 弦环：y[n]=ρ/2·(y[n-N]+y[n-N-1])——按周期块向量化（数值=逐样本同式）
    ring = int((dur + 0.9) * SR)
    n_per = int(np.ceil(ring / N)) + 1
    y = np.empty(n_per * N)
    y[:N] = exc
    a = rho * 0.5
    last2 = 0.0
    for k in range(1, n_per):
        prev = y[(k - 1) * N:k * N]
        t2 = np.concatenate(([last2], prev[:-1]))
        y[k * N:(k + 1) * N] = a * (prev + t2)
        last2 = prev[-1]
    y = y[:ring]
    # 琴体（FFT 域固定响应：三共振峰并联 + 高频空气衰减；零相位、逐位确定）
    if body > 0:
        Y = np.fft.rfft(y)
        f = np.fft.rfftfreq(len(y), 1 / SR)
        H = np.ones_like(f)
        for f_res, q, g in ((102.0, 14.0, 0.9), (232.0, 18.0, 0.55),
                            (405.0, 26.0, 0.3)):
            w = 2 * np.pi * f_res / SR
            r = np.exp(-w / (2 * q))
            c1, c2, b0 = 2 * r * np.cos(w), r * r, (1 - r * r)
            wv = 2 * np.pi * f / SR
            denom = np.abs(1 - c1 * np.exp(-1j * wv) - (-c2) * np.exp(-2j * wv))
            H += g * b0 / np.maximum(denom, 1e-12)
        y = np.fft.irfft(Y * (1 + 0.45 * body * (H - 1)), len(y))
    # 触弦 tick（极轻 1.5ms）+ 总起音 2.5ms 升余弦 + 末 80ms 释音
    tl = int(0.0015 * SR)
    tick = np.diff(_sm64(seed ^ 0x71C1, tl + 1), prepend=0.0)[:tl]
    y[:tl] += tick * 0.035 * np.exp(-np.arange(tl) / (0.0004 * SR))
    nr = min(int(0.0025 * SR), len(y) - 1)
    y[:nr] *= 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, nr))
    nf = min(int(0.08 * SR), len(y) - 1)
    y[-nf:] *= 0.5 + 0.5 * np.cos(np.linspace(0, np.pi, nf))
    return y

# ── 时间线（= 宿主的轨道，但它是 numpy 数组）───────────────────────
class Track:
    def __init__(self, dur: float):
        self.buf = np.zeros(int(dur * SR) + SR)            # 多留 1s 给尾巴
    def add(self, sig: np.ndarray, at: float, gain=1.0):
        i = int(at * SR)
        seg = sig[:len(self.buf) - i]
        self.buf[i:i + len(seg)] += seg * gain

def mixdown(tracks: dict, master_lp=9500.0) -> np.ndarray:
    """各轨求和 → 整体暖色低通 → 软限幅，输出立体声（微 Haas 展宽）"""
    n = max(len(t) for t in tracks.values())
    mono = np.zeros(n)
    for t in tracks.values():
        mono[:len(t)] += t
    mono = band(mono, None, master_lp)
    mono = softclip(mono * 0.9, 1.1)
    d = int(0.012 * SR)                                     # 12ms Haas
    stereo = np.stack([mono, np.concatenate([np.zeros(d), mono[:-d]]) * 0.98], 1)
    return stereo[:len(mono)]
