"""bgm-render 插件（缝 A · prepare 源产物 + 缝 C · probe 虚拟源 facts，协议 v1.2；
设计权威 docs/ARCHITECTURE.md §2）。

sources[] 级键 ``"bgm": true | {"output": "master|premaster|stem:<轨名>",
"extra_deps": [...]}``——**视频层直接引用 music_tools 工程 JSON**（其时间线
文件，引用方式类比 gfx 轨直接引用 HTML：不写键零行为，写了键该源不再是媒体
文件而是「待渲染音乐工程」）：

    {"id": "bgm_main", "path": "../../music_project/works/xxx/project.json",
     "bgm": {"output": "master"}}

- prepare（缝 A）：``bgm render <工程> -o <共享缓存> --quiet`` → 取
  master/premaster/stem 产物 → 转 48kHz stereo s16（与内核裸提取同格式）落
  内容寻址键：

      cache/audio/{audio_key}__bgm_{version}_{digest}.wav

  digest = bgm 键值 + 插件代码摘要 + **$ref 外部文件内容摘要**（music_tools
  schema v2 的 track $ref 拆分件——主工程字节不变而 $ref 件被改时产物键仍翻转，
  不出陈旧缓存）+ extra_deps 显式补充（采样库等深度依赖由用户声明）。缓存命中
  直返（同工程再引用零渲染）；键变/工程变/$ref 变/代码变 → 新键自动重渲。
- **共享渲染缓存（v1.1.0）**：整工程渲染产物落持久目录 ``cache/bgm/<render_digest>/``
  （render_digest = 工程字节 + $ref 件 + extra_deps 件 + 插件代码摘要，**不含
  bgm 键值/轨名**——轨名只影响派生哪个 stem，不影响渲染本身）。多源指向同一
  工程只渲染一次，各源从共享产物派生（13 分轨场景 = 1 次渲染而非 13 次）；
  渲染成功后原子写 ``_complete.json`` 标记（tmp + os.replace），``<digest>.lock``
  flock 防跨进程双写（P6 并发窗防御惯例）。目录经 registry ``cache_dirs: ["bgm"]``
  进 gc 白名单（timeline 不再声明 bgm 键时可被 gc 回收）。
- **stem 输出（v1.1.0）**：``output: "stem:<轨名>"`` 派生该轨的**总线分轨**
  ``stems/<轨名>.bus.wav``（含 gain/pan/space 的 float32 工程全长 bus，
  music_tools mixer._render_track_chain 产出）——视频侧自挂独立音轨自行混音
  的场景（对位旧片的分轨 flac 转制），避开母带 loudnorm(-14 LUFS) 与母带 IR
  尾。轨名值域 ``^[A-Za-z0-9][A-Za-z0-9_.-]*$``（validate 防路径穿透）；bgm
  render 结果 JSON 的 ``stems`` 映射是**引擎级** stem（``stems/<轨>.<engine>.wav``）
  ——bus stem 无结果映射，按 mixer 命名约定拼路径，不存在时 fail-hard 列出
  可用轨名。**轨名永不拼进 shell 命令行字符串**（subprocess 列表参数）。
- probe（缝 C）：validate 期经 ``bgm show structure --json`` 取 duration_s（与
  render 的 build_render_plan 同口径，mix 被 atrim 定长到该时长）→ nb_frames
  供给事件时长校验——工程 JSON 不再触发 ffprobe 误报。分轨时长 = 工程时长
  （stem 与成片等长），facts 逻辑不受 stem 影响。
- 环境缺失 fail-hard：bgm CLI 不可解析（config bgm.cli / ~/.local/bin/bgm /
  bgm.repo + python3 -m bgm_gen 三通道全败）→ 打印逐通道缺什么 + exit 1
  （v1.2.0：config 通道指向不存在/不可执行路径 = 该通道不可用落下一通道，
  不再裸栈崩）。
- 回执身份：工程 JSON 作为 sources[] 条目，stat 身份天然入 receipt_fingerprint；
  深度依赖（$ref/采样）建议同时列入 project.cache_dependencies 获得回执级失效。
- contribute_keys（不变量 ①）：全部 bgm 源产物键进 gc 白名单；共享渲染目录
  经 registry cache_dirs 白名单保活，**v1.2.0 起键级回收**（cache_dir_keys 供给
  当前 render_digest 集——cache/bgm/ 下陈旧 digest 目录由 gc 列出/回收，
  在用目录保活，不再无界累积）。

config 键（vtx.api.cfg_get）：``bgm.cli``（bgm 包装脚本路径，优先）；
``bgm.repo``（music_tools 仓根，python3 -m bgm_gen 兜底通道的 PYTHONPATH）。
"""
from vtx.diagnostics import DiagnosticMessage, key_path, emit
from vtx import fstxn
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from vtx.api import audio_key as _audio_key
from vtx.api import cfg_get, media_cache_valid, plugin_code_digest

