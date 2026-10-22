"""Shared browser discovery; callers own failure policies."""
import os
import shutil


def chrome_names():
    # msedge 同为 Chromium 内核，headless 截图兼容。
    return (os.environ.get("VTX_CHROME"), "google-chrome", "google-chrome-stable",
            "chromium", "chromium-browser", "chrome", "msedge")


def chrome_install_paths(platform, *, include_user=True):
    if platform == "win32":
        w = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        paths = [rf"{w}\Google\Chrome\Application\chrome.exe",
                 rf"{pf86}\Google\Chrome\Application\chrome.exe"]
        if include_user:
            local = os.environ.get("LOCALAPPDATA", "")
            paths.append(rf"{local}\Google\Chrome\Application\chrome.exe")
        # 系统自带 Edge 默认装 (x86) 目录；仅安装 Edge 也保留该候选。
        paths.extend((rf"{w}\Microsoft\Edge\Application\msedge.exe",
                      rf"{pf86}\Microsoft\Edge\Application\msedge.exe"))
        return tuple(paths)
    if platform == "darwin":
        return ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
    return ()


def find_chrome_candidate(platform):
    """Return the first available candidate, retaining its name/path spelling."""
    for candidate in (*chrome_names(), *chrome_install_paths(platform)):
        if candidate and (shutil.which(candidate) or os.path.isfile(candidate)):
            return candidate
    return None
