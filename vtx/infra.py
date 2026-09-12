"""vtx 基础设施层：Ctx 上下文、ffmpeg 执行、并行任务调度。

由 stages/composite/qc/encoders 共用。入口 vtx/vtx.py 保持可直接
`python3 vtx/vtx.py ...` 运行（同目录平铺 import，sys.path 由入口脚本保证）。
"""
import hashlib, json, os, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# PR#1 合入（2026-09-11）：回执侧溯源（依赖门/下游失效/输入身份绑定）。
# 缓存布局仍用 main 13bed7a 的 per-产物内容寻址键（hygiene.py）——见 cache_state.py 模块头合并裁定。
from cache_state import (DOWNSTREAM, load_receipt, receipt_fingerprint,
                         validate_cache_dependencies)


def is_ffmpeg_cmd(cmd) -> bool:
    """dry-run 判断收拢（原两处 str(cmd[0]).endswith("ffmpeg") 重复）。"""
    return str(cmd[0]).endswith("ffmpeg")


class Ctx:
    def __init__(self, timeline_path: Path):
        self.tl_path = timeline_path
        self.root = timeline_path.parent
        self.tl = json.loads(timeline_path.read_text(encoding="utf-8"))
        self.fps = self.tl["project"]["fps"]
        # fps 契约前置校验（2026-09-11）：spf_samples = 48000 // fps 须整除，否则音频
        # 事件 atrim 采样网格漂移——非正整数或不能整除 48000 直接 ValueError
        # （由 vtx.py main 的 except 捕获友好打印，NTSC 29.97 等非整数帧率不支持）。
        if not (isinstance(self.fps, int) and not isinstance(self.fps, bool)
                and self.fps > 0 and 48000 % self.fps == 0):
            raise ValueError(
                f"project.fps={self.fps!r} 无效：fps 必须为能整除 48000 的正整数"
                "（如 24/30/60；NTSC 29.97 等非整数帧率不支持）")
        self.dur = self.tl["project"]["duration_frames"]
        self.w = self.tl["project"]["width"]
        self.h = self.tl["project"]["height"]
        self.spf = 1.0 / self.fps
        self.spf_samples = 48000 // self.fps   # 30fps → 1600（24fps 时代为 2000），须整除
        self.cache = self.root / "cache"; self.out = self.root / "out"
        # PR#1 合入：声明依赖快速失败 + 回执输入身份（注意：不是缓存目录键——
        # 缓存布局维持 main 的 per-产物内容寻址，见 cache_state.py 合并裁定）。
        validate_cache_dependencies(self)
        self.input_fingerprint = receipt_fingerprint(self)
        self.probes = {}
        self.dry = False   # --dry-run（main 里按 CLI 参数赋值；替代原模块级可变全局 DRY）
        # receipt.json 并发写锁（方案二阶段2，2026-09-12）：`vtx all` 依赖图流水线的
        # 并行波内 gfx/audio 两线程几乎同时 c.receipt() / c.begin_stage()——两者都是
        # receipt.json 的「读-改-写」，无锁竞态会互相覆盖丢回执（真丢数据风险，非
        # 理论洁癖）。实例锁（每 Ctx 一把足够：receipt.json 每 Ctx 恰一份）；
        # dry 早退路径在锁外（见各方法）。
        self._receipt_lock = threading.Lock()

    # ---- 基础设施 ----
    def run(self, cmd, need=True):
        # 与 run_ffmpeg_task 的关系（两种失败语义，不可合一）：本方法面向编排主线程，
        # 失败打印 + sys.exit 立即终止；run_ffmpeg_task 面向并行 worker 线程——线程内
        # sys.exit 不生效，改为抛 RuntimeError 由主线程统一收集后退出。
        if self.dry and is_ffmpeg_cmd(cmd):
            print("[dry-run]", " ".join(map(str, cmd))[:300] + (" …" if len(cmd) > 20 else ""))
            return ""
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
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
            # r_frame_rate 安全解析（原 eval：异常值会裸抛 SyntaxError/ZeroDivisionError，
            # 且 eval 本身是任意表达式执行面）。正常形如 "30/1"；分母非正/解析失败 →
            # 回落 nb_frames ÷ format.duration 路径，仍不可得才友好报错退出。
            fps = None
            num, _, den = str(v.get("r_frame_rate") or "").partition("/")
            try:
                den_i = int(den.strip() or "1")
                if den_i > 0:
                    fps = int(num.strip()) / den_i
            except (ValueError, ZeroDivisionError):
                fps = None
            if fps is None:
                try:
                    dur_s = float(d.get("format", {}).get("duration") or 0)
                except (TypeError, ValueError):
                    dur_s = 0.0
                if dur_s > 0 and nb.isdigit() and int(nb) > 0:
                    fps = int(nb) / dur_s
                else:
                    print(f"✗ ffprobe 无法解析 {path.name} 的帧率"
                          f"（r_frame_rate={v.get('r_frame_rate')!r}，duration/帧数回落亦不可得）",
                          file=sys.stderr)
                    sys.exit(1)
            info = {"fps": fps,
                    "nb_frames": int(nb) if nb.isdigit() else 0,
                    "w": int(v["width"]), "h": int(v["height"]),
                    "has_video": True, "has_audio": a is not None}
        else:  # 纯音频源（音效/BGM/环境声文件）：时长换算到项目帧网格，仅供音频事件引用
            dur_raw = d.get("format", {}).get("duration")
            if not dur_raw:
                print(f"✗ ffprobe 未返回纯音频源 {path.name} 的 format.duration，"
                      "无法换算到项目帧网格", file=sys.stderr)
                sys.exit(1)
            dur = float(dur_raw)
            info = {"fps": None, "nb_frames": int(dur * self.fps),
                    "w": 0, "h": 0, "has_video": False, "has_audio": a is not None}
        self.probes[path] = info; return info

    def receipt(self, stage, ok, detail="", extra=None):
        if self.dry:
            return   # dry-run 不落回执（PR#1：预览运行不得改写真实阶段状态）——锁外早退
        # 读-改-写整段持锁（方案二阶段2）：波内并发阶段的回执写入串行化，防互相覆盖
        with self._receipt_lock:
            f = self.out / "receipt.json"
            rec = load_receipt(f, self.input_fingerprint)
            rec["timeline"] = hashlib.sha256(self.tl_path.read_bytes()).hexdigest()
            rec["updated"] = time.strftime("%F %T")
            rec["stages"][stage] = {"ok": bool(ok), "detail": detail}
            if extra:
                rec["stages"][stage].update(extra)   # 结构化附加字段（如 audio 的响度数据），不改旧字段
            self.out.mkdir(exist_ok=True); f.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

    # （PR#1 的 Ctx.gfx_dir 路径哈希版被放弃：main 13bed7a 的 hygiene.gfx_key
    #  内容寻址键覆盖其全部场景——同名页不同内容/同页换时长自然分键，且同内容
    #  跨路径可复用；此处不保留第二实现。）

    def begin_stage(self, stage):
        """PR#1：拒绝过期前置 + 运行前撤销下游成功回执（失败重跑不留旧 QC PASS）。"""
        if self.dry:
            return
        # 读-改-写整段持锁（方案二阶段2）：与 receipt() 同一锁——波内 gfx/audio 两路
        # begin_stage 并发时依赖门判定与下游撤销也须串行化（dry 早退在锁外）。
        with self._receipt_lock:
            rec = load_receipt(self.out / 'receipt.json', self.input_fingerprint)
            dependencies = {'audio': ['prepare'], 'mux': ['video', 'audio'],
                            'mux_draft': ['video_draft', 'audio'], 'qc': ['mux']}
            if any(t.get('kind') == 'gfx' for t in self.tl['video_tracks']):
                dependencies['video'] = dependencies['video_draft'] = ['gfx']
            missing = [name for name in dependencies.get(stage, [])
                       if not rec['stages'].get(name, {}).get('ok')]
            if missing:
                raise RuntimeError('当前输入要求先成功完成阶段: ' + ', '.join(missing) +
                                   '（输入已变化，旧成功不继承；请按序重跑或直接 vtx all）')
            for name in DOWNSTREAM.get(stage, []) + [stage]:
                rec['stages'].pop(name, None)
            self.out.mkdir(exist_ok=True)
            (self.out / 'receipt.json').write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding='utf-8')

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

