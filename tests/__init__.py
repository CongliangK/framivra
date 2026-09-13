"""tests 包标记（v2 P1-1）：`python3 -m unittest discover -s tests -t .` 在
Python 3.11+ 要求 start 目录可导入（隐式命名空间包已从 discovery 移除）——
空 __init__ 使 tests 成为常规包，测试模块以 tests.test_* 导入（repo 根为
top-level，from vtx import ... 锚定一致）。"""
