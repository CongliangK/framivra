#!/usr/bin/env python3
"""鼠标操作记录器：从内核 input 层捕获所有鼠标左键事件，实时打印点击/拖拽的
达芬奇窗口相对坐标，Ctrl+C 结束并输出整段 JSON。

用法:
    env python3 record_clicks.py                # 开始记录，Ctrl+C 结束
    env python3 record_clicks.py --out ops.json # 同时写入 JSON 文件
"""
import argparse
import json
import os
import struct
import sys
import time

import mouse_ctl

# ---- 找鼠标设备（读内核事件，Wayland/X11 均有效）----

def find_mouse_devices():
    devs = []
    names = {}
    with open("/proc/bus/input/devices") as f:
        cur_handlers = None
        cur_name = ""
        for line in f:
            if line.startswith("N:"):
                cur_name = line.split(":", 1)[1].strip()
            elif line.startswith("H:"):
                low = line.lower()
                if "handlers=" not in low:
                    continue
                cur_handlers = line[low.index("handlers=") + len("handlers="):].strip().split()
                for h in cur_handlers:
                    # handlers 里有 mouseN 的就是指针设备（与 Name 无关）
                    if h.startswith("event") and any(x.startswith("mouse") for x in cur_handlers):
                        path = f"/dev/input/{h}"
                        if os.access(path, os.R_OK):
                            devs.append(path)
                            names[path] = cur_name
    return devs, names


EVENT_FMT = "@llhHi"  # timeval(2*long), type, code, value
EVENT_SIZE = struct.calcsize(EVENT_FMT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="JSON 输出文件路径")
    ap.add_argument("--raw", action="store_true", help="同时打印屏幕绝对坐标")
    a = ap.parse_args()

    try:
        g = mouse_ctl.getgeometry()
        print(f"达芬奇窗口: ({g['x']},{g['y']}) {g['w']}x{g['h']}  id={g['id']}")
    except RuntimeError as e:
        print(f"警告: {e}（只记录屏幕绝对坐标）")
        g = None

    devs, names = find_mouse_devices()
    if not devs:
        sys.exit("找不到可读的鼠标 event 设备（检查 /dev/input 权限）")
    print(f"监听设备: {[names[d] for d in devs]}")
    print("开始记录。在达芬奇里正常操作，Ctrl+C 结束。\n")

    clicks = []  # {t, x, y, wx, wy, kind}
    press_pos = {}  # dev -> (sx, sy, t, btn)
    import select
    fobjs = {d: open(d, "rb", buffering=0) for d in devs}
    try:
        while True:
            readable, _, _ = select.select(list(fobjs.values()), [], [], 0.05)
            for f in readable:
                d = f.name
                data = os.read(f.fileno(), EVENT_SIZE * 32)
                for off in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                    _, _, etype, code, value = struct.unpack(EVENT_FMT, data[off:off + EVENT_SIZE])
                    # EV_KEY(1), BTN_LEFT=272 / BTN_RIGHT=273; EV_REL(2) 移动忽略
                    if etype != 1 or code not in (272, 273):
                        continue
                    out = mouse_ctl.xdo("getmouselocation", "--shell")
                    dd = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
                    sx, sy = int(dd["X"]), int(dd["Y"])
                    t = time.time()
                    btn = "left" if code == 272 else "right"
                    if value == 1:  # press
                        press_pos[d] = (sx, sy, t, btn)
                    elif value == 0 and d in press_pos:  # release
                        psx, psy, pt, pbtn = press_pos.pop(d)
                        moved = abs(sx - psx) + abs(sy - psy)
                        kind = "drag" if moved > 8 else "click"
                        rec = {"t": round(t, 2), "kind": kind, "button": pbtn,
                               "from": [psx, psy], "to": [sx, sy]}
                        if g:
                            rec["win_from"] = [psx - g["x"], psy - g["y"]]
                            rec["win_to"] = [sx - g["x"], sy - g["y"]]
                        clicks.append(rec)
                        line = f"[{len(clicks):02d}] {kind:4s} {pbtn} "
                        if kind == "click":
                            line += f"win=({rec['win_from'][0]},{rec['win_from'][1]})"
                        else:
                            line += f"win={rec['win_from']} -> {rec['win_to']}"
                        if a.raw:
                            line += f"  screen={rec['from']}->{rec['to']}"
                        print(line, flush=True)
    except KeyboardInterrupt:
        print(f"\n\n共记录 {len(clicks)} 个操作。")
        if a.out:
            json.dump({"meta": {"window": g}, "events": clicks}, open(a.out, "w"),
                      ensure_ascii=False, indent=1)
            print(f"已写入 {a.out}")


if __name__ == "__main__":
    main()