# -filter_complex_threads 的线程数（2026-09-12 滤镜多线程解锁，方案二阶段1）。
# v3.6 时代（2026-09-08）强制 1 的依据——「多线程 filtergraph 下 framesync 窗口
# 边界密集带调度竞态致主链丢帧」——已被 61 层 ±20 帧密集端点复现图证伪
# （2026-09-11/12 两轮：threads=1 与 4 输出 MD5 逐位一致，4 快 1.9×，实验收编于
# tests/test_filter_threads.py）。但 bench_p2 全链 A/B（2026-09-12 阶段1 验收）后
# 默认仍保持 1，两条实测依据：①真实项目（voice-ime 16746f/69 层）前 3 段——恰为
# 输入路数最多的 15/11/13 路——存在与线程数无关的跨运行输出非确定性（同 argv
# 隔离复渲 4 次 4 个 md5；seg03-07 五段 t1/t4/基线逐字节全等），逐字节等价门不可
# 复现，放行证据不足；②8 段并行 + x264 自带多线程下 threads=4 无墙钟收益
# （99s vs 98/99s；隔离单段仅 1.35×）——解锁收益在单进程大 graph 场景，分段
# 并行管线吃不到。安全网不变：输出端 -fps_mode cfr 补帧 + 段拼接后总帧数硬自检。
# VTX_FILTER_THREADS=4 可显式开启（合成复现图 MD5 等价、全链 QC 75 项字段级
# 一致、差异段 PSNR 与同线程方差同分布，无新增风险）。
def filter_threads() -> int:
    v = os.environ.get("VTX_FILTER_THREADS", "1")
    # 采纳值封顶 cpu 核数：滤镜线程超核数无收益只增调度开销（对齐 io_workers 封顶纪律）
    if v.isdigit() and int(v) > 0:
        return min(int(v), max(1, os.cpu_count() or 8))
    return 1

