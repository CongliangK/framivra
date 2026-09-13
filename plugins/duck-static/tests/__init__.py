"""tests 包标记（v2 P2b）：`cd plugins/duck-static && env python3 -m unittest
discover -s tests -t .` 在 Python 3.11+ 要求 start 目录可导入——空 __init__ 使
tests 成为常规包（plugins/duck-static 为 top-level；repo 根/sys.path 锚定由
测试文件 parents 链推导，与 apps/*/tests、plugins/denoise/tests 同款）。"""
