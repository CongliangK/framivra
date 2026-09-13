"""vtx 内核包（v2 插件化架构，永不可关——ARCHITECTURE §0.1/§1）。

- 稳定公共面见 vtx.api（apps/plugins/skills 复用）；本 __init__ 只再导出
  CLI 主入口 main 与 _stage_receipt_name（tests/test_pipeline.py 等既有依赖）。
- 模块内一律相对导入（§1.4）；外部一律 `from vtx import ...` / `import vtx.xxx`。
  禁止 sys.path 注入 repo/vtx——平铺/包双实例会让 infra 的 receipt 锁 /
  hygiene 的 manifest 锁 / loudness_cache 的 flock 落在两个模块对象上，
  锁形同虚设（双模块裂分，§1.4）。
- 入口三通（§5.2 clone 即用与 pip 安装同码同义）：`python3 vtx/vtx.py`
  （薄启动器）/ `python3 -m vtx` / console script `vtx`。
"""
from pathlib import Path

__all__ = ["main", "_stage_receipt_name", "__version__"]


def _read_version() -> str:
    """VERSION 文件读当前版本（v2 P1-2 双通道）：repo 根 VERSION（clone 即用，
    单一事实源）→ 包内 VERSION（pip 安装布局，pyproject package-data 预留通道）
    → 内置字面量回落。版本串只用于标识，不参与任何门控（P0 复审口径）。"""
    here = Path(__file__).resolve().parent
    for candidate in (here.parent / "VERSION", here / "VERSION"):
        try:
            value = candidate.read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError:
            continue
    return "2.0.0"


__version__ = _read_version()

from .cli import main, _stage_receipt_name   # noqa: E402（需在 __version__ 后无依赖序，cli 为纯相对导入）
