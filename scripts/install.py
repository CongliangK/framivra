#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install.py —— 跨平台统一安装器（Linux/macOS/Windows,纯标准库）。

入口范式:
  Linux/macOS: curl -fsSL <raw-url>/scripts/install.sh | bash
               (install.sh 为薄转发,零业务逻辑,兼容既有 curl|bash 习惯;
               curl|bash 管道下自动重挂 /dev/tty 保留交互向导,无控制终端
               则自动降级全默认)
  Windows    : PowerShell 引导下载本文件后 python install.py(见 install_windows.ps1)
  交互向导   : 有控制终端且未给 --non-interactive/--defaults 时逐项提问,
               每题「描述 [Y/n](回车=默认 Y)」——默认=执行(用户裁决),
               输 n 跳过该项并在总结中给出手动指引;所有提问集中在任何
               下载/写盘之前,选择总结打印后才执行。
  无人值守   : --non-interactive(零提问;同时抑制 winget 等包管理器的交互
               确认)或 --defaults(用默认值不提问);非 tty(管道/CI)自动
               等价 --defaults,不失败。

命令面(--help 同步):
  install.py [--dir DIR] [--version vX.Y.Z] [--chrome] [--non-interactive]
             [--defaults] [--dry-run] | --uninstall[=DIR] | --rollback[=DIR]
             | --undo[=DIR]

