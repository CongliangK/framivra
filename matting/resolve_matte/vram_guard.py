#!/usr/bin/env python3
"""显存护栏：VRAM 占用超阈值则干净地重启达芬奇（保存工程→杀进程→等显存释放→重开）。

用法: env python3 vram_guard.py [--threshold 10000] [--project NAME] [--timeline grid9]
"""
import argparse
import subprocess
import sys
import time

sys.path.insert(0, "/home/liangruochong/文档/01_活跃项目/video_tools/matting/resolve_matte")
import mouse_ctl
from resolve_ctl import get_resolve, ensure_project

RESOLVE_PAT = "/opt/resolve/bin/resolve"


def vram_used():
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                          "--format=csv,noheader,nounits"], capture_output=True, text=True)
    return int(out.stdout.strip().splitlines()[0])


def free_vram():
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total,memory.used",
                          "--format=csv,noheader,nounits"], capture_output=True, text=True)
    total, used = [int(x) for x in out.stdout.strip().splitlines()[0].split(",")]
    return total - used


KILLABLE = {
    # 名称片段 -> 进程匹配模式（可安全自动重启/无害的应用）
    "showtime": "showtime",
    "firefox": "firefox",
}


def kill_apps(names):
    for n in names:
        pat = KILLABLE.get(n, n)
        subprocess.run(["pkill", "-f", pat], capture_output=True)
        print(f"[vram_guard] 已结束进程: {n}", flush=True)
    time.sleep(2)


def alive():
    # 注意：不能用 pgrep -x resolve（实测匹配不到进程名），必须 -f 全命令行
    return subprocess.run(["pgrep", "-f", RESOLVE_PAT], capture_output=True).returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=int, default=10000, help="MiB，超过则重启")
    ap.add_argument("--project", default="matte_auto_v7")
    ap.add_argument("--timeline", default="grid9")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--min-free", type=int, default=15000, help="启动前要求的最小空闲显存 MiB")
    ap.add_argument("--floor", type=int, default=13000, help="清理后放行的下限 MiB")
    ap.add_argument("--kill", default="showtime", help="显存不足时允许清理的应用白名单")
    a = ap.parse_args()

    used = vram_used()
    print(f"[vram_guard] 当前显存 {used} MiB / 阈值 {a.threshold}", flush=True)
    need_restart = a.force or used > a.threshold or not alive()
    if need_restart:
        # 1) 尽力保存工程
        try:
            r = get_resolve(auto_launch=False)
            if r:
                r.GetProjectManager().GetCurrentProject().SaveProject()
                print("[vram_guard] 已保存工程", flush=True)
        except Exception as e:
            print("[vram_guard] 保存跳过:", e, flush=True)
        time.sleep(1)
        # 2) 杀进程
        subprocess.run(["pkill", "-f", RESOLVE_PAT])
        for _ in range(10):
            if not alive():
                break
            time.sleep(1)
        print("[vram_guard] 进程已退出", flush=True)
        # 3) 等显存释放；目标：启动达芬奇前空闲 ≥ min_free
        for _ in range(45):
            if free_vram() >= a.min_free:
                break
            time.sleep(2)
        if free_vram() < a.min_free and a.kill:
            kill_apps(a.kill.split(","))
            for _ in range(30):
                if free_vram() >= a.min_free:
                    break
                time.sleep(2)
        fv = free_vram()
        if fv >= a.min_free:
            print(f"[vram_guard] 启动前空闲显存 {fv} MiB（达标 {a.min_free}）", flush=True)
        elif fv >= a.floor:
            print(f"[vram_guard] 空闲 {fv} MiB，未达目标 {a.min_free} 但已过下限 {a.floor}（桌面基线所致），放行", flush=True)
        else:
            sys.exit(f"空闲显存仅 {fv} MiB（< 下限 {a.floor}），拒绝启动达芬奇，请手动清理后重试")
    else:
        print("[vram_guard] 显存充足且进程存活，无需重启", flush=True)

    # 4) 恢复现场（无论是否重启都执行，确保工程/时间线/页面正确）
    r = get_resolve(auto_launch=True)
    p = ensure_project(r.GetProjectManager(), a.project)
    tl = p.GetCurrentTimeline()
    if tl is None or tl.GetName() != a.timeline:
        for i in range(1, (p.GetTimelineCount() or 0) + 1):
            t = p.GetTimelineByIndex(i)
            if t.GetName() == a.timeline:
                p.SetCurrentTimeline(t)
                break
    time.sleep(1)
    r.OpenPage("color")
    time.sleep(2)
    mouse_ctl.activate()
    print(f"[vram_guard] 重启完成，工程 {a.project} / 时间线 {a.timeline} / 调色页就绪，显存 {vram_used()} MiB", flush=True)


if __name__ == "__main__":
    main()
