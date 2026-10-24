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
    if platform == "linux":
        # Native distro binaries, not their PATH shell launchers. Do not infer
        # targets by executing/parsing wrappers or scanning the host filesystem.
        return ("/opt/google/chrome/chrome",
                "/usr/lib/chromium/chromium",
                "/usr/lib/chromium-browser/chromium-browser",
                "/usr/lib64/chromium-browser/chromium-browser",
                "/usr/lib64/chromium/chromium",
                "/opt/microsoft/msedge/msedge")
    return ()


def _linux_native_executable(candidate):
    resolved = shutil.which(candidate) or (candidate if os.path.isabs(candidate) else None)
    if not resolved or not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
        return False
    try:
        with open(resolved, 'rb') as stream:
            return stream.read(4) == b'\x7fELF'
    except OSError:
        return False


def find_chrome_candidate(platform, *, allow_override_file=True):
    """Retain candidate spelling; Linux automatic discovery requires native ELF.

    Renderer callers historically accept an explicit file even without X_OK;
    doctor disables that file-only fallback to retain its executable policy.
    """
    names = chrome_names()
    # An available override remains the user's executable, including a custom
    # CLI wrapper. Never silently replace it with a distro browser; renderer
    # identity/launch failures remain the caller's responsibility.
    if names[0] and (shutil.which(names[0]) or
                     (allow_override_file and os.path.isfile(names[0]))):
        return names[0]
    if platform == "linux":
        for candidate in (*names[1:], *chrome_install_paths(platform)):
            if _linux_native_executable(candidate):
                return candidate
        return None
    for candidate in (*names[1:], *chrome_install_paths(platform)):
        if candidate and (shutil.which(candidate) or os.path.isfile(candidate)):
            return candidate
    return None
