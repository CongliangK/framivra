"""Environment of the explicit, editable managed installation only.

A normal clone/wheel never scans HOME or the workspace for configuration. An
installer-created venv may read its adjacent receipt, bound to this source root.
Tool argv remain owned by their consumers; the receipt supplies paths, not code.
"""
import json
import os
from pathlib import Path
import re
import sys


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
    for name in ('git', 'ffmpeg', 'ffprobe', 'sox', 'chrome'):
        value = tools.get(name)
        if not isinstance(value, str) or not Path(value).is_absolute() or not Path(value).is_file():
            raise RuntimeError('Managed tool unavailable: ' + name)
        paths.append(str(Path(value).parent))
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
    env.setdefault('VTX_CONFIG', config)
    env.setdefault('VTX_CHROME', tools['chrome'])
    env.setdefault('VTX_GFX', 'cdp')
    return True
