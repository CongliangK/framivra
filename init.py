#!/usr/bin/env python3
"""video_tools 初始化脚本（跨平台：Linux / macOS / Windows）

用途：新环境（新机 / 新 clone）快速就绪——依赖自检 + 可选自动安装 + 生成本地配置。
幂等：重复运行无副作用；config.local.json 已存在时不覆盖。

用法：
  python3 init.py            # 自检 + 打印各 OS 安装指引
  python3 init.py --fix      # 自检 + 尝试自动安装缺失的「基础依赖」（winget/brew/apt/dnf/pacman）
  python3 init.py --json     # 机器可读结果（CI/脚本用）

依赖分级：
  基础（vtx 渲染链必需）：python3≥3.9、ffmpeg/ffprobe（建议带 libfdk_aac）、Chrome/Chromium/Edge
  可选：blender（预览桥）、sox（音频后处理）、NVIDIA GPU+NVENC（--gpu/--draft 硬编码，缺则回落 x264）
  大模型（默认关闭）：matting 需 conda comfyui env + 权重；denoise 需 resemble 权重；
  三大生成模块 asr/tts/imgen（4b 段探测）：ASR 需 qwen-asr 解释器+模型+sidecar 18085；TTS 需 IndexTTS repo/edge-tts；imgen 需速创 API key+requests
"""
import argparse, json, os, platform, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OS = sys.platform  # linux / darwin / win32

# Windows 控制台/管道默认 GBK 编码，✓✗ 中文输出会 UnicodeEncodeError 崩溃（Python
# ≤3.14 非 UTF-8 模式）——统一重配 UTF-8 + replace，跨平台输出稳定
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# ---------------------------------------------------------------- 依赖探测
def find_binary(names, extra_paths=()):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    for p in extra_paths:
        if Path(p).is_file():
            return p
    return None


def find_chrome():
    """与 vtx.py find_chrome 同源逻辑：VTX_CHROME 优先 → PATH → 标准安装路径。"""
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
                 rf"{p86}\Microsoft\Edge\Application\msedge.exe"]  # 系统自带 Edge 默认装 (x86) 目录
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