VERSION = "1.2.0"
KEY = "bgm"
OUTPUTS = ("master", "premaster")     # master=母带终态（默认，-14 LUFS/-1 dBTP）；premaster=母前
STEM_PREFIX = "stem:"
# 轨名白名单（防路径穿透）：字母数字开头，仅 [A-Za-z0-9_.-]；首字符约束天然拒绝
# ".." / ".hidden"；"/" 与 os.sep 均不在字符集内（stem:../x、stem:a/b 必拒）。
# 匹配一律 fullmatch（R1-F F3）：re 的 $ 允许尾随换行（"abc\n" 曾被 match+$ 放行，
# 轨名带 \n 会拼出跨行路径），fullmatch 才是整串锚定。
_STEM_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_DEFAULT_REPO = "~/文档/01_活跃项目/music_tools"
RENDER_CACHE_DIR = "bgm"              # cache/ 下共享渲染目录名（registry cache_dirs 对齐）
_COMPLETE = "_complete.json"          # 共享渲染完整标记（渲染成功后原子写）


# ---------------------------------------------------------------- config / CLI 解析
def _bgm_cmd(c=None):
    """bgm CLI 解析 → (argv 前缀, 额外 env)。三通道：config bgm.cli >
    ~/.local/bin/bgm > python3 -m bgm_gen（PYTHONPATH=config bgm.repo）。
    v1.2.0（R1-F F4）：通道解析目标不在盘/不可执行 = 该通道不可用，落下一通道
    （config bgm.cli 指向不存在路径原会在 _run_bgm 里 FileNotFoundError 裸栈崩）；
    全败返回 (None, 逐通道缺什么说明——含 config 通道的具体坏路径)。"""
    fails = []
    cli = cfg_get("bgm.cli", "")
    if cli:
        expanded = os.path.expanduser(cli)
        if os.name == 'nt':
            # Apply Windows argv quoting, preserving backslashes in paths.
            import ctypes
            from ctypes import wintypes
            shell = ctypes.WinDLL('shell32', use_last_error=True)
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
            shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
            kernel.LocalFree.argtypes = [ctypes.c_void_p]
            count = ctypes.c_int()
            pointer = shell.CommandLineToArgvW(expanded, ctypes.byref(count))
            if not pointer:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                argv = [pointer[i] for i in range(count.value)]
            finally:
                kernel.LocalFree(pointer)
        else:
            argv = shlex.split(expanded)
        # 存在性/可执行门：shutil.which 对含分隔符的路径按字面探测、裸命令名查
        # PATH，且只认可执行件——非执行文件过了这道门也会在 subprocess 处崩。
        if argv and shutil.which(argv[0]):
            return argv, None
        fails.append(f"config bgm.cli 指向不存在/不可执行路径（{cli}）")
    wrapper = Path.home() / ".local" / "bin" / "bgm"
    if wrapper.is_file():
        return [str(wrapper)], None
    repo = Path(os.path.expanduser(cfg_get("bgm.repo", _DEFAULT_REPO)))
    py = sys.executable if os.name == "nt" else shutil.which("python3")
    if repo.is_dir() and py:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo) + (os.pathsep + env["PYTHONPATH"]
                                         if env.get("PYTHONPATH") else "")
        return [py, "-m", "bgm_gen"], env
    fails.append(f"~/.local/bin/bgm 不在（{wrapper}）")
    fails.append(f"bgm.repo（{repo}）不可用或缺 python3")
    return None, ("bgm CLI 不可解析：" + "；".join(fails)
                  + "——config 配置见 config.example.json bgm 节")


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


