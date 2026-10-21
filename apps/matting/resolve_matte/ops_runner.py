#!/usr/bin/env python3
"""操作序列（Operation Sequence）回放器。

JSON 格式：
{
  "meta": {"screen": "3840x2160", "ui_note": "达芬奇默认布局，缩放100%"},
  "steps": [
    {"op": "click", "x": 100, "y": 200, "button": 1, "comment": "点击调色页"},
    {"op": "move",  "x": 100, "y": 200},
    {"op": "drag",  "from": [100, 200], "to": [400, 500], "comment": "拖静帧到片段"},
    {"op": "key",   "keys": "ctrl+z"},
    {"op": "type",  "text": "abc"},
    {"op": "wait",  "ms": 2000, "comment": "等 Magic Mask 演算"},
    {"op": "shell", "cmd": "env python3 some_step.py", "comment": "插入 CLI 步骤"}
  ]
}

坐标默认为达芬奇窗口相对坐标；加 "screen": true 表示屏幕绝对坐标。

用法:
    env python3 ops_runner.py op_sequence.json           # 全部执行
    env python3 ops_runner.py op_sequence.json --dry     # 只打印不执行
    env python3 ops_runner.py op_sequence.json --from 3  # 从第3步（0基）执行
"""
import argparse
import json
import subprocess
import sys
import time

import mouse_ctl


def run_step(s, dry=False):
    op = s["op"]
    desc = s.get("comment", "")
    if op == "click":
        print(f"  click ({s['x']},{s['y']}) btn={s.get('button',1)} {desc}")
        if not dry:
            mouse_ctl.click(s["x"], s["y"], s.get("button", 1), s.get("screen", False))
    elif op == "move":
        print(f"  move ({s['x']},{s['y']}) {desc}")
        if not dry:
            mouse_ctl.move(s["x"], s["y"], s.get("screen", False))
    elif op == "drag":
        print(f"  drag {s['from']} -> {s['to']} {desc}")
        if not dry:
            mouse_ctl.drag(*s["from"], *s["to"], screen=s.get("screen", False))
    elif op == "key":
        print(f"  key {s['keys']} {desc}")
        if not dry:
            mouse_ctl.key(s["keys"])
    elif op == "type":
        print(f"  type {s['text']!r} {desc}")
        if not dry:
            mouse_ctl.type_text(s["text"])
    elif op == "wait":
        print(f"  wait {s['ms']}ms {desc}")
        if not dry:
            time.sleep(s["ms"] / 1000)
    elif op == "shell":
        print(f"  shell: {s['cmd']} {desc}")
        if not dry:
            r = subprocess.run(s["cmd"], shell=True)
            if r.returncode != 0 and not s.get("ignore_fail"):
                raise RuntimeError(f"shell 步骤失败（exit {r.returncode}）: {s['cmd']}")
    else:
        raise ValueError(f"未知 op: {op}")
    if not dry:
        time.sleep(s.get("settle_ms", 300) / 1000)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sequence")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--from", dest="start", type=int, default=0)
    ap.add_argument("--to", dest="end", type=int, default=None)
    a = ap.parse_args()
    seq = json.load(open(a.sequence))
    print(f"序列: {a.sequence}  共 {len(seq['steps'])} 步")
    if not a.dry:
        g = mouse_ctl.getgeometry()
        print(f"达芬奇窗口: id={g['id']} ({g['x']},{g['y']}) {g['w']}x{g['h']}")
        mouse_ctl.activate()
    for i, s in enumerate(seq["steps"]):
        if i < a.start or (a.end is not None and i >= a.end):
            continue
        print(f"[{i}]")
        run_step(s, a.dry)
    print("完成")


if __name__ == "__main__":
    main()
