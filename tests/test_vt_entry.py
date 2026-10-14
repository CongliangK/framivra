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
  ⑥ doctor --fix（P4）：挂载链接联动重建（VT_MOUNT_ROOT 隔离）+ 无可修复项时
     明确说明 + live 挂载属其他仓只报人工项（安全门）+ registry.json 缺字段
     回填幂等；--json 输出含 fix 段。
  ⑦ skills 段对未注册包（examples/skill-demo，不在 skills/ 也不在 external）
     不扫描不误报。
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
from unittest import mock

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


def _isolated_env(root):
    """Real CPU tools only; no developer HOME, configuration or optional CLIs."""
    root = Path(root)
    home = root / "home"; home.mkdir()
    tools = root / "bin"; tools.mkdir()
    for name in ("env", "bash", "git", "ffmpeg", "ffprobe", "node", "google-chrome"):
        binary = shutil.which(name)
        if not binary:
            raise AssertionError(f"CLI fixture requires a real executable: {name}")
        (tools / name).symlink_to(binary)
    # Preserve the selected venv interpreter, not its resolved base executable.
    (tools / "python3").symlink_to(sys.executable)
    config = root / "config.json"
    config.write_text(json.dumps({"bgm": {"enabled": True,
                                        "cli": str(root / "missing-bgm")}}))
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("VTX_", "VT_", "SC_", "NANOBANANA", "IMGEN_", "ASR_", "TTS_"))
           and k not in ("PYTHONPATH", "PYTHONHOME", "BGM_CLI", "BGM_REPO",
                         "DENOISE_PY", "RESEMBLE_WEIGHTS")}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
               PATH=str(tools), VTX_CONFIG=str(config), VTX_DOCTOR_OFFLINE="1",
               PYTHONNOUSERSITE="1", VT_MOUNT_ROOT=str(home / "video"))
    return env


def _vt(*args, env_extra=None):
    with tempfile.TemporaryDirectory(prefix="vt-entry-env-") as root:
        env = _isolated_env(root)
        env.update(env_extra or {})
        return subprocess.run(["env", "python3", str(REPO / "vt.py"), *args],
                              capture_output=True, text=True, cwd=str(REPO), env=env)


def _assert_doctor_passes_with_optional_unavailable(case, result):
    """核心依赖齐备 → doctor exit 0；builtin 可选面缺件降 ℹ unavailable 不 FAIL
    （Windows 适配 PR-A1：blender 缺失、bgm/denoise health 未就绪均属未装可选
    能力；渲染核心硬门在依赖面 error 级）。"""
    case.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    data = json.loads(result.stdout)
    case.assertEqual(data["dependencies"]["errors"], [], data["dependencies"])
    entries = data["components"]["entries"]
    for e in entries:
        case.assertTrue(e["manifest_ok"], e)
        case.assertFalse(e["fail"], e)              # builtin 件一律不再 FAIL
    by_key = {(e["kind"], e["name"]): e for e in entries}
    # blender 缺失 → requires ✗ + unavailable（app 与 plugin 两面）
    for key in (("app", "blender-bridge"), ("plugin", "blender-bridge")):
        e = by_key[key]
        case.assertFalse(e["requires"]["ok"], e)
        case.assertIn("缺 blender", e.get("unavailable", []), e)
    # health 未就绪（可选 CLI/权重不在）→ unavailable，不 FAIL
    for key in (("plugin", "bgm-render"), ("plugin", "denoise")):
        e = by_key[key]
        case.assertFalse(e["health"]["ok"], e)
        case.assertIn("health 未就绪", e.get("unavailable", []), e)
    # 其余件（含 google-chrome/node/numpy 依赖面）全就绪
    for e in entries:
        if not e.get("unavailable"):
            case.assertTrue(e["requires"]["ok"], e)
            case.assertIsNot(e["health"]["ok"], False, e)
    case.assertFalse(data["components"]["fail"])
    case.assertFalse(data["skills"]["fail"])
    case.assertTrue(data["components"]["import_vtx"]["ok"])
    # 依赖面：blender/sox 缺只 warn（本隔离环境无 blender/sox）
    warns = " ".join(data["dependencies"]["warns"])
    case.assertIn("blender 缺失", warns)
    return data


