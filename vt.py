#!/usr/bin/env python3
"""vt —— Framivra(video_tools 仓)v2 统一入口（ARCHITECTURE §5.1，吸收原 init.py 全部语义）。

子命令：
  doctor [--json] [--fix]  三检：①版本（本地 HEAD vs origin/HEAD 漂移；真 fetch
                         5s 超时，VTX_DOCTOR_OFFLINE=1 或超时降级 WARN；
                         clean/dirty/ahead/behind；VERSION 展示，无 tag 不告警）
                         ②组件（registry 全量 apps/plugins 的 manifest 有效 +
                         health 探测 + requires 依赖；enabled 才 FAIL 门——未装/
                         disabled 仅提示；附 `import vtx` 实际解析路径自检）
                         ③skills（工作流包完好性；未注册包如 examples/skill-demo
                         不在扫描面，不误报）
  doctor --fix          修复三检中可自动修复项：挂载链接重建（联动 --fix-links
                         同表；live 挂载指向其他仓时只报人工项不自动改）+
                         plugins/registry.json 缺字段回填（manifest 为声明数据源，
                         §2.3）。无可自动修复项时明确说明。
  apps|plugins|skills list|check
                         list = 注册表 + 安装态清单；check = doctor 组件段单层体检
  version                VERSION 内容 + git HEAD 短哈希
  init [--fix] [--json]  原 init.py 语义（七小节自检 + --fix 自动装基础依赖 +
                         config.local.json 幂等生成）
  --fix-links [--dry]    重建挂载符号链接（skill 5 链接 + 第 6 条 skills 链接，指向本仓
                         对应目录；VT_MOUNT_ROOT 可覆盖挂载根——测试/多仓并存用）

子进程纪律（全局 AGENTS.md argv[0] 劫持 / §5.1）：一律 shutil.which 定位 python3，
禁 sys.executable；零 spec_from_file_location、零跨目录 import——原 init.py 对
asr/client.py、imgen/secrets.py 的 spec 复用已废除（asr/imgen 现无探针子命令，
保留为 config_key 型健康检查 = 读 config 判断；P2 迁移时各 app README 落实探针后
doctor 改走 manifest health.command 子进程探测）。

注：init 的 --fix（安装缺失基础依赖）与 doctor 的 --fix（修复可自动修复项，如
链接/注册表）语义不同，不得混用（§5.1）。
"""
import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OS = sys.platform   # linux / darwin / win32

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))   # repo 根（clone 即用；pip 安装时 ROOT 已在 path）

# Windows 控制台/管道默认 GBK，✓✗ 中文输出会 UnicodeEncodeError（原 init.py 同款防护）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from vtx.pluginlib import config as _pcfg    # noqa: E402（统一加载，§5.3）
from vtx.pluginlib import registry as _preg  # noqa: E402（发现/静态注册表，§2.1）
from vtx.pluginlib.manifest import load_manifest as _load_manifest  # noqa: E402


def py3() -> str:
    """python3 解释器路径（shutil.which——禁 sys.executable：本机 AppImage argv[0]
    劫持会使其指向错误解释器，见全局 AGENTS.md）。找不到返回 None 由调用方报错。"""
    return shutil.which("python3") or shutil.which("python")


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _read_version() -> str:
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        try:
            import vtx
            return vtx.__version__
        except Exception:
            return "?"


# ================================================================ 依赖探测（init/doctor 共用）
def find_binary(names, extra_paths=()):
    for n in names:
        if n:
            p = shutil.which(n)
            if p:
                return p
    for p in extra_paths:
        if Path(p).is_file():
            return p
    return None


def find_chrome():
    """与 vtx.encoders.find_chrome 同源逻辑：VTX_CHROME 优先 → PATH → 标准安装路径。"""
    exe = ".exe" if OS == "win32" else ""
    names = (os.environ.get("VTX_CHROME"), "google-chrome", "google-chrome-stable",
             "chromium", "chromium-browser", "chrome", "msedge")
    paths = []
    if OS == "win32":
        w = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        p86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        la = os.environ.get("LOCALAPPDATA", "")
        paths = [rf"{w}\Google\Chrome\Application\chrome.exe",
                 rf"{p86}\Google\Chrome\Application\chrome.exe",
                 rf"{la}\Google\Chrome\Application\chrome.exe",
                 rf"{w}\Microsoft\Edge\Application\msedge.exe",
                 rf"{p86}\Microsoft\Edge\Application\msedge.exe"]
    elif OS == "darwin":
        paths = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/Applications/Chromium.app/Contents/MacOS/Chromium",
                 "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    return find_binary([n for n in names if n], paths)


def ffmpeg_encoders():
    r = sh(["ffmpeg", "-hide_banner", "-encoders"])
    return r.stdout or ""


def tool_version(cmd):
    r = sh([cmd, "-version"])
    return (r.stdout or r.stderr or "").splitlines()[0].strip() if (r.stdout or r.stderr) else ""


# ================================================================ 安装指引 / --fix（init）
FIX = {
    # 键 = rec() 的 fail 键名（python/ffmpeg/ffprobe/chrome，P6 修复 C-P2-1：v1 的
    # "google-chrome" 键与 fail 键 "chrome" 不匹配 → init --fix 对 chrome 恒"跳过"）
    "linux": {
        "dnf":  {"ffmpeg": ["dnf", "install", "-y", "ffmpeg"], "chrome": None},
        "apt":  {"ffmpeg": ["apt", "install", "-y", "ffmpeg"], "chrome": None},
        "pacman": {"ffmpeg": ["pacman", "-S", "--noconfirm", "ffmpeg"], "chrome": None},
    },
    "darwin": {"brew": {"ffmpeg": ["brew", "install", "ffmpeg"], "chrome": None}},
    "win32": {"winget": {
        "ffmpeg": ["winget", "install", "--id", "Gyan.FFmpeg", "-e", "--accept-source-agreements", "--accept-package-agreements"],
        "chrome": ["winget", "install", "--id", "Google.Chrome", "-e", "--accept-source-agreements", "--accept-package-agreements"],
        "blender": ["winget", "install", "--id", "BlenderFoundation.Blender", "-e"],
    }},
}

