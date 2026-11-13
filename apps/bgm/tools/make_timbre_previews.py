#!/usr/bin/env python3
"""make_timbre_previews —— 逐音色库生成"典型音色串烧"MP3 预览。

产物：catalog/preview/<slug>.mp3（每库一件）+ INDEX.md（时间码索引表，脚本再生成）。

设计要点：
- 预览声与产线同源：一律经 bgm_gen 真实引擎渲染（synth/sf2/sfz/sample），
  听到的=用工程引用该音色时实际得到的声音（同引擎同渲染路径）。
- 每库内按"乐器 → 固定乐句"排列：旋律件=C 大调七音短句（mf，~2.5s），
  持续/铺底件=柱式和弦，打击件=节奏型——库间可直接横向对比。
- 逐段峰值归一化（-6 dBFS）：VSCO2 电平缺陷族（-54~-77dB）等库间响度差
  会被抹平，听感公平；代价=原始电平特征丢失，电平补偿请查 catalog/timbres.md。
- 确定性：引擎逐位确定；ffmpeg 解码/编码（f32le / libmp3lame 160k）同版本确定
  ——整脚本可重跑再生成（中间件落 out/preview_build/，gitignored）。

用法：python3 tools/make_timbre_previews.py [--only <slug>]
（--only 只重生成指定库，slug 见 PREVIEWS 键）
"""

import os
import subprocess
import sys
import wave

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from bgm_gen.engines import run_engine                     # noqa: E402

SR = 44100
OUT_DIR = os.path.abspath(os.path.join("out", "timbre-previews"))
BUILD_DIR = os.path.join(OUT_DIR, "build")
from bgm_gen.paths import assets_root, local_runtime, samples_root  # noqa: E402

ATTRIBUTIONS = {
    "synth": "自产 synthlib 程序化渲染；MIT，Copyright (c) 2026 Scoreweft contributors。",
    "sf2_fluidr3": "FluidR3 by Frank Wen (MIT) — https://www.fluidsynth.org/soundfonts/；完整版权与许可声明见 [NOTICE](<../../NOTICE.md>)。",
    "sf2_generaluser": "GeneralUser GS by S. Christian Collins (GeneralUser GS License v2.0) — https://schristiancollins.com/generaluser.php；音乐使用许可与源样本边界见 [NOTICE](<../../NOTICE.md>)。",
    "sfz_vsco2_ce": "VSCO 2 CE / Versilian Studios 及贡献者 (CC0) — https://github.com/sgossner/VSCO-2-CE/tree/SFZ；无强制署名。",
    "sfz_karoryfer": "Karoryfer bigcat cello / Meatbass / War Tuba (CC0) — https://www.karoryfer.com/karoryfer-samples；无强制署名。",
    "sfz_salamander": "钢琴音源：Salamander Grand Piano V3 by Alexander Holm (CC-BY 3.0) — https://archive.org/details/SalamanderGrandPianoV3；SFZ 化 kinwie；许可 https://creativecommons.org/licenses/by/3.0/ 。",
    "sfz_vpo": "弦乐音源：Virtual Playing Orchestra by Paul Battersby — https://virtualplaying.com；音乐自由使用，采样来源分层许可详见 [NOTICE](<../../NOTICE.md>)。",
    "sample_kenney": "Kenney (CC0) — https://kenney.nl/assets；无强制署名。",
}

PREVIEW_NOTICE = "> 这些预览由固定乐句渲染/触发，经归一化、截断、淡入淡出、拼接与 MP3 编码（改动说明）；第三方权利与署名详见 [NOTICE](<../../NOTICE.md>)。Mixkit/Sonniss 裸采样预览未随开源版发布，可在核验条款后本地自建。"

# ---- 乐句素材（beats @120bpm，beat=0.5s） --------------------------------
MEL = [[60, 0.0, 0.45, 100], [64, 0.5, 0.45, 100], [67, 1.0, 0.45, 100],
       [72, 1.5, 0.45, 100], [67, 2.0, 0.45, 100], [64, 2.5, 0.45, 100],
       [60, 3.0, 1.8, 100]]
