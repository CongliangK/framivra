#!/usr/bin/env bash
# Complete non-AI source deployment. Linux and macOS; Bash 3.2 compatible.
# HTTPS/Git source trust, not the separate signed-release installer.
set -eu
bootstrap() {
  if [ "${1-}" = '--help' ]; then
    printf '%s\n' 'Usage: bash scripts/bootstrap.sh [--yes] [--plan] [--no-system] [--update]' 'Installs the managed non-AI/CDP environment in ~/.local/share/framivra.' 'Requires HTTPS access; missing Python/package manager setup may request permission.'
    exit 0
  fi
  SYSTEM_ALLOWED=1
  AUTO_YES=0
  for arg in "$@"; do
    case "$arg" in --plan|--no-system) SYSTEM_ALLOWED=0 ;; --yes) AUTO_YES=1 ;; esac
  done
  if [ "$(uname -s)" = Darwin ] && [ "$SYSTEM_ALLOWED" = 1 ]; then
    for dir in /opt/homebrew/bin /usr/local/bin; do
      if [ -x "$dir/brew" ]; then PATH="$dir:$PATH"; export PATH; fi
    done
    if ! command -v brew >/dev/null 2>&1; then
      printf '%s\n' 'Missing Homebrew. Install the official package manager (may request administrator approval)? [y/N]' >&2
      answer=y
      if [ "$AUTO_YES" != 1 ]; then read -r answer; fi
      case "$answer" in y|Y|yes) ;; *) exit 1 ;; esac
      BREW_SCRIPT="$(mktemp "${TMPDIR:-/tmp}/framivra-brew.XXXXXXXX")"
      curl -fL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh -o "$BREW_SCRIPT"
      /bin/bash "$BREW_SCRIPT"
      rm -f -- "$BREW_SCRIPT"
      PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"; export PATH
    fi
  fi
  PYTHON=''
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3,9))' >/dev/null 2>&1; then
      PYTHON="$candidate"
      break
    fi
  done
  if [ -z "$PYTHON" ]; then
    for arg in "$@"; do
      if [ "$arg" = '--plan' ] || [ "$arg" = '--no-system' ]; then
        printf '%s\n' 'Python >=3.9 is missing; no changes made. Install Python before planning or using --no-system.' >&2
        exit 1
      fi
    done
    printf '%s\n' 'Python >=3.9 is required. Prepare it using your system package manager? [y/N]' >&2
    read -r answer
    case "$answer" in y|Y|yes) ;; *) exit 1 ;; esac
    case "$(uname -s)" in
      Linux)
        SUDO=''
        [ "$(id -u)" = 0 ] || SUDO=sudo
        if command -v apt-get >/dev/null 2>&1; then
          $SUDO apt-get update
          $SUDO apt-get install -y python3 python3-venv
        elif command -v dnf >/dev/null 2>&1; then
          $SUDO dnf install -y python3 python3-pip
        elif command -v pacman >/dev/null 2>&1; then
          $SUDO pacman -S --needed --noconfirm python python-pip
        else
          printf '%s\n' 'Install Python >=3.9 using your distribution package manager, then rerun.' >&2
          exit 1
        fi
        PYTHON=python3
        ;;
      Darwin)
        for dir in /opt/homebrew/bin /usr/local/bin; do
          if [ -x "$dir/brew" ]; then PATH="$dir:$PATH"; export PATH; fi
        done
        if ! command -v brew >/dev/null 2>&1; then
          printf '%s\n' 'Homebrew is needed for native dependencies. Install from https://brew.sh, then rerun this same command.' >&2
          exit 1
        fi
        brew install python@3.13
        PYTHON="$(brew --prefix python@3.13)/bin/python3.13"
        ;;
      *) printf '%s\n' 'Use bootstrap.ps1 on Windows.' >&2; exit 1 ;;
    esac
  fi
  "$PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3,9))'
  # Keep installer source in a unique temporary file, never in the caller cwd.
  TEMP_SCRIPT="$(mktemp "${TMPDIR:-/tmp}/framivra-source.XXXXXXXX")"
  trap 'test -z "${TEMP_SCRIPT-}" || rm -f -- "$TEMP_SCRIPT"' EXIT
  curl -fL --proto '=https' --tlsv1.2 'https://raw.githubusercontent.com/CongliangK/framivra/main/scripts/install_source.py' -o "$TEMP_SCRIPT"
  "$PYTHON" "$TEMP_SCRIPT" "$@"
}
# Parse this entire compound block BEFORE switching pipe stdin to the terminal.
# Always exit after the installer; never leave a shell reading terminal commands.
{
  if [ -e /dev/tty ] && { true </dev/tty; } 2>/dev/null; then
    exec </dev/tty
  fi
  bootstrap "$@"
  exit $?
}
