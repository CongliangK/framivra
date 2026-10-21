#!/usr/bin/env bash
# docs_smoke.sh —— 文档命令可执行性冒烟（验收门 3 的机器化清单生成器，v2 P3 创建）。
#
# 扫描范围：README.md + templates/**/*.md + apps/*/README.md + docs/**/*.md（含 docs/skill）
# （docs/plans* 历史归档豁免：由 collect_targets() 功能过滤，按 docs 根 parts[0] 前缀排除，非子串）。
# 提取 fenced bash/sh 块中含 python3/python/bash/env/node 的命令行，
# 对含仓库路径形态（apps/ plugins/ scripts/ vtx/ 及根级 vt.py，含 $VIDEO_TOOLS/<video_tools>/
# $VTX/~/.agents/skills/video/ 等前缀与 README 相对 ../ 路径）的行做可执行性冒烟：
#   .py  → --help 优先（截到脚本级加 --help，30s 超时）；无 argparse 面则内存 compile 语法门
#   .sh  → bash -n（init/下载类脚本绝不执行）
#   .mjs → node --check（node 不在则 SKIP）
# 洞 1 围栏外 inline 通道：行内码 span/表格/列表行命中 CMD_WORDS+路径形态者按 method 判定表
# 入通道冒烟（输出行缀 inline）；无四前缀 lead 而 TOKEN 命中者 SKIP-登记（计 s，不冒烟不入 K）；
# 上限自检：入通道 n>同次扫描候选（历史锚仅留作来源记录）→ FAIL [selfcheck] 一条。
# 同一脚本去重后只冒烟一次；输出 PASS/FAIL/SKIP 清单 + 登记行 + 结论行。
# exit 语义（形态 A）：FAIL>0 → 1；SKIP>0 或 降级>0 → 0（降级/SKIP 经 ⚠️ 结论行告警，知会形态）。
# --golden <dir>：负例 golden 模式，targets=dir 递归（共用 collect_targets，不施 plans 过滤），
# exit=0 当且仅当四档预期全命中（rc 反转，patch P11 形态①）。
#
# 用法：bash scripts/docs/docs_smoke.sh [--verbose] [--golden <dir>]
set -uo pipefail
# Also required when this leaf runs directly, outside doc_gates.sh.
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
VERBOSE=""; GOLDEN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --verbose) VERBOSE="--verbose"; shift ;;
    --golden) GOLDEN="${2:-}"; if [ -z "$GOLDEN" ]; then echo "--golden 需要目录参数" >&2; exit 2; fi; shift 2 ;;
    --golden=*) GOLDEN="${1#--golden=}"; if [ -z "$GOLDEN" ]; then echo "--golden 需要目录参数" >&2; exit 2; fi; shift ;;
    *) echo "未知参数: $1（用法: bash scripts/docs/docs_smoke.sh [--verbose] [--golden <dir>]）" >&2; exit 2 ;;
  esac
done
SMOKE_BASH="$BASH"
if command -v cygpath >/dev/null 2>&1; then
  SMOKE_BASH="$(cygpath -m "$BASH")"
fi
export DOCS_SMOKE_ROOT="$ROOT" DOCS_SMOKE_VERBOSE="$VERBOSE" DOCS_SMOKE_GOLDEN="$GOLDEN" DOCS_SMOKE_BASH="$SMOKE_BASH"
env python3 - <<'PYEOF'
import os, re, subprocess, sys
from pathlib import Path

ROOT = Path(os.environ["DOCS_SMOKE_ROOT"]).resolve()
VERBOSE = os.environ.get("DOCS_SMOKE_VERBOSE", "")
GOLDEN = os.environ.get("DOCS_SMOKE_GOLDEN", "").strip()

# ---------- 目标收集（单段实现：主面四组 与 --golden 共用；patch P4/P5） ----------
def collect_targets(base: Path, pattern: str, docs_root=None):
    """base 下按 pattern 收集 .md；docs_root 给定时对 base=docs 施加 plans* 前缀豁免
    （按 docs 根 relative parts[0].startswith("plans") 功能过滤，禁注释充数）。"""
    out = sorted(base.glob(pattern))
    if docs_root is not None:
        out = [t for t in out if not t.relative_to(docs_root).parts[0].startswith("plans")]
    return [t for t in out if t.is_file()]

