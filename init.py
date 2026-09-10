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
  大模型（默认关闭）：matting 需 conda comfyui env + 权重；denoise 需 resemble 权重；ASR 需 sidecar
"""
import argparse, json, os, platform, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OS = sys.platform  # linux / darwin / win32


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
                 rf"{w}\Microsoft\Edge\Application\msedge.exe"]
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
        "win32": "winget install Gyan.FFmpeg（或 gyan.dev 全量包，含 libfdk_aac 需选 full+ 或自备 BtbN/tytan650 构建）",
    },
    "chrome": {
        "linux": "dnf install google-chrome-stable（Google 官方 repo）或 chromium",
        "darwin": "brew install --cask google-chrome",
        "win32": "winget install Google.Chrome（Edge 亦可：vtx 自动识别 msedge）",
    },
    "libfdk_aac": "缺 libfdk_aac 时 vtx 自动回落原生 aac 并告警——正式交付需复测编码后解码 TP（edit.md 坑表）。"
                  "Linux 装 ffmpeg 自 fdk 构建（RPM Fusion / deb-multimedia）；Windows 用 gyan.dev full+ 或 BtbN 构建包。",
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

    say("--- 4/5 大模型能力（默认关闭，按需开启）---")
    comfy = Path(os.path.expanduser("~/miniconda3/envs/comfyui"))
    rec("comfyui_env", comfy.is_dir(), "matting/ 抠像链需要（开启步骤见 matting/README.md）")
    rw = Path.home() / "models/resemble-enhance"
    rec("resemble_weights", rw.is_dir(), "denoise_voice.py 需要" if rw.is_dir() else "缺 ~/models/resemble-enhance（denoise 可选）")

    say("--- 5/5 本地配置 ---")
    cfg = ROOT / "config.local.json"
    if cfg.exists():
        say("  config.local.json 已存在，保持不动")
    else:
        cfg.write_text((ROOT / "config.example.json").read_text(), encoding="utf-8")
        say("  已生成 config.local.json（comfyui / asr / tts 全部 enabled:false）")
    res["config_local"] = {"ok": True, "detail": str(cfg)}

    # --fix：只装基础缺失项
    if a.fix and fail:
        pm = pm_available()
        print(f"--- --fix：尝试自动安装（包管理器={pm or '无'}）---")
        table = FIX.get(OS, {}).get(pm or "", {}) if pm else {}
        for key in fail:
            cmd = table.get(key)
            print(f"  [{key}] {try_fix(key, cmd)}")
        print("  安装后请重跑 init 验证。")
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
