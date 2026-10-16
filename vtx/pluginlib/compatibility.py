"""Explicit requires.core minimum gate; no third-party version parser.

The manifest contract spells >=version. Only stable MAJOR.MINOR.PATCH minimums
are supported; this is not a general SemVer/PEP 440 range implementation.
"""
import re

_VERSION = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
_MINIMUM = re.compile(r">=\s*" + _VERSION)
_RELEASE = re.compile(_VERSION)
_UPGRADE = "升级/修复内核或安装兼容插件；入口 https://github.com/CongliangK/framivra#readme"


def core_requirement_error(value):
    """Return a structural error, or None for the supported explicit syntax."""
    if not isinstance(value, str) or not _MINIMUM.fullmatch(value.strip()):
        return (f"requires.core {value!r} 必须为非空最低版本约束 >=MAJOR.MINOR.PATCH"
                "（仅稳定版本，不支持范围组合/预发布/通配符）；" + _UPGRADE)
    return None


def require_compatible_core(name, manifest):
    """Fail before plugin import; an omitted core field retains legacy policy."""
    requires = manifest["requires"]
    if "core" not in requires:
        return
    # Import at call time: vtx initializes its version before loading the CLI.
    from vtx import __version__
    constraint = requires["core"]
    error = core_requirement_error(constraint)
    current = _RELEASE.fullmatch(__version__) if isinstance(__version__, str) else None
    prefix = f"插件 {name}: 检测内核版本 {__version__!r}, requires.core={constraint!r}；"
    if error:
        raise RuntimeError(prefix + error)
    if current is None:
        raise RuntimeError(prefix + "无法判定稳定内核版本；" + _UPGRADE)
    minimum = _MINIMUM.fullmatch(constraint.strip())
    # Compare numeric components without int conversion (unbounded input length).
    current_parts = tuple((len(x), x) for x in current.groups())
    minimum_parts = tuple((len(x), x) for x in minimum.groups())
    if current_parts < minimum_parts:
        raise RuntimeError(prefix + "不兼容，拒绝加载；" + _UPGRADE)
