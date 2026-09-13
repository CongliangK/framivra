#!/usr/bin/env python3
"""等待 Magic Mask 演算完成：监听 ResolveDebug.txt 的新增行，
出现 "Tracking has been completed"（或 No more source frame）即退出。

用法: env python3 wait_tracking_done.py [--timeout 600]
在日志无新增且超时后以非零退出，便于流水线感知异常。
"""
import argparse
import sys
import time

LOG = "/home/liangruochong/.local/share/DaVinciResolve/logs/ResolveDebug.txt"
DONE_MARKS = ("Tracking has been completed", "No more source frame to track")
START_MARK = "Start tracking"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args()

    try:
        f = open(LOG, "r", errors="ignore")
        f.seek(0, 2)  # 只看新增
        offset = f.tell()
    except FileNotFoundError:
        sys.exit("找不到日志文件")

    t0 = time.time()
    started = False
    while time.time() - t0 < a.timeout:
        f.seek(offset)
        new = f.read()
        offset = f.tell()
        for line in new.splitlines():
            if START_MARK in line:
                started = True
                print("[wait] 检测到演算开始:", line.split("|")[-1].strip(), flush=True)
            if any(m in line for m in DONE_MARKS):
                print("[wait] 演算完成 ✓", flush=True)
                return
        time.sleep(1)
    if not started:
        print("[wait] 超时且未见演算开始（可能未触发跟踪）", file=sys.stderr)
    else:
        print("[wait] 演算超时未完成", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
