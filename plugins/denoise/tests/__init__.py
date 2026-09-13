"""tests 包标记（v2 P2b）：`cd plugins/denoise && env python3 -m unittest discover
-s tests -t .` 在 Python 3.11+ 要求 start 目录可导入——空 __init__ 使 tests 成为
常规包，测试模块以 tests.test_* 导入（plugins/denoise 为 top-level；repo 根
sys.path 锚定由测试文件 parents 链推导，与 apps/*/tests 同款）。"""