if GOLDEN:
    groot = Path(GOLDEN) if os.path.isabs(GOLDEN) else ROOT / GOLDEN
    groot = groot.resolve()
    if not groot.is_dir():
        print(f"docs_smoke: --golden 目录不存在：{groot}", file=sys.stderr)
        sys.exit(2)
    targets = sorted(set(collect_targets(groot, "**/*.md")))
    plans_exempt = 0
else:
    docs_root = ROOT / "docs"
    targets = []
    if (ROOT / "README.md").is_file():
        targets.append(ROOT / "README.md")
    for pat, base, dr in (("templates/**/*.md", ROOT, None),
                          ("apps/*/README.md", ROOT, None), ("docs/**/*.md", ROOT, docs_root)):
        targets.extend(collect_targets(base, pat, dr))
    targets = sorted(set(t for t in targets if t.is_file()))
    plans_exempt = sum(1 for t in docs_root.glob("**/*.md")
                       if t.relative_to(docs_root).parts[0].startswith("plans"))

FENCE = re.compile(r"^```(bash|sh|shell)\s*$", re.M)
# R5 修:① heads 含 templates(templates/tech-talk/scripts/*.py 等产线脚本入冒烟面);
# ② 行首边界负向后顾 (?<![\w./$-])——路径必须始于词边界,禁止在
# $VIDEO_TOOLS/templates/... 一类长路径中段回退误捕尾部 scripts/xxx.py。
TOKEN = re.compile(
    r"(?<![\w./$-])"
    r"(?:(?:\$\{?VIDEO_TOOLS\}?|<video_tools>|<本仓>|<skill>|~/文档/01_活跃项目/video_tools[\w-]*"
    r"|~/.agents/skills/video)/+)?"                       # 可选前缀（env 占位/挂载/家目录）
    r"((?:\.\./)*(?:apps|plugins|scripts|vtx|templates)/[A-Za-z0-9_./-]+\.(?:py|sh|mjs))"
    r"(?=[\s'\"\\<>|$;#&)]|$)")                           # 仓库路径形态（含 README 相对 ../）
CMD_WORDS = re.compile(r"\b(?:python3?|bash|env|node)\b")
INLINE_SPAN = re.compile(r"`([^`]+)`")
LIST_ROW = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
# method 判定表 lead 词表（patch P3，与围栏内 normalize() 同一词表，不新造语义）：
# env python3|python3|python→py（--help 失败回落 compile）；bash→sh（bash -n）；env node|node→mjs（node --check）
LEAD_PY = re.compile(r"^(?:env\s+)?python3?\b")
LEAD_MJS = re.compile(r"^(?:env\s+)?node\b")
LEAD_SH = re.compile(r"^bash\b")
# 栅栏外候选常量口径（§五）：开工重录实出值（HEAD 3c415b2 / 2026-09-19，提取器全 35 文档表面）。
# 起草锚 11 系 docs/skill/SKILL.md 单文件 grep 行口径，仅对读参考（P1(b) 重锚；留档件 §四）。
INLINE_CAND_ANCHOR = 69
# golden 四档预期冻结表（patch §六 正本，席 2 盲跑唯一口径；开工后作者禁改）
GOLDEN_EXPECT = {
    "dsmoke-golden-bad_path.md": "FAIL",
    "dsmoke-golden-bad_nested.md": "FAIL",
    "dsmoke-golden-good.md": "PASS",
    "dsmoke-golden-plans_like_bad.md": "FAIL",
}

