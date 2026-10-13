"""vtx 缓存卫生层：内容寻址键、manifest 记账、gc/df/snapshot、VERSIONS、体积预算。

设计依据：vtx 缓存卫生与体积治理升级方案（2026-09-11，voice-ime 174G→25G 清理实测）。
核心原则：
  - gc 以「当前 timeline 键集」为存留判据，manifest 只做记账/df/audit，不做删除判据
    （键集可从 timeline 独立重算，manifest 缺损/损坏不影响回收正确性）。
  - cache/reports/ 永不回收（报告/回执/证据）；out/VERSIONS.json、out/history/ 永不回收。
  - A02（issue #21 / F-01）：不可再生交付件保护清单（OUT_PROTECTED_*，out_protected()）
    对 clean/gc 全量生效——普通 clean/gc 绝不删除；全删唯一入口 = clean
    --i-know-this-nukes-everything 显式危险旗标（见 vtx/cli.py）。
  - 回收只由显式命令或预算门触发，无后台守护、无静默删除。
"""
import fnmatch, hashlib, json, os, shutil, threading, time
from pathlib import Path

from . import fstxn
from .encoders import seg_encoder_kind
from .infra import ProjectPathError, ensure_inside_project
from .pluginlib import hooks as _plugin_hooks

REPORTS = "reports"          # cache/reports/：gc 永不触碰
TMP = "tmp"                  # cache/tmp/<runid>/：一次性中间物，gc 清扫孤儿
KEEP_BASELINES = ("manifest.json", "qc_pass_segs.json")   # cache 根下永不删的记账/状态件
# cache/audio/ 下不回收的状态件（方案三阶段1审查 P1-2）：loudness_receipts.json 是
# 响度测量回执（loudness_cache，qc/gates 免重测依据）——语义同 manifest（报告/回执/
# 证据不回收，见模块头），误回收只降级不破坏（回退全检/重测一轮），但 gc --dry
# 持续列它会诱导误删。伴生件（.lock flock 锁文件 / .tmp 原子写残件，P6 修复
# B-P2-1：锁文件被 budget_gate 自动 gc 删除后新开 inode，跨进程互斥失效）按
# 名字前缀一并豁免。
AUDIO_KEEP = ("loudness_receipts.json",)

# A02 不可再生交付件保护清单（issue #21 / F-01）：out/ 一级条目命中即视为
# 不可再生，普通 clean/gc 绝不删除——成片本体（final.mp4）、版本账本
# （VERSIONS.json）、历史轮转（history/）、发布冻结凭证（FREEZE*，runbook
# 步骤 6 冻结记录）、4K 发布件（final_4k*.mp4，apps/publish/export_4k 产物）
# 与证据目录（reports/，gc 既有白名单同款）。全删除唯一入口 = clean
# --i-know-this-nukes-everything（vtx/cli.py 显式危险旗标）；gc/gc --all
# 无任何变体可删保护件。清单是全仓唯一权威：clean 保护、gc --all 白名单、
# gc_plan out 豁免都经 out_protected() 取判据，禁在各处复制名单。
OUT_PROTECTED_NAMES = ("final.mp4", "VERSIONS.json")
OUT_PROTECTED_DIRS = ("history", "reports")
OUT_PROTECTED_PATTERNS = ("final_4k*.mp4", "FREEZE*")   # 发布冻结凭证/4K 发布件


def out_protected(name: str) -> bool:
    """out/ 一级条目名是否属不可再生交付件保护清单（A02）。只判名字，不 stat：
    计划阶段零副作用；条目本身是符号链接时同样按名保护（不跟随）。"""
    return (name in OUT_PROTECTED_NAMES or name in OUT_PROTECTED_DIRS
            or any(fnmatch.fnmatch(name, p) for p in OUT_PROTECTED_PATTERNS))


def cleanup_allowed(c, path: Path, *, nuke=False) -> bool:
    """issue #21/#30：计划枚举先解析目标；交付件、越界目标和锁永不穿透。"""
    path = Path(path)
    try:
        target = path.resolve()
        roots = [c.root.resolve() / name for name in ("cache", "out")]
        if not any(target == root or root in target.parents for root in roots):
            reason = "解析目标落在 cache/out 根之外"
        elif path.name.endswith(".lock") or target.name.endswith(".lock"):
            reason = "事务锁文件永不删除（issue #30）"
        else:
            out = roots[1]
            rel = target.relative_to(out) if out in target.parents else None
            protected = rel is not None and out_protected(rel.parts[0])
            # 危险 clean 仅允许直接删除保护件，不允许链接绕过保护边界。
            if protected and (not nuke or target != path.absolute()):
                reason = "解析目标属于不可再生交付件保护集（issue #21）"
            elif any(root.is_symlink() for root in (c.cache, c.out)
                     if path == root or root in path.parents):
                reason = "拒绝清理符号链接根"
            else:
                return True
    except (OSError, RuntimeError, ValueError) as e:
        reason = f"无法安全解析目标（{e}）"
    print(f"✗ 拒绝清理 {path}：{reason}")
    return False


