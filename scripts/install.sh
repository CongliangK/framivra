#!/usr/bin/env bash
# install.sh —— Linux/macOS 一键安装引导(克隆/安全更新仓库 + 运行 vt.py init)。
# 设计理念对齐 ibus-voice-ime 的 remote-install.sh(标杆)与 Windows 侧
# scripts/install_windows.ps1(克隆模式):一键安装=快速初始化手段(克隆或安全
# 更新仓库,再跑仓内既有幂等 init),不替代手动克隆(README「快速开始」的
# clone 路径始终可用),不依赖发版资产(Releases)与 git 以外的工具(不要求 gh CLI)。
# 用法(curl|bash 与本地双形态均可执行;本脚本绝不 sudo,也不自动安装包管理器
# 依赖——Linux/macOS 包管理器分裂,缺失一律 fail 并给一行指引):
#   curl -fsSL https://raw.githubusercontent.com/CongliangK/framivra/main/scripts/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/CongliangK/framivra/main/scripts/install.sh | bash -s -- --skip-update
#   curl -fsSL https://raw.githubusercontent.com/CongliangK/framivra/main/scripts/install.sh | bash -s -- -- --fix
#   bash scripts/install.sh --skip-update -- --json
# 参数:--skip-update 跳过更新检查(脏克隆也放行使用,不改动其内容),须置于
# 首位;其余参数(或 -- 之后的全部参数)原样透传给 vt.py init,如 --fix / --json。
# 重复运行=安全更新:克隆干净(git status --porcelain 为空)时 fetch+reset
# --hard origin/<ref>;克隆变脏(含未跟踪文件)时拒绝自动更新、绝不静默丢弃
# 改动(可加 --skip-update 跳过更新直接复用现有克隆)。上游新版本把本地被忽略
# 文件的同名路径转为跟踪文件(config.local.json 是必然场景)时,porcelain 判
# 脏看不见,碰撞预检同样拒绝更新,防 reset --hard 静默覆盖用户文件。
# 环境变量(空值/全空白/控制字符/短横线开头一律拒绝;空白仅 FRAMIVRA_DIR
# 放行空格——目录路径含空格合法,其余空白与 REPO/REF 的任何空白仍拒):
#   FRAMIVRA_DIR  默认 ~/.local/share/framivra-repo(克隆目录;须为绝对路径;
#                 不得使用运行时数据根 ~/.local/share/video-tools——安装运行
#                 数据会让克隆变脏,下一次引导拒绝自动更新)
#   FRAMIVRA_REPO 默认 https://github.com/CongliangK/framivra.git(可信镜像也可)
#   FRAMIVRA_REF  默认 stable;可选 vX.Y.Z / vX.Y.Z-rc.N / main(开发版)
# 信任边界:本脚本经 HTTPS 从 GitHub raw 下载——首次安装信任的是「下载通道 +
# 仓库当前内容」;发布者身份验证(keyless provenance)随发版通道后续接入,
# 引导层不自称签名验证(可先取脚本审读后再执行)。
# macOS 兼容:目标解释器覆盖系统自带 bash 3.2——不使用 bash4+ 才有的特性
# (关联数组/参数小写化展开/整块读入数组等),也不依赖 GNU realpath -m
# (数据根守卫用纯 bash 词法规范化,见 canon;与标杆 remote-install.sh 同一
# 约束)。
set -euo pipefail

fail() { printf '[bootstrap] 错误:%s\n' "$1" >&2; exit 1; }
network_hint() {
  printf '[bootstrap] 提示:请检查网络、仓库地址与分支;可设置 https_proxy,或把 FRAMIVRA_REPO 指向可信镜像后重试。\n' >&2
}

# ---------- 前置依赖:python3>=3.9 与 git(缺失不自动装,fail 并给一行指引) ----------
if ! command -v python3 >/dev/null 2>&1; then
  fail '未找到 python3:请先安装 Python 3.9+(Debian/Ubuntu: sudo apt install python3;Fedora: sudo dnf install python3;macOS: brew install python3;或 https://www.python.org/downloads/ ),新开终端后重跑本命令。'
