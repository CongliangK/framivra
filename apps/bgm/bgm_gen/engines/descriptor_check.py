"""描述符结构检查共享实现（engines.base 注册表与 doctor 共用，唯一实现防双漂移）。

R1 审查官 B·D1 修复：doctor 原为弱化子集且 docstring 声称一致失实——本模块
上移为唯一实现。仅标准库依赖（doctor 可直跑脚本模式，系统 python3 亦可）。
"""

import os

_TRACK_ENGINES = ("synth", "sf2", "sfz", "sample")
_DESC_REQUIRED_FIELDS = ("engine", "version", "binary_pin", "parameters",
                         "input_ports", "events_supported", "formats",
                         "invariants", "coverage")
_PARAM_TYPES = ("int", "float", "enum", "bool", "str", "path", "any",
                "object", "array", "tuple")
_EVENT_TYPES = ("cc", "pitch_bend", "keyswitch", "program_change",
                "note_on", "note_off")
# 跨字段/条件约束断言的注册 id（engines.base.INVARIANTS 注册表与之双向锁定）。
INVARIANT_IDS = ("synth_params_by_program", "sample_hits_or_notes_entry",
                 "master_matchering_ref_required")


def check_descriptor_structure(desc, src):
    """描述符自身结构自检。返回错误文本；None=通过。"""
    if not isinstance(desc, dict):
        return "不是 JSON 对象"
    for k in _DESC_REQUIRED_FIELDS:
        if k not in desc:
            return f"缺必填字段 {k}"
    if desc.get("engine") != os.path.splitext(os.path.basename(src))[0]:
        return f"engine 字段 {desc.get('engine')!r} 与文件名不一致"
    if not isinstance(desc.get("binary_pin"), dict):
        return "binary_pin 不是对象"
    params = desc.get("parameters")
    if not isinstance(params, list) or not params:
        return "parameters 缺失或为空"
    ids = [p.get("id") for p in params if isinstance(p, dict)]
    if any(i is None for i in ids) or len(set(ids)) != len(ids):
        return "parameters 缺 id 或 id 不唯一"
    for p in params:
        if p.get("type") not in _PARAM_TYPES:
            return f"参数 {p.get('id')!r} 类型非法：{p.get('type')!r}"
        if p.get("type") == "enum" and not p.get("enum"):
            return f"参数 {p.get('id')!r} 为 enum 但缺枚举清单"
    for ev in desc.get("events_supported") or []:
        if not isinstance(ev, dict) or ev.get("type") not in _EVENT_TYPES:
            return f"events_supported 类型非法：{ev!r}"
    for inv in desc.get("invariants") or []:
        if not isinstance(inv, dict) or not inv.get("id"):
            return f"invariants 条目缺 id：{inv!r}"
        if inv.get("check") and inv["id"] not in INVARIANT_IDS:
            return f"invariant {inv['id']!r} check=true 但未注册于 INVARIANTS"
    cov = desc.get("coverage")
    if not isinstance(cov, dict) or cov.get("tier") not in ("A", "B", "C"):
        return "coverage 缺 tier（A/B/C）"
    if desc.get("engine") in _TRACK_ENGINES and "track_projection" not in desc:
        return f"工程轨引擎 {desc['engine']} 缺 track_projection"
    return None
