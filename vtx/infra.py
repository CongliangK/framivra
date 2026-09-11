"""vtx 基础设施层：Ctx 上下文、ffmpeg 执行、并行任务调度。

由 stages/composite/qc/encoders 共用。入口 vtx/vtx.py 保持可直接
`python3 vtx/vtx.py ...` 运行（同目录平铺 import，sys.path 由入口脚本保证）。
"""
import hashlib, json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def is_ffmpeg_cmd(cmd) -> bool:
    """dry-run 判断收拢（原两处 str(cmd[0]).endswith("ffmpeg") 重复）。"""
    return str(cmd[0]).endswith("ffmpeg")


class Ctx:
    def __init__(self, timeline_path: Path):
        self.tl_path = timeline_path
        self.root = timeline_path.parent
        self.tl = json.loads(timeline_path.read_text())
        self.fps = self.tl["project"]["fps"]
        self.dur = self.tl["project"]["duration_frames"]
        self.w = self.tl["project"]["width"]
        self.h = self.tl["project"]["height"]
        self.spf = 1.0 / self.fps
        self.spf_samples = 48000 // self.fps   # 30fps → 1600（24fps 时代为 2000），须整除
        self.cache = self.root / "cache"; self.out = self.root / "out"
        self.probes = {}
        self.dry = False   # --dry-run（main 里按 CLI 参数赋值；替代原模块级可变全局 DRY）

    # ---- 基础设施 ----
    def run(self, cmd, need=True):
        # 与 run_ffmpeg_task 的关系（两种失败语义，不可合一）：本方法面向编排主线程，
        # 失败打印 + sys.exit 立即终止；run_ffmpeg_task 面向并行 worker 线程——线程内
        # sys.exit 不生效，改为抛 RuntimeError 由主线程统一收集后退出。
        if self.dry and is_ffmpeg_cmd(cmd):
            print("[dry-run]", " ".join(map(str, cmd))[:300] + (" …" if len(cmd) > 20 else ""))
            return ""
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 and need:
            print("CMD FAILED:", " ".join(map(str, cmd))[:300], file=sys.stderr)
            print(r.stderr[-1500:], file=sys.stderr); sys.exit(1)
        return r

    def probe(self, path: Path):
        path = Path(path)
        if path in self.probes: return self.probes[path]
        # 不用 -count_frames（逐帧解码计数：voice-ime 实测 1080p 全源 12-27s/个，
        # validate+video 每次进程合计 ~2.5min 纯浪费）。mp4/mov 流头 nb_frames 即精确；
        # 流头缺失才回落 -count_packets（仅解封装，秒级）。
        r = self.run(["ffprobe", "-v", "error", "-print_format", "json",
                      "-show_streams", "-show_format", str(path)])
        d = json.loads(r.stdout)
        v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
        a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
        if v is not None:
            nb = str(v.get("nb_frames") or "")
            if not nb.isdigit() or int(nb) == 0:
                r2 = self.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                               "-count_packets", "-show_entries", "stream=nb_read_packets",
                               "-of", "csv=p=0", str(path)])
                nb = r2.stdout.strip() if r2 else ""
            info = {"fps": eval(v["r_frame_rate"]),
                    "nb_frames": int(nb) if nb.isdigit() else 0,
                    "w": int(v["width"]), "h": int(v["height"]),
                    "has_video": True, "has_audio": a is not None}
        else:  # 纯音频源（音效/BGM/环境声文件）：时长换算到项目帧网格，仅供音频事件引用
            dur = float(d["format"]["duration"])
            info = {"fps": None, "nb_frames": int(dur * self.fps),
                    "w": 0, "h": 0, "has_video": False, "has_audio": a is not None}
        self.probes[path] = info; return info

    def receipt(self, stage, ok, detail="", extra=None):
        f = self.out / "receipt.json"
        rec = json.loads(f.read_text()) if f.exists() else {"timeline": None, "stages": {}}
        rec["timeline"] = hashlib.sha256(self.tl_path.read_bytes()).hexdigest()
        rec["updated"] = time.strftime("%F %T")
        rec["stages"][stage] = {"ok": bool(ok), "detail": detail}
        if extra:
            rec["stages"][stage].update(extra)   # 结构化附加字段（如 audio 的响度数据），不改旧字段
        self.out.mkdir(exist_ok=True); f.write_text(json.dumps(rec, ensure_ascii=False, indent=2))

    def src_path(self, sid):
        return self.root / next(s for s in self.tl["sources"] if s["id"] == sid)["path"]

    # ---- 16:9 中心裁切（任意源尺寸通用，保持偶数） ----
    def crop169(self, sid):
        i = self.probe(self.src_path(sid))
        cw = i["w"] - i["w"] % 2
        ch = int(i["w"] * 9 / 16); ch -= ch % 2
        if ch > i["h"]: ch = i["h"] - i["h"] % 2; cw = int(i["h"] * 16 / 9); cw -= cw % 2
        return f"crop={cw}:{ch}:0:{(i['h'] - ch) // 2}"