def cleanup_children(c, path):
    """issue #21：iterdir 前先挡住目录链接穿透，候选条目仍逐一校验。"""
    if not cleanup_allowed(c, path) or not path.is_dir():
        return []
    return sorted(path.iterdir())


def cleanup_entries(c, path, reason, *, nuke=False):
    """issue #21/#30：安全子树压成一项；含锁/危险链接的祖先保留，安全兄弟照常清。"""
    def visit(p):
        if not cleanup_allowed(c, p, nuke=nuke):
            return [], False
        if p.is_symlink() or not p.is_dir():
            return [(p, dir_size(p), reason)], True
        entries, complete = [], True
        for child in sorted(p.iterdir()):
            part, safe = visit(child)
            entries.extend(part)
            complete = complete and safe
        if complete:
            return [(p, dir_size(p), reason)], True
        return entries, False

    return visit(Path(path))[0]


# manifest 读-改-写串行锁（2026-09-11）：video 段间并行 worker 与 gfx 页级并行
# 会并发调用 manifest_record，无锁时读-改-写竞态会互相覆盖丢笔。
_MANIFEST_LOCK = threading.Lock()


def _md5_file(p) -> str:
    """分块 md5（1MB 块流式 update）：终片/audio.wav 可达 GB 级，read_bytes 整读
    会瞬时占用等量内存；分块结果与整读完全一致。"""
    m = hashlib.md5()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            m.update(block)
    return m.hexdigest()


# ---------------------------------------------------------------- 内容寻址键
# 键变即新条目，旧键自然成为垃圾由 gc 回收——D29（源改缓存不重渲）与
# D38（同索引误命中旧内容）从机制上消失。源视频不整文件哈希（GB 级太贵），
# 用 stat 指纹 + 首/尾 64KB 采样哈希（PR#1 合入补强：同尺寸 + 恢复时间戳的
# 同路径替换不再误命中——媒体重编码首尾字节必变；与 cache_state.media_stat_digest
# 同一判据，保持回执敏感度 == 缓存键敏感度）。

def _sha1(b) -> str:
    return hashlib.sha1(b).hexdigest()[:12]


def _fp(p: Path) -> str:
    from .cache_state import media_stat_digest
    st = p.stat()
    return f"{p}:{st.st_size}:{st.st_mtime_ns}:{media_stat_digest(p)}"


def tl_md5(c) -> str:
    return hashlib.md5(c.tl_path.read_bytes()).hexdigest()


def gfx_key(c, cl) -> str:
    """gfx 页缓存键 = sha1(html 字节 + 显式依赖内容 + fps + 视口 + 时长)[:12]。

    PR#1 合入：project.cache_dependencies 声明的本地资产（HTML/CSS/JS 引用的
    图片/字体/样式/脚本）内容入键——改依赖即重渲；未声明/网络资产不追踪
    （cache-provenance.md）。声明属项目级，所有 gfx 页共享（保守过失效，可接受）。
    """
    html = (c.root / cl["src"]).resolve()
    blob = html.read_bytes()
    from .cache_state import declared_dependencies, file_digest
    for dep in declared_dependencies(c):
        blob += b"|" + str((c.root / dep).resolve()).encode() + b"\0" \
                + file_digest(c.root / dep).encode()
    return _sha1(blob + f"|{c.fps}|{c.w}x{c.h}|{cl['dur']}".encode())


def gfx_dir(c, cl) -> Path:
    return c.cache / "gfx" / gfx_key(c, cl)


