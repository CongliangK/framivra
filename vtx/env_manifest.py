#!/usr/bin/env python3
"""env_manifest.py — 跨后端渲染环境指纹采集器（GFX 离屏渲染双路线战役 Phase 0，
issue #102；深化 #48 D05 Chrome 环境指纹，支撑 #62/#63/#69 的像素比对/基准前置门禁）。

不同 Linux 显示环境（纯 Headless / Xvfb / X11 / Wayland）、字体配置微差
（Noto Sans CJK SC 版本不一致）或 GPU 驱动静默回退（命令行传了启用 GPU 但
Chromium 内部回退 SwiftShader）会导致像素级幽灵差异——本模块在每次实验/
基准启动时产出**不可伪造**的 `env_manifest.json`，作为跨后端像素比对的
环境一致性前置门禁证据（消费方：scripts/gfx/gfx_golden.py compare --env-manifest）。

manifest 顶层 schema（"vtx.env_manifest/1"，字段名=issue #102 工单口径）：
  run_id                "YYYYMMDDTHHMMSSZ-<8位随机hex>"（UTC + secrets.token_hex）
  backend_id            调用方传入的后端路线标识（如 cef-route-a / electron-route-b）
  collected_at_utc      ISO 秒级 UTC
  git_sha / git_clean / uncommitted / baseline_sha / baseline_deviation
                        git rev-parse HEAD + 工作区 clean 校验；baseline_deviation
                        = at_baseline|descendant|diverged_or_unknown|unknown
                        （基线 70b6853=v2.0.1 偏离状态；git 不可得 → git_sha=None +
                        git_error 显形，fail-open 不编造）
  os_kernel             platform.uname() 五字段 dict
  cpu_model             /proc/cpuinfo "model name"（回退 platform）
  ram_total_mib         /proc/meminfo MemTotal（回退 sysconf；再不可得 → None）
  display_server        {session_type, env, xdpyinfo, x_servers, xvfb:{...}}——
                        DISPLAY/WAYLAND_DISPLAY/XDG_SESSION_TYPE 探测 + /proc 进程表
                        与 xdpyinfo 精确识别当前是否运行在 Xvfb 虚拟帧缓冲下
  gpu_driver_and_vram   nvidia-smi 单查（2.0s 超时）→ {"gpus":[…]}；任何失效
                        fail-open 记录字符串 "unavailable"（同源
                        vtx/encoders._vram_query_used_mib 的安全调用方式）
  fonts_fingerprint    {status, required:{<家族>:{status,files:{路径:sha256}}},
                        "sans-serif":{…}}——fc-list : file family 枚举项目必需字体
                        （字幕烧录用 Noto Sans CJK SC=vtx/composite.py ass_head、
                        gfx 模板引用的 Inter / JetBrains Mono），外加 fc-match 解析
                        系统无衬线回退；哈希=字体文件真实内容 sha256（非路径）
  runtime_backend_truth 生效后端唯一权威（见下），视口/色彩空间同居于此（一事一家）

runtime_backend_truth 强制外部传入（`collect_env_manifest(backend_id,
runtime_gpu_info)`，None 即拒绝）：生效后端必须来自**渲染进程启动后的内部接口
实测**——CDP=Browser.getVersion+SystemInfo.getInfo / CEF=CefGetVersion+GL 探针 /
Electron=app.getGPUFeatureStatus()+app.getGPUInfo('complete')——严禁仅转录命令行
启动参数。runtime_gpu_info 结构：
  {"effective_backend": "nvidia_egl"|"swiftshader"|"vulkan"|"software_skia",
   "source": "cdp"|"cef"|"electron",
   "evidence": {<按 source 定的内部接口原始返回，见 SOURCE_REQUIRED_EVIDENCE>},
   "chromium_version": "152.0.6834.83",        # 可选；缺省从 evidence 推导
   "viewport": {"width":1920,"height":1080},   # 可选（也收 "WxH" / [w,h]）
   "color_space": "srgb"}                       # 可选，缺省 "srgb"

verify_manifest_compatibility(a, b) 比对：Chromium 主版本、字体 SHA256（逐家族+
sans-serif 回退）、色彩空间（srgb）、视口尺寸、生效后端（GPU 静默回退检出）；
证据不可得按不兼容处理（无法证明一致 = 不能比像素）。双侧同缺某字体=兼容
（回退路径一致，回退目标由 sans-serif 指纹另行把关）。

落盘走 vtx/fstxn.atomic_write（同目录 tmp + os.replace）：并发或中断下不产生
半截 JSON。CLI：`python3 -m vtx.env_manifest collect|verify --help`。

tests 经 _run_cmd / _read_text / _read_bytes / _list_x_servers 四个 seam 注入桩
（tests/plugins/test_env_manifest.py 全密封离线，不起真 nvidia-smi/fc-list/git/chrome）。
"""
from __future__ import annotations   # requires-python>=3.9：dict|None 注解需之