# ---------------------------------------------------------------- 并行基础设施（阶段三 2026-09-10）
# seg 预编码与抽音频都是「任务间零依赖的 ffmpeg 子进程」，串行是纯浪费；线程池并行，
# 墙钟 ≈ max(单任务)。IO/CPU 混合负载，线程数取 min(6, cpu//4)（可 VTX_IO_WORKERS 覆盖）。
# 与 composite.render_segmented 的段间并行是两套各自独立的并行度，不可统一：
# io_workers 上限 6 面向 IO 混合任务（prepare 抽音频 / seg 预编码）；段间并行面向
# NVENC 编码会话，默认 N=8（VTX_VIDEO_SEGMENTS）需要 8 线程 > 本函数上限。
def io_workers() -> int:
    v = os.environ.get("VTX_IO_WORKERS", "")
    if v.isdigit() and int(v) > 0: return int(v)
    return max(1, min(6, (os.cpu_count() or 8) // 4))

def run_ffmpeg_task(c: Ctx, label: str, cmd):
    # 并行 worker 内不用 c.run：其失败路径 sys.exit 在线程中不生效，改为抛异常
    # 由主线程统一收集传播（任一任务失败 → 整阶段失败）。
    if c.dry and is_ffmpeg_cmd(cmd):
        print("[dry-run]", " ".join(map(str, cmd))[:300] + (" …" if len(cmd) > 20 else ""))
        return
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{label}: " + r.stderr[-1500:])

def run_parallel(c: Ctx, tasks, what: str):
    """tasks = [(label, cmd)]，全部执行；任一失败打印并退出。进度按完成序打印（带标签）。"""
    if not tasks: return
    workers = min(io_workers(), len(tasks))
    t0 = time.time()
    print(f"  {what}：{len(tasks)} 个任务，{workers} 线程并行")
    failed = None
    if workers == 1 or c.dry:      # 串行保底 / dry-run 只打印
        for label, cmd in tasks:
            try: run_ffmpeg_task(c, label, cmd)
            except RuntimeError as e: failed = failed or (label, str(e))
            print(f"    · {label}")
    else:
        with ThreadPoolExecutor(workers) as ex:
            futs = {ex.submit(run_ffmpeg_task, c, label, cmd): label for label, cmd in tasks}
            for fu in as_completed(futs):
                label = futs[fu]
                try:
                    fu.result(); print(f"    ✓ {label}（+{time.time() - t0:.0f}s）")
                except RuntimeError as e:
                    failed = failed or (label, str(e)); print(f"    ✗ {label} 失败")
    if failed:
        print(f"✗ {what}失败（{failed[0]}）：\n{failed[1]}", file=sys.stderr); sys.exit(1)
    print(f"  ✓ {what}完成（{time.time() - t0:.0f}s）")