MEL_DYN = [[p, b, d, v] for (p, b, d, _), v
           in zip(MEL, (62, 74, 86, 98, 110, 100, 88))]
CHORD = [[48 + i, 0.06 * i, 4.0, 92] for i in (0, 7, 12, 16, 19)]  # 微错位=模拟合奏起音


def tr(notes, semis):
    return [[p + semis, b, d, v] for p, b, d, v in notes]


def _ffmpeg(args, *, data=None):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error"] + args,
                       capture_output=True, input=data)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败：{' '.join(args[:6])}…\n{r.stderr.decode()[:400]}")
    return r.stdout


def render_snippet(name, engine, payload):
    """引擎渲染一段 → 立体声 float64 数组。"""
    spec = {"engine": engine, "spec_version": "1", "sr": SR,
            "out": os.path.join(BUILD_DIR, f"{name}.wav"), "payload": payload}
    rt = local_runtime()
    rt["assets_root"] = assets_root(rt)
    rt["samples_root"] = samples_root(rt)
    # 任何成功解析出的 sf2 根（env > runtime > 仓库 assets/ fallback）都注入默认
    # GeneralUser 映射——纯 fallback 场景 resolve_sf2 不再报"未知 sf2 名字"。
    generaluser = os.path.join(rt["assets_root"], "sf2", "GeneralUser_GS.sf2")
    if os.path.isfile(generaluser):
        rt["sf2"] = dict(rt.get("sf2") or {})
        rt["sf2"]["GeneralUser_GS"] = generaluser
    run_engine(spec, runtime=rt)
    raw = _ffmpeg(["-i", spec["out"], "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"])
    x = np.frombuffer(raw, dtype="<f4").astype(np.float64).reshape(-1, 2)
    return x


def decode_hit(path, at, gain_db=0.0):
    """sample 引擎用的 hit 描述（文件须存在，调用方先校验）。"""
    return {"file": path, "at": at, "gain_db": gain_db}


def snippet(x, peak=0.5, cap_s=5.0, fade_ms=15):
    """归一化 + 截断 + 淡入淡出 + 0.4s 间隔静音。"""
    n_cap = int(cap_s * SR)
    if len(x) > n_cap:
        x = x[:n_cap]
    m = np.abs(x).max()
    if m > 1e-9:
        x = x / m * peak
    n_f = int(SR * fade_ms / 1000)
    if n_f and len(x) > 2 * n_f:
        env = np.ones(len(x))
        env[:n_f] = np.linspace(0, 1, n_f)
        env[-n_f:] = np.linspace(1, 0, n_f)
        x = x * env[:, None]
    return np.vstack([x, np.zeros((int(0.4 * SR), 2))])


def sfz_notes(sfz, notes, bpm=120):
    return {"sfz": sfz, "notes": notes, "bpm": bpm}


def sf2_notes(sf2, program, notes, bpm=120):
    return {"sf2": sf2, "bank": 0, "program": program, "notes": notes, "bpm": bpm}


def synth_notes(program, notes, bpm=120):
    return {"program": program, "notes": notes, "bpm": bpm}


def mel_entry(label, engine, payload):
    return (label, engine, payload)


def first_files(directory, suffix, count):
    fs = sorted(f for f in os.listdir(directory) if f.endswith(suffix))
    return [os.path.join(directory, f) for f in fs[:count]]


# ---- Kenney 盘点（环境变量或既有 runtime.json；缺件跳过） ----------------
SAMPLES_ROOT = samples_root()
KENNEY_ROOT = os.path.join(SAMPLES_ROOT, "_raw", "kenney")


