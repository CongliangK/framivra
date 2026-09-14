"""bgm-render 插件（缝 A · prepare 源产物 + 缝 C · probe 虚拟源 facts，协议 v1.2；
设计权威 docs/ARCHITECTURE.md §2）。

sources[] 级键 ``"bgm": true | {"output": "master|premaster", "extra_deps": [...]}``
——**视频层直接引用 music_tools 工程 JSON**（其时间线文件，引用方式类比 gfx 轨
直接引用 HTML：不写键零行为，写了键该源不再是媒体文件而是「待渲染音乐工程」）：

    {"id": "bgm_main", "path": "../../music_project/works/xxx/project.json",
     "bgm": {"output": "master"}}

- prepare（缝 A）：``bgm render <工程> -o <tmp>`` → 取母带 wav（loudnorm -14/-1，
  B 站口径，交付即用）→ 转 48kHz stereo s16（与内核裸提取同格式）落内容寻址键：

      cache/audio/{audio_key}::bgm:{version}:{digest}.wav

  digest = bgm 键值 + 插件代码摘要 + **$ref 外部文件内容摘要**（music_tools
  schema v2 的 track $ref 拆分件——主工程字节不变而 $ref 件被改时产物键仍翻转，
  不出陈旧缓存）+ extra_deps 显式补充（采样库等深度依赖由用户声明）。缓存命中
  直返（同工程再引用零渲染）；键变/工程变/$ref 变/代码变 → 新键自动重渲。
- probe（缝 C）：validate 期经 ``bgm show structure --json`` 取 duration_s（与
  render 的 build_render_plan 同口径，mix 被 atrim 定长到该时长）→ nb_frames
  供给事件时长校验——工程 JSON 不再触发 ffprobe 误报。
- 环境缺失 fail-hard：bgm CLI 不可解析（config bgm.cli / ~/.local/bin/bgm /
  bgm.repo + python3 -m bgm_gen 三通道全败）→ 打印缺什么 + exit 1。
- 回执身份：工程 JSON 作为 sources[] 条目，stat 身份天然入 receipt_fingerprint；
  深度依赖（$ref/采样）建议同时列入 project.cache_dependencies 获得回执级失效。
- contribute_keys（不变量 ①）：全部 bgm 源产物键进 gc 白名单。

config 键（vtx.api.cfg_get）：``bgm.cli``（bgm 包装脚本路径，优先）；
``bgm.repo``（music_tools 仓根，python3 -m bgm_gen 兜底通道的 PYTHONPATH）。
"""
import hashlib
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from vtx.api import audio_key as _audio_key
from vtx.api import cfg_get, plugin_code_digest

VERSION = "1.0.0"
KEY = "bgm"
OUTPUTS = ("master", "premaster")     # master=母带终态（默认，-14 LUFS/-1 dBTP）；premaster=母前
_DEFAULT_REPO = "~/文档/01_活跃项目/music_tools"


# ---------------------------------------------------------------- config / CLI 解析
def _bgm_cmd(c=None):
    """bgm CLI 解析 → (argv 前缀, 额外 env)。三通道：config bgm.cli >
    ~/.local/bin/bgm > python3 -m bgm_gen（PYTHONPATH=config bgm.repo）。
    全败返回 (None, 缺什么说明)。"""
    cli = cfg_get("bgm.cli", "")
    if cli:
        return shlex.split(os.path.expanduser(cli)), None
    wrapper = Path.home() / ".local" / "bin" / "bgm"
    if wrapper.is_file():
        return [str(wrapper)], None
    repo = Path(os.path.expanduser(cfg_get("bgm.repo", _DEFAULT_REPO)))
    py = shutil.which("python3")
    if repo.is_dir() and py:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo) + (os.pathsep + env["PYTHONPATH"]
                                         if env.get("PYTHONPATH") else "")
        return [py, "-m", "bgm_gen"], env
    return None, (f"bgm CLI 不可解析：未配置 bgm.cli、~/.local/bin/bgm 不在、"
                  f"bgm.repo（{repo}）不可用或缺 python3——config 配置见 "
                  f"config.example.json bgm 节")