fi
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
  fail "python3 版本过低(需 >=3.9,当前 $(python3 --version 2>&1)):请升级后重跑本命令。"
fi
if ! command -v git >/dev/null 2>&1; then
  fail '未找到 git:请先安装(Debian/Ubuntu: sudo apt install git;Fedora: sudo dnf install git;macOS: brew install git;或 https://git-scm.com/ ),新开终端后重跑本命令。'
fi

# ---------- 参数解析:--skip-update 归引导器,其余原样透传 vt.py init ----------
SKIP_UPDATE=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --skip-update) SKIP_UPDATE=1; shift ;;
    --) shift; break ;;
    *) break ;;
  esac
done

# ---------- 环境变量(显式空值保留给校验拒绝,而非回退默认) ----------
DIR="${FRAMIVRA_DIR-${HOME-}/.local/share/framivra-repo}"
REPO="${FRAMIVRA_REPO-https://github.com/CongliangK/framivra.git}"
REF="${FRAMIVRA_REF-stable}"
EXPECTED_COMMIT=""
if [ "$REF" = stable ]; then
  REF="$(python3 - <<'PY_RELEASE'
import json, urllib.request
request = urllib.request.Request('https://api.github.com/repos/CongliangK/framivra/releases/latest', headers={'User-Agent': 'framivra-bootstrap'})
with urllib.request.urlopen(request, timeout=30) as response:
    release = json.load(response)
if release.get('draft') is not False or release.get('prerelease') is not False:
    raise SystemExit('stable release is not public and final')
print(release['tag_name'])
PY_RELEASE
)" || fail '稳定发行解析失败;不会回落 main。'
fi
case "$REF" in
  main) SOURCE_REF="refs/heads/main"; TARGET_REF="refs/remotes/origin/main" ;;
  *)
    [[ "$REF" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-rc\.[1-9][0-9]*)?$ ]] || fail 'FRAMIVRA_REF 非法:只接受 stable、vX.Y.Z、vX.Y.Z-rc.N 或 main。'
    SOURCE_REF="refs/tags/$REF"; TARGET_REF="$SOURCE_REF"
    # A public network tag must belong to a published Release. Absolute local
    # repositories are an explicit offline candidate/testing trust boundary.
    case "$REPO" in
      /*) ;;
      *)
        python3 - "$REF" <<'PY_PUBLIC_RELEASE' || fail '指定版本不是已公开发行,或查询失败;不会回落 main。'
import json, sys, urllib.request
ref = sys.argv[1]
request = urllib.request.Request('https://api.github.com/repos/CongliangK/framivra/releases/tags/' + ref, headers={'User-Agent': 'framivra-bootstrap'})
with urllib.request.urlopen(request, timeout=30) as response:
    release = json.load(response)
assert release.get('tag_name') == ref and release.get('draft') is False
assert release.get('prerelease') is ('-rc.' in ref)
PY_PUBLIC_RELEASE
        ;;
    esac
    REMOTE_TAG="$(git ls-remote --exit-code "$REPO" "$SOURCE_REF" "$SOURCE_REF^{}")" || fail '发行 tag 不存在或网络失败;不会回落 main。'
    EXPECTED_COMMIT="$(printf '%s\n' "$REMOTE_TAG" | python3 -c 'import sys; rows=[x.split() for x in sys.stdin if x.strip()]; peeled=[r[0] for r in rows if r[1].endswith("^{}")]; assert len(peeled)==1, "release requires annotated tag"; print(peeled[0])')" || fail '发行须为 annotated tag。'
    ;;
esac
printf '[bootstrap] 选择 %s @ %s\n' "$REF" "${EXPECTED_COMMIT:-development/main}"
# 去掉尾部斜杠与 /. 尾缀:/tmp/link/ 或 /tmp/link/. 的 -L 判定为假,会绕过
# 符号链接检查;数据根比对同理须用规范化路径(标杆红队发现)。
while [ "$DIR" != "/" ]; do
  case "$DIR" in
    */) DIR="${DIR%/}" ;;
    */.) DIR="${DIR%/.}" ;;
    *) break ;;
  esac
