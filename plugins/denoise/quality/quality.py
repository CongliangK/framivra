"""quality.py —— 客观三维指标计算(R-20 §3.3;修订五/七实现口径)。

指标面(全部 16 kHz mono 测量域,带宽/全带能量除外——48k 原生域):
  降噪量  segSNR(dB)   —— 活动窗/静默窗分段,窗口法见 seg_snr docstring(成文);
  可懂度  STOI(0-1)    —— pystoi 0.4.1 标准 STOI(extended=False);
          CER          —— 见 cer.py(真实轨;ASR 产物入参);
  伪损    PESQ         —— pesq 宽带模式(仅收 8k/16k,本域 16k);
          LSD(dB)      —— 对数谱距离,n_fft=512/hop=256/hann;
          带宽收缩(Hz) —— 48k 原生域带限能量比法,见 bandwidth_48k docstring。

对齐防御(修订七.2,成文):
  全局延迟校正 = FFT 互相关 argmax(滞后整数样本,范围 ±1 s),deg 左移对齐;
  分帧漂移检查 = 0.5 s 帧/0.25 s 步逐帧互相关滞后,漂移嫌疑判据 =
    std(帧滞后) > 0.25 ms(4 样本@16k)或 max|帧滞后−全局滞后| > 1.0 ms;
  命中即置 confidence.suspicious=True(reason=nonlinear_drift),该段 PESQ/STOI
  带低置信标记;排除与汇总规则在 report.py(排除率 >30% 整轮无效)。

纪律:本模块禁读盘/禁写盘(纯函数域;盘 IO 归 corpus/report),禁 librosa/
ffmpeg 重采样旁路;解释器 = DENOISE_PY(quality.conda_python 同链约定)。
"""
import numpy as np

try:  # 自包含包双形态导入:包内相对 / 直连脚本(包目录置顶后按包导入,防同名自碰)
    from . import MEASURE_FS, require_measure_domain
except ImportError:  # noqa: E402
    import sys as _sys
    from pathlib import Path as _Path

    _d = str(_Path(__file__).resolve().parent.parent)
    if _d not in _sys.path:
        _sys.path.insert(0, _d)
    from quality import MEASURE_FS, require_measure_domain  # type: ignore

SEGSNR_CAP_DB = 100.0  # 恒等退化(seg==ref)封顶值,防 inf 入 JSON
PESQ_CAP = 4.64        # PESQ 宽带满分上界(恒等时 pesq 库返回 4.643)

# STFT 口径(LSD 与帧特征共用)
N_FFT = 512
HOP = 256

# 漂移检查参数(修订七.2 成文)
DRIFT_FRAME_S = 0.5
DRIFT_HOP_S = 0.25
DRIFT_STD_MAX_SAMPLES = 4.0     # 0.25 ms @16k
DRIFT_DEV_MAX_SAMPLES = 16.0    # 1.0 ms @16k
ALIGN_MAX_LAG_S = 1.0           # 全局互相关搜索窗 ±1 s


# ---------------------------------------------------------------- 域准备
def to_measure_domain(x, fs: int) -> np.ndarray:
    """下混(通道算术均值)→ soxr 重采样至 16 kHz(修订五唯一入口)。

    重采样器 = soxr 单一实现(quality.RESAMPLER);输入可为 (n,) 或 (n, ch)。
    """
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim == 2:
        arr = arr.mean(axis=1)  # 先下混:通道算术均值(钉死顺序)
    elif arr.ndim != 1:
        raise ValueError(f"非法音频形状 {arr.shape}")
    if int(fs) == MEASURE_FS:
        return arr.astype(np.float64)
    import soxr

    return soxr.resample(arr, int(fs), MEASURE_FS, quality="HQ")


