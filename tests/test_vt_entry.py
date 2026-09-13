#!/usr/bin/env python3
"""tests/test_vt_entry.py —— vt.py 统一入口（v2 §5.1）子进程级冒烟 + init 薄转发等价。

覆盖：
  ① `vt.py version`：VERSION 内容 + git HEAD 短哈希；
  ② `vt.py doctor --json`：合法 JSON，版本段/组件段/skills 段齐备（VTX_DOCTOR_OFFLINE=1
     跳过 fetch，测试确定性）；
  ③ `vt.py plugins list`：registry 声明两插件 + 未安装态；
  ④ `vt.py --fix-links`：VT_MOUNT_ROOT 隔离（绝不触 live ~/.agents/skills/video/）——
     dry 只报告；真实修复产出**相对**符号链接指向本仓对应目录；skills 链接目标
     不存在时跳过并提示；
  ⑤ init 薄转发等价：`init.py --json` 输出键结构与 v1 对齐（七小节自检键齐全，
     32 键全集）——init 在 repo 根生成 config.local.json 的副作用在测试内复原。
运行：bash scripts/run_all_tests.sh。子进程一律 env python3（argv[0] 劫持纪律）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

V1_INIT_KEYS = [
    "python", "ffmpeg", "ffprobe", "chrome",                       # 1/5 基础
    "libfdk_aac", "nvenc",                                          # 2/5 编码
    "blender", "sox",                                               # 3/5 可选
    "vtx_cli", "new_project", "bridge_json2blend.py",
    "bridge_verify_blend.py", "bridge_blend2json.py",
    "bridge_subs_alpha.py", "audit_bundle",                         # 3b/5 程序自检
    "comfyui_env", "matanyone2", "birefnet_hr", "matting_resnet18",
    "comfyui_service", "resemble_weights",                          # 4/5 大模型
    "asr_model", "asr_aligner", "asr_python", "asr_sidecar",       # 4b/5 生成模块
    "tts_repo", "tts_python", "tts_voice_ref", "tts_edge",
    "imgen_key", "imgen_requests",
    "config_local",                                                 # 5/5 配置
]


def _vt(*args, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(["env", "python3", str(REPO / "vt.py"), *args],
                          capture_output=True, text=True, cwd=str(REPO), env=env)


class VersionCmd(unittest.TestCase):
    def test_version_prints_version_and_head(self):
        r = _vt("version")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        version = (REPO / "VERSION").read_text(encoding="utf-8").strip()
        self.assertIn(version, r.stdout)
        self.assertIn("HEAD", r.stdout)


class DoctorJson(unittest.TestCase):
    def test_doctor_json_sections_complete(self):
        r = _vt("doctor", "--json", env_extra={"VTX_DOCTOR_OFFLINE": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        d = json.loads(r.stdout)
        # 三段齐备（§5.1：版本/组件/skills）
        self.assertEqual(sorted(d), ["components", "skills", "version"])
        # 版本段字段
        v = d["version"]
        for k in ("version", "head", "state", "notes"):
            self.assertIn(k, v)
        # 组件段：注册表声明 + import vtx 自检（P2b 两插件落地 active）
        c = d["components"]
        self.assertIn("declared", c)
        self.assertIn("import_vtx", c)
        declared = {x["name"]: x for x in c["declared"]}
        self.assertIn("denoise", declared)
        self.assertIn("duck-static", declared)
        for name in ("denoise", "duck-static"):
            self.assertEqual(declared[name]["state"], "active")
            self.assertEqual(declared[name]["version"], "1.0.0")
        # 发现件（builtin）两插件也须过 health 门
        plugins = [e for e in c["entries"] if e["kind"] == "plugin"]
        self.assertEqual({e["name"] for e in plugins}, {"denoise", "duck-static"})
        for e in plugins:
            self.assertFalse(e["fail"], e)
        self.assertTrue(c["import_vtx"]["ok"], c["import_vtx"])
        self.assertTrue(str(REPO) in c["import_vtx"]["path"])
        # skills 段（P3 起 tech-talk 落地：发现 + uses_apps 逐件在册）
        self.assertIn("skills", d)
        self.assertFalse(d["skills"]["fail"])
        installed = {s["name"]: s for s in d["skills"]["installed"]}
        self.assertIn("tech-talk", installed)
        tt = installed["tech-talk"]
        self.assertTrue(tt["ok"], tt)
        self.assertEqual(tt["version"], "1.0.0")
        self.assertEqual(len(tt["uses_apps"]), 12)

    def test_doctor_human_readable_offline(self):
        r = _vt("doctor", env_extra={"VTX_DOCTOR_OFFLINE": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for marker in ("① 版本", "② 组件", "③ skills", "doctor 结论"):
            self.assertIn(marker, r.stdout)


class ListCheck(unittest.TestCase):
    def test_plugins_list_shows_declared(self):
        r = _vt("plugins", "list")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("denoise", r.stdout)
        self.assertIn("duck-static", r.stdout)
        # P2b 两插件落地：list 展示 enabled 态与 registry 声明（1.0.0）
        self.assertIn("✓ enabled denoise", r.stdout)
        self.assertIn("duck-static@1.0.0", r.stdout)
        self.assertNotIn("✗ FAIL", r.stdout)

    def test_apps_and_skills_list_ok(self):
        for args in (("apps", "list"), ("skills", "list"),
                     ("apps", "check"), ("plugins", "check"), ("skills", "check")):
            r = _vt(*args)
            self.assertEqual(r.returncode, 0, f"{args}: {r.stdout}{r.stderr}")

    def test_bad_subcommand_usage_error(self):
        r = _vt("plugins", "bogus")
        self.assertEqual(r.returncode, 2)


class FixLinks(unittest.TestCase):
    """挂载链接修复（VT_MOUNT_ROOT 隔离——绝不触 live 挂载）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.mount = Path(self._tmp.name) / "video"

    def tearDown(self):
        self._tmp.cleanup()

    def test_dry_reports_missing_then_fix_creates_relative_links(self):
        env = {"VT_MOUNT_ROOT": str(self.mount)}
        r = _vt("--fix-links", "--dry", env_extra=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("缺失", r.stdout)
        self.assertFalse(self.mount.exists())          # dry 不落任何东西
        r2 = _vt("--fix-links", env_extra=env)
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        for name, rel in (("SKILL.md", "skill/SKILL.md"), ("references", "skill/references"),
                          ("assets", "skill/assets"), ("vtx", "vtx"), ("scripts", "scripts"),
                          ("skills", "skills")):
            link = self.mount / name
            self.assertTrue(link.is_symlink(), f"{name} 未建链")
            target = Path(os.readlink(link))
            self.assertFalse(target.is_absolute(), f"{name} 应为相对链接")
            self.assertEqual((link.parent / target).resolve(), (REPO / rel).resolve())
        # P3 起 skills/tech-talk 落地：第 6 条 skills 链接照常建立（过渡期断言已随包成立退役）
        self.assertTrue((self.mount / "skills" / "tech-talk" / "SKILL.md").is_file())
        # 幂等：再跑一遍全 ✓ 零变更
        r3 = _vt("--fix-links", env_extra=env)
        self.assertEqual(r3.returncode, 0)
        self.assertIn("0 处变更", r3.stdout)

    def test_wrong_target_repaired(self):
        env = {"VT_MOUNT_ROOT": str(self.mount)}
        _vt("--fix-links", env_extra=env)               # 先建正确链接
        wrong = Path("/tmp")
        link = self.mount / "vtx"
        link.unlink()
        link.symlink_to(wrong)                          # 指向别处 → 修复回本仓
        r = _vt("--fix-links", env_extra=env)
        self.assertEqual((link.parent / Path(os.readlink(link))).resolve(),
                         (REPO / "vtx").resolve())


class InitForwarderParity(unittest.TestCase):
    def test_init_json_key_structure_matches_v1(self):
        cfg_file = REPO / "config.local.json"
        existed = cfg_file.exists()
        content = cfg_file.read_text(encoding="utf-8") if existed else None
        try:
            r = subprocess.run(["env", "python3", str(REPO / "init.py"), "--json"],
                               capture_output=True, text=True, cwd=str(REPO))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            d = json.loads(r.stdout)
            missing = [k for k in V1_INIT_KEYS if k not in d]
            self.assertEqual(missing, [], f"init --json 缺 v1 结构键：{missing}")
            # 每项都是 {ok, detail} 形（v1 结构）
            for k, v in d.items():
                self.assertEqual(sorted(v), ["detail", "ok"], f"{k} 结构不符")
        finally:
            if existed:
                cfg_file.write_text(content, encoding="utf-8")
            elif cfg_file.exists():
                cfg_file.unlink()                       # 复原测试副作用（生成件）

    def test_init_via_vt_equivalent(self):
        cfg_file = REPO / "config.local.json"
        existed = cfg_file.exists()
        try:
            r1 = _vt("init", "--json")
            r2 = subprocess.run(["env", "python3", str(REPO / "init.py"), "--json"],
                                capture_output=True, text=True, cwd=str(REPO))
            self.assertEqual(r1.returncode, r2.returncode)
            self.assertEqual(json.loads(r1.stdout), json.loads(r2.stdout))  # 转发等价
        finally:
            if not existed and cfg_file.exists():
                cfg_file.unlink()


class VtxRegression(unittest.TestCase):
    def test_vtx_help_still_works(self):
        r = subprocess.run(["env", "python3", str(REPO / "vtx" / "vtx.py"), "-h"],
                           capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(r.returncode, 0)
        self.assertIn("vtx", r.stdout)


if __name__ == "__main__":
    unittest.main()
