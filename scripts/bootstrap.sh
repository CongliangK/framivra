#!/usr/bin/env bash
# Source deployment, HTTPS/Git trust; not the signed-release installer.
# Bash 3.2 compatible. Only the installer bundle is temporary; downloads persist.
set -euo pipefail
bootstrap() {
  if [ "${1-}" = '--help' ]; then
    printf '%s\n' 'Usage: bash scripts/bootstrap.sh [--yes] [--plan] [--no-system] [--update [--restart-update]] [--network cn|official] [--verify-render]' 'Installs non-AI/CDP under ~/.local/share/framivra. Verified downloads resume across invocations.' 'CDP end-to-end render verification is opt-in and may take several minutes.'
    exit 0
  fi
  SYSTEM_ALLOWED=1; AUTO_YES=0; NETWORK=cn; SOURCE_REF=main; SOURCE_COMMIT=''; RENDER_PLAN=skipped
  next=''
  for arg in "$@"; do
    if [ -n "$next" ]; then
      case "$next" in network) NETWORK="$arg";; ref) SOURCE_REF="$arg";; commit) SOURCE_COMMIT="$arg";; esac
      next=''; continue
    fi
    case "$arg" in
      --plan|--no-system) SYSTEM_ALLOWED=0;;
      --yes) AUTO_YES=1;;
      --verify-render) RENDER_PLAN=enabled;;
      --network) next=network;; --network=*) NETWORK="${arg#*=}";;
      --ref) next=ref;; --ref=*) SOURCE_REF="${arg#*=}";;
      --source-commit) next=commit;; --source-commit=*) SOURCE_COMMIT="${arg#*=}";;
    esac
  done
  [ -z "$next" ] || { printf '%s\n' 'Missing bootstrap option value' >&2; exit 1; }
  case "$NETWORK" in cn|official) ;; *) printf '%s\n' 'Use --network cn or official' >&2; exit 1;; esac
  BUNDLE=''; BREW_SCRIPT=''; BREW_LOG=''
  cleanup() {
    # Both names were created uniquely by this invocation, never the install root.
    if [ -n "$BREW_SCRIPT" ]; then
      case "$BREW_SCRIPT" in */framivra-brew.*) test ! -f "$BREW_SCRIPT" || rm -f -- "$BREW_SCRIPT";; esac
    fi
    if [ -n "$BREW_LOG" ]; then
      case "$BREW_LOG" in */framivra-brew-log.*) test ! -f "$BREW_LOG" || rm -f -- "$BREW_LOG";; esac
    fi
    if [ -n "$BUNDLE" ]; then
      case "$BUNDLE" in */framivra-source.*) test ! -d "$BUNDLE" || rm -rf -- "$BUNDLE";; esac
    fi
  }
  trap cleanup EXIT
  confirm() {
    printf '%s\n' "$1" >&2
    [ "$AUTO_YES" = 1 ] && return 0
    answer=''; read -r answer
    case "$answer" in y|Y|yes) ;; *) printf '%s\n' 'Cancelled before this transaction' >&2; exit 1;; esac
  }
  fetch() {
    curl -fL --proto '=https' --proto-redir '=https' --tlsv1.2 --retry 2 \
      --connect-timeout 15 --speed-limit 1000 --speed-time 60 "$@"
  }
  if [ "$(uname -s)" = Darwin ]; then
    if ! command -v brew >/dev/null 2>&1; then
      for dir in /opt/homebrew/bin /usr/local/bin; do
        if [ -x "$dir/brew" ]; then PATH="$dir:$PATH"; export PATH; break; fi
      done
    fi
    if ! command -v brew >/dev/null 2>&1 && [ "$SYSTEM_ALLOWED" = 1 ]; then
      confirm 'Prepare official Homebrew (may request administrator approval)? [y/N]'
      BREW_SCRIPT="$(mktemp "${TMPDIR:-/tmp}/framivra-brew.XXXXXXXX")"
      fetch https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh -o "$BREW_SCRIPT"
      BREW_LOG="$(mktemp "${TMPDIR:-/tmp}/framivra-brew-log.XXXXXXXX")"
      if [ "$AUTO_YES" = 1 ]; then export NONINTERACTIVE=1; fi
      brew_install() {
        # Preserve terminal stdin/stdout (sudo/installer approval). Retain error
        # output until exit so a trust failure cannot masquerade as transport.
        local status=0 line=''
        env "$@" LC_ALL=C LANG=C GIT_HTTP_LOW_SPEED_LIMIT=1 GIT_HTTP_LOW_SPEED_TIME=60 \
          /bin/bash "$BREW_SCRIPT" 2>"$BREW_LOG" || status=$?
        while IFS= read -r line; do printf '%s\n' "$line" >&2; done <"$BREW_LOG"
        [ "$status" = 0 ] && return 0
        case "$(< "$BREW_LOG")" in
          *SHA256*|*SHA512*|*checksum*|*Checksum*|*signature*|*Signature*|*certificate*|*Certificate*|*SSL*|*TLS*|*GPG*|*not\ signed*)
            printf '%s\n' 'Homebrew trust failure; not retrying another source.' >&2; return 2;;
        esac
        case "$(< "$BREW_LOG")" in
          *Could\ not\ resolve*|*Couldn\'t\ resolve*|*Failed\ to\ connect*|*Could\ not\ connect*|*Connection\ timed\ out*|*Operation\ timed\ out*|*Connection\ reset*|*connection\ reset*|*Network\ is\ unreachable*|*remote\ end\ hung\ up*|*error:\ 5[0-9][0-9]*|*HTTP\ 5[0-9][0-9]*|*error:\ 404*|*error:\ 429*) return 1;;
        esac
        printf '%s\n' 'Homebrew non-transport failure; not retrying another source.' >&2
        return 2
      }
      if [ "$NETWORK" = cn ]; then
        printf '%s\n' 'New Homebrew: TUNA Git/API/bottles first, official Git fallback. Its new origin may remain the selected mirror; existing brew remotes are not changed.' >&2
        status=0
        brew_install HOMEBREW_BREW_GIT_REMOTE=https://mirrors.tuna.tsinghua.edu.cn/git/homebrew/brew.git \
          HOMEBREW_CORE_GIT_REMOTE=https://mirrors.tuna.tsinghua.edu.cn/git/homebrew/homebrew-core.git \
          HOMEBREW_API_DOMAIN=https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles/api \
          HOMEBREW_BOTTLE_DOMAIN=https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles || status=$?
        if [ "$status" != 0 ]; then
          [ "$status" != 2 ] || exit 2
          printf '%s\n' 'Homebrew mirror attempt failed; retrying the official installer transport.' >&2
          brew_install -u HOMEBREW_BREW_GIT_REMOTE -u HOMEBREW_CORE_GIT_REMOTE -u HOMEBREW_API_DOMAIN -u HOMEBREW_BOTTLE_DOMAIN
        fi
      else
        brew_install -u HOMEBREW_BREW_GIT_REMOTE -u HOMEBREW_CORE_GIT_REMOTE -u HOMEBREW_API_DOMAIN -u HOMEBREW_BOTTLE_DOMAIN
      fi
      PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"; export PATH
    fi
  fi
  PYTHON=''
  for candidate in python3 python /opt/homebrew/opt/python@3.13/bin/python3.13 /usr/local/opt/python@3.13/bin/python3.13; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -I -c 'import sys,platform; raise SystemExit(sys.version_info < (3,9) or (sys.platform == "darwin" and platform.machine().lower() not in ("arm64","aarch64")))' >/dev/null 2>&1; then
      PYTHON="$candidate"; break
    fi
  done
  if [ -z "$PYTHON" ]; then
    if [ "$SYSTEM_ALLOWED" != 1 ]; then
      printf '%s\n' 'Python >=3.9 is missing; no changes made. Install Python before --plan/--no-system.' >&2; exit 1
    fi
    confirm 'Prepare Python >=3.9 using the native manager? Linux stage-zero uses your configured trusted repositories. [y/N]'
    case "$(uname -s)" in
      Linux)
        SUDO=''; [ "$(id -u)" = 0 ] || SUDO=sudo
        if command -v apt-get >/dev/null 2>&1; then
          $SUDO apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=60 -o Acquire::https::Timeout=60 update
          $SUDO apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=60 -o Acquire::https::Timeout=60 install -y python3 python3-venv
        elif command -v dnf >/dev/null 2>&1; then
          $SUDO dnf --setopt=timeout=60 --setopt=retries=2 install -y python3 python3-pip
        elif command -v pacman >/dev/null 2>&1; then
          # A rolling system must not install from newly refreshed partial state.
          confirm 'Arch requires a complete -Syu transaction before Python; this may upgrade the system. [y/N]'
          $SUDO pacman -Syu --needed --noconfirm python python-pip
        else
          printf '%s\n' 'Install Python >=3.9 using your trusted distribution, then rerun.' >&2; exit 1
        fi
        PYTHON=python3;;
      Darwin)
        command -v brew >/dev/null 2>&1 || { printf '%s\n' 'Install Homebrew, then rerun.' >&2; exit 1; }
        brew_transport() {
          if [ "$NETWORK" = cn ]; then
            HOMEBREW_API_DOMAIN=https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles/api \
            HOMEBREW_BOTTLE_DOMAIN=https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles brew "$@"
          else
            env -u HOMEBREW_API_DOMAIN -u HOMEBREW_BOTTLE_DOMAIN brew "$@"
          fi
        }
        DEPENDENCIES="$(brew_transport deps --formula --union python@3.13)"
        bottles=()
        while IFS= read -r dependency; do
          if [ -n "$dependency" ]; then bottles+=("$dependency"); fi
        done <<< "$DEPENDENCIES"
        brew_transport install --force-bottle "${bottles[@]}" python@3.13
        PYTHON="$(brew --prefix python@3.13)/bin/python3.13";;
      *) printf '%s\n' 'Use bootstrap.ps1 on Windows.' >&2; exit 1;;
    esac
  fi
  "$PYTHON" -I -c 'import sys; raise SystemExit(sys.version_info < (3,9))'
  for arg in "$@"; do
    if [ "$arg" = '--plan' ]; then
      printf '%s\n' 'Read-only bootstrap plan: same-commit source installer bundle; native packages, pinned wheels and fonts. No network or files changed.' "CDP render verification on install: $RENDER_PLAN."
      exit 0
    fi
  done
  # Resolve once: modules and fresh main source may not drift between requests.
  COMMIT="$(fetch --max-filesize 2000000 https://api.github.com/repos/CongliangK/framivra/commits/main |
    "$PYTHON" -I -c 'import json,re,sys; v=json.load(sys.stdin).get("sha", ""); assert re.fullmatch("[0-9a-f]{40}",v), "Invalid installer commit"; print(v)')"
  BUNDLE="$(mktemp -d "${TMPDIR:-/tmp}/framivra-source.XXXXXXXX")"
  for name in install_source.py install_downloads.py install_native_windows.py install_native_posix.py install_python_packages.py install_runtime_fonts.py demo_fonts.py; do
    fetch --max-filesize 1000000 "https://raw.githubusercontent.com/CongliangK/framivra/$COMMIT/scripts/$name" -o "$BUNDLE/$name"
  done
  if [ "$SOURCE_REF" = main ] && [ -z "$SOURCE_COMMIT" ]; then
    set -- "$@" --source-commit "$COMMIT"
  fi
  "$PYTHON" "$BUNDLE/install_source.py" "$@"
}
# Fully parse the block before restoring terminal stdin after curl | bash.
{
  if [ -e /dev/tty ] && { true </dev/tty; } 2>/dev/null; then exec </dev/tty; fi
  bootstrap "$@"
  exit $?
}
