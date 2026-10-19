"""引擎接口契约：envelope + payload 两层（PLAN.md BG-D6）。

envelope（全引擎统一、统一版本号 "1"）：
    {"engine": str, "spec_version": "1",
     "sr": int(可选，默认 44100), "channels": int(可选，默认 2),
     "out": str(可选，输出路径), "payload": {...引擎自有...}}

出参契约（render() 返回 dict，必含）：
    path / sr / channels / duration_s / rms_db / engine / determinism
    determinism ∈ {"deterministic", "tolerant", "nondeterministic"}

spec["sr"] 若声明则结果必须相等（R2 验收口径）。

v3 R1-W1 增（PLAN_v3 §3.1 机制 A / BG-D13）：
    描述符注册表 DESCRIPTORS（启动时读 descriptors/*.json，自检结构）+ validate_payload
    （单字段约束描述符驱动；跨字段约束走 INVARIANTS 断言注册表——C13 划界）+ content_hash_key
    （增量渲染 hash 键，经注册表读描述符/binary_pin 版本；签名与键集自 R1 前置接口桩冻结）。
"""

import hashlib
import json
import math
import os
import subprocess
import tempfile

import numpy as np

ENVELOPE_SPEC_VERSION = "1"
ENGINES = {}


class EngineError(Exception):
    pass


def register_engine(name):
    def deco(cls):
        ENGINES[name] = cls
        cls.name = name
        return cls
    return deco


# ---------------------------------------------------------- 描述符注册表（v3 R1-W1）

_DESC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "descriptors")
# 描述符结构检查 = engines/descriptor_check.py 唯一共享实现（与 doctor 同源，
# 杜绝双实现漂移——R1 审查官 B·D1 修复）。
from .descriptor_check import (INVARIANT_IDS,  # noqa: E402
                               check_descriptor_structure)

# 跨字段/条件约束断言注册表（C13：单字段可无上下文求值 → 描述符 parameters；
# 跨字段/条件 → 此处注册，描述符 invariants 条目 check=true 引用）。
_SYNTH_PARAM_KEYS = {
    "kick": (), "snare": (), "hat": ("decay",), "bass": (), "lead": (),
    "fm_ep": ("ratio", "idx", "bright_decay"),
    "nylon": ("bright", "pick", "body", "decay_ref"),
}


def synth_program_param_keys(program):
    """synth 引擎 program 的合法 params 键（invariant synth_params_by_program 的
    数据源；v2 工程层对 track.params.params 嵌套键的同源校验——R2 审查 B·A2，
    编辑层不得写出渲染必失败的工程）。"""
    return tuple(_SYNTH_PARAM_KEYS.get(program, ()))


def _inv_synth_params_by_program(payload, desc):
    prog = payload.get("program")
    allowed = set(_SYNTH_PARAM_KEYS.get(prog, ()))
    params = payload.get("params")
    if params is None:
        return
    if not isinstance(params, dict):
        raise EngineError(f"synth payload 校验失败：params 应为对象：{params!r}")
    unknown = set(params) - allowed
    if unknown:
        raise EngineError(f"synth payload 校验失败：program {prog!r} 不接受 params 键 "
                          f"{sorted(unknown)}（合法：{sorted(allowed)}）")


def _inv_sample_hits_or_notes(payload, desc):
    hits, notes = payload.get("hits"), payload.get("notes")
    if hits is None and notes is None:
        raise EngineError("sample payload 校验失败：缺 hits/notes（或为空）")
    if hits is not None and (not isinstance(hits, list) or not hits):
        raise EngineError("sample payload 校验失败：缺 hits/notes（或为空）")
    if notes is not None:
        if not isinstance(notes, list) or not notes:
            raise EngineError("sample payload 校验失败：缺 hits/notes（或为空）")
        if not payload.get("instrument"):
            raise EngineError("sample payload 校验失败：走 notes 入口时缺 "
                              "instrument（采样文件）")


def _inv_master_matchering_ref(payload, desc):
    if payload.get("mode", "loudnorm") == "matchering":
        ref = payload.get("ref")
        if not ref or not isinstance(ref, str):
            raise EngineError(f"master payload 校验失败：matchering 模式缺 "
                              f"payload.ref：{ref!r}")


INVARIANTS = {
    "synth_params_by_program": _inv_synth_params_by_program,
    "sample_hits_or_notes_entry": _inv_sample_hits_or_notes,
    "master_matchering_ref_required": _inv_master_matchering_ref,
}
assert set(INVARIANTS) == set(INVARIANT_IDS), (
    "INVARIANTS 注册表与 descriptor_check.INVARIANT_IDS 漂移（双向锁）")


