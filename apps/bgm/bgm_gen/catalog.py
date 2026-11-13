"""catalog：音色目录——扫描音色源 → catalog dict → Markdown（P1 交付）。

命令入口：`bgm catalog`（人读 Markdown）/ `bgm catalog --json`（JSON）/
`bgm catalog --write`（落盘 <仓库根>/catalog/{timbres.md,timbres.json}）。

数据来源（全部真实磁盘/代码事实，不手写清单）：
    synth   bgm_gen/engines/synth.py 的 PERC/TONAL 白名单（代码事实，非磁盘）
    sf2     runtime.json sf2 表 + 文件存在性 + 内嵌 GM 128 标准音色名（0..127）
    sfz     assets_root/sfz/ 递归 *.sfz（四库，许可证联查 assets.json）
    sample  samples_root（Kenney 10 包>类目）+ assets_root/samples/drums_gdc
            （manifest.json）+ _raw 其余原始包（mixkit/sonniss/99sounds）包级计数
非静态音色源（ai/demucs/master）无固定音色可列，以 engines_note 说明段带过。

模块级 import 只用标准库（仓库惯例：系统 python3 可直接跑；引擎层 lazy import）。
"""

import json
import os
from datetime import date

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))       # bgm_gen/
_REPO_ROOT = os.path.dirname(_PKG_DIR)                      # scoreweft 仓库根/

AUDIO_EXT = (".ogg", ".wav", ".mp3", ".flac")

# GM Level 1 标准旋律乐器名（bank 0, program 0..127）——两库（FluidR3_GM /
# GeneralUser_GS）均 GM 兼容，bank 128 为 GM 打击组（channel 10）。
GM_PROGRAMS = [
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano",
    "Honky-tonk Piano", "Electric Piano 1", "Electric Piano 2", "Harpsichord",
    "Clavi", "Celesta", "Glockenspiel", "Music Box", "Vibraphone", "Marimba",
    "Xylophone", "Tubular Bells", "Dulcimer", "Drawbar Organ", "Percussive Organ",
    "Rock Organ", "Church Organ", "Reed Organ", "Accordion", "Harmonica",
    "Tango Accordion", "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)",
    "Electric Guitar (jazz)", "Electric Guitar (clean)", "Electric Guitar (muted)",
    "Overdriven Guitar", "Distortion Guitar", "Guitar harmonics", "Acoustic Bass",
    "Electric Bass (finger)", "Electric Bass (pick)", "Fretless Bass",
    "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2", "Violin",
    "Viola", "Cello", "Contrabass", "Tremolo Strings", "Pizzicato Strings",
    "Orchestral Harp", "Timpani", "String Ensemble 1", "String Ensemble 2",
    "SynthStrings 1", "SynthStrings 2", "Choir Aahs", "Voice Oohs", "Synth Voice",
    "Orchestra Hit", "Trumpet", "Trombone", "Tuba", "Muted Trumpet", "French Horn",
    "Brass Section", "SynthBrass 1", "SynthBrass 2", "Soprano Sax", "Alto Sax",
    "Tenor Sax", "Baritone Sax", "Oboe", "English Horn", "Bassoon", "Clarinet",
    "Piccolo", "Flute", "Recorder", "Pan Flute", "Blown Bottle", "Shakuhachi",
    "Whistle", "Ocarina", "Lead 1 (square)", "Lead 2 (sawtooth)",
    "Lead 3 (calliope)", "Lead 4 (chiff)", "Lead 5 (charang)", "Lead 6 (voice)",
    "Lead 7 (fifths)", "Lead 8 (bass + lead)", "Pad 1 (new age)", "Pad 2 (warm)",
    "Pad 3 (polysynth)", "Pad 4 (choir)", "Pad 5 (bowed)", "Pad 6 (metallic)",
    "Pad 7 (halo)", "Pad 8 (sweep)", "FX 1 (rain)", "FX 2 (soundtrack)",
    "FX 3 (crystal)", "FX 4 (atmosphere)", "FX 5 (brightness)", "FX 6 (goblins)",
    "FX 7 (echoes)", "FX 8 (sci-fi)", "Sitar", "Banjo", "Shamisen", "Koto",
    "Kalimba", "Bag pipe", "Fiddle", "Shanai", "Tinkle Bell", "Agogo",
    "Steel Drums", "Woodblock", "Taiko Drum", "Melodic Tom", "Synth Drum",
    "Reverse Cymbal", "Guitar Fret Noise", "Breath Noise", "Seashore",
    "Bird Tweet", "Telephone Ring", "Helicopter", "Applause", "Gunshot",
]