class DoctorComponentBranches(unittest.TestCase):
    """Named fixtures exercise real health/FAIL gates, not global health mocks."""
    def check_branch(self, enabled, health_exit):
        import argparse
        import contextlib
        import io
        import vt as vt_mod
        with tempfile.TemporaryDirectory(prefix="vt-component-") as root:
            root = Path(root)
            (root / "health.py").write_text(f"raise SystemExit({health_exit})\n")
            manifest = {"health": {"type": "command", "command": "health.py"},
                        "requires": {"binaries": [], "python_packages": [], "platforms": []}}
            entry = {"name": "controlled-health", "kind": "plugin", "source": "external",
                     "dir": root, "enabled": enabled, "manifest_errors": [], "manifest": manifest}
            output = io.StringIO()
            with mock.patch.object(vt_mod._preg, "discover", return_value=[entry]), \
                 mock.patch.object(vt_mod._preg, "declared_plugins", return_value={}), \
                 mock.patch.object(vt_mod, "_doctor_version", return_value={"state": "clean"}), \
                 mock.patch.object(vt_mod, "_doctor_skills", return_value={"fail": False}), \
                 mock.patch.object(vt_mod, "_probe_health", wraps=vt_mod._probe_health) as health, \
                 mock.patch.object(vt_mod, "_probe_requires", wraps=vt_mod._probe_requires) as requires, \
                 contextlib.redirect_stdout(output):
                exit_code = vt_mod.cmd_doctor(argparse.Namespace(json=True, fix=False))
                if enabled:
                    health.assert_called_once()
                    requires.assert_called_once()
                else:
                    health.assert_not_called()
                    requires.assert_not_called()
            data = json.loads(output.getvalue())
            self.assertEqual(exit_code, 1 if enabled and health_exit else 0)
            self.assertEqual(data["components"]["fail"], bool(enabled and health_exit))
            self.assertEqual(data["components"]["entries"][0]["fail"], bool(enabled and health_exit))

    def test_healthy_enabled_exits_zero(self):
        self.check_branch(True, 0)

    def test_unavailable_enabled_exits_one(self):
        self.check_branch(True, 1)

    def test_disabled_is_not_probed_and_exits_zero(self):
        self.check_branch(False, 1)


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
        d = _assert_doctor_passes_with_optional_unavailable(self, r)
        # 四段齐备（§5.1 + PR-A1：版本/依赖面/组件/skills）
        self.assertEqual(sorted(d), ["components", "dependencies", "skills", "version"])
        self.assertIn("errors", d["dependencies"])
        self.assertIn("warns", d["dependencies"])
        self.assertIn("items", d["dependencies"])
        # 版本段字段
        v = d["version"]
        for k in ("version", "head", "state", "notes"):
            self.assertIn(k, v)
        # 组件段：注册表声明 + import vtx 自检（P2b 两插件 + P6 v1.2 两插件落地 active）
        c = d["components"]
        self.assertIn("declared", c)
        self.assertIn("import_vtx", c)
        declared = {x["name"]: x for x in c["declared"]}
        self.assertIn("denoise", declared)
        self.assertIn("duck-static", declared)
        # 版本三面一致（R1-F F6：doctor 组件段读 registry——只拿 doctor 输出比
        # registry.json 是同义反复永真；真比对锚在 plugin.json manifest：
        # manifest ↔ registry ↔ doctor 输出同值，任一面 drift 在此被抓）
        registry = json.loads((REPO / "plugins" / "registry.json")
                              .read_text(encoding="utf-8"))["plugins"]
        for name in ("denoise", "duck-static", "blender-bridge", "bgm-render"):
            manifest_v = json.loads((REPO / "plugins" / name / "plugin.json")
                                    .read_text(encoding="utf-8"))["version"]
            self.assertEqual(declared[name]["state"], "active")
            self.assertEqual(declared[name]["version"], manifest_v,
                             f"{name}: doctor 输出版本须等于 plugin.json")
            self.assertEqual(registry[name]["version"], manifest_v,
                             f"{name}: registry 与 manifest 双面一致")
        # 发现件（builtin）四插件也须过 health 门
        plugins = [e for e in c["entries"] if e["kind"] == "plugin"]
        self.assertEqual({e["name"] for e in plugins},
                         {"denoise", "duck-static", "blender-bridge", "bgm-render"})
        # builtin 可选面降 ℹ：三件 unavailable 但不 FAIL（PR-A1 分级）
        for e in plugins:
            if e["name"] == "duck-static":
                self.assertFalse(e["fail"], e)
                self.assertNotIn("unavailable", e)
            else:
                self.assertFalse(e["fail"], e)
                self.assertTrue(e.get("unavailable"), e)
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
        _assert_doctor_passes_with_optional_unavailable(self, _vt("doctor", "--json"))
        for marker in ("① 版本", "② 依赖面", "③ 组件", "④ skills",
                       "doctor 结论", "✅ 通过"):
            self.assertIn(marker, r.stdout)
        self.assertIn("unavailable（缺 blender", r.stdout)


