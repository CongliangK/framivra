"""响度测量回执共享（方案三阶段1，2026-09-13）：同一解码域输入全链只测一次。

动机：同一成片的 I/TP 在 qc 响度项与 gates G2/G3 各测一遍（解码域全片 ebur128，
bench_p2 各 ~0.4-5s）——按输入内容键存档回执后，先测者归档、后测者免测。
跨域不共享（总控裁定）：post_loudness 的 wav 域测量不入本回执——DEV.md 明言
wav 域 TP 不算数（原生 aac 真峰过冲教训），跨域共享危险且它只 2.5s。

回执文件：<项目>/cache/audio/loudness_receipts.json
  {"entries": {key: {"path","size","mtime_ns","digest","i_lufs","tp_dbfs","when"}}}
键 = sha1(f"{path}|{size}|{mtime_ns}|{digest}")[:12]——digest 用
cache_state.media_stat_digest（首尾 64KB 采样哈希，与 hygiene._fp 同判据）。
文件任何变动（size/mtime_ns/内容采样）→ 键变 → 自然失效重测，不跨版本复用数字。

API 不依赖 Ctx（scripts/content_gates.py 侧复用）：
  lookup(path, root=None) -> dict|None
  measure_and_store(path, run_fn, root=None) -> dict
run_fn() -> (i_lufs, tp_dbfs)：实际测量由调用方组装（通常是一条 ffmpeg ebur128），
模块只负责键计算/归档；i 或 tp 为 None（测量失败/缺峰）不入回执。写入原子
（tmp + os.replace），进程内 threading.Lock 防并发读-改-写丢笔（对齐
hygiene._MANIFEST_LOCK 惯例）；跨进程一致性由 replace 原子性兜底。

跨进程互斥（方案三阶段3，2026-09-13，M4 依赖编排核对的实质发现）：qc 与
content_gates 是两个进程，都会对本文件做「读-改-写 + os.replace」——4K 导出与
qc/gates 并行的发布链场景下，两进程各自基于旧快照回写会丢对方的笔（replace 原子
只保证文件不撕裂，不保证读-改-写串行化；threading.Lock 管不到跨进程）。故写入段
再持 loudness_receipts.json.lock 的 flock 排他锁（锁文件随回执目录存在；获取-写-
释放，fd 关闭即释放，异常不残留锁态——flock 语义随 fd 生命周期）。Windows 无
fcntl 时降级为仅进程内锁（本仓跨平台待验项目，Linux 为主战场）。
root 缺省 = media 父目录的父目录（out/final.mp4 → 项目根）。
"""
import hashlib, json, os, threading, time
from pathlib import Path

from cache_state import media_stat_digest

try:
    import fcntl    # POSIX 跨进程文件锁；Windows 缺该模块（降级见模块头注释）
except ImportError:   # pragma: no cover - Windows 主战场外的降级路径
    fcntl = None

_LOCK = threading.Lock()


def _receipts_path(media: Path, root=None) -> Path:
    base = Path(root) if root is not None else Path(media).resolve().parent.parent
    return Path(base) / "cache" / "audio" / "loudness_receipts.json"


def _key_of(path: Path):
    """(key, entry 素材) 或 None（文件不存在/不可 stat——无结论可言）。"""
    try:
        st = path.stat()
        digest = media_stat_digest(path)
    except OSError:
        return None
    key = hashlib.sha1(
        f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}|{digest}".encode()
    ).hexdigest()[:12]
    return key, {"path": str(path.resolve()), "size": st.st_size,
                 "mtime_ns": st.st_mtime_ns, "digest": digest}


def _load(rp: Path) -> dict:
    try:
        d = json.loads(rp.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("entries"), dict) \
            else {"entries": {}}
    except (OSError, ValueError):
        return {"entries": {}}


def _flock_path(rp: Path) -> Path:
    """跨进程锁文件路径（随回执目录存在，见模块头注释）。"""
    return rp.parent / (rp.name + ".lock")


def _write_entry_locked(rp: Path, key: str, entry: dict):
    """跨进程互斥的读-改-写（方案三阶段3）：threading.Lock 管进程内并发（同进程
    qc 与 gates 不会并存，此锁防的是未来进程内多线程消费），flock 管跨进程——
    qc 与 content_gates 两进程同时归档时，无 flock 则后写者基于旧快照回写会丢
    对方的笔。锁获取-写-释放全在 with 块内；flock 随 fd 关闭自动释放，异常路径
    也不残留锁态。落盘失败（OSError）不影响测量主流程（下次重测而已）。"""
    with _LOCK:
        if fcntl is not None:
            rp.parent.mkdir(parents=True, exist_ok=True)   # 锁文件须目录先在
            with open(_flock_path(rp), "a+b") as lf:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
                try:
                    _store(rp, key, entry)
                finally:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
        else:
            _store(rp, key, entry)


def _store(rp: Path, key: str, entry: dict):
    """无锁回执写入（调用方 _write_entry_locked 已持锁）：读-改-写 + tmp+replace。"""
    data = _load(rp)
    data["entries"][key] = entry
    try:
        rp.parent.mkdir(parents=True, exist_ok=True)
        tmp = rp.parent / (rp.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, rp)
    except OSError:
        pass   # 回执落盘失败不影响测量主流程（下次重测而已）


def lookup(path, root=None):
    """按内容键查回执；命中返回 entry dict（含 i_lufs/tp_dbfs），未命中/文件缺失
    /条目残缺返回 None（调用方回落真实测量）。只读不写：写入侧 tmp+os.replace
    原子替换保证读者看到的要么是旧文件要么是新文件，无须参与写锁。"""
    path = Path(path)
    k = _key_of(path)
    if k is None:
        return None
    with _LOCK:
        e = _load(_receipts_path(path, root))["entries"].get(k[0])
    if isinstance(e, dict) and isinstance(e.get("i_lufs"), (int, float)) \
            and isinstance(e.get("tp_dbfs"), (int, float)):
        return e
    return None


def measure_and_store(path, run_fn, root=None):
    """测量并归档：run_fn() -> (i_lufs, tp_dbfs)。失败（i/tp 为 None 或文件不可
    stat）不落回执（下次重测）。返回 {"i_lufs": ..., "tp_dbfs": ...}。"""
    path = Path(path)
    k = _key_of(path)
    i, tp = run_fn()
    if k is None or i is None or tp is None:
        return {"i_lufs": i, "tp_dbfs": tp}
    entry = dict(k[1], i_lufs=float(i), tp_dbfs=float(tp),
                 when=time.strftime("%F %T"))
    _write_entry_locked(_receipts_path(path, root), k[0], entry)
    return {"i_lufs": i, "tp_dbfs": tp}