HINTS = {
    "ffmpeg": {
        "linux": "dnf install ffmpeg | apt install ffmpeg（RPM Fusion/ Deb 多媒体源）",
        "darwin": "brew install ffmpeg",
        "win32": "winget install Gyan.FFmpeg（注意：gyan.dev/BtbN 的 GPL 发行包均不含 libfdk_aac，见下方 libfdk_aac 条）",
    },
    "chrome": {
        "linux": "dnf install google-chrome-stable（Google 官方 repo）或 chromium",
        "darwin": "brew install --cask google-chrome",
        "win32": "winget install Google.Chrome（Edge 亦可：vtx 自动识别 msedge，系统自带 Edge 在 (x86) 目录也能定位）",
    },
    "libfdk_aac": "缺 libfdk_aac 时 vtx 自动回落原生 aac 并告警——正式交付需复测编码后解码 TP（edit.md 坑表）。"
                  "Linux 装 ffmpeg 自 fdk 构建（RPM Fusion / deb-multimedia）；Windows 的 gyan.dev/BtbN/winget "
                  "发行包均不含 fdk（nonfree 不可随 GPL 分发），需自编译或找 nonfree 自动构建（如 tytan650 "
                  "autobuild 系列），否则接受原生 aac 回落 + 交付前复测 TP。",
    "nvenc": "需要 NVIDIA 显卡 + NVIDIA 驱动；不可用时 vtx 自动回落 x264（慢但可用）。",
    "blender": {"linux": "dnf install blender", "darwin": "brew install --cask blender", "win32": "winget install BlenderFoundation.Blender"},
    "sox": {"linux": "dnf install sox", "darwin": "brew install sox", "win32": "winget install Gyan.FFmpeg 替代（sox 可选）"},
}


def pm_available():
    for pm in ("winget", "brew", "dnf", "apt", "pacman"):
        if shutil.which(pm):
            return pm
    return None


def try_fix(pkg_key, install_cmd):
    if not install_cmd:
        return "跳过（无自动安装命令，见指引）"
    if shutil.which(install_cmd[0]) is None:
        return f"跳过（无 {install_cmd[0]}）"
    r = sh(install_cmd)
    return "已执行安装命令" + ("" if r.returncode == 0 else f"（退出码 {r.returncode}，请手动检查）")


def _conda_env_py(env, hook):
    """conda env 解释器路径：环境变量钩子优先，其次按平台标准布局。"""
    hooked = os.environ.get(hook)
    if hooked:
        return Path(hooked)
    return Path.home() / "miniconda3" / "envs" / env / ("python.exe" if OS == "win32" else "bin/python")