done
[ -n "${HOME-}" ] || fail 'HOME 未设置:默认克隆目录与运行时数据根守卫均依赖它,请在正常登录会话中重跑。'
for name in FRAMIVRA_DIR FRAMIVRA_REPO FRAMIVRA_REF; do
  case "$name" in
    FRAMIVRA_DIR) value="$DIR" ;;
    FRAMIVRA_REPO) value="$REPO" ;;
    FRAMIVRA_REF) value="$REF" ;;
  esac
  if ! [[ "$value" =~ [^[:space:]] ]]; then
    fail "$name 非法:不得为空或全空白。"
  fi
  if [[ "$value" =~ [[:cntrl:]] ]]; then
    fail "$name 非法:不得含控制字符。"
  fi
  case "$value" in
    -*) fail "$name 非法:不得以短横线开头。" ;;
  esac
  if [ "$name" != FRAMIVRA_DIR ]; then
    # REPO/REF 作为单参数传给 git,任何空白都会分裂语义,一律拒绝。
    if [[ "$value" =~ [[:space:]] ]]; then
      fail "$name 非法:不得含空白。"
    fi
  else
    # DIR 含空格合法(默认值随用户名而定);其余空白(制表/换行等)仍拒。
    probe="${value// /}"
    if [[ "$probe" =~ [[:space:]] ]]; then
      fail "$name 非法:除空格外不得含其他空白字符。"
    fi
  fi
