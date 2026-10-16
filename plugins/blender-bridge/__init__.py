"""blender-bridge 插件（缝 D · finish 管线收尾钩子，协议 v1.2；设计权威 docs/ARCHITECTURE.md）。

timeline **顶层键** ``"blender_bridge": true | {"out": "...", "verify": bool}``——
`vtx all` 全链（mux/qc 完成后；qc 失败不阻断——产物已就绪，退出码如实汇总）
自动导出 Blender 审片工程（apps/blender-bridge/
json2blend.py，经 ``blender --background --factory-startup --python`` 运行）。
「JSON 里配置了就默认直接导出」——省去每次手敲 blender 命令行。

- 产物默认 ``<项目>/preview.blend``（键参 ``out`` 相对项目根可改；拒绝越出
  项目根的 .. 路径）；``verify: true`` 时追加 verify_blend.py 回读断言。
- 字幕透明轨前置保障：timeline 有字幕时，只有 ``cache/subs_alpha.mov``
  通过 ffprobe 视频流/正时长校验且 sidecar 的 ASS+project 键一致才复用；
  缺失、损坏或陈旧时自动调 apps/blender-bridge/subs_alpha.py（纯 python3，
  无 bpy，经 ``sys.executable`` 运行）生成。``cache/subs.ass`` 缺失 → 明确
  指引先跑 ``vtx subs render``；生成失败含完整命令与原因（不吞）。
- 失败文本滤噪（缺陷2修复，2026-10-11）：子进程 stderr 先滤除 Blender 5.x
  DeprecationWarning 块（告警行+缩进上下文行）——json2blend 的真实 ！错误行
  打到 stdout，旧 ``stderr or stdout`` 一刀切会让弃用告警完全淹没真实错误；
  滤除计数聚合为一行提示。子进程退出码语义不变。
- blender 不在 PATH → finish 返回错误行（exit 1，**正式产物 final.mp4 不受
  影响**）——键声明即用户要导出，静默跳过 = 配置了却没导出；安装期预检走
  doctor（requires.binaries blender + health 探针）。
- 与 apps/blender-bridge（应用件）的关系：app 提供 json2blend/verify_blend/
  audit_bundle 等交互工具（命令面不变），本插件只是把「渲染完自动导出」钩进
  `vtx all` 收尾缝——关停本插件不影响主链（§0.2 关停测试）。
- --draft / --dry-run / `all --until` 截断链不触发（cli 缝 D 接线口径）。
"""
from vtx.diagnostics import DiagnosticMessage, key_path, emit
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

VERSION = "1.0.0"
KEY = "blender_bridge"
_REPO_ROOT = Path(__file__).resolve().parents[2]     # plugins/blender-bridge → 仓根
_JSON2BLEND = _REPO_ROOT / "apps" / "blender-bridge" / "json2blend.py"
_VERIFY = _REPO_ROOT / "apps" / "blender-bridge" / "verify_blend.py"
_SUBS_ALPHA = _REPO_ROOT / "apps" / "blender-bridge" / "subs_alpha.py"


def source_resolve_map(c):
    """缝 A 读取点映射（P6）：sources[] 的插件产物解析（虚拟源如 bgm 工程 JSON、
    降噪源 denoise）→ {sid: wav 绝对路径}。经 vtx.api.resolve_source_wav（内核
    统一解析，缓存命中即时；产物缺失按插件语义产出——finish 期 prepare 已跑过，
    恒为缓存命中）。媒体源返回 None 不进映射。json2blend 在 blender 解释器内
    无法 import vtx，故由本插件算好映射经 argv 传入。"""
    from vtx.api import resolve_source_wav
    mapping = {}
    for s in c.tl.get("sources", []):
        if not isinstance(s, dict) or not isinstance(s.get("id"), str):
            continue
        try:
            p = resolve_source_wav(c, s["id"])
        except Exception:
            p = None
        if p is not None and Path(p).is_file():
            mapping[s["id"]] = str(Path(p).resolve())
    return mapping