import argparse
import hashlib
import json
import os
import platform
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__:   # 包内一律相对导入（vtx/__init__ §1.4）；flat 实例仅限
    from .fstxn import atomic_write   # `python3 vtx/env_manifest.py` 直跑——
else:             # atomic_write 无锁无状态，不涉 receipt/manifest 锁裂分面
    from fstxn import atomic_write

__all__ = ["collect_env_manifest", "verify_manifest_compatibility",
           "write_manifest", "classify_gl_backend", "main"]

# issue #102 基线（v2.0.1）：偏离状态显形记录，供跨后端实验对齐同一基线
BASELINE_SHA = "70b6853b496540b2529da6bfe47bcba37ee5b0df"

# 生效后端词表（issue #102：区分四类；GPU 静默回退=swiftshader/software_skia）
RUNTIME_BACKENDS = ("nvidia_egl", "swiftshader", "vulkan", "software_skia")

# 内部接口来源词表：命令行启动参数不构成来源（严禁转录）
INTERNAL_SOURCES = ("cdp", "cef", "electron")

# 各 source 必须携带的 evidence 键（内部接口原始返回；缺一即拒绝生成 manifest）
SOURCE_REQUIRED_EVIDENCE = {
    "cdp": ("browser_version", "system_info"),       # Browser.getVersion + SystemInfo.getInfo
    "cef": ("cef_version", "gl_probe"),              # CefGetVersion + GL 探针
    "electron": ("gpu_feature_status", "gpu_info_complete"),  # GPUFeatureStatus + GPUInfo
}

# 项目必需字体（issue #102）：Noto Sans CJK SC=字幕烧录（vtx/composite.py
# ass_head 的 libass 字体）、Inter/JetBrains Mono=gfx HTML 模板引用；
# sans-serif=系统无衬线回退（fc-match 解析），四者同入指纹。
REQUIRED_FONT_FAMILIES = ("Noto Sans CJK SC", "Inter", "JetBrains Mono")
GENERIC_FALLBACK_FAMILY = "sans-serif"

GPU_QUERY = ["nvidia-smi",
             "--query-gpu=name,driver_version,memory.total,memory.used",
             "--format=csv,noheader"]
GPU_QUERY_TIMEOUT = 2.0     # 工单成文值（同 _vram_query_used_mib 阈值）
FC_TIMEOUT = 10.0
XDPYINFO_TIMEOUT = 3.0
GIT_TIMEOUT = 5.0

_X_SERVER_COMMS = ("Xvfb", "Xorg", "Xwayland", "Xvnc")


# ---------------------------------------------------------------- 采集 seam（tests 注入桩）

def _run_cmd(argv, timeout):
    """安全子进程调用（fail-open）：命令不可得（OSError/FileNotFoundError）、
    超时（TimeoutExpired）或任何 SubprocessError → None，绝不抛出伤采集主链。
    调用方式同源 vtx/encoders._vram_query_used_mib（capture_output+text+2s 级超时）。"""
    try:
        return subprocess.run(list(argv), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _read_bytes(path):
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


def _list_x_servers():
    """/proc 进程表扫描：当前在跑的 X 服务器 [(pid, comm, cmdline)]。
    非 Linux/无 /proc → 空表（探测面在 display_server.x_servers 显形，不臆测）。"""
    out = []
    try:
        names = os.listdir("/proc")
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        comm = _read_text(f"/proc/{name}/comm")
        if comm is None or comm.strip() not in _X_SERVER_COMMS:
            continue
        raw = _read_bytes(f"/proc/{name}/cmdline")
        cmdline = [tok.decode("utf-8", "replace") for tok in raw.split(b"\0") if tok] if raw else []
        out.append({"pid": int(name), "comm": comm.strip(), "cmdline": cmdline})
    return out


# ---------------------------------------------------------------- 各字段采集器

def _new_run_id() -> str:
    """UTC 时间戳 + 8 位随机 hex（secrets：不可预测，两 run 不重号）。"""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)