# synth 音色的可调 params（synthlib 函数签名事实，payload.params 透传 kwargs）
# 与一句话描述（照 synthlib.py 注释/实现归纳）
_SYNTH_PARAMS = {
    "kick": {"params": [], "desc": "808 血统底鼓：音高 155Hz 下扫 45Hz"},
    "snare": {"params": [], "desc": "军鼓：185/330Hz body + 带通噪声 snap"},
    "hat": {"params": ["decay"],
            "desc": "踩镲：7kHz 以上带通噪声 × 指数衰减"},
    "fm_ep": {"params": ["ratio", "idx", "bright_decay"],
              "desc": "FM 电钢琴（DX7 tine 两算子近似，亮度随调制指数衰减）"},
    "bass": {"params": [], "desc": "合成 bass：基频+八度泛音，tanh 饱和"},
    "lead": {"params": [], "desc": "合成 lead：双振荡器微失谐 + 迟到颤音"},
    "nylon": {"params": ["bright", "pick", "body", "decay_ref"],
              "desc": "尼龙弦吉他单音拨弦（Karplus-Strong 拨弦物理模型：延迟线弦"
                      "+梳状拨弦+FFT 琴体共振；默认 bright 0.55 激励亮度 / pick 0.26 "
                      "拨弦位置 / body 1.0 琴体共鸣 / decay_ref 6.0 参考音 G2 的 "
                      "T60 秒——与 synthlib.nylon 签名默认值一致）"},
}


def _load_runtime():
    from .paths import effective_runtime
    return effective_runtime()


def _payload_doc(engine):
    """提取引擎 payload 参数文档（v3 R1-W1：由描述符驱动——
    engines/descriptors/<engine>.json 的参数表，每参数一行）——md 引用示例用。

    旧实现照抄引擎 docstring 的 payload 段；描述符化后 payload 文档与
    validate_payload 校验同源（防漂移四件套）。
    """
    path = os.path.join(_PKG_DIR, "engines", "descriptors", f"{engine}.json")
    try:
        with open(path, encoding="utf-8") as f:
            desc = json.load(f)
    except Exception:
        return ""
    cov = desc.get("coverage") or {}
    lines = [f"payload（描述符 v{desc.get('version')} · tier {cov.get('tier', '?')}）:"]
    for p in desc.get("parameters") or []:
        req = "*" if "required" in (p.get("flags") or []) else " "
        parts = [f"  {req}{p['id']:<14} {p.get('type', '?'):<6}"]
        extra = []
        if p.get("default") is not None:
            extra.append(f"默认 {p['default']}")
        if p.get("min") is not None or p.get("max") is not None:
            lo = p.get("min", "…")
            extra.append(f"范围 {lo}..{p.get('max', '…')}")
        if p.get("enum"):
            extra.append("枚举 " + "/".join(str(e) for e in p["enum"]))
        if p.get("unit"):
            extra.append(p["unit"])
        if p.get("description"):
            extra.append(p["description"])
        parts.append("；".join(extra))
        lines.append("".join(parts))
    return "\n".join(lines)


# ------------------------------------------------------------ 各源扫描 ----

def _scan_synth():
    from .engines import synth as _synth          # lazy：引擎层到用时才 import
    perc, tonal = sorted(_synth.PERC), sorted(_synth.TONAL)
    timbres = {}
    for name in perc + tonal:
        meta = _SYNTH_PARAMS.get(name, {"params": [], "desc": ""})
        timbres[name] = {
            "type": "percussive" if name in _synth.PERC else "tonal",
            "pitch": "ignored" if name in _synth.PERC else "MIDI pitch → f0",
            "params": meta["params"],
            "desc": meta["desc"],
        }
    return {
        "engine": "synth",
        "module": "bgm_gen/synthlib.py",
        "count": len(timbres),
        "percussive": perc,
        "tonal": tonal,
        "timbres": timbres,
        "payload": _payload_doc("synth"),
    }


def _scan_sf2(runtime):
    fonts = {}
    for name, path in sorted((runtime.get("sf2") or {}).items()):
        ok = os.path.isfile(path)
        fonts[name] = {"path": path, "exists": ok,
                       "size_mb": round(os.path.getsize(path) / 1e6, 1) if ok else None}
    return {
        "engine": "sf2",
        "count": len(fonts),
        "fonts": fonts,
        "banks": {
            "0": "GM 128 melodic（program 0..127，标准名见 gm_programs）",
            "128": "GM 打击组（channel 10 鼓组）",
        },
        "gm_programs": list(GM_PROGRAMS),
        "shorthand": ('instrument 速记 "FluidR3_GM:0:48" = 名字:bank:program'
                      "（core/project.py track_engine_spec）"),
        "payload": _payload_doc("sf2"),
    }