done
git check-ref-format --branch "$REF" >/dev/null 2>&1 || fail 'FRAMIVRA_REF 非法:须为有效的分支名。'
case "$DIR" in
  /*) ;;
  *) fail "FRAMIVRA_DIR 须为绝对路径(如 \$HOME/framivra-repo;相对路径会让守卫比对与实际克隆位置分裂),留空则用默认 ~/.local/share/framivra-repo。" ;;
esac
# 克隆目录与运行时数据根分离:install.py 语义的 POSIX 运行时数据根是
# ~/.local/share/video-tools,混用会让克隆在首次安装后变脏,下一次引导拒绝
# 自动更新(对齐 ps1 数据根守卫与标杆红线)。双侧经同一词法规范化函数
# 处理后比对,/./ 与 /../ 中缀、尾斜杠、连续斜杠均不得绕过。
# canon 为纯 bash 词法实现,不依赖外部 realpath:macOS 没有 GNU realpath -m
# (shell_cmds 的 realpath 仅支持 -q,更老版本没有 realpath),外部命令
# 失败回退原串会让守卫在 macOS 退化为原串比对、变体放行(红队发现)。
# 与 Test-Path/git 落盘语义的偏差面如实声明:本函数不访问文件系统、不
# 展开符号链接,也不验证任何段是否存在——DIR 与数据根经符号链接指向同一
# 物理目录时字面比对不拦(这是与真实落盘语义的最大偏差;入口的符号链接
# 目录拒绝与「目录已存在即要求是本脚本可识别的克隆」共同兜底该面)。
# 相对路径不属本函数职责(入口已拒绝),原样返回不参与守卫。
canon() (
  set -f
  path="$1" out="" seg=""
  case "$path" in
    /*) path="${path#/}" ;;
    *) printf '%s' "$path"; exit 0 ;;
  esac
  # 逐段消解:空段(连续斜杠)与 . 丢弃;.. 弹出上一段(根之上不再弹)。
  while [ -n "$path" ]; do
    seg="${path%%/*}"
    if [ "$path" = "$seg" ]; then path=""; else path="${path#*/}"; fi
    case "$seg" in
      ""|".") ;;
      "..")
        case "$out" in
          */*) out="${out%/*}" ;;
          ?*) out="" ;;
        esac ;;
      *) out="${out:+$out/}$seg" ;;
    esac
  done
  printf '/%s' "$out"
)
if [ "$(canon "$DIR")" = "$(canon "${HOME-}/.local/share/video-tools")" ]; then
  fail 'FRAMIVRA_DIR 不得使用运行时数据根 ~/.local/share/video-tools(安装运行数据会让克隆变脏,下次更新被拒);请改用其他目录(默认 framivra-repo 即可)。'
fi

MARKER_NAME='framivra-bootstrap'
if [ -e "$DIR" ] || [ -L "$DIR" ]; then
  if ! { [ -d "$DIR" ] && [ ! -L "$DIR" ] && [ -d "$DIR/.git" ]; }; then
    fail "$DIR 已存在,但不是可独立更新的 Git 克隆(缺 .git 目录,或 .git 是文件——git worktree 链接工作树不由引导器管理,请改用其主克隆目录或手动更新,或是符号链接);不会覆盖,请设置 FRAMIVRA_DIR 使用其他目录。"
  fi
  TOP="$(git -C "$DIR" rev-parse --show-toplevel 2>/dev/null)" || fail '安装目录不是有效的 Git 工作仓库,不会覆盖。'
  DIR="$(cd -- "$DIR" && pwd -P)"
  if [ "$TOP" != "$DIR" ]; then
    fail '安装目录不是 Git 仓库根目录(是子目录),不会覆盖;请把 FRAMIVRA_DIR 指向克隆根。'
  fi
  ORIGIN="$(git -C "$DIR" remote get-url origin 2>/dev/null)" || fail '现有克隆缺少 origin 远端,不会覆盖。'
  if [ "$ORIGIN" != "$REPO" ]; then
    fail "现有克隆的 origin($ORIGIN)与 FRAMIVRA_REPO($REPO)不匹配,不会覆盖;请检查仓库地址或使用其他目录。"
  fi
  if [ "$SKIP_UPDATE" -eq 1 ]; then
    printf '[bootstrap] 已指定 --skip-update:跳过更新,直接复用现有克隆。\n'
  else
    # 判脏:porcelain 任何输出(含未跟踪文件)都算脏——拒绝自动更新,不静默
    # 丢弃用户改动;只判空不解析路径,带空格/非 ASCII 文件名不误判。status
    # 自身失败(索引损坏/仓库异常)不得被命令替换吞掉当作「干净」放行更新:
    # 读数失败一律显式 fail(对齐 ps1 与标杆的 fail-closed 语义)。
    DIRTY="$(git -C "$DIR" status --porcelain --untracked-files=all)" ||
      fail '无法读取本地克隆的 git status(索引或仓库可能损坏),拒绝自动更新,未改动任何本地文件。'
    if [ -n "$DIRTY" ]; then
      fail '现有克隆有未提交变更(含未跟踪文件),拒绝自动更新(不会静默丢弃改动);请把需要的文件备份到克隆外后清理,或删除整个克隆目录让引导器重新拉取;也可加 --skip-update 跳过更新直接使用现有克隆。'
    fi
    printf '[bootstrap] 正在更新已有的干净克隆……\n'
    # 显式分支映射支持浅克隆切换/更新,避免 reset 到过时的远端跟踪引用;
    # 失败时尚未重置任何本地文件。
    if ! git -C "$DIR" fetch --depth 1 origin "+$SOURCE_REF:$TARGET_REF"; then
      network_hint
      fail '获取远端分支失败,尚未改动本地文件。'
    fi
    # 标签/简写歧义防护:一律用全限定 ref,防名为 origin/<ref> 的标签劫持 reset。
    NEW_HEAD="$(git -C "$DIR" rev-parse --verify "$TARGET_REF^{commit}")" || fail '远端分支解析失败,尚未重置本地文件。'
    [ -z "$EXPECTED_COMMIT" ] || [ "$NEW_HEAD" = "$EXPECTED_COMMIT" ] || fail '远端 tag 在安装期间移动,拒绝更新。'
    # ignored→tracked 碰撞预检(fail-closed,红线):porcelain 判脏看不见
    # ignored 文件——上游新版本把原 ignored 路径(vt.py init 生成的
    # config.local.json,以及 out/、*.mp4 等被 .gitignore 忽略的产物)转为
    # tracked 时 porcelain 仍为空,reset --hard 会静默覆盖用户文件。比对
    # 「远端目标 commit 将跟踪的路径集」(ls-tree)与「本地被忽略文件集」
    # (ls-files -o -i --exclude-standard):完全同名,或路径前缀碰撞
    # (ignored 文件 a 对 incoming 目录 a/x,或反之)都算冲突。结果经退出码
    # 传递(0=无冲突 2=有冲突 其它=预检故障),不用 stdout 文本判据——文件
    # 名恰为换行等畸形名时命令替换会剥成空串导致假通过;预检自身任何故障
    # 一律 fail-closed 拒绝更新。实现移植自标杆 remote-install.sh(红队注入
    # 实测过的版本,两侧均 -z 整段字节解析,空格/换行文件名不误判)。
    COLLISION_RC=0
    COLLISION="$(
      python3 - "$DIR" "$TARGET_REF" <<'PY_COLLISION'
import subprocess, sys
repo, target_ref = sys.argv[1], sys.argv[2]
def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)
def git_bytes(*args):
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True)
    if proc.returncode != 0:
        die("git " + " ".join(args[:2]) + " failed: " + proc.stderr.decode("utf-8", "replace").strip())
    return proc.stdout
incoming = set(p.decode("utf-8", "surrogateescape") for p in git_bytes("ls-tree", "-r", "--name-only", "-z", target_ref).split(b"\0") if p)
proc = subprocess.run(["git", "-C", repo, "ls-files", "-o", "-i", "--exclude-standard", "-z"],
                      capture_output=True)
if proc.returncode != 0:
    die("git ls-files failed: " + proc.stderr.decode("utf-8", "replace").strip())
ignored = [p.decode("utf-8", "surrogateescape") for p in proc.stdout.split(b"\0") if p]
hits = set()
for g in ignored:
    if g in incoming:
        hits.add(g)
        continue
    parts = g.split("/")
    if any("/".join(parts[:i]) in incoming for i in range(1, len(parts))):
        hits.add(g)
        continue
    if any(t.startswith(g + "/") for t in incoming):
        hits.add(g)
for h in sorted(hits):
    name = h.encode("utf-8", "surrogateescape")
    name = name.replace(b"\n", b"LF-ESC").replace(b"\r", b"CR-ESC")
    sys.stdout.buffer.write(name + b"\n")
sys.exit(2 if hits else 0)
PY_COLLISION
    )" || COLLISION_RC=$?
    case "$COLLISION_RC" in
      0) ;;
      2) fail "本地被忽略的文件与新版本跟踪路径冲突,拒绝覆盖以防数据丢失:$COLLISION。处理:先把冲突文件备份到克隆目录外,删除整个克隆目录后重跑本命令(上游新版本已把上述路径转为跟踪文件,本地文件不会自动迁移);或临时改用其他 FRAMIVRA_DIR。" ;;
      *) fail 'ignored 碰撞预检失败(fail-closed),拒绝自动更新。' ;;
    esac
    MARKER="$DIR/.git/$MARKER_NAME"
    HEAD_NOW="$(git -C "$DIR" rev-parse HEAD)"
    # 本地提交保护:引导器用 .git 内的同步标记识别自己管理的克隆;HEAD 与
    # 标记不一致说明用户提交过本地变更,浅克隆无法区分落后/分叉,一律拒绝
    # 自动重置(reset --hard 会丢弃本地提交);克隆后从未由引导器同步过的
    # 克隆首次更新按 HEAD==远端放行。
    if [ "$HEAD_NOW" != "$NEW_HEAD" ]; then
      if ! { [ -f "$MARKER" ] && [ "$(cat -- "$MARKER")" = "$HEAD_NOW" ]; }; then
        fail '克隆的 HEAD 与引导器上次同步的不一致(可能存在本地提交);为防数据丢失拒绝自动更新,请自行处理(备份后删除整个克隆目录可重来)后再试。'
      fi
      git -C "$DIR" reset --hard "$TARGET_REF" || fail '更新本地克隆失败,请检查目录权限与 Git 状态。'
    fi
    printf '%s\n' "$NEW_HEAD" > "$MARKER"
  fi
else
  printf '[bootstrap] 正在克隆仓库到 %s……\n' "$DIR"
  if ! git clone --depth 1 -b "$REF" -- "$REPO" "$DIR"; then
    network_hint
    fail "克隆失败,安装尚未开始;若留下半成品目录($DIR),请检查后自行处理或改用其他 FRAMIVRA_DIR。"
  fi
  DIR="$(cd -- "$DIR" && pwd -P)"
  [ -z "$EXPECTED_COMMIT" ] || [ "$(git -C "$DIR" rev-parse HEAD)" = "$EXPECTED_COMMIT" ] || fail '克隆提交与发行 tag 不符,拒绝初始化。'
  printf '%s\n' "$(git -C "$DIR" rev-parse HEAD)" > "$DIR/.git/$MARKER_NAME"
fi

if [ "$SKIP_UPDATE" -eq 0 ]; then
  python3 - "$DIR" "$REF" <<'PY_RECORD'
import json, pathlib, subprocess, sys
root, ref = sys.argv[1:]
commit = subprocess.check_output(['git', '-C', root, 'rev-parse', 'HEAD'], text=True).strip()
(pathlib.Path(root) / '.git/framivra-release.json').write_text(json.dumps({'ref': ref, 'commit': commit}, sort_keys=True) + '\n')
PY_RECORD
fi
[ -f "$DIR/vt.py" ] || fail '仓库缺少 vt.py:请检查 FRAMIVRA_REPO/FRAMIVRA_REF 指向的是 Framivra 本仓。'
printf '[bootstrap] 在克隆内运行 vt.py init(依赖自检 + 幂等生成 config.local.json;额外参数已原样透传)……\n'
cd -- "$DIR"
# curl|bash 的 stdin 是脚本管道,vt.py init 会把管道判为非 tty 而误降级全
# 默认;有可打开的 /dev/tty 时把 init 的 stdin 接回终端保留交互;无控制
# 终端(真无人值守)保持原 stdin,由 init 自行非终端降级,不追加旗标。
# 最终动作必须以 exec 进程替换终结(对齐标杆):若以子进程方式跑 init,
# 脚本文本耗尽后宿主 bash 会回读 fd0(已重挂为 /dev/tty)且永无 EOF——
# curl|bash 形态下脚本不退出,用户后续终端输入还会被残余 bash 当命令执行
# (红队 pty 实测:注入的 touch 与 sudo 密码均被执行,安全级缺陷)。exec
# 之后本 shell 不再存在,完成提示横幅因此前移到此处打印;init 的退出码
# 即整个脚本的退出码,经进程替换原样透传。
printf '\n[bootstrap] init 完成后即可开始使用:\n'
printf '  cd %s\n' "$DIR"
printf '  python3 vt.py doctor    # 体检;缺基础依赖可 python3 vt.py init --fix 自动装\n'
# stdin 切换与 exec 必须同在一个复合块内:bash 先从管道读完整个复合块再
# 执行,切换后不会再去终端读脚本的「下一行」导致入口中断(标杆实测坑;
# 本块之后不得再有任何脚本语句)。
{
  if [ -e /dev/tty ] && { true </dev/tty; } 2>/dev/null; then
    exec </dev/tty
  fi
  exec python3 vt.py init "$@"
}