def _collect_git_state(repo: Path) -> dict:
    """git rev-parse HEAD + 工作区 clean 校验 + 基线偏离状态。git 不可得 →
    fail-open 显形（git_sha=None/git_error 在册），绝不编造 sha。"""
    def git(*args):
        return _run_cmd(["git", "-C", str(repo), *args], GIT_TIMEOUT)

    state = {"git_sha": None, "git_clean": None, "uncommitted": [],
             "baseline_sha": BASELINE_SHA, "baseline_deviation": "unknown"}
    r = git("rev-parse", "HEAD")
    if r is None or r.returncode != 0:
        why = "git 不可得" if r is None else f"exit={r.returncode}"
        state["git_error"] = f"git rev-parse HEAD 失败（{why}），sha 未采集"
        return state
    state["git_sha"] = (r.stdout or "").strip() or None
    if state["git_sha"] is None:
        state["git_error"] = "git rev-parse HEAD 输出为空"
        return state
    s = git("status", "--porcelain")
    if s is not None and s.returncode == 0:
        entries = [ln for ln in (s.stdout or "").splitlines() if ln.strip()]
        state["uncommitted"] = entries[:50]        # 封顶防爆manifest（超50条加省略行）
        if len(entries) > 50:
            state["uncommitted_truncated"] = True
        state["git_clean"] = not entries
    if state["git_sha"] == BASELINE_SHA:
        state["baseline_deviation"] = "at_baseline"
        return state
    m = git("merge-base", "--is-ancestor", BASELINE_SHA, "HEAD")
    if m is not None and m.returncode == 0:
        state["baseline_deviation"] = "descendant"     # 基线是 HEAD 祖先：纯前进
    else:
        state["baseline_deviation"] = "diverged_or_unknown"  # 分叉/浅克隆无基线
    return state


def _collect_os_kernel() -> dict:
    u = platform.uname()
    return {"system": u.system, "node": u.node, "release": u.release,
            "version": u.version, "machine": u.machine, "processor": u.processor}


def _collect_cpu_model() -> str:
    text = _read_text("/proc/cpuinfo")
    if text:
        for line in text.splitlines():
            if line.startswith("model name"):
                value = line.partition(":")[2].strip()
                if value:
                    return value
    return platform.processor() or platform.uname().machine or "unknown"


def _collect_ram_total_mib():
    text = _read_text("/proc/meminfo")
    if text:
        for line in text.splitlines():
            if line.startswith("MemTotal:"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return round(int(parts[1]) / 1024)   # meminfo "kB" 实为 KiB
    try:   # 回退：sysconf 物理页总数（Linux/macOS）
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
                     / 1024 / 1024)
    except (ValueError, OSError, AttributeError):
        return None


def _norm_display(value):
    """:99.0 → :99（display 号归一；screen 号不参与 Xvfb 归属比对）。"""
    if not value:
        return None
    m = re.match(r"^(:\d+)", str(value))
    return m.group(1) if m else None