def seg_key(c, cl, ti: int, ci: int, windowed: bool, need_alpha: bool, encoder: str) -> str:
    """seg 缓存键 = sha1(源指纹 + 切片/窗口/fade 参数 + 编码路径 + 编码器 token)[:12]。

    ti/ci 不参与（同参数的两处 clip 复用同一 seg 是正确的）；windowed/need_alpha
    决定滤镜与编码器（prores 4444 vs x264/NVENC），必须入键。
    encoder（方案二阶段3，2026-09-12）："nvenc"/"x264"/"prores"——无 alpha 分支按
    encoders.seg_encoder_kind(c) 的判定传入，alpha 分支恒 "prores"（ProRes 4444
    编码不变，但键加 token 使格式变更在未来也能自然分键）。键变 → 旧 seg 一次性
    失效由 gc 回收（内容寻址设计使然，正常）。
    """
    src = c.src_path(cl["src"])
    spec = ["seg", _fp(src), str(cl["src_in"]), str(cl["src_out"]), str(cl["dur"]),
            repr(cl.get("scale")), repr(cl.get("x")), repr(cl.get("y")),
            repr(cl.get("opacity")), repr(cl.get("fade_in_frames")),
            repr(cl.get("fade_out_frames")),
            "win" if windowed else "full", "alpha" if need_alpha else "opaque",
            encoder,
            f"{c.fps}|{c.w}x{c.h}"]
    return _sha1("|".join(spec).encode())


def audio_key(c, sid) -> str:
    """prepare 抽音频键 = sha1(源指纹 + 提取参数)[:12]。"""
    return _sha1(("audio|" + _fp(c.src_path(sid)) + "|48000stereo").encode())


# ---------------------------------------------------------------- manifest 记账
def _manifest_path(c) -> Path:
    return c.cache / "manifest.json"


def manifest_record(c, path: Path, stage: str, key: str = ""):
    """写完一个产物后记一笔（路径/阶段/键/字节/mtime/timeline_md5）。只追加，不阻塞：
    记账失败绝不影响渲染主流程（gc 不依赖 manifest）。"""
    try:
        with _MANIFEST_LOCK:   # 并发 worker（段编码/gfx 页）读-改-写串行化，防丢笔
            f = _manifest_path(c)
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                rec = {"entries": []}
            try:
                size = path.stat().st_size if path.is_file() else \
                    sum(x.stat().st_size for x in path.rglob("*") if x.is_file())
            except OSError:
                size = 0
            rec["entries"].append({
                "path": str(Path(path).relative_to(c.root)),
                "stage": stage, "key": key, "bytes": size,
                "mtime": time.strftime("%F %T"), "tl": tl_md5(c)})
            if len(rec["entries"]) > 5000:      # 防无限膨胀：只留最近 5000 笔
                rec["entries"] = rec["entries"][-4000:]
            # issue #23（A04）：改走 fstxn 原子写（tmp+os.replace）——预置
            # cache/manifest.json → 根外 的符号链接时 write_text 会跟随链接
            # 覆盖链接目标；os.replace 只换目录项，不跟随末段符号链接（目标
            # 侧免疫）。锁文件侧防护见 fstxn._flock_for。
            fstxn.atomic_write(f, json.dumps(rec, ensure_ascii=False, indent=1))
    except Exception as e:
        print(f"⚠ manifest 记账失败（不影响渲染）: {e}")


# ---------------------------------------------------------------- 体积统计
def tmp_run(c, label: str):
    """一次性中间物目录上下文（tmp 纪律，2026-09-11 定）：

    with hygiene.tmp_run(c, "qc_frames") as d:
        ...

    - 位置恒为项目内 cache/tmp/<UTC>-<label>/——GB 级媒体中间物严禁写系统 /tmp
      （Fedora 的 /tmp 是 tmpfs：写入即占 RAM/swap，且 systemd-tmpfiles 默认 10 天
      自动清理，长任务文件可能中途消失）。≤数十 MB 小件（探针帧/临时 JSON）可用
      /tmp 享受 tmpfs 速度。
    - with 正常/异常退出都删除目录（阶段结束即删）；被 kill -9 留下的孤儿
      （D33 看门狗杀长命令有先例）由 gc 的 tmp_orphans（>24h）兜底清扫。
    """
    d = c.cache / TMP / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{label}"
    # issue #23（A04）：tmp 也属衍生写路径——预置 cache/tmp → 根外 的符号链接
    # 会把 GB 级中间物写穿到链接目标；解析后必须仍落在 cache 内。
    ensure_inside_project(d, c.cache, what="tmp 中间物目录")
    d.mkdir(parents=True, exist_ok=True)
    return _TmpDir(d)


class _TmpDir:
    def __init__(self, p: Path): self.path = p

    def __enter__(self): return self.path

    def __exit__(self, et, ev, tb):
        shutil.rmtree(self.path, ignore_errors=True)
        return False