def build_previews():
    previews = []  # (slug, 标题, [entry…])；entry=(label, engine, payload)

    # ① synth（synthlib 六音色全览）
    previews.append(("synth", "synth · 程序化合成器（synthlib 六音色）", [
        ("kick · 底鼓（四分律动）", "synth", synth_notes("kick",
            [[36, 0, 0.25, 127], [36, 1, 0.25, 100], [36, 2, 0.25, 127], [36, 3, 0.25, 100]])),
        ("snare · 军鼓（2/4 反拍）", "synth", synth_notes("snare",
            [[38, 0, 0.25, 110], [38, 2, 0.25, 110]])),
        ("hat · 踩镲（八分）", "synth", synth_notes("hat",
            [[42, i * 0.5, 0.2, 90] for i in range(8)])),
        ("fm_ep · FM 电钢（旋律）", "synth", synth_notes("fm_ep", MEL)),
        ("bass · 合成贝斯（旋律）", "synth", synth_notes("bass", tr(MEL, -24))),
        ("lead · 合成主音（旋律）", "synth", synth_notes("lead", MEL)),
    ]))

    # ②③ 两个 GM 库：同 12 program 同乐句 → 直接 A/B
    gm_progs = [(0, "Acoustic Grand 声学大钢琴", "dyn"), (4, "Rhodes 电钢", "mel"),
                (11, "Vibraphone 颤音琴", "mel"), (16, "Drawbar Organ 拉杆风琴", "mel"),
                (24, "Nylon Guitar 尼龙吉他", "mel"), (33, "Finger E.Bass 电贝斯", "bass"),
                (40, "Violin 小提琴", "mel"), (48, "Strings Ensemble 弦乐合奏", "chord"),
                (56, "Trumpet 小号", "mel"), (65, "Alto Sax 中音萨克斯", "mel"),
                (73, "Flute 长笛", "mel"), (88, "Warm Pad 暖铺底", "chord")]
    for sf2_name, title in (("FluidR3_GM", "sf2 · FluidR3_GM（系统 GM 128 音色）"),
                            ("GeneralUser_GS", "sf2 · GeneralUser GS（assets GM 128 音色）")):
        entries = []
        for prog, label, mode in gm_progs:
            if mode == "bass":
                notes = tr(MEL, -24)
            elif mode == "chord":
                notes = CHORD
            elif mode == "dyn":
                notes = MEL_DYN
            else:
                notes = MEL
            entries.append((f"p{prog:03d} {label}", "sf2", sf2_notes(sf2_name, prog, notes)))
        previews.append((f"sf2_{sf2_name.split('_')[0].lower()}", title, entries))

    # ④ VSCO2 CE 管弦
    v = "VSCO2/"
    previews.append(("sfz_vsco2_ce", "sfz · VSCO2 CE 社区版管弦（CC0）", [
        ("独奏小提琴 SViolinVib（力度弧）", "sfz", sfz_notes(v + "SViolinVib.sfz", MEL_DYN)),
        ("小提琴组 ViolinEnsSusVib（和弦）", "sfz", sfz_notes(v + "ViolinEnsSusVib.sfz", CHORD)),
        ("中提琴组 ViolaEnsSusVib（和弦）", "sfz", sfz_notes(v + "ViolaEnsSusVib.sfz", CHORD)),
        ("大提琴组 CelloEnsSusVib（和弦）", "sfz", sfz_notes(v + "CelloEnsSusVib.sfz", CHORD)),
        ("低音提琴 ContrabassSusVB（旋律）", "sfz", sfz_notes(v + "ContrabassSusVB.sfz", tr(MEL, -24))),
        ("长笛 FluteSusVib", "sfz", sfz_notes(v + "FluteSusVib.sfz", MEL)),
        ("双簧管 OboeSusVib", "sfz", sfz_notes(v + "OboeSusVib.sfz", MEL)),
        ("单簧管 ClarinetSus", "sfz", sfz_notes(v + "ClarinetSus.sfz", MEL)),
        ("大管 BassoonSus", "sfz", sfz_notes(v + "BassoonSus.sfz", tr(MEL, -12))),
        ("圆号 FHornSus", "sfz", sfz_notes(v + "FHornSus.sfz", MEL)),
        ("竖琴 Harp", "sfz", sfz_notes(v + "Harp.sfz", MEL)),
        ("立式钢琴 UprightPiano（力度弧）", "sfz", sfz_notes(v + "UprightPiano.sfz", MEL_DYN)),
        ("定音鼓滚奏 TimpaniRolls", "sfz", sfz_notes(v + "TimpaniRolls.sfz", [[36, 0, 4, 110]])),
        ("钟琴 Glockenspiel（库内电平极低，已归一化）", "sfz",
         sfz_notes(v + "Glockenspiel.sfz", tr(MEL, 12))),
    ]))

    # ⑤ karoryfer 三件套（大提琴×大贝斯×战争大号）
    k = "karoryfer/"
    bc = k + "bigcat_cello/Karoryfer_Bigcat_cello/Programs/"
    mb = k + "meatbass/Meatbass/Programs/"
    wt = k + "war_tuba/War_tuba/Programs/"
    previews.append(("sfz_karoryfer", "sfz · Karoryfer 三件套（CC0：大提琴/大贝斯/战争大号）", [
        ("bigcat 大提琴 · 弓奏力度层", "sfz", sfz_notes(bc + "01- Bowed (velocity layer).sfz", MEL_DYN)),
        ("bigcat 大提琴 · 拨奏", "sfz", sfz_notes(bc + "03- Plucked.sfz", MEL)),
        ("meatbass 大贝斯 · 弓奏三层力度", "sfz", sfz_notes(mb + "02_arco_3vel.sfz", tr(MEL, -24))),
        ("meatbass 大贝斯 · 拨奏", "sfz", sfz_notes(mb + "04_pizz.sfz", tr(MEL, -24))),
        ("war_tuba · 独奏复音", "sfz", sfz_notes(wt + "2-solo-poly.sfz", tr(MEL, -24))),
        ("war_tuba · 双人复音（和弦）", "sfz", sfz_notes(wt + "4-duo-poly.sfz", tr(CHORD, -24))),
    ]))

    # ⑥ SalamanderGrand 大钢琴
    previews.append(("sfz_salamander", "sfz · SalamanderGrand V3 大钢琴（CC-BY）", [
        ("旋律（pp→f 力度弧，vel 62-110）", "sfz",
         sfz_notes("SalamanderGrand/Salamander Grand Piano V3.sfz", MEL_DYN)),
        ("柱式和弦强奏", "sfz",
         sfz_notes("SalamanderGrand/Salamander Grand Piano V3.sfz", CHORD)),
    ]))

    # ⑦ VPO 弦乐五声部
    vp = "VPO/Strings/"
    previews.append(("sfz_vpo", "sfz · VPO 虚拟演奏管弦弦乐（分层许可，SEC-sustain）", [
        ("第一小提琴 1st-violin", "sfz", sfz_notes(vp + "1st-violin-SEC-sustain.sfz", MEL)),
        ("第二小提琴 2nd-violin", "sfz", sfz_notes(vp + "2nd-violin-SEC-sustain.sfz", MEL)),
        ("中提琴 viola", "sfz", sfz_notes(vp + "viola-SEC-sustain.sfz", MEL)),
        ("大提琴 cello", "sfz", sfz_notes(vp + "cello-SEC-sustain.sfz", tr(MEL, -12))),
        ("低音提琴 bass", "sfz", sfz_notes(vp + "bass-SEC-sustain.sfz", tr(MEL, -24))),
        ("全弦乐齐奏 all-strings（和弦收束）", "sfz",
         sfz_notes(vp + "all-strings-SEC-sustain.sfz", CHORD)),
    ]))

    # ⑧ Kenney 采样包（每包 3 发）
    entries = []
    if os.path.isdir(KENNEY_ROOT):
        for pack in sorted(d for d in os.listdir(KENNEY_ROOT)
                           if d.startswith("kenney_") and os.path.isdir(os.path.join(KENNEY_ROOT, d))):
            audio = os.path.join(KENNEY_ROOT, pack, "Audio")
            if not os.path.isdir(audio):          # 少数包无 Audio/ 子目录（如 voiceover-pack）
                audio = os.path.join(KENNEY_ROOT, pack)
            files = first_files(audio, ".ogg", 3)
            if files:
                rel = [os.path.relpath(f, SAMPLES_ROOT) for f in files]
                hits = [decode_hit(r, 0.8 * i, -6) for i, r in enumerate(rel)]
                entries.append((f"{pack[len('kenney_'):]}（3 发代表件）", "sample",
                                {"hits": hits, "length_s": 0.8 * len(files) + 1.2}))
    if entries:
        previews.append(("sample_kenney", "sample · Kenney 全 10 包（CC0，one-shot）", entries))

    # Mixkit/Sonniss GDC：开源版不含裸采样预览，可本地自建；
    # 获取、生成和发布前须自行核验源库条款，禁止将受限采样作为工具素材再分发。

    return previews


