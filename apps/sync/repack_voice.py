#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""repack_voice.py · 真人录音替换 voice 轨占位源（通用化收编自 voice-ime/repack_voice.py，D29 T4）

用途：把用户按窗口录制的真人 take（takes 目录/<窗口ID>.wav）替换进 voice 轨占位源
（final 目录/<窗口ID>.wav）。事件窗口/字幕不动。

机制（与 vtx 实现严格对齐，voice-ime cache/remake-briefs/preflight-T4.md §四）：
  · validate 硬门：源帧数 ≥ 每事件 src_in+窗长——不足直接拒绝渲染；
  · atrim 截断：超链长部分被丢弃——目标长度=事件链总长+20ms（句尾保护）；
  · 复用源特则：同一 vt_<ID> 源被多片窗引用（src_in 续接）时，用户按窗口全文连
    续朗读一份 take，脚本在第一片窗长处 ±3s 探测最低能量谷（句间气口）切分定界，
    两段按序拼接——硬切在句中会被探测谷自动纠正，找不到谷（>-45dB）回退硬切并警告；
  · 缓存坑位：vtx prepare 缓存只看存在性，替换后必须清缓存重跑（脚本结束打印序列）。

用法：
  python3 repack_voice.py [-t timeline.json] [--takes-dir ...] [--final-dir ...] [窗口ID ...]
    -t/--timeline   timeline 路径（默认 ./timeline.json；项目根由其父目录推导）
    --takes-dir     用户 take 目录（默认 <项目根>/assets/voice_takes）
    --final-dir     占位源目录（默认 <项目根>/assets/voice_tts/final）
输出格式：48kHz / mono / pcm_s16le。首次运行自动备份当前占位到 final_tts_backup/。

来源项目：voice-ime（决策 D29/D31：A-roll 素材链、D-A 双源策略配套）。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

EXIT_MISSING_DEP = 3  # 依赖缺失专用退出码（与 argparse 参数错=2、运行期报错=1 区分；A4）

try:
    import numpy as np
except ImportError:   # 不让 --help 失败；main() 入口统一给中文指引后退出（A4）
    np = None

FPS = 30
TAIL_MS = 20  # 句尾保护余量（TTS 惯例同款）


def sh(*cmd):
    r = subprocess.run(list(cmd), capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"命令失败: {' '.join(map(str, cmd))}\n{r.stderr[:500]}")
    return r


def load_wav(path):
    """→ float32 mono 48k"""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".raw")
    tmp.close()
    subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "48000",
                    "-f", "f32le", "-y", tmp.name], check=True, capture_output=True)
    x = np.fromfile(tmp.name, dtype=np.float32)
    os.unlink(tmp.name)
    return x


def write_wav(path, x):
    t = path + ".tmp.raw"
    x.astype(np.float32).tofile(t)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "f32le", "-ar", "48000", "-ac", "1",
                    "-i", t, "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1",
                    "-f", "wav", "-y", path], check=True, capture_output=True)
    os.unlink(t)


def find_valley(x, center_s, win_s=3.0):
    """在 center±win 秒内找 RMS 最低谷（帧 50ms）。返回 (谷底秒, 是否<-45dB)。"""
    h = 2400  # 50ms @48k
    lo = max(0, int((center_s - win_s) * 48000))
    hi = min(len(x), int((center_s + win_s) * 48000))
    if hi - lo < 2 * h:
        return center_s, False
    n = (hi - lo) // h
    rms = np.sqrt((x[lo:lo + n * h].reshape(-1, h) ** 2).mean(axis=1))
    db = 20 * np.log10(rms + 1e-9)
    k = int(np.argmin(db))
    return lo / 48000 + (k + 0.5) * h / 48000, bool(db[k] < -45)


