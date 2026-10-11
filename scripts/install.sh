#!/usr/bin/env bash
# install.sh —— video_tools 自托管安装入口(curl | bash 安全形态)
# 设计目标:《自托管安装入口执行方案》2026-09-20;R-23 P0。
# 设计要点(§3.1):零 git 零 sudo 依赖 / $0 无关 / 非交互安全 / 幂等复用 /
#   ffmpeg≥5.1 下限 / --chrome 显式走 npmmirror / SHA256SUMS 校验 /
#   --uninstall 标记门 / 版本源 releases.json(--version 可覆盖)。
# 行为契约(§3.3):checkout 复用不覆盖 / 非 checkout 目标 die / 二次跑幂等 /
#   --dry-run 零副作用 / stdin 非 TTY 零提示 / 缺 ffmpeg 提示+exit 0 /
#   ffmpeg<5.1 警告 / 校验失败 die / --uninstall 无标记 die。
set -uo pipefail

BASE_URL="${VIDEO_TOOLS_DL_BASE:-https://ruochongliang.top/dl/video-tools}"
CFT_MIRROR="${CHROME_CFT_MIRROR:-https://registry.npmmirror.com/-/binary/chrome-for-testing}"
CHROME_VERSION="${CHROME_VERSION:-153.0.8010.52}"   # R-23 P1 定版:headless=new 渲染+CDP 端点实测通过(2026-09-20)
DEFAULT_HOME="${HOME}/.local/share/video-tools"

log()  { printf '[install] %s\n' "$*"; }
die()  { printf '[install] ✗ %s\n' "$*" >&2; exit 1; }
warn() { printf '[install] ⚠ %s\n' "$*"; }

# ---- 参数解析(非交互安全:不 read,不依赖 TTY) ----
VERSION="" ; TARGET_DIR="" ; CHROME=0 ; UNINSTALL=0 ; DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --version)   VERSION="${2:-}"; [ -n "$VERSION" ] || die "--version 需要值"; VERSION="${VERSION#v}"; shift 2 ;;
    --version=*) VERSION="${1#--version=}"; [ -n "$VERSION" ] || die "--version 需要值"; VERSION="${VERSION#v}"; shift ;;
    --dir)       TARGET_DIR="${2:-}"; [ -n "$TARGET_DIR" ] || die "--dir 需要值"; shift 2 ;;
    --dir=*)     TARGET_DIR="${1#--dir=}"; [ -n "$TARGET_DIR" ] || die "--dir 需要值"; shift ;;
    --chrome)    CHROME=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --uninstall=*) UNINSTALL=1; TARGET_DIR="${1#--uninstall=}"; shift ;;
    --dry-run)   DRY_RUN=1; shift ;;
    -h|--help)
      cat <<USAGE
用法: install.sh [--dir DIR] [--version vX.Y.Z] [--chrome] [--uninstall] [--dry-run]
  --dir DIR      安装根(默认顺序: \$VT_HOME > 当前目录若为 checkout > $DEFAULT_HOME)
  --version VER  安装指定版本(默认取 releases.json 最新)
  --chrome       同时下载 Chrome for Testing(npmmirror,~170MB)
  --uninstall    卸载(仅限带 .vt-install.json 标记的安装目录)
  --dry-run      演练:零下载零写入零修改
USAGE
      exit 0 ;;
    *) die "未知参数: $1(见 --help)" ;;
  esac
done

need() { command -v "$1" >/dev/null 2>&1 || die "缺少依赖: $1(本安装器需要 bash/curl/tar/python3)"; }
need curl; need tar; need python3

# ---- 卸载分支(标记门) ----
if [ "$UNINSTALL" = 1 ]; then
  [ -n "$TARGET_DIR" ] || TARGET_DIR="${VT_HOME:-$DEFAULT_HOME}"
  [ -f "$TARGET_DIR/.vt-install.json" ] || die "拒绝卸载:$TARGET_DIR 无 .vt-install.json 标记(非本安装器所装,防误删)"
  if [ "$DRY_RUN" = 1 ]; then log "[dry-run] 将删除 $TARGET_DIR(有标记,允许)"; exit 0; fi
  rm -rf "$TARGET_DIR"
  log "已卸载 $TARGET_DIR"
  exit 0