def _collect_display_server() -> dict:
    """DISPLAY / WAYLAND_DISPLAY / XDG_SESSION_TYPE 探测 + xdpyinfo 佐证 +
    /proc 进程表精确识别当前 DISPLAY 是否由 Xvfb 虚拟帧缓冲持有。"""
    env = {k: os.environ.get(k) for k in
           ("DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE")}
    if env["WAYLAND_DISPLAY"]:
        session = "wayland"
    elif env["DISPLAY"]:
        session = "x11"
    elif env["XDG_SESSION_TYPE"]:
        session = env["XDG_SESSION_TYPE"]
    else:
        session = "none"

    xdpyinfo = None
    if env["DISPLAY"]:
        r = _run_cmd(["xdpyinfo"], XDPYINFO_TIMEOUT)
        if r is not None and r.returncode == 0:
            info = {"available": True}
            for line in (r.stdout or "").splitlines():
                for key in ("name of display", "vendor string",
                            "version number", "vendor release number"):
                    if line.strip().startswith(key):
                        info[key] = line.partition(":")[2].strip()
            xdpyinfo = info
        else:
            why = "不可得" if r is None else f"exit={r.returncode}"
            xdpyinfo = {"available": False, "note": f"xdpyinfo {why}"}

    servers = _list_x_servers()
    xvfb_servers = [s for s in servers if s["comm"] == "Xvfb"]
    want = _norm_display(env["DISPLAY"])
    matched = []
    if want:
        for srv in xvfb_servers:
            if any(_norm_display(arg) == want for arg in srv["cmdline"]):
                matched.append(srv)
    evidence = None
    if matched:
        srv = matched[0]
        evidence = (f"PID {srv['pid']} Xvfb 持有 {want}"
                    f"（cmdline={' '.join(srv['cmdline'][:6])}，与 DISPLAY={env['DISPLAY']} 匹配）")
    elif want and xvfb_servers:
        evidence = (f"检测到 {len(xvfb_servers)} 个 Xvfb 进程但均不持有 {want}"
                    f"（当前 DISPLAY 非其虚拟帧缓冲）")
    return {
        "session_type": session,
        "env": env,
        "xdpyinfo": xdpyinfo,
        "x_servers": servers,
        "xvfb": {
            "servers_running": len(xvfb_servers),
            # 无 DISPLAY 时无法归属 → None（区别于 False=当前不在 Xvfb 下）
            "under_xvfb": bool(matched) if want else None,
            "evidence": evidence,
        },
    }


def _mib_or_none(token: str):
    """"16376 MiB" → 16376；"[N/A]"/畸形 → None（encoders 口径）。"""
    token = token.strip()
    if token.casefold().endswith("mib"):
        token = token[:-3].strip()
    return int(token) if token.isdigit() else None


def _collect_gpu_driver_and_vram():
    """nvidia-smi 单查（GPU 逐卡 name/driver_version/memory.total/memory.used）。
    成功 → {"gpus":[…]}；任何失效（不可得/超时/非零退出/输出畸形）→ 字符串
    "unavailable"（fail-open 显形，与 _vram_query_used_mib 同口径；宁缺毋造：
    半张 GPU 表恰会掩盖要猎捕的驱动回退）。"""
    r = _run_cmd(GPU_QUERY, GPU_QUERY_TIMEOUT)
    if r is None or r.returncode != 0:
        return "unavailable"
    gpus = []
    for line in (r.stdout or "").strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 4:
            return "unavailable"
        total, used = _mib_or_none(parts[2]), _mib_or_none(parts[3])
        if total is None or used is None:
            return "unavailable"
        gpus.append({"name": parts[0], "driver_version": parts[1],
                     "memory_total_mib": total, "memory_used_mib": used})
    return {"gpus": gpus} if gpus else "unavailable"


def _sha256_file(path: str):
    data = _read_bytes(path)
    return None if data is None else hashlib.sha256(data).hexdigest()


def _parse_fc_list(stdout: str) -> dict:
    """fc-list 行 `<file>: <family>[,<family>…]` → {file: set(family)}。"""
    mapping = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        file_part, sep, rest = line.partition(": ")
        if not sep or not file_part:
            continue
        families = {f.strip().strip('"') for f in rest.split(",") if f.strip()}
        mapping.setdefault(file_part, set()).update(families)
    return mapping


def _fc_match_file(pattern: str):
    """fc-match 解析泛型名（sans-serif）→ 具体字体文件全路径。

    首选 `--format %{file}`（本机实测默认输出只给 basename——裸文件名相对
    cwd 不可哈希，曾致 sans-serif 误判 unreadable）；--format 不可用/空输出时
    回退解析默认输出 `<file>: <family>`。两路皆失效 → None。"""
    r = _run_cmd(["fc-match", "--format", "%{file}", pattern], FC_TIMEOUT)
    if r is not None and r.returncode == 0:
        lines = (r.stdout or "").strip().splitlines()
        if lines and lines[0].strip():
            return lines[0].strip()
    r = _run_cmd(["fc-match", pattern], FC_TIMEOUT)
    if r is not None and r.returncode == 0:
        lines = (r.stdout or "").strip().splitlines()
        if lines:
            file_part, sep, _ = lines[0].partition(": ")
            if sep and file_part:
                return file_part
    return None


