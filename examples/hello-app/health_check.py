#!/usr/bin/env python3
"""hello-app 健康探针（doctor health.command 消费，v2 §5.1 / §3.1）。

约定：exit 0 = 健康（stdout 首行被 doctor 摘要展示）；非 0 = unhealthy（enabled
件的探针失败会计入 doctor FAIL 门）。纯标准库自检——真实 app 在这里探测自己的
关键依赖（解释器版本/外部二进制/权重），缺失时打印缺什么再以非 0 退出。
"""
import platform
import sys

v = sys.version_info
ok = (v.major, v.minor) >= (3, 9)
print(f"hello-app healthy｜python {v.major}.{v.minor}.{v.micro}｜{platform.system()}")
sys.exit(0 if ok else 1)