def _params(c):
    """键值归一：true → 缺省参数；dict → 原样。无键 → None（不生效）。"""
    val = c.tl.get(KEY)
    if val is None:
        return None
    return {} if val is True else val


def validate(c):
    """validate 钩子（协议 v1.1）：键值形状早失败。"""
    val = c.tl.get(KEY)
    if val is None:
        return []
    errs = []
    if not (val is True or isinstance(val, dict)):
        return [DiagnosticMessage(f"timeline: 键 {KEY!r} 须为 true 或对象 {{out, verify}}，得到 {val!r}", json_path=key_path('$', KEY), rule_id='plugin.blender.type', measured={'value': val}, threshold={'type': 'true or object'}, suggested_actions=[{'op': 'review_plugin_parameter', 'params': {'json_path': key_path('$', KEY)}}])]
    if isinstance(val, dict):
        unknown = sorted(set(val) - {"out", "verify"})
        if unknown:
            errs.append(DiagnosticMessage(f"timeline: 键 {KEY!r} 含未知参数 {unknown}（可用：out/verify）", json_path=key_path('$', KEY), rule_id='plugin.blender.keys', measured={'keys': unknown}, threshold={'allowed': ['out', 'verify']}, suggested_actions=[{'op': 'review_plugin_parameter', 'params': {'json_path': key_path('$', KEY)}}]))
        out = val.get("out", "preview.blend")
        if not isinstance(out, str) or not out:
            errs.append(DiagnosticMessage(f"timeline: {KEY}.out 须为非空字符串（相对项目根，如 preview.blend）", json_path=key_path('$', KEY)+'.out', rule_id='plugin.blender.output_type', measured={'value': out}, threshold={'type': 'nonempty string'}, suggested_actions=[{'op': 'review_plugin_parameter', 'params': {'json_path': key_path('$', KEY)+'.out'}}]))
        else:
            p = Path(out)
            if p.is_absolute() or ".." in p.parts:
                errs.append(DiagnosticMessage(f"timeline: {KEY}.out 禁止绝对路径/..（须落在项目根内）: {out}", json_path=key_path('$', KEY)+'.out', rule_id='plugin.blender.output_path', measured={'value': out}, threshold={'relative_to_project': True, 'parent_traversal': False}, suggested_actions=[{'op': 'review_plugin_parameter', 'params': {'json_path': key_path('$', KEY)+'.out'}}]))
        if "verify" in val and not isinstance((verify_value := val["verify"]), bool):
            errs.append(DiagnosticMessage(f"timeline: {KEY}.verify 须为布尔值", json_path=key_path('$', KEY)+'.verify', rule_id='plugin.blender.verify', measured={'value': verify_value}, threshold={'type': 'boolean'}, suggested_actions=[{'op': 'review_plugin_parameter', 'params': {'json_path': key_path('$', KEY)+'.verify'}}]))
    return errs


_DEPRECATION_HEADER = re.compile(
    r"^(?:[A-Za-z]:)?[^\s:\r\n][^:\r\n]*:\d+:\s*DeprecationWarning:")


def _filter_deprecation(lines):
    """仅滤 path:line: DeprecationWarning 警告块及缩进上下文。

    不按子串匹配：traceback 源码、异常消息中的同名文本必须原样保留。
    返回 (保留行列表, 滤除行数)。"""
    kept, filtered = [], 0
    i, n = 0, len(lines)
    while i < n:
        if _DEPRECATION_HEADER.match(lines[i]):
            filtered += 1
            i += 1
            while i < n and lines[i][:1] in (" ", "\t") and lines[i].strip():
                filtered += 1
                i += 1
            continue
        kept.append(lines[i])
        i += 1
    return kept, filtered


