"""tests 包标记（v2 P2a）：`cd apps/publish && env python3 -m unittest discover -s tests -t .`
在 Python 3.11+ 要求 start 目录可导入（隐式命名空间包已从 discovery 移除）——
空 __init__ 使 tests 成为常规包，测试模块以 tests.test_* 导入（apps/publish 为
top-level；repo 根/sys.path 锚定由测试文件自身 parents 链推导）。"""
