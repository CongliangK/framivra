"""noise_gen.py —— 合成加噪段确定性生成(R-20 修订一;授权窗 #2)。

族(修订一,三族):white(白噪)/pink(粉噪)/hum(低频嗡 50–150 Hz 族,
带限白噪——正对 v1 陷波 50/100/150 Hz 频带的族间同档比较面)。
档(修订一):统一 10 dB 注入(族间同档,消除档值不对称对中位的操纵;
v1 的 10/10/20 三档读法废止)。**档值校准 = 名册冻结前完成**(过点即触发
「样本变更=基线重测」;本模块 `calibrate` 即校准器)。

确定性(成文):随机源 = np.random.RandomState(seed_int);seed_int = 全局
SEED_ROSTER 与 sha256("<base_id>|<family>|<snr_db>") 前 8 字节的复合——同
名册同命令必得同产物(换席重建逐位一致);白噪/粉噪经 FFT 域成形(种子入
RandomState,流兼容承诺见 numpy RandomState 文档)。

注入口径(成文):scale = sqrt(P_base/P_noise)·10^(−snr_db/20);
degraded = base + noise(s16 WAV 落盘,基底原生采样率);校准值
measured_injection_snr = 10·log10(mean(base²)/mean((degraded_reload−base)²)),
要求 |校准−10| ≤ 0.5 dB 方可入名册(CALIB_TOL_DB)。
"""
import hashlib

import numpy as np

try:  # 自包含包双形态导入:包内相对 / 直连脚本(包目录置顶后按包导入)
    from . import MEASURE_FS
except ImportError:  # noqa: E402
    import sys as _sys
    from pathlib import Path as _Path

    _d = str(_Path(__file__).resolve().parent.parent)
    if _d not in _sys.path:
        _sys.path.insert(0, _d)
    from quality import MEASURE_FS  # type: ignore

SEED_ROSTER = 20260919          # 全局种子(值入档,修订一)
FAMILIES = ("white", "pink", "hum")
SNR_DB = 10.0                   # 统一档值(修订一;名册冻结件)
CALIB_TOL_DB = 0.5              # 档值校准容差(名册冻结判据)
HUM_BAND_HZ = (50.0, 150.0)     # 低频嗡族频带(修订一)


def seed_for(base_id: str, family: str, snr_db: float = SNR_DB) -> int:
    """段级确定性种子:sha256("<base_id>|<family>|<snr_db>") 前 8 字节 ⊕ 全局种子,
    截断至 32 位域(RandomState 合法域;映射确定性,入档可复现)。"""
    h = hashlib.sha256(f"{base_id}|{family}|{snr_db}".encode("utf-8")).digest()
    return (int.from_bytes(h[:8], "big") ^ SEED_ROSTER) & 0xFFFFFFFF


def _white(rs: np.random.RandomState, n: int) -> np.ndarray:
    return rs.standard_normal(n)


def _pink(rs: np.random.RandomState, n: int) -> np.ndarray:
    """粉噪:FFT 域 1/√f 幅度成形(−3 dB/oct),相位随机。"""
    spec = np.fft.rfft(rs.standard_normal(n))
    f = np.fft.rfftfreq(n, 1.0 / MEASURE_FS)
    shape = np.ones_like(f)
    band = f > 0
    shape[band] = 1.0 / np.sqrt(f[band])
    shape[0] = 0.0
    x = np.fft.irfft(spec * shape, n)
    return x / (np.abs(x).max() + 1e-12) * rs.standard_normal()


def _hum(rs: np.random.RandomState, n: int, fs: int) -> np.ndarray:
    """低频嗡族:白噪 → 4 阶 Butterworth 带通 50–150 Hz(scipy sosfilt)。"""
    from scipy.signal import butter, sosfilt

    sos = butter(4, HUM_BAND_HZ, btype="bandpass", fs=fs, output="sos")
    return sosfilt(sos, rs.standard_normal(n))


def gen_noise(family: str, n: int, fs: int, seed: int) -> np.ndarray:
    """按族生成单位功率归一前的原始噪声序列(确定性)。"""
    if family not in FAMILIES:
        raise ValueError(f"未知噪声族 {family}(值域 {FAMILIES})")
    rs = np.random.RandomState(seed)
    if family == "white":
        x = _white(rs, n)
    elif family == "pink":
        x = _pink(rs, n)
    else:
        x = _hum(rs, n, fs)
    p = float(np.mean(x ** 2))
    return x / np.sqrt(p) if p > 0 else x  # 单位功率归一(功率域确定性)


def inject_fs(base: np.ndarray, fs: int, family: str, snr_db: float, seed: int) -> np.ndarray:
    """注入(唯一入口;fs=基底原生采样率):degraded = base + noise·scale,
    scale 使全段功率 SNR = snr_db。

    SNR 口径(成文):10·log10(mean(base²)/mean(noise²)),全段功率
    (含基底静默段——噪声功率以基底全段功率锚定,静默段即纯噪声区,
    供 segSNR 静默窗与噪声底下降口径使用)。
    """
    base = np.asarray(base, dtype=np.float64)
    noise = gen_noise(family, base.size, fs, seed)
    p_base = float(np.mean(base ** 2)) + 1e-20
    scale = np.sqrt(p_base / (float(np.mean(noise ** 2)) + 1e-20)) * 10 ** (-snr_db / 20.0)
    return base + noise * scale