def dir_size(p: Path) -> int:
    if not p.exists(): return 0
    if p.is_file(): return p.stat().st_size
    return sum(x.stat().st_size for x in p.rglob("*") if x.is_file())


def gb(n: int) -> str:
    return f"{n / 1024**3:.2f}G" if n >= 1024**3 else f"{n / 1024**2:.1f}M"


def tmp_orphans(c) -> list:
    """cache/tmp/ 下超过 24h 未touch 的 runid 目录（正常运行结束即删，残留=孤儿）。"""
    d = c.cache / TMP
    if not d.exists(): return []
    cut = time.time() - 86400
    return [x for x in d.iterdir() if x.is_dir() and x.stat().st_mtime < cut]


# ---------------------------------------------------------------- gc
def _current_keys(c):
    """当前 timeline 的全部合法缓存键（gfx 目录名 / seg 文件名 / audio 文件名）。"""
    gfx, seg, audio = set(), set(), set()
    # seg 编码器判定与 composite.prepare_layers 同源（encoders.seg_encoder_kind，
    # 方案二阶段3）：gc 重算键必须与编码时同判据，否则键集对不上会误回收活 seg。
    # 循环外算一次（nvenc_ok 真探测时起 ffmpeg 子进程，逐 clip 探测是纯浪费）。
    seg_kind = seg_encoder_kind(c)
    for ti, tr in enumerate(c.tl["video_tracks"]):
        for ci, cl in enumerate(tr["clips"]):
            if tr.get("kind") == "gfx":
                if (c.root / cl["src"]).exists():
                    gfx.add(gfx_key(c, cl))
            else:
                if c.src_path(cl["src"]).exists() and "src_in" in cl:
                        windowed = any(k in cl for k in ("scale", "x", "y"))
                        need_alpha = (cl.get("opacity", 1) < 1 or
                                      cl.get("fade_in_frames") or cl.get("fade_out_frames"))
                        seg.add((seg_key(c, cl, ti, ci, windowed, need_alpha,
                                         "prores" if need_alpha else seg_kind),
                                 need_alpha))
    for s in c.tl["sources"]:
        if c.src_path(s["id"]).exists():
            audio.add(audio_key(c, s["id"]))
    # v2 §2.1 不变量 ①：插件缓存键贡献并入 ak 集合（插件产物不注册键就会被
    # budget_gate 自动触发的 gc 回收）；P1-2 空转 = 空集，键集零变化。
    audio |= _plugin_hooks.contribute_keys(c)
    return gfx, seg, audio


