"""vtx 编码器/渲染器选型纯决策函数：Chrome 定位、NVENC 能力探测、输出编码参数选择。

无副作用（除探测子进程本身）；全部行为与拆分前逐字一致。
"""
import os, shutil, sys
from concurrent.futures import ThreadPoolExecutor


def find_chrome() -> str:
    """跨平台定位 Chrome/Chromium：环境变量 VTX_CHROME 优先，其次 PATH 常见名，再退标准安装路径。"""
    cands = [os.environ.get("VTX_CHROME")] if os.environ.get("VTX_CHROME") else []
    cands += ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
              "chrome", "msedge"]  # msedge 同为 Chromium 内核，headless 截图兼容
    w = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    lapp = os.environ.get("LOCALAPPDATA", "")
    if sys.platform == "win32":
        cands += [rf"{w}\Google\Chrome\Application\chrome.exe",
                  rf"{pf86}\Google\Chrome\Application\chrome.exe",
                  rf"{lapp}\Google\Chrome\Application\chrome.exe",
                  rf"{w}\Microsoft\Edge\Application\msedge.exe",
                  rf"{pf86}\Microsoft\Edge\Application\msedge.exe"]  # 系统自带 Edge 默认装 (x86) 目录（只装 Edge 的出厂机 gfx 链不断）
    elif sys.platform == "darwin":
        cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    for c in cands:
        if c and (shutil.which(c) or os.path.isfile(c)):
            return c
    print("✗ 找不到 Chrome/Chromium（gfx 帧截图需要）。可设环境变量 VTX_CHROME 指定可执行文件路径"); sys.exit(1)


def nvenc_ok(c) -> bool:
    if os.environ.get("VTX_NO_NVENC"): return False
    if c.dry: return True
    # 探测尺寸须 ≥ NVENC 最小宽 145（128 会假失败）
    r = c.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x144:d=0.04",
               "-frames:v", "1", "-c:v", "h264_nvenc", "-f", "null", "-"], need=False)
    return r != "" and r.returncode == 0


def nvenc_concurrency(c, workers: int) -> int:
    """NVENC 并发会话探测：workers 路并发编码探测，任一失败回落串行（返回 1）。

    自 composite.render_segmented 提取（2026-09-10 模块化拆分），探测命令与
    打印信息与拆分前逐字一致。
    """
    def _p(_):
        r = c.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                   "-i", "color=c=black:s=256x144:d=0.04", "-frames:v", "1",
                   "-c:v", "h264_nvenc", "-f", "null", "-"], need=False)
        return r == "" or r.returncode == 0
    with ThreadPoolExecutor(workers) as ex:
        if not all(ex.map(_p, range(workers))):
            print("⚠ NVENC 并发会话探测失败，回落串行渲染")
            return 1
    return workers


def pick_vcodec(c, draft: bool):
    """输出编码参数选择（自 composite 提取，2026-09-10 阶段二；选项与旧版逐字一致）。"""
    if draft:
        if nvenc_ok(c):
            return (["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0",
                     "-pix_fmt", "yuv420p"], "NVENC p4")
        return (["-c:v", "libx264", "-crf", "26", "-preset", "veryfast", "-pix_fmt", "yuv420p"], "x264 veryfast")
    if getattr(c, "gpu", False):
        if nvenc_ok(c):
            # GPU 正式渲染：p7 最高质量档 + tune hq + vbr-cq17（对 1080p 交付与
            # 下游 4K 重编码透明；voice-ime 2026-09-08 引入）
            return (["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "17",
                     "-b:v", "0", "-pix_fmt", "yuv420p"], "NVENC p7")
        print("⚠ NVENC 不可用，回落 x264 medium（官方参数）")
        return (["-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p"], "x264 medium(回落)")
    return (["-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p"], "x264 medium")
