"""report.py —— 质量对比报告 schema 与输出(R-20 §3.4/§六.3;修订七/十/十一/十五)。

预注册字段路径(修订十一;schema 冻结须逐字含之,否则判据空转):
  .summary.segSNR_gain_median      .summary.by_family.<f>.segSNR_gain_median
  .records[].segSNR_gain           .summary.stoi_median
  .summary.cer_median              .summary.cer_ins_median
  .summary.pesq_median             .summary.best_relative_gain
  .summary.ab_pref_rate            .summary.likert_gap
  .summary.session_count
计数口径(修订十):#4 = `grep -o '"sample_id"' 报告 | wc -l`;故每记录恰含
1 处 "sample_id" 字面,他处禁用该键名。

null 语义(修订十五两类分拆):
  「临时未测」null = P0–P1 期未测段位,P2 起关闭;
  「结构不适用」= 真实段(A 族)非配对指标(STOI/PESQ/segSNR 及逐段值)永为
  null,schema 标 na_type="n/a",完整性计数(#4 按 sample_id 记录数)不计。

排除规则(修订七.2):漂移嫌疑段(PESQ/STOI 低置信)逐段原始值永在 records,
汇总中位数排除低置信段并单列 excluded_segments;排除率 >30%(EXCLUDE_MAX)
→ measurement_valid=false(该轮测量无效,整轮重测)。

解释限定条款(修订七.4,入报告生成逻辑):跨族(chain_family 异)PESQ/STOI
差值不得单独作为「量化提升」结论依据,须与同族内基线差/DNSMOS/样听三源合读
——interpretation 字段随报告恒载。
"""
import hashlib
import json
import math
import statistics
from pathlib import Path

NA = "n/a"  # 结构不适用标记(修订十五)

EXCLUDE_MAX = 0.30  # 排除率上限(修订七.2;超限整轮无效)

# 汇总字段预注册路径(修订十一;DOTTED 供 schema 自检与 jq 路径核对)
PRE_REGISTERED_PATHS = (
    "summary.segSNR_gain_median", "summary.by_family", "summary.stoi_median",
    "summary.cer_median", "summary.cer_ins_median", "summary.pesq_median",
    "summary.best_relative_gain", "summary.ab_pref_rate", "summary.likert_gap",
    "summary.session_count",
)

INTERPRETATION_LIMIT = (
    "跨族 PESQ/STOI 差值不得单独作为「量化提升」结论依据;须与同族内基线差、"
    "DNSMOS 跨族仲裁、样听双件套三源合读(修订七.4)。"
)