def measured_snr(base: np.ndarray, degraded: np.ndarray) -> float:
    """校准口径:10·log10(mean(base²)/mean((degraded−base)²))(落盘重读后算)。"""
    base = np.asarray(base, dtype=np.float64)
    degraded = np.asarray(degraded, dtype=np.float64)
    n = min(base.size, degraded.size)
    noise_p = float(np.mean((degraded[:n] - base[:n]) ** 2))
    return 10.0 * np.log10(float(np.mean(base[:n] ** 2)) / (noise_p + 1e-30))


def calibrate(base: np.ndarray, fs: int, family: str, snr_db: float = SNR_DB,
              seed: int = None) -> dict:
    """档值校准(名册冻结前置,修订一):生成→s16 量化→重读→复测 SNR。

    返回 {seed, family, snr_nominal_db, snr_measured_db, delta_db, within_tol}。
    within_tol=False 的段禁入名册(过点重测条款锚点)。
    """
    seed = seed_for("calib", family, snr_db) if seed is None else seed
    deg = inject_fs(base, fs, family, snr_db, seed)
    q = np.clip(np.round(deg * 32767.0), -32768, 32767) / 32767.0  # s16 量化仿真
    m = measured_snr(base, q)
    return {"seed": int(seed), "family": family, "snr_nominal_db": snr_db,
            "snr_measured_db": round(float(m), 4),
            "delta_db": round(float(m - snr_db), 4),
            "within_tol": bool(abs(m - snr_db) <= CALIB_TOL_DB)}


# ---------------------------------------------------------------- CLI
def _read_wav(path):
    import soundfile as sf

    x, fs = sf.read(str(path), dtype="float64", always_2d=False)
    return x, int(fs)


def _write_wav16(path, x, fs):
    import soundfile as sf

    q = np.clip(np.round(np.asarray(x) * 32767.0), -32768, 32767).astype(np.int16)
    sf.write(str(path), q, fs, subtype="PCM_16")


def main(argv):
    """noise_gen.py <roster.json> <bench_root> —— 按名册合成段表生成全部 18 段。

    幂等:同名册同种子逐位重写(重跑零漂移,换席重建必得同产物)。
    """
    import json
    import sys
    from pathlib import Path

    if len(argv) != 3:
        print(__doc__)
        return 2
    roster = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    bench = Path(argv[2])
    out_dir = bench / "corpus"
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for seg in roster["segments"]:
        if seg.get("family_type") != "synthetic":
            continue
        base = next((b for b in roster.get("bases", []) if b["base_id"] == seg["base_id"]),
                    None) or next(s for s in roster["segments"] if s["segment_id"] == seg["base_id"])
        bx, fs = _read_wav(bench / base["file"])
        seed = seed_for(seg["base_id"], seg["noise_family"], seg["snr_nominal_db"])
        deg = inject_fs(bx, fs, seg["noise_family"], seg["snr_nominal_db"], seed)
        out = bench / seg["file"]
        out.parent.mkdir(parents=True, exist_ok=True)
        _write_wav16(out, deg, fs)
        cal = measured_snr(bx, _read_wav(out)[0])
        seg["seed"] = seed
        seg["snr_measured_db"] = round(float(cal), 4)
        seg["sha256"] = hashlib.sha256(out.read_bytes()).hexdigest()
        made.append({"file": seg["file"], "seed": seed, "snr_measured_db": seg["snr_measured_db"]})
    print(json.dumps({"generated": len(made), "snr_nominal_db": SNR_DB,
                      "calib_tol_db": CALIB_TOL_DB, "items": made},
                     ensure_ascii=False, indent=2))
    bad = [m for m in made if abs(m["snr_measured_db"] - SNR_DB) > CALIB_TOL_DB]
    if bad:
        print(f"[noise_gen] 校准超差 {len(bad)} 段(禁入名册,过点重测条款):", file=sys.stderr)
        for b in bad:
            print(f"  {b['file']}: {b['snr_measured_db']} dB", file=sys.stderr)
        return 1
    # 档值校准回写(修订一:校准于名册冻结前完成;种子/实测档值/产物哈希入名册件)
    roster_path = Path(argv[1])
    roster["calibration"] = {
        "rule": f"10·log10(mean(base²)/mean((reload−base)²)),容差 ±{CALIB_TOL_DB} dB",
        "snr_nominal_db": SNR_DB, "all_within_tol": True,
        "tool": "noise_gen.py(SEED_ROSTER=%d)" % SEED_ROSTER,
    }
    roster_path.write_text(json.dumps(roster, ensure_ascii=False, indent=2, sort_keys=True),
                           encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv))
