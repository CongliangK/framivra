#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/safety/test_install_sh_bootstrap.py —— install.sh 克隆引导(POSIX)对抗测试。

install.sh 已从「薄转发 install.py(发版通道,仓从未发版,实际不可用)」重构为
Linux/macOS 一键安装引导(克隆/安全更新仓库 + 克隆内运行 vt.py init),语义对齐
install_windows.ps1(克隆模式)与标杆 ibus-voice-ime remote-install.sh。本套件用
本地仓库模拟上游(离线,零网络),钉住四大场景与 fail-closed 守卫:

  - 新装:浅克隆 + marker(.git/framivra-bootstrap)写入 + 克隆内 vt.py init
    (参数原样透传,-- 之前的未知参数同样透传);
  - 干净更新:显式 refspec 浅 fetch + 全限定 ref reset --hard + marker 刷新;
  - 脏拒绝:porcelain(含未跟踪)非空 → 拒绝更新,工作区零改动;
  - 碰撞拒绝(红线):ignored→tracked(config.local.json 必然场景)porcelain
    看不见 → 预检拒绝,本地被忽略文件不被 reset --hard 覆盖;
  - 附:--skip-update 脏克隆放行、本地提交 marker 保护、数据根/相对路径/
    非法变量/符号链接/origin 不匹配 fail-closed、python3/git 缺失指引、
    bash3.2 禁用特性静态哨兵。

红队第二轮存活项补强:数据根变体在无 realpath 的 PATH 下同样拒绝(macOS
无 GNU realpath -m,守卫不得依赖外部命令)、git status 自身失败显式
fail(不得当作干净放行)、.git 为文件的 git worktree 链接工作树拒绝、
上游同名标签(origin/<ref>)不得劫持 reset、FRAMIVRA_REF 分支切换;stdin
(cat|bash)脚本形态的 pty 冒烟见 test_install_sh_stdin_pty.py。