def _tail_json(stdout: str, what: str):
    """bgm CLI 的 stdout 尾部 JSON 解析（indent=2 多行输出取最后一个行首 { 起）。"""
    try:
        return json.loads(stdout[stdout.rfind("\n{"):].strip()
                          if "\n{" in stdout else stdout)
    except ValueError as e:
        print(f"✗ bgm-render：{what} 输出解析失败（{e}）：{stdout[-300:]}",
              file=sys.stderr)
        sys.exit(1)


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


# 目录递归签名的伴生件豁免（对齐 hygiene gc 的 .lock/.tmp/__pycache__ 惯例）：
# 锁文件/原子写残件随处理过程出现/消失，计入会使摘要无意义抖动；__pycache__
# 目录整体跳过（字节码与解释器版本绑定，非工程内容）。
_DIR_SKIP_DIRNAMES = ("__pycache__",)
_DIR_SKIP_SUFFIXES = (".lock", ".tmp")
# 递归签名护栏（R1-F F1）：extra_deps 声明采样库这类目录时，文件数/累计字节
# 超限降级为 (相对路径, size, mtime_ns) 清单签名——GB 级库全量哈希会把
# validate/prepare 拖到分钟级；清单敏感度=stat 级（改内容必动 mtime），降级
# 事实经返回值前缀（DIRL）进 digest JSON，可观测。
_DIR_SIG_MAX_FILES = 5000
_DIR_SIG_MAX_BYTES = 2 * 1024 ** 3


