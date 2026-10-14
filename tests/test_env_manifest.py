"""tests/test_env_manifest.py — vtx/env_manifest 单测（issue #102）。

全密封离线：子进程经 em._run_cmd seam 注入桩、文件读经 _read_text/_read_bytes
seam、进程表经 _list_x_servers seam、环境变量经 patch.dict——不起真
nvidia-smi/fc-list/git/xdpyinfo/chrome。**无任何 skipTest/skipUnless/skipIf
门禁**（规避 scripts/ci_checks.py check_media_inventory 静态扫描爆破面）。

DoD 对抗用例：test_verify_tamper_scenarios_100pct_detected —— 逐项篡改字体
SHA256 / GPU 静默回退 swiftshader 等全部门禁字段，verify_manifest_compatibility
必须 100% 检出（非空不兼容项 + 关键词命中）。另覆盖 gfx_golden compare 的
环境前置门禁接入（build --env-manifest 嵌入 / compare 门禁拒绝/警告）。
"""
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from vtx import env_manifest as em   # noqa: E402
import gfx_golden as gg              # noqa: E402
from PIL import Image                # noqa: E402

W, H = 32, 24   # golden 门禁测试用假帧尺寸（小图提速）

CP0 = subprocess.CompletedProcess([], 0)

NOTO_SHA = hashlib.sha256(b"noto-vf-content").hexdigest()
INTER_R_SHA = hashlib.sha256(b"inter-regular").hexdigest()
INTER_B_SHA = hashlib.sha256(b"inter-bold").hexdigest()

GIT_CLEAN_AT_BASELINE = {
    "git rev-parse HEAD": em.BASELINE_SHA + "\n",
    "git status --porcelain": "",
    f"git merge-base --is-ancestor {em.BASELINE_SHA} HEAD": CP0,
}
NVIDIA_OK = {"nvidia-smi":
             "NVIDIA GeForce RTX 4080, 615.71.09, 16376 MiB, 4451 MiB\n"}
FC_LINES = ("/usr/fake-fonts/NotoSansCJK-VF.ttc: Noto Sans CJK SC\n"
            "/usr/fake-fonts/Inter-Regular.otf: Inter,Inter\n"
            "/usr/fake-fonts/Inter-Bold.otf: Inter\n")
FC_MATCH_FILE = "/usr/fake-fonts/NotoSansCJK-VF.ttc\n"          # --format %{file} 输出
FC_MATCH_DEFAULT = ('/usr/fake-fonts/NotoSansCJK-VF.ttc: '
                    '"Noto Sans CJK SC" "Regular"\n')            # 默认输出（回退解析路）
XDPY_OK = {"xdpyinfo":
           "name of display:    :0\nversion number:    11.0\n"
           "vendor string:    The X.Org Foundation\n"
           "vendor release number:    12401000\n"}
