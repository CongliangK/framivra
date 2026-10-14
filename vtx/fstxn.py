"""fstxn —— 共享底层文件事务工具（B05，2026-10-13；issue #29/#30 / F-08 / #8 的
receipt 落地件，B07 的 manifest/VERSIONS 应用将复用本模块）。

统一三件套（此前 receipt/manifest/loudness 各自为政的三种写法收拢为一）：
  ① flock 跨进程互斥（fcntl，POSIX）：同一文件的「读-改-写」跨进程串行化——
     os.replace 原子只保证文件不撕裂，不保证读-改-写不丢笔（两进程各自基于
     旧快照回写，后写者覆盖前写者）。锁文件 <目标名>.lock 随目标目录存在，
     fd 关闭即释放（进程被杀内核自动回收，无陈旧锁残留——flock 语义随 fd
     生命周期，loudness_cache 同惯例）。Windows 无 fcntl 时降级为仅进程内锁
     （本仓 Linux 为主战场）。
  ② 临时文件 + os.replace 原子写：写者崩溃/被杀后目标文件要么旧值要么新值，
     永不为截断半写件（同目录 tmp 保证 replace 的原子性；残件以 . 前缀 +
     .tmp 后缀落盘，gc 的 out/cache 中间物规则兜底清扫）。
  ③ 两层锁序：进程内 per-path threading.Lock（快速层，防同进程多线程——
     `vtx all` 依赖图波内 gfx/audio 并发回执）之上再加 flock（慢速层，防
     跨进程）。锁序恒为「线程锁 → flock」，本模块是唯一入口，无倒序死锁面。

issue #59（快照 TOCTOU 写窗 / 固定名预置链接写穿面）起，能力满足的平台整体
升级为 fd 锚定写（O_NOFOLLOW 逐组件开目录描述符 + 随机名 O_EXCL tmp + 提交前
inode 对账 + dir_fd 原子替换）：
  - 平台能力探测（write_capability）：缺 O_NOFOLLOW / dir_fd / /proc/self/fd
    的平台（如 Windows、部分 macOS）不抛错瘫痪，而是显式降级回本模块原先的
    基线安全等级（mkstemp + os.replace + ensure_inside_project 锁守卫），降级
    一次性记录到 stderr 并可经 write_capability() 观测（'fd-anchored'|'legacy'）。
  - 写目标的符号链接解析边界由显式项目根（bind_project_root，Ctx 构造时以
    c.root 注册）判定，不再按 cache/out 目录名猜测。
  - 子进程写者（ffmpeg/chrome）经 locked_staging 拿同目录随机名真实路径：
    /proc 锚定路径会被子进程沙箱/yama 拒开，故以 O_EXCL 随机名 + 提交前
    inode 对账收口。

API（上下文管理器 + mutator 两形态）：
  locked_write(path)            CM：持锁 + 代管同目录 tmp；yield /proc 锚定的
                                 tmp Path（进程内写者专用——置换 tmp 目录项不
                                 影响已开 inode），调用方填充内容，正常退出经
                                 身份对账 + os.replace 落盘，异常退出清残件、
                                 目标保持旧值。
  locked_staging(path)          CM：同上但 yield 真实 tmp 路径（子进程写者专用）。
  locked_update(path, mutator, *, load=None, default=None, dump=None)
                                 「读-改-写」事务糖：path 经 load 读出（缺省
                                 json.loads，缺失/损坏回 default）→ mutator(data)
                                 → dump 后经 ①② 落盘。mutator 返回 None 中止
                                 事务不写（目标不动）。账本类目标的末段符号链接
                                 允许被原子替换（目标侧免疫，issue #23/#31 语义）。
  atomic_write(path, data)       无锁原子写（仅 ②）：调用方已持锁区段内的
                                 伴生件写入用（如 receipt 轮转旁证 prev.json，
                                 持 receipt.json 锁的写者天然独占）。
  mkdir/unlink/replace           写点原语的 fd 锚定形态（降级平台等价 main 行为）。
  pinned_parent/fd_path/flock_at/remove_at/entry_identity/check_regular_at
                                 fd 锚定公开原语（要求 write_capability() ==
                                 'fd-anchored'；hygiene 快照/轮转等直接编排
                                 目录描述符的调用面）。

不可重入：mutator/with 体内不得再对同路径调用本模块（threading.Lock 不可
重入即死锁）；对其他路径的嵌套事务须保持全仓一致的获取顺序（receipt 主件
先于 prev 伴生件）。
"""
import contextlib, copy, errno, json, os, secrets, stat, sys, tempfile, threading
from pathlib import Path