def _error_tail(stderr: str, stdout: str):
    """两路均滤警告；json2blend 错误标记优先，再附两路尾部与总滤除数。"""
    err_kept, n_err = _filter_deprecation((stderr or "").splitlines())
    out_kept, n_out = _filter_deprecation((stdout or "").splitlines())
    streams = [[line.strip() for line in lines
                if line.strip() and line.strip() != "Blender quit"
                and not (line.startswith("Blender ") and "(hash" in line)]
               for lines in (err_kept, out_kept)]
    primary = []
    for lines in streams:
        in_errors = False
        for line in lines:
            if line.startswith("[json2blend]"):
                in_errors = bool(re.match(r"^\[json2blend\]\s*(?:!|！|错误)", line))
                if in_errors:
                    primary.append(line)
            elif in_errors and line.startswith(("!", "！")):
                primary.append(line)
            else:
                in_errors = False
    selected = []
    for line in primary + streams[0][-3:] + streams[1][-3:]:
        if line not in selected:
            selected.append(line)
    return " | ".join(selected) or "（无错误输出）", n_err + n_out


def _run_blender_script(blender, script, args, what):
    """blender --background --factory-startup --python <script> -- <args>。"""
    cmd = [blender, "--background", "--factory-startup",
           "--python", str(script), "--", *[str(a) for a in args]]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        tail, n_warn = _error_tail(r.stderr or "", r.stdout or "")
        err = f"{what} 失败（exit {r.returncode}）：{tail}"
        if n_warn:
            err += f"（已滤除 {n_warn} 行 Blender DeprecationWarning）"
        return cmd, err
    return cmd, ""


def _subs_alpha_key(ass, project):
    """局部缓存身份：ASS 字节（含样式）与透明画布/时基/帧长。"""
    params = {name: project[name] for name in ("width", "height", "fps", "duration_frames")}
    return {"ass_sha1": hashlib.sha1(ass.read_bytes()).hexdigest(), "project": params}


def _valid_subs_alpha(mov):
    """ffprobe 须成功读到视频流和正时长；截断残片/非法元数据不可复用。"""
    if not mov.is_file():
        return False
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=codec_type,duration:format=duration", "-of", "json", str(mov)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        if r.returncode != 0:
            return False
        info = json.loads(r.stdout)
        videos = [stream for stream in info.get("streams", [])
                  if stream.get("codec_type") == "video"]
        durations = [stream.get("duration") for stream in videos]
        durations.append(info.get("format", {}).get("duration"))
        return bool(videos) and any(
            math.isfinite(float(value)) and float(value) > 0
            for value in durations if value not in (None, "N/A"))
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, AttributeError):
        return False


