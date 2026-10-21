"""vtx diff —— 两份 JSON 报告的字段级对比（信息性命令，与 gc/df/snapshot 同类）。

支持 qc_report.json / audit_report.json / subs_check.json 任意两种同构报告：
已知结构做语义化展示（qc checks 状态翻转 ✓→✗、audit indicators 状态变化、
subs_check 的 summary PASS/FAIL 翻转与逐条字幕四项检查变化），未知字段一律
通用 JSON 递归 diff 兜底（叶子值比较）。纯读命令：不写任何产物、不做 receipt。

差异条目（entry）四类：state=状态翻转（显著标出，如 ✓→✗）/ change=值变化
（数值给 旧→新）/ add=新增 / del=删除。末行打印 PASS（无实质差异）或
DIFF（共 N 条）；退出码 PASS=0 / DIFF=1（与 diff 惯例一致）。
"""
import json
import sys
from pathlib import Path

# 状态 → 显示符号（audit 用 ℹ，subs_check 用 ✗，qc 的 pass 是布尔）
_SYM = {"ok": "✓", "warn": "⚠", "info": "ℹ", "fail": "✗", "PASS": "PASS", "FAIL": "FAIL"}

# 四类条目的严重度排序权重（输出按：状态翻转 > 值变化 > 新增 > 删除）
_SEV = {"state": 0, "change": 1, "add": 2, "del": 3}


def _num(v):
    """数值（含布尔除外）原样展示；其余 JSON 单行化截断。"""
    if isinstance(v, str) and len(v) > 40:
        return v[:37] + "…"
    return json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v


def _status_of(v):
    """bool（qc pass）与字符串状态统一转符号，无 recognizer 返回 None。"""
    if isinstance(v, bool):
        return "✓" if v else "✗"
    if isinstance(v, str) and v in _SYM:
        return _SYM[v]
    return None


# ---------------------------------------------------------------- 通用递归 diff
def _walk(path, a, b, out):
    """通用 JSON 递归 diff：dict 按键集、list 按下标、叶子直接比较。
    out 追加 (kind, path, old, new) 四元组。"""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(("add", f"{path}.{k}", None, b[k]))
            elif k not in b:
                out.append(("del", f"{path}.{k}", a[k], None))
            else:
                _walk(f"{path}.{k}", a[k], b[k], out)
    elif isinstance(a, list) and isinstance(b, list):
        for i in range(max(len(a), len(b))):
            if i >= len(a):
                out.append(("add", f"{path}[{i}]", None, b[i]))
            elif i >= len(b):
                out.append(("del", f"{path}[{i}]", a[i], None))
            else:
                _walk(f"{path}[{i}]", a[i], b[i], out)
    else:
        if a != b:
            out.append(("change", path, a, b))


def _generic(root_a, root_b, skip=()):
    """整文档兜底 diff，跳过已语义化处理的顶层键。"""
    out = []
    for k in sorted(set(root_a) | set(root_b)):
        if k in skip:
            continue
        if k not in root_a:
            out.append(("add", k, None, root_b[k]))
        elif k not in root_b:
            out.append(("del", k, root_a[k], None))
        else:
            _walk(k, root_a[k], root_b[k], out)
    return out


# ---------------------------------------------------------------- 语义化 diff
def _by_key(items, key):
    """列表 → {key: item}；键冲突保留首个（防御性，正常报告键唯一）。"""
    d = {}
    for it in items:
        d.setdefault(key(it), it)
    return d


def _diff_named(a, b, out, field):
    """Compare QC checks or audit indicators by name, preserving their policies.

    QC keeps the first duplicate and compares pass truthiness; audit keeps the
    last duplicate and compares raw status values before formatting symbols.
    """
    qc = field == "checks"
    if qc:
        ma = _by_key(a.get(field, []), lambda x: x.get("name", ""))
        mb = _by_key(b.get(field, []), lambda x: x.get("name", ""))
    else:
        ma = {x.get("name", ""): x for x in a.get(field, [])}
        mb = {x.get("name", ""): x for x in b.get(field, [])}
    for name in sorted(set(ma) | set(mb)):
        p = f"{field}[{name}]"
        if name not in ma:
            out.append(("add", p, None, mb[name].get("detail", "")))
        elif name not in mb:
            out.append(("del", p, ma[name].get("detail", ""), None))
        else:
            ca, cb = ma[name], mb[name]
            sa = bool(ca.get("pass")) if qc else ca.get("status")
            sb = bool(cb.get("pass")) if qc else cb.get("status")
            if sa != sb:
                out.append(("state", p, _status_of(sa) or sa, _status_of(sb) or sb))
            if ca.get("detail", "") != cb.get("detail", ""):
                out.append(("change", f"{p}.detail",
                            ca.get("detail", ""), cb.get("detail", "")))
    return {field}