# ================================================================ init（原 init.py 全部语义）
def cmd_init(a):
    res, fail = {}, []

    def rec(key, ok, detail=""):
        res[key] = {"ok": bool(ok), "detail": detail}
        (print(f"  {'✓' if ok else '✗'} {key}{(' — ' + detail) if detail else ''}") if not a.json else None)
        if not ok and key in ("python", "ffmpeg", "ffprobe", "chrome"):
            fail.append(key)

    say = (lambda s: None) if a.json else (lambda s: print(s))
    say(f"=== Framivra init（vt.py）===  platform={platform.system()} ({OS})")

    say("--- 1/5 基础依赖（vtx 渲染链必需）---")
    v = sys.version_info
    rec("python", (v.major, v.minor) >= (3, 9), f"{v.major}.{v.minor}.{v.micro}")
    ff = find_binary(["ffmpeg"])
    rec("ffmpeg", bool(ff), (tool_version("ffmpeg") if ff else HINTS["ffmpeg"].get(OS, "")).split("--")[0][:80])
    rec("ffprobe", bool(find_binary(["ffprobe"])))
    chrome = find_chrome()
    rec("chrome", bool(chrome), chrome or HINTS["chrome"].get(OS, ""))

    say("--- 2/5 编码能力（影响画质/速度，缺则自动回落）---")
    encs = ffmpeg_encoders() if ff else ""
    rec("libfdk_aac", "libfdk_aac" in encs, "" if "libfdk_aac" in encs else HINTS["libfdk_aac"])
    rec("nvenc", "h264_nvenc" in encs, "" if "h264_nvenc" in encs else HINTS["nvenc"])

    say("--- 3/5 可选工具 ---")
    rec("blender", bool(find_binary(["blender"])), "预览桥 blender_bridge/ 需要")
    rec("sox", bool(find_binary(["sox"])), "部分音频后处理")

    say("--- 3b/5 程序自检（本仓组件可运行性）---")
    interp = py3()
    if interp is None:
        rec("vtx_cli", False, "找不到 python3 解释器（PATH）")
    else:
        r = sh([interp, str(ROOT / "vtx" / "vtx.py"), "--help"])
        rec("vtx_cli", r.returncode == 0, "vtx/vtx.py（渲染 CLI）")
    rec("new_project", (ROOT / "scripts/new_project.sh").is_file(), "scripts/new_project.sh（bash 脚本；Windows 需 Git Bash/WSL）")
    for f in ("json2blend.py", "verify_blend.py", "blend2json.py", "subs_alpha.py"):
        try:
            compile((ROOT / "apps/blender-bridge" / f).read_text(encoding="utf-8"), f, "exec")
            rec(f"bridge_{f}", True, "语法门；运行需 blender --python")
        except (SyntaxError, OSError) as e:
            # Optional apps are intentionally absent in the kernel-only wheel.
            # Report missing/broken bridge files instead of crashing init.
            rec(f"bridge_{f}", False, str(e))
    if interp is None:
        rec("audit_bundle", False, "找不到 python3 解释器（PATH）")
    else:
        r = sh([interp, str(ROOT / "apps/blender-bridge/audit_bundle.py"), "--help"])
        rec("audit_bundle", r.returncode == 0)

    say("--- 4/5 大模型能力（默认关闭，按需开启；各链 init 见下）---")
    # matting 抠像链：细粒度探测（2026-09-11，此前只探 env 目录）
    _m = ROOT / "apps" / "matting"
    comfy_py = _conda_env_py("comfyui", "COMFYUI_PY")   # 钩子对齐 apps/matting/init.sh
    rec("comfyui_env", comfy_py.is_file(), "apps/matting/ 抠像推理 env；细探测 bash apps/matting/init.sh")
    rec("matanyone2", (_m / "MatAnyone2/pretrained_models/hf2/model.safetensors").is_file(),
        "缺失跑：bash apps/matting/init.sh --download（github 克隆+HF 权重 141MB）")
    rec("birefnet_hr", (_m / "BiRefNet_HR/config.json").is_file(),
        "缺失跑：bash apps/matting/init.sh --download（HF ZhengPeng7/BiRefNet_HR-matting）")
    resnet = Path("~/.cache/torch/hub/checkpoints/resnet18-5c106cde.pth").expanduser()
    rec("matting_resnet18", resnet.is_file() or (_m / "resnet18-5c106cde.pth").is_file(),
        "MatAnyone2 光流隐藏依赖（被墙假死坑），--download 会预置")
    try:
        urllib.request.urlopen("http://127.0.0.1:8188", timeout=2)
        rec("comfyui_service", True, "ComfyUI 8188 在线（背景图生成）")
    except Exception:
        rec("comfyui_service", False, "未运行（可选）：bash apps/matting/comfyui_init.sh --start")
    rw = Path.home() / "models/resemble-enhance"
    rec("resemble_weights", rw.is_dir(), "denoise_voice.py 需要（下载：bash plugins/denoise/denoise_init.sh --download）" if rw.is_dir() else "缺 ~/models/resemble-enhance（denoise 可选；bash plugins/denoise/denoise_init.sh --download）")

    say("--- 4b/5 三大生成模块 asr/tts/imgen（默认关闭，按需开启）---")
    # config 统一加载（§5.3：VTX_CONFIG > config.local.json；坏 JSON 警告 + {}；
    # 路径型键已 ~ 展开）。asr/imgen 探测为 config_key 型（v2 §5.1 裁定：废除
    # spec_from_file_location 复用，P2 各 app 落实探针子命令后改走 health.command）。
    lc = _pcfg.load_config()
    # asr：读 config 判断（模型根/对齐器/解释器）+ sidecar HTTP 探测
    am = _pcfg.cfg_get(lc, "asr.model_dir", None)
    am_dir = Path(am) if am else None
    rec("asr_model", bool(am_dir and am_dir.is_dir()),
        str(am_dir) if am_dir and am_dir.is_dir() else "未配置 asr.model_dir（config_key 型探测；bash apps/asr/init.sh）")
    al = _pcfg.cfg_get(lc, "asr.aligner_dir", None)
    al_dir = Path(al) if al else None
    rec("asr_aligner", bool(al_dir and al_dir.is_dir()),
        str(al_dir) if al_dir and al_dir.is_dir() else "未配置 asr.aligner_dir（字级时间戳需要；bash apps/asr/init.sh）")
    ap = _pcfg.cfg_get(lc, "asr.python", None)
    ap_path = Path(ap) if ap else None
    rec("asr_python", bool(ap_path and ap_path.is_file()),
        str(ap_path) if ap_path and ap_path.is_file() else "未配置 asr.python（qwen-asr 解释器；bash apps/asr/init.sh）")
    asr_base = _pcfg.cfg_get(lc, "asr.base_url", "http://127.0.0.1:18085")
    try:
        req = urllib.request.Request(str(asr_base).rstrip("/") + "/health", method="GET")
        with urllib.request.urlopen(req, timeout=0.5) as hr:
            h = json.loads(hr.read().decode("utf-8"))
        rec("asr_sidecar", True, f"ok model={h.get('model', '?')}")
    except Exception:
        rec("asr_sidecar", False, f"未运行（{asr_base}；python3 apps/asr/asr_cli.py sidecar start）")
    # tts：IndexTTS-2.5 repo（checkpoints 五件齐）+ 解释器 + 音色参考 + edge 兜底
    tts_repo = Path(_pcfg.cfg_get(lc, "tts.repo", "~/AI/IndexTTS/repo")).expanduser()
    five = ("checkpoints/config.yaml", "checkpoints/gpt.pth", "checkpoints/qwen0.6bemo4-merge",
            "checkpoints/codec.pth", "checkpoints/s2mel.pth")
    miss = [n for n in five if not (tts_repo / n).exists()]
    rec("tts_repo", tts_repo.is_dir() and not miss,
        (str(tts_repo) + "（checkpoints 五件齐）") if not miss
        else f"缺 {'、'.join(miss)}（{tts_repo}）；部署路线见 apps/tts/README.md，或用 --engine edge 兜底")
    tts_py = next((p for p in (_pcfg.cfg_get(lc, "tts.python", str(_conda_env_py("indextts", "TTS_PY"))),
                               str(tts_repo / (".venv/Scripts/python.exe" if OS == "win32" else ".venv/bin/python")))
                   if p and Path(p).expanduser().is_file()), None)
    rec("tts_python", bool(tts_py), str(Path(tts_py).expanduser()) if tts_py
        else "缺 indextts 解释器（conda create -n indextts python=3.10，见 apps/tts/README.md）")
    vr = Path(_pcfg.cfg_get(lc, "tts.voice_ref", "~/音乐/5_G_Resemble仅降噪.wav")).expanduser()
    rec("tts_voice_ref", vr.is_file(), str(vr) if vr.is_file() else "缺 5-10s 干声音色参考（apps/tts/README.md）")
    eb = Path(_pcfg.cfg_get(lc, "tts.edge_bin", "~/miniconda3/" +
                            ("Scripts/edge-tts.exe" if OS == "win32" else "bin/edge-tts"))).expanduser()
    rec("tts_edge", eb.is_file(), str(eb) if eb.is_file() else "缺 edge-tts（pip install edge-tts；免 GPU 兜底引擎）")
    # imgen：key 链 config_key 型（env SC_API_KEY/NANOBANANA2_API_KEY > ~/.config/vtx-imgen/
    # secrets.json 的 api_key——与 imgen/secrets.py 同一实践链的只读复刻，不跨目录 import）
    sc_key = (os.environ.get("SC_API_KEY") or os.environ.get("NANOBANANA2_API_KEY") or "").strip() or None
    if not sc_key:
        try:
            sc_key = (json.loads(Path("~/.config/vtx-imgen/secrets.json").expanduser()
                                 .read_text(encoding="utf-8")).get("api_key")) or None
        except Exception:
            sc_key = None
    rec("imgen_key", bool(sc_key), (f"尾4位 ****{sc_key[-4:]}" if sc_key
        else "未配置（SC_API_KEY / NANOBANANA2_API_KEY / keyring；解析链见 python3 apps/imgen/imgen_cli.py key status）"))
    # requests 探测：importlib.util.find_spec（doctor requires 同款；零子进程）
    rec("imgen_requests", importlib.util.find_spec("requests") is not None,
        "requests 可导入" if importlib.util.find_spec("requests") is not None
        else "缺 requests（pip install requests；仅 imgen 需要）")

    say("--- 5/5 本地配置 ---")
    cfg = ROOT / "config.local.json"
    example = ROOT / "config.example.json"
    if cfg.exists():
        say("  config.local.json 已存在，保持不动")
    elif example.is_file():
        cfg.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        say("  已生成 config.local.json（comfyui / asr / tts / imgen 全部 enabled:false）")
    else:
        # pip 安装布局只含内核（§5.2：apps/plugins/config.example 不进发行包——
        # P6 修复 C-P3-2：v1 此处裸 FileNotFoundError 崩 init）
        say("  config.example.json 不在（pip 安装布局）——跳过生成，"
            "clone 仓获取完整 config 模板")
    res["config_local"] = {"ok": True, "detail": str(cfg) if cfg.exists() else "未生成（pip 布局）"}

    # --fix：只装基础缺失项
    if a.fix and fail:
        pm = pm_available()
        print(f"--- --fix：尝试自动安装（包管理器={pm or '无'}）---")
        table = FIX.get(OS, {}).get(pm or "", {}) if pm else {}
        for key in fail:
            cmd = table.get(key)
            print(f"  [{key}] {try_fix(key, cmd)}")
        print("  安装后请重开终端再重跑 init 验证（winget 装的 PATH 不刷新进当前进程）。")
    elif fail:
        pm = pm_available()
        print(f"--- 缺失基础依赖安装指引（检测到包管理器：{pm or '未识别，请手动安装'}；或重跑加 --fix 自动安装）---")
        for key in fail:
            hint = HINTS.get(key, {})
            print(f"  [{key}] {hint.get(OS, hint) if isinstance(hint, dict) else hint}")

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    ok = not fail
    say(f"--- 结果：{'✅ 基础依赖齐备，vtx 渲染链可用' if ok else '❌ 缺基础依赖：' + ', '.join(fail)} ---")
    return 0 if ok else 1


