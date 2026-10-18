#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/safety/test_install_sh_stdin_pty.py —— install.sh stdin(cat|bash)形态 pty 冒烟。

红队阻断缺陷回归:install.sh 末块曾以子进程方式跑 vt.py init,脚本文本耗尽
后宿主 bash 回读已重挂 /dev/tty 的 fd0 且永无 EOF——curl|bash 形态下脚本不
退出,用户后续终端输入被残余 bash 当命令执行(实测 touch 与 sudo 密码会被
执行)。修复后末块以 exec 进程替换终结(对齐标杆 remote-install.sh)。本套
件在真伪终端里复现 curl|bash 形态,钉住四点:

  ① cat install.sh | bash -s -- 见完成横幅后亚秒退出 rc=0(不再需要 EOT);
  ② 横幅后向终端注入 touch 命令不被执行(进程已终结,残余输入无人消费),
     /dev/tty 重挂仍生效(init 从终端读到注入前喂入的行);
  ③ 本地文件形态(bash install.sh)与无控制终端(setsid,无 pty)形态不回归;
  ④ vt.py init 的退出码经 exec 原样透传(桩退出 7 → 脚本退出 7,参数同透传)。

依赖声明:依赖 Linux 的 pty 模块与伪终端语义(python3:import pty),非
Linux 平台整文件 skip;上游与克隆全为本地路径,零网络。

