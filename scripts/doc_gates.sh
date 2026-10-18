#!/usr/bin/env bash
# doc_gates.sh — M1 文档七门聚合器(R-22,席 A;M2 席 D 增 leaf_5/leaf_6;M3 席 E 增 leaf_7)。
# LEAVES=7
#
# 保什么:
#   - 七个 leaf 的 rc 全收集,逐行显形(leaf_N: <id> rc=<rc> PASS|FAIL);
#   - 聚合非零语义:任一 leaf FAIL,或尾部对账三数(registry/output/header)不等 → 总 exit 1;
#   - 七个注册 leaf 均必需;目标脚本缺位/空文件 → FAIL,不得以 SKIP 代替执行。
#   - leaf 内允许的可选检查仍由 leaf 自身 rc 判定,不扫描其 warning/SKIP 文本。
# 不保什么:
#   - 散文语义质量(= review 分工,本门只做可机检形态);
#   - C4 极限声明(不在本门判定)。
#   - 保:区内生成物单源一致与三源版本同值;不保:catalog 区外散文与版本号应然值。
#   - 保:三源键面不脱节(leaf_7);不保:schema 本身正确与散文语义=kernel review。
#
# 注册面唯一来源为下方 heredoc:七行,行首即 leaf_,格式 leaf_N:TAG:id:command。
# 目标脚本取命令第二 token 做存在性预检;执行统一 bash -c "<command>",cwd=仓根。

set -u

SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
ROOT="$(cd "$(dirname "$SELF")/.." && pwd)"
cd "$ROOT" || exit 1

TMP="$(mktemp "${TMPDIR:-/tmp}/doc_gates_leaf_lines.XXXXXX")"
trap 'rm -f "$TMP"' EXIT

# leaf 显形行同时上 stdout 与临时账本(TMP 只存 leaf 行,供尾部对账计 output 数)
emit() {
  printf '%s\n' "$*"
  printf '%s\n' "$*" >>"$TMP"
}

REGISTRY="$(cat <<'REG_EOF'
leaf_1:L1:docs_smoke:bash scripts/docs_smoke.sh
leaf_2:L2:doc_budget:python3 scripts/verify_doc_budget.py
leaf_3:L3:repeat_anchors:python3 scripts/verify_repeat_anchors.py
leaf_4:L4:charter_budget:python3 scripts/verify_charter_budget.py
leaf_5:L5:catalog_check:python3 scripts/verify_manifest_catalog.py --check
leaf_6:L6:version_sync:python3 scripts/verify_version_sync.py
leaf_7:L7:contract_surface:python3 scripts/verify_contract_surface.py
REG_EOF
)"

pass=0
fail=0
skip=0

while IFS= read -r line; do
  case "$line" in
    leaf_[0-9]*:*) : ;;
    *) continue ;;
  esac
  seq="${line%%:*}"
  rest="${line#*:}"
  tag="${rest%%:*}"
  rest="${rest#*:}"
  id="${rest%%:*}"
  cmd="${rest#*:}"
  # 目标脚本 = 命令第二个 token(存在性预检)
  target=""
  read -r _ target _ <<<"$cmd"
  if [ -z "$target" ] || [ ! -f "$target" ]; then
    emit "$seq: $id rc=127 FAIL reason=target-missing"
    fail=$((fail + 1))
    continue
  fi
  if [ ! -s "$target" ]; then
    emit "$seq: $id rc=126 FAIL reason=target-empty"
    fail=$((fail + 1))
    continue
  fi
  bash -c "$cmd" </dev/null
  rc=$?
  if [ "$rc" -eq 0 ]; then
    emit "$seq: $id rc=$rc PASS"
    pass=$((pass + 1))
  else
    emit "$seq: $id rc=$rc FAIL"
    fail=$((fail + 1))
  fi
done <<RUN_EOF
$REGISTRY
RUN_EOF

# 尾部对账三数:r=注册面行数(对本文件自身 grep),o=本次输出 leaf 行数,h=头注 LEAVES 值
r="$(grep -cE '^leaf_[0-9]+:' "$SELF" || true)"
o="$(grep -cE '^leaf_[0-9]+:' "$TMP" || true)"
h_line="$(grep -m1 -E '^# LEAVES=[0-9]+$' "$SELF" || true)"
h="${h_line#\# LEAVES=}"

recon="OK"
case "$r" in '' | *[!0-9]*) recon="MISMATCH" ;; esac
case "$o" in '' | *[!0-9]*) recon="MISMATCH" ;; esac
case "$h" in '' | *[!0-9]*) recon="MISMATCH" ;; esac
if [ "$recon" = "OK" ] && { [ "$r" -ne "$o" ] || [ "$o" -ne "$h" ]; }; then
  recon="MISMATCH"
fi

printf 'doc_gates: registry=%s output=%s header=%s 对账=%s; PASS=%s FAIL=%s SKIP=%s\n' \
  "$r" "$o" "$h" "$recon" "$pass" "$fail" "$skip"

if [ "$recon" != "OK" ] || [ "$fail" -gt 0 ]; then
  exit 1
fi
exit 0
