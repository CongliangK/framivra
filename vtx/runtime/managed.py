"""Environment of the explicit, editable managed installation only.

A normal clone/wheel never scans HOME or the workspace for configuration. An
installer-created venv may read its adjacent receipt, bound to this source root.
Tool argv remain owned by their consumers; the receipt supplies paths, not code.
"""
import json
import os
from pathlib import Path
import re
import stat
import sys


def tool_search_order():
    # Shared bins (e.g. Git/SoX or Windows shims) can contain reduced FFmpeg.
    if sys.platform in ('darwin', 'win32'):
        return ('ffmpeg', 'ffprobe', 'git', 'sox', 'chrome')
    return ('git', 'ffmpeg', 'ffprobe', 'sox', 'chrome')


def _windows_pre_path_dirs():
    """Get the documented CreateProcess locations ahead of PATH, excluding cwd."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    # Maximum Win32 extended path length including the terminating NUL.
    buffer = ctypes.create_unicode_buffer(32768)

    def query(api, argtypes=(), *args):
        api.argtypes = (*argtypes, wintypes.LPWSTR, wintypes.DWORD)
        api.restype = wintypes.DWORD
        length = api(*args, buffer, len(buffer))
        if not length:
            error = ctypes.WinError(ctypes.get_last_error())
            raise RuntimeError('Cannot qualify Windows executable search: ' + api.__name__) from error
        if length >= len(buffer) or not (path := Path(buffer.value)).is_absolute():
            raise RuntimeError('Invalid Windows executable search path: ' + api.__name__)
        return path

    # A Windows venv redirector can report its Scripts/python.exe as
    # sys.executable while the process image is the base installation's python.exe.
    image = query(kernel.GetModuleFileNameW, (wintypes.HMODULE,), None)
    system = query(kernel.GetSystemDirectoryW)
    windows = query(kernel.GetWindowsDirectoryW)
    return image.parent, system, windows / 'System', windows


def shadowed_windows_tools(paths, tools):
    """Check bare native .exe lookup for this process image and the proposed PATH.

    The launcher must set NoDefaultCurrentDirectoryInExePath in its own
    environment. This is a point-in-time check, not a pin across file changes
    or a qualification of subprocess grandchildren with a different image.
    """
    directories = (*_windows_pre_path_dirs(), *(Path(d.strip('"')) for d in paths if d.strip('"')))
    shadowed = []
    for name in ('ffmpeg', 'ffprobe', 'git', 'sox'):
        selected = None
        for directory in directories:
            candidate = directory / (name + '.exe')
            try:
                mode = candidate.stat().st_mode
            except (FileNotFoundError, NotADirectoryError):
                continue
            if stat.S_ISREG(mode):
                selected = candidate.resolve(strict=True)
                break
        if selected is None or selected != Path(tools[name]).resolve(strict=True):
            shadowed.append(name)
    return shadowed


def configure_environment(prefix=None, source=None, environment=None):
    prefix = Path(prefix if prefix is not None else sys.prefix)
    source = Path(source if source is not None else Path(__file__).resolve().parents[2])
    env = os.environ if environment is None else environment
    if prefix.name != 'venv':
        return False
    root = prefix.parent
    receipt = root / 'installation.json'
    if not receipt.exists():
        return False
    for path in (receipt, root, *root.parents):
        if path.is_symlink() or (path.exists() and getattr(path.lstat(), 'st_file_attributes', 0) & 0x400):
            raise RuntimeError('Managed installation receipt must not follow symlinks/junctions')
    if not receipt.is_file() or receipt.stat().st_size > 65536:
        raise RuntimeError('Invalid managed installation receipt')
    data = json.loads(receipt.read_text(encoding='utf-8'))
    # Installer probes run with their explicit complete environment before ready.
    if data.get('status') == 'preparing' and env.get('FRAMIVRA_VERIFY_ROOT') == str(root):
        return False
    if (data.get('schema') != 'framivra.installation/v1' or data.get('status') != 'ready'
            or data.get('sourceRoot') != str(source) or source != root / 'source'
            or data.get('profile') != 'non-ai' or data.get('renderer') != 'cdp'
            or not re.fullmatch('[0-9a-f]{40}', data.get('commit', ''))):
        raise RuntimeError('Managed environment is incomplete or belongs to another source; rerun the installer')
    paths = [str(prefix / ('Scripts' if sys.platform == 'win32' else 'bin'))]
    tools = data.get('tools', {})
    for name in tool_search_order():
        value = tools.get(name)
        if not isinstance(value, str) or not Path(value).is_absolute() or not Path(value).is_file():
            raise RuntimeError('Managed tool unavailable: ' + name)
        paths.append(str(Path(value).parent))
    windows = sys.platform == 'win32'
    if windows:
        shadowed = shadowed_windows_tools(paths, tools)
        if shadowed:
            raise RuntimeError('Managed runtime executable search shadows selected tools: ' +
                               ', '.join(shadowed) + '. Remove the conflicting tools or rerun the installer.')
    config = data.get('configFile')
    if config != str(root / 'config.json') or not Path(config).is_file():
        raise RuntimeError('Managed configuration unavailable')
    font_env = data.get('environment', {})
    expected_fonts = {'FONTCONFIG_FILE': str(root / 'fonts/fonts.conf')} if sys.platform == 'linux' else {}
    if font_env != expected_fonts:
        raise RuntimeError('Managed font environment mismatch')
    for key, value in font_env.items():
        if not Path(value).is_file():
            raise RuntimeError('Managed font configuration unavailable')
        env.setdefault(key, value)
    env['PATH'] = os.pathsep.join(dict.fromkeys([*paths, env.get('PATH', '')]))
    if windows:
        # Bare native subprocess commands must not prefer the caller's directory.
        env.setdefault('NoDefaultCurrentDirectoryInExePath', '1')
    env.setdefault('VTX_CONFIG', config)
    env.setdefault('VTX_CHROME', tools['chrome'])
    env.setdefault('VTX_GFX', 'cdp')
    return True