def _ensure_subs_alpha(c):
    """仅复用源键匹配且可读的透明轨；缺失、陈旧、损坏均自动重建。

    项目根取 timeline 文件自身路径的父目录，不跟随 timeline symlink。
    成功且产物通过 ffprobe 才记 sidecar；失败含完整命令与原因。"""
    subs = c.tl.get("subtitles") if isinstance(c.tl, dict) else None
    if not subs:
        return []
    timeline = Path(c.tl_path).absolute()
    cache = timeline.parent / "cache"
    mov, ass = cache / "subs_alpha.mov", cache / "subs.ass"
    key_file = cache / "subs_alpha.key.json"
    if not ass.is_file():
        return [f"缺少 {ass}（字幕透明轨 subs_alpha.mov 的源）——先跑 vtx 字幕阶段："
                f"vtx -t {c.tl_path} subs render 生成 subs.ass，再重跑 vtx all"]
    try:
        key = _subs_alpha_key(ass, c.tl["project"])
        try:
            saved_key = json.loads(key_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved_key = None
        if saved_key == key and _valid_subs_alpha(mov):
            return []
    except (OSError, KeyError, TypeError) as exc:
        return [f"subs_alpha 缓存键读取失败：{exc}"]
    if not _SUBS_ALPHA.is_file():
        return [f"subs_alpha 脚本缺失：{_SUBS_ALPHA}（apps/blender-bridge 应用件不完整？）"]
    py = sys.executable or shutil.which("python3") or "python3"
    cmd = [py, str(_SUBS_ALPHA), str(timeline)]
    print("  · blender-bridge: cache/subs_alpha.mov 缺失、陈旧或损坏，自动生成（subs_alpha.py）")
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 or not _valid_subs_alpha(mov):
        why, _ = _error_tail(r.stderr or "", r.stdout or "")
        if why == "（无错误输出）":
            why = f"exit {r.returncode}，产物未落盘或 ffprobe 无可读视频/正时长"
        return [f"subs_alpha 自动生成失败（exit {r.returncode}）：{why}"
                f"（完整命令：{' '.join(cmd)}）"]
    try:
        key_file.write_text(json.dumps(key, ensure_ascii=False, sort_keys=True) + "\n",
                            encoding="utf-8")
    except OSError as exc:
        return [f"subs_alpha 缓存键写入失败：{exc}"]
    print(f"✓ blender-bridge：已生成 cache/subs_alpha.mov（{mov.stat().st_size} B，字幕透明轨）")
    return []


def finish(c):
    """缝 D 钩子（协议 v1.2）：`vtx all` 收尾自动导出 .blend。返回错误行列表。"""
    params = _params(c)
    if params is None:
        return []
    blender = shutil.which("blender")
    if blender is None:
        return ["blender 不在 PATH（timeline 已声明 blender_bridge 键要求自动导出 .blend）——"
                "安装 blender 后重跑 `vtx all`，或移除该键改用手动导出"]
    if not _JSON2BLEND.is_file():
        return [f"json2blend 脚本缺失：{_JSON2BLEND}（apps/blender-bridge 应用件不完整？）"]
    out = Path(params.get("out", "preview.blend"))
    out_abs = (c.root / out).resolve()
    if c.root.resolve() not in out_abs.parents:
        return [f"{KEY}.out 越出项目根: {out}"]
    out_abs.parent.mkdir(parents=True, exist_ok=True)   # 子目录产物（如 audit/ep.blend）父目录自建
    errs = _ensure_subs_alpha(c)      # 缺陷1修复：字幕透明轨缺失 → 自动生成/指引
    if errs:
        return errs
    print(f"  · blender-bridge: 导出 {out}（json2blend，blender --background）")
    # 缝 A 产物映射（P6）：虚拟源（bgm 工程 JSON）/降噪源的音频走插件产物 wav，
    # json2blend 直引源路径会对 JSON 源挂掉（v1 盲区——denoise 源同样被忽略）
    resolve_map = source_resolve_map(c)
    map_file = None
    extra_args = []
    if resolve_map:
        map_file = c.cache / "tmp" / "blender_bridge_source_map.json"
        map_file.parent.mkdir(parents=True, exist_ok=True)
        map_file.write_text(json.dumps(resolve_map, ensure_ascii=False), encoding="utf-8")
        extra_args = [map_file]
        print(f"    源产物映射：{len(resolve_map)} 源经插件解析（bgm/denoise 产物 wav）")
    try:
        cmd, err = _run_blender_script(blender, _JSON2BLEND,
                                       [c.tl_path, out_abs, *extra_args],
                                       "json2blend 导出")
    finally:
        if map_file is not None:
            map_file.unlink(missing_ok=True)
    if err:
        return [err + f"（完整命令：{' '.join(cmd[:6])} …）"]
    if not out_abs.is_file():
        return [f"json2blend 声称成功但产物未落盘: {out_abs}"]
    print(f"✓ blender-bridge：已导出 {out}（{out_abs.stat().st_size} B，审片工程）")
    if params.get("verify"):
        # verify_blend 契约：blender --python verify_blend.py -- <timeline> <blend>
        cmd, err = _run_blender_script(blender, _VERIFY, [c.tl_path, out_abs],
                                       "verify_blend 校验")
        if err:
            return [err]
        print(f"✓ blender-bridge：verify_blend 回读校验通过（{out}）")
    return []
