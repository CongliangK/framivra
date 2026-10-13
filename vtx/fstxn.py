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

API（上下文管理器 + mutator 两形态）：
  locked_write(path)            CM：持锁 + 代管同目录 tmp；yield tmp Path，
                                 调用方填充内容，正常退出 os.replace 落盘，
                                 异常退出清残件、目标保持旧值。
  locked_update(path, mutator, *, load=None, default=None, dump=None)
                                 「读-改-写」事务糖：path 经 load 读出（缺省
                                 json.loads，缺失/损坏回 default）→ mutator(data)
                                 → dump 后经 ①② 落盘。mutator 返回 None 中止
                                 事务不写（目标不动）。
  atomic_write(path, data)       无锁原子写（仅 ②）：调用方已持锁区段内的
                                 伴生件写入用（如 receipt 轮转旁证 prev.json，
                                 持 receipt.json 锁的写者天然独占）。

不可重入：mutator/with 体内不得再对同路径调用本模块（threading.Lock 不可
重入即死锁）；对其他路径的嵌套事务须保持全仓一致的获取顺序（receipt 主件
先于 prev 伴生件）。
"""
import contextlib, copy, json, os, tempfile, threading
from pathlib import Path

__all__ = ["locked_write", "locked_update", "atomic_write"]

try:
    import fcntl    # POSIX 跨进程文件锁；Windows 缺该模块（降级见模块头注释）
except ImportError:   # pragma: no cover - Windows 主战场外的降级路径
    fcntl = None

# 进程内 per-path 锁注册表（resolved 路径 → Lock）。CLI 生命周期内路径数有限
# （每项目一份 receipt/manifest/VERSIONS），不淘汰。fstxn 是全部文件事务的
# 唯一入口 → 同路径恒定先线程锁后 flock，跨路径顺序由调用方（见模块头尾注）。
_LOCKS_GUARD = threading.Lock()
_LOCKS = {}


def _thread_lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


@contextlib.contextmanager
def _flock_for(path: Path):
    """目标文件的跨进程排他锁（锁文件 = <目标名>.lock，随目标目录存在）。"""
    if fcntl is None:   # pragma: no cover - Windows 降级：仅进程内锁
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)   # 锁文件须目录先在
    # issue #23（A04）：锁文件同为写路径——open("a+b") 跟随符号链接、目标缺失
    # 即创建，预置 <目标名>.lock → 根外 的符号链接会把锁文件创建/打开导出目标
    # 目录（工单「锁路径也应受护」）。经 infra.ensure_inside_project 统一判定：
    # 解析后必须仍落在目标父目录内（指向内的链接 = 允许）。延迟导入防模块期
    # 环（infra 顶层已 import fstxn；调用期两模块均已初始化完毕，无半初始化面）。
    from .infra import ensure_inside_project
    ensure_inside_project(path.parent / (path.name + ".lock"), path.parent,
                          what="事务锁文件")
    with open(path.parent / (path.name + ".lock"), "a+b") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


class _Txn:
    """单次文件事务的内部句柄：commit 置位才 os.replace（默认中止——残件在
    _transaction 的 finally 清理，目标保持旧值）。"""

    def __init__(self, target: Path, tmp: Path):
        self.target = target
        self.tmp = tmp
        self.commit = False


@contextlib.contextmanager
def _transaction(path):
    """事务核心：两层锁 + tmp 代管；被包裹方置 txn.commit 后正常退出才替换。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)   # tmp/锁文件都须目录先在
    with _thread_lock_for(p), _flock_for(p):
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


@contextlib.contextmanager
def locked_write(path):
    """持锁 + 原子写上下文管理器：yield 同目录临时文件 Path。

    with locked_write(p) as tmp:
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    正常退出 → os.replace(tmp, p) 原子落盘并释放两层锁；异常退出 → 清残件、
    目标保持旧值。调用方须实际写 tmp（with 体正常走完即提交 tmp 现有内容，
    未写 = 用空文件替换目标）。
    """
    with _transaction(path) as txn:
        yield txn.tmp
        txn.commit = True   # 调用方 with 体正常走完 = 待提交


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
    返回 mutator 的返回值（中止时 None）。崩溃安全见模块头 ②：中断/被杀后
    目标要么旧值要么新值，永不为截断 JSON。
    """
    reader = load if load is not None else (lambda p: _default_load(p, default))
    with _transaction(path) as txn:
        new = mutator(reader(path))
        if new is not None:
            txn.tmp.write_text((dump or _default_dump)(new), encoding="utf-8")
            txn.commit = True   # mutator 返回 None = 中止事务（目标不动）
    return new


def atomic_write(path, data):
    """无锁原子写（tmp + os.replace）：调用方已持锁区段内的伴生件写入用
    （如 _archive_stale_receipt 的 receipt.prev.json——receipt.json 锁的持有
    者独占轮转，无需再拿第二把锁文件）。str 按 UTF-8 编码，bytes 直写。
    目标目录不存在时自动建；写失败抛原始异常（残件在 finally 清理）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = data if isinstance(data, bytes) else str(data).encode("utf-8")
    fd, name = tempfile.mkstemp(prefix="." + p.name + ".", suffix=".tmp",
                                dir=p.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        os.replace(name, p)
    finally:
        Path(name).unlink(missing_ok=True)   # 成功路径已被 replace 移走；失败清残件