__all__ = ["locked_write", "locked_staging", "locked_update", "atomic_write",
           "cleanup_identity", "remove_pinned", "mkdir", "unlink", "replace",
           "pinned_parent", "fd_path", "entry_identity", "flock_at", "remove_at",
           "check_regular_at", "bind_project_root", "write_capability"]

# Capture the real APIs once: tests may instrument the final unlink syscall.
_CLEANUP_DIR_FD_APIS = (os.open, os.stat, os.unlink, os.rmdir)
_WRITE_DIR_FD_APIS = (os.open, os.mkdir, os.stat, os.unlink, os.rename)


def _cleanup_error(path, reason):
    from .infra import ProjectPathError
    return ProjectPathError(f"拒绝清理 {path}：{reason}")


def entry_identity(info):
    """目录项身份指纹：(st_dev, st_ino, 类型位)。

    公开契约（#56 计划/执行对账与 #59 写侧 staging 对账共用同一口径）：仅凭
    此三元组判定「同一 inode 且类型未变」；调用方负责先取 stat（follow 与否
    由取 stat 方决定）。"""
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))


def cleanup_identity(path):
    """Read-only plan token: identities of every ancestor and the leaf.

    Never keep descriptors in a preview plan. Execution reopens the same inode
    chain with O_NOFOLLOW; a missing candidate is harmless and never authorizes
    deleting a replacement that appears later.
    """
    path = Path(os.path.abspath(path))
    chain = list(reversed(path.parents)) + [path]
    result = []
    for component in chain:
        try:
            info = component.lstat()
        except FileNotFoundError:
            return None
        if component != path and not stat.S_ISDIR(info.st_mode):
            raise _cleanup_error(path, "计划祖先不是普通目录（不跟随符号链接）")
        result.append(entry_identity(info))
    return tuple(result)


def remove_pinned(path, expected, *, preserve):
    """Delete relative to pinned no-follow directory descriptors, never paths.

    The plan binds the whole ancestry including the project/cache/out root.
    Recursive traversal uses only dir_fd operations; replacing an ancestor after
    the last identity check cannot redirect unlink into its new symlink target.
    preserve(relative_path) is checked for every descendant, so newly created
    locks survive even when their parent was planned as a whole-directory item.
    POSIX fd primitives are mandatory; there is no unsafe path-based fallback.
    This protects namespace substitution, not a hostile process physically
    moving unrelated protected inodes into an authorized garbage directory.
    """
    if expected is None:
        return
    # #56 语义：清理侧（删除）无降级——缺 fd 原语的平台显式拒绝（实时探测
    # os.supports_dir_fd，不走写侧降级缓存；与写路径的 #59 降级契约互不覆盖）。
    if (not getattr(os, 'O_NOFOLLOW', 0) or not getattr(os, 'O_DIRECTORY', 0)
            or not all(api in os.supports_dir_fd for api in _CLEANUP_DIR_FD_APIS)
            or os.stat not in os.supports_follow_symlinks
            or os.listdir not in os.supports_fd):
        raise _cleanup_error(path, "平台不支持安全 dir_fd/O_NOFOLLOW 清理")
    path = Path(os.path.abspath(path))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptors = []
    try:
        fd = os.open(path.anchor, flags)
        descriptors.append(fd)
        if entry_identity(os.fstat(fd)) != expected[0]:
            raise _cleanup_error(path, "计划根身份已变化")
        for index, name in enumerate(path.parts[1:-1], 1):
            fd = os.open(name, flags, dir_fd=fd)
            descriptors.append(fd)
            if entry_identity(os.fstat(fd)) != expected[index]:
                raise _cleanup_error(path, "计划祖先身份已变化")
        remove_at(fd, path.name, expected[-1], what=path, preserve=preserve)
    except FileNotFoundError:
        # Another collector already removed the entry/ancestor. No deletion is
        # redirected to any later replacement, and idempotent cleanup is kept.
        return
    except OSError as error:
        raise _cleanup_error(path, f"路径变化或安全删除失败（{error}）") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def remove_at(parent_fd, name, expected, *, what=None, preserve):
    """按计划身份删除锚定目录下的一个条目（文件或整棵子树）——remove_pinned
    的单点公开形态（#59 快照 staging 清理 / 旧快照轮转复用同一判据）。

    契约：parent_fd 必须是本模块 pinned_parent/fd 写路径锚定的目录描述符；
    expected 是 entry_identity 取得的计划身份，入口即对账（置换条目拒绝删除，
    绝不删到后出现的新目录）；preserve(relative_path) 对每个后代生效（返回
    True 即保留该后代，如并发写者的 .lock）；目录在 rmdir 前复验身份。
    要求 write_capability() == 'fd-anchored'（清理侧无路径形态降级，#56 语义）。"""
    _remove_at(parent_fd, name, Path(what) if what else Path(name), expected,
               os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, preserve)


