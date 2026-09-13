"""vtx.api —— 内核稳定公共面（v2 §1.1 条目 8，供 apps/plugins/skills 复用）。

稳定性承诺：本模块的每个导出都是**内核 API 面**——签名与语义变更须过对抗审查
（ARCHITECTURE §0 修改纪律），不做静默破坏性变更。外部组件（apps/plugins/
skills/tests）一律经本模块或 `from vtx import ...` 使用内核能力，禁止各自
sys.path hack（§1.4 双模块裂分防护）。

导出分组：
  Ctx                     渲染上下文（timeline 加载/fps 契约/回执读写入口）
  media_stat_digest       源媒体 stat 身份（缓存键/回执指纹的公共判据）
  DOWNSTREAM              阶段下游失效表（回执撤销语义的权威数据）
  load_receipt            回执读取（输入身份不符 → 空表，不继承旧成功）
  loudness_cache.lookup / measure_and_store
                          响度回执（跨进程 flock，同一解码域输入全链只测一次）
  gfx_key / gfx_dir       gfx 内容寻址键与帧目录定位（与渲染器永远同一套键）
  load_timeline_check     timeline 加载 + 契约校验（validate 的加载段封装）
  read_receipt_atomic / write_receipt_atomic
                          回执原子读写助手（tmp + os.replace 语义，插件回执用）
"""
import json, os, tempfile
from pathlib import Path

from .infra import Ctx
from .cache_state import DOWNSTREAM, load_receipt, media_stat_digest
from .loudness_cache import lookup, measure_and_store
from .hygiene import gfx_key, gfx_dir
from .stages import load_timeline_check

__all__ = [
    "Ctx", "DOWNSTREAM", "load_receipt", "media_stat_digest",
    "lookup", "measure_and_store", "gfx_key", "gfx_dir", "load_timeline_check",
    "read_receipt_atomic", "write_receipt_atomic",
]


def read_receipt_atomic(path):
    """回执 JSON 原子读：os.replace 语义保证读者看到的是旧文件或新文件整体，
    不会读到撕裂态。缺失/损坏 → {}（与 cache_state.load_receipt 的容错一致；
    不做指纹比对——身份校验由调用方用 load_receipt(path, fingerprint) 自理）。

    稳定性承诺：内核 API 面，变更须过对抗审查。"""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_receipt_atomic(path, data):
    """回执 JSON 原子写：同目录 tmp + os.replace（写者崩溃不留半写文件；读者永不
    看到撕裂态；replace 只换目录项，旧 inode 若被硬链接共享则内容原样保留——
    mux 轮转历史同款语义）。目录不存在时自动建；序列化失败抛原始异常。

    稳定性承诺：内核 API 面，变更须过对抗审查。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + p.name + ".", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(name, p)
    finally:
        Path(name).unlink(missing_ok=True)   # 成功路径已被 replace 移走；失败清残件