def normalize(line: str, doc_dir: Path):
    """命令行 → (仓库内绝对路径, 方法)；无可冒烟路径返回 None。"""
    if line.lstrip().startswith("#"):
        return None
    if not CMD_WORDS.search(line):
        return None
    s = line.strip().rstrip("\\").strip()
    # $VTX / <VTX> 占位 → vtx/vtx.py（先归一再走统一路径形态匹配）
    s = re.sub(r"\$\{?VTX\}?|<VTX>", "vtx/vtx.py", s)
    m = TOKEN.search(s)
    if m:
        return resolve(m.group(1), doc_dir)
    # 根级入口 vt.py（裸名）
    if re.search(r"(?:^|[\s/])vt\.py(?:\s|$)", s):
        return ROOT / "vt.py", "py"
    return None

def resolve(rel: str, doc_dir: Path):
    rel = rel.strip("/")
    ups = 0
    while rel.startswith("../"):
        rel = rel[3:]; ups += 1
    if ups:
        p = (doc_dir / ("../" * ups) / rel).resolve()
    else:
        p = (ROOT / rel).resolve()
    method = "sh" if rel.endswith(".sh") else ("mjs" if rel.endswith(".mjs") else "py")
    return p, method

def lead_word(cand: str):
    """lead 提取（与判定表同口径）：剥表格/列表行装饰与反引号后取首词。"""
    t = cand.strip()
    t = re.sub(r"^\|+", "", t).strip()          # 表格行装饰
    t = LIST_ROW.sub("", t)                     # 列表行装饰
    return t.lstrip("`").strip()

def lead_ok(cand: str):
    """method 判定表（patch P3）：表格/列表行取 CMD_WORDS 所在子句（行内码 span）lead；
    四前缀（py/sh/mjs 词表）→ True；否则 None→SKIP-登记（如 blender 行，不冒烟不入 K）。"""
    t = lead_word(cand)
    return bool(LEAD_PY.match(t) or LEAD_MJS.match(t) or LEAD_SH.match(t))

def inline_cands(ln: str):
    """洞 1 围栏外语境候选串：行内码 span / 表格行 / 列表行；纯散文不入通道。
    表格/列表行含 span 时逐 span 判定（span 即 CMD_WORDS 所在子句，免双计）。"""
    s = ln.strip()
    spans = INLINE_SPAN.findall(ln)
    is_row = s.startswith("|") or bool(LIST_ROW.match(s))
    if is_row and spans:
        return spans
    if is_row:
        return [s]
    return spans

# ---------- 提取（两阶段：先围栏全集后 inline 分类，防 doc 间交错误分类） ----------
# 阶段 1：围栏内提取（原语义逐字节不动，先建全仓键集 found）
found = {}          # 围栏内 (path,method) -> rec；首 loc 与 ×n 口径不变（条 6 回归保真）
inline_new = {}     # inline 新键（与围栏同键则并入 found locs，不增 K 不动 ×n）
inline_skips = []   # SKIP-登记 (loc, lead)（计 s，不冒烟不入 K）
n_channel = 0       # 入通道 n（dedup 前，四前缀 lead）
n_cand = 0          # 候选（围栏外语境命中 CMD_WORDS∧路径形态，含 SKIP-登记）
fences = 0
for doc in targets:
    doc_rel = str(doc.relative_to(ROOT))
    lines = doc.read_text(encoding="utf-8", errors="replace").splitlines()
    in_fence = False
    for i, ln in enumerate(lines, 1):
        if not in_fence and re.match(r"^```(bash|sh|shell)\s*$", ln):
            in_fence, fences = True, fences + 1
            continue
        if in_fence and ln.startswith("```"):
            in_fence = False
            continue
        if not in_fence:
            continue
        hit = normalize(ln, doc.parent)
        if hit:
            p, method = hit
            key = (str(p), method)
            loc = f"{doc_rel}:{i}"
            rec = found.get(key)
            if rec is None:
                found[key] = {"loc": loc, "locs": [loc], "n": 1,
                              "doc": doc_rel, "method": method, "inline": False}
            else:
                rec["n"] += 1
                rec["locs"].append(loc)