def _remove_at(parent_fd, name, path, expected, flags, preserve):
    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if entry_identity(info) != expected:
        raise _cleanup_error(path, "计划条目身份已变化")
    if preserve(path):
        return False
    if not stat.S_ISDIR(info.st_mode):
        os.unlink(name, dir_fd=parent_fd)  # unlink never follows the leaf
        return True
    child_fd = os.open(name, flags, dir_fd=parent_fd)
    try:
        if entry_identity(os.fstat(child_fd)) != expected:
            raise _cleanup_error(path, "目录在打开时被置换")
        complete = True
        for child in sorted(os.listdir(child_fd)):
            try:
                child_info = os.stat(child, dir_fd=child_fd, follow_symlinks=False)
                removed = _remove_at(child_fd, child, path / child,
                                     entry_identity(child_info), flags, preserve)
                complete = complete and removed
            except FileNotFoundError:
                continue
        if not complete:
            return False
        # The descriptor pins traversal, but rmdir still acts on a directory
        # entry. Refuse a replaced entry instead of removing a new directory.
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if entry_identity(current) != expected:
            raise _cleanup_error(path, "目录在删除时被置换")
        try:
            os.rmdir(name, dir_fd=parent_fd)
        except OSError as error:
            if error.errno == errno.ENOTEMPTY:
                # Concurrent writer/new lock: keep the directory, never retry
                # with an unbounded recursive delete of unexamined contents.
                return False
            raise
        return True
    finally:
        os.close(child_fd)

try:
    import fcntl    # POSIX 跨进程文件锁；Windows 缺该模块（降级见模块头注释）
except ImportError:   # pragma: no cover - Windows 主战场外的降级路径
    fcntl = None

# 进程内 per-path 锁注册表（收口后的目标路径 → Lock）。CLI 生命周期内路径数
# 有限（每项目一份 receipt/manifest/VERSIONS），不淘汰。fstxn 是全部文件事务的
# 唯一入口 → 同路径恒定先线程锁后 flock，跨路径顺序由调用方（见模块头尾注）。
_LOCKS_GUARD = threading.Lock()
_LOCKS = {}


def _thread_lock_for(path: Path) -> threading.Lock:
    key = str(path.absolute())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _write_error(path, reason):
    from .infra import ProjectPathError
    return ProjectPathError(f"拒绝写入 {path}（issue #23/#59）：{reason}")


# ---------------------------------------------------------------- 显式项目根
_PROJECT_ROOT = None   # 进程级项目根（bind_project_root 注册；None=未声明）


def bind_project_root(root):
    """显式声明本进程的项目根（issue #59 返工：取代按 cache/out 目录名猜根）。

    写目标的符号链接解析边界以唯一权威为准——infra.Ctx 构造时以 c.root 注册。
    声明在根内的路径：父链全解析后必须仍落在根内（指向根外的链接 = 拒绝
    写入；指向根内的链接 = 允许，与 infra.ensure_inside_project 同判据）。
    根外路径与未声明根的库级调用（独立测试/gfx_render 单文件模式）退回
    「全解析父链、不设包含判定」口径。幂等；root=None 清除绑定（测试用）。"""
    global _PROJECT_ROOT
    _PROJECT_ROOT = Path(os.path.abspath(root)) if root is not None else None


