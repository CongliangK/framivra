#!/usr/bin/env python3
"""等待 Magic Mask 演算完成（多段稳定判定）。

关键经验：达芬奇演算分正向+反向多段，第一段 "Tracking has been completed"
之后往往立即跟着新的 "Start tracking"（反向段），且**演算期间达芬奇会抓住
指针，所有鼠标操作都会失效**。因此必须等到"完成后再无新开始"才可继续
鼠标操作。

判定规则：见到 completed 后，连续 STABLE_SECONDS 秒内没有新的
"Start tracking" 行，才算真正完成。

用法: env python3 wait_tracking_done.py [--timeout 600] [--stable 20] [--log FILE]
超时未开始/未完成以非零退出。
"""
import argparse
import os
import sys
import time

# Resolve 调试日志：--log 参数 > env RESOLVE_DEBUG_LOG > ~ 展开默认（原硬编码平移）
LOG_DEFAULT = os.path.expanduser(
    "~/.local/share/DaVinciResolve/logs/ResolveDebug.txt")
DONE_MARKS = ("Tracking has been completed", "No more source frame to track")
START_MARK = "Start tracking"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--stable", type=int, default=20,
                    help="completed 后需保持无新 Start tracking 的秒数（等反向段）")
    ap.add_argument("--log", default=os.environ.get("RESOLVE_DEBUG_LOG") or LOG_DEFAULT,
                    help="ResolveDebug.txt 路径（env RESOLVE_DEBUG_LOG 同效）")
    a = ap.parse_args()
    LOG = a.log

    try:
        f = open(LOG, "r", errors="ignore")
        f.seek(0, 2)  # 只看新增
        offset = f.tell()
    except FileNotFoundError:
        sys.exit("找不到日志文件")

    t0 = time.time()
    started = False
    done_at = None
    last_activity = time.time()
    while time.time() - t0 < a.timeout:
        f.seek(offset)
        new = f.read()
        offset = f.tell()
        for line in new.splitlines():
            if START_MARK in line:
                started = True
                done_at = None  # 反向段开始，重新等稳定
                last_activity = time.time()
                print("[wait] 演算段开始:", line.split("|")[-1].strip()[-90:], flush=True)
            if any(m in line for m in DONE_MARKS):
                if done_at is None:
                    print("[wait] 一段演算完成，等待确认无后续段 ...", flush=True)
                done_at = time.time()
                last_activity = time.time()
        if done_at is not None and time.time() - done_at >= a.stable:
            print(f"[wait] 演算全部完成 ✓（稳定 {a.stable}s 无新段）", flush=True)
            return
        time.sleep(1)
    if not started:
        print("[wait] 超时且未见演算开始（可能未触发跟踪，或指针被达芬奇抓住）", file=sys.stderr)
    elif done_at is None:
        print("[wait] 演算超时未完成", file=sys.stderr)
    else:
        print("[wait] 演算段间间隔过长，超时", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
