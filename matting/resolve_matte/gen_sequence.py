#!/usr/bin/env python3
"""生成 9 轨道抠像操作序列 op_sequence.json。

依据 my_ops.json（用户在片段01上的完整人工操作录制，丢弃最后的焦点点击），
前置加上"点击共享静帧集"步骤，后接逐轨的"点卡→右键静帧→应用→演算→等日志"。

卡片条按时长升序排列、标签乱序（实测 matte_auto_v8 grid9）：
    卡1→V1  卡2→V5  卡3→V2  卡4→V3  卡5→V9
    卡6→V4  卡7→V8  卡8→V6  卡9→V7
坐标为达芬奇窗口相对坐标。
"""
import json
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
_CARD_POINTS = json.load(open(_DIR + '/card_points.json'))['points']

# 卡片条按时长升序：卡位 k 对应的轨道（实测映射，时间线布局变化需重新核对）
CARD_TO_TRACK = {1: 1, 2: 5, 3: 2, 4: 3, 5: 9, 6: 4, 7: 8, 8: 6, 9: 7}

ALBUM_CLICK = (167, 254)           # 点击"共享静帧集"相册（用户录制[02]验证）
STILL_RIGHT_CLICK = (623, 286)     # 右键 PowerGrade 模板静帧
MENU_APPLY = (820, 420)            # 菜单：应用
STEP_A = (2242, 525)               # [04]
STEP_B = (2160, 737)               # [05]
STEP_C = (2359, 748)               # [06]
STEP_D = (2631, 1696)              # [07] 演算/跟踪


def steps_for_card(k):
    """k: 卡位 1..9（注意卡片与轨道的映射见 CARD_TO_TRACK）"""
    tr = CARD_TO_TRACK[k]
    return [
        {"op": "click", "x": _CARD_POINTS[k-1][0], "y": _CARD_POINTS[k-1][1],
         "comment": f"选择片段卡 {k:02d}（轨道 V{tr}）"},
        {"op": "wait", "ms": 800},
        {"op": "click", "x": STILL_RIGHT_CLICK[0], "y": STILL_RIGHT_CLICK[1],
         "button": 3, "comment": "右键 PowerGrade 模板静帧"},
        {"op": "wait", "ms": 600},
        {"op": "click", "x": MENU_APPLY[0], "y": MENU_APPLY[1], "comment": "菜单：应用"},
        {"op": "wait", "ms": 2000},
        {"op": "click", "x": STEP_A[0], "y": STEP_A[1], "comment": "录制步骤[04]"},
        {"op": "wait", "ms": 800},
        {"op": "click", "x": STEP_B[0], "y": STEP_B[1], "comment": "录制步骤[05]"},
        {"op": "wait", "ms": 800},
        {"op": "click", "x": STEP_C[0], "y": STEP_C[1], "comment": "录制步骤[06]"},
        {"op": "wait", "ms": 3500},
        {"op": "click", "x": STEP_D[0], "y": STEP_D[1], "comment": "录制步骤[07]：演算"},
        {"op": "shell", "cmd": "env python3 wait_tracking_done.py --timeout 900 --stable 20",
         "comment": "日志监听：等演算全部段完成（正向+反向，期间指针被达芬奇抓住勿操作）",
         "ignore_fail": True},
        {"op": "wait", "ms": 3000, "comment": "完成后缓冲"},
    ]


def main():
    steps = [
        {"op": "click", "x": ALBUM_CLICK[0], "y": ALBUM_CLICK[1], "comment": "选择共享静帧集相册"},
        {"op": "wait", "ms": 800},
    ]
    for k in range(1, 10):
        steps += steps_for_card(k)
    doc = {
        "meta": {
            "screen": "3840x2160@1.2 (X11 4608x2592 per screen)",
            "window_origin": (0, 64),
            "note": "坐标为达芬奇窗口相对坐标；卡片映射 CARD_TO_TRACK 见 gen_sequence.py",
        },
        "steps": steps,
    }
    json.dump(doc, open(os.path.join(_DIR, "op_sequence.json"), "w"), ensure_ascii=False, indent=1)
    print(f"生成 {len(steps)} 步 -> op_sequence.json")
    print("卡片点位:", _CARD_POINTS[:9])
    print("卡片→轨道:", CARD_TO_TRACK)


if __name__ == "__main__":
    main()