def gc_plan(c, keep_days: int = 0):
    """返回 {类别: [(路径, 字节, 原因)]}。只计算，不删除。"""
    plan = {"gfx": [], "seg": [], "audio": [], "plugin_cache": [], "legacy": [],
            "tmp": [], "out_intermediate": [], "vseg": []}
    gk, sk, ak = _current_keys(c)
    cut = time.time() - keep_days * 86400 if keep_days else 0

    def fresh(p):    # --keep-days N：近期动过的条目暂缓回收
        return cut and p.stat().st_mtime > cut

    def add(category, path, reason):
        plan[category].extend(cleanup_entries(c, path, reason))

    d = c.cache / "gfx"
    if d.exists():
        for x in cleanup_children(c, d):
            if x.name not in gk:
                add("gfx", x, "键不在当前 timeline 键集")
    d = c.cache / "seg"
    if d.exists():
        for x in cleanup_children(c, d):
            if x.stem not in {k for k, _ in sk}:
                add("seg", x, "键不在当前 timeline 键集")
    d = c.cache / "audio"
    if d.exists():
        for x in cleanup_children(c, d):
            # 伴生件前缀豁免（.lock/.tmp，见 AUDIO_KEEP 注）：精确名或前缀命中即免
            if x.name in AUDIO_KEEP or \
                    any(x.name.startswith(k + ".") for k in AUDIO_KEEP):
                continue   # 测量回执不属内容寻址键集（方案三阶段1审查 P1-2）
            if x.stem not in ak:
                add("audio", x, "源指纹不在当前 timeline 键集")
    # v2 §2.1 不变量 ①（v1.2.0 键级扩展，R1-F F2）：插件 cache_dirs 的**键级
    # 条目**回收——插件经 cache_dir_keys 声明键集的目录（bgm-render 的
    # cache/bgm/<render_digest>/），一级子目录按内容寻址键对待：在当前 timeline
    # 键集内保活，不在 = 陈旧（--dry 列出、gc 删）——修复整工程渲染缓存目录随
    # 工程/digest 演进无界累积。伴生件（<digest>.lock 等非目录件）按 AUDIO_KEEP
    # 同惯例豁免不回收（锁文件删除破跨进程互斥，B-P2-1；量级仅空文件）。仅对
    # 经 registry cache_dirs 注册的目录生效；无插件声明键集 = 本类恒空（通道
    # 消费兼容：整目录白名单语义对其他 cache_dirs 消费者不变）。
    dirs_whitelist = _plugin_hooks.get_cache_dirs(c)
    for dname, dkeys in _plugin_hooks.get_cache_dir_keys(c).items():
        d = c.cache / dname
        if dname not in dirs_whitelist or not d.is_dir():
            continue
        for x in cleanup_children(c, d):
            if x.is_dir() and x.name not in dkeys:
                add("plugin_cache", x, "键不在当前 timeline 键集（陈旧共享渲染缓存）")
    # 历史遗留目录（迁移期）：cache 根下非本方案布局的一级目录/文件 → 全部视为垃圾
    # （subs*.ass/qc_clean/vseg* 另有专属类别，这里排除避免重复计数）
    known = {"gfx", "seg", "audio", REPORTS, TMP, "snapshots", "manifest.json",
             "subs.ass", "subs_draft.ass", "qc_clean.mp4",
             "vseg", "vseg_draft", "vseg_qc", "vseg_pass", "preview", "report_audio"} | set(KEEP_BASELINES)
    # v2 §2.1 不变量 ①：插件声明的 cache 子目录并入白名单（自立目录型插件产物
    # 不再判 legacy 垃圾）；P1-2 空转 = 空集，白名单零变化。传 c 使「仅对本
    # timeline 生效的插件」过滤生效（与 get_cache_dirs docstring 口径一致）。
    known |= _plugin_hooks.get_cache_dirs(c)
    for x in cleanup_children(c, c.cache):
        if x.name in known: continue
        add("legacy", x, "旧布局条目（键不含内容指纹，不可信）")
    # 分段渲染中间物：切点/段名随 timeline 变化且无内容键，整体按可再生垃圾处理
    for name in ("vseg", "vseg_draft", "vseg_qc"):
        d = c.cache / name
        if d.exists(): add("vseg", d, "分段渲染中间物（可再生）")
    for x in [c.cache / "qc_clean.mp4", c.cache / "subs.ass", c.cache / "subs_draft.ass"]:
        if x.exists(): add("legacy", x, "一次性中间物（可再生）")
    for x in cleanup_children(c, c.cache / TMP):
        if x.is_dir() and cleanup_allowed(c, x) and x.stat().st_mtime < time.time() - 86400:
            add("tmp", x, "tmp 孤儿（>24h）")
    # out/ 一次性中间物（成片/回执/VERSIONS/history/reports 不动）
    out_keep = {"final.mp4", "draft_final.mp4", "draft.mp4", "video_noaudio.mp4",
                "audio.wav", "receipt.json", "receipt.prev.json", "VERSIONS.json",
                "audit_report.json", "qc_report.json", "history", "reports"}
    if c.out.exists():
        for x in cleanup_children(c, c.out):
            # A02：保护清单经 out_protected() 单源取判据（与 clean/gc --all 同一
            # 权威；out_keep 本就含全部保护名，此处判据等价合并，行为不变）
            if x.name in out_keep or out_protected(x.name):
                continue
            add("out_intermediate", x, "out 一次性中间物")
    if keep_days:
        for cat in ("gfx", "seg", "audio", "plugin_cache", "legacy", "vseg"):
            plan[cat] = [e for e in plan[cat] if not fresh(e[0])]
    return plan


def _out_deliverable(name: str) -> bool:
    """out/ 交付件白名单模式（2026-09-14 补，红队 gc --dry 实证遗漏）：
    final_4k*.mp4 = apps/publish/export_4k.py 发布件（runbook R5 4K 链产物，
    final_4k_x264_ref.mp4 质量对照件同前缀）；FREEZE*.json = runbook 步骤 6
    冻结记录（md5 清单证据）。两者此前落「out 一次性中间物」分类被 gc 列为
    可回收——发布件/冻结证据不可再生，属白名单遗漏交付件。
    A02 起并入 out_protected() 保护清单（模式部分仍由本函数承载）。"""
    return any(fnmatch.fnmatch(name, p) for p in OUT_PROTECTED_PATTERNS)