def _collect_fonts_fingerprint() -> dict:
    """fc-list : file family 枚举必需字体 + 内容 SHA256 哈希表。
    fc-list 不可得/失败 → status="unavailable"（显形；哈希表宁缺毋造）。"""
    r = _run_cmd(["fc-list", ":", "file", "family"], FC_TIMEOUT)
    if r is None or r.returncode != 0:
        note = "fc-list 不可得" if r is None else f"fc-list exit={r.returncode}"
        return {"status": "unavailable",
                "required": {fam: {"status": "unavailable", "files": {}}
                             for fam in REQUIRED_FONT_FAMILIES},
                GENERIC_FALLBACK_FAMILY: {"status": "unavailable", "files": {}},
                "note": f"{note}（fontconfig 未装？字体一致性无法证明）"}

    mapping = _parse_fc_list(r.stdout or "")
    hashed = {}   # file → sha（家族间共用文件只哈希一次，如 sans-serif↔Noto）

    def entry(files):
        files = sorted(set(files))
        if not files:
            return {"status": "missing", "files": {}}
        hashes, unreadable = {}, False
        for f in files:
            if f not in hashed:
                hashed[f] = _sha256_file(f)
            hashes[f] = hashed[f]
            if hashed[f] is None:
                unreadable = True
        return {"status": "unreadable" if unreadable else "ok", "files": hashes}

    required = {}
    for fam in REQUIRED_FONT_FAMILIES:
        want = fam.casefold()   # fontconfig 家族匹配大小写不敏感
        hit = [f for f, fams in mapping.items()
               if any(x.casefold() == want for x in fams)]
        required[fam] = entry(hit)
    sans_file = _fc_match_file(GENERIC_FALLBACK_FAMILY)
    if sans_file is None:
        sans = {"status": "unavailable", "files": {},
                "note": "fc-match 不可得，系统无衬线回退未解析"}
    else:
        sans = entry([sans_file])
    return {"status": "ok", "required": required, GENERIC_FALLBACK_FAMILY: sans}


# ---------------------------------------------------------------- runtime_backend_truth

def classify_gl_backend(renderer: str, angle_backend: str = "") -> str:
    """GL 探针实测串 → 生效后端类别（best-effort 纯映射；CEF GL 探针路线用）。

    词表粗粒度（issue #102 四类）：NVIDIA 硬件一律归 nvidia_egl（Linux Chromium
    GPU 进程原生走 EGL 族表面；GLX 亦并入此桶，桶名不区分 EGL/GLX 细节）。
    归类不出 → ""（调用方必须改用其他内部证据定值；collect 拒绝空值/自造值）。
    CDP/Electron 路线有结构化证据（SystemInfo.getInfo / GPUFeatureStatus），
    应直接据其定值，不必走本串匹配。"""
    s = (renderer or "").casefold()
    a = (angle_backend or "").casefold()
    if "swiftshader" in s:
        return "swiftshader"
    if any(k in s for k in ("llvmpipe", "softpipe", "softfp", "software")):
        return "software_skia"
    if "vulkan" in s or a == "vulkan":
        return "vulkan"
    if any(k in s for k in ("nvidia", "geforce", "quadro", "tesla")):
        return "nvidia_egl"
    return ""


_CHROMIUM_VER_RE = re.compile(r"(?:Chrome|Chromium|CEF)[/\s](\d+)")


def _chromium_version(runtime_gpu_info: dict):
    """(版本串, 主版本 int|None)：显式 chromium_version 优先，否则从 CDP
    browser_version.product/userAgent 推导；不可推导 → (None, None)。"""
    value = runtime_gpu_info.get("chromium_version")
    if not value:
        bv = runtime_gpu_info.get("evidence", {}).get("browser_version")
        if isinstance(bv, dict):
            for key in ("product", "Browser", "userAgent", "User-Agent"):
                if bv.get(key):
                    value = bv[key]
                    break
    if not value:
        return None, None
    text = str(value)
    m = _CHROMIUM_VER_RE.search(text)
    if m:
        return text, int(m.group(1))
    head = text.split(".")[0]
    return (text, int(head)) if head.isdigit() else (text, None)