vt.py 为记录 stdin 首行/参数的桩(不触真实 init)。
"""
import os
import pty
import select
import shutil
import subprocess
import sys
import time
import unittest

try:
    from ._support import REPO, SandboxCase
except ImportError:
    from _support import REPO, SandboxCase

INSTALL_SH = REPO / "scripts" / "install.sh"
BASH = shutil.which("bash") or "/bin/bash"
# 完成横幅锚点(修复后横幅前移到 exec 之前打印;文本含「开始使用」)。
DONE_BANNER = "开始使用".encode("utf-8")

VT_PY_STDIN = ('import sys\n'
               'from pathlib import Path\n'
               'line = sys.stdin.readline()\n'
               'with Path("calls.txt").open("a") as stream:\n'
               '    stream.write("|" + line.strip() + "\\n")\n')
VT_PY_EXIT7 = ('import sys\n'
               'from pathlib import Path\n'
               'with Path("calls.txt").open("a") as stream:\n'
               '    stream.write("args=" + ",".join(sys.argv[2:]) + "\\n")\n'
               'sys.exit(7)\n')
GITIGNORE = b'config.local.json\nout/\ncalls.txt\n'


def quote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


@unittest.skipUnless(
    sys.platform.startswith("linux"),
    "stdin 形态冒烟依赖 Linux pty 模块与伪终端语义")
class InstallShStdinPty(SandboxCase):
    """真 pty 驱动 curl|bash(管道 stdin)形态,上游为本地 git 仓(零网络)。"""

    def setUp(self):
        super().setUp()
        (self.home / ".gitconfig").write_text(
            "[user]\n\tname = safety\n\temail = safety@example.invalid\n"
            "[init]\n\tdefaultBranch = main\n")
        self.upstream = self.root / "upstream"
        self.upstream.mkdir()
        (self.upstream / "vt.py").write_text(VT_PY_STDIN)
        (self.upstream / ".gitignore").write_bytes(GITIGNORE)
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-qm", "v1")
        self.clone_dir = self.root / "framivra-repo"

    # ---- 助手 ----
    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=str(cwd or self.upstream),
                              env=dict(os.environ), capture_output=True,
                              text=True, timeout=30, check=True)

    def upstream_stub(self, text):
        (self.upstream / "vt.py").write_text(text)
        self.git("add", "-A")
        self.git("commit", "-qm", "stub")

    def calls(self):
        return (self.clone_dir / "calls.txt").read_text(encoding="utf-8")

    def marker(self):
        return (self.clone_dir / ".git" / "framivra-bootstrap").read_text().strip()

    def drive_pty(self, inner, feed=b"", inject_after_banner=None,
                  deadline=60.0):
        """真 pty:inner(bash -c 命令串)以 slave 为三端并自成会话。

        feed 在启动即写入 master(横幅前入队,init 经重挂的 /dev/tty 读到);
        inject_after_banner 在横幅出现后写入(②的终端注入)。返回
        (rc, banner_seen, 横幅→退出秒数或 None, 输出字节)。
        """
        env = dict(os.environ)
        for key in ("FRAMIVRA_DIR", "FRAMIVRA_REPO", "FRAMIVRA_REF"):
            env.pop(key, None)
        env["FRAMIVRA_DIR"] = str(self.clone_dir)
        env["FRAMIVRA_REPO"] = str(self.upstream)
        env["FRAMIVRA_REF"] = "main"
        master, slave = pty.openpty()
        proc = subprocess.Popen(
            ["bash", "-c", inner], stdin=slave, stdout=slave, stderr=slave,
            preexec_fn=os.setsid, close_fds=True, cwd=self.root, env=env)
        os.close(slave)
        if feed:
            try:
                os.write(master, feed)
            except OSError:
                pass
        out, banner_at, exit_at = b"", None, None
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    chunk = b""
                if not chunk:
                    # master 读到 EOF/EIO = slave 端全关 = 进程已终结:此即
                    # 退出时刻(poll 可能晚一拍观测到)。
                    if banner_at:
                        exit_at = time.monotonic()
                    break
                out += chunk
            if banner_at is None and DONE_BANNER in out:
                banner_at = time.monotonic()
                if inject_after_banner:
                    try:
                        os.write(master, inject_after_banner)
                    except OSError:
                        pass
            if proc.poll() is not None:
                exit_at = time.monotonic()
                for _ in range(6):  # 排空残余输出后再收口
                    ready, _, _ = select.select([master], [], [], 0.1)
                    if not ready:
                        break
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    out += chunk
                break
        try:
            rc = proc.wait(timeout=10)  # EOF 后进程在退出路上,先等收尸
        except subprocess.TimeoutExpired:  # 仍存活=挂起回归,杀掉后由断言报
            proc.kill()
            rc = proc.poll()
        proc.wait(timeout=30)
        try:
            os.close(master)
        except OSError:
            pass
        elapsed = (exit_at - banner_at) if (banner_at and exit_at) else None
        return rc, banner_at is not None, elapsed, out

    # ---- ①+② 管道形态:横幅后亚秒退出 + 终端注入不被执行 ----
    def test_pipe_form_exits_after_banner_and_ignores_terminal_input(self):
        post = self.root / "POST-EXECUTED"
        rc, banner, elapsed, out = self.drive_pty(
            "cat %s | %s -s --" % (quote(INSTALL_SH), quote(BASH)),
            feed=b"FED-LINE\n",
            inject_after_banner=("touch %s\n" % quote(post)).encode("utf-8"))
        self.assertTrue(banner, "应见到完成横幅:%r" % out[-300:])
        self.assertEqual(rc, 0, "横幅后应自然退出 rc=0,输出尾:%r" % out[-300:])
        self.assertIsNotNone(elapsed, "应观测到横幅后的退出时刻")
        self.assertLess(elapsed, 1.0, "横幅后应亚秒退出,实测 %.2fs" % elapsed)
        self.assertIn("|FED-LINE", self.calls())  # /dev/tty 重挂仍生效
        time.sleep(0.5)
        self.assertFalse(post.exists(),
                         "终端注入的 touch 不得被执行(残余输入无人消费)")
        self.assertTrue(self.marker())  # 克隆与 marker 正常完成

    # ---- ④ 管道形态:init 退出码与参数经 exec 原样透传 ----
    def test_pipe_form_passes_through_init_exit_code_and_args(self):
        self.upstream_stub(VT_PY_EXIT7)
        rc, banner, _elapsed, out = self.drive_pty(
            "cat %s | %s -s -- --skip-update -- -- --json"
            % (quote(INSTALL_SH), quote(BASH)))
        self.assertTrue(banner, "应见到完成横幅:%r" % out[-300:])
        self.assertEqual(rc, 7, "init 退出码 7 应经 exec 透传,输出尾:%r"
                         % out[-300:])
        self.assertIn("args=--,--json", self.calls())  # 参数原样透传

    # ---- ③ 本地文件形态:stdin 即终端,不回归 ----
    def test_local_file_form_with_pty_terminal(self):
        rc, banner, _elapsed, out = self.drive_pty(
            "%s %s" % (quote(BASH), quote(INSTALL_SH)), feed=b"FED-LINE\n")
        self.assertTrue(banner, "应见到完成横幅:%r" % out[-300:])
        self.assertEqual(rc, 0, "本地文件形态应正常完成,输出尾:%r" % out[-300:])
        self.assertIn("|FED-LINE", self.calls())

    # ---- ③ 无控制终端(setsid,无 pty)形态:保持管道 stdin,不回归 ----
    def test_no_controlling_terminal_form_degrades_without_remount(self):
        env = dict(os.environ)
        for key in ("FRAMIVRA_DIR", "FRAMIVRA_REPO", "FRAMIVRA_REF"):
            env.pop(key, None)
        env.update({"FRAMIVRA_DIR": str(self.clone_dir),
                    "FRAMIVRA_REPO": str(self.upstream), "FRAMIVRA_REF": "main"})
        proc = subprocess.run(
            ["bash", "-c", "cat %s | setsid %s -s --"
             % (quote(INSTALL_SH), quote(BASH))],
            cwd=self.root, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0,
                         "无 tty 形态应正常完成:%r" % (proc.stderr[-300:],))
        self.assertTrue((self.clone_dir / ".git" / "framivra-bootstrap").is_file())
        # /dev/tty 打不开 → 不重挂:stub 的 stdin 仍是脚本管道(残余为 EOF)。
        self.assertIn("|", self.calls())


if __name__ == "__main__":
    unittest.main()
