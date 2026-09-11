"""阶段回执溯源：回执绑定输入身份 + 依赖门 + 下游失效（PR#1 与 13bed7a 缓存治理的融合层）。

合并裁定（2026-09-11，PR#1 Windows 适配合入 main）：
  - 放弃 PR 的「项目级全量指纹 + cache/<input-fingerprint>/ 整仓内容寻址」布局：
    每次 CLI 调用全量 sha256 所有源媒体（GB 级 I/O），且与 main 13bed7a 的
    per-产物内容寻址键（hygiene.py gfx/seg/audio 键）+ gc/df/snapshot/manifest
    布局正面冲突。保留 main 的缓存架构。
  - 保留 PR 的回执侧价值：receipt.json 绑定输入身份（输入变 → 不继承旧阶段成功）、
    begin_stage 依赖门（前置阶段未成功即拒绝）、DOWNSTREAM 下游失效表（生产者
    运行前撤销下游成功——失败重跑不得保留旧 QC PASS）。
  - 输入身份 receipt_fingerprint 用「便宜但与缓存键同等敏感」的材料：timeline 字节
    + 源媒体 stat 身份（path+size+mtime_ns+首尾采样哈希，同 hygiene._fp 哲学）+
    gfx html 字节 + 声明依赖（cache_dependencies）内容。任何缓存键会变的输入变化，
    回执门同样重置；不做全量媒体哈希，不做 vtx/*.py 实现哈希（代码变更即全缓存
    失效，开发迭代不可接受——升级渲染器后请显式 `vtx clean`/`gc`）。
"""
import hashlib
import json
from pathlib import Path

SAMPLE_BYTES = 65536   # 源媒体首/尾采样字节数（stat 身份的内容成分）


def file_digest(path) -> str:
    """小文件全量 sha256（html/依赖等 KB 级资产）。"""
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def media_stat_digest(path) -> str:
    """源媒体 stat 身份：size+mtime_ns+首尾 64KB 采样哈希。

    与 hygiene._fp 同一判据（保持回执敏感度 == 缓存键敏感度）。采样补强：
    同尺寸 + 恢复时间戳的同路径替换（PR#1 回归场景）不再误命中——媒体重编码
    首尾字节必变。非对抗性保证：刻意构造首尾不变的中间字节篡改不在覆盖范围
    （PR 文档同口径：这是流程溯源，不是防篡改完整性证明）。
    """
    p = Path(path)
    st = p.stat()
    h = hashlib.sha1()
    if st.st_size <= 2 * SAMPLE_BYTES:
        h.update(p.read_bytes())
    else:
        with p.open('rb') as f:
            h.update(f.read(SAMPLE_BYTES))
            f.seek(st.st_size - SAMPLE_BYTES)
            h.update(f.read(SAMPLE_BYTES))
    return f"{st.st_size}:{st.st_mtime_ns}:{h.hexdigest()[:16]}"


def declared_dependencies(c):
    """project.cache_dependencies 声明表（list[str]，相对项目根）。"""
    deps = c.tl.get("project", {}).get("cache_dependencies", [])
    if not isinstance(deps, list) or not all(isinstance(p, str) for p in deps):
        raise ValueError('project.cache_dependencies must be a list of file paths')
    return deps


def validate_cache_dependencies(c):
    """PR#1：声明的本地依赖必须存在且是文件——Ctx 构造即快速失败。"""
    for item in declared_dependencies(c):
        path = c.root / item
        if not path.is_file():
            raise ValueError(f'cache dependency is not a file: {item}')


def receipt_fingerprint(c) -> str:
    """回执输入身份：timeline 字节 + 源 stat 身份 + gfx html 字节 + 声明依赖内容。"""
    h = hashlib.sha256()
    h.update(c.tl_path.read_bytes())
    for s in sorted(c.tl.get("sources", []), key=lambda s: str(s.get("path", ""))):
        p = c.root / s["path"]
        h.update((str(s.get("path", "")) + ":" +
                  (media_stat_digest(p) if p.is_file() else "MISSING") + "\n").encode())
    for tr in c.tl.get("video_tracks", []):
        if tr.get("kind") != "gfx":
            continue
        for cl in tr.get("clips", []):
            p = c.root / cl["src"]
            h.update((str(cl["src"]) + ":" +
                      (file_digest(p) if p.is_file() else "MISSING") + "\n").encode())
    for item in declared_dependencies(c):
        h.update((item + ":" + file_digest(c.root / item) + "\n").encode())
    return h.hexdigest()


# 生产者运行前撤销其下游旧成功（含失败重跑场景——PR#1 的核心回归修复之一）。
DOWNSTREAM = {
    'prepare': ['audio', 'mux', 'mux_draft', 'qc'],
    'gfx': ['video', 'video_draft', 'mux', 'mux_draft', 'qc'],
    'video': ['mux', 'qc'], 'video_draft': ['mux_draft'],
    'audio': ['mux', 'mux_draft', 'qc'], 'mux': ['qc'],
}


def load_receipt(path, fingerprint):
    """读回执；输入身份不符（或文件缺失/损坏）→ 空阶段表，不继承任何旧成功。"""
    try:
        record = json.loads(Path(path).read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        record = {}
    if record.get('input_fingerprint') != fingerprint:
        record = {'input_fingerprint': fingerprint, 'stages': {}}
    return record
