#!/usr/bin/env bash
# release.sh —— video_tools 自托管发布流程(唯一发布路径,防漂移)
# 用法: bash scripts/release.sh v2.1.0
# 流程(方案 §4):工作区净检→tag 存在检→git archive 打包→SHA256SUMS→
#   releases.json 更新(线上历史合并)→ssh 建目录+scp 四件→回读验证 curl。
# 纪律:非零即停;只增版本不覆盖既有版本;install.sh 冻结副本随版本入档。
set -euo pipefail

VERSION="${1:-}"
BASE_URL="${VIDEO_TOOLS_DL_BASE:-https://ruochongliang.top/dl/video-tools}"
REMOTE="${VT_RELEASE_REMOTE:-}"
[ -n "$REMOTE" ] || { echo "✗ 未设置发布服务器 ssh 目标:先 export VT_RELEASE_REMOTE=<ssh-target>(自托管下载宿主)"; exit 1; }
DL_ROOT="/var/www/dl/video-tools"
[ -n "$VERSION" ] || { echo "用法: bash scripts/release.sh v2.1.0"; exit 2; }
[[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "✗ 版本串须形如 v2.1.0"; exit 2; }
V_NO_V="${VERSION#v}"

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# 1) 工作区净检
[ -z "$(git status --porcelain)" ] || { echo "✗ 工作区不净(porcelain≠0)——先收口再发布"; exit 1; }
# 1b) pyproject 版本同步义务(leaf_6 活门教训 R-23.FIX:发布版本三源 VERSION/pyproject/releases 必须同值)
[ "$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)" = "${VERSION#v}" ] || { echo "✗ pyproject.toml version 与发布版本 $VERSION 不同步——先同步(防三源异)再发布"; exit 1; }

# 2) tag 存在检
git rev-parse -q --verify "refs/tags/$VERSION" >/dev/null || { echo "✗ tag $VERSION 不存在(先 git tag -a $VERSION -m …)"; exit 1; }
echo "✓ tag $VERSION 在册($(git rev-parse --short "$VERSION^{commit}"))"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 3) git archive 打包(实测保真 symlink,R-23 P0 首项)
git archive "$VERSION" -o "$WORK/video-tools-$V_NO_V.tar.gz"
echo "✓ 打包 $(stat -c %s "$WORK/video-tools-$V_NO_V.tar.gz") bytes"

# 4) SHA256SUMS(线上历史合并 + 新版本条目)
ssh "$REMOTE" "cat $DL_ROOT/SHA256SUMS 2>/dev/null || true" > "$WORK/SHA256SUMS" || true
grep -v "video-tools-$V_NO_V.tar.gz" "$WORK/SHA256SUMS" > "$WORK/SHA256SUMS.new" || true
( cd "$WORK" && sha256sum "video-tools-$V_NO_V.tar.gz" >> SHA256SUMS.new )
mv "$WORK/SHA256SUMS.new" "$WORK/SHA256SUMS"

# 5) releases.json(线上历史合并 + latest=本版本)
ssh "$REMOTE" "cat $DL_ROOT/releases.json 2>/dev/null || true" > "$WORK/releases.json.old" || true
python3 - "$WORK" "$VERSION" <<'PY'
import json, sys
from pathlib import Path
work, version = Path(sys.argv[1]), sys.argv[2]
old = {}
try:
    old = json.loads((work / "releases.json.old").read_text() or "{}")
except Exception:
    pass
rels = old.get("releases", [])
bare = version[1:]                      # releases.json 存裸版本号(2.0.0),URL 由消费方拼 v 前缀
rels = [r for r in rels if r.get("version") != bare]
rels.append({"version": bare,
             "tar": f"v{bare}/video-tools-{bare}.tar.gz",
             "install_sh": f"v{bare}/install.sh"})
work.joinpath("releases.json").write_text(
    json.dumps({"latest": bare, "releases": rels}, ensure_ascii=False, indent=2) + "\n")
PY

# 6) 服务器建目录+上传五件(稳定入口 install.sh/载荷/冻结 install.sh/SHA256SUMS/releases.json)
ssh "$REMOTE" "mkdir -p $DL_ROOT/v$V_NO_V && chmod 755 $DL_ROOT $DL_ROOT/v$V_NO_V"
scp -q scripts/install.sh "$REMOTE:$DL_ROOT/install.sh"
scp -q "$WORK/video-tools-$V_NO_V.tar.gz" "$REMOTE:$DL_ROOT/v$V_NO_V/video-tools-$V_NO_V.tar.gz"
scp -q scripts/install.sh "$REMOTE:$DL_ROOT/v$V_NO_V/install.sh"
scp -q "$WORK/SHA256SUMS" "$REMOTE:$DL_ROOT/SHA256SUMS"
scp -q "$WORK/releases.json" "$REMOTE:$DL_ROOT/releases.json"
ssh "$REMOTE" "chmod 644 $DL_ROOT/install.sh $DL_ROOT/v$V_NO_V/* $DL_ROOT/SHA256SUMS $DL_ROOT/releases.json"

# 7) 回读验证(含稳定入口)
curl -fsS "$BASE_URL/install.sh" | bash -n && echo "✓ 稳定入口 install.sh 语法门通过"
curl -fsS "$BASE_URL/releases.json" | python3 -c 'import json,sys;d=json.load(sys.stdin);print("✓ releases.json latest =",d["latest"])'
curl -fsSI "$BASE_URL/v$V_NO_V/video-tools-$V_NO_V.tar.gz" | head -1
curl -fsS "$BASE_URL/v$V_NO_V/install.sh" | bash -n && echo "✓ 版本冻结 install.sh 语法门通过"
echo "✅ 发布完成: $VERSION → $BASE_URL"