def _write_target(path):
    """收口写目标：NUL 拒绝 + 父链全解析（叶子绝不解析——原子替换替换的是
    目录项，绝不解析到链接受害者）+ 显式根包含判定（见 bind_project_root）。"""
    try:
        p = Path(os.path.abspath(path))
        if '\x00' in str(p):
            raise ValueError('NUL in path')
        parent = p.parent.resolve()
        root = _PROJECT_ROOT
        if root is not None:
            real = root.resolve()
            declared_inside = p == root or root in p.parents \
                or p == real or real in p.parents
            if declared_inside and parent != real and real not in parent.parents:
                raise _write_error(p, '符号链接或路径逃逸项目根')
        return parent / p.name
    except (OSError, RuntimeError, ValueError) as error:
        from .infra import ProjectPathError
        if isinstance(error, ProjectPathError):
            raise
        raise _write_error(path, f'非法路径（{error}）') from error


# ---------------------------------------------------------------- 能力探测与降级
_FD_WRITE_SUPPORT = None      # None=未探测；True/False=结论（测试可覆写以模拟平台）
_DEGRADED_NOTICED = False


def _probe_fd_writes():
    """fd 锚定写所需的全套平台原语是否齐备（探测本身零副作用）。"""
    return bool(getattr(os, 'O_NOFOLLOW', 0) and getattr(os, 'O_DIRECTORY', 0)
                and all(api in os.supports_dir_fd for api in _WRITE_DIR_FD_APIS)
                and Path('/proc/self/fd').is_dir())


def _fd_write_supported():
    global _FD_WRITE_SUPPORT
    if _FD_WRITE_SUPPORT is None:
        _FD_WRITE_SUPPORT = _probe_fd_writes()
    return _FD_WRITE_SUPPORT


def write_capability():
    """当前进程写路径安全等级：'fd-anchored'（fd 锚定全保真）或 'legacy'
    （降级=基线安全等级，原 main 行为）。降级首次发生时向 stderr 记一条
    显式标记（issue #59 返工契约：降级绝不抛错瘫痪写路径，且必须可观测）。"""
    return 'fd-anchored' if _fd_write_supported() else 'legacy'


def _note_degraded_once():
    global _DEGRADED_NOTICED
    if not _DEGRADED_NOTICED:
        _DEGRADED_NOTICED = True
        print("⚠ fstxn：平台缺 O_NOFOLLOW/dir_fd//proc/self/fd 支持，文件写入"
              "降级为基线安全等级（issue #59；write_capability()='legacy'）",
              file=sys.stderr)


def _legacy_guard():
    """降级守卫：legacy 分支入口统一记录降级标记。"""
    if not _fd_write_supported():
        _note_degraded_once()
        return True
    return False


def _require_fd_writes(path, *, what="写入"):
    """fd 锚定原语的硬门槛——仅限内部已按能力分支后必须 fd 的路径（清理侧
    #56 无降级；写侧入口均已先经 _legacy_guard 分流，不会走到这里）。"""
    if not _fd_write_supported():
        raise _write_error(path, f'平台不支持安全 dir_fd/O_NOFOLLOW/proc-fd {what}')


@contextlib.contextmanager
def pinned_parent(path, *, create=True):
    """Open/create every directory component via a no-follow pinned parent fd.

    Holding the final descriptor protects all subsequent mkdir/open/rename from
    namespace substitution. Never mkdir through an unchecked pathname. 契约：
    仅 with 体内由本函数自身的目录链操作抛出的 OSError 会转 ProjectPathError；
    调用方 with 体内的异常（含 OSError）原样穿透——loudness 回执的 advisory
    吞错与既有测试对原始 OSError 的断言都依赖该语义。要求
    write_capability() == 'fd-anchored'。"""
    p = _write_target(path)
    _require_fd_writes(p)
    descriptors = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        fd = os.open(p.anchor, flags)
        descriptors.append(fd)
        for name in p.parts[1:-1]:
            if create:
                try:
                    os.mkdir(name, dir_fd=fd)
                except FileExistsError:
                    pass
            fd = os.open(name, flags, dir_fd=fd)
            descriptors.append(fd)
    except OSError as error:
        for descriptor in descriptors:
            os.close(descriptor)
        raise _write_error(p, f'路径变化或安全访问失败（{error}）') from error
    try:
        yield fd, p.name, p
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def fd_path(fd):
    """本进程已开 fd 的稳定路径形态（/proc/<pid>/fd/<fd>）。

    仅限本进程内写者使用（写穿保护：置换目录项不影响已开 inode）。子进程
    沙箱/yama 可能拒开他人 /proc 项——子进程写者改用 locked_staging。"""
    return Path('/proc') / str(os.getpid()) / 'fd' / str(fd)