def gc(c, do: bool, deep: bool = False, keep_days: int = 0):
    """gc 主入口。do=False 只列清单（--dry）；do=True 执行；deep 等价原 clean 但
    保留 reports/VERSIONS/history/snapshots 等（A02：out 侧保护经 out_protected()
    单源判据，与 cli 的 gc --all 白名单同一权威；cache 侧仍留 reports/tmp/
    snapshots/记账基线）。注：CLI 的 gc/clean 统一走 vtx/cli.py _cleanup_plan
    （计划-预览-执行三段），本函数供 budget_gate 与直调方使用。"""
    if deep:
        # issue #21/#30：预算门/直调与 CLI 复用同一安全计划，不保留绕过入口。
        from .cli import ExecutionPolicy, _cleanup_plan
        plan = _cleanup_plan(c, "gc", deep=True)
        total = sum(size for items in plan.values() for _, size, _ in items)
        if do:
            ExecutionPolicy.EXECUTE.execute_cleanup(plan)
        print(("✓ 深度回收（保留不可再生交付件与 reports/snapshots）" if do
               else "深度回收清单（--dry）") + f"：{gb(total)}")
        return
    plan = gc_plan(c, keep_days)
    total = 0; n = 0
    for cat, items in plan.items():
        if not items: continue
        sz = sum(s for _, s, _ in items)
        total += sz; n += len(items)
        print(f"{cat}: {len(items)} 项，{gb(sz)}")
        for p, s, why in items[:30]:
            print(f"  - {p.relative_to(c.root)}  {gb(s)}  {why}")
        if len(items) > 30: print(f"  … 另 {len(items) - 30} 项")
    if not n:
        print("✓ 无可回收垃圾，cache/out 与当前 timeline 键集一致"); return
    print(f"合计：{n} 项，{gb(total)}")
    if not do:
        print("（--dry 预览；执行回收请去掉 --dry 重跑 `vtx gc`）"); return
    from .cli import ExecutionPolicy
    ExecutionPolicy.EXECUTE.execute_cleanup(plan)
    print(f"✓ 已回收 {n} 项，{gb(total)}")


# ---------------------------------------------------------------- df
def df(c, quiet: bool = False):
    """体积体检：分项占用 + top-20 大文件 + tmp 孤儿。quiet=True（audit 内嵌）只算不打印，
    返回 {cache, out, total} 字节数。"""
    P = (lambda *a, **k: None) if quiet else print
    # B03（issue #27 / F-05）：df 是只读体积体检——cache/ 不存在时按空布局报告，
    # 绝不因体检创建目录（原 mkdir 兜底使 df/audit 内嵌路径长出 cache/，命令
    # 执行前后项目树出现新目录即违例）。audit 内嵌（quiet）同口径零写盘。
    P("== cache/ ==")
    tcache = 0
    if c.cache.is_dir():
        entries = sorted(((x, dir_size(x)) for x in c.cache.iterdir()),
                         key=lambda e: -e[1])
        for x, s in entries:
            P(f"  {x.name:24s} {gb(s):>10s}"); tcache += s
    P(f"  cache 合计 {gb(tcache)}")
    tout = 0
    if c.out.exists():
        P("== out/ ==")
        for x, s in sorted(((x, dir_size(x)) for x in c.out.iterdir()), key=lambda e: -e[1]):
            P(f"  {x.name:24s} {gb(s):>10s}"); tout += s
    P(f"  out 合计 {gb(tout)}")
    total = tcache + tout
    P(f"总计 {gb(total)}")
    files = sorted(c.cache.rglob("*"), key=lambda p: p.stat().st_size if p.is_file() else 0,
                   reverse=True)[:20]
    P("== top-20 大文件（cache）==")
    for p in files:
        if p.is_file(): P(f"  {gb(p.stat().st_size):>10s}  {p.relative_to(c.root)}")
    orph = tmp_orphans(c)
    P("== tmp 孤儿 ==")
    if orph:
        for x in orph: P(f"  {x}  {gb(dir_size(x))}（vtx gc 回收）")
    else:
        P("  无")
    m = _manifest_path(c)
    if m.exists():
        try:
            last = json.loads(m.read_text(encoding="utf-8"))["entries"][-1]
            P(f"最近一笔记账：{last['mtime']} {last['stage']} {last['path']}")
        except Exception:
            pass
    return {"cache": tcache, "out": tout, "total": total}