PROC_TEXTS = {
    "/proc/cpuinfo": "processor\t: 0\nmodel name\t: AMD Ryzen 9 7950X 16-Core Processor\n",
    "/proc/meminfo": "MemTotal:  16384000 kB\nSwapTotal:  0 kB\n",
}
FONT_BYTES = {
    "/usr/fake-fonts/NotoSansCJK-VF.ttc": b"noto-vf-content",
    "/usr/fake-fonts/Inter-Regular.otf": b"inter-regular",
    "/usr/fake-fonts/Inter-Bold.otf": b"inter-bold",
}
ENV_X11 = {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"}

DEFAULT_RT = {
    "effective_backend": "nvidia_egl",
    "source": "cdp",
    "evidence": {
        "browser_version": {"product": "Chrome/152.0.6834.83",
                            "userAgent": "Mozilla/5.0 ... Chrome/152.0.6834.83"},
        "system_info": {"gpu": {"devices": [{"vendorId": 4318}],
                                 "featureStatus": {"webgl": "enabled"}}},
    },
    "viewport": {"width": 1920, "height": 1080},
    "color_space": "srgb",
}


def rt_info(**over):
    info = json.loads(json.dumps(DEFAULT_RT))   # 深拷贝
    info.update(over)
    return info


@contextlib.contextmanager
def patched_seams(*, cmds=None, texts=None, bytes_=None, servers=None,
                  environ=None):
    """四采集 seam 全量桩化（deterministic；未登记的命令=不可得→None）。"""
    cmds, texts, bytes_ = cmds or {}, texts or {}, bytes_ or {}

    def run_cmd(argv, timeout):
        argv = list(argv)
        if argv[0] == "git":
            key = "git " + " ".join(argv[3:])
        elif argv[0] == "fc-match" and "--format" in argv:
            key = "fc-match format"
        else:
            key = argv[0]
        canned = cmds.get(key)
        if canned is None:
            return None
        if isinstance(canned, str):
            return subprocess.CompletedProcess(argv, 0, stdout=canned, stderr="")
        return canned

    patchers = [
        mock.patch.object(em, "_run_cmd", run_cmd),
        mock.patch.object(em, "_read_text", lambda p: texts.get(p)),
        mock.patch.object(em, "_read_bytes", lambda p: bytes_.get(p)),
        mock.patch.object(em, "_list_x_servers", lambda: list(servers or [])),
    ]
    if environ is not None:
        patchers.append(mock.patch.dict(os.environ, environ, clear=True))
    for p in patchers:
        p.start()
    try:
        yield
    finally:
        for p in patchers:
            p.stop()


def stub_cmds(**extra):
    """标准全绿命令桩（git 基线 clean + nvidia-smi + fc-list/fc-match + xdpyinfo）。"""
    cmds = dict(GIT_CLEAN_AT_BASELINE)
    cmds.update(NVIDIA_OK)
    cmds["fc-list"] = FC_LINES
    cmds["fc-match format"] = FC_MATCH_FILE
    cmds.update(XDPY_OK)
    cmds.update(extra)
    return cmds


def stub_collect(backend_id="cdp-baseline", rt=None, **seam_kw):
    """桩化环境下的 collect（确定性：同桩同输出，仅 run_id/时间戳随机）。"""
    seams = {"cmds": stub_cmds(), "texts": dict(PROC_TEXTS),
             "bytes_": dict(FONT_BYTES), "servers": [], "environ": dict(ENV_X11)}
    seams.update(seam_kw)
    with patched_seams(**seams):
        return em.collect_env_manifest(backend_id, rt if rt is not None else rt_info())


def _tamper_font_sha(m, family, path):
    """模拟篡改某字体文件 SHA256（DoD 对抗场景）。"""
    m["fonts_fingerprint"]["required"][family]["files"][path] = "0" * 64


def _tamper_sans_sha(m):
    fp = m["fonts_fingerprint"]["sans-serif"]["files"]
    fp[next(iter(fp))] = "0" * 64


def _drop_inter(m):
    m["fonts_fingerprint"]["required"]["Inter"] = {"status": "missing", "files": {}}


class TestCollectManifestStructure(unittest.TestCase):
    def test_full_manifest_structure_and_fields(self):
        m = stub_collect()
        self.assertEqual(m["schema"], "vtx.env_manifest/1")
        self.assertEqual(m["backend_id"], "cdp-baseline")
        for key in ("run_id", "collected_at_utc", "git_sha", "git_clean", "uncommitted",
                    "baseline_sha", "baseline_deviation", "os_kernel", "cpu_model",
                    "ram_total_mib", "display_server", "gpu_driver_and_vram",
                    "fonts_fingerprint", "runtime_backend_truth"):
            self.assertIn(key, m, f"manifest 缺顶层键 {key}")
        # git（顶层平铺，工单字段口径）：基线 clean（at_baseline / 无未提交）
        self.assertEqual(m["git_sha"], em.BASELINE_SHA)
        self.assertTrue(m["git_clean"])
        self.assertEqual(m["baseline_deviation"], "at_baseline")
        self.assertEqual(m["uncommitted"], [])
        self.assertEqual(m["baseline_sha"], em.BASELINE_SHA)
        # os/cpu/ram（/proc 桩）
        self.assertEqual(m["cpu_model"], "AMD Ryzen 9 7950X 16-Core Processor")
        self.assertEqual(m["ram_total_mib"], 16000)   # 16384000 KiB → 16000 MiB
        self.assertIn(m["os_kernel"]["system"], ("Linux", "Darwin", "Windows"))
        # display（x11 会话，无 Xvfb）
        self.assertEqual(m["display_server"]["session_type"], "x11")
        self.assertFalse(m["display_server"]["xvfb"]["under_xvfb"])
        self.assertTrue(m["display_server"]["xdpyinfo"]["available"])
        self.assertEqual(m["display_server"]["xdpyinfo"]["name of display"], ":0")
        # gpu（nvidia-smi 桩解析）
        self.assertEqual(m["gpu_driver_and_vram"], {
            "gpus": [{"name": "NVIDIA GeForce RTX 4080", "driver_version": "615.71.09",
                      "memory_total_mib": 16376, "memory_used_mib": 4451}]})
        # fonts（哈希=文件内容 sha256；JetBrains Mono 桩环境缺失→missing）
        fp = m["fonts_fingerprint"]
        self.assertEqual(fp["status"], "ok")
        self.assertEqual(fp["required"]["Noto Sans CJK SC"]["status"], "ok")
        self.assertEqual(
            fp["required"]["Noto Sans CJK SC"]["files"]["/usr/fake-fonts/NotoSansCJK-VF.ttc"],
            NOTO_SHA)
        self.assertEqual(fp["required"]["Inter"]["status"], "ok")
        self.assertEqual(fp["required"]["JetBrains Mono"]["status"], "missing")
        self.assertEqual(fp["sans-serif"]["status"], "ok")
        self.assertEqual(fp["sans-serif"]["files"], {"/usr/fake-fonts/NotoSansCJK-VF.ttc": NOTO_SHA})
        # runtime truth（CDP 证据推导）
        t = m["runtime_backend_truth"]
        self.assertEqual(t["effective_backend"], "nvidia_egl")
        self.assertEqual(t["source"], "cdp")
        self.assertEqual(t["chromium_major"], 152)
        self.assertEqual(t["viewport"], "1920x1080")
        self.assertEqual(t["color_space"], "srgb")

    def test_run_id_format_and_uniqueness(self):
        a, b = stub_collect()["run_id"], stub_collect()["run_id"]
        import re as _re
        for rid in (a, b):
            self.assertRegex(rid, r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")
        self.assertNotEqual(a, b)

    def test_empty_backend_id_rejected(self):
        with self.assertRaises(ValueError):
            stub_collect(backend_id="")


class TestGitState(unittest.TestCase):
    def _git(self, cmds):
        with patched_seams(cmds=cmds):
            return em._collect_git_state(Path("/fake/repo"))

    def test_dirty_descendant_baseline(self):
        cmds = {
            "git rev-parse HEAD": "abc123def0000000000000000000000000000000\n",
            "git status --porcelain": "?? vtx/env_manifest.py\n M vtx/gfx_render.py\n",
            f"git merge-base --is-ancestor {em.BASELINE_SHA} HEAD": CP0,
        }
        g = self._git(cmds)
        self.assertEqual(g["git_sha"], "abc123def0000000000000000000000000000000")
        self.assertFalse(g["git_clean"])
        self.assertEqual(len(g["uncommitted"]), 2)
        self.assertEqual(g["baseline_deviation"], "descendant")

    def test_diverged_from_baseline(self):
        cmds = {
            "git rev-parse HEAD": "abc123def0000000000000000000000000000000\n",
            "git status --porcelain": "",
            f"git merge-base --is-ancestor {em.BASELINE_SHA} HEAD":
                subprocess.CompletedProcess([], 1),
        }
        g = self._git(cmds)
        self.assertEqual(g["baseline_deviation"], "diverged_or_unknown")

    def test_git_unavailable_fail_open_explicit(self):
        g = self._git({})   # git 全不可得
        self.assertIsNone(g["git_sha"])
        self.assertIsNone(g["git_clean"])
        self.assertEqual(g["baseline_deviation"], "unknown")
        self.assertTrue(g["git_error"])   # 失效面显形，非静默


class TestOsHardware(unittest.TestCase):
    def test_cpu_ram_fallback_when_proc_missing(self):
        with patched_seams(texts={}, environ=dict(ENV_X11)):
            m = em.collect_env_manifest("x", rt_info())
        self.assertTrue(isinstance(m["cpu_model"], str) and m["cpu_model"])
        # sysconf 回退在本机必得正值；不可得平台亦只是 None（不崩）
        self.assertTrue(m["ram_total_mib"] is None or m["ram_total_mib"] > 0)


class TestDisplayServer(unittest.TestCase):
    def _display(self, **seam):
        seams = {"servers": [], "environ": dict(ENV_X11)}
        seams.update(seam)
        with patched_seams(**seams):
            return em._collect_display_server()

    def test_headless_none_session(self):
        d = self._display(environ={})
        self.assertEqual(d["session_type"], "none")
        self.assertIsNone(d["xdpyinfo"])
        self.assertIsNone(d["xvfb"]["under_xvfb"])   # 无 DISPLAY 无法归属

    def test_wayland_wins_over_display(self):
        d = self._display(environ={"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0",
                                   "XDG_SESSION_TYPE": "wayland"})
        self.assertEqual(d["session_type"], "wayland")

    def test_xvfb_matched_display_detected(self):
        srv = {"pid": 4242, "comm": "Xvfb",
               "cmdline": ["/usr/bin/Xvfb", ":99", "-screen", "0", "1920x1080x24"]}
        d = self._display(environ={"DISPLAY": ":99"}, servers=[srv])
        self.assertTrue(d["xvfb"]["under_xvfb"])
        self.assertIn("4242", d["xvfb"]["evidence"])
        self.assertEqual(d["xvfb"]["servers_running"], 1)

    def test_xvfb_screen_suffix_normalized(self):
        """客户端 DISPLAY=:99.0（带 screen 号）也必须归属到 Xvfb :99。"""
        srv = {"pid": 4242, "comm": "Xvfb", "cmdline": ["Xvfb", ":99"]}
        d = self._display(environ={"DISPLAY": ":99.0"}, servers=[srv])
        self.assertTrue(d["xvfb"]["under_xvfb"])

    def test_xvfb_other_display_not_attributed(self):
        srv = {"pid": 4242, "comm": "Xvfb", "cmdline": ["Xvfb", ":99"]}
        d = self._display(environ={"DISPLAY": ":0"}, servers=[srv])
        self.assertFalse(d["xvfb"]["under_xvfb"])
        self.assertIn("均不持有", d["xvfb"]["evidence"])

    def test_xwayland_server_not_xvfb(self):
        srv = {"pid": 7, "comm": "Xwayland", "cmdline": ["Xwayland", ":0"]}
        d = self._display(environ={"DISPLAY": ":0"}, servers=[srv])
        self.assertFalse(d["xvfb"]["under_xvfb"])
        self.assertEqual(d["x_servers"][0]["comm"], "Xwayland")

    def test_x_server_scan_structure_on_real_proc(self):
        """真实 /proc 扫描（_read_text/_read_bytes 桩为全 None）不崩且结构恒定。"""
        with mock.patch.object(em, "_read_text", lambda p: None), \
             mock.patch.object(em, "_read_bytes", lambda p: None):
            servers = em._list_x_servers()
        self.assertIsInstance(servers, list)
        for s in servers:
            self.assertEqual(set(s), {"pid", "comm", "cmdline"})


class TestGpuDriverAndVram(unittest.TestCase):
    def _gpu(self, canned):
        with patched_seams(cmds={} if canned is None else {"nvidia-smi": canned}):
            return em._collect_gpu_driver_and_vram()

    def test_multi_gpu_parsed(self):
        out = ("NVIDIA GeForce RTX 4080, 615.71.09, 16376 MiB, 4451 MiB\n"
               "NVIDIA GeForce RTX 3060, 615.71.09, 12288 MiB, 300 MiB\n")
        g = self._gpu(out)
        self.assertEqual(len(g["gpus"]), 2)
        self.assertEqual(g["gpus"][1]["memory_total_mib"], 12288)

    def test_fail_open_unavailable(self):
        self.assertEqual(self._gpu(None), "unavailable")   # 命令不可得
        bad_rc = subprocess.CompletedProcess([], 1, stdout="x", stderr="")
        with patched_seams(cmds={"nvidia-smi": bad_rc}):
            self.assertEqual(em._collect_gpu_driver_and_vram(), "unavailable")
        for malformed in ("",                                  # 空输出
                          "NVIDIA GeForce RTX 4080, 615.71.09",   # 列数不足
                          "NVIDIA, 615.71.09, [N/A], 100 MiB\n"):  # 显存畸形
            self.assertEqual(self._gpu(malformed), "unavailable", repr(malformed))


class TestFontsFingerprint(unittest.TestCase):
    def _fonts(self, cmds="DEFAULT", bytes_=None):
        if cmds == "DEFAULT":
            cmds = {"fc-list": FC_LINES, "fc-match format": FC_MATCH_FILE}
        seams = {"cmds": cmds,
                 "bytes_": bytes_ if bytes_ is not None else dict(FONT_BYTES)}
        with patched_seams(**seams):
            return em._collect_fonts_fingerprint()

    def test_family_match_case_insensitive(self):
        fp = self._fonts(cmds={
            "fc-list": "/usr/f/noto.ttc: noto sans cjk sc\n",
            "fc-match format": "/usr/f/noto.ttc\n",
        }, bytes_={"/usr/f/noto.ttc": b"cjk"})
        self.assertEqual(fp["required"]["Noto Sans CJK SC"]["status"], "ok")
        self.assertEqual(fp["required"]["Noto Sans CJK SC"]["files"],
                         {"/usr/f/noto.ttc": hashlib.sha256(b"cjk").hexdigest()})
        self.assertEqual(fp["required"]["Inter"]["status"], "missing")

    def test_fc_match_default_output_fallback_path(self):
        """--format %{file} 不被支持时回退解析默认输出 `<file>: <family>`
        （真机踩坑：默认输出可能只给 basename，全路径以 --format 为权威）。"""
        fp = self._fonts(cmds={"fc-list": FC_LINES, "fc-match": FC_MATCH_DEFAULT})
        self.assertEqual(fp["sans-serif"]["status"], "ok")
        self.assertEqual(fp["sans-serif"]["files"],
                         {"/usr/fake-fonts/NotoSansCJK-VF.ttc": NOTO_SHA})

    def test_unreadable_font_file_flags_entry(self):
        """字体文件存在但读不到内容 → 该家族 entry 判 unreadable（宁缺毋造）。"""
        fp = self._fonts(bytes_={k: v for k, v in FONT_BYTES.items()
                                 if k != "/usr/fake-fonts/Inter-Regular.otf"})
        self.assertEqual(fp["required"]["Inter"]["status"], "unreadable")
        self.assertIsNone(
            fp["required"]["Inter"]["files"]["/usr/fake-fonts/Inter-Regular.otf"])

    def test_fc_list_unavailable_explicit(self):
        fp = self._fonts(cmds={}, bytes_={})   # fc-list/fc-match 全不可得
        self.assertEqual(fp["status"], "unavailable")
        for fam in em.REQUIRED_FONT_FAMILIES:
            self.assertEqual(fp["required"][fam]["status"], "unavailable")
        self.assertEqual(fp["sans-serif"]["status"], "unavailable")

    def test_fc_match_unavailable_sans_flagged(self):
        fp = self._fonts(cmds={"fc-list": FC_LINES})   # fc-match 缺席
        self.assertEqual(fp["status"], "ok")
        self.assertEqual(fp["sans-serif"]["status"], "unavailable")


class TestRuntimeBackendTruth(unittest.TestCase):
    def test_none_rejected_no_forged_manifest(self):
        with self.assertRaises(ValueError) as ctx:
            em.collect_env_manifest("cdp-baseline", None)
        self.assertIn("内部接口", str(ctx.exception))
        self.assertIn("严禁", str(ctx.exception))

    def test_argv_only_evidence_rejected(self):
        """严禁仅转录命令行启动参数：source=argv / evidence 只含 argv 均拒绝。"""
        with self.assertRaises(ValueError) as ctx:
            em.collect_env_manifest("x", rt_info(source="argv"))
        self.assertIn("严禁转录", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            em.collect_env_manifest("x", rt_info(
                source="cdp", evidence={"argv": ["chrome", "--use-gl=egl"]}))
        self.assertIn("缺内部接口证据键", str(ctx.exception))

    def test_invalid_backend_vocabulary_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            em.collect_env_manifest("x", rt_info(effective_backend="hardware_gl"))
        self.assertIn("词表", str(ctx.exception))

    def test_per_source_required_evidence(self):
        cases = {
            "cdp": ({"browser_version": {"product": "Chrome/152"}}, "system_info"),
            "cef": ({"gl_probe": {"renderer": "NVIDIA GeForce RTX 4080"}}, "cef_version"),
            "electron": ({"gpu_feature_status": {"webgl": "enabled"}}, "gpu_info_complete"),
        }
        for source, (partial, missing_key) in cases.items():
            with self.assertRaises(ValueError) as ctx:
                em.collect_env_manifest("x", rt_info(source=source, evidence=partial))
            self.assertIn(missing_key, str(ctx.exception))
        # 三 source 证据齐备均可生成
        em.collect_env_manifest("x", rt_info(source="cef", evidence={
            "cef_version": {"cef_version_str": "132.2.5+g..."},
            "gl_probe": {"renderer": "NVIDIA GeForce RTX 4080"}}))
        em.collect_env_manifest("x", rt_info(source="electron", evidence={
            "gpu_feature_status": {"webgl": "enabled"},
            "gpu_info_complete": {"auxAttributes": {"gpuVendor": "nvidia"}}}))

    def test_chromium_major_derivation(self):
        self.assertEqual(
            em.collect_env_manifest("x", rt_info())["runtime_backend_truth"]["chromium_major"],
            152)   # product=Chrome/152.0.6834.83
        self.assertEqual(
            em.collect_env_manifest("x", rt_info(chromium_version="132.0.6834.83"))
            ["runtime_backend_truth"]["chromium_major"], 132)
        ua_only = rt_info(evidence={
            "browser_version": {"userAgent": "Mozilla/5.0 ... Chrome/151.0.0.0 Safari/537.36"},
            "system_info": {}})
        self.assertEqual(
            em.collect_env_manifest("x", ua_only)["runtime_backend_truth"]["chromium_major"], 151)
        none_info = rt_info(evidence={"browser_version": {}, "system_info": {}},
                            chromium_version="")
        self.assertIsNone(
            em.collect_env_manifest("x", none_info)["runtime_backend_truth"]["chromium_major"])

    def test_viewport_and_color_space_normalization(self):
        def truth(**over):
            with patched_seams():
                return em._runtime_backend_truth("x", rt_info(**over))
        self.assertEqual(truth()["viewport"], "1920x1080")
        self.assertEqual(truth(viewport="1920x1080")["viewport"], "1920x1080")
        self.assertEqual(truth(viewport=[1920, 1080])["viewport"], "1920x1080")
        self.assertIsNone(truth(viewport="garbage")["viewport"])
        self.assertIsNone(truth(viewport=None)["viewport"])
        self.assertEqual(truth()["color_space"], "srgb")
        self.assertEqual(truth(color_space="display-p3")["color_space"], "display-p3")

    def test_classify_gl_backend(self):
        self.assertEqual(em.classify_gl_backend("SwiftShader for x86"), "swiftshader")
        self.assertEqual(em.classify_gl_backend("llvmpipe (LLVM 15.0.7)"), "software_skia")
        self.assertEqual(em.classify_gl_backend("NVIDIA GeForce RTX 4080"), "nvidia_egl")
        self.assertEqual(em.classify_gl_backend("ANGLE (Vulkan, RADV)"), "vulkan")
        self.assertEqual(em.classify_gl_backend("ANGLE (OpenGL), Radeon"), "")   # 无法归类
        self.assertEqual(em.classify_gl_backend(""), "")


class TestVerifyCompatibility(unittest.TestCase):
    """DoD：篡改必检字段（字体 SHA256 / swiftshader 静默回退 等）100% 检出。"""

    SCENARIOS = [
        ("chromium_major", lambda m: m["runtime_backend_truth"].__setitem__("chromium_major", 151),
         ["主版本"]),
        ("chromium_major_missing", lambda m: m["runtime_backend_truth"].__setitem__("chromium_major", None),
         ["主版本", "不可得"]),
        ("gpu_silent_fallback_swiftshader",
         lambda m: m["runtime_backend_truth"].__setitem__("effective_backend", "swiftshader"),
         ["生效后端", "swiftshader"]),
        ("gpu_fallback_software_skia",
         lambda m: m["runtime_backend_truth"].__setitem__("effective_backend", "software_skia"),
         ["生效后端"]),
        ("color_space", lambda m: m["runtime_backend_truth"].__setitem__("color_space", "display-p3"),
         ["色彩空间"]),
        ("viewport", lambda m: m["runtime_backend_truth"].__setitem__("viewport", "3840x2160"),
         ["视口"]),
        ("viewport_missing", lambda m: m["runtime_backend_truth"].__setitem__("viewport", None),
         ["视口", "不可得"]),
        ("font_sha_noto", lambda m: _tamper_font_sha(m, "Noto Sans CJK SC",
                                                     "/usr/fake-fonts/NotoSansCJK-VF.ttc"),
         ["SHA256", "Noto Sans CJK SC"]),
        ("font_sha_inter", lambda m: _tamper_font_sha(m, "Inter",
                                                      "/usr/fake-fonts/Inter-Bold.otf"),
         ["SHA256", "Inter"]),
        ("font_sha_sans_fallback", _tamper_sans_sha, ["SHA256", "sans-serif"]),
        ("font_missing_one_side", _drop_inter, ["状态不一致", "Inter"]),
    ]

    def test_verify_tamper_scenarios_100pct_detected(self):
        """DoD 对抗主用例：全部单字段篡改场景逐项 100% 检出（非空 + 关键词）。"""
        base = stub_collect()
        for name, tamper, keywords in self.SCENARIOS:
            with self.subTest(scenario=name):
                b = json.loads(json.dumps(base))
                tamper(b)
                items = em.verify_manifest_compatibility(base, b)
                self.assertTrue(items, f"{name} 未检出")
                joined = " ".join(items)
                for kw in keywords:
                    self.assertIn(kw, joined, f"{name} 缺关键词 {kw}：{items}")

    def test_identical_manifests_compatible(self):
        a = stub_collect()
        self.assertEqual(em.verify_manifest_compatibility(a, json.loads(json.dumps(a))), [])

    def test_backend_id_difference_alone_is_not_incompatibility(self):
        """跨后端比对的本意：路线标识可不同，环境一致即可比像素。"""
        a = stub_collect(backend_id="cef-route-a")
        b = stub_collect(backend_id="electron-route-b")
        self.assertEqual(em.verify_manifest_compatibility(a, b), [])

    def test_fonts_evidence_unavailable_is_incompatible(self):
        """任一侧字体指纹不可得 → 无法证明一致 → 判不兼容（不能比像素）。"""
        a = stub_collect()
        b = json.loads(json.dumps(a))
        b["fonts_fingerprint"] = {"status": "unavailable", "required": {},
                                  "sans-serif": {"status": "unavailable", "files": {}}}
        items = em.verify_manifest_compatibility(a, b)
        self.assertTrue(any("字体指纹不可得" in i for i in items))

    def test_both_missing_same_family_compatible(self):
        """双侧同缺 JetBrains Mono → 回退路径一致，不构成不兼容项。"""
        a = stub_collect()
        b = stub_collect()
        for m in (a, b):
            m["fonts_fingerprint"]["required"]["JetBrains Mono"] = {"status": "missing",
                                                                    "files": {}}
        self.assertEqual(em.verify_manifest_compatibility(a, b), [])

    def test_missing_truth_section_reports_all_gaps(self):
        a = stub_collect()
        items = em.verify_manifest_compatibility(a, {"schema": "vtx.env_manifest/1"})
        joined = " ".join(items)
        for kw in ("主版本", "生效后端", "视口", "字体指纹"):
            self.assertIn(kw, joined)


class TestWriteManifest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_roundtrip_via_fstxn_atomic_write_no_tmp_residue(self):
        m = stub_collect()
        out = self.dir / "env_manifest.json"
        with mock.patch.object(em, "atomic_write",
                               wraps=em.atomic_write) as spy:
            em.write_manifest(out, m)
        spy.assert_called_once()   # 落盘路由= fstxn.atomic_write（工单项）
        self.assertEqual(json.loads(out.read_text(encoding="utf-8")), m)
        self.assertEqual(list(self.dir.iterdir()), [out])   # 无 .tmp 残件/锁件

    def test_failed_write_keeps_old_value(self):
        out = self.dir / "env_manifest.json"
        out.write_text('{"old": 1}', encoding="utf-8")
        with self.assertRaises(TypeError):
            em.write_manifest(out, {"bad": object()})   # 不可序列化 → 不落半份
        self.assertEqual(out.read_text(encoding="utf-8"), '{"old": 1}')
        self.assertEqual(list(self.dir.iterdir()), [out])


class TestCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _run(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = em.main(argv)
        return rc, buf.getvalue()

    def test_collect_then_verify_ok_and_tamper_detected(self):
        info = self.dir / "rt.json"
        info.write_text(json.dumps(rt_info()), encoding="utf-8")
        out = self.dir / "env_manifest.json"
        with patched_seams(cmds=stub_cmds(), texts=dict(PROC_TEXTS),
                           bytes_=dict(FONT_BYTES), servers=[], environ=dict(ENV_X11)):
            rc, text = self._run(["collect", "--backend-id", "cdp-baseline",
                                  "--runtime-info", str(info), "--out", str(out)])
        self.assertEqual(rc, 0)
        self.assertIn("已写入", text)
        manifest = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(manifest["runtime_backend_truth"]["chromium_major"], 152)

        tampered = json.loads(json.dumps(manifest))
        tampered["runtime_backend_truth"]["effective_backend"] = "swiftshader"
        tb = self.dir / "b.json"
        tb.write_text(json.dumps(tampered), encoding="utf-8")
        rc, text = self._run(["verify", str(out), str(tb)])
        self.assertEqual(rc, 1)
        self.assertIn("生效后端", text)
        rc, text = self._run(["verify", str(out), str(out)])
        self.assertEqual(rc, 0)
        self.assertIn("环境一致", text)

    def test_collect_rejects_argv_only_runtime_info(self):
        info = self.dir / "rt.json"
        info.write_text(json.dumps(rt_info(source="argv")), encoding="utf-8")
        rc, text = self._run(["collect", "--backend-id", "x",
                              "--runtime-info", str(info),
                              "--out", str(self.dir / "m.json")])
        self.assertEqual(rc, 1)
        self.assertIn("拒绝生成", text)
        self.assertFalse((self.dir / "m.json").exists())

    def test_collect_missing_runtime_info_file(self):
        rc, text = self._run(["collect", "--backend-id", "x",
                              "--runtime-info", str(self.dir / "nope.json"),
                              "--out", str(self.dir / "m.json")])
        self.assertEqual(rc, 1)
        self.assertIn("读取失败", text)


def _make_frame(path: Path, i: int):
    im = Image.new("RGBA", (W, H), (i % 256, (i * 7) % 256, (i * 13) % 256, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


def _project(root: Path, clips):
    for src, _ in clips:
        p = root / src
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("<html><body>fake gfx page</body></html>", encoding="utf-8")
    tl = {"project": {"fps": 30, "duration_frames": sum(d for _, d in clips),
                      "width": 1920, "height": 1080},
          "sources": [], "video_tracks": [{"name": "gfx", "kind": "gfx",
                                           "clips": [{"src": s, "dst_in": 0, "dur": d}
                                                     for s, d in clips]}],
          "subtitles": [], "audio_tracks": []}
    tp = root / "timeline.json"
    tp.write_text(json.dumps(tl), encoding="utf-8")
    return tp


def _fill_cache(tp: Path, clips):
    c = gg.make_ctx(tp)
    for src, dur in clips:
        d = c.cache / "gfx" / gg.hygiene.gfx_key(c, {"src": src, "dur": dur})
        for i in range(dur):
            _make_frame(d / f"f{i:04d}.png", i)


class TestGfxGoldenEnvGate(unittest.TestCase):
    """消费点接入：scripts/gfx_golden.py build --env-manifest 嵌入基线，
    compare --env-manifest 跨后端环境前置门禁（不兼容 → 拒绝像素比对）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.tp = _project(self.root, [("pages/a.html", 6)])
        _fill_cache(self.tp, [("pages/a.html", 6)])
        self.golden = self.root / "golden.json"
        # 路线 A/B 各一份 manifest（同一环境基线：字体/GPU/视口全一致）
        self.ma = self._write_manifest("cef-route-a", rt_info())
        self.mb = self._write_manifest("electron-route-b", rt_info())

    def _write_manifest(self, backend_id, rt):
        p = self.root / f"env_{backend_id}.json"
        em.write_manifest(p, stub_collect(backend_id, rt))
        return p

    def _run(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = gg.main(argv)
        return rc, buf.getvalue()

    def test_build_embeds_manifest_and_gate_passes(self):
        rc, _ = self._run(["build", "-t", str(self.tp), "--golden", str(self.golden),
                           "--env-manifest", str(self.ma)])
        self.assertEqual(rc, 0)
        g = json.loads(self.golden.read_text(encoding="utf-8"))
        self.assertEqual(g["meta"]["env_manifest"]["backend_id"], "cef-route-a")
        rc, text = self._run(["compare", "-t", str(self.tp), "--golden", str(self.golden),
                              "--env-manifest", str(self.mb)])
        self.assertEqual(rc, 0)
        self.assertIn("环境前置门禁通过", text)

    def test_gate_blocks_pixel_compare_on_incompatibility(self):
        self._run(["build", "-t", str(self.tp), "--golden", str(self.golden),
                   "--env-manifest", str(self.ma)])
        # 篡改 B：GPU 静默回退 swiftshader → 门禁拒绝（像素比对不再进行）
        b = json.loads(self.mb.read_text(encoding="utf-8"))
        b["runtime_backend_truth"]["effective_backend"] = "swiftshader"
        self.mb.write_text(json.dumps(b), encoding="utf-8")
        rc, text = self._run(["compare", "-t", str(self.tp), "--golden", str(self.golden),
                              "--env-manifest", str(self.mb)])
        self.assertEqual(rc, 1)
        self.assertIn("生效后端", text)
        self.assertIn("门禁未通过", text)
        # 字体 SHA256 篡改同样拦截
        b2 = json.loads(self.ma.read_text(encoding="utf-8"))
        b2["fonts_fingerprint"]["required"]["Noto Sans CJK SC"]["files"][
            "/usr/fake-fonts/NotoSansCJK-VF.ttc"] = "0" * 64
        other = self.root / "font_tamper.json"
        other.write_text(json.dumps(b2), encoding="utf-8")
        rc, text = self._run(["compare", "-t", str(self.tp), "--golden", str(self.golden),
                              "--env-manifest", str(other)])
        self.assertEqual(rc, 1)
        self.assertIn("SHA256", text)

    def test_gate_warns_when_golden_has_manifest_but_flag_missing(self):
        self._run(["build", "-t", str(self.tp), "--golden", str(self.golden),
                   "--env-manifest", str(self.ma)])
        rc, text = self._run(["compare", "-t", str(self.tp), "--golden", str(self.golden)])
        self.assertEqual(rc, 0)
        self.assertIn("门禁未激活", text)

    def test_gate_refuses_when_golden_lacks_manifest(self):
        rc, _ = self._run(["build", "-t", str(self.tp), "--golden", str(self.golden)])
        self.assertEqual(rc, 0)
        g = json.loads(self.golden.read_text(encoding="utf-8"))
        self.assertIsNone(g["meta"]["env_manifest"])
        rc, text = self._run(["compare", "-t", str(self.tp), "--golden", str(self.golden),
                              "--env-manifest", str(self.mb)])
        self.assertEqual(rc, 1)
        self.assertIn("golden 未嵌入环境 manifest", text)

    def test_build_rejects_missing_manifest_file(self):
        rc, text = self._run(["build", "-t", str(self.tp), "--golden", str(self.golden),
                              "--env-manifest", str(self.root / "nope.json")])
        self.assertEqual(rc, 1)
        self.assertIn("读取失败", text)
        self.assertFalse(self.golden.exists())


if __name__ == "__main__":
    unittest.main()
