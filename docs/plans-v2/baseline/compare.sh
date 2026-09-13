#!/usr/bin/env bash
# compare.sh —— vtx 基线对比器（P2/P3/P5 复验直接用）。
# 用法: compare.sh <新基线目录> [重锚目录]
#   <新基线目录> 须含与本目录同构的 norm/（由 run-baseline.sh + normalize.py 产出）。
#   [重锚目录]   baseline-r2/（P1 响度终链后的重锚基线）：给出后，凡 r2/norm/ 存在
#                的产物改为对 r2 比较（audio 全部 + qc 含 TP 项），其余仍对本
#                baseline（r1）比较——ARCHITECTURE §9 门 2 两段口径的机器实现。
# 对 5 条命令（validate/diff/subs/qc/audio）的归一化产物逐条比较：
#   .txt 逐字节 cmp；.json 按 known-diffs.conf 白名单豁免/容差后深度比较。
# 输出 PASS/FAIL 汇总；全部一致退出 0，有差异/缺失退出 1（白名单外差异即 FAIL）。
set -u
BASE="$(cd "$(dirname "$0")" && pwd)"
NEW="${1:?用法: compare.sh <新基线目录> [重锚目录]}"
CONF="$BASE/known-diffs.conf"
R2=""
if [ "${2:-}" != "" ]; then
  if [ ! -d "$2/norm" ]; then
    echo "✗ 重锚目录 $2/norm 不存在"
    exit 2
  fi
  R2="$(cd "$2" && pwd)"
fi
if [ ! -d "$NEW/norm" ]; then
  echo "✗ $NEW/norm 不存在（先按 NORMALIZATION.md 产出归一化产物）"
  exit 2
fi
NEW="$(cd "$NEW" && pwd)"

json_cmp() {  # json_cmp <base.json> <new.json> <norm文件名> → 0 一致 / 1 差异
  env python3 - "$1" "$2" "$3" "$CONF" <<'PYEOF'
import json, re, sys

base_f, new_f, name, conf_f = sys.argv[1:5]
drop_keys, drop_items, num_tol = [], [], None
try:
    for line in open(conf_f, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "json-drop-key" and parts[1] == name:
            drop_keys.append(parts[2])
        elif len(parts) >= 5 and parts[0] == "json-drop-items" and parts[1] == name:
            drop_items.append((parts[2], parts[3], re.compile(parts[4])))
        elif len(parts) == 3 and parts[0] == "num-tol" and parts[1] == name:
            num_tol = float(parts[2])
except FileNotFoundError:
    pass  # 无 conf = 全严格

def drop_key(obj, path):
    """按点路径剔除键；路径段支持 * 通配（匹配任意 dict 键 / 展开 list 全部条目）。"""
    keys = path.split(".")
    def rec(cur, i):
        if i == len(keys) - 1:
            if isinstance(cur, dict):
                cur.pop(keys[i], None)
            return
        k = keys[i]
        if k == "*":
            children = list(cur.values()) if isinstance(cur, dict) else \
                       list(cur) if isinstance(cur, list) else []
            for ch in children:
                rec(ch, i + 1)
        elif isinstance(cur, dict) and k in cur:
            rec(cur[k], i + 1)
    rec(obj, 0)

def drop_item(obj, arr_path, field, rx):
    keys = arr_path.split(".")
    cur = obj
    for k in keys[:-1]:
        if not isinstance(cur, dict) or k not in cur:
            return
        cur = cur[k]
    last = keys[-1]
    if isinstance(cur, dict) and isinstance(cur.get(last), list):
        cur[last] = [it for it in cur[last]
                     if not (isinstance(it, dict) and field in it and rx.search(str(it[field])))]

def norm(obj):
    if isinstance(obj, dict):
        return {k: norm(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [norm(v) for v in obj]
    if isinstance(obj, float):
        return round(obj, 6)
    return obj

def diff_paths(a, b, path=""):
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k}: 新增")
            elif k not in b:
                out.append(f"{path}.{k}: 缺失")
            else:
                out.extend(diff_paths(a[k], b[k], f"{path}.{k}"))
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: 长度 {len(a)} vs {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            out.extend(diff_paths(x, y, f"{path}[{i}]"))
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and \
            not isinstance(a, bool) and not isinstance(b, bool):
        if num_tol is not None and abs(float(a) - float(b)) > num_tol:
            out.append(f"{path}: {a} vs {b}（超出容差 {num_tol}）")
        elif num_tol is None and a != b:
            out.append(f"{path}: {a} vs {b}")
    elif a != b:
        out.append(f"{path}: {a!r} vs {b!r}")
    return out

with open(base_f, encoding="utf-8") as f: base = json.load(f)
with open(new_f, encoding="utf-8") as f: new = json.load(f)
for path in drop_keys:
    drop_key(base, path); drop_key(new, path)
for arr, field, rx in drop_items:
    drop_item(base, arr, field, rx); drop_item(new, arr, field, rx)
diffs = diff_paths(norm(base), norm(new))
for d in diffs[:40]:
    print("    " + d)
sys.exit(1 if diffs else 0)
PYEOF
}

overall=0
compared=0
for cmd in validate diff subs qc audio; do
  for ext in txt json; do
    base_f="$BASE/norm/$cmd.norm.$ext"
    new_f="$NEW/norm/$cmd.norm.$ext"
    # 重锚模式：r2 有的产物改对 r2 比较（audio 全部 + qc 含 TP 项）
    if [ -n "$R2" ] && [ -f "$R2/norm/$cmd.norm.$ext" ]; then
      base_f="$R2/norm/$cmd.norm.$ext"
    fi
    # 基线侧没有的产物类型（validate/diff 无 .json）直接跳过
    [ -f "$base_f" ] || continue
    compared=$((compared + 1))
    if [ ! -f "$new_f" ]; then
      echo "FAIL  $cmd.norm.$ext  （新基线缺失）"
      overall=1
      continue
    fi
    if [ "$ext" = "txt" ]; then
      if cmp -s "$base_f" "$new_f"; then
        echo "PASS  $cmd.norm.$ext"
      else
        echo "FAIL  $cmd.norm.$ext"
        diff -u "$base_f" "$new_f" | head -40
        overall=1
      fi
    else
      if json_cmp "$base_f" "$new_f" "$cmd.norm.$ext"; then
        echo "PASS  $cmd.norm.$ext"
      else
        echo "FAIL  $cmd.norm.$ext"
        overall=1
      fi
    fi
  done
done

echo "──"
if [ "$overall" -eq 0 ]; then
  echo "汇总：PASS（$compared 份归一化产物在白名单口径下全部一致）"
else
  echo "汇总：FAIL（存在白名单外差异或缺失，详见上方）"
fi
exit $overall
