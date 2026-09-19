"""corpus.py —— 名册与谱系核查(R-20 §3.2 修订一/二;授权窗 #1 只读口径)。

谱系核查(修订二,三分档,成文规则):
  对 A 族 13 件逐件:
    ① 与同名 raw 底稿(assets/raw_mp3/<stem>.mp3)对比——解码归一(16k mono
       s16 PCM)哈希相等,或(时长差 ≤50 ms 且谱指纹相关 ≥0.995)→「原始」
       (文件=底稿直转,未经工程面);
    ② 非「原始」而与某口播.aup3 提取件区域谱指纹相关 ≥0.98 →「aup3 导出」;
    ③ 皆非 →「不明」。
  合成基底只取「原始」分档件;「导出/不明」保留真实轨非配对指标与样听资格,
  禁入配对基底。原始档池 = 「原始」分档件 ∪ 口播.aup3 原始提取 takes;池内按
  PCM 归一哈希去重(同内容取 demo_audio_v2 副本为正本,提取件记 alias)。

噪声底估计(修订一,只读滤镜法,成文命令随名册在档):
  ① silencedetect 窗:ffmpeg -i <f> -af silencedetect=noise=-35dB:d=0.3 -f null -
     (无窗则降 -45dB:d=0.2;仍无窗 → 不可估,不入分层);
  ② 逐窗 astats(滤镜侧 atrim 裁剪;输出侧 -ss/-t 在 -f null 下不阻断
     滤镜统计,禁用):ffmpeg -i <f> -af \
     atrim=start=<s>:end=<e>,astats=measure_overall=RMS_level:measure_perchannel=none \
     -f null - 取 "RMS level dB";段值 = 逐窗中位。

分层择段(修订一,成文):按噪声底估计升序排序(并列取 PCM 归一哈希字典序),
连续切三档(低/中/高;池 6k+r 时前三档各 ⌈n/3⌉、余入高档),各档取序首 2 段
——换席重建必得同 6 段。

CLI:
  corpus.py genealogy <bench_root>           → genealogy/genealogy.json 证据
  corpus.py roster <bench_root> <out.json>   → 全量 31 段名册件(未冻结稿)
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

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

A_FAMILY_DIR = "assets/a_family"
RAW_MP3_DIR = "assets/raw_mp3"
AUP3_DIR = "assets/aup3_extract"
CORPUS_DIR = "corpus"
GENEALOGY_DIR = "genealogy"

# 谱系判据阈值(成文;冻结前校准注记:RAW_CORR_MIN 由 0.995 校准至 0.99——
# 依据=同一性聚簇(durΔ=0 的直转件 corr 0.9947–0.9996)与非同一性最高
# (0.839)之间的间隙,校准于名册冻结前完成并随名册在档)
RAW_DUR_TOL_S = 0.05
RAW_CORR_MIN = 0.99
AUP3_CORR_MIN = 0.98

FEAT_FRAME = 1024
FEAT_HOP = 512


# ---------------------------------------------------------------- 基础件
def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pcm16k(path) -> np.ndarray:
    """归一解码:ffmpeg 管道 → 16k mono s16 → float64(只读,零落盘)。"""
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
           "-ac", "1", "-ar", str(MEASURE_FS), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0


def pcm_hash_arr(x: np.ndarray) -> str:
    """浮点 PCM → int16 规范重量化哈希(round(x·32768) 后截幅;与 pcm16k 的
    /32768 归一互逆,数组与文件两形态共用同一身份口径)。"""
    q = np.clip(np.round(np.asarray(x, dtype=np.float64) * 32768.0), -32768, 32767)
    return hashlib.sha256(np.ascontiguousarray(q.astype("<i2")).tobytes()).hexdigest()


def pcm_sha256(path) -> str:
    """PCM 归一哈希(谱系/去重身份锚;容器差不影响)。"""
    return pcm_hash_arr(pcm16k(path))


def duration_s(path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True,
                         check=True).stdout.strip()
    return float(out)


def _feat(x: np.ndarray) -> np.ndarray:
    """谱指纹特征:log 幅度谱帧矩阵(帧 1024/hop 512,幅度对数压缩)。"""
    n = len(x)
    if n < FEAT_FRAME:
        pad = np.zeros(FEAT_FRAME)
        pad[:n] = x
        x = pad
    cnt = max(1, 1 + (len(x) - FEAT_FRAME) // FEAT_HOP)
    idx = np.arange(FEAT_FRAME)[None, :] + FEAT_HOP * np.arange(cnt)[:, None]
    mag = np.abs(np.fft.rfft(x[idx] * np.hanning(FEAT_FRAME), axis=1))
    return np.log1p(mag.T)  # (bins, frames)


def region_match(seg: np.ndarray, take: np.ndarray) -> dict:
    """段是否含于 take:特征互相关扫描(4 相位栅格偏移,防帧跨越失配),
    返回 {corr, offset_frame}。"""
    best = (-1.0, -1)
    for off in (0, FEAT_HOP // 4, FEAT_HOP // 2, 3 * FEAT_HOP // 4):
        Fs = _feat(seg)
        Ft = _feat(take[off:])
        if Fs.shape[1] > Ft.shape[1]:
            continue
        Fs0 = Fs - Fs.mean(axis=1, keepdims=True)
        n = Fs0.shape[1]
        positions = Ft.shape[1] - n
        step = max(1, positions // 400)  # 扫描步(≥400 点均匀覆盖)
        Fn = np.linalg.norm(Fs0)
        if Fn < 1e-9:
            continue
        for p in range(0, positions + 1, step):
            W = Ft[:, p : p + n]
            W0 = W - W.mean(axis=1, keepdims=True)
            denom = np.linalg.norm(W0)
            if denom < 1e-9:
                continue
            c = float(np.sum(Fs0 * W0) / (Fn * denom))
            if c > best[0]:
                best = (c, p)
    return {"corr": round(best[0], 5), "offset_frame": best[1]}


def spectral_corr(a: np.ndarray, b: np.ndarray) -> float:
    """等长对比谱相关(原始比对辅证)。"""
    n = min(len(a), len(b))
    Fa, Fb = _feat(a[:n]), _feat(b[:n])
    m = min(Fa.shape[1], Fb.shape[1])
    Fa, Fb = Fa[:, :m] - Fa[:, :m].mean(axis=1, keepdims=True), Fb[:, :m] - Fb[:, :m].mean(axis=1, keepdims=True)
    na, nb = np.linalg.norm(Fa), np.linalg.norm(Fb)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return round(float(np.sum(Fa * Fb) / (na * nb)), 5)


# ---------------------------------------------------- 只读滤镜法噪声底
_SILENCE_RE = re.compile(r"silence_start: ([\d.]+)")
_SILEND_RE = re.compile(r"silence_end: ([\d.]+)")
_RMS_RE = re.compile(r"RMS level dB:\s*(-?[\d.]+|-inf)")


def silence_windows(path, noise_db: str = "-35dB", min_d: str = "0.3"):
    """静默窗检测(只读滤镜法①;silibillity: silencedetect 口径)。"""
    cmd = ["ffmpeg", "-nostdin", "-i", str(path), "-af",
           f"silencedetect=noise={noise_db}:d={min_d}", "-f", "null", "-"]
    err = subprocess.run(cmd, capture_output=True, text=True).stderr
    starts = [float(m) for m in _SILENCE_RE.findall(err)]
    ends = [float(m) for m in _SILEND_RE.findall(err)]
    windows = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        windows.append((s, e))
    return windows


def noise_floor_ffmpeg(path) -> dict:
    """噪声底估计(只读滤镜法,成文):silencedetect 静默窗 → 逐窗 astats RMS
    → 中位。返回 {noise_floor_astats_rms_dbfs, n_windows, windows_used,
    detect_pass}。无窗 → 降阈值 -45dB/0.2s 复测;仍无 → detect_pass=False,
    不可估(不入分层,名册列记 null)。"""
    windows = silence_windows(path, "-35dB", "0.3")
    pass_name = "silencedetect=-35dB:d=0.3"
    if not windows:
        windows = silence_windows(path, "-45dB", "0.2")
        pass_name = "silencedetect=-45dB:d=0.2"
    if not windows:
        return {"noise_floor_astats_rms_dbfs": None, "n_windows": 0,
                "windows_used": 0, "detect_pass": pass_name, "estimable": False}
    vals = []
    for s, e in windows:
        if e is None or e - s < 0.05:
            continue
        # 滤镜侧裁剪(atrim):输出侧 -ss/-t 在 -f null 下不阻断滤镜统计,禁用
        cmd = ["ffmpeg", "-nostdin", "-i", str(path), "-af",
               f"atrim=start={s:.3f}:end={e:.3f},"
               "astats=measure_overall=RMS_level:measure_perchannel=none",
               "-f", "null", "-"]
        err = subprocess.run(cmd, capture_output=True, text=True).stderr
        m = _RMS_RE.findall(err)
        if m and m[-1] not in ("-inf", "nan"):
            vals.append(float(m[-1]))
    if not vals:
        return {"noise_floor_astats_rms_dbfs": None, "n_windows": len(windows),
                "windows_used": 0, "detect_pass": pass_name, "estimable": False}
    return {"noise_floor_astats_rms_dbfs": round(float(np.median(vals)), 3),
            "n_windows": len(windows), "windows_used": len(vals),
            "detect_pass": pass_name, "estimable": True}


# ---------------------------------------------------------------- 谱系核查
def genealogy_check(bench: Path) -> dict:
    """A 族 13 件谱系三分档(修订二;核查命令=本函数 CLI 全文随名册在档)。"""
    bench = Path(bench)
    a_dir = bench / A_FAMILY_DIR
    raw_dir = bench / RAW_MP3_DIR
    aup3_dir = bench / AUP3_DIR
    takes = sorted(aup3_dir.glob("*.wav"))
    take_pcm = {t.name: pcm16k(t) for t in takes}
    report = {"files": [], "takes": {}, "rule": {
        "raw_match": f"同名 raw mp3 解码归一哈希相等 或 (时长差≤{RAW_DUR_TOL_S}s 且 谱相关≥{RAW_CORR_MIN})",
        "aup3_export": f"非原始且与某提取件区域谱相关≥{AUP3_CORR_MIN}",
        "unknown": "皆非",
    }, "commands": {
        "pcm_decode": "ffmpeg -i <f> -ac 1 -ar 16000 -f s16le - (sha256 归一)",
        "entry": "DENOISE_PY plugins/denoise/quality/corpus.py genealogy <bench_root>",
    }}
    # 提取件↔raw 底稿对应(池去重与工程内容判定旁证)
    raws = sorted(raw_dir.glob("*.mp3")) if raw_dir.exists() else []
    raw_pcm = {r.stem: pcm16k(r) for r in raws}
    for t in takes:
        tp = take_pcm[t.name]
        best = {"raw": None, "corr": 0.0}
        for rs, rp in raw_pcm.items():
            c = region_match(rp, tp)["corr"]
            if c > best["corr"]:
                best = {"raw": rs, "corr": c}
        report["takes"][t.name] = {"sha256": sha256_file(t), "duration_s": duration_s(t),
                                   "best_raw_match": best["raw"], "best_raw_corr": best["corr"]}
    for f in sorted(a_dir.glob("*.wav")):
        x = pcm16k(f)
        stem = f.stem
        ev = {"file": f"{A_FAMILY_DIR}/{f.name}", "sha256": sha256_file(f),
              "pcm_sha256": pcm_sha256(f), "duration_s": duration_s(f),
              "sample_rate": int(subprocess.run(
                  ["ffprobe", "-v", "error", "-show_entries", "stream=sample_rate",
                   "-of", "csv=p=0", str(f)], capture_output=True, text=True).stdout.strip())}
        lineage, why = "不明", "无同名 raw 底稿且未命中提取件区域"
        raw_f = raw_dir / f"{stem}.mp3"
        if raw_f.exists():
            rp = raw_pcm[stem]
            eq = ev["pcm_sha256"] == pcm_hash_arr(rp)
            dur_delta = abs(duration_s(raw_f) - ev["duration_s"])
            c = spectral_corr(rp, x)
            ev["raw_match"] = {"raw_file": f"{RAW_MP3_DIR}/{raw_f.name}", "pcm_hash_equal": eq,
                               "duration_delta_s": round(dur_delta, 4), "spectral_corr": c}
            if eq or (dur_delta <= RAW_DUR_TOL_S and c >= RAW_CORR_MIN):
                lineage, why = "原始", f"同名 raw 底稿直转(hash_equal={eq}, corr={c})"
        if lineage != "原始":
            hits = []
            for tname, tp in take_pcm.items():
                m = region_match(x, tp)
                if m["corr"] >= AUP3_CORR_MIN:
                    hits.append({"take": f"{AUP3_DIR}/{tname}", **m})
            ev["aup3_hits"] = hits
            if hits:
                lineage, why = "aup3 导出", f"命中提取件区域 {len(hits)} 处(max corr={max(h['corr'] for h in hits)})"
        ev["lineage"] = lineage
        ev["lineage_reason"] = why
        report["files"].append(ev)
    out = bench / GENEALOGY_DIR / "genealogy.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return report


# ---------------------------------------------------------------- 择段与名册
def pool_build(bench: Path, genealogy: dict) -> list:
    """原始档池 = 「原始」分档件 ∪ aup3 提取 takes;PCM 归一哈希去重。"""
    bench = Path(bench)
    entries = []
    for ev in genealogy["files"]:
        if ev["lineage"] == "原始":
            entries.append({"kind": "a_family", "file": ev["file"],
                            "pcm_sha256": ev["pcm_sha256"], "canon": True})
    for tname, tv in genealogy["takes"].items():
        entries.append({"kind": "aup3_take", "file": f"{AUP3_DIR}/{tname}",
                        "pcm_sha256": pcm_sha256(bench / AUP3_DIR / tname), "canon": True})
    seen = {}
    for e in entries:  # 去重:同内容保首见(demo 原始件先于提取件入列=正本优先)
        h = e["pcm_sha256"]
        if h in seen:
            seen[h]["aliases"].append(e["file"])
        else:
            e = dict(e, aliases=[])
            seen[h] = e
    return list(seen.values())


def stratified_select(pool: list, k_per_tier: int = 2) -> list:
    """分层择段(修订一成文):噪声底升序(并列 PCM 哈希序)→ 连续三档 →
    各档取序首 k=2。返回入选 6 段(含 tier 列)。"""
    scored = []
    for e in pool:
        nf = noise_floor_ffmpeg(Path(e["_abs"]))
        e = dict(e, noise_floor=nf)
        if not nf["estimable"]:
            continue
        scored.append(e)
    scored.sort(key=lambda e: (e["noise_floor"]["noise_floor_astats_rms_dbfs"], e["pcm_sha256"]))
    n = len(scored)
    if n < 3 * k_per_tier:
        raise RuntimeError(f"原始档池可估段 {n} < 6(修订二:差额补足/名册冻结延期报经理)")
    t1 = (n + 2) // 3
    t2 = t1 + n // 3
    tiers = (["low"] * t1) + (["mid"] * (t2 - t1)) + (["high"] * (n - t2))
    for e, tier in zip(scored, tiers):
        e["tier"] = tier
    picked = []
    for tier in ("low", "mid", "high"):
        got = [e for e in scored if e["tier"] == tier][:k_per_tier]
        picked.extend(got)
    return picked


# ---------------------------------------------------------------- 名册组装
def build_roster(bench: Path, out_path: Path) -> dict:
    """31 段名册(修订一:样本段=13 真实段 A+18 合成段;总数 31)。

    基底六段出自原始档池(「原始」分档件 ∪ aup3 提取 takes,去重后分层)。
    基底若为 A 族成员则兼入样本段(标注 tier/基座用途);若为 aup3 提取件,
    则登记于 bases 基底注册表(不计入 31 样本段——其自身不被测,只作所配
    合成段的 paired_reference;31 计数口径自洽于修订一「总数 31」与 #2/#4)。
    原始档池 <6 可估段 → 修订二延期程序(抛错报经理,不降格凑数)。
    """
    bench = Path(bench)
    genealogy = genealogy_check(bench)
    pool = pool_build(bench, genealogy)
    for e in pool:
        e["_abs"] = str(bench / e["file"])
    picked = stratified_select(pool)
    segments = []
    for i, ev in enumerate(sorted(genealogy["files"], key=lambda e: e["file"]), 1):
        seg = {"segment_id": f"A-{i:02d}", "family_type": "real_a",
               "family": "真实段A", "file": ev["file"], "sha256": ev["sha256"],
               "pcm_sha256": ev["pcm_sha256"], "duration_s": ev["duration_s"],
               "sample_rate": ev["sample_rate"], "lineage": ev["lineage"],
               "lineage_reason": ev["lineage_reason"], "paired_reference": None,
               "use": "真实轨非配对指标(噪声底下降旁证/CER/LSD/带宽)+样听"}
        segments.append(seg)
    by_file = {sg["file"]: sg for sg in segments}
    bases = []      # 基底注册表(A 族成员与提取件统一形态)
    take_idx = 0
    for j, e in enumerate(sorted(picked, key=lambda x: x["file"]), 1):
        if e["kind"] == "a_family":
            seg = by_file[e["file"]]
            seg["use"] += ";合成基座(配对基底,分层=" + e["tier"] + ")"
            seg["tier"] = e["tier"]
            seg["noise_floor_est"] = e["noise_floor"]
            base = {"base_id": seg["segment_id"], "kind": "a_family",
                    "file": seg["file"], "sha256": seg["sha256"],
                    "pcm_sha256": seg["pcm_sha256"], "duration_s": seg["duration_s"],
                    "sample_rate": seg["sample_rate"], "tier": e["tier"],
                    "noise_floor_est": e["noise_floor"], "aliases": e["aliases"]}
        else:
            take_idx += 1
            f = bench / e["file"]
            base = {"base_id": f"T-{take_idx:02d}", "kind": "aup3_take",
                    "file": e["file"], "sha256": sha256_file(f),
                    "pcm_sha256": e["pcm_sha256"],
                    "duration_s": duration_s(f),
                    "sample_rate": int(subprocess.run(
                        ["ffprobe", "-v", "error", "-show_entries", "stream=sample_rate",
                         "-of", "csv=p=0", str(f)], capture_output=True, text=True).stdout.strip()),
                    "tier": e["tier"], "noise_floor_est": e["noise_floor"],
                    "aliases": e["aliases"]}
            base["lineage"] = "aup3 原始提取 take(原始档池;修订二补足通道)"
        base["base_index"] = j
        bases.append(base)
    s_idx = 0
    for base in sorted(bases, key=lambda b: b["base_index"]):
        for family in ("white", "pink", "hum"):
            s_idx += 1
            rel = f"{CORPUS_DIR}/{base['base_id']}_{family}_10dB.wav"
            segments.append({
                "segment_id": f"S-{s_idx:02d}", "family_type": "synthetic",
                "family": "合成加噪段", "file": rel, "base_id": base["base_id"],
                "noise_family": family, "snr_nominal_db": 10.0,
                "paired_reference": base["file"], "use": "配对轨三维指标权威",
                "lineage": "合成(base=" + base["base_id"] + ")"})
    roster = {
        "roster_version": "1.0(P0 冻结候选)",
        "total": len(segments),
        "count_check": {"real_a": 13, "synthetic": 18, "expected_total": 31,
                        "bases_registry": len(bases)},
        "measure_fs": MEASURE_FS,
        "snr_nominal_db": 10.0,
        "seed_roster": 20260919,
        "selection_rule": {
            "pool": "A 族「原始」分档件 ∪ 口播.aup3 提取 takes;PCM 归一哈希去重",
            "noise_floor": "只读滤镜法:silencedetect(先 -35dB/0.3s,无窗降 -45dB/0.2s)"
                           "静默窗 → 逐窗 atrim+astats RMS(滤镜侧裁剪)→ 逐窗中位",
            "stratify": "按噪声底升序(并列取 PCM 哈希字典序)连续切低/中/高三档,各档取序首 2",
            "genealogy": "三分档=原始/aup3 导出/不明;合成基底只取「原始」;判据阈值"
                         f"RAW: 时长差≤{RAW_DUR_TOL_S}s 且谱相关≥{RAW_CORR_MIN}(冻结前校准注记:"
                         "0.995→0.99,依据同一性聚簇 0.9947–0.9996 与非同一性 ≤0.839 的间隙);"
                         f"AUP3: 区域相关≥{AUP3_CORR_MIN}",
        },
        "commands": {
            "interpreter": "DENOISE_PY(默认 ~/miniconda3/envs/denoise/bin/python;与链同一解释器约定)",
            "genealogy": "DENOISE_PY plugins/denoise/quality/corpus.py genealogy <bench_root>",
            "roster": "DENOISE_PY plugins/denoise/quality/corpus.py roster <bench_root> <out.json>",
            "inject": "DENOISE_PY plugins/denoise/quality/noise_gen.py <roster.json> <bench_root>",
        },
        "bases": bases,
        "segments": segments,
    }
    if roster["total"] != 31:
        raise RuntimeError(f"名册总数 {roster['total']} ≠ 31(修订一计数口径),禁冻结")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(roster, ensure_ascii=False, indent=2, sort_keys=True),
                        encoding="utf-8")
    return roster


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    bench = Path(argv[2])
    if argv[1] == "genealogy":
        rep = genealogy_check(bench)
        lineages = {}
        for ev in rep["files"]:
            lineages[ev["lineage"]] = lineages.get(ev["lineage"], 0) + 1
        print(json.dumps({"files": len(rep["files"]), "lineages": lineages,
                          "out": str(bench / GENEALOGY_DIR / "genealogy.json")},
                         ensure_ascii=False, indent=2))
        return 0
    if argv[1] == "roster":
        out = Path(argv[3]) if len(argv) > 3 else bench / "roster.candidate.json"
        roster = build_roster(bench, out)
        from collections import Counter
        dist = Counter(s["family"] for s in roster["segments"])
        print(json.dumps({"total": roster["total"], "family_dist": dict(dist),
                          "out": str(out)}, ensure_ascii=False, indent=2))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv))