def _viewport_str(viewport):
    """{"width":1920,"height":1080} / "1920x1080" / [1920,1080] → "1920x1080"。"""
    if not viewport:
        return None
    if isinstance(viewport, dict):
        w, h = viewport.get("width"), viewport.get("height")
    elif isinstance(viewport, (list, tuple)) and len(viewport) == 2:
        w, h = viewport
    elif isinstance(viewport, str):
        parts = viewport.lower().split("x")
        if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
            return f"{int(parts[0])}x{int(parts[1])}"
        return None
    else:
        return None
    try:
        return f"{int(w)}x{int(h)}" if w and h else None
    except (TypeError, ValueError):
        return None


def _runtime_backend_truth(backend_id: str, runtime_gpu_info) -> dict:
    """生效后端唯一权威。强制内部接口实测（issue #102 严禁转录命令行参数）：
    runtime_gpu_info=None / source 不在内部接口词表 / evidence 缺该 source 必备
    键 → 一律 ValueError 拒绝生成 manifest。"""
    if runtime_gpu_info is None:
        raise ValueError(
            "runtime_gpu_info 必须传入渲染进程启动后从内部接口实测得到的生效后端数据"
            "（CDP=Browser.getVersion+SystemInfo.getInfo / CEF=CefGetVersion+GL 探针 /"
            " Electron=app.getGPUFeatureStatus()+app.getGPUInfo('complete')）——"
            "严禁仅凭命令行启动参数生成 manifest（issue #102 不可伪造约束）")
    if not isinstance(runtime_gpu_info, dict):
        raise ValueError("runtime_gpu_info 必须是 dict（内部接口实测值的结构化载体），"
                         f"得到 {type(runtime_gpu_info).__name__}")
    backend = runtime_gpu_info.get("effective_backend")
    if backend not in RUNTIME_BACKENDS:
        raise ValueError(f"effective_backend={backend!r} 不在词表 {RUNTIME_BACKENDS}——"
                         "生效后端必须由内部接口实测归类，不得留空/自造值")
    source = runtime_gpu_info.get("source")
    if source not in INTERNAL_SOURCES:
        raise ValueError(f"source={source!r} 不在内部接口来源 {INTERNAL_SOURCES}——"
                         "命令行启动参数不构成生效后端证据来源（issue #102 严禁转录）")
    evidence = runtime_gpu_info.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        raise ValueError("evidence 必须是非空 dict（内部接口原始返回，供对抗审计复核）")
    missing = [k for k in SOURCE_REQUIRED_EVIDENCE[source] if k not in evidence]
    if missing:
        raise ValueError(f"source={source} 缺内部接口证据键 {missing}"
                         f"（须实测取得 {SOURCE_REQUIRED_EVIDENCE[source]}）")
    version, major = _chromium_version(runtime_gpu_info)
    return {
        "backend_id": backend_id,
        "effective_backend": backend,
        "source": source,
        "chromium_version": version,
        "chromium_major": major,
        "viewport": _viewport_str(runtime_gpu_info.get("viewport")),
        "color_space": str(runtime_gpu_info.get("color_space") or "srgb"),
        "evidence": evidence,
        "policy": "生效后端仅采信渲染进程内部接口实测值；命令行启动参数不作证据",
    }


# ---------------------------------------------------------------- 主入口

def collect_env_manifest(backend_id: str, runtime_gpu_info: dict | None = None) -> dict:
    """采集当前环境的跨后端渲染指纹（issue #102 工单项逐字段）。

    backend_id：本次渲染路线标识（如 cdp-baseline / cef-route-a / electron-route-b）。
    runtime_gpu_info：渲染进程内部接口实测值（结构见模块头）——**强制传入**，
    None/结构不符/证据缺键即 ValueError（生效后端不可伪造，见 _runtime_backend_truth）。
    其余采集面（git/os/display/gpu/fonts）fail-open 显形：不可得记 "unavailable"/None，
    绝不编造。返回可 JSON 序列化 dict；落盘用 write_manifest（fstxn.atomic_write）。
    """
    if not isinstance(backend_id, str) or not backend_id:
        raise ValueError("backend_id 必须是非空 str（后端路线标识，如 cef-route-a）")
    repo = Path(__file__).resolve().parents[1]
    return {
        "schema": "vtx.env_manifest/1",
        "run_id": _new_run_id(),
        "backend_id": backend_id,
        "collected_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **_collect_git_state(repo),   # 顶层平铺：git_sha/git_clean/uncommitted/
        #                              baseline_sha/baseline_deviation（工单字段口径）
        "os_kernel": _collect_os_kernel(),
        "cpu_model": _collect_cpu_model(),
        "ram_total_mib": _collect_ram_total_mib(),
        "display_server": _collect_display_server(),
        "gpu_driver_and_vram": _collect_gpu_driver_and_vram(),
        "fonts_fingerprint": _collect_fonts_fingerprint(),
        "runtime_backend_truth": _runtime_backend_truth(backend_id, runtime_gpu_info),
    }