def _diff_subs(a, b, out):
    """subs_check：summary PASS/FAIL 翻转显著标出；results 按 index 配对，
    逐条比较文本/时间与 align/cps/overlap/width 四项的状态与 detail。"""
    if a.get("summary") != b.get("summary"):
        out.append(("state", "summary", a.get("summary"), b.get("summary")))
    ra = {r.get("index", i): r for i, r in enumerate(a.get("results", []))}
    rb = {r.get("index", i): r for i, r in enumerate(b.get("results", []))}
    for idx in sorted(set(ra) | set(rb)):
        p = f"results[#{idx:03d}]"
        if idx not in ra:
            out.append(("add", p, None, rb[idx].get("text", "")))
            continue
        if idx not in rb:
            out.append(("del", p, ra[idx].get("text", ""), None))
            continue
        x, y = ra[idx], rb[idx]
        for k in ("text", "in_s", "out_s"):
            if x.get(k) != y.get(k):
                out.append(("change", f"{p}.{k}", x.get(k), y.get(k)))
        for k in ("align", "cps", "overlap", "width"):
            sa, sb_ = x.get(k, {}), y.get(k, {})
            sta, stb = sa.get("status"), sb_.get("status")
            if sta != stb:
                out.append(("state", f"{p}.{k}",
                            _status_of(sta) or sta, _status_of(stb) or stb))
            if sa.get("detail", "") != sb_.get("detail", ""):
                out.append(("change", f"{p}.{k}.detail",
                            sa.get("detail", ""), sb_.get("detail", "")))
    return {"results", "summary"}


def _detect(rep):
    """报告类型识别（按特征键）：qc / audit / subs / generic。"""
    if isinstance(rep, dict):
        if isinstance(rep.get("checks"), list):
            return "qc"
        if isinstance(rep.get("indicators"), list):
            return "audit"
        if isinstance(rep.get("results"), list) and "summary" in rep:
            return "subs"
    return "generic"


def diff_reports(a, b):
    """两份已加载的报告 dict → 差异条目列表 [(kind, path, old, new)]，
    按 状态翻转 > 值变化 > 新增 > 删除 稳定排序。"""
    kind = _detect(a)
    out = []
    skip = set()
    if kind == "qc" and _detect(b) == "qc":
        skip = _diff_named(a, b, out, "checks")
    elif kind == "audit" and _detect(b) == "audit":
        skip = _diff_named(a, b, out, "indicators")
    elif kind == "subs" and _detect(b) == "subs":
        skip = _diff_subs(a, b, out)
    out += _generic(a, b, skip)
    out.sort(key=lambda e: (_SEV[e[0]], e[1]))
    return out


def run_diff(old_path, new_path, dry=False):
    """CLI 入口：读两份 JSON、打印字段级差异、末行 PASS/DIFF。
    纯读命令：--dry-run 与真实运行行为一致（仅打印说明），不写任何产物。"""
    for p in (old_path, new_path):
        if not Path(p).exists():
            print(f"✗ 报告不存在：{p}"); sys.exit(2)
    try:
        a = json.loads(Path(old_path).read_text(encoding="utf-8"))
        b = json.loads(Path(new_path).read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        print(f"✗ 报告读取/解析失败：{e}"); sys.exit(2)
    print(f"对比：{old_path}  vs  {new_path}（类型：{_detect(a)} / {_detect(b)}）")
    if dry:
        print("  [dry-run] diff 为纯读命令，真实运行输出与此一致")
    entries = diff_reports(a, b)
    for kind, path, old, new in entries:
        if kind == "state":
            print(f"  ⚡ [状态翻转] {path}: {_num(old)} → {_num(new)}")
        elif kind == "change":
            print(f"  · [变化] {path}: {_num(old)} → {_num(new)}")
        elif kind == "add":
            print(f"  + [新增] {path}: {_num(new)}")
        else:
            print(f"  - [删除] {path}: {_num(old)}")
    if entries:
        print(f"DIFF｜共 {len(entries)} 条差异（状态翻转 "
              f"{sum(1 for e in entries if e[0] == 'state')} 条）")
        return 1
    print("PASS｜两份报告无实质差异")
    return 0