fi

# ---- 安装根裁定(优先级:--dir > VT_HOME > 当前目录若 checkout > 默认) ----
is_checkout() { [ -f "$1/vt.py" ] && [ -d "$1/vtx" ]; }
if [ -z "$TARGET_DIR" ]; then
  if [ -n "${VT_HOME:-}" ]; then TARGET_DIR="$VT_HOME"
  elif is_checkout "$PWD"; then TARGET_DIR="$PWD"
  else TARGET_DIR="$DEFAULT_HOME"; fi
fi
log "安装根: $TARGET_DIR"

# ---- 复用检测(幂等) ----
if is_checkout "$TARGET_DIR"; then
  v_now="$(python3 - "$TARGET_DIR/VERSION" 2>/dev/null <<'PY' || echo unknown
print(open(__import__("sys").argv[1]).read().strip())
PY
)"
  v_cur="$(curl -fsS "$BASE_URL/releases.json" 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["latest"])' 2>/dev/null || echo unknown)"
  # rc 语义钉死(RECP-20260920-001):复用分支=检测+告知后干净返回 0;
  # 历史缺陷=本赋值行与 log 行粘连,v_cur 沦为临时赋值永不持久,下一行 $v_cur 撞 set -u 崩溃 rc=1。
  log "检测到既有 checkout($TARGET_DIR,VERSION=$v_now)——复用不覆盖。"
  log "线上最新版本: $v_cur(如需升级请移除后重装或手动 git pull)"
  exit 0
fi
if [ -e "$TARGET_DIR" ] && [ -n "$(ls -A "$TARGET_DIR" 2>/dev/null)" ]; then
  die "目标 $TARGET_DIR 已存在、非空且不是 video_tools checkout——拒绝覆盖(换 --dir 或清空后重试)"
fi

# ---- 版本解析 ----
if [ -z "$VERSION" ]; then
  VERSION="$(curl -fsS "$BASE_URL/releases.json" | python3 -c 'import json,sys;print(json.load(sys.stdin)["latest"])')" \
    || die "releases.json 拉取/解析失败($BASE_URL)"
fi
VERSION="${VERSION#v}"
log "安装版本: $VERSION"
[ "$VERSION" = "$(printf '%s' "$VERSION" | tr -cd 'A-Za-z0-9.v_-')" ] || die "版本串含非法字符"

# ---- ffmpeg 探测(≥5.1 下限;缺则提示+exit 0;旧版警告) ----
FFMPEG_MAJOR=0
if command -v ffmpeg >/dev/null 2>&1; then
  FFMPEG_MAJOR="$(ffmpeg -version 2>/dev/null | head -1 | sed -n 's/.*version \([0-9]*\)\..*/\1/p')"
  FFMPEG_MAJOR="${FFMPEG_MAJOR:-0}"
fi
if [ "$FFMPEG_MAJOR" -eq 0 ]; then
  warn "未检测到 ffmpeg。video_tools 渲染链依赖 ffmpeg ≥5.1(-fps_mode)。"
  log "安装仍将继续(内核 validate/subs 等非 ffmpeg 面可用);建议随后安装:"
  log "  Debian/Ubuntu: sudo apt install ffmpeg   |  Fedora: sudo dnf install ffmpeg"
  log "  (Arch: sudo pacman -S ffmpeg)"
elif [ "$FFMPEG_MAJOR" -lt 5 ]; then
  warn "ffmpeg 版本 $(ffmpeg -version | head -1 | awk '{print $3}') < 5.1:渲染链使用的 -fps_mode 参数为 5.1 引入,视频合成阶段将失败。"
  warn "建议升级 ffmpeg 后重跑 vt.py doctor 复核。"