# 阶段 2：洞 1 围栏外 inline 通道（对完整 found 键集分类：同键并入不增 K，异键新冒烟）
for doc in targets:
    doc_rel = str(doc.relative_to(ROOT))
    lines = doc.read_text(encoding="utf-8", errors="replace").splitlines()
    in_fence = False
    for i, ln in enumerate(lines, 1):
        if not in_fence and re.match(r"^```(bash|sh|shell)\s*$", ln):
            in_fence = True
            continue
        if in_fence and ln.startswith("```"):
            in_fence = False
            continue
        if in_fence:
            continue
        for cand in inline_cands(ln):
            hit = normalize(cand, doc.parent)
            if not hit:
                continue
            n_cand += 1
            loc = f"{doc_rel}:{i}"
            if not lead_ok(cand):
                lw = lead_word(cand).split()
                inline_skips.append((loc, lw[0] if lw else "∅"))
                continue
            n_channel += 1
            p, method = hit
            key = (str(p), method)
            if key in found:
                found[key]["locs"].append(loc)   # 与围栏同键：并入不增 K、×n 不动
            else:
                rec = inline_new.get(key)
                if rec is None:
                    inline_new[key] = {"loc": loc, "locs": [loc], "n": 1,
                                       "doc": doc_rel, "method": method, "inline": True}
                else:
                    rec["n"] += 1
                    rec["locs"].append(loc)

combined = dict(found)
combined.update(inline_new)
K = len(combined)
T = sum(len(r["locs"]) for r in found.values()) + sum(len(r["locs"]) for r in inline_new.values())
m = len(inline_new)

def key_token(path_s: str, method: str, rec):
    rel = os.path.relpath(path_s, ROOT)
    return f"key={rel}|{method}" + ("|inline" if rec.get("inline") else "")

key_tokens = {k: key_token(k[0], k[1], rec) for k, rec in combined.items()}

