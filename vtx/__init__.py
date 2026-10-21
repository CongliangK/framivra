"""vtx 内核包（v2 插件化架构，永不可关——ARCHITECTURE §0.1/§1）。

- 稳定公共面见 vtx.api（apps/plugins/skills 复用）；本包拥有 CLI 生命周期
  main 与纯函数 _stage_receipt_name；执行层仅在 main 调用时导入。
- 模块内一律相对导入（§1.4）；外部一律 `from vtx import ...` / `import vtx.xxx`。
  禁止 sys.path 注入 repo/vtx——平铺/包双实例会让 infra 的 receipt 锁 /
  hygiene 的 manifest 锁 / loudness_cache 的 flock 落在两个模块对象上，
  锁形同虚设（双模块裂分，§1.4）。
- 入口三通（§5.2 clone 即用与 pip 安装同码同义）：`python3 vtx/vtx.py`
  （薄启动器）/ `python3 -m vtx` / console script `vtx`。
"""
import re
from pathlib import Path

__all__ = ["main", "_stage_receipt_name", "__version__"]

# 回退字面量与三源（VERSION/pyproject.toml/本文件）同值，改一必三（doc_gates
# L6 守护）。F-03/#25：旧值 2.0.0 与 pyproject 2.1.0 漂移，wheel 布局（无根
# VERSION 文件）曾误报旧版本；B01 起字面量随三源同步。
_FALLBACK_VERSION = "2.1.0"

# 发行包名（pyproject [project].name）；wheel 安装布局经 importlib.metadata 取版本。
# B01 红队对抗补：此常量是 ④ 查询名的运行时锚——L6 门（verify_version_sync）
# 打印本值并与 pyproject name 行比对，改名须两处同步（堵「篡改 name 行让已装
# 陈旧 dist 被 ④ 静默 skip」的绕过）。
_DIST_NAME = "framivra"

# PEP 621 version 行（行首零缩进；与 scripts/verify_version_sync.py L6 门同口径）。
_PYPROJECT_VERSION_RE = re.compile(r'(?m)^version\s*=\s*"([^"]*)"\s*$')


def _version_from_pyproject(here: Path) -> str:
    """源树 pyproject.toml 的 version 行（here=包目录，取其父级文件）。

    wheel/site-packages 布局无 pyproject.toml → 返回空串（OSError 静默）。"""
    try:
        text = (here.parent / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return ""
    m = _PYPROJECT_VERSION_RE.search(text)
    return m.group(1).strip() if m else ""


def _version_from_metadata() -> str:
    """已安装发行包（framivra）的 importlib.metadata 版本。

    wheel 安装布局无 VERSION/pyproject，此处为权威回退（F-07/#7 版本部分）；
    未安装发行包 → 返回空串。import 延迟到调用点：常态（VERSION 在位）零开销。"""
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover —— Python>=3.8 恒有
        return ""
    try:
        return version(_DIST_NAME)
    except PackageNotFoundError:
        return ""


def _read_version(here=None) -> str:
    """VERSION 文件读当前版本（v2 P1-2 双通道 + B01 回退链）：repo 根 VERSION
    （clone 即用，单一事实源）→ 包内 VERSION（pip 安装布局，pyproject
    package-data 预留通道）→ pyproject.toml version 行（源树缺 VERSION 时与
    三源纪律一致）→ importlib.metadata（wheel 安装布局权威源）→ 内置字面量
    （与三源同步）。版本串供标识及显式 requires.core 兼容门使用。
    here=包目录，默认本包；测试注入模拟布局（tests/distribution/test_version_sources.py）。"""
    here = Path(here) if here is not None else Path(__file__).resolve().parent
    for candidate in (here.parent / "VERSION", here / "VERSION"):
        try:
            value = candidate.read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError:
            continue
    return (
        _version_from_pyproject(here)
        or _version_from_metadata()
        or _FALLBACK_VERSION
    )


__version__ = _read_version()


def _stage_receipt_name(name: str, draft: bool) -> str:
    """阶段回执名（现行内联规则抽 helper，2026-09-12）：draft 模式的 video/mux 记为
    video_draft/mux_draft（receipt/依赖门/下游撤销共用同一名字），其余阶段原名。
    线性序列与流水线两处复用（单一权威）。"""
    return name + "_draft" if draft and name in ("video", "mux") else name


def main():
    """One invocation, including input-load and finish failures."""
    import sys

    from .cli import _main
    from . import events
    from .infra import ProjectPathError

    if sys.platform == 'win32':
        # All three entry points share this path. Redirected Windows streams
        # may default to GBK, which cannot encode status glyphs or user paths.
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding='utf-8', errors='replace')
            except (AttributeError, OSError, ValueError):
                pass
    execution = events.ExecutionContext()
    code, failure = 0, None
    try:
        try:
            result = _main(execution)
            code = result or 0
            return result
        except ProjectPathError as error:
            print(f'✗ {error}', file=sys.stderr)
            code, failure = 1, error
            return 1
    except BaseException as error:
        code, failure = events.exit_code(error), error
        raise
    finally:
        execution.finish(code, failure)