# ---------------------------------------------------------------- 对齐
def _xcorr_lag(a: np.ndarray, b: np.ndarray, max_lag: int) -> int:
    """FFT 互相关 argmax:返回滞后 k 使 b ≈ a 右移 k(即 b[n] ≈ a[n−k])。

    符号约定经实证钉死:cc = irfft(conj(A)·B) 峰位 m 满足「b=a 延迟 m」
    (b 超前 a 时 m 为负);负滞后由循环卷积尾部折返读取。
    """
    n = int(2 ** np.ceil(np.log2(len(a) + len(b))))
    A = np.fft.rfft(a, n)
    B = np.fft.rfft(b, n)
    cc = np.fft.irfft(np.conj(A) * B, n)
    cc = np.concatenate([cc[-max_lag:], cc[: max_lag + 1]])
    k = int(np.argmax(cc)) - max_lag
    return k


def global_align(ref: np.ndarray, deg: np.ndarray):
    """全局延迟校正:返回 (deg_aligned, lag_samples)。

    lag>0 表示 deg 相对 ref 滞后 lag 样本;对齐 = deg 左移 lag。
    |lag| 上限 ±1 s(ALIGN_MAX_LAG_S),超限视为失配(不硬扭)。
    """
    ref = require_measure_domain(ref, MEASURE_FS)
    deg = require_measure_domain(deg, MEASURE_FS)
    n = min(len(ref), len(deg))
    max_lag = int(ALIGN_MAX_LAG_S * MEASURE_FS)
    k = _xcorr_lag(ref[:n], deg[:n], max_lag)
    if k > 0:
        aligned = deg[k:]
    else:
        aligned = np.concatenate([np.zeros(-k), deg[: len(deg) + k]]) if k < 0 else deg
    m = min(len(ref), len(aligned))
    return aligned[:m], k