def _font_incompatibilities(fa, fb) -> list:
    """字体维度不兼容项：逐必需家族 + sans-serif 回退，比对内容 SHA256 集合。"""
    def status_of(f):
        return f.get("status") if isinstance(f, dict) else "缺失"

    if status_of(fa) != "ok" or status_of(fb) != "ok":
        return [f"字体指纹不可得：a={status_of(fa)} b={status_of(fb)}"
                f"（fc-list 枚举失败/未记录，字体一致性无法证明 → 不能比像素）"]
    items = []
    for fam in (*REQUIRED_FONT_FAMILIES, GENERIC_FALLBACK_FAMILY):
        ea = (fa.get("required") or {}).get(fam) or fa.get(fam) or {}
        eb = (fb.get("required") or {}).get(fam) or fb.get(fam) or {}
        sa, sb = ea.get("status"), eb.get("status")
        if sa == "missing" and sb == "missing":
            continue   # 双侧同缺：回退路径一致（回退目标由 sans-serif 指纹把关）
        if sa != sb or "ok" not in (sa, sb):
            items.append(f"字体 {fam} 状态不一致：a={sa} b={sb}"
                         "（一侧缺失/不可读，字形回退行为分叉）")
            continue
        ha = sorted(h for h in (ea.get("files") or {}).values() if h)
        hb = sorted(h for h in (eb.get("files") or {}).values() if h)
        if ha != hb:
            items.append(f"字体 {fam} 文件 SHA256 不一致：a={len(ha)} 个哈希 "
                         f"b={len(hb)} 个哈希（字体版本/字形差异 → 像素幽灵差异源）")
    return items


def verify_manifest_compatibility(manifest_a: dict, manifest_b: dict) -> list:
    """跨后端环境一致性门禁（issue #102）：比对两份 manifest 的

    ① Chromium 主版本 ② 生效后端（GPU 静默回退检出：如 nvidia_egl→swiftshader）
    ③ 色彩空间（基准 srgb） ④ 视口尺寸 ⑤ 字体 SHA256（逐必需家族+sans-serif 回退）

    返回不兼容项列表（人读中文句子；空表=通过）。证据不可得（任一侧主版本/视口/
    字体指纹缺失/生效后端缺失或未知）按不兼容处理——无法证明一致就不能比像素；
    双侧同缺某具体字体家族=兼容（回退路径一致）。消费方：scripts/gfx/gfx_golden.py compare --env-manifest
    在跨后端像素比对前作前置门禁（非空 → 拒绝比对）。

    入参必须是 JSON 对象（dict）：null/字符串/列表/数字（手改或损坏的 manifest）
    → ValueError 干净拒收（含实际类型名，fail-closed；对齐 gfx_golden
    load_env_manifest 加载路对 null/非对象的拒收口径）。"""
    for label, m in (("manifest_a", manifest_a), ("manifest_b", manifest_b)):
        if not isinstance(m, dict):
            raise ValueError(f"{label} 不是 JSON 对象（得到 {type(m).__name__}）——"
                             "manifest 损坏或被手改，拒绝比对（fail-closed）")
    a, b = manifest_a, manifest_b
    ta = a.get("runtime_backend_truth") or {}
    tb = b.get("runtime_backend_truth") or {}
    items = []

    ma, mb = ta.get("chromium_major"), tb.get("chromium_major")
    if ma is None or mb is None:
        items.append(f"Chromium 主版本证据不可得：a={ma} b={mb}"
                     "（无法证明一致 → 不能比像素）")
    elif ma != mb:
        items.append(f"Chromium 主版本不一致：a={ma} b={mb}（光栅化行为随大版本漂移）")

    ea, eb = ta.get("effective_backend"), tb.get("effective_backend")
    if ea not in RUNTIME_BACKENDS or eb not in RUNTIME_BACKENDS:
        items.append(f"生效后端证据不可得或未知：a={ea} b={eb}"
                     "（无法证明一致 → 不能比像素）")
    elif ea != eb:
        items.append(f"生效后端不一致：a={ea} b={eb}"
                     "（一侧疑似 GPU 静默回退：swiftshader/software_skia，像素不可比）")

    ca, cb = ta.get("color_space") or "srgb", tb.get("color_space") or "srgb"
    if ca != cb:
        items.append(f"色彩空间不一致：a={ca} b={cb}（跨后端像素比对基准要求 srgb）")

    va, vb = ta.get("viewport"), tb.get("viewport")
    if va is None or vb is None:
        items.append(f"视口尺寸证据不可得：a={va} b={vb}（无法证明一致 → 不能比像素）")
    elif va != vb:
        items.append(f"视口尺寸不一致：a={va} b={vb}（光栅网格不同，逐帧 hash 必差）")

    items.extend(_font_incompatibilities(a.get("fonts_fingerprint"),
                                         b.get("fonts_fingerprint")))
    return items