vt.py 为记录参数的桩(不触真实 init);上游默认分支 main。
"""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

try:
    from ._support import REPO, SandboxCase
except ImportError:
    from _support import REPO, SandboxCase

INSTALL_SH = REPO / "scripts" / "install.sh"
# 子进程按子 env 的 PATH 解析可执行名;PATH 被替换的用例须用绝对路径。
BASH = shutil.which("bash") or "/bin/bash"

VT_PY = (b'import sys\n'
         b'from pathlib import Path\n'
         b'with Path("calls.txt").open("a") as stream:\n'
         b'    stream.write(",".join(sys.argv[2:]) + "\\n")\n')
GITIGNORE = b'config.local.json\nout/\ncalls.txt\n'


class InstallShBootstrap(SandboxCase):
    """本地 bare 风格上游 + 真实 git/bash/install.sh 全链路(零网络)。"""

    def setUp(self):
        super().setUp()
        (self.home / ".gitconfig").write_text(
            "[user]\n\tname = safety\n\temail = safety@example.invalid\n"
            "[init]\n\tdefaultBranch = main\n")
        self.upstream = self.root / "upstream"
        self.upstream.mkdir()
        (self.upstream / "vt.py").write_bytes(VT_PY)
        (self.upstream / ".gitignore").write_bytes(GITIGNORE)
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-qm", "v1")
        self.clone_dir = self.root / "framivra-repo"

    # ---- 助手 ----
    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=str(cwd or self.upstream),
                              env=self.env, capture_output=True, text=True,
                              timeout=30, check=True)

    def git_out(self, *args, cwd=None):
        return self.git(*args, cwd=cwd).stdout.strip()

    def upstream_commit(self, name, files):
        for rel, content in files.items():
            path = self.upstream / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.git("add", "-A")
        self.git("commit", "-qm", name)

    def bootstrap(self, *args, **env_over):
        env = dict(self.env)
        for key in ("FRAMIVRA_DIR", "FRAMIVRA_REPO", "FRAMIVRA_REF"):
            env.pop(key, None)  # 防开发者外层 shell 泄漏同名变量
        env.setdefault("FRAMIVRA_DIR", str(self.clone_dir))
        env.setdefault("FRAMIVRA_REPO", str(self.upstream))
        env.setdefault("FRAMIVRA_REF", "main")
        env.update(env_over)
        return subprocess.run(
            [BASH, str(INSTALL_SH), *args], cwd=self.root, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=60)

    def marker(self):
        return (self.clone_dir / ".git" / "framivra-bootstrap").read_text().strip()

    def calls(self):
        return (self.clone_dir / "calls.txt").read_text()

    def output(self, result):
        return (result.stdout or "") + (result.stderr or "")

    # ---- 场景一:新装 ----
    def test_fresh_install_clones_runs_init_writes_marker(self):
        result = self.bootstrap()
        self.assert_ok(result)
        self.assertTrue((self.clone_dir / "vt.py").is_file())
        self.assertEqual(self.calls(), "\n")  # init 无参数也被真实执行
        self.assertEqual(self.marker(), self.git_out("rev-parse", "HEAD",
                                                     cwd=self.clone_dir))
        self.assertIn("vt.py doctor", result.stdout)

    def test_init_args_pass_through_with_and_without_separator(self):
        self.assert_ok(self.bootstrap("--", "--fix"))
        self.assertEqual(self.calls(), "--fix\n")
        self.assert_ok(self.bootstrap("--json"))
        self.assertEqual(self.calls(), "--fix\n--json\n")

    # ---- 场景二:干净更新 ----
    def test_clean_update_advances_clone_and_refreshes_marker(self):
        self.assert_ok(self.bootstrap())
        self.upstream_commit("v2", {"v2.txt": b"from upstream\n"})
        result = self.bootstrap()
        self.assert_ok(result)
        self.assertEqual((self.clone_dir / "v2.txt").read_bytes(),
                         b"from upstream\n")
        new_head = self.git_out("rev-parse", "HEAD", cwd=self.upstream)
        self.assertEqual(self.marker(), new_head)
        self.assertEqual(self.calls(), "\n\n")  # init 每次都重跑(幂等)

    # ---- 场景三:脏拒绝 ----
    def test_dirty_clone_rejects_update_leaving_worktree_untouched(self):
        self.assert_ok(self.bootstrap())
        self.upstream_commit("v2", {"v2.txt": b"from upstream\n"})
        (self.clone_dir / "notes.txt").write_bytes(b"user data\n")
        result = self.bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("未提交变更", self.output(result))
        self.assertFalse((self.clone_dir / "v2.txt").exists())
        self.assertEqual((self.clone_dir / "notes.txt").read_bytes(),
                         b"user data\n")

    # ---- 场景四:ignored→tracked 碰撞拒绝(红线) ----
    def test_ignored_tracked_collision_rejects_reset(self):
        self.assert_ok(self.bootstrap())
        (self.clone_dir / "config.local.json").write_bytes(b"user-local\n")
        # 红线前提:被忽略文件对 porcelain 判脏不可见(预检是唯一防线)。
        self.assertEqual(
            self.git_out("status", "--porcelain", "--untracked-files=all",
                         cwd=self.clone_dir), "")
        self.upstream_commit("v2", {"v2.txt": b"from upstream\n"})
        # tracked 化必然是被上游强制收编(上游自己的 .gitignore 也忽略它)。
        (self.upstream / "config.local.json").write_bytes(b"repo-tracked\n")
        self.git("add", "-f", "config.local.json")
        self.git("commit", "-qm", "v2-track-config")
        result = self.bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("冲突", self.output(result))
        # 本地被忽略文件未被 reset --hard 静默覆盖,其余更新也未落盘。
        self.assertEqual((self.clone_dir / "config.local.json").read_bytes(),
                         b"user-local\n")
        self.assertFalse((self.clone_dir / "v2.txt").exists())

    # ---- 更新保护的其余分支 ----
    def test_skip_update_reuses_dirty_clone_and_passes_args(self):
        self.assert_ok(self.bootstrap())
        self.upstream_commit("v2", {"v2.txt": b"from upstream\n"})
        (self.clone_dir / "notes.txt").write_bytes(b"user data\n")
        result = self.bootstrap("--skip-update", "--", "--json")
        self.assert_ok(result)
        self.assertFalse((self.clone_dir / "v2.txt").exists())
        self.assertEqual((self.clone_dir / "notes.txt").read_bytes(),
                         b"user data\n")
        self.assertEqual(self.calls(), "\n--json\n")

    def test_local_commit_marker_protection_rejects_reset(self):
        self.assert_ok(self.bootstrap())
        (self.clone_dir / "vt.py").write_bytes(b"# local edit\n")
        self.git("commit", "-am", "local", cwd=self.clone_dir)
        self.upstream_commit("v2", {"v2.txt": b"from upstream\n"})
        result = self.bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("本地提交", self.output(result))
        self.assertFalse((self.clone_dir / "v2.txt").exists())
        self.assertEqual((self.clone_dir / "vt.py").read_bytes(),
                         b"# local edit\n")

    def test_tag_named_origin_ref_cannot_hijack_reset(self):
        # 对抗前提:克隆内已存在名为 origin/main 的标签(如早期 fetch/clone
        # 带入),指向旧 commit;reset 目标必须是全限定 refs/remotes/origin/
        # main,不得被同名标签劫持回旧版本。
        self.assert_ok(self.bootstrap())
        stale = self.git_out("rev-parse", "HEAD", cwd=self.clone_dir)
        self.git("tag", "origin/main", stale, cwd=self.clone_dir)
        # 标签引用不影响工作区,克隆仍判干净、更新可进行
        self.assertEqual(
            self.git_out("status", "--porcelain", "--untracked-files=all",
                         cwd=self.clone_dir), "")
        self.upstream_commit("v2", {"v2.txt": b"from upstream\n"})
        result = self.bootstrap()
        self.assert_ok(result)
        new_head = self.git_out("rev-parse", "HEAD", cwd=self.upstream)
        self.assertNotEqual(new_head, stale)
        self.assertEqual((self.clone_dir / "v2.txt").read_bytes(),
                         b"from upstream\n")
        self.assertEqual(self.marker(), new_head)  # 落在远端分支而非旧标签

    def test_unapproved_development_branch_is_rejected(self):
        self.assert_ok(self.bootstrap())
        before = self.marker()
        result = self.bootstrap(FRAMIVRA_REF="feature")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.marker(), before)

    def test_annotated_release_tag_pins_exact_commit(self):
        self.git("tag", "-a", "v2.1.0", "-m", "stable")
        expected = self.git_out("rev-parse", "HEAD")
        self.upstream_commit("new-main", {"new.txt": b"not stable"})
        self.assert_ok(self.bootstrap(FRAMIVRA_REF="v2.1.0"))
        self.assertEqual(self.marker(), expected)
        self.assertFalse((self.clone_dir / "new.txt").exists())

    def test_default_stable_resolves_public_release_instead_of_main(self):
        self.git("tag", "-a", "v2.1.0", "-m", "stable")
        stable = self.git_out("rev-parse", "HEAD")
        self.upstream_commit("development-only", {"new.txt": b"main only"})
        shim = self.root / 'python-shim'; shim.mkdir()
        (shim / 'sitecustomize.py').write_text("import io,urllib.request\nurllib.request.urlopen=lambda *a,**k: io.BytesIO(b'{\"tag_name\":\"v2.1.0\",\"draft\":false,\"prerelease\":false}')\n")
        self.assert_ok(self.bootstrap(FRAMIVRA_REF='stable', PYTHONPATH=str(shim)))
        self.assertEqual(self.marker(), stable)
        self.assertFalse((self.clone_dir / 'new.txt').exists())

    def test_lightweight_release_tag_is_rejected(self):
        self.git("tag", "v2.1.0")
        result = self.bootstrap(FRAMIVRA_REF="v2.1.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.clone_dir.exists())

    def test_git_status_failure_fails_closed(self):
        # 判脏读数失败(坏索引,git status 非零而 rev-parse --show-toplevel
        # 仍可用)不得被命令替换吞掉当作「干净」放行更新(红队缺陷3)。
        self.assert_ok(self.bootstrap())
        marker_before = self.marker()
        self.upstream_commit("v2", {"v2.txt": b"from upstream\n"})
        (self.clone_dir / ".git" / "index").write_bytes(b"not-a-real-index")
        result = self.bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("git status", self.output(result))
        self.assertFalse((self.clone_dir / "v2.txt").exists())
        self.assertEqual(self.marker(), marker_before)

    def test_symlink_clone_dir_rejected(self):
        self.assert_ok(self.bootstrap())
        link = self.root / "link-to-clone"
        os.symlink(self.clone_dir, link)
        result = self.bootstrap(FRAMIVRA_DIR=str(link))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("符号链接", self.output(result))

    def test_git_file_linked_worktree_rejected(self):
        # git worktree 链接工作树的 .git 是文件:旧的 -e 检查会放行(红队
        # 发现),须 -d 判目录并给指引。
        self.assert_ok(self.bootstrap())
        wt = self.root / "linked-wt"
        self.git("worktree", "add", "-q", str(wt), "-b", "wt-branch",
                 cwd=self.clone_dir)
        self.assertTrue((wt / ".git").is_file())  # 链接工作树前提成立
        result = self.bootstrap(FRAMIVRA_DIR=str(wt))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("链接工作树", self.output(result))
        # 拒绝路径零副作用:链接工作树与主克隆均未被改动
        self.assertTrue((wt / "vt.py").is_file())
        self.assertTrue((self.clone_dir / ".git" / "framivra-bootstrap").is_file())

    def test_origin_mismatch_rejected(self):
        self.assert_ok(self.bootstrap())
        other = self.root / "other-origin.git"
        result = self.bootstrap(FRAMIVRA_REPO=str(other))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("origin", self.output(result))
        self.assertFalse(other.exists())

    # ---- fail-closed 守卫 ----
    def test_data_root_dir_rejected_in_all_spellings(self):
        base = str(self.home / ".local/share/video-tools")
        for value in (base, base + "/", base + "/.",
                      str(self.home / ".local/share/./video-tools"),
                      str(self.home / ".local/share/x/../video-tools")):
            with self.subTest(dir=value):
                result = self.bootstrap(FRAMIVRA_DIR=value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("video-tools", self.output(result))
                self.assertFalse(Path(base).exists())

    # ---- 数据根守卫不得依赖外部 realpath(macOS 无 GNU realpath -m) ----
    def restricted_path_without_realpath(self):
        """只含 bash/git/python3(/sh) 的 PATH:realpath 一律不可见。"""
        binroot = self.sandbox / "restricted-bin"
        binroot.mkdir()
        for name in ("bash", "git", "python3", "sh"):
            found = shutil.which(name)
            self.assertIsNotNone(found, "宿主缺 %s,测试环境不成立" % name)
            os.symlink(found, binroot / name)
        self.assertIsNone(shutil.which("realpath", path=str(binroot)),
                          "受限 PATH 不应能看到 realpath")
        return str(binroot)

    def test_data_root_variants_rejected_without_realpath_in_path(self):
        # 红队缺陷2:canon 若回退外部 realpath,PATH 无 realpath 时(等价
        # macOS)守卫退化为原串比对、/./ 与 /../ 变体放行。现须纯 bash 拒。
        restricted = self.restricted_path_without_realpath()
        base = str(self.home / ".local/share/video-tools")
        variants = (base + "//", base + "/./", base + "/sub/..",
                    base + "/./sub/..", base + "/.",
                    str(self.home / ".local/share/./video-tools"),
                    str(self.home / ".local/share/x/../video-tools"),
                    str(self.home / ".local/share/video-tools/../video-tools"))
        for value in variants:
            with self.subTest(dir=value):
                result = self.bootstrap(PATH=restricted, FRAMIVRA_DIR=value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("video-tools", self.output(result))
                self.assertFalse(Path(base).exists())

    def test_full_install_works_without_realpath_in_path(self):
        # 对照:受限 PATH(无 realpath)下全新安装与 init 全链路不受影响。
        restricted = self.restricted_path_without_realpath()
        result = self.bootstrap(PATH=restricted)
        self.assert_ok(result)
        self.assertEqual(self.calls(), "\n")
        self.assertEqual(self.marker(),
                         self.git_out("rev-parse", "HEAD", cwd=self.upstream))

    def test_invalid_variables_fail_closed(self):
        cases = [
            ("FRAMIVRA_DIR", "", "不得为空"),
            ("FRAMIVRA_DIR", "   ", "不得为空或全空白"),
            ("FRAMIVRA_DIR", "repo", "绝对路径"),
            ("FRAMIVRA_DIR", "-x/abs", "短横线"),
            ("FRAMIVRA_DIR", str(self.root / "tab\tdir"), "不得含控制字符"),
            ("FRAMIVRA_REPO", " ", "不得为空或全空白"),
            ("FRAMIVRA_REPO", "a b", "不得含空白"),
            ("FRAMIVRA_REF", "bad..name", "FRAMIVRA_REF 非法"),
        ]
        for name, value, expected in cases:
            with self.subTest(**{name: value}):
                result = self.bootstrap(**{name: value})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, self.output(result))

    def test_dir_with_spaces_is_accepted(self):
        spaced = self.root / "cl one"
        result = self.bootstrap(FRAMIVRA_DIR=str(spaced))
        self.assert_ok(result)
        self.assertTrue((spaced / "vt.py").is_file())

    def test_missing_python3_or_git_fails_with_hint(self):
        bare = self.sandbox / "bare-path"
        bare.mkdir()
        result = self.bootstrap(PATH=str(bare))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("python3", self.output(result))
        bin_python_only = self.sandbox / "python-only"
        bin_python_only.mkdir()
        os.symlink(subprocess.run(["sh", "-c", "command -v python3"],
                                  capture_output=True, text=True,
                                  check=True).stdout.strip(),
                   bin_python_only / "python3")
        result = self.bootstrap(PATH=str(bin_python_only))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("git", self.output(result))


class InstallShBash32Guard(unittest.TestCase):
    """bash 3.2 兼容静态哨兵:macOS 系统自带 bash 3.2 下必须可运行。

    逐个 bash4+ 特性做文本级排除(关联数组/大小写展开/mapfile/;;&/|&/&>>);
    动态验证以 bash -n + 真实端到端为准(见上类),本类防回归引入。
    """

    FORBIDDEN = (
        "declare -A", "mapfile", "readarray", ",,}", "^^}",
        ";;&", "|&", "&>>", "${var@", "coproc",
    )

    def test_no_bash4plus_constructs(self):
        text = INSTALL_SH.read_text(encoding="utf-8")
        for token in self.FORBIDDEN:
            self.assertNotIn(token, text, "bash4+ 特性禁用: %r" % token)


if __name__ == "__main__":
    unittest.main()