# ---------------------------------------------------------------- snapshot
def snapshot(c, keep: int = None):
    """timeline.json + 其引用的全部文本资产 → cache/snapshots/<UTC>-<md5[:8]>/。
    只收文本（html/js/json/ass，KB 级），媒体不入快照。只留最近 keep 份。

    issue #23（A04 / P0，工单点名补齐）相对路径安全校验：资产引用串来自不可信
    的 timeline，快照落点不得照搬引用串——「../」「绝对路径」「预置的 snapshots
    子目录符号链接」都会把 mkdir/copy2 写到快照目录乃至项目根之外。三道写侧门
    全部在**任何写动作之前**完成（含 dst.mkdir 之前——半途快照本身也是脏写）：
      ① 快照目录门：dst 解析后必须落在 cache 内（防预置 cache/snapshots →
         根外 的符号链接把整棵快照写穿到链接目标）；
      ② 落点门：dst/<引用串> 解析后必须落在本次快照目录 dst 内（拒 ../ 穿越
         与绝对路径——pathlib 拼接 ``dst / "/abs"`` 会整体替换左侧前缀）；
      ③ 资产门：被引用的文本资产解析后必须落在项目根内——快照只收项目内文本
         资产，根内符号链接指向根外 = 拒绝（报错含去向）。
    素材「读取」的外部合法性不受影响：validate/gc 等读侧照常接受项目外素材
    （tests/safety/test_timeline_escape_safety.py 对照组）。缺失资产仍按既有 UX
    警告跳过（落点门先行，缺失不会放过越界引用）。"""
    keep = keep if keep is not None else int(os.environ.get("VTX_SNAPSHOT_KEEP", "2"))
    md5 = tl_md5(c)
    dst = c.cache / "snapshots" / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{md5[:8]}"
    # ①（issue #23）：dst 自身先收口，且须在 dst.exists() 探测之前——预置的
    # cache/snapshots 符号链接会把后续全部写动作导向链接目标，攻击者还可在
    # 链接目标侧预建同名目录骗过「快照已存在」早退。
    ensure_inside_project(dst, c.cache, what="快照目录")
    if dst.exists():
        print(f"✓ 快照已存在（同 timeline md5）：{dst.name}"); return dst
    texts = set()
    for tr in c.tl.get("video_tracks", []):
        for cl in tr.get("clips", []):
            if tr.get("kind") == "gfx": texts.add(cl["src"])
    # v2 subtitles_src（§1.1 条目 6）：引用的字幕文件同为文本资产（KB 级）——快照
    # 含它才有完整回滚锚点；仅影响声明了 subtitles_src 的 timeline（v1 零变化）。
    _sub_src = c.tl.get("subtitles_src")
    if isinstance(_sub_src, str) and _sub_src:
        texts.add(_sub_src)
    # ②③（issue #23）：全部资产先判定，任一拒绝即整体失败（不留半途快照）。
    problems = []
    for p in sorted(texts):
        try:
            ensure_inside_project(dst / p, dst, what="快照落点")
        except ProjectPathError as e:
            problems.append(str(e)); continue
        src = c.root / p
        if not src.exists():
            continue   # 缺失资产按既有 UX 在写循环内警告跳过（落点已确认安全）
        try:
            ensure_inside_project(src, c.root, what="快照文本资产")
        except ProjectPathError as e:
            problems.append(str(e))
    if problems:
        raise ProjectPathError("快照拒绝不安全资产引用（issue #23）："
                               + "；".join(sorted(set(problems))))
    dst.mkdir(parents=True)
    (dst / "timeline.json").write_text(c.tl_path.read_text(encoding="utf-8"), encoding="utf-8")
    for p in sorted(texts):
        src = c.root / p
        if not src.exists(): print(f"  ⚠ 快照跳过不存在的资产: {p}"); continue
        op = dst / p
        op.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, op)
    snaps = sorted((c.cache / "snapshots").iterdir())
    # 注意切片写法：snaps[:-keep] 在 keep=0 时是 snaps[:0]（语义反转为全保留），
    # 显式 len(snaps)-keep 才是「保留最近 keep 份、其余全删」。
    for old in snaps[:len(snaps) - keep] if len(snaps) > keep else []:
        shutil.rmtree(old, ignore_errors=True)
    n = len(texts) + 1
    print(f"✓ 快照 {dst.name}（{n} 个文本文件，{gb(dir_size(dst))}；保留最近 {keep} 份）")
    return dst