def mmss(t):
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


def main(argv):
    import argparse
    global OUT_DIR, BUILD_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default=None)
    parser.add_argument("-o", "--out", default=OUT_DIR, help="Output directory (writes media)")
    args = parser.parse_args(argv[1:])
    only = args.only
    OUT_DIR = os.path.abspath(args.out)
    BUILD_DIR = os.path.join(OUT_DIR, "build")
    os.makedirs(BUILD_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    index_rows = ["# catalog/preview —— 音色库试听预览索引", "",
                  "> 由 `tools/make_timbre_previews.py` 生成（引擎真实渲染，重跑确定性再生）。",
                  "> **逐段峰值归一化（-6 dBFS）**：库间原始电平差异已被抹平（如 VSCO2 电平缺陷族），",
                  "> 电平补偿请查 [../timbres.md](../timbres.md)。每行=一个乐器，时间码可直接拖动播放。",
                  PREVIEW_NOTICE, ""]

    for slug, title, entries in build_previews():
        if only and slug != only:
            continue
        chunks, rows, t = [], [], 0.0
        for i, (label, engine, payload) in enumerate(entries):
            try:
                x = render_snippet(f"{slug}_{i:02d}", engine, payload)
            except Exception as e:  # 缺件/引擎拒载：跳过并在索引披露
                # 失败详情可能含本地工具/素材的绝对路径，仅留在本地控制台。
                rows.append(f"| 跳过 | {label} | 渲染失败（{type(e).__name__}），详见本地日志 |")
                print(f"[skip] {slug} · {label}: {str(e)[:120]}")
                continue
            s = snippet(x)
            chunks.append(s)
            rows.append(f"| {mmss(t)} | {label} | {engine} |")
            t += len(s) / SR
        if not chunks:
            print(f"[warn] {slug} 无可用段落，跳过")
            continue
        mix = np.vstack(chunks)
        pcm = (np.clip(mix, -1, 1) * 32767).astype("<i2")
        wav_p = os.path.join(BUILD_DIR, f"{slug}_master.wav")
        with wave.open(wav_p, "wb") as w:
            w.setnchannels(2), w.setsampwidth(2), w.setframerate(SR)
            w.writeframes(pcm.tobytes())
        mp3_p = os.path.join(OUT_DIR, f"{slug}.mp3")
        _ffmpeg(["-i", wav_p, "-codec:a", "libmp3lame", "-b:a", "160k", "-y", mp3_p])
        index_rows += [f"## {title}", "", f"**文件**：`{slug}.mp3` · 时长 {mmss(t)} · {len(chunks)} 段", "",
                       "**来源与义务**：" + ATTRIBUTIONS[slug], "",
                       "| 起 | 乐器/段落 | 引擎 |", "|---|---|---|"] + rows + [""]
        print(f"[ok] {slug}.mp3  {mmss(t)}  {len(chunks)} 段")

    with open(os.path.join(OUT_DIR, "INDEX.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(index_rows) + "\n")
    print("索引 → " + os.path.join(OUT_DIR, "INDEX.md"))


if __name__ == "__main__":
    main(sys.argv)
