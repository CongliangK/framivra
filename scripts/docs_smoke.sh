#!/usr/bin/env bash
# docs_smoke.sh —— 文档命令可执行性冒烟（验收门 3 的机器化清单生成器，v2 P3 创建）。
#
# 扫描范围：README.md + skill/**/*.md + skills/tech-talk/**/*.md + apps/*/README.md + docs/*.md
# （docs/plans* 历史归档豁免）。提取 fenced bash/sh 块中含 python3/python/bash/env/node 的命令行，
# 对含仓库路径形态（apps/ plugins/ scripts/ vtx/ 及根级 vt.py，含 $VIDEO_TOOLS/<video_tools>/
# $VTX/~/.agents/skills/video/ 等前缀与 README 相对 ../ 路径）的行做可执行性冒烟：
#   .py  → --help 优先（截到脚本级加 --help，30s 超时）；无 argparse 面则内存 compile 语法门
#   .sh  → bash -n（init/下载类脚本绝不执行）
#   .mjs → node --check（node 不在则 SKIP）
# 同一脚本去重后只冒烟一次；输出 PASS/FAIL/SKIP 清单，任一 FAIL 退出码 1。
#
# 用法：bash scripts/docs_smoke.sh [--verbose]
set -uo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
VERBOSE="${1:-}"
export DOCS_SMOKE_ROOT="$ROOT" DOCS_SMOKE_VERBOSE="$VERBOSE"
env python3 - <<'PYEOF'
import os, re, subprocess, sys
from pathlib import Path

ROOT = Path(os.environ["DOCS_SMOKE_ROOT"]).resolve()
VERBOSE = os.environ.get("DOCS_SMOKE_VERBOSE", "")

# ---------- 扫描范围（docs/plans* 历史归档豁免） ----------
targets = []
if (ROOT / "README.md").is_file():
    targets.append(ROOT / "README.md")
for pat, base in (("skill/**/*.md", ROOT), ("skills/tech-talk/**/*.md", ROOT),
                  ("apps/*/README.md", ROOT), ("docs/*.md", ROOT)):
    targets.extend(sorted(base.glob(pat)))
targets = sorted(set(t for t in targets if t.is_file()))

FENCE = re.compile(r"^```(bash|sh|shell)\s*$", re.M)
TOKEN = re.compile(
    r"(?:(?:\$\{?VIDEO_TOOLS\}?|<video_tools>|<本仓>|<skill>|~/文档/01_活跃项目/video_tools[\w-]*"
    r"|~/.agents/skills/video)/+)?"                       # 可选前缀（env 占位/挂载/家目录）
    r"((?:\.\./)*(?:apps|plugins|scripts|vtx)/[A-Za-z0-9_./-]+\.(?:py|sh|mjs))"
    r"(?=[\s'\"\\<>|$;#&)]|$)")                           # 仓库路径形态（含 README 相对 ../）
CMD_WORDS = re.compile(r"\b(?:python3?|bash|env|node)\b")

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

# ---------- 提取（fenced bash/sh 块） ----------
found = {}   # (path, method) -> {loc, cmd, n}
fences = 0
for doc in targets:
    text = doc.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    in_fence, fence_start = False, 0
    for i, ln in enumerate(lines, 1):
        if not in_fence and re.match(r"^```(bash|sh|shell)\s*$", ln):
            in_fence, fence_start = True, i; fences += 1; continue
        if in_fence and ln.startswith("```"):
            in_fence = False; continue
        if not in_fence:
            continue
        hit = normalize(ln, doc.parent)
        if hit:
            p, method = hit
            key = (str(p), method)
            rec = found.setdefault(key, {"loc": f"{doc.relative_to(ROOT)}:{i}", "cmd": ln.strip(), "n": 0})
            rec["n"] += 1

# ---------- 冒烟 ----------
results = []
for (path_s, method), rec in sorted(found.items()):
    p = Path(path_s)
    rel = os.path.relpath(p, ROOT)
    tag = f"[{rec['loc']}] ×{rec['n']}"
    if not p.is_file():
        results.append(("FAIL", f"{tag} 路径不存在：{rel}"))
        continue
    if method == "sh":
        r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True, timeout=30)
        results.append(("PASS" if r.returncode == 0 else "FAIL",
                        f"{tag} bash -n {rel}" + ("" if r.returncode == 0 else f" — {r.stderr.strip()[:160]}")))
    elif method == "mjs":
        if subprocess.run(["which", "node"], capture_output=True).returncode != 0:
            results.append(("SKIP", f"{tag} node 不在 PATH：{rel}"))
        else:
            r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True, timeout=30)
            results.append(("PASS" if r.returncode == 0 else "FAIL",
                            f"{tag} node --check {rel}" + ("" if r.returncode == 0 else f" — {r.stderr.strip()[:160]}")))
    else:  # py：--help 优先，失败/超时回落内存 compile
        try:
            r = subprocess.run(["python3", str(p), "--help"], capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                results.append(("PASS", f"{tag} --help {rel}")); continue
            why = f"exit {r.returncode}"
        except subprocess.TimeoutExpired:
            why = "timeout>30s"
        except Exception as e:
            why = str(e)
        try:
            compile(p.read_text(encoding="utf-8", errors="replace"), str(p), "exec")
            results.append(("PASS", f"{tag} compile {rel}（--help 不可用：{why}）"))
        except SyntaxError as e:
            results.append(("FAIL", f"{tag} compile {rel} — 语法错误 {e}"))

# ---------- 报告 ----------
print(f"docs_smoke：扫描 {len(targets)} 份文档 / {fences} 个 bash 围栏 / {len(found)} 个唯一仓库脚本引用\n")
order = {"FAIL": 0, "PASS": 1, "SKIP": 2}
for status, msg in sorted(results, key=lambda x: order[x[0]]):
    print(f"{status:4s} {msg}")
n_fail = sum(1 for s, _ in results if s == "FAIL")
n_pass = sum(1 for s, _ in results if s == "PASS")
n_skip = sum(1 for s, _ in results if s == "SKIP")
print(f"\n结论：{'✅ 全 PASS' if n_fail == 0 else '❌ 存在 FAIL'}（PASS {n_pass} / FAIL {n_fail} / SKIP {n_skip}）")
sys.exit(1 if n_fail else 0)
PYEOF