def run_ffmpeg_task(c: Ctx, label: str, cmd):
    # 并行 worker 内不用 c.run：其失败路径 sys.exit 在线程中不生效，改为抛异常
    # 由主线程统一收集传播（任一任务失败 → 整阶段失败）。
    if c.dry and is_ffmpeg_cmd(cmd):
        print("[dry-run]", " ".join(map(str, cmd))[:300] + (" …" if len(cmd) > 20 else ""))
        return
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
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


def run_wave(c: Ctx, jobs):
    """jobs = [(label, fn)] 的 Python 可调用并行波执行器（方案二阶段2，2026-09-12，
    `vtx all` 依赖图流水线）：gfx / audio / video 预编码三路零数据依赖任务重叠执行。
    与 run_parallel 的分工：后者面向「ffmpeg 子进程任务」（label+cmd，io_workers 限流），
    本函数面向「整段 Python 阶段函数」——每路一个线程（len(jobs) 即线程数，波内三路
    各自内部还有自己的并行度：gfx 页池 / run_parallel / 段间并行，CPU 聚合即设计目标）。

    失败语义：每个 fn 包 try/except BaseException——阶段函数内部失败走 sys.exit，
    在线程里 SystemExit 只杀线程不杀进程，必须显式捕获记入失败表；任一 job 失败后
    其余 job 仍跑完再统一退出（取消另一半波内 Chrome/ffmpeg 进程树的复杂度不值，
    失败路径罕见），join 后打印首个失败标签与异常并以退出码 1 结束（不静默）。

    打点（对齐 range.py [timing] 惯例）：波开始一行（含墙钟时刻）；每 job 线程体开头
    `== {label} ==`（与线性序列阶段头同格式，外部队列按行 grep 兼容）、结束
    `✓ {label} 完成 [timing] +{t:.1f}s（结束时刻）`；波结束打印总耗时。多线程 print
    按行交错（行缓冲），可接受——方案明文「日志交错但分项计时保留可归因」。
    c 参数与 run_parallel 签名对称（当前无消费，预留 dry/上下文扩展位）。
    """
    if not jobs:
        return
    t0 = time.monotonic()
    print(f"── 并行波（{len(jobs)} 路：{' + '.join(l for l, _ in jobs)}）"
          f"开始 {time.strftime('%H:%M:%S')} ──")
    failed = []   # [(label, exc)]；list.append 在 GIL 下原子，无需额外锁

    def _worker(label, fn):
        t1 = time.monotonic()
        print(f"== {label} ==")
        try:
            fn()
        except BaseException as e:   # 含 SystemExit：sys.exit 在线程内只杀线程，必须捕获
            failed.append((label, e))
            if isinstance(e, SystemExit):
                # 真实错误文案已由阶段函数自行打印，这里只归因到波内标签
                print(f"✗ {label} 失败（exit {e.code}，错误详情见上方该路输出）")
            else:
                print(f"✗ {label} 失败：{e}")
            return
        print(f"✓ {label} 完成 [timing] +{time.monotonic() - t1:.1f}s"
              f"（{time.strftime('%H:%M:%S')}）")

    with ThreadPoolExecutor(len(jobs)) as ex:
        list(ex.map(lambda j: _worker(*j), jobs))
    if failed:
        label, e = failed[0]
        if not isinstance(e, SystemExit):
            print(f"✗ 并行波失败（首个失败：{label}）：{e!r}", file=sys.stderr)
        else:
            print(f"✗ 并行波失败（首个失败：{label}，exit {e.code}）", file=sys.stderr)
        sys.exit(1)
    print(f"✓ 并行波完成 [timing] 总耗时 {time.monotonic() - t0:.1f}s"
          f"（{time.strftime('%H:%M:%S')}）")


def filter_filename(path):
    """Escape an absolute filename through FFmpeg option and filtergraph parsers.

    This value is passed in an argv element, not through a shell.
    """
    value = Path(path).resolve().as_posix()
    for chars in ("\\':", "\\'[],;"):
        value = ''.join('\\' + ch if ch in chars else ch for ch in value)
    return value
