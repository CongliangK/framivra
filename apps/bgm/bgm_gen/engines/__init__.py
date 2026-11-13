"""引擎层包（PLAN.md §2/BG-D6）。

自动发现：本目录下所有非下划线模块在包导入时自动 import，
引擎类通过 @register_engine("name") 自注册进 ENGINES——
worker 并行开发时只增文件、不改共享文件。

注意约定：引擎模块的模块级 import 只允许标准库+numpy（系统 python3 可用）；
torch/ffmpeg 子进程等重依赖一律放到函数体内或经 runtime.json 指定的解释器调起。
"""

import importlib
import pkgutil

from .base import (ENGINES, BaseEngine, EngineError, load_runtime,
                   measure_wav, register_engine, run_engine)

_loaded = []
for _m in pkgutil.iter_modules(__path__):
    if _m.name.startswith("_") or _m.name == "base":
        continue
    importlib.import_module(f"{__name__}.{_m.name}")
    _loaded.append(_m.name)

__all__ = ["ENGINES", "BaseEngine", "EngineError", "load_runtime", "measure_wav",
           "register_engine", "run_engine", "_loaded"]