def _median(vals):
    vals = [v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return round(statistics.median(vals), 5) if vals else None


def new_report(meta: dict) -> dict:
    """空白报告:meta 至少含 {report_kind, stage, roster_sha256, chains:[{chain_id,
    chain_family}]};chain_family 值域 = 生成式|滤镜族(修订七.1 必选字段)。"""
    for key in ("report_kind", "roster_sha256", "chains"):
        if key not in meta:
            raise ValueError(f"meta 缺必选 {key}")
    for ch in meta["chains"]:
        if "chain_family" not in ch:
            raise ValueError(f"chain {ch} 缺 chain_family(修订七.1 必选)")
    return {
        "schema": "denoise-quality-report/1.0(P0 定稿;修订十/十一/十五口径)",
        "meta": dict(meta, interpretation_limit=INTERPRETATION_LIMIT),
        "records": [],
        "summary": {
            "segSNR_gain_median": None, "by_family": {}, "stoi_median": None,
            "cer_median": None, "cer_ins_median": None, "pesq_median": None,
            "best_relative_gain": None, "ab_pref_rate": None, "likert_gap": None,
            "session_count": None,
            "excluded_segments": [], "exclusion_rate": None,
            "measurement_valid": None,
        },
    }


def add_record(report: dict, *, sample_id: str, chain_id: str, chain_family: str,
               family_type: str, paired: bool, metrics: dict, confidence: dict = None,
               chain_ref: str = "baseline") -> None:
    """追加一条记录(每段×链一条;链由 chain_id+chain_ref 区分)。

    family_type=real_a 且非配对 → STOI/PESQ/segSNR 系字段恒 null 且 na_type="n/a"
    (修订十五「结构不适用」);合成段(paired=True)全指标必填(None 仅限
    「临时未测」且 P2 起关闭)。
    """
    rec = {
        "sample_id": sample_id,
        "chain_id": chain_id,
        "chain_family": chain_family,
        "chain_ref": chain_ref,
        "family_type": family_type,
        "paired": bool(paired),
        "segSNR": None, "segSNR_gain": None, "stoi": None, "pesq": None,
        "lsd": None, "bandwidth_shrink_hz": None,
        "noise_floor_dbfs": None, "noise_floor_delta_db": None,
        "cer": None, "cer_ins": None,
        "confidence": dict(confidence or {}),
        "na_fields": [],
    }
    paired_metrics = ("segSNR", "stoi", "pesq")
    for k, v in metrics.items():
        if k in rec:
            rec[k] = v
    if family_type == "real_a" and not paired:
        for k in paired_metrics:
            if rec[k] is not None:
                raise ValueError(f"{sample_id}: 真实段非配对指标 {k} 须为 null(修订十五)")
        rec["na_fields"] = list(paired_metrics) + ["segSNR_gain"]
    report["records"].append(rec)


def finalize(report: dict) -> dict:
    """汇总行:配对段全链记录出三维中位;分族行;排除与有效门;样听位留空。

    segSNR_gain 语义:同段同链 vs 基线链同段记录之差(基线记录自身 =0,
    由调用方以 chain_ref=baseline 标注);合并中位 + by_family 分族行
    (修订九:每族中位下限判据的机检载体)。
    """
    recs = report["records"]
    base_by_sample = {r["sample_id"]: r for r in recs if r.get("chain_ref") == "baseline"}
    for r in recs:
        if r.get("chain_ref") == "baseline" or not r["paired"]:
            continue
        b = base_by_sample.get(r["sample_id"])
        if b and b["segSNR"] is not None and r["segSNR"] is not None:
            r["segSNR_gain"] = round(r["segSNR"] - b["segSNR"], 4)
    low_conf = [r for r in recs if r["confidence"].get("suspicious")]
    excluded_ids = sorted({r["sample_id"] for r in low_conf})
    paired_recs = [r for r in recs if r["paired"] and r["sample_id"] not in excluded_ids]
    s = report["summary"]
    s["excluded_segments"] = excluded_ids
    s["exclusion_rate"] = (round(len(excluded_ids) / max(1, len({r["sample_id"] for r in recs if r["paired"]})), 4)
                           if recs else None)
    s["measurement_valid"] = bool((s["exclusion_rate"] or 0) <= EXCLUDE_MAX)
    s["segSNR_gain_median"] = _median([r["segSNR_gain"] for r in paired_recs])
    s["stoi_median"] = _median([r["stoi"] for r in paired_recs])
    s["pesq_median"] = _median([r["pesq"] for r in paired_recs])
    s["cer_median"] = _median([r["cer"] for r in recs if r.get("cer") is not None])
    s["cer_ins_median"] = _median([r["cer_ins"] for r in recs if r.get("cer_ins") is not None])
    by_family = {}
    for fam in {r["chain_family"] for r in paired_recs}:
        fr = [r for r in paired_recs if r["chain_family"] == fam and r.get("chain_ref") != "baseline"]
        by_family[fam] = {"segSNR_gain_median": _median([r["segSNR_gain"] for r in fr]),
                          "stoi_median": _median([r["stoi"] for r in fr]),
                          "pesq_median": _median([r["pesq"] for r in fr]),
                          "n": len(fr)}
    s["by_family"] = by_family
    gains = [r["segSNR_gain"] for r in paired_recs if r.get("chain_ref") != "baseline"]
    stoi_b = _median([r["stoi"] for r in paired_recs if r.get("chain_ref") == "baseline"])
    pesq_b = _median([r["pesq"] for r in paired_recs if r.get("chain_ref") == "baseline"])
    rel = []
    if s["segSNR_gain_median"] is not None and stoi_b:
        rel.append(s["segSNR_gain_median"] / 10.0)  # dB→相对:10 dB ≈ ×10 功率,归一尺度
    if s["stoi_median"] is not None and stoi_b:
        rel.append((s["stoi_median"] - stoi_b) / stoi_b)
    if s["pesq_median"] is not None and pesq_b:
        rel.append((s["pesq_median"] - pesq_b) / pesq_b)
    s["best_relative_gain"] = round(max(rel), 5) if rel else None
    return report


def validate(report: dict) -> list:
    """schema 自检(报告写盘前必跑):预注册路径在位/计数口径/n-a 规则/排除门。
    返回违例列表(空=过)。"""
    errs = []
    root = report
    for path in PRE_REGISTERED_PATHS:
        node = root
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                errs.append(f"预注册路径缺失: {path}(修订十一)")
                break
            node = node[part]
    ids = [r.get("sample_id") for r in report["records"]]
    if any(not i for i in ids):
        errs.append("记录缺 sample_id(修订十计数口径)")
    s = report["summary"]
    if s.get("measurement_valid") is False:
        errs.append(f"排除率 {s.get('exclusion_rate')} > {EXCLUDE_MAX}:整轮无效(修订七.2)")
    for r in report["records"]:
        if r["family_type"] == "real_a" and not r["paired"]:
            for k in ("segSNR", "stoi", "pesq"):
                if r.get(k) is not None:
                    errs.append(f"{r['sample_id']}: 真实段 {k} 非 null(修订十五)")
    if report["meta"].get("interpretation_limit") != INTERPRETATION_LIMIT:
        errs.append("解释限定条款缺失(修订七.4)")
    return errs


def dump_bytes(report: dict) -> bytes:
    """规范序列化(§六.4:编码/排序/空白成文):UTF-8/键序 sort/indent 2/LF。"""
    return (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_report(report: dict, path) -> str:
    """校验→规范写盘→返回 sha256(报告哈希在档;宣称哈希即此)。"""
    errs = validate(report)
    if errs:
        raise RuntimeError("report.validate 未过:\n  " + "\n  ".join(errs))
    data = dump_bytes(report)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def count_sample_ids(path) -> int:
    """#4 复算形态(修订十):grep -o '"sample_id"' | wc -l 的等价实现。"""
    import re

    return len(re.findall(r'"sample_id"', Path(path).read_text(encoding="utf-8")))