# ================================================================ doctor
def _doctor_version(json_out: bool) -> dict:
    """版本段：HEAD vs origin/HEAD 漂移（fetch 5s 超时 / VTX_DOCTOR_OFFLINE=1 降级 WARN）。"""
    out = {"version": _read_version(), "notes": []}
    if not (ROOT / ".git").exists():
        out["notes"].append("非 git 仓（无版本漂移检测）")
        return out
    if os.environ.get("VTX_DOCTOR_OFFLINE") == "1":
        out["notes"].append("VTX_DOCTOR_OFFLINE=1：跳过 fetch（离线模式，漂移以本地引用为准）")
    else:
        try:
            subprocess.run(["git", "-C", str(ROOT), "fetch", "--quiet"],
                           capture_output=True, timeout=5)
        except subprocess.TimeoutExpired:
            out["notes"].append("git fetch 超时（>5s）：漂移以本地引用为准（可能滞后）")
        except Exception as e:
            out["notes"].append(f"git fetch 失败（{e}）：漂移以本地引用为准")
    r = sh(["git", "-C", str(ROOT), "rev-parse", "--verify", "HEAD"])
    head = r.stdout.strip() if r.returncode == 0 else None
    out["head"] = head or "?"
    r = sh(["git", "-C", str(ROOT), "status", "--porcelain"])
    out["state"] = "dirty" if (r.returncode == 0 and r.stdout.strip()) else \
                   ("clean" if r.returncode == 0 else "?")
    # 远端跟踪引用：origin/HEAD 优先，缺则 @{u}
    upstream = None
    for ref in ("origin/HEAD", "@{u}"):
        rr = sh(["git", "-C", str(ROOT), "rev-parse", "-q", "--verify", ref])
        if rr.returncode == 0 and rr.stdout.strip():
            upstream = ref
            break
    if upstream is None:
        out["notes"].append("无远端跟踪引用：ahead/behind 未知（本地仓/未 fetch）")
        out["ahead"], out["behind"] = None, None
    else:
        rr = sh(["git", "-C", str(ROOT), "rev-list", "--left-right", "--count",
                 f"HEAD...{upstream}"])
        if rr.returncode == 0:
            parts = rr.stdout.split()
            out["ahead"], out["behind"] = int(parts[0]), int(parts[1])
        else:
            out["ahead"], out["behind"] = None, None
            out["notes"].append(f"rev-list 对 {upstream} 失败：漂移未知")
    # VERSION 仅发布标识，无 tag 不告警（§5.1）
    if not json_out:
        drift = ""
        if out.get("ahead") is not None:
            drift = f"，ahead {out['ahead']} / behind {out['behind']}"
        print(f"  版本 {out['version']}｜HEAD {out['head'][:12] if out['head'] != '?' else '?'}"
              f"｜{out['state']}{drift}")
        for n in out["notes"]:
            print(f"  ⚠ {n}")
    return out


def _probe_requires(manifest: dict) -> tuple:
    """requires 依赖探测：binaries → shutil.which；python_packages → find_spec；
    platforms → sys.platform；core → VERSION 约束对照。返回 (ok, findings)。"""
    req = (manifest or {}).get("requires", {}) or {}
    findings, missing = [], []
    for b in req.get("binaries", []) or []:
        found = shutil.which(b)
        findings.append(f"binary {b}: {'✓ ' + found if found else '✗ 缺失'}")
        if not found:
            missing.append(f"binary:{b}")
    for p in req.get("python_packages", []) or []:
        spec = importlib.util.find_spec(p)
        findings.append(f"py {p}: {'✓' if spec else '✗ 缺失'}")
        if spec is None:
            missing.append(f"py:{p}")
    plats = req.get("platforms", []) or []
    if plats:
        ok_p = sys.platform in plats
        findings.append(f"platform {sys.platform}: {'✓' if ok_p else '✗ 不在支撑列表 ' + ','.join(plats)}")
        if not ok_p:
            missing.append("platform")
    core = req.get("core")
    if isinstance(core, str) and core.strip():
        findings.append(f"core {core}: 当前 {_read_version()}（约束对照）")
    return (not missing), findings