def mkdir(path):
    """Create a directory without following a preplanted or exchanged link;
    platforms without fd primitives degrade to Path.mkdir (main 安全等级)."""
    if _legacy_guard():
        Path(path).mkdir(parents=True, exist_ok=True)
        return Path(path)
    with pinned_parent(Path(path) / '.directory-anchor'):
        pass
    return Path(path)


def _regular_leaf(fd, name, path, *, allow_symlink=False):
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) and not (allow_symlink and stat.S_ISLNK(info.st_mode)):
        raise _write_error(path, '目标不是普通文件（不写穿链接）')


def check_regular_at(parent_fd, name, *, what=None, allow_symlink=False):
    """锚定目录内叶子条目的写前身份检查（公开原语）。

    契约：条目缺失 = 合法新目标（返回）；存在且为普通文件 = 可原子替换；
    目录/管道等非普通条目 = 拒绝（ProjectPathError）；allow_symlink=True 时
    末段符号链接亦放行——替换换的是目录项本身（目标侧免疫，issue #23/#31，
    账本类目标语义），绝不跟随改写链接受害者。要求 fd 锚定能力。"""
    _regular_leaf(parent_fd, name, Path(what) if what else Path(name),
                  allow_symlink=allow_symlink)


@contextlib.contextmanager
def _legacy_flock_for(path: Path):
    """降级平台的锁守卫（原 main 行为）：锁文件经 ensure_inside_project 判定
    后常规创建/打开。延迟导入防模块期环（infra 顶层已 import fstxn）。"""
    if fcntl is None:   # pragma: no cover - Windows 降级：仅进程内锁
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)   # 锁文件须目录先在
    from .infra import ensure_inside_project
    ensure_inside_project(path.parent / (path.name + ".lock"), path.parent,
                          what="事务锁文件")
    with open(path.parent / (path.name + ".lock"), "a+b") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def flock_at(parent_fd, name, *, what=None):
    """在已锚定目录描述符内安全创建/打开 <name>.lock 并持跨进程排他锁。

    公开契约（issue #59）：O_NOFOLLOW 创建 + 普通文件校验——预置锁符号链接
    一律拒绝（不写穿、不在链接目标侧创建）；fcntl 缺席的平台仅持描述符锁。
    要求 fd 锚定能力（降级平台经 _legacy_flock_for 走 main 锁守卫）。"""
    target = Path(what) if what else Path(str(name) + '.lock')
    lockname = name + '.lock'
    try:
        lockfd = os.open(lockname, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                         0o600, dir_fd=parent_fd)
    except OSError as error:
        raise _write_error(target, f'事务锁文件不安全（{error}）') from error
    try:
        if not stat.S_ISREG(os.fstat(lockfd).st_mode):
            raise _write_error(target, '事务锁文件不是普通文件')
        if fcntl is not None:
            fcntl.flock(lockfd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lockfd, fcntl.LOCK_UN)
    finally:
        os.close(lockfd)


class _Txn:
    """单次文件事务的内部句柄：commit 置位才 os.replace（默认中止——残件在
    _transaction 的 finally 清理，目标保持旧值）。"""

    def __init__(self, target: Path, tmp: Path):
        self.target = target
        self.tmp = tmp
        self.commit = False


