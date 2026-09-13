#!/usr/bin/env python3
"""坐标拾取器：运行后进入循环——把鼠标移到目标位置，按一次回车，打印当前鼠标的
窗口相对坐标（JSON 片段，可直接粘进操作序列）。Ctrl+C 退出。

用法:
    env python3 pick_pos.py            # 循环拾取
    env python3 pick_pos.py --delay 5  # 延迟 5 秒后读位置（给你时间移动鼠标）
"""
import argparse
import json
import sys
import time

import mouse_ctl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=int, default=5, help="每次拾取前的倒计时秒数")
    ap.add_argument("--screen", action="store_true", help="输出屏幕绝对坐标而非窗口相对坐标")
    a = ap.parse_args()
    g = mouse_ctl.getgeometry()
    print(f"达芬奇窗口: ({g['x']},{g['y']}) {g['w']}x{g['h']}  id={g['id']}")
    print(f"每次读数倒计时 {a.delay}s，Ctrl+C 退出。把鼠标放到目标点上然后等读数。\n")
    while True:
        try:
            for i in range(a.delay, 0, -1):
                print(f"\r{i}s ", end="", flush=True)
                time.sleep(1)
            loc = mouse_ctl.xdo("getmouselocation", "--shell")
            d = dict(l.split("=", 1) for l in loc.splitlines() if "=" in l)
            sx, sy = int(d["X"]), int(d["Y"])
            if a.screen:
                print(f"\r{{\"op\": \"click\", \"x\": {sx}, \"y\": {sy}, \"screen\": true}}   ")
            else:
                wx, wy = sx - g["x"], sy - g["y"]
                print(f"\r{{\"op\": \"click\", \"x\": {wx}, \"y\": {wy}}}   // 屏幕({sx},{sy})")
        except KeyboardInterrupt:
            print("\n退出")
            return


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