fi

# ---- 下载与校验(dry-run 零副作用) ----
WORK="$(mktemp -d)"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT
if [ "$DRY_RUN" = 1 ]; then
  log "[dry-run] 将执行:下载 $BASE_URL/v$VERSION/video-tools-$VERSION.tar.gz + SHA256SUMS 校验 + 解包到 $TARGET_DIR + vt.py init + doctor"
  log "[dry-run] 零下载零写入零修改——演练结束"
  exit 0
fi
log "下载载荷…"
curl -fsS "$BASE_URL/v$VERSION/video-tools-$VERSION.tar.gz" -o "$WORK/video-tools-$VERSION.tar.gz" \
  || die "载荷下载失败:$BASE_URL/v$VERSION/video-tools-$VERSION.tar.gz"
curl -fsS "$BASE_URL/SHA256SUMS" -o "$WORK/SHA256SUMS" \
  || die "SHA256SUMS 下载失败"
( cd "$WORK" && grep "video-tools-$VERSION.tar.gz" SHA256SUMS | sha256sum -c - >/dev/null 2>&1 ) \
  || die "SHA256 校验失败——载荷不落地(信任源校验不过,检查网络或 releases 一致性)"
log "校验通过。"

# ---- 解包安装 ----
mkdir -p "$TARGET_DIR"
tar -xzf "$WORK/video-tools-$VERSION.tar.gz" -C "$TARGET_DIR" || die "解包失败"
cat > "$TARGET_DIR/.vt-install.json" <<MARK
{"installed_by": "install.sh", "version": "$VERSION", "base_url": "$BASE_URL", "date": "$(date -Iseconds)"}
MARK

# ---- 初始化 + doctor 门 ----
log "运行 vt.py init(非交互幂等)…"
( cd "$TARGET_DIR" && python3 vt.py init ) || warn "vt.py init 非零退出(可随后手动补跑;config.local.json 已存在则不动)"
log "运行 vt.py doctor(安装成功门)…"
if ( cd "$TARGET_DIR" && python3 vt.py doctor ); then
  log "✅ 安装完成:$TARGET_DIR(VERSION=$VERSION)"
  log "常用入口: python3 vt.py doctor | python3 vt.py init --fix | python3 -m vtx"
else
  warn "doctor 未全绿——见上方输出(多为可选依赖缺省,基础渲染面不受影响;补依赖后重跑 doctor)"
fi

# ---- Chrome(显式开启;npmmirror CDN,绝不占自托管带宽) ----
if [ "$CHROME" = 1 ]; then
  log "下载 Chrome for Testing $CHROME_VERSION(npmmirror CDN,~195MB)…"
  curl -fsSL "$CFT_MIRROR/$CHROME_VERSION/linux64/chrome-linux64.zip" -o "$WORK/chrome-linux64.zip" \
    || die "Chrome for Testing 下载失败($CFT_MIRROR/$CHROME_VERSION)"
  mkdir -p "$TARGET_DIR/chrome-for-testing"
  ( cd "$TARGET_DIR/chrome-for-testing" && python3 -c "import zipfile;zipfile.ZipFile('$WORK/chrome-linux64.zip').extractall()" ) \
    || die "Chrome 解包失败"
  # python zipfile 不保留 unix 权限位:补主二进制与崩溃处理器执行位
  chmod +x "$TARGET_DIR/chrome-for-testing/chrome-linux64/chrome" \
           "$TARGET_DIR/chrome-for-testing/chrome-linux64/chrome_crashpad_handler" 2>/dev/null || true
  log "✓ Chrome 就绪:$TARGET_DIR/chrome-for-testing/chrome-linux64/chrome"
  log "  启用方式(二选一): export VTX_CHROME=$TARGET_DIR/chrome-for-testing/chrome-linux64/chrome"
  log "                    或将该路径加入 PATH(export PATH=\$PATH:$TARGET_DIR/chrome-for-testing/chrome-linux64)"
fi
exit 0