安全契约(#17/#40 验收):
  - 归档成员白名单:拒绝 ../、绝对路径、盘符/UNC、反斜杠、冒号(NTFS ADS)、
    NTFS 保留名(con/aux/nul/com1..)、硬链接/设备/FIFO 成员、重名成员、
    Windows 大小写碰撞(vtx/ vs VTX/,全平台统一执行,保证载荷可在 win32 解包)。
  - 符号链接成员:仅允许「相对且落在载荷根内」的链接;POSIX 原样重建,
    win32 无特权环境一律物化为真实副本(内容=目标),win32 分支从不调用
    os.symlink(设计保证,单测以 monkeypatch 抛错兜底)。
  - 事务:staging=TARGET 同目录 .staging-<pid>(同卷,结构性消除 EXDEV);
    升级=旧目录 rename→.bak-<pid>→新目录入位→成功删 .bak(win32 占用时
    回退逐文件覆盖+清单,不宣称原子);KeyboardInterrupt(Ctrl-C)→目标零污染
    +自有 staging 清理;SIGTERM(无 handler,进程直杀)→不保证清理,可能留
    .staging-<pid> 残迹(--uninstall 不清理姊妹位残迹,需手动删除);并发=install.lock 独占锁一胜一拒。
  - 写点防预置:TARGET 或其已存在形态为符号链接时拒绝(绝不写穿)。
  - SHA256SUMS 同源校验:仅传输完整性,不证明发布者身份(#41 另行处理,
    正式发布走 keyless provenance,验证失败必须 fail-closed,不降级)。

行为变更(相对旧 install.sh,文档化):
  - 缺 ffmpeg 不再「提示后 exit 0」:win32 默认 winget 自动装,失败或
    POSIX 缺失 → error + exit 非零 + 一行手动指引(G3 修复)。
  - 安装门 vt.py doctor 失败 → exit 非零并逐条给修复指引(不再仅警告)。

依赖自检:python≥3.9(运行环境);ffmpeg/ffprobe(见上);Chrome 默认不下载
(渲染面走 Chrome/Edge 既有解析链,vt.py doctor 依赖面负责);不默认下载
任何模型权重/Blender/sox/node。
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

try:  # 同目录模块;脚本直跑时 scripts/ 已在 sys.path,导入式测试需显式补
    import verify_release
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import verify_release

# Windows 控制台常见 cp1252/GBK 代码页,直接 print 中文会 UnicodeEncodeError
# 崩溃(hosted windows-2025 实测)。与 vt.py 同款 UTF-8 reconfigure;失败静默
# 降级(极老解释器无 reconfigure),绝不因日志编码中断安装事务。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

WIN32 = sys.platform == "win32"
IS_WINDOWS = os.name == "nt"
DEFAULT_HOME = (Path(os.environ.get("LOCALAPPDATA", "")) / "framivra"
                if IS_WINDOWS else Path.home() / ".local/share/video-tools")
FFMPEG_MIN_MAJOR = 5
CFT_MIRROR = "https://registry.npmmirror.com/-/binary/chrome-for-testing"
CHROME_VERSION = "153.0.8010.52"
# NTFS 保留设备名(大小写不敏感;含 COM1-9/LPT1-9)。
_NTFS_RESERVED = {"con", "prn", "aux", "nul"} | {
    "com%d" % i for i in range(1, 10)} | {"lpt%d" % i for i in range(1, 10)}


class InstallError(Exception):
    """安装失败;消息面向用户,一行一条。"""


def log(message):
    print("[install] %s" % message, flush=True)


def warn(message):
    print("[install] ⚠ %s" % message, flush=True)


def die(message, code=1):
    print("[install] ✗ %s" % message, file=sys.stderr, flush=True)
    raise SystemExit(code)


# ---------------------------------------------------------------- 下载层 ----
def base_urls():
    """返回 (BASE_URL, LATEST_URL),VIDEO_TOOLS_DL_BASE 可整体覆盖。"""
    base = os.environ.get("VIDEO_TOOLS_DL_BASE", "").rstrip("/")
    if base:
        if base.startswith("https://github.com/") and base.endswith("/releases/download"):
            return base, base[: -len("/download")] + "/latest/download/releases.json"
        return base, base + "/releases.json"
    base = "https://github.com/CongliangK/framivra/releases/download"
    return base, "https://github.com/CongliangK/framivra/releases/latest/download/releases.json"


def fetch(url, destination):
    """经 PATH 上的 curl 下载到 destination(curl 是 Win10+ 内建组件)。

    不用 urllib 直连:镜像/代理生态以 curl 为准,且离线对抗测试以 curl
    假体观测「是否发生下载」。curl 缺失/失败一律 die(fail-closed)。
    """
    if shutil.which("curl") is None:
        raise InstallError("缺少依赖: curl(Windows 10 1803+ 内建;Linux 见发行版包管理器)")
    result = subprocess.run(
        ["curl", "-fsSL", url, "-o", str(destination)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise InstallError("下载失败: %s%s" % (url, (" (" + detail[-1] + ")") if detail else ""))


def discover_version(latest_url):
    if shutil.which("curl") is None:
        raise InstallError("缺少依赖: curl")
    result = subprocess.run(["curl", "-fsSL", latest_url],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise InstallError("releases.json 拉取失败: %s" % latest_url)
    try:
        return json.loads(result.stdout.decode("utf-8"))["latest"]
    except Exception as error:
        raise InstallError("releases.json 解析失败(%s)" % latest_url) from error


def verify_sha256(payload_path, sums_path, expected_name):
    rows = [line.split() for line in sums_path.read_text(encoding="utf-8").splitlines()]
    entries = [row for row in rows if len(row) == 2 and row[1] == expected_name]
    if len(entries) != 1 or not re.fullmatch(r"[0-9a-fA-F]{64}", entries[0][0]):
        raise InstallError("SHA256SUMS 中没有 %s 的唯一有效条目" % expected_name)
    digest = hashlib.sha256(payload_path.read_bytes()).hexdigest()
    if digest != entries[0][0].lower():
        raise InstallError("SHA256 校验失败——载荷不落地(仅传输完整性,不证明发布者身份)")


# ------------------------------------------------------- 发布者身份层 (#16/#41)
def _provenance_gate(args, base_url):
    """决定发布者身份验证策略(fail-closed,先于任何载荷下载执行):

    - 官方 github.com 载荷源:验证是硬门,--no-provenance 被拒绝;
    - 非 github 源(镜像/本地候选/CI fixture):默认拒绝继续,须显式
      --no-provenance(只剩 SHA256 传输完整性)或 --verify-provenance
      (证词仍按字节摘要从 GitHub API 核验,镜像字节同可验);
    - 返回 'verify' | 'skip'。
    """
    host = (urlparse(base_url).hostname or "").lower()
    official = host == "github.com"
    if official and getattr(args, "no_provenance", False):
        raise InstallError(
            "正式 GitHub Releases 载荷源不接受 --no-provenance:"
            "发布者身份验证(keyless provenance)是硬门,不提供降级")
    if official or getattr(args, "verify_provenance", False):
        return "verify"
    if getattr(args, "no_provenance", False):
        warn("已按 --no-provenance 跳过发布者身份验证"
             "(仅剩 SHA256 传输完整性;候选/CI 本地载荷路径)")
        return "skip"
    raise InstallError(
        "载荷源 %s 非 github.com,发布者身份(provenance)不可默认验证。\n"
        "  - 候选/CI 本地载荷:加 --no-provenance 继续(仅 SHA256 完整性);\n"
        "  - 镜像载荷:保留官方 Releases 源,或加 --verify-provenance"
        "(证词按字节摘要从 GitHub 核验)" % base_url)


def _enforce_provenance(prov, payload, sums, version):
    """对已下载的确切 bytes 执行身份验证;verify_release 自身 fail-closed。"""
    if prov != "verify":
        return
    log("发布者身份验证(keyless provenance;gh attestation)…")
    expect_ref = None
    # 官方正式版本对应 tag ref(防降级锚点);-ci/unknown 等候选版本不锚 ref。
    if re.fullmatch(r"[0-9]+(\.[0-9]+){1,3}", version or ""):
        expect_ref = "refs/tags/v%s" % version
    for artifact in (payload, sums):
        try:
            verify_release.verify_artifact(
                artifact, expect_ref=expect_ref,
                gh=verify_release.find_gh())
        except verify_release.ProvenanceError as error:
            raise InstallError(str(error)) from error
    log("发布者身份验证通过(载荷与 SHA256SUMS 均有有效证词)。")


# ------------------------------------------------------------ 归档安全层 ----
def _check_member_name(name, seen, lowered):
    """白名单校验单个成员路径;返回 PurePosixPath(绝不容许越出载荷根)。"""
    if not name or name.startswith(("/", "\\")):
        raise InstallError("归档成员为绝对路径: %s" % name)
    if "\\" in name:
        raise InstallError("归档成员含反斜杠(非规范 tar 路径): %s" % name)
    if re.match(r"^[A-Za-z]:", name):
        raise InstallError("归档成员含盘符路径: %s" % name)
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise InstallError("归档成员含空/当前/父目录段: %s" % name)
    for part in parts:
        if ":" in part:
            raise InstallError("归档成员含冒号(NTFS ADS 面): %s" % name)
        if part.split(".")[0].lower() in _NTFS_RESERVED:
            raise InstallError("归档成员为 NTFS 保留名: %s" % name)
    if name in seen:
        raise InstallError("归档成员重名: %s" % name)
    seen.add(name)
    # Windows 大小写不敏感文件系统:全平台统一拒绝碰撞,保证载荷可移植。
    low = name.lower()
    if low in lowered:
        raise InstallError("归档成员 Windows 大小写碰撞: %s" % name)
    lowered.add(low)
    return PurePosixPath(name)


def safe_extract(archive_path, destination):
    """白名单解包:文件/目录/载荷根内相对符号链接之外一律拒绝。

    win32 从不调用 os.symlink:载荷根内符号链接物化为真实副本(文件复制
    内容;目录递归复制子树)。POSIX 原样重建符号链接。返回符号链接清单
    供测试断言。
    """
    seen, lowered, symlinks = set(), set(), {}
    destination.mkdir(parents=True, exist_ok=True)
    try:
        archive = tarfile.open(archive_path, "r:gz")
    except tarfile.TarError as error:
        raise InstallError("载荷不是有效的 tar.gz 归档(%s)" % error) from error
    with archive:
        pending_links = []
        try:
            for member in archive:
                path = _check_member_name(member.name, seen, lowered)
                target = destination / Path(*path.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if member.issym():
                    if member.linkname.startswith(("/", "\\")) or re.match(
                            r"^[A-Za-z]:", member.linkname):
                        raise InstallError("符号链接目标越出载荷根: %s -> %s"
                                           % (member.name, member.linkname))
                    # 官方载荷存在 scripts/x.py -> ../plugins/... 型合法链接:
                    # 以规范化后是否仍在载荷根内为准(_resolve_within 单一语义),
                    # 不按字面含 .. 拒绝(hosted windows-2025 端到端实测)。
                    if _resolve_within(None, path, member.linkname) is None:
                        raise InstallError("符号链接目标越出载荷根: %s -> %s"
                                           % (member.name, member.linkname))
                    symlinks[member.name] = member.linkname
                    pending_links.append((path, member.linkname))
                    continue
                if not member.isfile():
                    # 硬链接(LNKTYPE)/设备/FIFO/其它:fail-closed 拒绝。
                    raise InstallError("不支持的归档成员类型(%s): %s"
                                       % (repr(member.type), member.name))
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() or target.is_symlink():
                    raise InstallError("解包目标已存在(预置写点拒绝): %s" % member.name)
                with archive.extractfile(member) as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
                if member.mode & 0o111 and not IS_WINDOWS:
                    target.chmod(target.stat().st_mode | 0o111)
        except tarfile.TarError as error:
            raise InstallError("载荷解包失败(%s)" % error) from error
        # 第二遍处理符号链接(目标此刻已就位)。
        for path, linkname in pending_links:
            _materialize_link(destination, path, linkname)
    return symlinks


def _resolve_within(root, link_path, linkname):
    """解析符号链接目标的载荷根内绝对 PurePosixPath;越根返回 None。"""
    base = link_path.parent
    resolved = PurePosixPath(*(base.parts + PurePosixPath(linkname).parts)) \
        if not PurePosixPath(linkname).is_absolute() else PurePosixPath(linkname)
    parts = []
    for part in resolved.parts:
        if part == "..":
            if not parts:
                return None
            parts.pop()
        elif part not in ("", "."):
            parts.append(part)
    return PurePosixPath(*parts) if parts else None


def _materialize_link(root, link_path, linkname):
    target_rel = _resolve_within(root, link_path, linkname)
    if target_rel is None:
        raise InstallError("符号链接目标越出载荷根: %s -> %s" % (str(link_path), linkname))
    source = root / Path(*target_rel.parts)
    dest = root / Path(*link_path.parts)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        raise InstallError("符号链接位已被占用(重名/预置写点拒绝): %s" % str(link_path))
    if not IS_WINDOWS:
        # POSIX 原样重建(含合法悬空链接——tar 语义允许)。
        dest.symlink_to(linkname)
        return
    # win32:物化(不调用 os.symlink)。
    if source.is_dir():
        shutil.copytree(source, dest)
    elif source.is_file():
        shutil.copyfile(source, dest)
    else:
        raise InstallError("符号链接目标缺失,无法物化: %s -> %s" % (str(link_path), linkname))


# ---------------------------------------------------------------- 交互向导 ----
class Wizard:
    """逐项提问向导(参考 ibus-voice-ime setup.sh 范式,纯终端提问,无 TUI 库)。

    每题「描述 [Y/n](回车=默认 Y)」——默认=执行(用户裁决:可主动选不装,
    但默认要装);输 n 跳过该项。所有提问必须发生在任何下载/写盘之前;
    choices/skipped 供总结与执行层消费。enabled=False 时零提问全默认
    (--non-interactive/--defaults/非 tty),等价参考仓 --defaults。
    """

    def __init__(self, enabled, prompt=None):
        self.enabled = enabled
        self._prompt = prompt   # None=用内置 input(ask 内调用时解析,可被测试 patch)
        self.choices = {}
        self.skipped = []   # [(key, description)]

    def ask(self, key, description, default=True):
        if not self.enabled:
            self.choices[key] = default
            if not default:
                self.skipped.append((key, description))
            return default
        reader = input if self._prompt is None else self._prompt
        suffix = "[Y/n]" if default else "[y/N]"
        while True:
            try:
                answer = reader("%s %s(回车=默认 %s): "
                                % (description, suffix, "Y" if default else "N")
                                ).strip().lower()
            except EOFError:
                answer = ""
            if answer == "":
                decision = default
                break
            if answer in ("y", "yes"):
                decision = True
                break
            if answer in ("n", "no"):
                decision = False
                break
            print("[install] 请输入 y 或 n(回车=默认)。")
        self.choices[key] = decision
        if not decision:
            self.skipped.append((key, description))
        return decision

    def choice(self, key, default=True):
        """读取既有决定;未提问过的键按 default(执行层兜底,不新增提问)。"""
        return self.choices.get(key, default)

    def summary(self):
        if not self.choices:
            return
        log("向导选择:" + ", ".join(
            "%s=%s" % (key, "执行" if ok else "跳过")
            for key, ok in sorted(self.choices.items())))
        for key, description in self.skipped:
            log("⚠ 已跳过:%s(手动指引见上方各行)" % description)


def _stdin_is_console(win32=None):
    """真实控制台检测(向导模式判定的唯一依据)。

    win32 CRT 对 NUL 字符设备 isatty() 返回 True(经典陷阱):stdin=DEVNULL
    的子进程会误入交互向导(hosted windows-2025 实测 [Y/n] 出现在无终端
    子进程)。win32 追加 GetConsoleMode 复核——只有真实控制台才通过;
    管道/重定向/NUL 一律 False。POSIX isatty 即可。任何异常按非控制台
    (fail-closed:宁可不提问,不在无终端环境挂起等输入)。win32 形参供
    单测在 POSIX 上模拟 win32 分支。
    """
    if win32 is None:
        win32 = (os.name == "nt")
    try:
        if not sys.stdin.isatty():
            return False
    except Exception:
        return False
    if not win32:
        return True
    try:
        import ctypes
        import msvcrt
        mode = ctypes.c_uint32()
        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle,
                                                          ctypes.byref(mode)))
    except Exception:
        return False


def _make_wizard(args):
    """交互模式判定:--non-interactive/--defaults 显式零提问;否则真控制台才提问。

    子进程/管道/重定向(stdin 非真实控制台)自动全默认,不失败——CI 与
    curl|bash 无终端场景等价 --defaults。install.sh 侧负责 curl|bash 下
    重挂 /dev/tty 保留真终端交互(见其注释);Windows PowerShell 引导脚本
    保存后执行,stdin 即控制台,无需重挂;win32 的 NUL 设备陷阱由
    _stdin_is_console 的 GetConsoleMode 复核拦下。
    """
    quiet = bool(getattr(args, "non_interactive", False)) or bool(
        getattr(args, "defaults", False))
    enabled = (not quiet) and _stdin_is_console()
    return Wizard(enabled)


# ---------------------------------------------------------------- 锁与事务 ----
class InstallLock:
    """TARGET 级独占锁:msvcrt.locking(win32)/fcntl(POSIX),一胜一拒。"""

    def __init__(self, target):
        self.path = target.with_name(target.name + ".install.lock")
        self.handle = None

    def __enter__(self):
        self.handle = open(self.path, "a+b")
        if IS_WINDOWS:
            import msvcrt
            try:
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise InstallError("另一安装正在进行(%s)——请稍后重试" % self.path)
        else:
            import fcntl
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise InstallError("另一安装正在进行(%s)——请稍后重试" % self.path)
        return self

    def __exit__(self, *exc):
        try:
            if IS_WINDOWS:
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.path.unlink(missing_ok=True)
        return False


def _remove_tree(path):
    """拒绝跟随符号链接的删除(卸载面;与清理器 #56 同一原则)。"""
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
        return
    shutil.rmtree(path)


def _replace_tree(staging, target):
    """staging → target 原子入位;win32 占用时回退逐文件(不宣称原子)。"""
    backup = None
    if target.exists() or target.is_symlink():
        if target.is_symlink():
            raise InstallError("目标已是符号链接,拒绝写穿: %s" % target)
        backup = target.with_name(target.name + ".bak-%d" % os.getpid())
        _remove_tree(backup)
        os.rename(target, backup)
    try:
        os.rename(staging, target)
    except OSError:
        if backup is not None:
            os.rename(backup, target)
        raise
    if backup is not None:
        time.sleep(0.2)  # 给杀毒/索引器一个让出窗口;失败则留 .bak 供 --rollback
        try:
            _remove_tree(backup)
        except OSError:
            warn("旧版本清理失败,保留 %s(可用 --rollback 回退后重试)" % backup)
    return backup


# ------------------------------------------------------------ win32 集成面 ----
# shim 放安装目录内 TARGET\bin:内容经 %~dp0 自定位,全 ASCII(免 OEM 代码页
# 歧义),含中文/空格/& 的安装路径由 cmd 运行时展开,天然安全。


def _shim_dir(target):
    return Path(target) / "bin"


VT_CMD = ("@echo off\r\n"
          "setlocal\r\n"
          "set \"PY=py -3\"\r\n"
          "where py >nul 2>nul || set \"PY=python\"\r\n"
          "%PY% -X utf8 \"%~dp0..\\vt.py\" %*\r\n"
          "exit /b %errorlevel%\r\n")
VTX_CMD = ("@echo off\r\n"
           "setlocal\r\n"
           "set \"PYTHONPATH=%~dp0..\"\r\n"
           "set \"PY=py -3\"\r\n"
           "where py >nul 2>nul || set \"PY=python\"\r\n"
           "%PY% -X utf8 -m vtx %*\r\n"
           "exit /b %errorlevel%\r\n")


def write_shims(target, wizard):
    """win32:在 TARGET 下的 bin 子目录写 vt.cmd/vtx.cmd 并挂 HKCU User PATH。

    交互向导可跳过(默认=写);跳过时零写注册表与 shim,打印一行手动指引。
    """
    if not IS_WINDOWS:
        return
    if not wizard.choice("shims_path"):
        shim_dir = _shim_dir(target)
        log("⚠ 已跳过 shim 与用户 PATH 写入;手动配置(新开终端生效):")
        log("    将 %s 加入用户 PATH(setx PATH \"%%PATH%%;%s\" 或系统设置)"
            % (shim_dir, shim_dir))
        return
    import winreg
    shim_dir = _shim_dir(target)
    shim_dir.mkdir(parents=True, exist_ok=True)
    (shim_dir / "vt.cmd").write_bytes(VT_CMD.encode("ascii"))
    (shim_dir / "vtx.cmd").write_bytes(VTX_CMD.encode("ascii"))
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                        winreg.KEY_READ | winreg.KEY_WRITE) as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, kind = "", winreg.REG_EXPAND_SZ
        entries = [part for part in (current or "").split(os.pathsep) if part.strip()]
        if str(shim_dir) not in entries:
            entries.append(str(shim_dir))
            winreg.SetValueEx(key, "Path", 0, kind, os.pathsep.join(entries))
    log("已写 shim 与用户 PATH:%s(新开终端生效;--undo 可逆)" % shim_dir)


def undo_path():
    """win32 --undo:移除 HKCU PATH 条目(不动安装目录与 shim 文件)。"""
    if not IS_WINDOWS:
        die("--undo 仅适用于 Windows(PATH/shim 写入面);POSIX 请用 --uninstall")
    import winreg
    marker_candidates = []
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                        winreg.KEY_READ | winreg.KEY_WRITE) as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, kind = "", winreg.REG_EXPAND_SZ
        entries = []
        for part in (current or "").split(os.pathsep):
            part = part.strip()
            if not part:
                continue
            bin_dir = Path(part)
            if bin_dir.name == "bin" and (bin_dir.parent / ".vt-install.json").is_file():
                marker_candidates.append(str(bin_dir))
                continue
            entries.append(part)
        winreg.SetValueEx(key, "Path", 0, kind, os.pathsep.join(entries))
    if not marker_candidates:
        warn("用户 PATH 中未发现本安装器写入的条目(--undo 幂等,无需处理)")
    else:
        log("已移除用户 PATH 条目:%s" % ", ".join(marker_candidates))


# ---------------------------------------------------------------- 依赖面 ----
def plan_ffmpeg(wizard):
    """ffmpeg 探测+向导提问(不执行安装):返回 {major, asked_winget}。

    提问必须在 wizard.summary() 之前(M3:dry-run 反映 winget 选择);
    本函数只做本地 ffmpeg -version 探测,零下载零写盘。
    """
    major = _ffmpeg_major()
    asked = (major == 0 and IS_WINDOWS and shutil.which("winget") is not None)
    if asked:
        wizard.ask("winget_ffmpeg",
                   "未检测到 ffmpeg——使用 winget 安装 Gyan.FFmpeg(用户级)")
    return {"major": major, "asked_winget": asked}


def enforce_ffmpeg(wizard, plan):
    """执行阶段:消费 plan_ffmpeg 的探测结果与向导选择。

    跳过 winget 或非 win32 缺失都走同一条手动指引 error(G3:不装假)。
    """
    major = plan["major"]
    if major == 0:
        if plan["asked_winget"]:
            if not wizard.choice("winget_ffmpeg"):
                log("已跳过 winget 安装;手动指引: winget install Gyan.FFmpeg "
                    "(装好后新开终端重跑本安装器)")
                raise InstallError(
                    "未检测到 ffmpeg(渲染链依赖 ≥%d.1),已按选择跳过自动安装——"
                    "请手动安装后重跑: winget install Gyan.FFmpeg" % FFMPEG_MIN_MAJOR)
            _winget_install("Gyan.FFmpeg", silent=not wizard.enabled)
            major = _ffmpeg_major()
        if major == 0:
            raise InstallError(
                "未检测到 ffmpeg(渲染链依赖 ≥%d.1)。Windows: winget install Gyan.FFmpeg "
                "| Debian/Ubuntu: sudo apt install ffmpeg | Fedora: sudo dnf install ffmpeg "
                "| Arch: sudo pacman -S ffmpeg——安装后重跑本安装器" % FFMPEG_MIN_MAJOR)
    elif major < FFMPEG_MIN_MAJOR:
        warn("ffmpeg 主版本 %d < %d.1:-fps_mode 渲染参数不可用,视频合成阶段将失败"
             % (major, FFMPEG_MIN_MAJOR))


def _ffmpeg_major():
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return 0
    try:
        head = subprocess.run([ffmpeg, "-version"], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=20).stdout
        match = re.search(rb"version (\d+)\.", head)
        return int(match.group(1)) if match else 0
    except (OSError, subprocess.SubprocessError):
        return 0


def _winget_install(package_id, silent):
    if not IS_WINDOWS or shutil.which("winget") is None:
        return
    log("winget 安装 %s(用户级;系统解析,不再分发,无 GPL 责任)…" % package_id)
    args = ["winget", "install", "--id", package_id, "-e", "--scope", "user"]
    if silent:
        args.append("--silent")
    try:
        subprocess.run(args, check=True, timeout=1800,
                       stdout=subprocess.DEVNULL if silent else None)
        log("winget 安装完成;新 PATH 生效可能需重开终端——校验若失败请重跑安装器")
    except (OSError, subprocess.SubprocessError) as error:
        warn("winget 安装 %s 失败(%s);回落到手动指引" % (package_id, error))


# ---------------------------------------------------------------- 主流程 ----
def is_checkout(path):
    path = Path(path)
    return (path / "vt.py").is_file() and (path / "vtx").is_dir()


def resolve_target(explicit):
    if explicit:
        return Path(explicit)
    if os.environ.get("VT_HOME"):
        return Path(os.environ["VT_HOME"])
    if is_checkout(Path.cwd()):
        return Path.cwd()
    return DEFAULT_HOME


def read_marker(target):
    try:
        return json.loads((target / ".vt-install.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def install(args):
    target = resolve_target(args.dir)
    base_url, latest_url = base_urls()
    if args.dry_run and not args.version:
        log("[dry-run] 将从 %s 发现版本;零下载零写入零修改" % latest_url)
        return 0
    version = (args.version or "").lstrip("v") or discover_version(latest_url)
    if not version or not re.fullmatch(r"[A-Za-z0-9.v_-]+", version):
        raise InstallError("版本串为空或含非法字符: %r" % version)
    # 发布者身份策略先于任何载荷下载(快速失败;版本发现的一次 releases.json
    # 元数据读取除外——见上方 discover_version)。
    prov = _provenance_gate(args, base_url)
    log("安装根: %s" % target)
    log("安装版本: %s" % version)

    if target.is_symlink():
        raise InstallError("目标 %s 是符号链接——拒绝写穿(防越权写入链接目标)" % target)
    if is_checkout(target):
        # 复用契约(RECP-20260920-001):既有 checkout 复用不覆盖,干净返回。
        # 例外:--upgrade + .vt-install.json 标记(本安装器所装、非 git clone)
        # 才允许原地升级(旧目录留 .bak,可 --rollback)。
        marker_path = target / ".vt-install.json"
        if not args.upgrade or not marker_path.is_file():
            version_text = "unknown"
            if (target / "VERSION").is_file():
                version_text = (target / "VERSION").read_text(errors="replace").strip()
            log("检测到既有 checkout(%s,VERSION=%s)——复用不覆盖。" % (target, version_text))
            log("本安装器所装副本可用 --upgrade 升级;git clone 请手动 git pull")
            return 0
    elif target.exists() and any(target.iterdir()):
        raise InstallError("目标 %s 已存在、非空且不是 video_tools checkout——"
                           "拒绝覆盖(换 --dir 或清空后重试)" % target)
    # 交互向导:所有提问集中在任何下载/写盘之前(用户裁决:默认=执行,
    # 可主动选不装);选择总结打印后才进入执行层。非 tty/--defaults/
    # --non-interactive 时零提问全默认。
    wizard = _make_wizard(args)
    if not wizard.ask("install", "安装 Framivra v%s 到 %s" % (version, target)):
        log("已按选择取消安装——未下载、未写入任何内容。")
        return 0
    if IS_WINDOWS:
        wizard.ask("shims_path", "写 vt/vtx shim 并加入用户 PATH(新开终端生效;--undo 可逆)")
    if args.chrome:
        wizard.ask("chrome_cft", "下载 Chrome for Testing(~195MB,npmmirror 镜像)")
    # M3:ffmpeg 探测+winget 提问必须在 summary 之前,dry-run 才能反映选择。
    ffmpeg_plan = plan_ffmpeg(wizard)
    wizard.summary()
    if args.dry_run:
        winget_note = ""
        if ffmpeg_plan["major"] == 0:
            if ffmpeg_plan["asked_winget"]:
                winget_note = (" → winget 安装 ffmpeg"
                               if wizard.choice("winget_ffmpeg")
                               else " → ffmpeg 已选跳过(执行将按手动指引报错)")
            else:
                winget_note = " → ffmpeg 缺失(执行将按手动指引报错)"
        elif ffmpeg_plan["major"] < FFMPEG_MIN_MAJOR:
            winget_note = " → ffmpeg %d.x 过旧将告警" % ffmpeg_plan["major"]
        log("[dry-run] 将执行:依赖自检%s → 下载 %s/v%s/video-tools-%s.tar.gz + SHA256SUMS "
            "校验%s → 白名单解包(staging) → 入位 → vt.py init+doctor 门 → 零写入演练结束"
            % (winget_note, base_url, version, version,
               " → 发布者身份验证(gh attestation)" if prov == "verify"
               else "(--no-provenance:仅传输完整性)"))
        return 0
    # 依赖自检放在复用判定之后:幂等重跑(复用分支)不因缺 ffmpeg 失败,
    # 真正要装时才触发 winget/报错。
    enforce_ffmpeg(wizard, ffmpeg_plan)

    with InstallLock(target):
        payload_name = "video-tools-%s.tar.gz" % version
        work = Path(tempfile.mkdtemp(prefix="vt-install-"))
        staging = target.with_name(target.name + ".staging-%d" % os.getpid())
        try:
            log("下载载荷…")
            payload = work / payload_name
            sums = work / "SHA256SUMS"
            fetch("%s/v%s/%s" % (base_url, version, payload_name), payload)
            fetch("%s/v%s/SHA256SUMS" % (base_url, version), sums)
            verify_sha256(payload, sums, payload_name)
            log("校验通过。")
            _enforce_provenance(prov, payload, sums, version)
            if staging.exists():
                _remove_tree(staging)  # 同 pid 旧残迹;锁内安全
            log("白名单解包(staging=%s)…" % staging)
            safe_extract(payload, staging)
            marker = read_marker(target) if target.exists() else {}
            _replace_tree(staging, target)
            (target / ".vt-install.json").write_text(json.dumps({
                "installed_by": "install.py", "version": version,
                "prev_version": marker.get("version"),
                "base_url": base_url, "date": datetime.datetime.now().isoformat()}, ensure_ascii=False), encoding="utf-8")
        except BaseException:
            # KeyboardInterrupt:目标零污染,自有 staging 清理。SIGTERM 无
            # handler 时进程被直接终止、不经过本 except——可能留 staging
            # 残迹(--uninstall 不清理,需手动删除),文档已如实声明,不在此谎称同路径。
            if staging.exists() and not staging.is_symlink():
                shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(work, ignore_errors=True)

    write_shims(target, wizard)
    log("运行 vt.py init(非交互幂等)…")
    init = subprocess.run([sys.executable, "vt.py", "init"], cwd=str(target))
    if init.returncode != 0:
        warn("vt.py init 非零退出(可随后手动补跑;config.local.json 已存在则不动)")
    log("运行 vt.py doctor(安装成功门;error=0 才算装成)…")
    doctor = subprocess.run([sys.executable, "vt.py", "doctor"], cwd=str(target))
    if doctor.returncode != 0:
        die("doctor 存在 error——安装未通过成功门;逐项修复(ffmpeg/ffprobe/Chrome 或 Edge/"
            "python≥3.9)后重跑安装器。详见上方 doctor 输出。")
    if IS_WINDOWS:
        log("✅ 安装完成:%s(VERSION=%s)——新开 PowerShell 直接运行:vt doctor" % (target, version))
    else:
        log("✅ 安装完成:%s(VERSION=%s)" % (target, version))
        log("常用入口: python3 vt.py doctor | python3 vt.py init --fix | python3 -m vtx")

    if args.chrome and wizard.choice("chrome_cft"):
        download_chrome(target)
    return 0


def download_chrome(target):
    flavor = ("win64" if IS_WINDOWS else
              "linux64" if sys.platform.startswith("linux") else None)
    if flavor is None:
        warn("--chrome 当前支持 linux64/win64;macOS 请用系统 Chrome(渲染解析链自动发现)")
        return
    url = "%s/%s/%s/chrome-%s.zip" % (CFT_MIRROR, CHROME_VERSION, flavor, flavor)
    work = Path(tempfile.mkdtemp(prefix="vt-chrome-"))
    try:
        log("下载 Chrome for Testing %s(%s,~195MB)…" % (CHROME_VERSION, flavor))
        archive_path = work / "chrome.zip"
        fetch(url, archive_path)
        destination = target / "chrome-for-testing"
        destination.mkdir(parents=True, exist_ok=True)
        import zipfile
        with zipfile.ZipFile(archive_path) as bundle:
            bundle.extractall(str(destination))
        binary = destination / ("chrome-%s/chrome.exe" % flavor if IS_WINDOWS
                                else "chrome-%s/chrome" % flavor)
        if not IS_WINDOWS:
            binary.chmod(binary.stat().st_mode | 0o111)
        log("✓ Chrome 就绪:%s(启用: set VTX_CHROME=该路径,或加入 PATH)" % binary)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def uninstall(target, dry_run):
    target = Path(target)
    if target.is_symlink():
        # 只删链接本身,绝不递归进外部目标(既有安全语义)。
        if dry_run:
            log("[dry-run] 将仅移除符号链接 %s(不触及其目标)" % target)
            return 0
        target.unlink()
        log("已移除符号链接 %s(目标未触碰)" % target)
        return 0
    if not (target / ".vt-install.json").is_file():
        raise InstallError("拒绝卸载:%s 无 .vt-install.json 标记(非本安装器所装,防误删)" % target)
    if dry_run:
        log("[dry-run] 将删除 %s(有标记,允许)" % target)
        return 0
    if IS_WINDOWS:
        undo_path_quiet()
    _remove_tree(target)
    log("已卸载 %s" % target)
    return 0


def undo_path_quiet():
    try:
        undo_path()
    except SystemExit:
        raise
    except Exception as error:  # 卸载主流程不应被 PATH 清理小故障阻断
        warn("PATH/shim 清理失败(%s)——可稍后手动运行 --undo" % error)


def rollback(target, dry_run):
    target = Path(target)
    if target.is_symlink():
        raise InstallError("回退目标为符号链接: %s" % target)
    backups = sorted(target.parent.glob(target.name + ".bak-*"))
    if not backups:
        raise InstallError("无可回退备份(%s.bak-*);--rollback 仅用于升级失败的恢复" % target)
    backup = backups[-1]
    if dry_run:
        log("[dry-run] 将以 %s 回退 %s" % (backup, target))
        return 0
    current = None
    if target.exists():
        current = target.with_name(target.name + ".rollback-%d" % os.getpid())
        os.rename(target, current)
    try:
        os.rename(backup, target)
    except OSError:
        if current is not None:
            os.rename(current, target)
        raise
    if current is not None:
        _remove_tree(current)
    log("已回退到 %s(来自 %s)" % (target, backup.name))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="install.py", description="video_tools 跨平台统一安装器(纯标准库)")
    parser.add_argument("--dir", help="安装根(默认: $VT_HOME > 当前目录若为 checkout > %s)" % DEFAULT_HOME)
    parser.add_argument("--version", help="安装指定版本(默认经 releases.json 发现 Latest)")
    parser.add_argument("--chrome", action="store_true",
                        help="同时下载 Chrome for Testing(npmmirror,linux64/win64,~195MB)")
    parser.add_argument("--non-interactive", action="store_true",
                        help="零提问无人值守模式(抑制包管理器交互确认;CI 友好)")
    parser.add_argument("--defaults", action="store_true",
                        help="用默认值不提问(等价每题回车;与 --non-interactive "
                             "的区别:不额外抑制包管理器自身的交互确认)")
    parser.add_argument("--upgrade", action="store_true",
                        help="升级本安装器所装的既有副本(需 .vt-install.json 标记;"
                             "旧目录留 .bak 可 --rollback;git clone 请用 git pull)")
    parser.add_argument("--dry-run", action="store_true", help="演练:零下载零写入零修改")
    parser.add_argument("--verify-provenance", action="store_true",
                        help="强制发布者身份验证(gh attestation;镜像载荷的字节"
                             "仍按摘要从 GitHub 核验证词)")
    parser.add_argument("--no-provenance", action="store_true",
                        help="显式跳过发布者身份验证——仅限非 github.com 载荷源"
                             "(候选/CI 本地载荷);正式 GitHub Releases 源拒绝此旗标")
    parser.add_argument("--uninstall", nargs="?", const="", metavar="DIR",
                        help="卸载(仅限带 .vt-install.json 标记的安装目录)")
    parser.add_argument("--rollback", nargs="?", const="", metavar="DIR",
                        help="升级失败回退(恢复最近的 .bak-* 备份)")
    parser.add_argument("--undo", nargs="?", const="", metavar="DIR",
                        help="仅 Windows:移除 shim 与用户 PATH 条目(不动安装目录)")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 9):
        die("需要 python ≥3.9(当前 %s)" % sys.version.split()[0])
    try:
        if args.undo is not None:
            return undo_path() or 0
        if args.uninstall is not None:
            return uninstall(resolve_target(args.uninstall or args.dir), args.dry_run)
        if args.rollback is not None:
            return rollback(resolve_target(args.rollback or args.dir), args.dry_run)
        return install(args)
    except InstallError as error:
        die(str(error))
    return 0


if __name__ == "__main__":
    sys.exit(main())