def _run_bgm(c, args, what):
    """bgm 子命令执行（fail-hard：解析失败/退出非零打印缺什么 + exit 1）。"""
    cmd, env = _bgm_cmd(c)
    if cmd is None:
        print(f"✗ bgm-render 环境缺失（fail-hard）：{env}", file=sys.stderr)
        sys.exit(1)
    r = subprocess.run(cmd + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    if r.returncode != 0:
        print(f"✗ bgm-render：{what} 失败（{' '.join(cmd)} {' '.join(args[:2])} …，"
              f"exit {r.returncode}）：", file=sys.stderr)
        print((r.stderr or r.stdout or "")[-1500:], file=sys.stderr)
        sys.exit(1)
    return r.stdout


# ---------------------------------------------------------------- 键值与摘要
def _source(c, sid):
    for s in c.tl.get("sources", []):
        if isinstance(s, dict) and s.get("id") == sid:
            return s
    return None


def _params(s):
    """键值归一：true → 缺省；dict → 原样；无键 → None。"""
    val = s.get(KEY)
    if val is None:
        return None
    return {} if val is True else val


def _walk_refs(node, out):
    """递归收集 music_tools schema v2 的 "$ref" 字符串值（track 外部拆分件）。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str):
                out.append(v)
            else:
                _walk_refs(v, out)
    elif isinstance(node, list):
        for x in node:
            _walk_refs(x, out)


def _file_sig(p: Path) -> str:
    """依赖文件内容签名（小文件全量 sha256；KB 级工程/引用件；缺失记 MISSING）。"""
    h = hashlib.sha256()
    try:
        with p.open("rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()[:16]
    except OSError:
        return "MISSING"


def param_digest(c, sid) -> str:
    """产物键参数摘要 = bgm 键值 + 插件代码摘要 + $ref 件内容 + extra_deps 件内容
    （排序 JSON sha1[:12]）——任一变化产物键翻转，自动重渲不出陈旧缓存。"""
    s = _source(c, sid) or {}
    proj = Path(c.src_path(sid))
    refs = []
    try:
        _walk_refs(json.loads(proj.read_text(encoding="utf-8")), refs)
    except (OSError, ValueError):
        pass
    dep_sigs = {}
    for ref in sorted(set(refs)):
        rp = (proj.parent / ref).resolve()
        dep_sigs[str(rp)] = _file_sig(rp)
    for extra in sorted(_params(s).get("extra_deps", []) or []):
        if isinstance(extra, str):
            ep = Path(extra).expanduser()
            if not ep.is_absolute():
                ep = (proj.parent / ep).resolve()
            dep_sigs[str(ep)] = _file_sig(ep)
    blob = json.dumps({"bgm": s.get(KEY), "deps": dep_sigs,
                       "code": plugin_code_digest("bgm-render")},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def product_path(c, sid) -> Path:
    """产物路径 cache/audio/{audio_key}::bgm:{version}:{param_digest}.wav。"""
    return c.cache / "audio" / (
        f"{_audio_key(c, sid)}::bgm:{VERSION}:{param_digest(c, sid)}.wav")


# ---------------------------------------------------------------- 缝 C：probe facts
def probe_source(c, sid):
    """缝 C 钩子（协议 v1.2）：源声明 bgm 键 → 供给 facts（bgm show structure 的
    duration_s 与 render 同口径；mix 被 atrim 定长，帧数精确）。每 Ctx 每源
    memo（validate 源级/事件级两处调用只跑一次 CLI）。未声明 → None（回落
    ffprobe）。"""
    s = _source(c, sid)
    if s is None or KEY not in s:
        return None
    memo = getattr(c, "_bgm_render_probe", None)
    if memo is None:
        memo = {}
        setattr(c, "_bgm_render_probe", memo)
    if sid in memo:
        return memo[sid]
    out = _run_bgm(c, ["show", "structure", "--json", str(c.src_path(sid))],
                   "工程解析（show structure）")
    try:
        view = json.loads(out[out.rfind("\n{"):].strip() if "\n{" in out else out)
        dur = float(view["duration_s"])
    except (ValueError, KeyError, TypeError) as e:
        print(f"✗ bgm-render：show structure 输出解析失败（{e}）：{out[-300:]}",
              file=sys.stderr)
        sys.exit(1)
    facts = {"fps": None,
             "nb_frames": int(math.ceil(dur * c.fps)),
             "w": 0, "h": 0, "has_video": False, "has_audio": True,
             "bgm_duration_s": dur}
    memo[sid] = facts
    return facts


# ---------------------------------------------------------------- 缝 A：prepare 产物
def prepare_source(c, sid):
    """缝 A 钩子（契约 v1.1）：源声明 bgm 键 → 保证渲染产物在盘并返回 Path；
    未声明 → None（调用方直抽裸 wav）。缓存命中直返；缺失则 bgm render（临时
    目录出工程全套产物，取 master/premaster wav 转 48k stereo s16 落内容寻址键，
    临时目录即弃——本插件的内容寻址键就是缓存层）；dry-run 不落产物。"""
    s = _source(c, sid)
    if s is None or KEY not in s:
        return None
    out = product_path(c, sid)
    if out.exists():
        return out
    if getattr(c, "dry", False):
        print(f"  [dry-run] bgm-render 产物 {out.name}（bgm render → 母带 → 48k stereo）")
        return out
    which = _params(s).get("output", "master")
    print(f"  · bgm-render: {sid} → {out.name}（bgm render，output={which}）")
    proj = str(c.src_path(sid))
    tmp = c.cache / "tmp" / f"bgm-render-{sid.replace('/', '_')}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        stdout = _run_bgm(c, ["render", proj, "-o", str(tmp), "--quiet"],
                          "工程渲染（render）")
        try:
            result = json.loads(stdout[stdout.rfind("\n{"):].strip()
                                if "\n{" in stdout else stdout)
            src_wav = Path(result[which])
        except (ValueError, KeyError, TypeError) as e:
            print(f"✗ bgm-render：render 结果解析失败（{e}）：{stdout[-300:]}",
                  file=sys.stderr)
            sys.exit(1)
        if not src_wav.is_file():
            print(f"✗ bgm-render：render 结果 {which} 产物未落盘：{src_wav}",
                  file=sys.stderr)
            sys.exit(1)
        out.parent.mkdir(parents=True, exist_ok=True)
        # 转封装与内核裸提取同一形状（48kHz stereo pcm_s16le）——下游 _audio_graph
        # / audio_report 无感；bgm 母带通常 44.1k，此处重采样进项目帧网格。
        c.run(["ffmpeg", "-v", "error", "-y", "-i", str(src_wav),
               "-vn", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out)])
        dur = result.get("duration_s")
        qc_ok = result.get("qc_ok")
        print(f"✓ bgm-render：{sid} 渲染完成（时长 {dur}s，music qc "
              f"{'ok' if qc_ok else '未过（bgm qc.json 见临时目录前的输出）'}）→ {out.name}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def contribute_keys(c) -> set:
    """不变量 ①（§2.1）：本 timeline 全部声明 bgm 键源的产物键集（gc 白名单）。"""
    return {product_path(c, s["id"]).stem
            for s in c.tl.get("sources", [])
            if isinstance(s, dict) and s.get("id") and KEY in s}


# ---------------------------------------------------------------- validate 钩子
def validate(c):
    """validate 钩子（协议 v1.1）：键值形状早失败。"""
    errs = []
    for si, s in enumerate(c.tl.get("sources", [])):
        if not isinstance(s, dict) or KEY not in s:
            continue
        val = s[KEY]
        if not (val is True or isinstance(val, dict)):
            errs.append(f"sources[{si}]: 键 {KEY!r} 须为 true 或对象 "
                        f"{{output, extra_deps}}，得到 {val!r}")
            continue
        path = s.get("path", "")
        if not isinstance(path, str) or not path.lower().endswith(".json"):
            errs.append(f"sources[{si}]: bgm 源的 path 须指向 music_tools 工程 JSON"
                        f"（.json），得到 {path!r}")
        if isinstance(val, dict):
            unknown = sorted(set(val) - {"output", "extra_deps"})
            if unknown:
                errs.append(f"sources[{si}]: 键 {KEY!r} 含未知参数 {unknown}"
                            "（可用：output/extra_deps）")
            out_which = val.get("output", "master")
            if out_which not in OUTPUTS:
                errs.append(f"sources[{si}]: {KEY}.output 须为 {'|'.join(OUTPUTS)}，"
                            f"得到 {out_which!r}")
            deps = val.get("extra_deps", [])
            if not isinstance(deps, list) or not all(isinstance(x, str) for x in deps):
                errs.append(f"sources[{si}]: {KEY}.extra_deps 须为字符串数组"
                            "（相对音乐工程目录的深度依赖，进产物键摘要）")
    return errs