# ---------------------------------------------------------------- VERSIONS（out 产物记账）
def versions_check(c, product: Path) -> bool:
    """覆盖前检测：现有文件 md5 与 VERSIONS 末条不符（被外部改动/并行工程改写）→
    显著告警并把旧条目标 superseded。返回是否告警。"""
    f = c.out / "VERSIONS.json"
    try:
        rec = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"versions": []}
    except Exception:
        rec = {"versions": []}
    if not product.exists(): return False
    old_md5 = _md5_file(product)
    last = next((v for v in reversed(rec["versions"])
                 if v.get("product") == product.name and v.get("md5")), None)
    if last and last["md5"] != old_md5:
        print(f"⚠⚠ 覆盖告警：将覆盖的 {product.name} md5={old_md5[:8]} 与 VERSIONS "
              f"末条 {last['md5'][:8]} 不符——文件曾被外部改动或并行工程改写！"
              f"旧版本已标记 superseded（历史见 out/history/）。")
        last["superseded"] = True
        last["superseded_note"] = f"{time.strftime('%F %T')} 覆盖前发现 md5 不符"
        # issue #23（A04）：同 manifest_record——fstxn 原子写对末段符号链接
        # 免疫（os.replace 不跟随），防预置 VERSIONS.json → 根外 链接覆盖目标。
        fstxn.atomic_write(f, json.dumps(rec, ensure_ascii=False, indent=2))
        return True
    return False


def versions_record(c, product: Path, label: str):
    """mux 成功后追加一条 VERSIONS 记录（只记元数据，不复制全片）。"""
    f = c.out / "VERSIONS.json"
    try:
        rec = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"versions": []}
    except Exception:
        rec = {"versions": []}
    rec["versions"].append({
        "time": time.strftime("%F %T"), "cmd": label, "product": product.name,
        "md5": _md5_file(product),
        "bytes": product.stat().st_size,
        "timeline_md5": tl_md5(c),
        "audio_md5": _md5_file(c.out / "audio.wav")
        if (c.out / "audio.wav").exists() else None})
    # issue #23（A04）：fstxn 原子写，同 versions_check 的符号链接免疫理由。
    fstxn.atomic_write(f, json.dumps(rec, ensure_ascii=False, indent=2))


def rotate_history(c, product: Path, keep: int):
    """覆盖写 final 前把旧片轮转进 out/history/（硬链接优先，跨文件系统回落复制）。

    issue #23（A04）：out/history 预置为指向根外的符号链接时轮转的 os.link/
    copy2 会写穿链接目标——hdir 经 ensure_inside_project 收口在 out 内后才 mkdir
    （指向内的链接 = 允许）。"""
    if not product.exists(): return
    hdir = c.out / "history"
    ensure_inside_project(hdir, c.out, what="历史轮转目录")
    hdir.mkdir(exist_ok=True)
    md5 = _md5_file(product)[:8]
    dst = hdir / f"{product.stem}_{time.strftime('%Y%m%d-%H%M%S')}_{md5}{product.suffix}"
    try:
        os.link(product, dst)
    except OSError:
        shutil.copy2(product, dst)
    keep = max(0, keep)
    olds = sorted(hdir.glob(f"{product.stem}_*{product.suffix}"))
    # 同 snapshot：olds[:-keep] 在 keep=0 时语义反转（全保留），显式 len(olds)-keep。
    for old in olds[:len(olds) - keep] if len(olds) > keep else []:
        old.unlink()


# ---------------------------------------------------------------- 预算门
def budget_gate(c, stage: str):
    """VTX_CACHE_BUDGET_GB（默认不设）：阶段开始前 cache 超预算 → 跑 gc 清单提示；
    VTX_GC_AUTO=1 时直接回收。"""
    budget = os.environ.get("VTX_CACHE_BUDGET_GB", "")
    if not budget or not budget.replace(".", "", 1).isdigit(): return
    limit = float(budget) * 1024**3
    size = dir_size(c.cache)
    if size <= limit: return
    print(f"⚠ 体积预算：cache {gb(size)} > 预算 {budget}G（{stage} 阶段前）")
    auto = os.environ.get("VTX_GC_AUTO", "") == "1"
    gc(c, do=auto)
    if auto:
        print(f"✓ VTX_GC_AUTO=1 已自动回收，cache 现 {gb(dir_size(c.cache))}")
    else:
        print("  上面为回收清单（--dry 预览）；执行 `vtx gc` 回收，或设 VTX_GC_AUTO=1 自动回收")

