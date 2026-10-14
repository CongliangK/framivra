#!/usr/bin/env bash
# build_cef_worker.sh — vtx_cef_worker 可复现构建入口（issue #108）
#
# 职责：按 cef_lock.json 获取官方 CEF Minimal 分发包（sha256/sha1 双校验，仓外缓存），
#       解包到本目录（.gitignore 覆盖的 cef_binary*/），CMake+Ninja 编译
#       libcef_dll_wrapper + vtx_cef_worker（C++20），把 CEF 运行时布置到 build/bin
#       与可执行同目录（RPATH=$ORIGIN），最后 ldd 无未解析 + sanity 自检。
#
# 用法：bash experiments/gfx_cef_worker/build_cef_worker.sh
# 幂等：缓存命中不重复下载；已解包不重复解包；重复构建走 Ninja 增量。
#
# 环境变量：
#   CEF_CACHE_DIR  tarball 缓存目录（默认 ~/.cache/cef，仓外，不污染检出）
#   CEF_DL_PROXY   直连失败时设为代理，如 http://127.0.0.1:7890
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LOCK_FILE="$SCRIPT_DIR/cef_lock.json"
BUILD_DIR="$SCRIPT_DIR/build"
BIN_DIR="$BUILD_DIR/bin"
CACHE_DIR="${CEF_CACHE_DIR:-$HOME/.cache/cef}"

log() { printf '[build_cef_worker] %s\n' "$*"; }
fail() { printf '[build_cef_worker] FAIL: %s\n' "$*" >&2; exit 1; }

for tool in python3 cmake ninja curl sha256sum sha1sum; do
  command -v "$tool" >/dev/null 2>&1 || fail "缺少工具: $tool"
done
[ -f "$LOCK_FILE" ] || fail "锁文件缺失: cef_lock.json"

# ---------- 1. 解析版本锁 ----------
lock_fields="$(python3 - "$LOCK_FILE" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    lock = json.load(fh)
need = ("cef_version", "chromium_version", "archive", "url",
        "size_bytes", "sha256", "sha1_official_index")
missing = [key for key in need if not lock.get(key)]
if missing:
    sys.exit("cef_lock.json 缺字段: %s" % missing)
print(lock["cef_version"], lock["chromium_version"], lock["archive"],
      lock["url"], lock["size_bytes"], lock["sha256"], lock["sha1_official_index"])
PY
)" || fail "cef_lock.json 解析失败"
read -r CEF_VERSION CHROMIUM_VERSION ARCHIVE URL SIZE_BYTES SHA256 SHA1 <<<"$lock_fields"
log "锁定 CEF: $CEF_VERSION (chromium $CHROMIUM_VERSION)"

# ---------- 2. 获取分发包（缓存命中跳过下载）----------
ARCHIVE_PATH="$CACHE_DIR/$ARCHIVE"
if [ -f "$ARCHIVE_PATH" ] && echo "$SHA256  $ARCHIVE_PATH" | sha256sum -c --quiet >/dev/null 2>&1; then
  log "缓存命中（sha256 一致，跳过下载）: $ARCHIVE_PATH"
else
  mkdir -p "$CACHE_DIR"
  log "下载: $URL"
  if [ -n "${CEF_DL_PROXY:-}" ]; then
    export https_proxy="$CEF_DL_PROXY" http_proxy="$CEF_DL_PROXY"
    log "使用代理: $CEF_DL_PROXY"
  fi
  rm -f "$ARCHIVE_PATH.part"
  curl -fL --retry 3 --retry-delay 2 -C - \
    --connect-timeout 15 --speed-limit 10240 --speed-time 60 \
    -o "$ARCHIVE_PATH.part" "$URL"
  echo "$SHA256  $ARCHIVE_PATH.part" | sha256sum -c --quiet \
    || fail "sha256 校验失败（分发包与锁不符）"
  [ "$(stat -c%s "$ARCHIVE_PATH.part")" -eq "$SIZE_BYTES" ] \
    || fail "分发包大小与锁不符: 期望 $SIZE_BYTES"
  mv -f "$ARCHIVE_PATH.part" "$ARCHIVE_PATH"
  log "下载完成并落缓存: $ARCHIVE_PATH"
fi
echo "$SHA1  $ARCHIVE_PATH" | sha1sum -c --quiet \
  || fail "sha1 交叉校验失败（与 cef-builds 官方索引不一致）"

