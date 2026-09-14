"""tests 包标记（v2 P2b）：`cd plugins/bgm-render && env python3 -m unittest discover -s tests -t .`
在 Python 3.11+ 要求 start 目录可导入——空 __init__ 使 tests 成为常规包
（repo 根/sys.path 锚定由测试文件 parents 链推导，与 plugins/denoise/tests 同款）。"""