def _sfz_licenses():
    """assets.json 联查：sfz/<库>/ relpath → 许可证（真实 manifest 数据）。"""
    out = {}
    try:
        with open(os.path.join(_PKG_DIR, "assets.json"), encoding="utf-8") as f:
            items = json.load(f).get("items", [])
    except Exception:
        return out
    for e in items:
        rp = e.get("relpath") or ""
        if not rp.startswith("sfz/"):
            continue
        top = rp[len("sfz/"):].split("/", 1)[0]
        lic = e.get("license") or ""
        # 同库多件（如 karoryfer 三乐器）许可名相同（首个"（"前）只留一条全文
        lic_name = lic.split("（", 1)[0].split("(", 1)[0]
        seen = out.setdefault(top, {"names": [], "texts": []})
        if lic and lic_name not in seen["names"]:
            seen["names"].append(lic_name)
            seen["texts"].append(lic)
    return {top: "；".join(v["texts"]) for top, v in out.items()}


def _scan_sfz(runtime):
    root = os.path.join(runtime.get("assets_root", ""), "sfz")
    libs = {}
    if os.path.isdir(root):
        for top in sorted(os.listdir(root)):
            top_path = os.path.join(root, top)
            if not os.path.isdir(top_path) or top.startswith((".", "_")):
                continue                                   # 跳过 _dl/ 等非库目录
            files = []
            for dirpath, dirnames, filenames in os.walk(top_path):
                dirnames.sort()
                files += [os.path.relpath(os.path.join(dirpath, fn), top_path)
                          for fn in sorted(filenames)
                          if fn.lower().endswith(".sfz")]
            if not files:
                continue
            groups = {}
            for rel in files:
                g = rel.split(os.sep, 1)[0] if os.sep in rel else "."
                groups.setdefault(g, []).append(rel)
            libs[top] = {"count": len(files),
                         "groups": dict(sorted(groups.items()))}
    licenses = _sfz_licenses()
    war_tuba_note = ("war_tuba 只用顶层 Programs 入口（1-solo-legato … 7-tubaborg，项目约定）")
    for top, info in libs.items():
        info["license"] = licenses.get(top, "")
        info["notes"] = [war_tuba_note] if top == "karoryfer" else []
    return {
        "engine": "sfz",
        "root": root,
        "exists": os.path.isdir(root),
        "total": sum(i["count"] for i in libs.values()),
        "libraries": libs,
        "aliases": {"VSCO2": "VSCO2_CE", "Karoryfer": "karoryfer"},
        "ref_rules": ("绝对路径 / 相对 assets_root/sfz / 纯文件名树内唯一匹配"
                      "（engines/sfz.py resolve_sfz）"),
        "payload": _payload_doc("sfz"),
    }


def _scan_audio_tree(root, ext):
    """递归按目录分组统计音频文件：({目录: {count, examples(≤3)}}, 总数)。

    目录键 "." = 根目录；walk 顺序 sorted → 结果确定。
    """
    groups, total = {}, 0
    if not os.path.isdir(root):
        return groups, total
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        hits = sorted(fn for fn in filenames if fn.lower().endswith(ext))
        if not hits:
            continue
        rel = os.path.relpath(dirpath, root)
        groups["." if rel == "." else rel] = {"count": len(hits),
                                              "examples": hits[:3]}
        total += len(hits)
    return dict(sorted(groups.items())), total


