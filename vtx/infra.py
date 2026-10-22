"""vtx 基础设施层：Ctx 上下文、ffmpeg 执行、并行任务调度。

由 stages/composite/qc/encoders 共用。v2 §1.4 包化：模块内相对导入；
`python3 vtx/vtx.py ...` 经薄启动器自锚定 repo 根后照常可跑。
"""
import hashlib, json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import ContextVar
from fractions import Fraction
from pathlib import Path

# PR#1 合入（2026-09-11）：回执侧溯源（依赖门/下游失效/输入身份绑定）。
# 缓存布局仍用 main 13bed7a 的 per-产物内容寻址键（hygiene.py）——见 cache_state.py 模块头合并裁定。
from .runtime import fstxn, events
from .cache_state import (DOWNSTREAM, InputIdentityError, load_receipt,
                         receipt_fingerprint, validate_cache_dependencies,
                         verify_input_identity)
from .constraints import AUDIO_SAMPLE_RATE, fps_error
from .runtime.diagnostics import emit, required_field, mark_handled, producer_exit, operation, indexed
from .media.encoders import (NVENC_RETRY_ATTEMPTS, nvenc_budget, nvenc_guard_note,
                       nvenc_retry_run)


_probe_json_path = ContextVar("vtx_probe_json_path", default="$")


def _audio_duration_from_ticks(stream):
    """Read exact nonnegative stream time, or leave legacy duration fallback intact."""
    ticks = stream.get('duration_ts')
    time_base = stream.get('time_base')
    if (isinstance(ticks, bool) or not isinstance(ticks, (int, str))
            or not isinstance(time_base, str)):
        return None
    parts = [str(ticks).strip(), *time_base.split('/')]
    if len(parts) != 3:
        return None
    parts = [part.strip() for part in parts]
    if not all(part.isascii() and part.isdecimal() for part in parts):
        return None
    try:
        count, numerator, denominator = map(int, parts)
        if numerator > 0 and denominator > 0:
            return count * Fraction(numerator, denominator)
    except ValueError:
        pass  # Unusable metadata is recoverable, not a terminal diagnostic.
    return None


def is_ffmpeg_cmd(cmd) -> bool:
    """Shared executable identity for dry execution and media publication."""
    return bool(cmd) and Path(str(cmd[0])).name.lower() in ('ffmpeg', 'ffmpeg.exe')


# ---------------------------------------------------------------- 写路径安全门（issue #23 / A04）
class ProjectPathError(ValueError):
    """issue #23（A04 / P0 安全止损）：写路径 resolve 后落在白名单根之外。

    ValueError 子类——cli 的 timeline 无效捕获链与 tests/safety 既有对抗用例的
    ``assertRaises((ValueError, OSError))`` 都按 ValueError 对待，无须新增捕获面。
    """


def ensure_inside_project(path, root, *, what: str = "写路径") -> Path:
    """issue #23（A04）统一项目路径校验入口：**写/输出**路径的单一安全门。

    语义（工单原文）：resolve（跟随符号链接、容忍末段缺失）后必须落在 root
    （同样 resolve）之内——root 本身或其子孙。三种逃逸载体在此一并拦截：
      - ``../`` 穿越（含符号链接间接放大深度的场景）；
      - 绝对路径（pathlib 拼接 ``dst / "/abs"`` 会整体替换左侧前缀）；
      - 符号链接指向根外（含断链——非 strict resolve 仍跟随链接目标）。
    符号链接指向根内 = 允许（A03 波 manifest 收口同款口径，不误杀正常软链接
    用法）。拒绝时报 ProjectPathError，文案含路径与去向（解析落点 + 白名单根）。

    只护写侧：「用户显式引用的外部素材」的**读取**不受此限（timeline 声明的
    源文件可以在项目外，validate/gc 等读侧不经过本门）。返回 resolve 后的绝对
    路径，调用方可继续用它落笔（同一落点，不再经过原符号链接）。

    与 A03 波清理侧收口（hygiene.cleanup_allowed / cli._symlink_roots）的分工：
    那里管「删除不得穿链接」，本门管「写入不得逃根」——同一白名单根思想、
    方向相反的两面，共用 resolve 判据。
    """
    try:
        if '\x00' in str(path) or '\x00' in str(root):
            raise ValueError('NUL 字符')
        root_real = Path(root).resolve()
        target = Path(path).resolve()
    except (OSError, RuntimeError, ValueError) as error:
        raise ProjectPathError(f"{what}非法路径：{path!r}（{error}）") from error
    if target != root_real and root_real not in target.parents:
        raise ProjectPathError(
            f"{what}逃逸安全根（issue #23）：{path} 解析为 {target}，"
            f"落在 {root_real} 之外（../ 穿越、绝对路径或符号链接指向根外）——"
            "系统写路径必须限制在根内；素材源读取不受此门限制")
    return target


