"""ffprobe 结论缓存（方案五阶段1 性能修复，2026-09-12）：prepare_layers 温态提速。

动机：区间渲染温态 `--range 0 6` 实测 10.5s，其中 prepare_layers 对 69 层逐层
ffprobe 段文件帧数占 8.7s（83%）。ffprobe 结论在同一 (路径, mtime_ns, size) 下
是确定性的——按该三元组缓存后，温态全部命中零 ffprobe；文件被删/被换/被重写
（mtime_ns 或 size 变）→ 键失配 → 照常 ffprobe，不漏检缓存失效。

两级缓存：
- 进程内 dict（同一进程内重复检查同/异层共享 seg 时免 stat 级重复）；
- 落盘 cache/preview/.probe_cache.json（跨进程温态命中——每次 vtx 运行都是新
  进程，落盘才是温态收益主体；preview 命名空间点文件，不碰正式键产物）。

正确性边界（写入注释，勿破坏）：
- 键含 mtime_ns+size：seg/gfx 产物写完 mtime 必变，旧结论不可能吃到新内容；
- 读取时对每个待查文件重新 stat——文件不存在直接 miss（删除检缺失场景）；
- 只缓存「ffprobe 成功返回的 nb_frames 整数」，失败/异常结果不入缓存（下次重试）。
"""
import json, subprocess, threading

_CACHE_LOCK = threading.Lock()
_MEM = {}          # (path, mtime_ns, size) -> int(nb_frames)
_DISK_LOADED = set()   # 已加载过落盘的 cache 文件路径（每进程每文件一次）


def _disk_path(c):
    return c.cache / "preview" / ".probe_cache.json"


def _load_disk(disk):
    try:
        d = json.loads(disk.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_disk(disk, d):
    try:
        disk.parent.mkdir(parents=True, exist_ok=True)
        tmp = disk.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp.replace(disk)
    except Exception:
        pass   # 缓存落盘失败绝不影响渲染主流程（下次冷查而已）


def ffprobe_nb_frames(c, path):
    """返回视频流帧数 int；ffprobe 失败/无流返回 None（不入缓存）。

    缓存键 = (绝对路径, mtime_ns, size)——温态快捷路径的正确性前提：命中即免
    ffprobe；键失配（文件变更/重建）自动回落真实 ffprobe。"""
    from pathlib import Path
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return None     # 文件不存在：无结论可言（调用方走编码/报错路径）
    key = [str(path.resolve()), str(st.st_mtime_ns), str(st.st_size)]
    k = tuple(key)
    with _CACHE_LOCK:
        if k in _MEM:
            return _MEM[k]
        disk = _disk_path(c)
        if disk not in _DISK_LOADED:
            _DISK_LOADED.add(disk)
            d = _load_disk(disk)
            _MEM.update({tuple(v["k"]): v["n"] for v in d.values()
                         if isinstance(v, dict) and isinstance(v.get("n"), int)
                         and len(v.get("k", [])) == 3})
        if k in _MEM:
            return _MEM[k]
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=nb_frames", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = r.stdout.strip()
    if r.returncode != 0 or not out.isdigit():
        return None
    n = int(out)
    with _CACHE_LOCK:
        _MEM[k] = n
        d = _load_disk(disk)
        d["|".join(key)] = {"k": key, "n": n}
        _save_disk(disk, d)
    return n
