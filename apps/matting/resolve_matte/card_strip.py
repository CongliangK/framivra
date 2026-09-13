#!/usr/bin/env python3
"""调色页片段卡选择器（视觉闭环版）。

每次选择都走：截图 → 检测卡片缩略图边界 → 计算目标卡中心 → 点击 → 红框验证。
不依赖预先标定的固定坐标，卡片数量/宽度/缩放变化均自适应。

用法:
    env python3 card_strip.py detect            # 打印当前检测到的卡片中心
    env python3 card_strip.py select 2          # 选择第 2 张卡（1 基）
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mouse_ctl

SHOT_SCALE = 1.2          # 物理(3840/屏) -> X11(4608/屏) 系数
WIN_ORIGIN_Y = 64
STRIP_SHOT_Y0, STRIP_SHOT_Y1 = 860, 1140   # 卡片带在截屏里的行范围


def screenshot():
    out = subprocess.run(["bash", os.path.expanduser(
        "~/.agents/skills/screenshot/scripts/screenshot.sh"), "1"],
        capture_output=True, text=True)
    path = out.stdout.strip().splitlines()[-1]
    from PIL import Image
    return Image.open(path).convert("RGB")


def to_win(shot_x, shot_y):
    return shot_x * SHOT_SCALE, shot_y * SHOT_SCALE - WIN_ORIGIN_Y


def detect_cards(im=None):
    """返回每张卡的中心 (win_x, win_y)，按从左到右排序。"""
    if im is None:
        im = screenshot()
    px = im.load()
    w, h = im.size
    y0, y1 = STRIP_SHOT_Y0, min(STRIP_SHOT_Y1, h)
    # 每列的平均亮度（缩略图区域），卡片图像亮、间隙暗
    cols = []
    for x in range(0, w, 4):
        s = 0
        n = 0
        for y in range(y0, y1, 8):
            r, g, b = px[x, y]
            s += (r + g + b) / 3
            n += 1
        cols.append((x, s / max(n, 1)))
    thresh = 28
    runs, cur = [], None
    for x, v in cols:
        if v > thresh:
            cur = [x, x] if cur is None else [cur[0], x]
        else:
            if cur and cur[1] - cur[0] > 100:   # 卡片宽 >100 物理 px
                runs.append(cur)
            cur = None
    if cur and cur[1] - cur[0] > 100:
        runs.append(cur)
    cards = []
    for x0, x1 in runs:
        cx_shot = (x0 + x1) / 2
        # 缩略图垂直中线
        cy_shot = (y0 + y1) / 2
        wx, wy = to_win(cx_shot, cy_shot)
        cards.append({"shot_span": (x0, x1), "win": (int(wx), int(wy))})
    return cards


def red_border_center(im):
    """当前选中卡红框中心（shot 坐标）。"""
    px = im.load()
    w, h = im.size
    best = None
    for y in range(STRIP_SHOT_Y0, min(STRIP_SHOT_Y1, h), 4):
        xs = [x for x in range(0, w, 2)
              if (lambda p: p[0] > 140 and p[1] < 100 and p[2] < 100)(px[x, y])]
        if xs and (max(xs) - min(xs)) > 100:
            span = max(xs) - min(xs)
            if best is None or span > best[1]:
                best = ((min(xs) + max(xs)) / 2, span)
    return best  # (cx_shot, span) 或 None


def select_card(k, max_retry=3):
    """选择第 k 张卡（1 基）。成功返回 True。"""
    for attempt in range(max_retry):
        im = screenshot()
        cards = detect_cards(im)
        if len(cards) < k:
            print(f"  [card] 只检测到 {len(cards)} 张卡，需要第 {k} 张", flush=True)
            time.sleep(2)
            continue
        tgt = cards[k - 1]["win"]
        print(f"  [card] 检测到 {len(cards)} 张卡，点击第 {k} 张 @ win {tgt}", flush=True)
        mouse_ctl.click(*tgt)
        time.sleep(1.2)
        im2 = screenshot()
        rb = red_border_center(im2)
        if rb is None:
            print("  [card] 未检测到选中红框，重试", flush=True)
            continue
        # 红框中心应落在目标卡 span 内
        span = cards[k - 1]["shot_span"]
        if span[0] - 10 <= rb[0] <= span[1] + 10:
            print(f"  [card] 第 {k} 张卡已选中 ✓", flush=True)
            return True
        print(f"  [card] 红框在 {rb[0]:.0f}，目标 {span}，重试", flush=True)
        time.sleep(1)
    return False


def main():
    cmd = sys.argv[1]
    if cmd == "detect":
        for i, c in enumerate(detect_cards(), 1):
            print(i, c)
    elif cmd == "select":
        ok = select_card(int(sys.argv[2]))
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