def _archive_stale_receipt(f: Path, fingerprint: str, *, root):
    """⑮ 最小修复（2026-09-14 登记+修复，KNOWN-ISSUES ⑮）：回执输入指纹即将
    翻转时（timeline/源/插件代码变更，或 validate-only 等单阶段运行在新指纹下
    落笔），旧 receipt.json 的全量阶段记录会被 load_receipt 归零覆盖（voice-ime
    v4.6 回执 9/14 被 13:28 validate 覆盖丢失的实案）。修复形态取「翻转前轮转
    单槽旁证」：旧记录原样存 out/receipt.prev.json——receipt.json 的语义/安全
    不变量（旧成功不继承）零变化，gc/VERSIONS 消费者不受影响。指纹一致/旧件
    无阶段记录/读写失败均静默直过（旁证尽力而为，不进主链失败路径）。"""
    try:
        old = json.loads(f.read_text(encoding="utf-8"))
        if (isinstance(old, dict)
                and old.get("input_fingerprint") not in (None, fingerprint)
                and old.get("stages")):
            # B06：旁证轮转同样走 fstxn 原子写（tmp+os.replace）——调用方已持
            # receipt.json 事务锁，prev 伴生件无须再拿第二把锁文件；写一半被杀
            # 不再留下截断的 prev.json。
            fstxn.atomic_write(f.parent / "receipt.prev.json",
                               json.dumps(old, ensure_ascii=False, indent=2), root=root)
    except (OSError, ValueError, TypeError):
        pass