# ---------- 冒烟 ----------
results = []
key_status = {}
n_degraded = 0
for (path_s, method), rec in sorted(combined.items()):
    p = Path(path_s)
    rel = os.path.relpath(p, ROOT)
    suffix = " inline" if rec.get("inline") else ""
    tag = f"[{rec['loc']}] ×{rec['n']}"
    status = None
    if not p.is_file():
        status = "FAIL"
        results.append((status, f"{tag} 路径不存在：{rel}{suffix}"))
    elif method == "sh":
        r = subprocess.run([os.environ["DOCS_SMOKE_BASH"], "-n", p.as_posix()], capture_output=True, text=True, timeout=30)
        status = "PASS" if r.returncode == 0 else "FAIL"
        results.append((status, f"{tag} bash -n {rel}" +
                        ("" if r.returncode == 0 else f" — {r.stderr.strip()[:160]}") + suffix))
    elif method == "mjs":
        if subprocess.run(["which", "node"], capture_output=True).returncode != 0:
            status = "SKIP"
            results.append((status, f"{tag} node 不在 PATH：{rel}{suffix}"))
        else:
            r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True, timeout=30)
            status = "PASS" if r.returncode == 0 else "FAIL"
            results.append((status, f"{tag} node --check {rel}" +
                            ("" if r.returncode == 0 else f" — {r.stderr.strip()[:160]}") + suffix))
    else:  # py：--help 优先，失败/超时回落内存 compile（降级计数，洞 3）
        try:
            r = subprocess.run(["env", "python3", str(p), "--help"], capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                status = "PASS"
                results.append((status, f"{tag} --help {rel}{suffix}"))
            else:
                why = f"exit {r.returncode}"
        except subprocess.TimeoutExpired:
            why = "timeout>30s"
        except Exception as e:
            why = str(e)
        if status is None:
            try:
                compile(p.read_text(encoding="utf-8", errors="replace"), str(p), "exec")
                status = "PASS"
                n_degraded += 1
                results.append((status, f"{tag} compile {rel}（--help 不可用：{why}）{suffix}"))
            except SyntaxError as e:
                status = "FAIL"
                results.append((status, f"{tag} compile {rel} — 语法错误 {e}{suffix}"))
    key_status[(path_s, method)] = status

# ---------- 自检（patch P3 上限 / P9 双向 / 登记行在场；失检=工具故障按 FAIL 计） ----------
selfcheck = []
# Compare channels from the same collected surface; the historical anchor is not a cap.
if n_channel > n_cand:
    selfcheck.append(("FAIL", f"[selfcheck] inline 提取越界 n>候选（{n_channel}>{n_cand}）"))

def md_inventory():
    """在役 .md 清单（P2 三数正本=本桌留档件 §三口径）：排除点目录、cache/、tests/、docs/plans*。"""
    out = []
    for p in ROOT.rglob("*.md"):
        rel = p.relative_to(ROOT)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if rel.parts[0] in ("cache", "tests", "node_modules"):
            continue
        if rel.parts[0] == "docs" and len(rel.parts) > 1 and rel.parts[1].startswith("plans"):
            continue
        out.append(p)
    return out
reg_line = ""
reg_ok = False
if not GOLDEN:
    try:
        n_service = len(md_inventory())
        reg_line = (f"登记: 门面 {len(targets)} / 在役 {n_service} / 未入检 {n_service - len(targets)}(清单=留档件)"
                    f" | T={T} / K={K} / 冒烟 {K}"
                    f" | 栅栏外 候选 {n_cand}（历史锚 {INLINE_CAND_ANCHOR}） / 入通道 {n_channel} / 冒烟 {m} / SKIP-登记 {len(inline_skips)}"
                    f" | 豁免 plans* {plans_exempt} | 降级 {n_degraded}")
        reg_ok = reg_line.startswith("登记: 门面 ")
    except Exception:
        reg_line, reg_ok = "", False
    if not reg_ok:
        selfcheck.append(("FAIL", "[selfcheck] 登记行缺失"))
if VERBOSE and len(set(key_tokens.values())) != K:
    selfcheck.append(("FAIL", "[selfcheck] verbose 键集去重≠K"))
results.extend(selfcheck)

# ---------- 报告 ----------
print(f"docs_smoke:扫描 {len(targets)} 份文档 / {fences} 个围栏 / {K} 个唯一键 / 栅栏外 {n_channel}")
if reg_line:
    print(reg_line)
print()
order = {"FAIL": 0, "PASS": 1, "SKIP": 2}
for status, msg in sorted(results, key=lambda x: order[x[0]]):
    print(f"{status:4s} {msg}")
for loc, lead in inline_skips:
    print(f"SKIP [inline] {loc} lead={lead} 非四前缀形态,登记不冒烟")
n_fail = sum(1 for st, _ in results if st == "FAIL")
n_pass = sum(1 for st, _ in results if st == "PASS")
n_skip = sum(1 for st, _ in results if st == "SKIP")
if n_fail == 0 and n_skip == 0 and n_degraded == 0:
    print(f"\n结论：✅ 全 PASS（PASS {n_pass} / FAIL {n_fail} / SKIP {n_skip}）")
else:
    print(f"\n结论：⚠️ PASS(FAIL {n_fail}/SKIP {n_skip}/降级 {n_degraded})")
if VERBOSE:
    print(f"\n--verbose loc 明细（T={T}）:")
    for key, rec in sorted(combined.items()):
        for loc in rec["locs"]:
            print(f"loc: {loc} {key_tokens[key]}")
if GOLDEN:
    file_status = {}
    rank = {"FAIL": 0, "SKIP": 1, "PASS": 2}
    for key, rec in combined.items():
        st = key_status.get(key)
        if st is None:
            continue
        fname = Path(rec["doc"]).name
        if fname not in file_status or rank[st] < rank[file_status[fname]]:
            file_status[fname] = st
    golden_ok = True
    for fname, exp in sorted(GOLDEN_EXPECT.items()):
        act = file_status.get(fname)
        hit = act == exp
        golden_ok = golden_ok and hit
        print(f"golden {'✓' if hit else '✗'} {fname}: 预期 {exp} / 实出 {act or '无检查行'}")
    print(f"golden 判定:{'4/4 档预期全命中' if golden_ok else '存在未命中档'}")
    sys.exit(0 if golden_ok else 1)
sys.exit(1 if n_fail else 0)
PYEOF