# ---------------------------------------------------------------- 安装指引 / --fix
FIX = {
    "linux": {
        "dnf":  {"ffmpeg": ["dnf", "install", "-y", "ffmpeg"], "google-chrome": None},
        "apt":  {"ffmpeg": ["apt", "install", "-y", "ffmpeg"], "google-chrome": None},
        "pacman": {"ffmpeg": ["pacman", "-S", "--noconfirm", "ffmpeg"], "google-chrome": None},
    },
    "darwin": {"brew": {"ffmpeg": ["brew", "install", "ffmpeg"], "google-chrome": None}},
    "win32": {"winget": {
        "ffmpeg": ["winget", "install", "--id", "Gyan.FFmpeg", "-e", "--accept-source-agreements", "--accept-package-agreements"],
        "google-chrome": ["winget", "install", "--id", "Google.Chrome", "-e", "--accept-source-agreements", "--accept-package-agreements"],
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
        return f"跳过（无自动安装命令，见指引）"
    if shutil.which(install_cmd[0]) is None:
        return f"跳过（无 {install_cmd[0]}）"
    r = sh(install_cmd)
    return "已执行安装命令" + ("" if r.returncode == 0 else f"（退出码 {r.returncode}，请手动检查）")


# ------------------------------------------- 三大生成模块探测（asr/tts/imgen，方案 §6.6）
def _load_local_cfg():
    """容错读 config.local.json（缺文件/坏 JSON → {}，探测走默认链，不阻断）。"""
    f = ROOT / "config.local.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _cfg_get(cfg, path, default):
    """逐级取键；缺键/None → default（方案 §7.2，与 asr/tts/imgen 三模块同款语义）。"""
    node = cfg
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return default if node is None else node


def _load_module(rel, name):
    """按文件路径加载同仓平铺模块（避免 'client'/'secrets' 等通用名污染 sys.modules）。"""
    import importlib.util
    f = ROOT / rel
    if not f.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location(name, f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _conda_env_py(env, hook):
    """conda env 解释器路径：环境变量钩子优先（与各 init.sh 同名钩子对齐，消除探测面
    漂移），其次按平台取 ~/miniconda3/envs/<env>/bin/python（POSIX）或
    <env>/python.exe（Windows conda 无 bin/ 层）。"""
    hooked = os.environ.get(hook)
    if hooked:
        return Path(hooked)
    return Path.home() / "miniconda3" / "envs" / env / ("python.exe" if OS == "win32" else "bin/python")


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description="video_tools 跨平台初始化")
    ap.add_argument("--fix", action="store_true", help="尝试自动安装缺失的基础依赖")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    a = ap.parse_args()

    res, fail = {}, []
    def rec(key, ok, detail=""):
        res[key] = {"ok": bool(ok), "detail": detail}
        (print(f"  {'✓' if ok else '✗'} {key}{(' — ' + detail) if detail else ''}") if not a.json else None)
        if not ok and key in ("python", "ffmpeg", "ffprobe", "chrome"):
            fail.append(key)

    say = (lambda s: None) if a.json else (lambda s: print(s))
    say(f"=== video_tools init ===  platform={platform.system()} ({OS})")

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
    r = sh([sys.executable, str(ROOT / "vtx/vtx.py"), "--help"])
    rec("vtx_cli", r.returncode == 0, "vtx/vtx.py（渲染 CLI）")
    rec("new_project", (ROOT / "scripts/new_project.sh").is_file(), "scripts/new_project.sh（bash 脚本；Windows 需 Git Bash/WSL）")
    for f in ("json2blend.py", "verify_blend.py", "blend2json.py", "subs_alpha.py"):
        try:
            compile((ROOT / "blender_bridge" / f).read_text(encoding="utf-8"), f, "exec")
            rec(f"bridge_{f}", True, "语法门；运行需 blender --python")
        except SyntaxError as e:
            rec(f"bridge_{f}", False, str(e))
    r = sh([sys.executable, str(ROOT / "blender_bridge/audit_bundle.py"), "--help"])
    rec("audit_bundle", r.returncode == 0)

    say("--- 4/5 大模型能力（默认关闭，按需开启；各链 init 见下）---")
    # matting 抠像链：细粒度探测（2026-09-11，此前只探 env 目录）
    _m = ROOT / "matting"
    comfy_py = _conda_env_py("comfyui", "COMFYUI_PY")   # 钩子对齐 matting/init.sh，Windows 布局自动切换
    rec("comfyui_env", comfy_py.is_file(), "matting/ 抠像推理 env；细探测 bash matting/init.sh")
    rec("matanyone2", (_m / "MatAnyone2/pretrained_models/hf2/model.safetensors").is_file(),
        "缺失跑：bash matting/init.sh --download（github 克隆+HF 权重 141MB）")
    rec("birefnet_hr", (_m / "BiRefNet_HR/config.json").is_file(),
        "缺失跑：bash matting/init.sh --download（HF ZhengPeng7/BiRefNet_HR-matting）")
    resnet = Path("~/.cache/torch/hub/checkpoints/resnet18-5c106cde.pth").expanduser()
    rec("matting_resnet18", resnet.is_file() or (_m / "resnet18-5c106cde.pth").is_file(),
        "MatAnyone2 光流隐藏依赖（被墙假死坑），--download 会预置")
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:8188", timeout=2)
        rec("comfyui_service", True, "ComfyUI 8188 在线（背景图生成）")
    except Exception:
        rec("comfyui_service", False, "未运行（可选）：bash matting/comfyui_init.sh --start")
    rw = Path.home() / "models/resemble-enhance"
    rec("resemble_weights", rw.is_dir(), "denoise_voice.py 需要（下载：bash scripts/denoise_init.sh --download）" if rw.is_dir() else "缺 ~/models/resemble-enhance（denoise 可选；bash scripts/denoise_init.sh --download）")

    say("--- 4b/5 三大生成模块 asr/tts/imgen（默认关闭，按需开启）---")
    lc = _load_local_cfg()
    # asr：复用 asr/client.py 探测链（§6.5 复用优先：config → IME 资产 → 本模块 venv/~/models）
    asr_client = _load_module("asr/client.py", "vt_init_asr_client")
    if asr_client is None:
        for k in ("asr_model", "asr_aligner", "asr_python", "asr_sidecar"):
            rec(k, False, "asr/ 模块缺失（探测跳过）")
    else:
        acfg = asr_client.load_config(str(ROOT))
        am = asr_client.probe_model(acfg, "1.7b") or asr_client.probe_model(acfg, "0.6b")
        rec("asr_model", bool(am), am or "缺 Qwen3-ASR，跑 bash asr/init.sh")
        al = asr_client.probe_aligner(acfg)
        rec("asr_aligner", bool(al), al or "缺 Qwen3-ForcedAligner（字级时间戳需要），跑 bash asr/init.sh")
        ap = asr_client.probe_python(acfg)
        rec("asr_python", bool(ap), ap or "缺 qwen-asr 解释器，跑 bash asr/init.sh")
        asr_base = _cfg_get(lc, "asr.base_url", "http://127.0.0.1:18085")
        h = asr_client.health(asr_base, timeout=0.5)
        rec("asr_sidecar", bool(h), (f"ok model={h.get('model', '?')}" if h
            else f"未运行（{asr_base}；python3 asr/asr_cli.py sidecar start）"))
    # tts：IndexTTS-2.5 repo（checkpoints 五件齐）+ 解释器 + 音色参考 + edge 兜底
    tts_repo = Path(_cfg_get(lc, "tts.repo", "~/AI/IndexTTS/repo")).expanduser()
    five = ("checkpoints/config.yaml", "checkpoints/gpt.pth", "checkpoints/qwen0.6bemo4-merge",
            "checkpoints/codec.pth", "checkpoints/s2mel.pth")
    miss = [n for n in five if not (tts_repo / n).exists()]
    rec("tts_repo", tts_repo.is_dir() and not miss,
        (str(tts_repo) + "（checkpoints 五件齐）") if not miss
        else f"缺 {'、'.join(miss)}（{tts_repo}）；部署路线见 tts/README.md，或用 --engine edge 兜底")
    tts_py = next((p for p in (_cfg_get(lc, "tts.python", str(_conda_env_py("indextts", "TTS_PY"))),
                               str(tts_repo / (".venv/Scripts/python.exe" if OS == "win32" else ".venv/bin/python")))
                   if p and Path(p).expanduser().is_file()), None)
    rec("tts_python", bool(tts_py), str(Path(tts_py).expanduser()) if tts_py
        else "缺 indextts 解释器（conda create -n indextts python=3.10，见 tts/README.md）")
    vr = Path(_cfg_get(lc, "tts.voice_ref", "~/音乐/5_G_Resemble仅降噪.wav")).expanduser()
    rec("tts_voice_ref", vr.is_file(), str(vr) if vr.is_file() else "缺 5-10s 干声音色参考（tts/README.md）")
    eb = Path(_cfg_get(lc, "tts.edge_bin", "~/miniconda3/" +
                       ("Scripts/edge-tts.exe" if OS == "win32" else "bin/edge-tts"))).expanduser()
    rec("tts_edge", eb.is_file(), str(eb) if eb.is_file() else "缺 edge-tts（pip install edge-tts；免 GPU 兜底引擎）")
    # imgen：key 链复用 imgen/secrets.py（不复制逻辑）；requests 为模块唯一第三方依赖
    imgen_secrets = _load_module("imgen/secrets.py", "vt_init_imgen_secrets")
    if imgen_secrets is None:
        rec("imgen_key", False, "imgen/ 模块缺失（探测跳过）")
    else:
        sc_key = imgen_secrets.get_env_api_key() or imgen_secrets.get_stored_api_key()
        rec("imgen_key", bool(sc_key), (f"尾4位 {imgen_secrets.mask_key(sc_key)}" if sc_key
            else "未配置（解析链见 python3 imgen/imgen_cli.py key status）"))
    # requests 探测用当前解释器：Windows 无 python3 别名，subprocess 会 FileNotFoundError
    # 直接崩溃（2026-09-11 体检修复；与 imgen 实际运行解释器同源）
    r = sh([sys.executable, "-c", "import requests"])
    v = sh([sys.executable, "-c", "import requests;print(requests.__version__)"]).stdout.split()[-1:] or [""]
    rec("imgen_requests", r.returncode == 0, v[0]
        if r.returncode == 0 else "缺 requests（pip install requests；仅 imgen 需要）")

    say("--- 5/5 本地配置 ---")
    cfg = ROOT / "config.local.json"
    if cfg.exists():
        say("  config.local.json 已存在，保持不动")
    else:
        cfg.write_text((ROOT / "config.example.json").read_text(), encoding="utf-8")
        say("  已生成 config.local.json（comfyui / asr / tts / imgen 全部 enabled:false）")
    res["config_local"] = {"ok": True, "detail": str(cfg)}

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
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