class Ctx:
    def __init__(self, timeline_path: Path, *, execution=None, diagnostics=None):
        self.execution = execution if execution is not None else events.ExecutionContext()
        if diagnostics is not None:
            self.diagnostics = diagnostics
        self.tl_path = timeline_path
        self.root = timeline_path.parent
        try:
            self.timeline_bytes = timeline_path.read_bytes()
        except OSError as error:
            emit(self, str(error), rule_id='load.timeline_file', measured={'path': str(timeline_path)},
                 threshold={'readable_file': True})
            mark_handled(error)
            raise
        self.timeline_sha256 = hashlib.sha256(self.timeline_bytes).hexdigest()
        self.timeline_md5 = hashlib.md5(self.timeline_bytes).hexdigest()
        try:
            self.tl = json.loads(self.timeline_bytes.decode("utf-8"))
        except (ValueError, UnicodeError) as error:
            emit(self, str(error), rule_id='load.timeline_json', measured={'line': getattr(error, 'lineno', None),
                 'column': getattr(error, 'colno', None)}, threshold={'encoding': 'UTF-8', 'syntax': 'JSON'})
            mark_handled(error)
            raise
        def field(obj, key, parent):
            return required_field(self, obj, key, parent)
        project = field(self.tl, 'project', '$')
        self.fps = field(project, 'fps', '$.project')
        # fps 契约前置校验（B02/issue #26 / F-04 / #4 三面统一，2026-10-13）：
        # 允许值单一来源 constraints.ALLOWED_FPS——D24 换基后全管线固定 30fps
        # （≠29.97；24/25/50/60 及 NTSC 非整数帧率一律拒绝），schema/preview 同源
        # 判定，tests/media/test_fps_constraints.py 守护三面一致。旧门收「可整除 48000 的
        # 任意正整数」（24/25/50/60 全放行）与 schema const 30 漂移，即本单标的。
        # 数值等值的 30.0 归一为 int 30（JSON Schema const 数学等值语义，三面同判）。
        _fps_err = fps_error(self.fps)
        if _fps_err:
            emit(self, _fps_err, json_path='$.project.fps', rule_id='load.project_fps',
                 measured={'value': self.fps, 'unit': 'frames/s'}, threshold={'value': 30, 'unit': 'frames/s'},
                 suggested_actions=[{'op': 'normalize_project_fps', 'params': {'fps': 30}}])
            error = ValueError(_fps_err)
            mark_handled(error)
            raise error
        self.fps = int(self.fps)
        self.dur = field(project, 'duration_frames', '$.project')
        self.w = field(project, 'width', '$.project')
        self.h = field(project, 'height', '$.project')
        self.spf = 1.0 / self.fps
        self.spf_samples = AUDIO_SAMPLE_RATE // self.fps   # 30fps → 1600（24fps 时代为 2000），须整除
        self.cache = self.root / "cache"; self.out = self.root / "out"
        # v2 subtitles_src（§1.1 条目 6）：timeline 加载点解析——load 后 self.tl
        # ["subtitles"] 即为解析结果（各阶段无感）；解析失败/互斥错误记入
        # self.subtitles_errors 由 validate FAIL。subtitles_src 原文已参与输入
        # 指纹；解析前锁定、解析后复核，避免内存字幕和身份来自不同版本。
        # Pin before resolving external subtitles so changes during loading are
        # caught too. Hash exactly the bytes parsed above, not a second read.
        validate_cache_dependencies(self)
        from .render.gfx_backend import selected, identity
        from .hygiene import gfx_cache_ns
        from .render import gfx_svg
        if gfx_svg.route(self) == 'svg-sequence':
            self.gfx_backend = {'requested_backend': 'svg-sequence',
                                'identity': gfx_svg.engine_identity(),
                                'namespace': gfx_svg.NAMESPACE, 'experimental': True}
        else:
            self.gfx_backend = {"requested_backend": selected(), "identity": identity(),
                                "namespace": gfx_cache_ns()} if any(
                t.get("kind") == "gfx" for t in self.tl.get("video_tracks", [])) else None
        self.input_fingerprint = receipt_fingerprint(
            self, timeline_bytes=self.timeline_bytes)
        if self.gfx_backend is not None and getattr(self, '_gfx_environment', None):
            from .render.gfx_environment import identity as environment_identity
            self.gfx_backend['environment_identity'] = environment_identity(self)
            self.gfx_backend['environment_evidence'] = (
                self._gfx_environment[1]['status'] + '; pixel equivalence unproven')
        from .media.subtitles_src import resolve_subtitles_src
        resolve_subtitles_src(self)
        verify_input_identity(self)
        # PR#1 合入：声明依赖快速失败 + 回执输入身份（注意：不是缓存目录键——
        # 缓存布局维持 main 的 per-产物内容寻址，见 cache_state.py 合并裁定）。
        self.probes = {}
        self.dry = False   # --dry-run（main 里按 CLI 参数赋值；替代原模块级可变全局 DRY）
        # receipt.json 并发写锁（方案二阶段2，2026-09-12；B06 升级 2026-10-13）：
        # `vtx all` 依赖图流水线的并行波内 gfx/audio 两线程几乎同时
        # c.receipt() / c.begin_stage()——两者都是 receipt.json 的「读-改-写」，
        # 无锁竞态会互相覆盖丢回执（真丢数据风险，非理论洁癖）。锁本体已下沉
        # fstxn（B05）：进程内 per-path threading.Lock（快速层）之上再加
        # out/receipt.json.lock 的 flock（跨进程——另一 vtx 进程的 validate/qc
        # 与本进程并行落笔同样串行化，issue #29/#30 / F-08）；落盘一律
        # tmp+os.replace（中断不再产生截断 JSON）。原实例锁 _receipt_lock 随
        # 下沉移除——fstxn 的 per-path 锁覆盖同进程全部 Ctx 实例，序列化面只增不减。
        # dry 早退路径在锁外（见各方法）。
        #
        # 方案二阶段3：波内预编码路写入的 layers（依赖图流水线专用，NIT 显式声明
        # 替代动态属性——getattr 兜底在波失败路径实际不可达，见 p2 审查报告）。
        self._pre_layers = None

    # ---- 基础设施 ----
    def run(self, cmd, need=True, *, before_commit=None, after_commit=None):
        """Execute a command; commit callbacks are for owned, need=True media only.

        Dry execution invokes neither callback. The recovery transaction owns
        callback ordering and the final target lock, not this command wrapper.
        """
        # 与 run_ffmpeg_task 的关系（两种失败语义，不可合一）：本方法面向编排主线程，
        # 失败打印 + sys.exit 立即终止；run_ffmpeg_task 面向并行 worker 线程——线程内
        # sys.exit 不生效，改为抛 RuntimeError 由主线程统一收集后退出。
        if self.dry and is_ffmpeg_cmd(cmd):
            print("[dry-run]", " ".join(map(str, cmd))[:300] + (" …" if len(cmd) > 20 else ""))
            return ""
        from .recovery import atomic_ffmpeg
        observation = events.task(self, kind="process")
        def execute(argv):
            return observation.attempt(subprocess.run, argv, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace")
        try:
            with observation.scope():
                r = (atomic_ffmpeg(self, cmd, execute, before_commit=before_commit,
                                   after_commit=after_commit) if need else execute(cmd))
                if r.returncode:
                    observation.finish(r.returncode, RuntimeError())
        except RuntimeError as error:
            emit(self, str(error), json_path=_probe_json_path.get(),
                 rule_id='runtime.command', measured={'exception_type': type(error).__name__},
                 threshold={'exit_code': 0}, status='unknown')
            print(f"CMD FAILED: {error}", file=sys.stderr)
            producer_exit(1)
        if r.returncode != 0 and need:
            print("CMD FAILED:", (command_display := " ".join(map(str, cmd))[:300]), file=sys.stderr)
            print((error_display := r.stderr[-1500:]), file=sys.stderr)
            emit(self, error_display, json_path=_probe_json_path.get(),
                 rule_id='runtime.command', measured={'command_display': command_display, 'exit_code': r.returncode},
                 threshold={'exit_code': 0}, status='unknown')
            producer_exit(1)
        return r

    def probe(self, path: Path):
        path = Path(path)
        # No caller-provided timeline origin exists here. The provider/resource
        # is authoritative; even a warm cache hit must not scan source inputs.
        return self._probe(path)

    def _probe(self, path: Path):
        def metadata(fn, pointer, description, **options):
            return operation(self, fn, path=_probe_json_path.get(),
                             rule='probe.metadata_operation', status='unknown',
                             operands={'operation': description},
                             source={'provider': 'ffprobe', 'resource': str(path),
                                     'provider_path': pointer}, **options)
        if path in self.probes: return self.probes[path]
        # 不用 -count_frames（逐帧解码计数：voice-ime 实测 1080p 全源 12-27s/个，
        # validate+video 每次进程合计 ~2.5min 纯浪费）。mp4/mov 流头 nb_frames 即精确；
        # 流头缺失才回落 -count_packets（仅解封装，秒级）。
        r = self.run(["ffprobe", "-v", "error", "-print_format", "json",
                      "-show_streams", "-show_format", str(path)])
        d = metadata(lambda: json.loads(r.stdout), '$', 'decode ffprobe JSON')
        v = metadata(lambda: next((s for s in d['streams'] if s['codec_type'] == 'video'), None), '$.streams', 'select video stream')
        a = metadata(lambda: next((s for s in d['streams'] if s['codec_type'] == 'audio'), None), '$.streams', 'select audio stream')
        if v is not None:
            nb_pointer = '$.streams[video].nb_frames'
            nb = metadata(lambda: str(v.get("nb_frames") or ""),
                          '$.streams[video].nb_frames', 'read frame count text')
            if not nb.isdigit() or metadata(lambda: int(nb), nb_pointer, "video frame count integer") == 0:
                r2 = self.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                               "-count_packets", "-show_entries", "stream=nb_read_packets",
                               "-of", "csv=p=0", str(path)])
                nb_pointer = '$.streams[video].nb_read_packets'
                nb = metadata(lambda: r2.stdout.strip() if r2 else "",
                              '$.streams[video].nb_read_packets', 'read packet count text')
            # r_frame_rate 安全解析（原 eval：异常值会裸抛 SyntaxError/ZeroDivisionError，
            # 且 eval 本身是任意表达式执行面）。正常形如 "30/1"；分母非正/解析失败 →
            # 回落 nb_frames ÷ format.duration 路径，仍不可得才友好报错退出。
            rate_raw = None
            def parse_rate():
                nonlocal rate_raw
                num, _, den = str((rate_raw := v.get("r_frame_rate")) or "").partition("/")
                try:
                    den_i = int(den.strip() or "1")
                    if den_i > 0:
                        return int(num.strip()) / den_i
                except (ValueError, ZeroDivisionError):
                    pass  # Native recoverable fallback, never a failure record.
                return None
            fps = metadata(parse_rate, '$.streams[video].r_frame_rate',
                           'parse rational frame rate')
            if fps is None:
                duration_raw = None
                def parse_duration():
                    nonlocal duration_raw
                    try:
                        return float((duration_raw := d.get("format", {}).get("duration")) or 0)
                    except (TypeError, ValueError):
                        return 0.0  # Keep the original recovered-duration fallback.
                dur_s = metadata(parse_duration, '$.format.duration',
                                 'parse fallback duration')
                if dur_s > 0 and nb.isdigit() and metadata(lambda: int(nb), nb_pointer, "video frame count integer") > 0:
                    fps = metadata(lambda: int(nb) / dur_s,
                                   [nb_pointer, '$.format.duration'],
                                   'frame count divided by fallback duration')
                else:
                    emit(self, 'ffprobe 无法解析帧率', json_path=_probe_json_path.get(),
                         rule_id='probe.fps', measured={'r_frame_rate': rate_raw, 'duration': duration_raw, 'frames': nb},
                         threshold={'parseable_fps': True}, status='unknown',
                         source={'provider': 'ffprobe', 'resource': str(path),
                                 'provider_path': ['$.streams[video].r_frame_rate', nb_pointer, '$.format.duration']})
                    print(f"✗ ffprobe 无法解析 {path.name} 的帧率"
                          f"（r_frame_rate={v.get('r_frame_rate')!r}，duration/帧数回落亦不可得）",
                          file=sys.stderr)
                    producer_exit(1)
            info = {"fps": fps,
                    "nb_frames": metadata(lambda: int(nb), nb_pointer, "video frame count integer") if nb.isdigit() else 0,
                    "w": metadata(lambda: int(v['width']), '$.streams[video].width', 'video width integer'),
                    "h": metadata(lambda: int(v['height']), '$.streams[video].height', 'video height integer'),
                    "has_video": True, "has_audio": a is not None}
        else:  # 纯音频源（音效/BGM/环境声文件）：时长换算到项目帧网格，仅供音频事件引用
            dur = None
            duration_pointer = ['$.streams[0].duration_ts', '$.streams[0].time_base']
            # ffprobe rounds format.duration to decimal microseconds; a 54400-sample
            # WAV at 48 kHz is exactly 34 project frames, not int(1.133333 * 30)=33.
            # Only a single audio stream can replace container duration without
            # changing the existing multi-stream/container selection semantics.
            if a is not None and len(d['streams']) == 1:
                dur = metadata(lambda: _audio_duration_from_ticks(a), duration_pointer,
                               'parse exact audio stream duration')
            if dur is None:
                duration_pointer = '$.format.duration'
                dur_raw = metadata(lambda: d.get('format', {}).get('duration'), duration_pointer, 'read audio duration')
                if not dur_raw:
                    emit(self, 'ffprobe 未返回纯音频源 format.duration', json_path=_probe_json_path.get(),
                         rule_id='probe.audio_duration', measured=None, threshold={'unit': 's', 'present': True}, status='unknown',
                         source={'provider': 'ffprobe', 'resource': str(path), 'provider_path': '$.format.duration'})
                    print(f"✗ ffprobe 未返回纯音频源 {path.name} 的 format.duration，"
                          "无法换算到项目帧网格", file=sys.stderr)
                    producer_exit(1)
                dur = metadata(lambda: float(dur_raw), duration_pointer, 'audio duration float')
            info = {"fps": None, "nb_frames": metadata(lambda: int(dur * self.fps), duration_pointer, 'audio duration to frames'),
                    "w": 0, "h": 0, "has_video": False, "has_audio": a is not None}
        self.probes[path] = info; return info

    def reject_input_identity(self, stage, error):
        """Explicit failure under the pinned identity, never under new inputs."""
        if stage is not None:  # preview tasks must not touch the formal receipt
            self.receipt(stage, False, str(error))
        print(f"✗ {error}", file=sys.stderr)
        failure = SystemExit(1)
        if getattr(error, '_vtx_diagnostic_handled', False):
            mark_handled(failure)
        raise failure

    def verify_inputs(self, stage):
        if self.dry:
            return
        try:
            verify_input_identity(self)
        except InputIdentityError as e:
            self.reject_input_identity(stage, e)

    def receipt(self, stage, ok, detail="", extra=None):
        if self.dry:
            return   # dry-run 不落回执（PR#1：预览运行不得改写真实阶段状态）——锁外早退
        f = self.out / "receipt.json"

        observation = None

        def _mutate(rec):
            _archive_stale_receipt(f, self.input_fingerprint, root=self.root)   # ⑮ 指纹翻转旁证轮转
            if ok:
                verify_input_identity(self)
            rec["timeline"] = self.timeline_sha256
            rec["updated"] = time.strftime("%F %T")
            rec["stages"][stage] = {"ok": bool(ok), "detail": detail}
            if observation is not None:
                rec["stages"][stage]["observed_by"] = observation
            if self.gfx_backend is not None:
                rec["gfx_backend"] = self.gfx_backend
                rec["stages"][stage]["gfx_backend"] = self.gfx_backend
            if extra:
                rec["stages"][stage].update(extra)   # 结构化附加字段（如 audio 的响度数据），不改旧字段
            from .runtime.quality import receipt_quality
            rec["quality"] = receipt_quality(rec)
            return rec

        # B06：读-改-写整段走 fstxn 事务（原方案二阶段2 的实例锁已下沉——见
        # __init__ 注）：线程锁之上再加 receipt.json.lock 的 flock，波内并发阶段
        # 之外另一 vtx 进程的并行落笔也串行化；写入 tmp+os.replace，中断/被杀后
        # receipt.json 要么旧值要么新值，永不为截断 JSON。load 注入指纹门
        # （load_receipt 语义原样：身份不符/损坏 → 空阶段表，不继承旧成功）。
        def write(ref):
            nonlocal observation
            observation = ref
            return fstxn.locked_update(f, _mutate,
                                      load=lambda p: load_receipt(p, self.input_fingerprint), root=self.root)

        try:
            self.execution.receipt(stage, ok, write)
        except InputIdentityError as e:
            # Release the ledger lock before writing failure (no nested lock).
            self.reject_input_identity(stage, e)

    # （PR#1 的 Ctx.gfx_dir 路径哈希版被放弃：main 13bed7a 的 hygiene.gfx_key
    #  内容寻址键覆盖其全部场景——同名页不同内容/同页换时长自然分键，且同内容
    #  跨路径可复用；此处不保留第二实现。）

    def begin_stage(self, stage):
        """PR#1：拒绝过期前置 + 运行前撤销下游旧成功回执（失败重跑不留旧 QC PASS）。"""
        if self.dry:
            return
        f = self.out / 'receipt.json'

        def _mutate(rec):
            verify_input_identity(self)
            _archive_stale_receipt(f, self.input_fingerprint, root=self.root)
            dependencies = {'audio': ['prepare'], 'mux': ['video', 'audio'],
                            'mux_draft': ['video_draft', 'audio'], 'qc': ['mux']}
            if any(operation(self, lambda: t.get('kind') == 'gfx',
                             path=f'$.video_tracks[{ti}]', rule='load.operation',
                             operands={'operation': 'track kind for stage dependencies'})
                   for ti, t in indexed(self, required_field(self, self.tl, 'video_tracks', '$'), '$.video_tracks')):
                dependencies['video'] = dependencies['video_draft'] = ['gfx']
            missing = [name for name in dependencies.get(stage, [])
                       if not isinstance(rec['stages'].get(name), dict)
                       or rec['stages'][name].get('ok') is not True]
            if missing:
                error = RuntimeError('当前输入要求先成功完成阶段: ' + ', '.join(missing) +
                                     '（输入已变化，旧成功不继承；请按序重跑或直接 vtx all）')
                emit(self, str(error), rule_id='runtime.stage_prerequisite',
                     measured={'stage': stage}, threshold={'current_input_prerequisites': 'successful'},
                     suggested_actions=[{'op': 'run_pipeline', 'params': {'command': 'vtx all'}}])
                mark_handled(error)
                raise error
            for name in DOWNSTREAM.get(stage, []) + [stage]:
                rec['stages'].pop(name, None)
            from .runtime.quality import receipt_quality
            rec["quality"] = receipt_quality(rec)
            return rec

        # 与 receipt() 同一 fstxn 事务（原同持 _receipt_lock——锁下沉见 __init__ 注）：
        # 依赖门判定与下游撤销的读-改-写跨进程同样串行化；门失败（RuntimeError）
        # 时 tmp 清残件、receipt.json 保持旧值。dry 早退在锁外。
        fstxn.locked_update(f, _mutate,
                            load=lambda p: load_receipt(p, self.input_fingerprint), root=self.root)

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
# tests/media/test_filter_threads.py）。但 bench_p2 全链 A/B（2026-09-12 阶段1 验收）后
# 默认仍保持 1，两条实测依据：①真实项目（voice-ime 16746f/69 层）前 3 段——恰为
# 输入路数最多的 15/11/13 路——存在与线程数无关的跨运行输出非确定性（同 argv
# 隔离复渲 4 次 4 个 md5；seg03-07 五段 t1/t4/基线逐字节全等），逐字节等价门不可
# 复现，放行证据不足；②8 段并行 + x264 自带多线程下 threads=4 无墙钟收益
# （99s vs 98/99s；隔离单段仅 1.35×）——解锁收益在单进程大 graph 场景，分段
# 并行管线吃不到。安全网不变：输出端 -fps_mode cfr 补帧 + 段拼接后总帧数硬自检。
# VTX_FILTER_THREADS=4 可显式开启（合成复现图 MD5 等价、全链 QC 75 项字段级
# 一致、差异段 PSNR 与同线程方差同分布，无新增风险）。
# 更新（2026-09-14 R4-E1）：依据①的跨运行非确定性已归因为次输入 EOF 裁决依赖
# 帧到达顺序（非线程数问题），composite.py 次输入尾垫至段尾后同 argv 复渲逐字节
# 可复现（KNOWN-ISSUES ⑭ 核销），「逐字节等价门不可复现」不再是保持默认 1 的
# 理由；依据②的分段并行无收益结论不变，默认维持 1。
def filter_threads() -> int:
    v = os.environ.get("VTX_FILTER_THREADS", "1")
    # 采纳值封顶 cpu 核数：滤镜线程超核数无收益只增调度开销（对齐 io_workers 封顶纪律）
    if v.isdigit() and int(v) > 0:
        return min(int(v), max(1, os.cpu_count() or 8))
    return 1