# ---------- 3. 解包（幂等：已有 cef_binary*/ 且含 libcef.so 即跳过）----------
shopt -s nullglob
CEF_ROOT=""
for candidate in "$SCRIPT_DIR"/cef_binary*/; do
  if [ -f "$candidate/Release/libcef.so" ]; then
    CEF_ROOT="${candidate%/}"
    break
  fi
done
if [ -n "$CEF_ROOT" ]; then
  log "已解包，跳过: $CEF_ROOT"
else
  log "解包: $ARCHIVE_PATH"
  top_dir="$(python3 - "$ARCHIVE_PATH" <<'PY'
import tarfile, sys
with tarfile.open(sys.argv[1], "r:bz2") as tf:
    name = tf.next().name
print(name.split("/")[0])
PY
)"
  [ -n "$top_dir" ] && [ "$top_dir" != "." ] || fail "无法确定分发包顶层目录"
  tar -xjf "$ARCHIVE_PATH" -C "$SCRIPT_DIR"
  CEF_ROOT="$SCRIPT_DIR/$top_dir"
  [ -f "$CEF_ROOT/Release/libcef.so" ] || fail "解包不完整: 缺 Release/libcef.so"
  log "解包完成: $CEF_ROOT"
fi

# ---------- 4. 配置 + 编译（C++20 / Ninja）----------
log "CMake 配置: $BUILD_DIR (CEF_ROOT=$CEF_ROOT)"
cmake -S "$SCRIPT_DIR" -B "$BUILD_DIR" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCEF_ROOT="$CEF_ROOT"
log "编译 (nproc=$(nproc))"
cmake --build "$BUILD_DIR" --parallel "$(nproc)"

# ---------- 5. 运行时布置：CEF so/dat 与可执行同目录（RPATH=$ORIGIN）----------
# 分发包实际内容（Linux Minimal，与 cef_variables.cmake 的 CEF_BINARY_FILES/
# CEF_RESOURCE_FILES 一致）：Release/ 有 libcef.so、libvk_swiftshader.so、
# libvulkan.so.1、v8_context_snapshot.bin、vk_swiftshader_icd.json、
# chrome-sandbox；Resources/ 有 icudtl.dat、*.pak、locales/。
# 工单提到的 libEGL.so/libGLESv2.so 在 CEF Linux 分发包中不存在（非 CEF
# 交付物），存在才拷贝，缺席仅提示 —— RPATH=$ORIGIN 语义不变。
[ -f "$BIN_DIR/vtx_cef_worker" ] || fail "构建产物缺失: $BIN_DIR/vtx_cef_worker"
log "布置 CEF 运行时到 $BIN_DIR"
required_release=(libcef.so libvk_swiftshader.so v8_context_snapshot.bin)
issue_runtime=(libEGL.so libGLESv2.so)   # 工单列举；Linux 分发包不含，存在才拷
optional_release=(libvulkan.so.1 vk_swiftshader_icd.json chrome-sandbox)
for item in "${required_release[@]}"; do
  [ -f "$CEF_ROOT/Release/$item" ] || fail "分发包缺必需运行时: Release/$item"
  cp -f "$CEF_ROOT/Release/$item" "$BIN_DIR/"
done
for item in "${issue_runtime[@]}" "${optional_release[@]}"; do
  if [ -f "$CEF_ROOT/Release/$item" ]; then
    cp -f "$CEF_ROOT/Release/$item" "$BIN_DIR/"
  else
    log "分发包不含 $item（按 CEF Linux 清单属正常），跳过"
  fi
done
for item in icudtl.dat chrome_100_percent.pak chrome_200_percent.pak resources.pak; do
  [ -f "$CEF_ROOT/Resources/$item" ] || fail "分发包缺必需资源: Resources/$item"
  cp -f "$CEF_ROOT/Resources/$item" "$BIN_DIR/"
done
cp -rf "$CEF_ROOT/Resources/locales" "$BIN_DIR/"

# ---------- 6. 验证：ldd 无未解析 + sanity 自检 ----------
if ldd "$BIN_DIR/vtx_cef_worker" | tee "$BUILD_DIR/ldd.txt" | grep -q "not found"; then
  fail "ldd 存在未解析动态库（见 $BUILD_DIR/ldd.txt）"
fi
log "ldd 通过：无未解析动态库"
"$BIN_DIR/vtx_cef_worker" --sanity
log "OK: vtx_cef_worker 构建并通过 sanity（产物: $BIN_DIR/vtx_cef_worker）"