class ListCheck(unittest.TestCase):
    def test_plugins_list_shows_declared(self):
        r = _vt("plugins", "list")
        # builtin 可选面降 ℹ（PR-A1）：核心齐备 → list/check 不再 FAIL
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("denoise", r.stdout)
        self.assertIn("duck-static", r.stdout)
        self.assertIn("✓ enabled duck-static", r.stdout)
        self.assertIn("duck-static@1.0.0", r.stdout)
        self.assertNotIn("✗ FAIL", r.stdout)
        self.assertEqual([line for line in r.stdout.splitlines() if "✗" in line], [])

    def test_apps_and_skills_list_ok(self):
        for args in (("apps", "list"), ("skills", "list"),
                     ("apps", "check"), ("plugins", "check"), ("skills", "check")):
            r = _vt(*args)
            self.assertEqual(r.returncode, 0, f"{args}: {r.stdout}{r.stderr}")
            if args[0] != "skills":
                _assert_doctor_passes_with_optional_unavailable(self, _vt("doctor", "--json"))

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
    def setUp(self):
        root = tempfile.TemporaryDirectory(prefix="vt-init-env-")
        self.addCleanup(root.cleanup)
        self.env = _isolated_env(root.name)

    def test_init_json_key_structure_matches_v1(self):
        cfg_file = REPO / "config.local.json"
        existed = cfg_file.exists()
        content = cfg_file.read_text(encoding="utf-8") if existed else None
        try:
            r = subprocess.run(["env", "python3", str(REPO / "init.py"), "--json"],
                               capture_output=True, text=True, cwd=str(REPO), env=self.env)
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
            r1 = _vt("init", "--json", env_extra=self.env)
            r2 = subprocess.run(["env", "python3", str(REPO / "init.py"), "--json"],
                                capture_output=True, text=True, cwd=str(REPO), env=self.env)
            self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
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