def load_descriptors(dir=None):
    """（重新）加载 descriptors/*.json → (DESCRIPTORS, DESCRIPTOR_ERRORS)。

    base 模块 import 时对缺省目录执行一次；doctor/harvest/测试可用显式目录。
    单文件损坏只记录该引擎错误，绝不使整个包 import 崩溃（run_engine 时
    validate_payload 显式报 EngineError）。
    """
    d = dir or _DESC_DIR
    descs, errs = {}, {}
    if not os.path.isdir(d):
        return descs, errs
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        eng = fn[:-5]
        path = os.path.join(d, fn)
        try:
            with open(path, encoding="utf-8") as f:
                desc = json.load(f)
        except Exception as e:                                   # noqa: BLE001
            errs[eng] = f"解析失败：{e}"
            continue
        err = check_descriptor_structure(desc, fn)
        if err:
            errs[eng] = err
            continue
        descs[eng] = desc
    return descs, errs


DESCRIPTORS, DESCRIPTOR_ERRORS = load_descriptors()


def get_descriptor(engine):
    """取引擎描述符（缺失/损坏 → EngineError）。W2 track_projection 数据化复用。"""
    desc = DESCRIPTORS.get(engine)
    if desc is None:
        raise EngineError(
            f"引擎 {engine} 描述符不可用：{DESCRIPTOR_ERRORS.get(engine, '缺失')}"
            f"（bgm_gen/engines/descriptors/{engine}.json）")
    return desc


# -------------------------------------------------------- 描述符驱动 payload 校验

def _check_schema_fragment(engine, path, v, frag):
    """手写解释描述符参数的受限 schema 片段：
    type(any/number/str/object/array) · minimum/maximum/exclusiveMinimum ·
    minItems/maxItems · items · prefixItems（定长行）· properties · required。"""
    if not isinstance(frag, dict):
        return
    ft = frag.get("type")
    if ft == "object" and isinstance(v, dict):
        for req in frag.get("required") or []:
            if req not in v:
                raise EngineError(f"{engine} payload 校验失败：{path} 缺字段 {req}")
        for k, sub in (frag.get("properties") or {}).items():
            if k in v:
                _check_schema_fragment(engine, f"{path}.{k}", v[k], sub)
        return
    # 声明 object 但值不是对象 → 兜底拒绝（R1 审查官 B·B1：原实现静默穿透，
    # 随后引擎抛裸 TypeError，违反字段路径报错口径）
    if ft == "object":
        raise EngineError(f"{engine} payload 校验失败：{path} 应为对象：{v!r}")
    # 数组式检查：片段显式 type=array，或携带 items/prefixItems/minItems/maxItems
    # （notes/hits 片段依赖外层参数 type=tuple/array 定 list 性质，片段本身不重复 type）
    if isinstance(v, list) and (ft == "array" or any(
            k in frag for k in ("items", "prefixItems", "minItems", "maxItems"))):
        if frag.get("minItems") is not None and len(v) < frag["minItems"]:
            raise EngineError(f"{engine} payload 校验失败：{path} 长度 {len(v)} "
                              f"< 最小 {frag['minItems']}")
        if frag.get("maxItems") is not None and len(v) > frag["maxItems"]:
            raise EngineError(f"{engine} payload 校验失败：{path} 长度 {len(v)} "
                              f"> 最大 {frag['maxItems']}")
        prefix, items = frag.get("prefixItems"), frag.get("items")
        if prefix is not None:
            # prefixItems：本数组第 j 个元素按 prefix[j] 校验（JSON Schema 语义）
            for j, cell in enumerate(v):
                _check_schema_fragment(engine, f"{path}[{j}]", cell, prefix[j])
        elif items is not None:
            for i, row in enumerate(v):
                _check_schema_fragment(engine, f"{path}[{i}]", row, items)
        return
    # 声明 array（或携带数组式约束）但值不是数组 → 兜底拒绝（同 B·B1）
    if ft == "array" or any(k in frag for k in
                            ("items", "prefixItems", "minItems", "maxItems")):
        raise EngineError(f"{engine} payload 校验失败：{path} 应为数组：{v!r}")
    num = isinstance(v, (int, float)) and not isinstance(v, bool)
    if ft in ("number", "int", "float"):
        if not num or not math.isfinite(v):
            raise EngineError(f"{engine} payload 校验失败：{path} 应为有限数值：{v!r}")
        if frag.get("minimum") is not None:
            if frag.get("exclusiveMinimum"):
                if not v > frag["minimum"]:
                    raise EngineError(f"{engine} payload 校验失败：{path}={v} "
                                      f"越界（最小 > {frag['minimum']}）")
            elif v < frag["minimum"]:
                raise EngineError(f"{engine} payload 校验失败：{path}={v} "
                                  f"越界（最小 {frag['minimum']}）")
        if frag.get("maximum") is not None and v > frag["maximum"]:
            raise EngineError(f"{engine} payload 校验失败：{path}={v} "
                              f"越界（最大 {frag['maximum']}）")
        return
    if ft == "str" and not isinstance(v, str):
        raise EngineError(f"{engine} payload 校验失败：{path} 应为字符串：{v!r}")
    if ft == "bool" and not isinstance(v, bool):
        raise EngineError(f"{engine} payload 校验失败：{path} 应为布尔：{v!r}")
    # "any"/未识别类型：前向兼容不校验