def write_manifest(path, manifest: dict) -> Path:
    """manifest 落盘（issue #102：走 vtx/fstxn.atomic_write——同目录 tmp +
    os.replace，并发/中断下不产生半截 JSON）。"""
    payload = json.dumps(manifest, ensure_ascii=False, indent=2)
    atomic_write(Path(path), payload)
    return Path(path)


# ---------------------------------------------------------------- CLI

def _load_json_file(path_str: str):
    try:
        return json.loads(Path(path_str).read_text(encoding="utf-8")), None
    except (OSError, ValueError) as error:
        return None, f"{path_str} 读取失败（{error}）"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m vtx.env_manifest",
        description="跨后端渲染环境指纹采集器（issue #102）：采集 env_manifest.json "
                    "并做跨后端一致性门禁（供 gfx_golden 跨后端像素比对前置校验）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="采集当前环境指纹并落盘（须提供渲染进程内部接口实测值 JSON）")
    c.add_argument("--backend-id", required=True,
                   help="后端路线标识（如 cdp-baseline / cef-route-a / electron-route-b）")
    c.add_argument("--runtime-info", required=True,
                   help="渲染进程内部接口实测值 JSON 路径（结构见 vtx/env_manifest.py 模块头；"
                        "CDP=Browser.getVersion+SystemInfo.getInfo / CEF=CefGetVersion+GL 探针 /"
                        " Electron=GPUFeatureStatus+GPUInfo）")
    c.add_argument("--out", required=True, help="输出 env_manifest.json 路径")

    v = sub.add_parser("verify", help="比对两份 env manifest 的跨后端一致性（不兼容项非零退出）")
    v.add_argument("manifest_a", help="manifest A 路径（如 golden 内嵌基线）")
    v.add_argument("manifest_b", help="manifest B 路径（如当前渲染实测）")

    args = ap.parse_args(argv)
    if args.cmd == "collect":
        info, err = _load_json_file(args.runtime_info)
        if err:
            print(f"✗ {err}")
            return 1
        try:
            manifest = collect_env_manifest(args.backend_id, info)
        except ValueError as error:
            print(f"✗ 拒绝生成 manifest：{error}")
            return 1
        write_manifest(args.out, manifest)
        truth = manifest["runtime_backend_truth"]
        print(f"✓ env manifest 已写入 {args.out}（run_id={manifest['run_id']} "
              f"backend={args.backend_id} effective={truth['effective_backend']} "
              f"chromium_major={truth['chromium_major']} viewport={truth['viewport']}）")
        return 0

    a, err = _load_json_file(args.manifest_a)
    if err:
        print(f"✗ manifest_a {err}")
        return 1
    b, err = _load_json_file(args.manifest_b)
    if err:
        print(f"✗ manifest_b {err}")
        return 1
    try:
        items = verify_manifest_compatibility(a, b)
    except ValueError as error:
        print(f"✗ {error}")
        return 1
    for it in items:
        print(f"  ✗ {it}")
    if items:
        print(f"✗ {len(items)} 项不兼容：跨后端像素比对前置门禁未通过（先排查环境再比像素）")
        return 1
    print("✓ 两份 manifest 环境一致（Chromium 主版本/生效后端/色彩空间/视口/字体 SHA256）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