def _probe_health(manifest: dict, comp_dir: Path) -> tuple:
    """manifest health 探测（§5.1）：command 型 = py3 + 插件根内相对脚本子进程
    （超时 10s；路径安全已在 manifest 校验把关）；config_key 型 = 读 config 判断
    键存在。返回 (ok|None, detail)——None = 无法探测（不 FAIL）。"""
    health = (manifest or {}).get("health", {}) or {}
    htype = health.get("type")
    if htype == "command":
        interp = py3()
        if interp is None:
            return None, "找不到 python3 解释器，health.command 未执行"
        import shlex
        argv = shlex.split(health.get("command", ""))
        if not argv:
            return None, "health.command 为空"
        try:
            r = subprocess.run([interp, str(comp_dir / argv[0]), *argv[1:]],
                               capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            return False, f"health 探测超时（>10s）：{health.get('command')}"
        except Exception as e:
            return False, f"health 探测失败：{e}"
        detail = ((r.stdout or "").strip().splitlines() or [""])[0][:80]
        return (r.returncode == 0), (f"exit {r.returncode}" + (f"｜{detail}" if detail else ""))
    if htype == "config_key":
        cfg = _pcfg.load_config()
        node = cfg
        for part in str(health.get("key", "")).split("."):
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                break
        # 判定口径 = 键已配置（值非空即可，false 也算已配置——如 bgm 路由声明的开关标记）。
        # P4 审查 P2-5：键缺失 = 提示级（None）不 FAIL——config_key 型组件（bgm 路由
        # 声明类）的键即启用开关，未配置=未启用，按「enabled 才 FAIL 门」原则放行
        # （纯净 clone 无 config.local.json 时 doctor 不得因此 exit 1）。
        present = node is not None and node != ""
        if not present:
            return None, f"config {health.get('key')}: 未配置（可选能力，不阻断）"
        return present, f"config {health.get('key')}: 已配置"
    return None, f"health.type={htype!r} 未知，跳过探测"


def _doctor_components(json_out: bool, kind_filter=None) -> dict:
    """组件段：registry 全量 apps/plugins manifest 有效 + health + requires +
    `import vtx` 实际解析路径自检（双模块裂分常驻哨兵）。

    FAIL 门语义（§5.1）：**enabled 才 FAIL**——发现件（builtin/external enabled）
    的 manifest 错误/health 失败/requires 缺失计 FAIL；未装/disabled 仅提示；
    registry 声明未落地件列「未安装（规划中）」。kind_filter="app"|"plugin"
    供 `apps check` / `plugins check` 单层复用。
    """
    out = {"entries": [], "declared": [], "import_vtx": {}, "fail": False}
    for e in _preg.discover(ROOT):
        if kind_filter and e["kind"] != kind_filter:
            continue
        item = {"name": e["name"], "kind": e["kind"], "source": e["source"],
                "dir": str(e["dir"]) if e.get("dir") else None,
                "enabled": e["enabled"], "manifest_ok": not e["manifest_errors"],
                "manifest_errors": e["manifest_errors"]}
        entry_fail = False
        if e["enabled"]:
            if e["manifest_errors"]:
                entry_fail = True
            else:
                h_ok, h_detail = _probe_health(e["manifest"], e["dir"])
                item["health"] = {"ok": h_ok, "detail": h_detail}
                r_ok, findings = _probe_requires(e["manifest"])
                item["requires"] = {"ok": r_ok, "findings": findings}
                if h_ok is False or not r_ok:
                    entry_fail = True
        else:
            item["note"] = "disabled（external 注册默认禁用，§2.4）"
        item["fail"] = entry_fail
        out["fail"] = out["fail"] or entry_fail
        out["entries"].append(item)
    # 静态注册表声明（未落地件）：列键声明与 installed 状态——本轮全为规划中，不 FAIL
    for name, ent in sorted(_preg.declared_plugins(ROOT).items()):
        if kind_filter and kind_filter != "plugin":
            continue
        state = _preg.plugin_state(name, ROOT)
        out["declared"].append({
            "name": name, "version": ent.get("version", "?"),
            "keys": ent.get("keys", []), "hooks": ent.get("hooks", []),
            "installed": ent.get("installed", False),
            "state": state,
            "note": "未安装（规划中）——P2b 落地 plugins/<name>/ 并置 registry installed:true" if state == "not_installed" else state})
    # import vtx 自检：实际解析路径须落在本仓（双模块裂分哨兵）
    try:
        import vtx
        resolved = str(Path(vtx.__file__).resolve())
        ok = resolved.startswith(str(ROOT))
        out["import_vtx"] = {"ok": ok, "path": resolved}
        out["fail"] = out["fail"] or not ok
    except Exception as e:
        out["import_vtx"] = {"ok": False, "error": str(e)}
        out["fail"] = True
    if not json_out:
        for it in out["entries"]:
            flag = "✗" if it["fail"] else ("✓" if it["enabled"] else "ℹ")
            line = f"  {flag} [{it['kind']}:{it['source']}] {it['name']}"
            if it.get("manifest_errors"):
                line += "｜manifest 无效：" + "；".join(it["manifest_errors"][:3])
            if it["enabled"] and it.get("manifest_ok"):
                if it.get("health"):
                    # 三分支显示（P6 修复 C-P3-1）：None = 提示级（无法探测/未配置）
                    # 不再与 FAIL 同显 ✗——JSON 侧 ok:null 本就三态，文本对齐
                    h = it["health"]["ok"]
                    h_flag = "✓" if h else ("ℹ" if h is None else "✗")
                    line += f"｜health {h_flag}（{it['health']['detail']}）"
                if it.get("requires"):
                    line += f"｜requires {'✓' if it['requires']['ok'] else '✗'}"
            if it.get("note"):
                line += f"｜{it['note']}"
            print(line)
            for f in (it.get("requires") or {}).get("findings", []):
                if f.startswith(("binary", "py", "platform")) and "✗" in f:
                    print(f"      {f}")
        for d in out["declared"]:
            keys = "、".join(f"{k.get('path')}.{k.get('key')}" for k in d["keys"] if isinstance(k, dict))
            print(f"  ℹ [registry] {d['name']}@{d['version']}（键 {keys}）——{d['note']}")
        iv = out["import_vtx"]
        print(f"  {'✓' if iv.get('ok') else '✗'} import vtx → {iv.get('path') or iv.get('error')}"
              + ("" if iv.get("ok") else "（解析路径不在本仓：双模块裂分风险）"))
    return out


def _doctor_skills(json_out: bool) -> dict:
    """skills 段：skills/ 工作流包完好性（skill.json + SKILL.md 齐备 + uses 声明逐件在册）。

    在册校验（P3，tech-talk 落地）：uses_apps 逐件对照 apps 发现清单；uses_plugins/
    optional_plugins 逐件对照 plugins/registry.json 静态注册表——声明了未落地/未注册
    的件计 FAIL（uses 声明是包与组件之间的契约面，漂移即失效）。
    """
    out = {"installed": [], "fail": False}
    base = ROOT / "skills"
    if not base.is_dir():
        out["note"] = "无已安装工作流包（skills/ 未创建）"
    else:
        known_apps = {e["name"] for e in _preg.discover(ROOT) if e["kind"] == "app"}
        known_plugins = set(_preg.load_static_registry(ROOT)["plugins"])

        def _names(decl) -> list:
            if not isinstance(decl, list):
                return []
            return [u["name"] if isinstance(u, dict) else str(u) for u in decl]

        for d in sorted(base.iterdir()):
            if not d.is_dir() or d.name.startswith((".", "_")):
                continue
            item = {"name": d.name, "ok": True}
            try:
                meta = json.loads((d / "skill.json").read_text(encoding="utf-8"))
                item["version"] = meta.get("version", "?")
                item["uses_apps"] = _names(meta.get("uses_apps", []))
                item["uses_plugins"] = _names(meta.get("uses_plugins", []))
                item["optional_plugins"] = _names(meta.get("optional_plugins", []))
            except (OSError, ValueError) as e:
                item["ok"] = False
                item["error"] = f"skill.json 缺失/损坏: {e}"
            if not (d / "SKILL.md").is_file():
                item["ok"] = False
                item["error"] = (item.get("error", "") + "；SKILL.md 缺失").lstrip("；")
            if item["ok"]:
                errs = []
                miss_a = [n for n in item["uses_apps"] if n not in known_apps]
                if miss_a:
                    errs.append(f"uses_apps 不在册：{', '.join(miss_a)}")
                for field in ("uses_plugins", "optional_plugins"):
                    miss_p = [n for n in item[field] if n not in known_plugins]
                    if miss_p:
                        errs.append(f"{field} 未在 registry.json 注册：{', '.join(miss_p)}")
                overlap = set(item["uses_plugins"]) & set(item["optional_plugins"])
                if overlap:
                    errs.append(f"uses_plugins 与 optional_plugins 重复声明：{', '.join(sorted(overlap))}")
                if errs:
                    item["ok"] = False
                    item["error"] = "；".join(errs)
            out["installed"].append(item)
            out["fail"] = out["fail"] or not item["ok"]
    if not json_out:
        if "note" in out:
            print(f"  ℹ {out['note']}")
        for it in out["installed"]:
            print(f"  {'✓' if it['ok'] else '✗'} skills/{it['name']}@{it.get('version', '?')}"
                  + (f"（{it['error']}）" if it.get("error") else ""))
            if it.get("ok"):
                print(f"      uses_apps {len(it['uses_apps'])} 件逐件在册"
                      + (f"；plugins uses {len(it['uses_plugins'])} / optional {len(it['optional_plugins'])}"
                         if it.get("uses_plugins") or it.get("optional_plugins") else ""))
    return out


# ================================================================ doctor --fix（§5.1，P4 落地）
def _mount_link_state() -> list:
    """挂载链接体检（与 cmd_fix_links 同表 MOUNT_LINKS）：
    → [(link_path, target_path, problem, want)]，problem ∈ missing|wrong_target|
    absolute_form|occupied；want = 应有的相对链接目标串。空列表 = 全部健康。

    目标件本仓未落地的链接项不体检（cmd_fix_links 同口径：落地后再挂）。
    """
    mroot = mount_root()
    out = []
    for link_name, rel_target in MOUNT_LINKS:
        target = (ROOT / rel_target).resolve()
        link = mroot / link_name
        if not target.exists():
            continue
        want = os.path.relpath(target, mroot)
        if link.is_symlink():
            current = Path(os.readlink(link))
            resolved = (link.parent / current).resolve() if not current.is_absolute() else current.resolve()
            if resolved == target:
                if current.is_absolute():
                    out.append((link, target, "absolute_form", want))
                continue
            out.append((link, target, "wrong_target", want))
        elif link.exists():
            out.append((link, target, "occupied", want))       # 真实文件占用：永不自动改
        else:
            out.append((link, target, "missing", want))
    return out


def _mount_fix_safe(items: list) -> tuple:
    """挂载链接自动修复的安全门 → (safe, reason)。

    VTX_MOUNT_ROOT 覆盖 → 安全（测试/多仓并存的隔离环境）；默认 live 挂载根下
    任一现存链接解析到**本仓之外**的路径（含悬空——目标虽缺失但明确不属本仓）
    → 不安全（挂载属其他仓——本机 live voice-ime/主工作区场景，铁律：不自动
    改，列人工项）；其余（缺失/仓内错指/绝对形）→ 安全。
    判属用路径包含（is_relative_to）而非字符串前缀（P4 审查 P1-2：前缀判定
    会被兄弟检出 video_tools-v2-* 误放行）；悬空链接按属主判定（resolve 后
    is_relative_to 对不存在的目标同样成立）。"""
    if os.environ.get("VT_MOUNT_ROOT"):
        return True, "VT_MOUNT_ROOT 隔离环境"
    root = ROOT.resolve()
    for link, _target, problem, _want in items:
        if problem != "wrong_target":
            continue
        current = Path(os.readlink(link))
        resolved = (link.parent / current).resolve() if not current.is_absolute() else current.resolve()
        if not resolved.is_relative_to(root):
            return False, f"{link.name} 指向本仓之外（{resolved}）——挂载根属其他仓"
    return True, ""


def _apply_mount_link_fixes(items: list) -> list:
    """执行挂载链接修复（missing/wrong_target/absolute_form → 重建相对链接）；
    occupied 跳过。返回实际修复的链接名列表。"""
    mroot = mount_root()
    mroot.mkdir(parents=True, exist_ok=True)
    applied = []
    for link, _target, problem, want in items:
        if problem == "occupied":
            continue
        if link.is_symlink():
            link.unlink()
        os.symlink(want, link)
        applied.append(f"{link.name} → {want}")
    return applied


def _registry_fix_plan(repo_root=None) -> tuple:
    """registry.json 缺字段修复计划（§5.1 doctor --fix 能修项②）→ (fixes, manual)。

    plugins/<name>/ 实体在盘且 manifest 有效、registry 条目缺 version/keys/hooks/
    installed（manifest 是 registry.json 的声明数据源，§2.3）→ 回填清单
    [(name, field, value)]；installed 显式 false / 实体无条目 → 人工项（可能
    有意，不自动改）。fixes 为空且 manual 为空 = 注册表无缺字段。"""
    root = Path(repo_root) if repo_root else ROOT
    fixes, manual = [], []
    base = root / "plugins"
    if not base.is_dir():
        return fixes, manual
    reg = _preg.load_static_registry(root)["plugins"]
    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name.startswith((".", "_")):
            continue
        manifest, errors = _load_manifest(d)
        if errors or not manifest or manifest.get("kind") != "plugin":
            continue   # manifest 无效是组件段 FAIL 项，不属 --fix 语义
        entry = reg.get(d.name)
        ext = manifest.get("extends", {}) or {}
        if entry is None:
            manual.append(f"plugins/{d.name}/ 实体存在但 registry.json 未声明——"
                          "是否正式插件请人工补录条目")
            continue
        if "version" not in entry:
            fixes.append((d.name, "version", manifest.get("version", "?")))
        if not entry.get("keys"):
            sk = ext.get("schema_keys") or []
            if sk:
                fixes.append((d.name, "keys", sk))
        if not entry.get("hooks"):
            hk = ext.get("hooks") or []
            if hk:
                fixes.append((d.name, "hooks", hk))
        if "installed" not in entry:
            fixes.append((d.name, "installed", True))
        elif entry.get("installed") is False:
            manual.append(f"plugins/{d.name}: registry 显式 installed:false"
                          "（有意卸载？）——不自动改")
    return fixes, manual


def _apply_registry_fixes(fixes: list, repo_root=None) -> None:
    """按计划回填 registry.json（保留其余字段原样；原子性靠小文件整体重写）。"""
    root = Path(repo_root) if repo_root else ROOT
    f = root / "plugins" / "registry.json"
    data = json.loads(f.read_text(encoding="utf-8"))
    for name, field, value in fixes:
        data.setdefault("plugins", {}).setdefault(name, {})[field] = value
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")


def _doctor_fix(json_out: bool) -> dict:
    """doctor --fix 修复通道：挂载链接（联动 --fix-links 同表）+ registry.json
    缺字段。返回报告 dict；无可自动修复项时 applied 空 + 说明齐全。"""
    report = {"mount": {}, "registry": {}, "manual": []}
    items = _mount_link_state()
    # occupied 项任何分支都列人工（P6 修复 C-P2-3：v1 在 safe 分支跳过修复也不进
    # manual，全部为 occupied 时报 status="fixed", applied=[] ——问题在文本与 JSON
    # 双侧静默消失）
    occupied_notes = [f"挂载链接 {lk.name} 被真实文件占用（非符号链接）——永不自动改，"
                      "人工确认后处理（--fix-links 同口径跳过）"
                      for lk, _t, prob, _w in items if prob == "occupied"]
    if not items:
        report["mount"] = {"status": "ok", "note": "挂载链接全部健康"}
    else:
        safe, reason = _mount_fix_safe(items)
        if safe:
            applied = _apply_mount_link_fixes(items)
            report["mount"] = {"status": "fixed", "applied": applied}
        else:
            report["mount"] = {"status": "manual", "note": reason +
                               "——确认目标仓后跑 vt.py --fix-links"}
            report["manual"].append(f"挂载链接：{reason}")
    fixes, manual = _registry_fix_plan()
    report["registry"] = {"status": "fixed" if fixes else "ok",
                          "fixes": [f"{n}.{f} = {v!r}" for n, f, v in fixes]}
    if fixes:
        _apply_registry_fixes(fixes)
    report["manual"].extend(f"registry：{m}" for m in manual)
    report["manual"].extend(occupied_notes)
    n_applied = len(report["mount"].get("applied", [])) + len(fixes)
    if not json_out:
        print("== --fix：可自动修复项 ==")
        m = report["mount"]
        if m.get("status") == "ok":
            print("  ✓ 挂载链接全部健康")
        elif m.get("status") == "fixed":
            for a in m["applied"]:
                print(f"  · 已重建挂载链接 {a}")
        else:
            print(f"  ℹ 挂载链接：{m['note']}")
        r = report["registry"]
        if r["status"] == "ok":
            print("  ✓ registry.json 无缺字段")
        else:
            for line in r["fixes"]:
                print(f"  · 已回填 {line}")
        if report["manual"]:
            for line in report["manual"]:
                print(f"  ℹ 人工项（不自动改）：{line}")
        if n_applied == 0:
            print("  doctor --fix：无可自动修复项（链接/注册表均已健康或需人工处理）")
    return report


def cmd_doctor(a) -> int:
    if not a.json:
        print("== ① 版本 ==")
    version = _doctor_version(a.json)
    if not a.json:
        print("== ② 组件（apps + plugins）==")
    components = _doctor_components(a.json)
    if not a.json:
        print("== ③ skills（工作流包）==")
    skills = _doctor_skills(a.json)
    fix = _doctor_fix(a.json) if a.fix else None
    if a.json:
        out = {"version": version, "components": components, "skills": skills}
        if fix is not None:
            out["fix"] = fix
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1 if (components["fail"] or skills["fail"]) else 0
    fails = []
    if components["fail"]:
        fails.append("组件段存在 FAIL（enabled 件 manifest/health/requires 不通过）")
    if skills["fail"]:
        fails.append("skills 段存在 FAIL")
    print("doctor 结论：" + ("✅ 通过" if not fails else "❌ " + "；".join(fails)))
    return 1 if fails else 0


# ================================================================ list / check / version
def cmd_list(kind: str) -> int:
    if kind == "skills":
        sk = _doctor_skills(json_out=False)
        return 1 if sk["fail"] else 0
    comp = _doctor_components(json_out=True, kind_filter=("app" if kind == "apps" else "plugin"))
    if kind == "apps" and not comp["entries"]:
        print("  ℹ 无已安装 app（apps/ 未创建——P2 迁移落地，12 件清单见 ARCHITECTURE §3）")
    for it in comp["entries"]:
        # 红队 P2-2 修正：fail 优先；enabled 以插件实际状态为准（registry enabled:false
        # 的发现件此前误显 "✓ enabled"——it["enabled"] 只反映发现来源不是启停）
        st = _preg.plugin_state(it["name"]) if it["kind"] == "plugin" else ("active" if it["enabled"] else "disabled")
        state = "✗ FAIL" if it["fail"] else {"active": "✓ enabled", "disabled": "ℹ disabled"}.get(st, "ℹ 未安装")
        print(f"  {state:9s} {it['name']}（{it['kind']}:{it['source']}，{it['dir']}）")
    if kind == "plugins":
        for d in comp["declared"]:
            keys = "、".join(f"{k.get('path')}.{k.get('key')}" for k in d["keys"] if isinstance(k, dict))
            # P2b 起插件可落地：按实际安装态打标（未装=未安装；已装在上方发现
            # 段已按 enabled/FAIL 展示，此行是 registry 声明对账）
            tag = {"active": "✓ 已装", "disabled": "ℹ 已装禁用"}.get(d["state"], "ℹ 未安装")
            print(f"  {tag:9s} {d['name']}@{d['version']}（registry 声明，键 {keys}）——{d['note']}")
    return 1 if comp["fail"] else 0


def cmd_check(kind: str) -> int:
    """check = doctor 组件段单层（apps → app；plugins → plugin；skills → skills 段）。"""
    if kind == "skills":
        sk = _doctor_skills(json_out=False)
        print("skills check：" + ("✅" if not sk["fail"] else "❌"))
        return 1 if sk["fail"] else 0
    comp = _doctor_components(json_out=False, kind_filter=("app" if kind == "apps" else "plugin"))
    print(f"{kind} check：" + ("✅" if not comp["fail"] else "❌"))
    return 1 if comp["fail"] else 0


def cmd_version() -> int:
    head = "?"
    if (ROOT / ".git").exists():
        r = sh(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"])
        if r.returncode == 0:
            head = r.stdout.strip()
    print(f"framivra {_read_version()}（HEAD {head}）")
    return 0


# ================================================================ --fix-links
# 挂载表：~/.agents/skills/video/ 下 5 条 skill 链接 + 第 6 条 skills 链接（v2
# 工作流包目录），目标 = 本仓对应目录（VT_MOUNT_ROOT 可覆盖挂载根——测试/多仓并存；
# 默认指向 live 挂载，真实修复前先 --dry 核对目标仓是否为本仓）。
MOUNT_LINKS = (
    ("SKILL.md", "skill/SKILL.md"),
    ("references", "skill/references"),
    ("assets", "skill/assets"),
    ("vtx", "vtx"),
    ("scripts", "scripts"),
    ("skills", "skills"),       # P2 落地；目标缺失时跳过并提示
)


def mount_root() -> Path:
    return Path(os.environ.get("VT_MOUNT_ROOT", "~/.agents/skills/video")).expanduser()


def cmd_fix_links(dry: bool) -> int:
    mroot = mount_root()
    print(f"挂载根：{mroot}（目标仓：{ROOT}）")
    if not dry:
        mroot.mkdir(parents=True, exist_ok=True)
    changed = 0
    for link_name, rel_target in MOUNT_LINKS:
        target = (ROOT / rel_target).resolve()
        link = mroot / link_name
        if not target.exists():
            print(f"  ℹ {link_name}：目标不存在（{rel_target}）——跳过（P2 落地后再挂）")
            continue
        want = os.path.relpath(target, mroot)   # 相对链接（可移植）
        if link.is_symlink():
            current = Path(os.readlink(link))
            resolved = (link.parent / current).resolve() if not current.is_absolute() else current.resolve()
            if resolved == target:
                mark = "绝对" if current.is_absolute() else "相对"
                print(f"  ✓ {link_name}：已指向本仓目标（{mark}链接）")
                if current.is_absolute() and not dry:
                    link.unlink(); os.symlink(want, link)
                    print(f"    · 已规范化为相对链接 → {want}")
                    changed += 1
                continue
            print(f"  ✗ {link_name}：指向 {resolved}（非本仓目标）")
        elif link.exists():
            print(f"  ✗ {link_name}：被真实文件/目录占用（{link}）——不覆盖，请人工处理")
            continue
        else:
            print(f"  ✗ {link_name}：缺失")
        if dry:
            print(f"    [dry] 将创建相对链接 {link_name} → {want}")
        else:
            if link.is_symlink():
                link.unlink()       # 指向错误目标的旧链接先摘除再重建
            os.symlink(want, link)
            print(f"    · 已创建相对链接 → {want}")
        changed += 1
    print(("✓ 修复完成" if not dry else "[dry] 以上为修复计划") + f"（{changed} 处变更）")
    return 0


# ================================================================ main
def main():
    ap = argparse.ArgumentParser(description="vt —— Framivra v2 统一入口（doctor/list/check/version/init/--fix-links）")
    ap.add_argument("command", nargs="?", default=None,
                    help="doctor | apps|plugins|skills list|check | version | init")
    ap.add_argument("sub", nargs="?", default=None, help="list | check（apps/plugins/skills 的子命令）")
    ap.add_argument("--json", action="store_true", help="doctor/init：机器可读输出")
    ap.add_argument("--fix", action="store_true",
                    help="init：安装缺失基础依赖；doctor：修复可自动修复项（挂载链接重建 + "
                         "registry.json 缺字段回填，§5.1）——两者语义不同，不得混用")
    ap.add_argument("--fix-links", action="store_true",
                    help="重建挂载符号链接（skill 5 链接 + skills 链接，指向本仓；--dry 只报告）")
    ap.add_argument("--dry", action="store_true", help="--fix-links 专属：只报告不修复")
    a = ap.parse_args()

    if a.fix_links:
        return cmd_fix_links(a.dry)
    if a.command == "version":
        return cmd_version()
    if a.command == "init":
        return cmd_init(a)
    if a.command == "doctor":
        return cmd_doctor(a)
    if a.command in ("apps", "plugins", "skills"):
        if a.sub == "list":
            return cmd_list(a.command)
        if a.sub == "check":
            return cmd_check(a.command)
        print(f"✗ 用法：vt.py {a.command} list|check", file=sys.stderr)
        return 2
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