class DoctorFix(unittest.TestCase):
    """doctor --fix（P4，§5.1 修复通道）：挂载链接联动 + registry 缺字段回填 +
    live 挂载安全门（指向其他仓只报人工项不自动改）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.mount = Path(self._tmp.name) / "video"
        self.addCleanup(self._tmp.cleanup)

    def test_fix_rebuilds_missing_mount_links_then_reports_none(self):
        # 隔离挂载根（绝不触 live ~/.agents/skills/video/）：缺失 → 自动重建
        env = {"VT_MOUNT_ROOT": str(self.mount), "VTX_DOCTOR_OFFLINE": "1"}
        r = _vt("doctor", "--fix", env_extra=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        _assert_doctor_passes_with_optional_unavailable(self, _vt("doctor", "--fix", "--json", env_extra=env))
        for name in ("SKILL.md", "references", "assets", "vtx", "scripts", "skills"):
            link = self.mount / name
            self.assertTrue(link.is_symlink(), f"{name} 未建链")
            self.assertFalse(Path(os.readlink(link)).is_absolute())
        # 幂等：再跑 → 全健康 + 「无可自动修复项」明确说明
        r2 = _vt("doctor", "--fix", env_extra=env)
        self.assertEqual(r2.returncode, 0)
        self.assertIn("挂载链接全部健康", r2.stdout)
        self.assertIn("registry.json 无缺字段", r2.stdout)
        self.assertIn("无可自动修复项", r2.stdout)

    def test_doctor_fix_json_has_fix_section(self):
        env = {"VT_MOUNT_ROOT": str(self.mount), "VTX_DOCTOR_OFFLINE": "1"}
        r = _vt("doctor", "--fix", "--json", env_extra=env)
        d = _assert_doctor_passes_with_optional_unavailable(self, r)
        self.assertIn("fix", d)
        self.assertEqual(d["fix"]["mount"]["status"], "fixed")
        self.assertEqual(d["fix"]["registry"]["status"], "ok")

    def test_foreign_live_mount_not_auto_fixed(self):
        # 默认 live 挂载根存在指向**其他仓**的链接 → 安全门拒绝自动修复（本机
        # live voice-ime/主工作区铁律）；无 VT_MOUNT_ROOT 覆盖时才走该门
        import vt as vt_mod
        self.mount.mkdir(parents=True, exist_ok=True)
        (self.mount / "vtx").symlink_to(Path("/tmp"))     # 指向本仓之外的存在的路径
        with mock.patch.object(vt_mod, "mount_root", return_value=self.mount), \
                mock.patch.dict(os.environ, {"VT_MOUNT_ROOT": ""}):
            items = vt_mod._mount_link_state()
            safe, reason = vt_mod._mount_fix_safe(items)
            self.assertFalse(safe, reason)
            self.assertIn("本仓之外", reason)
            report = vt_mod._doctor_fix(json_out=True)
            self.assertEqual(report["mount"]["status"], "manual")
            # 指针未被改写
            self.assertEqual(os.readlink(self.mount / "vtx"), "/tmp")

    def test_registry_fix_backfills_missing_fields(self):
        # registry 条目缺 version/keys/hooks/installed（manifest 为声明数据源）→
        # 计划+回填+幂等；_comment 等无关字段保留
        import vt as vt_mod
        plug = Path(self._tmp.name) / "plugins" / "demo"
        plug.mkdir(parents=True)
        (plug / "health_check.py").write_text("print('ok')\n", encoding="utf-8")
        (plug / "README.md").write_text("# demo\n", encoding="utf-8")
        (plug / "plugin.json").write_text(json.dumps({
            "name": "demo", "version": "0.2.0", "kind": "plugin",
            "entry": {"cli": []},
            "health": {"type": "command", "command": "health_check.py"},
            "requires": {}, "config_keys": [], "docs": "README.md",
            "extends": {"schema_keys": [{"path": "sources[]", "key": "demo"}],
                        "hooks": ["prepare_source"]}}), encoding="utf-8")
        regf = Path(self._tmp.name) / "plugins" / "registry.json"
        regf.write_text(json.dumps({"_comment": "keep",
                                    "plugins": {"demo": {"docs": "..."}}}), encoding="utf-8")
        fixes, manual = vt_mod._registry_fix_plan(Path(self._tmp.name))
        fields = {f for _n, f, _v in fixes}
        self.assertEqual(fields, {"version", "keys", "hooks", "installed"})
        vt_mod._apply_registry_fixes(fixes, Path(self._tmp.name))
        data = json.loads(regf.read_text(encoding="utf-8"))
        self.assertEqual(data["plugins"]["demo"]["version"], "0.2.0")
        self.assertTrue(data["plugins"]["demo"]["installed"])
        self.assertIn("_comment", data)                    # 无关字段保留
        fixes2, _manual2 = vt_mod._registry_fix_plan(Path(self._tmp.name))
        self.assertEqual(fixes2, [])                       # 幂等


class DoctorSkillsSection(unittest.TestCase):
    def test_unregistered_skill_packages_not_misreported(self):
        # examples/skill-demo 这类未注册包（不在 skills/ 也不在 external）不进
        # doctor skills 段扫描面——不误报（§5.1；P4 起 examples/ 有 skill.json）
        self.assertTrue((REPO / "examples" / "skill-demo" / "skill.json").is_file())
        r = _vt("doctor", "--json", env_extra={"VTX_DOCTOR_OFFLINE": "1"})
        d = _assert_doctor_passes_with_optional_unavailable(self, r)
        names = [s["name"] for s in d["skills"]["installed"]]
        self.assertEqual(names, ["tech-talk"])
        self.assertFalse(d["skills"]["fail"])


if __name__ == "__main__":
    unittest.main()