@contextlib.contextmanager
def _flock_for(path: Path):
    """目标文件的跨进程排他锁（锁文件 = <目标名>.lock）：fd 锚定平台经锚定
    目录 + flock_at（O_NOFOLLOW 创建）；降级平台经 main 锁守卫
    （ensure_inside_project 判定后常规创建）。持有者与 _transaction 的锁
    互斥（同一 <目标名>.lock inode 的 flock）。"""
    if _legacy_guard():
        with _legacy_flock_for(path):
            yield
        return
    p = _write_target(path)
    with pinned_parent(p) as (parent, name, target):
        with flock_at(parent, name, what=target):
            yield


@contextlib.contextmanager
def _transaction(path, *, allow_symlink=False, staging=False):
    """事务核心：两层锁 + tmp 代管；被包裹方置 txn.commit 后正常退出才替换。

    fd 锚定平台：随机名 O_EXCL tmp + 提交前 inode 对账（tmp 目录项中途被置换
    即拒绝发布）+ dir_fd 原子替换；staging=True 时 tmp 为真实路径（子进程
    写者，见 locked_staging），否则 /proc 锚定（进程内写者）。
    allow_symlink=True：允许末段符号链接目标被原子替换（账本目标侧免疫）。
    降级平台：mkstemp + os.replace + main 锁守卫（原 main 行为）。"""
    p = _write_target(path)
    if _legacy_guard():
        with _thread_lock_for(p), _legacy_flock_for(p):
            fd, name = tempfile.mkstemp(prefix="." + p.name + ".", suffix=".tmp",
                                        dir=p.parent)
            os.close(fd)   # 只借 mkstemp 的唯一名，内容由调用方按需重开写
            txn = _Txn(p, Path(name))
            try:
                yield txn
                if txn.commit:
                    os.replace(txn.tmp, p)
            finally:
                txn.tmp.unlink(missing_ok=True)   # 成功路径已被 replace 移走；失败/中止清残件
        return
    with _thread_lock_for(p), pinned_parent(p) as (parent, name, target):
        with flock_at(parent, name, what=target):
            _regular_leaf(parent, name, target, allow_symlink=allow_symlink)
            tempname = '.' + name + '.' + secrets.token_hex(16) + '.tmp'
            fd = os.open(tempname, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
            txn = _Txn(target,
                       target.parent / tempname if staging else fd_path(fd))
            try:
                yield txn
                if txn.commit:
                    _regular_leaf(parent, name, target, allow_symlink=allow_symlink)
                    if entry_identity(os.stat(tempname, dir_fd=parent, follow_symlinks=False)) != entry_identity(os.fstat(fd)):
                        raise _write_error(target, '事务临时文件身份已变化')
                    os.replace(tempname, name, src_dir_fd=parent, dst_dir_fd=parent)
            finally:
                os.close(fd)
                try:
                    os.unlink(tempname, dir_fd=parent)
                except FileNotFoundError:
                    pass


@contextlib.contextmanager
def locked_write(path):
    """持锁 + 原子写上下文管理器：yield 同目录临时文件 Path（/proc 锚定形态，
    进程内写者专用——见模块头 issue #59 节）。

    with locked_write(p) as tmp:
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    正常退出 → os.replace(tmp, p) 原子落盘并释放两层锁；异常退出 → 清残件、
    目标保持旧值。调用方须实际写 tmp（with 体正常走完即提交 tmp 现有内容，
    未写 = 用空文件替换目标）。"""
    with _transaction(path) as txn:
        yield txn.tmp
        txn.commit = True   # 调用方 with 体正常走完 = 待提交


@contextlib.contextmanager
def locked_staging(path):
    """持锁 + 随机名真实 staging 路径（子进程写者专用：ffmpeg/chrome 等外部
    进程经 argv 接收输出路径）。

    yield 同目录临时文件的真实 Path；正常退出后提交前做 inode 对账（staging
    目录项中途被置换即拒绝发布）+ 原子替换。/proc 锚定路径会被子进程沙箱/
    yama ptrace 拒开，故子进程写者不经 fd_path；随机名不可预置，预置链接
    写穿面不存在（issue #59）。锁序与 locked_write 一致。"""
    with _transaction(path, staging=True) as txn:
        yield txn.tmp
        txn.commit = True


def _default_load(path, default):
    """缺省读取：json.loads；缺失/损坏（OSError/ValueError）回 default 深拷贝
    （与 cache_state.load_receipt 的容错同口径——身份门由调用方经 load 注入）。"""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return copy.deepcopy(default)


def _default_dump(obj) -> str:
    """缺省序列化（与 infra 回执既有落盘格式逐字节同款）。"""
    return json.dumps(obj, ensure_ascii=False, indent=2)


def locked_update(path, mutator, *, load=None, default=None, dump=None):
    """「读-改-写」文件事务：读 → mutator → 原子写回，整段持两层锁。

    - load：自定义读取 (path) -> data（infra 注入 load_receipt 携带指纹门）；
      缺省 _default_load（JSON，缺失/损坏回 default）。
    - mutator(data) -> new_data：纯变更函数；返回 None 中止事务（目标不动）。
    - dump：序列化器，缺省 json.dumps(ensure_ascii=False, indent=2)。
    目标侧免疫（issue #23/#31）：末段预置符号链接允许被原子替换——读侧经
    链接摄入外部内容、落笔为本地真身，链接受害者内容原样不动。
    返回 mutator 的返回值（中止时 None）。崩溃安全见模块头 ②：中断/被杀后
    目标要么旧值要么新值，永不为截断 JSON。"""
    reader = load if load is not None else (lambda p: _default_load(p, default))
    with _transaction(path, allow_symlink=True) as txn:
        new = mutator(reader(path))
        if new is not None:
            txn.tmp.write_text((dump or _default_dump)(new), encoding="utf-8")
            txn.commit = True   # mutator 返回 None = 中止事务（目标不动）
    return new


def unlink(path):
    """Remove a leaf in a pinned directory, never following its ancestors;
    degrade to Path.unlink(missing_ok=True) on platforms without fd writes."""
    if _legacy_guard():
        Path(path).unlink(missing_ok=True)
        return
    p = _write_target(path)
    if not p.parent.exists():
        return
    with pinned_parent(p, create=False) as (parent, name, target):
        try:
            os.unlink(name, dir_fd=parent)
        except FileNotFoundError:
            pass


def replace(source, target):
    """No-follow pinned-parent replacement for already generated owned files
    (mux tmp→final 等)；降级平台等价 os.replace（main 行为）。"""
    if _legacy_guard():
        os.replace(source, target)
        return
    with pinned_parent(source, create=False) as (srcfd, srcname, srcpath):
        with pinned_parent(target) as (dstfd, dstname, dstpath):
            _regular_leaf(srcfd, srcname, srcpath)
            _regular_leaf(dstfd, dstname, dstpath)
            os.replace(srcname, dstname, src_dir_fd=srcfd, dst_dir_fd=dstfd)


def atomic_write(path, data):
    """无锁原子写（tmp + os.replace）：调用方已持锁区段内的伴生件写入用
    （如 _archive_stale_receipt 的 receipt.prev.json——receipt.json 锁的持有
    者独占轮转，无需再拿第二把锁文件）。str 按 UTF-8 编码，bytes 直写。
    目标目录不存在时自动建；写失败抛原始异常（残件在 finally 清理）；
    fd 锚定平台附提交前 inode 对账（issue #59），降级平台为 main 行为。"""
    payload = data if isinstance(data, bytes) else str(data).encode("utf-8")
    p = _write_target(path)
    if _legacy_guard():
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="." + p.name + ".", suffix=".tmp",
                                    dir=p.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
            os.replace(name, p)
        finally:
            Path(name).unlink(missing_ok=True)   # 成功路径已被 replace 移走；失败清残件
        return
    with pinned_parent(p) as (parent, name, target):
        _regular_leaf(parent, name, target)
        tempname = '.' + name + '.' + secrets.token_hex(16) + '.tmp'
        fd = os.open(tempname, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(payload)
            _regular_leaf(parent, name, target)
            if entry_identity(os.stat(tempname, dir_fd=parent, follow_symlinks=False)) != entry_identity(os.fstat(fd)):
                raise _write_error(target, '临时文件身份已变化')
            os.replace(tempname, name, src_dir_fd=parent, dst_dir_fd=parent)
        finally:
            os.close(fd)
            try:
                os.unlink(tempname, dir_fd=parent)
            except FileNotFoundError:
                pass