def _scan_sample(runtime):
    sr = runtime.get("samples_root", "")
    kenney_root = os.path.join(sr, "_raw", "kenney")

    packages = {}
    if os.path.isdir(kenney_root):
        for name in sorted(os.listdir(kenney_root)):
            pkg = os.path.join(kenney_root, name)
            if not os.path.isdir(pkg) or not name.startswith("kenney_"):
                continue
            cats, total = _scan_audio_tree(pkg, ".ogg")
            packages[name[len("kenney_"):]] = {
                "ogg": total, "categories": cats,
                "ref_prefix": f"_raw/kenney/{name}/"}
    kenney_total = sum(p["ogg"] for p in packages.values())

    # drums_gdc：assets_root/samples/ 下（samples_root 之外，须绝对路径引用）
    dg = os.path.join(runtime.get("assets_root", ""), "samples", "drums_gdc")
    kits = {}
    if os.path.isdir(dg):
        for name in sorted(os.listdir(dg)):
            d = os.path.join(dg, name)
            if not os.path.isdir(d):
                continue
            wavs = sorted(fn for fn in os.listdir(d) if fn.lower().endswith(".wav"))
            if wavs:
                kits[name] = {"count": len(wavs), "examples": wavs[:2]}
    dg_manifest = 0
    dg_path = os.path.join(dg, "manifest.json")
    if os.path.isfile(dg_path):
        try:
            with open(dg_path, encoding="utf-8") as f:
                dg_manifest = len(json.load(f))
        except Exception:
            dg_manifest = 0

    mix_cats, mix_total = _scan_audio_tree(os.path.join(sr, "_raw", "mixkit"), ".mp3")
    mix_cats.pop(".", None)                               # 根目录散文件不计类目

    sonniss_root = os.path.join(sr, "_raw", "sonniss")
    zips = []
    if os.path.isdir(sonniss_root):
        zips = sorted(fn for fn in os.listdir(sonniss_root) if fn.endswith(".zip"))
    sonniss_bytes = sum(os.path.getsize(os.path.join(sonniss_root, z))
                        for z in zips if os.path.isfile(os.path.join(sonniss_root, z)))

    n99 = 0
    p99 = os.path.join(sr, "_raw", "99sounds")
    if os.path.isdir(p99):
        for _dp, _dn, fns in os.walk(p99):
            n99 += len(fns)

    bgm_n = 0
    bgm_dir = os.path.join(sr, "bgm")
    if os.path.isdir(bgm_dir):
        bgm_n = sum(1 for fn in os.listdir(bgm_dir) if fn.lower().endswith(AUDIO_EXT))

    return {
        "engine": "sample",
        "samples_root": sr,
        "exists": os.path.isdir(sr),
        "kenney": {"root": kenney_root, "packages": packages,
                   "total_ogg": kenney_total},
        "drums_gdc": {
            "path": dg,
            "exists": os.path.isdir(dg),
            "total_wav": sum(k["count"] for k in kits.values()),
            "kits": kits,
            "manifest_entries": dg_manifest,
            "source": "Sonniss GDC 2026 精选 one-shot（manifest.json 有逐条元数据）",
            "note": ("位于 assets_root/samples/（samples_root 之外），"
                     "sample 引擎须用绝对路径引用"),
        },
        "mixkit": {"root": os.path.join(sr, "_raw", "mixkit"),
                   "categories": mix_cats, "total_mp3": mix_total},
        "sonniss": {"root": sonniss_root, "zips": len(zips),
                    "total_gb": round(sonniss_bytes / 1e9, 2),
                    "note": "原始分卷 zip，未解压（精选件已入 drums_gdc）"},
        "other": {
            "99sounds": {"files": n99, "note": "空目录（Gumroad 渠道未脚本化）"},
            "bgm_audition": {"count": bgm_n,
                             "note": "AI 生成试听样（整曲 BGM，非 one-shot 音色）"},
        },
        "payload": _payload_doc("sample"),
    }


def build_catalog(runtime=None):
    """扫描全部音色源 → catalog dict（timbres.json 结构契约）。"""
    from .paths import effective_runtime
    rt = _load_runtime() if runtime is None else effective_runtime(runtime)
    return {
        "generated": date.today().isoformat(),
        "sources": {
            "synth": _scan_synth(),
            "sf2": _scan_sf2(rt),
            "sfz": _scan_sfz(rt),
            "sample": _scan_sample(rt),
        },
        "engines_note": (
            "非静态音色源：ai 引擎（ComfyUI ACE-Step 1.5 / Music3，text-to-music "
            "整段素材生成，无固定音色，工作流见 workflows/）；demucs 引擎（htdemucs "
            "分轨降维，非音色源）；master 引擎（母带响度处理，非音色源）。"),
    }


# ------------------------------------------------------------- Markdown ----

def _md_table(header, rows):
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def _md_sfz_library(lines, name, info):
    lic = info.get("license", "")
    lines.append(f"### {name}（{info['count']}）"
                 + (f" — {lic}" if lic else ""))
    for note in (info.get("notes") or []):
        lines.append(f"> {note}")
    for g, files in info["groups"].items():
        glabel = "（库根）" if g == "." else f"（{len(files)}）"
        if g == ".":
            lines.append(f"**全部位于库根**（{len(files)} 个）：")
        else:
            lines.append(f"**{g}/**{glabel}：")
        lines += [f"- `{f}`" for f in files]
        lines.append("")


