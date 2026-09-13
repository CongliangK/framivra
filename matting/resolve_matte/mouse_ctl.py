#!/usr/bin/env python3
"""达芬奇鼠标控制基建（xdotool / XWayland）。

坐标体系约定：所有 JSON 操作序列里的坐标默认是 **窗口相对坐标**（相对达芬奇主窗口
左上角），避免窗口位置变化导致点位漂移。分辨率/缩放改变后序列必须重新校准。

用法:
    env python3 mouse_ctl.py window                 # 打印主窗口位置与大小
    env python3 mouse_ctl.py click X Y [--screen]   # 点击（默认窗口坐标）
    env python3 mouse_ctl.py move X Y  [--screen]
    env python3 mouse_ctl.py drag X1 Y1 X2 Y2       # 按下-移动-释放（用于拖静帧）
    env python3 mouse_ctl.py key KEYS               # 如 "ctrl+z" / "Return"
    env python3 mouse_ctl.py type TEXT
"""
import argparse
import subprocess
import sys
import time

WINDOW_HINTS = ["DaVinci Resolve", "resolve"]


def xdo(*args):
    out = subprocess.run(["xdotool", *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"xdotool {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout.strip()


def find_window():
    """返回达芬奇主窗口 X id。"""
    for hint in WINDOW_HINTS:
        for opt in (["--onlyvisible", "--name"], ["--name"], ["--class"]):
            try:
                ids = xdo("search", *opt, hint).splitlines()
                ids = [i for i in ids if i.strip()]
                if ids:
                    return int(ids[0])
            except RuntimeError:
                continue
    raise RuntimeError("找不到达芬奇窗口（是否最小化到别的桌面？）")


def getgeometry(win_id=None):
    wid = win_id or find_window()
    out = xdo("getwindowgeometry", "--shell", str(wid))
    info = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return {"id": wid, "x": int(info["X"]), "y": int(info["Y"]),
            "w": int(info["WIDTH"]), "h": int(info["HEIGHT"])}


def activate():
    win = find_window()
    xdo("windowactivate", "--sync", str(win))
    time.sleep(0.3)
    return win


def to_screen(x, y):
    g = getgeometry()
    return g["x"] + x, g["y"] + y


def _pointer_write_scale():
    """xdotool mousemove 写入坐标与光标实际坐标(读回单位)存在倍率差
    （XWayland 分数缩放所致）。启动时用一次往返探测自动标定。"""
    global _WRITE_SCALE
    if _WRITE_SCALE is None:
        xdo("mousemove", "--sync", "500", "400")
        time.sleep(0.2)
        out = xdo("getmouselocation", "--shell")
        d = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
        _WRITE_SCALE = (int(d["X"]) / 500 + int(d["Y"]) / 400) / 2
    return _WRITE_SCALE


_WRITE_SCALE = None


def move(x, y, screen=False):
    sx, sy = (x, y) if screen else to_screen(x, y)
    k = _pointer_write_scale()
    xdo("mousemove", "--sync", str(round(sx / k)), str(round(sy / k)))


def click(x, y, button=1, screen=False, delay=0.08):
    move(x, y, screen)
    time.sleep(delay)
    xdo("mousedown", str(button))
    time.sleep(0.06)
    xdo("mouseup", str(button))


def drag(x1, y1, x2, y2, screen=False, steps=25, pause=0.02):
    move(x1, y1, screen)
    time.sleep(0.15)
    sx1, sy1 = (x1, y1) if screen else to_screen(x1, y1)
    sx2, sy2 = (x2, y2) if screen else to_screen(x2, y2)
    xdo("mousedown", "1")
    time.sleep(0.1)
    for i in range(1, steps + 1):
        cx = sx1 + (sx2 - sx1) * i // steps
        cy = sy1 + (sy2 - sy1) * i // steps
        xdo("mousemove", "--sync", str(cx), str(cy))
        time.sleep(pause)
    time.sleep(0.15)
    xdo("mouseup", "1")


def key(keys):
    xdo("key", "--clearmodifiers", keys)


def type_text(text):
    xdo("type", "--clearmodifiers", text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["window", "click", "move", "drag", "key", "type", "activate"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--screen", action="store_true", help="坐标为屏幕绝对坐标")
    a = ap.parse_args()
    if a.cmd == "window":
        print(getgeometry())
    elif a.cmd == "activate":
        print(activate())
    elif a.cmd == "click":
        x, y = int(a.args[0]), int(a.args[1])
        b = int(a.args[2]) if len(a.args) > 2 else 1
        click(x, y, b, a.screen)
    elif a.cmd == "move":
        move(int(a.args[0]), int(a.args[1]), a.screen)
    elif a.cmd == "drag":
        drag(*map(int, a.args[:4]), screen=a.screen)
    elif a.cmd == "key":
        key(a.args[0])
    elif a.cmd == "type":
        type_text(a.args[0])


if __name__ == "__main__":
    main()