def main():
    ap = argparse.ArgumentParser(description="真人录音替换 voice 轨 TTS 占位源")
    ap.add_argument("-t", "--timeline", default="./timeline.json")
    ap.add_argument("--takes-dir", default=None,
                    help="默认 <timeline 父目录>/assets/voice_takes")
    ap.add_argument("--final-dir", default=None,
                    help="默认 <timeline 父目录>/assets/voice_tts/final")
    ap.add_argument("windows", nargs="*", help="只替换指定窗口 ID（缺省=全量）")
    args = ap.parse_args()

    if np is None:  # A4：缺 numpy 时给中文指引而非裸 traceback（本脚本仅需 numpy，系统 python3 即可跑）
        print("repack_voice: 缺少第三方依赖 numpy（本脚本系统 python3 即可运行，仅需 numpy）。",
              f"\n补装：{sys.executable or 'python3'} -m pip install numpy", file=sys.stderr)
        sys.exit(EXIT_MISSING_DEP)

    ROOT = os.path.dirname(os.path.abspath(args.timeline))
    TL = os.path.abspath(args.timeline)
    TAKES = args.takes_dir or os.path.join(ROOT, "assets", "voice_takes")
    FINAL = args.final_dir or os.path.join(ROOT, "assets", "voice_tts", "final")
    BACKUP = os.path.join(FINAL + "_tts_backup")
    only = set(args.windows)

    tl = json.load(open(TL))
    vtrack = next(tr for tr in tl["audio_tracks"] if tr["name"] == "voice")
    by_src = {}
    for e in vtrack["events"]:
        by_src.setdefault(e["src"], []).append(e)

    if os.path.isdir(FINAL) and not os.path.isdir(BACKUP):
        shutil.copytree(FINAL, BACKUP)
        print(f"✓ 已备份占位 → {os.path.relpath(BACKUP, ROOT)}/（首次）")

    done, missing = [], []
    for src_id, evs in sorted(by_src.items()):
        assert src_id.startswith("vt_"), f"voice 轨混入非 vt_ 源: {src_id}"
        wid = src_id[3:]
        if only and wid not in only:
            continue
        take = os.path.join(TAKES, f"{wid}.wav")
        if not os.path.exists(take):
            missing.append(wid)
            continue
        evs.sort(key=lambda e: e.get("src_in", 0))
        chain_f = max(e.get("src_in", 0) + (e["out"] - e["in"]) for e in evs)
        target = int(round((chain_f / FPS + TAIL_MS / 1000) * 48000))

        x = load_wav(take)
        if len(evs) > 1:  # 复用源：第一片窗长处谷切
            cut_f = evs[0].get("src_in", 0) + (evs[0]["out"] - evs[0]["in"])
            cut_s, ok = find_valley(x, cut_f / FPS)
            cut = int(cut_s * 48000)
            x = np.concatenate([x[:cut], x[cut:]])  # 谷切仅定界，两段顺序拼接
            note = f"谷切@{cut_s:.2f}s（{'谷底-45dB 以下，安全' if ok else '⚠ 无明显静音谷，回退硬切 @%.2fs，请人工复核第一片结尾' % (cut_f / FPS)}）"
        else:
            note = ""
        if len(x) > target:  # 超链长：保链长截断（对窗体检应已拦）
            print(f"  ⚠ {wid}: 录音 {len(x)/48000:.2f}s 超链长 {target/48000:.2f}s，"
                  f"尾部 {len(x)-target} 采样将被丢弃（重录或接受）")
            x = x[:target]
        if len(x) < target:
            x = np.pad(x, (0, target - len(x)))
        os.makedirs(FINAL, exist_ok=True)
        out = os.path.join(FINAL, f"{wid}.wav")
        write_wav(out, x)
        assert abs(len(x) - target) <= 1
        done.append(wid)
        print(f"✓ {wid}: {target/48000:.2f}s（链长 {chain_f}f+{TAIL_MS}ms）{note}")

    if missing:
        print(f"\n⚠ takes 目录缺 {len(missing)} 窗: {' '.join(missing)}"
              f"（对应窗口保持占位）")
    print(f"\n替换 {len(done)} 窗完成。后续命令（缺一不可，项目根执行）：")
    print(f"  rm {ROOT}/cache/audio/vt_*.wav")
    print(f"  python3 ~/.agents/skills/video/vtx/vtx.py -t {args.timeline} prepare")
    print(f"  python3 ~/.agents/skills/video/vtx/vtx.py -t {args.timeline} audio   # voice 变必重跑")
    print(f"  （v2：vtx audio 已内置限幅，无需手动 post_loudness）")
    print(f"  python3 ~/.agents/skills/video/vtx/vtx.py -t {args.timeline} mux")
    print(f"  python3 ~/.agents/skills/video/vtx/vtx.py -t {args.timeline} qc && "
          f"python3 ~/.agents/skills/video/vtx/vtx.py -t {args.timeline} audit")


if __name__ == "__main__":
    main()