def render_markdown(cat):
    """catalog dict → 人读 Markdown（timbres.md 正文）。"""
    src = cat["sources"]
    lines = [
        "# bgm 音色目录（timbres）",
        "",
        f"> 生成：{cat['generated']} · `bgm catalog --write` 扫描真实磁盘生成"
        "（synth 段=引擎白名单，代码事实）。",
        f"> sfz 根：`{src['sfz']['root']}` · 采样根：`{src['sample']['samples_root']}`",
        "",
    ]

    # ---- 总览 ----
    kn, dg, mk = src["sample"]["kenney"], src["sample"]["drums_gdc"], src["sample"]["mixkit"]
    lines += _md_table(
        ["音色源", "引擎", "规模", "引用方式"],
        [["synth 内置音色", "synth", f"{src['synth']['count']}（打击 {len(src['synth']['percussive'])}"
          f" + 调性 {len(src['synth']['tonal'])}）", "payload.program = 音色名"],
         ["SoundFont", "sf2", f"{src['sf2']['count']} 库 × GM 128 program",
          "payload.sf2/bank/program（速记 `FluidR3_GM:0:48`）"],
         ["SFZ 乐器", "sfz", f"{len(src['sfz']['libraries'])} 库 / {src['sfz']['total']} 个 .sfz",
          "payload.sfz = 路径（绝对 / 相对 / 纯文件名）"],
         ["one-shot 采样", "sample",
          f"Kenney {kn['total_ogg']} ogg + drums_gdc {dg['total_wav']} wav"
          f" + mixkit {mk['total_mp3']} mp3",
          "payload.instrument / hits[].file（相对 samples_root）"]])
    lines += [""]

    # ---- synth ----
    sy = src["synth"]
    lines += [f"## synth —— synthlib 内置音色（{sy['count']}）", "",
              f"实现：`{sy['module']}`（纯 numpy）；引擎白名单 engines/synth.py。", ""]
    lines += _md_table(
        ["音色", "类型", "pitch", "可调 params（payload.params）", "说明"],
        [[n, t["type"], t["pitch"], ", ".join(t["params"]) or "—", t["desc"]]
         for n, t in sy["timbres"].items()])
    lines += ["", "payload 用法（描述符 engines/descriptors/synth.json）：", "",
              "```", sy["payload"], "```", ""]

    # ---- sf2 ----
    s2 = src["sf2"]
    lines += [f"## sf2 —— SoundFont 库（{s2['count']}）", "",
              "音色 = bank/program 寻址；bank 0 = GM 128 melodic（下表标准名），"
              "bank 128 = GM 打击组。", ""]
    lines += _md_table(
        ["名字（payload.sf2）", "路径", "存在", "大小 MB"],
        [[n, f"`{v['path']}`", "✓" if v["exists"] else "✗",
          v["size_mb"] if v["size_mb"] is not None else "—"]
         for n, v in s2["fonts"].items()])
    lines += ["", f"工程轨 instrument 速记法：`{s2['shorthand']}`。", "",
              "### GM 128 program（bank 0，program 0..127）", ""]
    gm = s2["gm_programs"]
    lines += _md_table(["prog", "名称", "prog", "名称", "prog", "名称", "prog", "名称"],
                       [[i, gm[i], i + 32, gm[i + 32], i + 64, gm[i + 64],
                         i + 96, gm[i + 96]] for i in range(32)])
    lines += ["", "payload 用法（描述符 engines/descriptors/sf2.json）：", "",
              "```", s2["payload"], "```", ""]

    # ---- sfz ----
    sz = src["sfz"]
    lines += [f"## sfz —— SFZ 乐器库（{len(sz['libraries'])} 库，{sz['total']} 个 .sfz）", "",
              f"根：`{sz['root']}` · 引用规则：{sz['ref_rules']} ·"
              f" 目录别名：{'、'.join(f'{k}→{v}' for k, v in sz['aliases'].items())}。", "",
              "> 文件名常见缩写：**SEC**=section 合奏部 · **SOLO**=独奏 · **SUS/VIB/Stac/Leg**"
              "=sustain/vibrato/staccato/legato 演奏法 · **KS**=keyswitch 键切换 ·"
              " **DXF**=dynamic crossfade 力度层交叉淡化 · **Ens**=ensemble。", ""]
    for name, info in sz["libraries"].items():
        _md_sfz_library(lines, name, info)
    lines += ["payload 用法（描述符 engines/descriptors/sfz.json）：", "",
              "```", sz["payload"], "```", ""]

    # ---- sample ----
    sm = src["sample"]
    lines += [f"## sample —— one-shot 采样（采样根 `{sm['samples_root']}`）", ""]
    lines += [f"### Kenney（{len(kn['packages'])} 包，{kn['total_ogg']} ogg，CC0）", ""]
    for pkg, p in kn["packages"].items():
        lines.append(f"#### {pkg}（{p['ogg']} ogg）— 引用前缀 `{p['ref_prefix']}`"
                     "（相对 samples_root；sample 引擎为严格路径拼接，无模糊匹配）")
        lines.append("")
        lines += _md_table(
            ["类目", "ogg", "示例"],
            [["（包根）" if c == "." else c, cinfo["count"],
              "、".join(f"`{e}`" for e in cinfo["examples"])]
             for c, cinfo in p["categories"].items()])
        lines.append("")
    dg = sm["drums_gdc"]
    lines += [f"### drums_gdc（{dg['total_wav']} one-shot，Sonniss GDC 2026 精选）", "",
              f"路径：`{dg['path']}`（manifest.json 逐条元数据 {dg['manifest_entries']} 条）",
              "", "> 注意：在 samples_root 之外，sample 引擎须用绝对路径引用。", ""]
    lines += _md_table(["组", "wav", "示例"],
                       [[k, v["count"],
                         "、".join(f"`{e}`" for e in v["examples"])]
                        for k, v in dg["kits"].items()])
    lines += ["", "### _raw 其余原始包（包级计数）", ""]
    mk_cats = sm["mixkit"]["categories"]
    lines += _md_table(
        ["包", "规模", "说明"],
        [["mixkit", f"{len(mk_cats)} 类目 / {sm['mixkit']['total_mp3']} mp3",
          "可商用免署名；类目：" + "、".join(mk_cats.keys())],
         ["sonniss", f"{sm['sonniss']['zips']} 分卷 zip / {sm['sonniss']['total_gb']} GB",
          sm["sonniss"]["note"]],
         ["99sounds", f"{sm['other']['99sounds']['files']} 文件",
          sm["other"]["99sounds"]["note"]],
         ["bgm/（samples_root 下）", f"{sm['other']['bgm_audition']['count']} 个音频",
          sm["other"]["bgm_audition"]["note"]]])
    lines += ["", "payload 用法（描述符 engines/descriptors/sample.json）：", "",
              "```", sm["payload"], "```", ""]

    # ---- 非静态音色源 ----
    lines += ["## 非静态音色源（说明）", "", f"{cat['engines_note']}", "",
              "- `ai` 引擎：工作流模板在 `workflows/`（wf_ace15*.json / wf_music3.json 等），"
              "payload.params 按节点点路径覆盖（duration/seed/style 等）。",
              "- `demucs` 引擎：`bgm stem` 子命令（四分轨 vocals/drums/bass/other）。",
              "- `master` 引擎：`bgm master` 子命令（loudnorm / matchering 两策略）。", ""]
    return "\n".join(lines)