def _file_sig(p: Path) -> str:
    """依赖件内容签名（进 param/render digest）：文件 → sha256[:16]；
    **目录 → 递归内容签名**（v1.2.0 R1-F F1：extra_deps 声明"采样库"目录不再
    等价 MISSING——目录内改文件/增删文件必翻转）；缺失记 MISSING（既有语义：
    缺失可被观测到翻转）。"""
    try:
        if p.is_dir():
            return _dir_sig(p)
        h = hashlib.sha256()
        with p.open("rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()[:16]
    except OSError:
        return "MISSING"


def _dir_sig(p: Path) -> str:
    """目录递归内容签名：收集 (相对路径, size, sha256[:16]) 排序后整体哈希，
    前缀 ``DIRC:``；护栏（文件数/累计字节超限，阈值可 monkeypatch）触发时降级
    (相对路径, size, mtime_ns) 清单签名，前缀 ``DIRL:``——前缀进 digest JSON
    是降级的可观测标记。枚举期消失的文件不计（下一轮签名自然可见）。"""
    files = []                                     # (相对路径 posix, stat)
    total = 0
    for f in p.rglob("*"):
        rel = f.relative_to(p)
        if any(part in _DIR_SKIP_DIRNAMES for part in rel.parts):
            continue                               # __pycache__ 整目录跳过
        if f.suffix in _DIR_SKIP_SUFFIXES:
            continue                               # .lock/.tmp 伴生件跳过
        try:
            if not f.is_file():
                continue
            st = f.stat()
        except OSError:
            continue
        files.append((rel.as_posix(), st))
        total += st.st_size
    listing = (len(files) > _DIR_SIG_MAX_FILES or total > _DIR_SIG_MAX_BYTES)
    h = hashlib.sha256(b"LISTING\n" if listing else b"CONTENT\n")
    for rel, st in sorted(files, key=lambda e: e[0]):
        h.update(rel.encode("utf-8", "surrogateescape") + b"\0")
        if listing:
            h.update(f"{st.st_size}:{st.st_mtime_ns}".encode())
        else:
            h.update(_file_sig(p / rel).encode())  # 纯文件支路（is_file 已滤）
    return ("DIRL:" if listing else "DIRC:") + h.hexdigest()[:16]


def _dep_sigs(c, s, proj: Path) -> dict:
    """深度依赖内容签名集：$ref 拆分件 + extra_deps 显式声明件（排序确定性）。"""
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
    return dep_sigs


def param_digest(c, sid) -> str:
    """产物键参数摘要 = bgm 键值 + 插件代码摘要 + $ref 件内容 + extra_deps 件内容
    （排序 JSON sha1[:12]）——任一变化产物键翻转，自动重渲不出陈旧缓存。轨名在
    bgm 键值内 → stem 名变更自然翻转产物键（派生件变），但不影响 render_digest。"""
    s = _source(c, sid) or {}
    proj = Path(c.src_path(sid))
    blob = json.dumps({"bgm": s.get(KEY), "deps": _dep_sigs(c, s, proj),
                       "code": plugin_code_digest("bgm-render")},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def render_digest(c, sid) -> str:
    """整工程渲染缓存摘要 = 工程字节 + $ref 件内容 + extra_deps 件内容 + 插件代码
    摘要（排序 JSON sha1[:12]）——**不含 bgm 键值与轨名**（master/premaster/stem:x
    共享同一次整工程渲染）。工程/$ref/extra_deps/插件代码任一变化 → 新 digest
    → 重渲；轨名/输出选择变化 → digest 不变 → 共享缓存直接派生零渲染。"""
    s = _source(c, sid) or {}
    proj = Path(c.src_path(sid))
    blob = json.dumps({"proj": _file_sig(proj), "deps": _dep_sigs(c, s, proj),
                       "code": plugin_code_digest("bgm-render")},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def product_path(c, sid) -> Path:
    """产物路径 cache/audio/{audio_key}__bgm_{version}_{param_digest}.wav。"""
    return c.cache / "audio" / (
        f"{_audio_key(c, sid)}__bgm_{VERSION}_{param_digest(c, sid)}.wav")


def render_cache_dir(c, sid) -> Path:
    """共享渲染缓存目录 cache/bgm/<render_digest>/（registry cache_dirs 白名单）。"""
    return c.cache / RENDER_CACHE_DIR / render_digest(c, sid)


# ---------------------------------------------------------------- 共享渲染缓存
def _read_complete(marker: Path):
    """读完整标记 → render result dict；缺失/残缺 → None（视为缓存未命中）。"""
    try:
        d = json.loads(marker.read_text(encoding="utf-8"))
        return d.get("result") if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


@contextmanager
def _flock(lock_path: Path, *, root=None):
    """跨进程排他锁（flock；对齐 loudness_cache.py 的 .lock 惯例：锁文件随目录
    存在、随 fd 关闭自动释放，异常路径不残留锁态）。prepare 内核侧串行，此锁防
    的是多 vtx 进程/区间预览并发触发同一工程的渲染双写。"""
    # fstxn owns the cross-platform lock backend and preserves the existing
    # <digest>.lock name. This plugin always supplies that suffix.
    target = lock_path.with_name(lock_path.name[:-5])
    with fstxn.file_lock(target, root=root):
        yield


def _ensure_shared_render(c, sid, force: bool = False):
    """保证整工程渲染产物在 cache/bgm/<render_digest>/ 并返回 (目录, result dict)。

    缓存命中（_complete.json 在且可读）→ 零渲染直返；缺失 → flock 双检后
    `bgm render <工程> -o <共享目录> --quiet`，成功后原子写 _complete.json
    （tmp + os.replace，P6 惯例：崩溃窗口最多留无标记残目录，下次重渲覆盖）。
    force=True（v1.2.0 自愈通道，R1-F F7）：无视缓存标记覆盖重渲——marker 完好
    但目标产物残缺（被外部删/截断）时的修复路；渲染输出覆盖写，成功后原子重写
    _complete.json。渲染失败 fail-hard（_run_bgm 打印命令/退出码/stderr 尾部
    + exit 1）。"""
    shared = render_cache_dir(c, sid)
    marker = shared / _COMPLETE
    result = None if force else _read_complete(marker)
    if result is not None:
        return shared, result
    with _flock(shared.parent / f"{shared.name}.lock", root=c.root):
        result = None if force else _read_complete(marker)
        if result is not None:     # 双检：持锁期间他进程可能已完成（force 跳过）
            return shared, result
        shared.mkdir(parents=True, exist_ok=True)
        print(f"  · bgm-render: 共享渲染缓存{'残缺覆盖重渲（自愈）' if force else '未命中'}，"
              f"整工程渲染 → {shared.name}/（同工程多源只渲这一次）")
        stdout = _run_bgm(c, ["render", str(c.src_path(sid)), "-o", str(shared),
                              "--quiet"], "工程渲染（render）")
        result = _tail_json(stdout, "render 结果")
        if not isinstance(result, dict):
            print(f"✗ bgm-render：render 结果不是 JSON 对象：{stdout[-300:]}",
                  file=sys.stderr)
            sys.exit(1)
        tmp = marker.parent / (marker.name + ".tmp")
        tmp.write_text(json.dumps({"result": result}, ensure_ascii=False,
                                  indent=1), encoding="utf-8")
        os.replace(tmp, marker)                  # 原子标记：可见即完整
    return shared, result


def _wav_ok(p: Path) -> bool:
    """盘上健康判据（R1-F F7 自愈口径）：文件在且 size>0——空 wav 下游必炸，
    与"缺失"同视为残缺。"""
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def _resolve_render_wav(shared: Path, result: dict, which: str, sid: str):
    """定位目标 wav（纯解析，不判在盘不退出）→ (Path|None, 错误说明|None)。

    事实口径（读 music_tools 代码确认）：bgm render 结果 JSON 的 ``stems`` 映射
    指向**引擎级** stem（stems/<轨>.<engine>.wav，混音前裸轨）；bus stem（含
    gain/pan/space 的总线分轨）无结果映射，按 mixer._render_track_chain 的命名
    约定 ``stems/<轨名>.bus.wav`` 拼路径（纯 Path 拼接，轨名不进任何 shell 字符
    串）。轨名不在工程轨集 → 错误说明列出可用轨名（重渲治不了，交 fail-hard）。"""
    if not which.startswith(STEM_PREFIX):
        p = result.get(which) if isinstance(result, dict) else None
        if not isinstance(p, str) or not p:
            return None, f"render 结果缺 {which!r} 产物路径：{sorted(result)}"
        return Path(p), None
    track = which[len(STEM_PREFIX):]
    avail = sorted(k for k in (result.get("stems") or {})
                   if isinstance(k, str))
    if track not in avail:
        return None, (f"{sid} 请求分轨 {which!r} 不在工程轨集——可用轨名："
                      f"{avail if avail else '（render 结果未含轨集，检查 cache/bgm/ 下产物）'}")
    return shared / "stems" / f"{track}.bus.wav", None


def _locate_render_wav(shared: Path, result: dict, which: str, sid: str) -> Path:
    """fail-hard 版定位：解析 + 在盘健康校验（size>0，v1.2.0 起空文件同拒）；
    失败打印缺什么 + exit 1。"""
    p, err = _resolve_render_wav(shared, result, which, sid)
    if p is None:
        print(f"✗ bgm-render：{err}", file=sys.stderr)
        sys.exit(1)
    if not _wav_ok(p):
        print(f"✗ bgm-render：{which!r} 的产物未落盘或为空文件：{p}"
              f"（缓存目录 {shared} 残缺且自愈重渲后仍缺——多为 render 引擎未按"
              f"约定产出该文件，可删除该目录后重跑排查）", file=sys.stderr)
        sys.exit(1)
    return p


# ---------------------------------------------------------------- 缝 C：probe facts
def probe_source(c, sid):
    """缝 C 钩子（协议 v1.2）：源声明 bgm 键 → 供给 facts（bgm show structure 的
    duration_s 与 render 同口径；mix 被 atrim 定长，帧数精确；分轨时长 = 工程
    时长，stem 输出不改本口径）。每 Ctx 每源 memo（validate 源级/事件级两处调用
    只跑一次 CLI）。未声明 → None（回落 ffprobe）。"""
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
    未声明 → None（调用方直抽裸 wav）。产物键缓存命中直返；缺失则从**共享渲染
    缓存**派生（cache/bgm/<render_digest>/ 整工程渲染一次，多源共用——v1.1.0
    起 master/premaster/stem 全走此通道），取 master/premaster/stem:<轨名>.bus
    wav 转 48k stereo s16 落内容寻址键。dry-run 不落产物只打印预期。"""
    s = _source(c, sid)
    if s is None or KEY not in s:
        return None
    out = product_path(c, sid)
    if media_cache_valid(out):
        return out
    which = _params(s).get("output", "master")
    if getattr(c, "dry", False):
        hit = _read_complete(render_cache_dir(c, sid) / _COMPLETE) is not None
        plan = ("共享渲染缓存命中，直接派生" if hit else
                "共享渲染缓存未命中，将整工程渲染一次（cache/bgm/，多源共享）")
        print(f"  [dry-run] bgm-render 产物 {out.name}（output={which}；{plan}）")
        return out
    print(f"  · bgm-render: {sid} → {out.name}（output={which}）")
    shared, result = _ensure_shared_render(c, sid)
    # 共享缓存自愈（v1.2.0 R1-F F7）：_complete.json 完好但目标产物残缺（被外部
    # 删/截断为空）→ 覆盖重渲一次；解析不出目标（轨名不在轨集等工程侧问题）不
    # 触发自愈——重渲治不了，交 _locate_render_wav fail-hard 给足排错信息。
    want, _err = _resolve_render_wav(shared, result, which, sid)
    if want is not None and not _wav_ok(want):
        shared, result = _ensure_shared_render(c, sid, force=True)
    src_wav = _locate_render_wav(shared, result, which, sid)
    # 转封装与内核裸提取同一形状（48kHz stereo pcm_s16le）——下游 _audio_graph
    # / audio_report 无感。bus stem 本就 48k f32 2ch，-ar 48000 同采样率直通
    # （零重采样损失）；44.1k 母带则重采样进项目帧网格。采样数保真不裁不补。
    c.run(["ffmpeg", "-v", "error", "-y", "-i", str(src_wav),
           "-vn", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out)])
    dur = result.get("duration_s")
    qc_ok = result.get("qc_ok")
    print(f"✓ bgm-render：{sid} 派生完成（output={which}，工程时长 {dur}s，"
          f"music qc {'ok' if qc_ok else '未过'}）→ {out.name}")
    return out


def contribute_keys(c) -> set:
    """不变量 ①（§2.1）：本 timeline 全部声明 bgm 键源的产物键集（gc 白名单）。
    共享渲染目录 cache/bgm/ 另经 registry cache_dirs 白名单 + cache_dir_keys
    键级保活（timeline 声明 bgm 键期间：在用 digest 保活、陈旧 digest 由 gc
    回收；全项目弃用 bgm 键后由 gc 作 legacy 回收）。"""
    return {product_path(c, s["id"]).stem
            for s in c.tl.get("sources", [])
            if isinstance(s, dict) and s.get("id") and KEY in s}


def cache_dir_keys(c) -> dict:
    """不变量 ① 的键级扩展（v1.2.0 R1-F F2）：cache/bgm/ 的内容寻址键级白名单
    = 当前 timeline 全部 bgm 源的 render_digest 目录名。digest 纯静态可算
    （工程字节 + $ref + extra_deps + 代码摘要，不跑 CLI）——gc 期与渲染期同源
    同值。cache/bgm/<digest>/ 在清单内 = 在用保活；不在 = 陈旧（gc --dry 列出、
    gc 回收）——修复陈旧 render_digest 目录（每个 ≈整工程渲染产物）无界累积。"""
    keys = set()
    for s in c.tl.get("sources", []):
        if isinstance(s, dict) and s.get("id") and KEY in s:
            keys.add(render_digest(c, s["id"]))
    return {RENDER_CACHE_DIR: keys}


# ---------------------------------------------------------------- validate 钩子
def validate(c):
    """validate 钩子（协议 v1.1）：键值形状早失败。output 值域 =
    master|premaster|stem:<轨名>（轨名 ^[A-Za-z0-9][A-Za-z0-9_.-]*$，防路径穿透）。"""
    errs = []
    for si, s in enumerate(c.tl.get("sources", [])):
        if not isinstance(s, dict) or KEY not in s:
            continue
        val = s[KEY]
        if not (val is True or isinstance(val, dict)):
            errs.append(DiagnosticMessage(f"sources[{si}]: 键 {KEY!r} 须为 true 或对象 "
                        f"{{output, extra_deps}}，得到 {val!r}", json_path=f'$.sources[{si}][{KEY!r}]', rule_id='plugin.bgm.type', measured={'value': val}, threshold={'type': 'true or object'}, suggested_actions=[{'op': 'review_plugin_parameter', 'params': {'json_path': f'$.sources[{si}][{KEY!r}]'}}]))
            continue
        path = s.get("path", "")
        if not isinstance(path, str) or not path.lower().endswith(".json"):
            errs.append(DiagnosticMessage(f"sources[{si}]: bgm 源的 path 须指向 music_tools 工程 JSON"
                        f"（.json），得到 {path!r}", json_path=f'$.sources[{si}].path', rule_id='plugin.bgm.path', measured={'value': path}, threshold={'extension': '.json'}, suggested_actions=[{'op': 'review_plugin_parameter', 'params': {'json_path': f'$.sources[{si}].path'}}]))
        if isinstance(val, dict):
            unknown = sorted(set(val) - {"output", "extra_deps"})
            if unknown:
                errs.append(DiagnosticMessage(f"sources[{si}]: 键 {KEY!r} 含未知参数 {unknown}"
                            "（可用：output/extra_deps）", json_path=f'$.sources[{si}][{KEY!r}]', rule_id='plugin.bgm.keys', measured={'keys': unknown}, threshold={'allowed': ['output', 'extra_deps']}, suggested_actions=[{'op': 'review_plugin_parameter', 'params': {'json_path': f'$.sources[{si}][{KEY!r}]'}}]))
            out_which = val.get("output", "master")
            stem_ok = (isinstance(out_which, str) and
                       out_which.startswith(STEM_PREFIX) and
                       bool(_STEM_NAME_RE.fullmatch(out_which[len(STEM_PREFIX):])))
            if out_which not in OUTPUTS and not stem_ok:
                errs.append(
                    DiagnosticMessage(f"sources[{si}]: {KEY}.output 须为 {'|'.join(OUTPUTS)} 或 "
                    f"'{STEM_PREFIX}<轨名>'（轨名 ^[A-Za-z0-9][A-Za-z0-9_.-]*$，"
                    f"不得含路径分隔符；可用轨名见 `bgm show structure`），"
                    f"得到 {out_which!r}", json_path=f'$.sources[{si}][{KEY!r}].output', rule_id='plugin.bgm.output', measured={'value': out_which}, threshold={'allowed': list(OUTPUTS), 'stem_pattern': _STEM_NAME_RE.pattern}, suggested_actions=[{'op': 'review_plugin_parameter', 'params': {'json_path': f'$.sources[{si}][{KEY!r}].output'}}]))
            deps = val.get("extra_deps", [])
            if not isinstance(deps, list) or not all(isinstance(x, str) for x in deps):
                errs.append(DiagnosticMessage(f"sources[{si}]: {KEY}.extra_deps 须为字符串数组"
                            "（相对音乐工程目录的深度依赖，进产物键摘要）", json_path=f'$.sources[{si}][{KEY!r}].extra_deps', rule_id='plugin.bgm.dependencies', measured={'value': deps}, threshold={'type': 'array of strings'}, suggested_actions=[{'op': 'review_plugin_parameter', 'params': {'json_path': f'$.sources[{si}][{KEY!r}].extra_deps'}}]))
    return errs