def drift_check(ref: np.ndarray, deg: np.ndarray, fs: int = MEASURE_FS) -> dict:
    """分帧漂移检查(修订七.2):逐帧滞后统计与嫌疑判定。

    嫌疑判据(成文):std(帧滞后) > 0.25 ms 或 max|帧滞后−全局滞后| > 1.0 ms。
    帧级搜索窗(改尺 v2,经理批准 2026-09-19):min(全局窗 ±1s, frame//2)——
    搜索窗不得超出帧支撑(超支撑=循环相关比零/环绕=边界伪峰;原实现 ±1s 对
    0.5s 帧系实现 bug,伪峰贴 ±0.9s 边界误触发排除门 61%)。
    活动门(改尺 v3,同日批准):仅活动帧(帧 RMS dBFS>第 10 百分位+10dB,
    =seg_snr 活动帧定义)测滞后——噪声主导帧滞后无定义(与 v1 同族根因:
    在无定义处做测量;静帧门 1e-6 形同虚设实证 -37.8dBFS vs -29.5dBFS)。
    双侧收口(改尺 v4,同日第三次批准):ref 与 deg 各自过本侧活动门方测
    滞后——链压静帧(v1 降噪 -19dB)deg 侧无定义,单侧门漏放。三处同族
    根因(搜索窗出支撑/ref 侧无定义/deg 侧无定义)至此语义闭合。
    返回 {frame_lags_samples, lag_std_samples, lag_std_ms, max_dev_samples,
          suspicious, reason}。
    """
    ref = require_measure_domain(ref, fs)
    deg = require_measure_domain(deg, fs)
    frame = int(DRIFT_FRAME_S * fs)
    hop = int(DRIFT_HOP_S * fs)
    frame_max_lag = min(int(ALIGN_MAX_LAG_S * fs), frame // 2)  # 改尺 v2(见 docstring)
    n_all = min(len(ref), len(deg))
    # 改尺 v3(经理批准 2026-09-19):活动门=seg_snr 成文口径复用——帧 RMS dBFS
    # 第 10 百分位为 floor,仅活动帧(floor+10dB 以上)测滞后。原静帧门 1e-6
    # (=-120dBFS)对真实语料形同虚设,噪声主导帧相关峰随机(无定义滞后入统计
    # =与 v1 搜索窗同族根因:「在无定义处做测量」)。
    # 改尺 v4(同日第三次批准·同族收口):活动门双侧化——ref 与 deg 各自超其
    # 本侧 floor+10dB 方测滞后(v1 链将帧压静 ~19dB 时 deg 侧无定义,ref 单侧
    # 门漏放;实证离群帧 deg 中位 -42.5dBFS vs 正常 -23.4dBFS)。
    idx_all = np.arange(frame)[None, :] + hop * np.arange(max(1, 1 + (n_all - frame) // hop))[:, None]
    db_ref = 20.0 * np.log10(np.maximum(np.sqrt(np.mean(ref[idx_all] ** 2, axis=1)), 1e-10))
    db_deg = 20.0 * np.log10(np.maximum(np.sqrt(np.mean(deg[idx_all] ** 2, axis=1)), 1e-10))
    floor_ref = float(np.percentile(db_ref, 10))
    floor_deg = float(np.percentile(db_deg, 10))
    lags = []
    for start in range(0, n_all - frame, hop):
        r = ref[start : start + frame]
        d = deg[start : start + frame]
        if 20.0 * np.log10(max(np.sqrt(np.mean(r * r)), 1e-10)) <= floor_ref + 10.0:
            continue  # ref 非活动帧:滞后无定义,不入统计(改尺 v3)
        if 20.0 * np.log10(max(np.sqrt(np.mean(d * d)), 1e-10)) <= floor_deg + 10.0:
            continue  # deg 非活动帧:同上(改尺 v4 双侧收口)
        lags.append(_xcorr_lag(r, d, frame_max_lag))
    if len(lags) < 3:
        return {"frame_lags_samples": lags, "lag_std_samples": 0.0, "lag_std_ms": 0.0,
                "max_dev_samples": 0.0, "suspicious": False, "reason": ""}
    arr = np.asarray(lags, dtype=np.float64)
    std = float(arr.std())
    dev = float(np.abs(arr - arr.mean()).max())
    suspicious = (std > DRIFT_STD_MAX_SAMPLES) or (dev > DRIFT_DEV_MAX_SAMPLES)
    return {"frame_lags_samples": lags, "lag_std_samples": std,
            "lag_std_ms": round(std / fs * 1000.0, 4),
            "max_dev_samples": dev,
            "suspicious": bool(suspicious),
            "reason": "nonlinear_drift" if suspicious else ""}


# ---------------------------------------------------------------- 三维指标
def seg_snr(ref: np.ndarray, deg: np.ndarray, fs: int = MEASURE_FS) -> float:
    """segSNR(dB)——活动窗/静默窗分段法(窗口法成文,本书 §3.3)。

    帧法:帧长 32 ms、帧移 16 ms;参考帧 RMS(dBFS)升序取第 10 百分位为
    噪底参考 floor_db;活动帧 = 帧 RMS(dBFS) > floor_db + 10 dB;
    segSNR = 10·log10( Σ_active ref² / Σ_active (ref−deg)² );
    无活动帧(全静音参考)→ 返回 NaN(调用方按「临时未测 null」处理);
    恒等(seg==ref)→ 分母趋 0,封顶 SEGSNR_CAP_DB=100.0。
    """
    ref = require_measure_domain(ref, fs)
    deg = require_measure_domain(deg, fs)
    n = min(len(ref), len(deg))
    ref, deg = ref[:n], deg[:n]
    frame = int(0.032 * fs)
    hop = int(0.016 * fs)
    if n < frame:
        return float("nan")
    fr = 1 + (n - frame) // hop
    idx = np.arange(frame)[None, :] + hop * np.arange(fr)[:, None]
    ref_f = ref[idx]
    rms = np.sqrt(np.mean(ref_f * ref_f, axis=1))
    db = 20.0 * np.log10(np.maximum(rms, 1e-10))
    floor_db = float(np.percentile(db, 10))
    active = db > floor_db + 10.0
    if not active.any():
        return float("nan")
    den_f = deg[idx]
    num = float(np.sum(ref_f[active] ** 2))
    den = float(np.sum((ref_f[active] - den_f[active]) ** 2))
    if den <= num * 10 ** (-SEGSNR_CAP_DB / 10.0):
        return SEGSNR_CAP_DB
    return 10.0 * np.log10(num / den)


def stoi(ref: np.ndarray, deg: np.ndarray, fs: int = MEASURE_FS) -> float:
    """STOI(0-1),pystoi 标准 STOI(extended=False;0.4.1 实测版本钉值)。"""
    ref = require_measure_domain(ref, fs)
    deg = require_measure_domain(deg, fs)
    from pystoi.stoi import stoi as _stoi_fn

    return float(_stoi_fn(ref, deg, fs_sig=int(fs), extended=False))


def pesq_wb(ref: np.ndarray, deg: np.ndarray, fs: int = MEASURE_FS):
    """PESQ 宽带模式(仅收 8000/16000;本域 16k)。

    pesq 库对无语音段抛 RuntimeError/NoUtterancesError → 返回 None
    (报告侧「临时未测 null」位;禁静默给 0/负值凑数)。
    """
    ref = require_measure_domain(ref, fs)
    deg = require_measure_domain(deg, fs)
    from pesq import pesq as _pesq

    try:
        return float(_pesq(int(fs), ref, deg, "wb"))
    except Exception:  # noqa: BLE001(NoUtterancesError/RuntimeError 同路:null)
        return None


def lsd(ref: np.ndarray, deg: np.ndarray, fs: int = MEASURE_FS) -> float:
    """LSD(dB)——对数谱距离:n_fft=512/hop=256/hann;帧谱功率比的对数 RMS。

    LSD = sqrt( mean_frames mean_bins [10·log10(P_ref/P_deg)]² );局限:>8 kHz
    谱细节不入本域 LSD(修订五.4 局限声明)。功率地板 eps = 1e-10·(P_ref+P_deg
    全体均值)——参考无能量频带的对数比数值爆炸防护(成文口径)。
    """
    ref = require_measure_domain(ref, fs)
    deg = require_measure_domain(deg, fs)
    n = min(len(ref), len(deg))
    if n < N_FFT:
        return float("nan")
    win = np.hanning(N_FFT)
    frames = 1 + (n - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(frames)[:, None]
    P_r = np.abs(np.fft.rfft(ref[idx] * win, axis=1)) ** 2
    P_d = np.abs(np.fft.rfft(deg[idx] * win, axis=1)) ** 2
    eps = 1e-10 * float(np.mean(P_r) + np.mean(P_d))
    diff = 10.0 * np.log10((P_r + eps) / (P_d + eps))
    return float(np.sqrt(np.mean(diff ** 2)))


# ---------------------------------------------------- 48k 原生域(修订五.3)
def bandwidth_48k(x, fs: int) -> dict:
    """48k 原生域带宽收缩与全带能量分布(带限能量比法,成文)。

    口径:Welch 型平均功率谱(帧长 4096/hop 2048/hann);累计能量自低频起
    首次 ≥ 总能量 99% 的频率 = 有效带宽 bandwidth_eff_hz;
    energy_above_8k_ratio_db = 10·log10(E(>8 kHz)/E(总))。
    返回 {bandwidth_eff_hz, energy_above_8k_ratio_db}。入参 fs=任一原生域
    (基底 44.1k/链产物 48k),禁先重采样(补 16k 域 8 kHz 以上盲区)。
    """
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim == 2:
        arr = arr.mean(axis=1)
    frame = min(4096, 1 << (arr.size - 1).bit_length() if arr.size > 1 else 4096)
    frame = min(frame, arr.size) if arr.size else 4096
    if arr.size < frame:
        pad = np.zeros(frame)
        pad[: arr.size] = arr
        seg = pad[None, :]
    else:
        hop = frame // 2
        cnt = 1 + (arr.size - frame) // hop
        idx = np.arange(frame)[None, :] + hop * np.arange(cnt)[:, None]
        seg = arr[idx]
    win = np.hanning(frame)
    P = np.abs(np.fft.rfft(seg * win, axis=1)) ** 2
    spec = P.mean(axis=0)
    freqs = np.fft.rfftfreq(frame, 1.0 / fs)
    total = float(spec.sum()) + 1e-20
    cum = np.cumsum(spec) / total
    eff_i = int(np.searchsorted(cum, 0.99))
    eff_hz = float(freqs[min(eff_i, len(freqs) - 1)])
    above = float(spec[freqs > 8000.0].sum())
    return {"bandwidth_eff_hz": eff_hz,
            "energy_above_8k_ratio_db": round(10.0 * np.log10(max(above, 1e-20) / total), 4)}


def bandwidth_shrink(ref_native, deg_native, fs_native: int) -> dict:
    """带宽收缩(伪损维,48k 原生域):ref 带宽 − deg 带宽(Hz;正值=收缩)。"""
    r = bandwidth_48k(ref_native, fs_native)
    d = bandwidth_48k(deg_native, fs_native)
    return {"ref_bandwidth_eff_hz": r["bandwidth_eff_hz"],
            "deg_bandwidth_eff_hz": d["bandwidth_eff_hz"],
            "bandwidth_shrink_hz": round(r["bandwidth_eff_hz"] - d["bandwidth_eff_hz"], 3),
            "ref_energy_above_8k_ratio_db": r["energy_above_8k_ratio_db"],
            "deg_energy_above_8k_ratio_db": d["energy_above_8k_ratio_db"]}


# ---------------------------------------------------- 真实轨噪声底(16k 域)
def noise_floor_16k(x, fs: int = MEASURE_FS) -> dict:
    """真实轨噪声底口径(16k 测量域;修订五.4「真实轨噪声底口径同 16k 域」)。

    帧法同 seg_snr(32/16 ms):floor_db = 帧 RMS(dBFS)第 10 百分位;
    返回 {noise_floor_dbfs, active_rms_dbfs, n_frames}。与 corpus 的
    只读滤镜法(ffmpeg silencedetect+astats,选段口径)两口径并存不互推
    (任务书 §1.7 交叉规则;本函数 = 测量报告列,彼 = 名册选段列)。
    """
    arr = require_measure_domain(x, fs)
    frame = int(0.032 * fs)
    hop = int(0.016 * fs)
    if arr.size < frame:
        return {"noise_floor_dbfs": None, "active_rms_dbfs": None, "n_frames": 0}
    cnt = 1 + (arr.size - frame) // hop
    idx = np.arange(frame)[None, :] + hop * np.arange(cnt)[:, None]
    rms = np.sqrt(np.mean(arr[idx] ** 2, axis=1))
    db = 20.0 * np.log10(np.maximum(rms, 1e-10))
    floor = float(np.percentile(db, 10))
    active = db > floor + 10.0
    act = float(np.mean(db[active])) if active.any() else None
    return {"noise_floor_dbfs": round(floor, 3),
            "active_rms_dbfs": None if act is None else round(act, 3),
            "n_frames": int(cnt)}


# ---------------------------------------------------- 配对段汇总入口
def measure_pair(ref16k: np.ndarray, deg16k: np.ndarray,
                 ref_native=None, deg_native=None, fs_native: int = 48000) -> dict:
    """配对段全指标:对齐→漂移检查→三维。ref/deg 已各自由调用方经
    to_measure_domain 归一;原生域入参可选(带宽维;缺省跳过并记 null)。

    返回字段:lag_samples/drift{...}/segSNR/stoi/pesq/lsd/bandwidth{...}。
    pesq=None(无语音)时 stoi/segSNR 仍出值,null 只落 pesq 位。
    """
    aligned, lag = global_align(ref16k, deg16k)
    n = min(len(ref16k), len(aligned))
    drift = drift_check(ref16k[:n], aligned[:n])
    out = {
        "lag_samples": int(lag),
        "drift": {k: drift[k] for k in ("lag_std_samples", "lag_std_ms",
                                        "max_dev_samples", "suspicious", "reason")},
        "segSNR": None if np.isnan(v := seg_snr(ref16k[:n], aligned[:n])) else round(v, 3),
        "stoi": round(stoi(ref16k[:n], aligned[:n]), 5),
        "pesq": pesq_wb(ref16k[:n], aligned[:n]),
        "pesq_rounded": None,
        "lsd": None if np.isnan(v := lsd(ref16k[:n], aligned[:n])) else round(v, 4),
        "bandwidth": None,
    }
    if out["pesq"] is not None:
        out["pesq_rounded"] = round(out["pesq"], 4)
    if ref_native is not None and deg_native is not None:
        bw = bandwidth_shrink(ref_native, deg_native, fs_native)
        out["bandwidth"] = bw
    return out