def run_ffmpeg_task(c: Ctx, label: str, cmd, nvenc: bool = False, *, observation=None):
    from .recovery import atomic_ffmpeg
    observation = observation or events.task(c, kind='ffmpeg')
    with observation.scope():
        return atomic_ffmpeg(c, cmd, lambda argv: _run_ffmpeg_task(c, label, argv, nvenc))


def _run_ffmpeg_task(c: Ctx, label: str, cmd, nvenc: bool = False):
    # 并行 worker 内不用 c.run：其失败路径 sys.exit 在线程中不生效，改为抛异常
    # 由主线程统一收集传播（任一任务失败 → 整阶段失败）。
    if c.dry and is_ffmpeg_cmd(cmd):
        print("[dry-run]", " ".join(map(str, cmd))[:300] + (" …" if len(cmd) > 20 else ""))
        return
    if nvenc:
        # R-07h S5 重试武装（机制本体=encoders.nvenc_retry_run）：失败入重试队列，
        # 至多 N=2 次指数退避后原 argv 原样重发（cmd 列表对象原样重用——编码命令
        # 一字不动）；占槽纪律：每次尝试独立占槽（_attempt 内），退避等待期不持槽
        # （retry_clock 在占槽域之外）；仅失败件重试，成功件零重试零副作用；耗尽→
        # 现行 fail-fast 语义（RuntimeError → run_parallel failed 收集 → rc=1 零变化）。
        def _attempt(_no):
            with nvenc_budget(c):   # 每次尝试独立占槽
                observe = events.current(c)
                invoke = observe.attempt if observe else lambda fn, *a, **kw: fn(*a, **kw)
                return invoke(subprocess.run, cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        ok, attempts, r = nvenc_retry_run(_attempt, label)
        if not ok:
            nvenc_guard_note("exhausted")
            # 失败清单行（修 11 格式冻结：label+尝试次数+瞬时/环境型判别；stderr 留档）
            print(f"✗ [NVENC 重试清单] label={label} 尝试={attempts}/{NVENC_RETRY_ATTEMPTS}"
                  f" 判别=环境型（重试耗尽，非瞬时争用）", file=sys.stderr)
            raise RuntimeError(f"{label}: " + r.stderr[-1500:])
        if attempts > 1:
            nvenc_guard_note("transient")
            print(f"↻ [NVENC 重试清单] label={label} 尝试={attempts}/{NVENC_RETRY_ATTEMPTS}"
                  f" 判别=瞬时型（重试成功）")
        return
    # 非 nvenc 任务（方案二阶段3 起 x264/prores/抽音频不占 NVENC 预算槽——占槽域零
    # 触达，S5 零武装；语义与原 nvenc=False 分支逐字等价）。
    observe = events.current(c)
    invoke = observe.attempt if observe else lambda fn, *a, **kw: fn(*a, **kw)
    r = invoke(subprocess.run, cmd, capture_output=True, text=True,
               encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"{label}: " + r.stderr[-1500:])

def run_parallel(c: Ctx, tasks, what: str):
    """tasks = [(label, cmd)]（或 (label, cmd, nvenc)），全部执行；任一失败打印并退出。
    进度按完成序打印（带标签）。nvenc 标志见 run_ffmpeg_task（方案二阶段3）。"""
    if not tasks: return
    observations = [events.task(c, kind="ffmpeg") for _ in tasks]
    workers = min(io_workers(), len(tasks))
    t0 = time.time()
    print(f"  {what}：{len(tasks)} 个任务，{workers} 线程并行")
    failed = None
    if workers == 1 or c.dry:      # 串行保底 / dry-run 只打印
        for t, observation in zip(tasks, observations):
            label, cmd = t[0], t[1]
            try: run_ffmpeg_task(c, label, cmd, t[2] if len(t) > 2 else False,
                                 observation=observation)
            except RuntimeError as e: failed = failed or (label, str(e), e)
            print(f"    · {label}")
    else:
        with ThreadPoolExecutor(workers) as ex:
            futs = {ex.submit(run_ffmpeg_task, c, t[0], t[1], t[2] if len(t) > 2 else False,
                              observation=observation): t[0]
                    for t, observation in zip(tasks, observations)}
            for fu in as_completed(futs):
                label = futs[fu]
                try:
                    fu.result(); print(f"    ✓ {label}（+{time.time() - t0:.0f}s）")
                except RuntimeError as e:
                    failed = failed or (label, str(e), e); print(f"    ✗ {label} 失败")
    if failed:
        print(f"✗ {what}失败（{failed[0]}）：\n{failed[1]}", file=sys.stderr)
        raise SystemExit(1) from failed[2]
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
    failed = []   # [(提交序 idx, label, exc)]；list.append 在 GIL 下原子，无需额外锁
                  # （P2-1，p2 审查报告：报告「首个失败」须取提交序而非完成序——
                  # 多路同败时完成序取值非确定，带 idx 后 min 即 STAGES 提交序）

    observations = [events.task(c, kind="wave_job") for _ in jobs]

    def _worker(idx, label, fn):
        t1 = time.monotonic()
        print(f"== {label} ==")
        try:
            observations[idx].call(fn)
        except BaseException as e:   # 含 SystemExit：sys.exit 在线程内只杀线程，必须捕获
            failed.append((idx, label, e))
            if isinstance(e, SystemExit):
                # 真实错误文案已由阶段函数自行打印，这里只归因到波内标签
                print(f"✗ {label} 失败（exit {e.code}，错误详情见上方该路输出）")
            else:
                print(f"✗ {label} 失败：{e}")
            return
        print(f"✓ {label} 完成 [timing] +{time.monotonic() - t1:.1f}s"
              f"（{time.strftime('%H:%M:%S')}）")

    with ThreadPoolExecutor(len(jobs)) as ex:
        list(ex.map(lambda j: _worker(j[0], j[1][0], j[1][1]), enumerate(jobs)))
    if failed:
        _, label, e = min(failed, key=lambda x: x[0])
        if not isinstance(e, SystemExit):
            print(f"✗ 并行波失败（首个失败：{label}）：{e!r}", file=sys.stderr)
        else:
            print(f"✗ 并行波失败（首个失败：{label}，exit {e.code}）", file=sys.stderr)
        raise SystemExit(1) from e
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