def _check_param(engine, param, payload):
    """单参数约束：required/type/enum/min-max/schema 片段。"""
    pid = param["id"]
    flags = param.get("flags") or []
    if pid not in payload:
        if "required" in flags:
            raise EngineError(f"{engine} payload 校验失败：缺 {pid}（required）")
        return
    v = payload[pid]
    t = param.get("type", "any")
    num = isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "int":
        if not num or (isinstance(v, float) and not v.is_integer()):
            raise EngineError(f"{engine} payload 校验失败：{pid} 应为整数：{v!r}")
    elif t == "float":
        if not num or not math.isfinite(v):
            raise EngineError(f"{engine} payload 校验失败：{pid} 应为有限数值：{v!r}")
    elif t == "bool":
        if not isinstance(v, bool):
            raise EngineError(f"{engine} payload 校验失败：{pid} 应为布尔：{v!r}")
    elif t == "str":
        if not isinstance(v, str):
            raise EngineError(f"{engine} payload 校验失败：{pid} 应为字符串：{v!r}")
    elif t == "path":
        if not isinstance(v, str) or not v:
            raise EngineError(f"{engine} payload 校验失败：{pid} 应为非空路径字符串：{v!r}")
    elif t == "enum":
        if v not in (param.get("enum") or []):
            raise EngineError(f"{engine} payload 校验失败：{pid} 不在枚举内：{v!r}"
                              f"（合法：{param.get('enum')}）")
    elif t == "object":
        if not isinstance(v, dict):
            raise EngineError(f"{engine} payload 校验失败：{pid} 应为对象：{v!r}")
    elif t in ("array", "tuple"):
        if not isinstance(v, list):
            raise EngineError(f"{engine} payload 校验失败：{pid} 应为数组：{v!r}")
    if num and param.get("min") is not None:
        if param.get("exclusive_min"):
            if not v > param["min"]:
                raise EngineError(f"{engine} payload 校验失败：{pid}={v} 越界"
                                  f"（最小 > {param['min']}）")
        elif v < param["min"]:
            raise EngineError(f"{engine} payload 校验失败：{pid}={v} 越界"
                              f"（最小 {param['min']}）")
    if num and param.get("max") is not None and v > param["max"]:
        raise EngineError(f"{engine} payload 校验失败：{pid}={v} 越界"
                          f"（最大 {param['max']}）")
    _check_schema_fragment(engine, pid, v, param.get("schema"))


def validate_payload(engine, payload):
    """描述符驱动 payload 校验（PLAN_v3 §3.1 / C13 划界）。

    单字段约束（required/type/range/enum）可无上下文求值 → 描述符 parameters 驱动；
    跨字段/条件约束 → INVARIANTS 注册表（描述符 invariants 中 check=true 的条目）。
    文件存在性等环境语义守卫留在引擎内（invariants check=false 文档化）。
    错误统一 EngineError；不注入默认值（默认由引擎 payload.get 应用）。
    """
    desc = get_descriptor(engine)
    payload = payload if isinstance(payload, dict) else {}
    for param in desc.get("parameters") or []:
        _check_param(engine, param, payload)
    for inv in desc.get("invariants") or []:
        if inv.get("check"):
            fn = INVARIANTS.get(inv["id"])
            if fn is None:          # 注册表缺口 = 仓库一致性错误（加载期已拦，双保险）
                raise EngineError(f"invariant {inv['id']!r} 未注册于 INVARIANTS"
                                  f"（{engine}）")
            fn(payload, desc)


def validate_param_value(engine, pid, value):
    """单参数值校验（对参数定义跑 _check_param 的复用封装；v3 R2，PLAN_v3 §3.6）。

    工程层 params 透传槽逐键校验复用（core/project.py）；错误口径 EngineError
    带参数名（引擎 payload 校验同源实现，杜绝双实现漂移）。
    """
    desc = get_descriptor(engine)
    param = next((p for p in desc.get("parameters") or [] if p.get("id") == pid),
                 None)
    if param is None:
        raise EngineError(f"{engine} payload 校验失败：未知参数 {pid}"
                          f"（描述符 parameters 无此项）")
    _check_param(engine, param, {pid: value})


def load_runtime():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "runtime.json")) as f:
        return json.load(f)


# 注意：本文件在 bgm_gen/engines/ 下，须三层 dirname 才到仓库根
#（R1 审查 MAJOR：两层只会到 bgm_gen/，缺省输出曾因此落进包内）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))   # scoreweft 仓库根/