def portable_catalog(cat, runtime=None):
    """发布副本只改路径前缀，不修改实时扫描结果或引擎运行路径。"""
    rt = _load_runtime() if runtime is None else runtime
    prefixes = sorted(
        [(os.path.expanduser(rt[key]).rstrip(os.sep), token)
         for key, token in (("assets_root", "$MUSIC_ASSETS_ROOT"),
                            ("samples_root", "$MUSIC_SAMPLES_ROOT"))
         if rt.get(key)], key=lambda pair: len(pair[0]), reverse=True)

    def visit(value):
        if isinstance(value, dict):
            return {key: visit(val) for key, val in value.items()}
        if isinstance(value, list):
            return [visit(val) for val in value]
        if isinstance(value, str):
            for prefix, token in prefixes:
                if value == prefix or value.startswith(prefix + os.sep):
                    return token + value[len(prefix):]
        return value

    return visit(cat)


def write_catalog(cat, out_dir=None):
    """Write explicitly selected output, defaulting to caller-owned cwd/catalog."""
    cat = portable_catalog(cat)
    out_dir = os.path.abspath(out_dir or "catalog")
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, "timbres.md")
    json_path = os.path.join(out_dir, "timbres.json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(cat))
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(cat, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return {"md": md_path, "json": json_path}