def default_out(*rel):
    """缺省输出路径：<仓库根>/out/<rel>（2026-08-22 迁移：与 cwd 解耦，
    统一 synth/sf2/sfz/sample/master/demucs/ai/render 的落盘口径）。"""
    return os.path.join(REPO_ROOT, "out", *rel)


def _decode_pcm_block(raw, sw):
    """PCM 块 → float64 一维（measure_wav 流式分块用；数值映射与全量版逐样本一致）。"""
    if sw == 2:
        return np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    if sw == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v > 0x7FFFFF, v - 0x1000000, v)
        return v.astype(np.float64) / 8388608.0
    if sw == 4:
        return np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    raise EngineError(f"measure_wav: 不支持的采样宽度 {sw}")


def measure_wav(path):
    """wav 度量：sr/channels/duration_s/rms_db。PCM16/24/32；float wav 走 ffmpeg 转码兜底。

    v3 R4：改为流式分块累计（块 ~0.5M 帧，常驻内存数十 MB 级）——全长
    float64 加载在病态大文件（如失控渲染产物）上会一次吃掉数倍文件体积的
    内存，曾间接致整机冻死。数值口径与全量版一致（float64 平方和）。"""
    import wave
    try:
        with wave.open(path, "rb") as w:
            sr, ch, n, sw = w.getframerate(), w.getnchannels(), \
                w.getnframes(), w.getsampwidth()
            sq_sum, remaining = 0.0, n
            block = 1 << 18
            while remaining > 0:
                raw = w.readframes(min(block, remaining))
                if not raw:
                    break
                remaining -= len(raw) // (ch * sw)
                a = _decode_pcm_block(raw, sw)
                sq_sum += float(np.sum(np.square(a)))
        rms = float(np.sqrt(sq_sum / max(n * ch, 1)))
        return {"sr": sr, "channels": ch, "duration_s": n / sr,
                "rms_db": 20.0 * float(np.log10(max(rms, 1e-12)))}
    except Exception:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        try:                                   # m-1：异常/递归路径均确保临时件清场
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path,
                            "-acodec", "pcm_s16le", tmp.name], check=True)
            return measure_wav(tmp.name)
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)


class BaseEngine:
    name = "?"
    determinism = "unknown"   # deterministic | tolerant | nondeterministic
    summary = ""

    def __init__(self, runtime):
        self.rt = runtime

    def render(self, spec):  # pragma: no cover - 由子类实现
        raise NotImplementedError


def content_hash_key(engine, payload, sr, channels):
    """增量渲染 hash 键（PLAN_v3 §3.3/BG-D12 草案）：组装后 payload 规范化
    JSON + engine 名 + 描述符/binary_pin 版本 + sr/channels。

    v3 R1-W1 已与描述符注册表集成：经 DESCRIPTORS 读 version / binary_pin.version
    （不再逐次读文件）；描述符缺失时对应键为 null——增量缓存只可能多失效一次，
    语义安全。签名与键集自 R1 前置接口桩起冻结（R1-W2 增量渲染直接调用）。
    """
    desc = DESCRIPTORS.get(engine) or {}
    obj = {
        "payload": payload,
        "engine": engine,
        "sr": int(sr),
        "channels": int(channels),
        "engine_version": desc.get("version"),
        "binary_version": (desc.get("binary_pin") or {}).get("version"),
    }
    canonical = json.dumps(obj, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_engine(spec, runtime=None):
    """CLI/编排层统一入口：校验 envelope → validate_payload（描述符驱动）→
    实例化引擎 → 校验出参契约。"""
    spec = dict(spec)
    if str(spec.get("spec_version", ENVELOPE_SPEC_VERSION)) != ENVELOPE_SPEC_VERSION:
        raise EngineError(f"spec_version 不支持：{spec.get('spec_version')}（当前 {ENVELOPE_SPEC_VERSION}）")
    name = spec.get("engine")
    if not name:
        raise EngineError("envelope 缺 engine")
    if name not in ENGINES:
        raise EngineError(f"未注册引擎 {name}；已注册：{sorted(ENGINES)}")
    if "payload" not in spec:
        raise EngineError("envelope 缺 payload")
    validate_payload(name, spec["payload"])
    rt = runtime or load_runtime()
    result = ENGINES[name](rt).render(spec)
    for key in ("path", "sr", "channels", "duration_s", "rms_db"):
        if key not in result:
            raise EngineError(f"引擎 {name} 出参缺 {key}")
    if spec.get("sr") is not None and result["sr"] != int(spec["sr"]):
        raise EngineError(f"引擎 {name} 输出 sr={result['sr']} ≠ spec 声明 {spec['sr']}（BG-D5）")
    result.setdefault("engine", name)
    result.setdefault("determinism", ENGINES[name].determinism)
    return result
