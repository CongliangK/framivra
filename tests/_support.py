"""Shared filesystem and plugin fixtures; no test cases or import-time mutation."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import wave

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / 'scripts'


class OwnedInitCheckout:
    """Copied init CLI contract fixture, not hardware/model acceptance.

    No live local config or ambient credentials enter the child. Cleanup is
    registered before copying; init can create/keep config only in this tree.
    """

    def __init__(self, case):
        temporary = tempfile.TemporaryDirectory(prefix="vt-owned-init-")
        case.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        source = _SCRIPTS_DIR.parent
        paths = ["vt.py", "VERSION", "config.example.json", "tests/__init__.py",
                 "tests/_support.py", "scripts/new_project.sh",
                 "apps/asr/independence.py"]
        paths += [p.relative_to(source).as_posix()
                  for p in sorted((source / "vtx").rglob("*.py"))]
        paths += ["apps/blender-bridge/" + name for name in
                  ("json2blend.py", "verify_blend.py", "blend2json.py",
                   "subs_alpha.py", "audit_bundle.py")]
        for name in paths:
            src, dst = source / name, self.repo / name
            if src.is_symlink():
                raise AssertionError("init fixture requires regular source: " + name)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        home = self.root / "home"
        home.mkdir()
        self.config = self.root / "config.json"
        self.config.write_text("{}", encoding="utf-8")
        self.log = self.root / "requests.json"
        self.env = {"HOME": str(home), "USERPROFILE": str(home),
                    "XDG_CONFIG_HOME": str(home / "config"),
                    "XDG_CACHE_HOME": str(home / "cache"),
                    "XDG_DATA_HOME": str(home / "data"),
                    "TMPDIR": str(self.root), "TMP": str(self.root), "TEMP": str(self.root),
                    "PATH": "", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                    "VTX_CONFIG": str(self.config), "VTX_DOCTOR_OFFLINE": "1",
                    "VT_MOUNT_ROOT": str(home / "video"), "LANG": "C.UTF-8"}
        # Windows needs its system directory to load the selected interpreter;
        # do not copy the rest of the caller's environment (including proxies).
        if "SystemRoot" in os.environ:
            self.env["SystemRoot"] = os.environ["SystemRoot"]

    def run(self, *, cwd=None):
        command = [sys.executable, "-I", "-B", "-c",
                   "import sys; sys.path.insert(0, sys.argv[1]); "
                   "from tests._support import _owned_init_child; "
                   "raise SystemExit(_owned_init_child())",
                   str(self.repo), str(self.log)]
        return subprocess.run(command, cwd=cwd or self.repo, env=self.env,
                              capture_output=True, text=True, timeout=90)

    def requests(self):
        return json.loads(self.log.read_text(encoding="utf-8"))["requests"]


def _owned_init_child():
    """Execute the copied CLI with named dependency data and zero network I/O."""
    import urllib.request
    from unittest import mock

    root, log = map(Path, sys.argv[1:3])
    allowed = [[sys.executable, str(root / name), "--help"] for name in
               ("vtx/vtx.py", "apps/blender-bridge/audit_bundle.py")]
    record = {"requests": [], "help_commands": []}

    def guard(event, args):
        # Popen may use posix_spawn internally; both audit events must satisfy
        # the same exact executable/argv boundary for the two owned programs.
        if event in ("subprocess.Popen", "os.posix_spawn"):
            if args[0] != sys.executable or list(args[1]) not in allowed:
                raise AssertionError("init fixture blocked process: " + repr(args[1]))
        elif event.startswith("socket.") or event in (
                "os.system", "os.fork", "os.forkpty",
                "os.exec", "os.kill", "os.killpg"):
            raise AssertionError("init fixture blocked " + event)

    sys.addaudithook(guard)

    def no_request(request, *args, **kwargs):
        url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
        if len(record["requests"]) >= 8:
            raise AssertionError("init fixture exceeded request-attempt bound")
        record["requests"].append(url)
        raise OSError("owned init fixture: network is forbidden")

    def help_only(command, **kwargs):
        if command not in allowed:
            raise AssertionError("init fixture blocked command: " + repr(command))
        record["help_commands"].append(command)
        return subprocess.run(command, capture_output=True, text=True, timeout=30)

    with mock.patch.object(urllib.request, "urlopen", side_effect=no_request):
        import vt
        # These tests own JSON/cwd/request-order contracts, not dependency
        # availability. Only the two copied Python --help programs really run.
        with mock.patch.object(vt, "find_binary", side_effect=lambda names:
                               "fixture:" + names[0] if names[0] in ("ffmpeg", "ffprobe") else None), \
             mock.patch.object(vt, "find_chrome", return_value="fixture:chrome"), \
             mock.patch.object(vt, "tool_version", return_value="fixture:ffmpeg version"), \
             mock.patch.object(vt, "ffmpeg_encoders", return_value=""), \
             mock.patch.object(vt, "py3", return_value=sys.executable), \
             mock.patch.object(vt, "sh", side_effect=help_only):
            sys.argv[:] = [str(root / "vt.py"), "init", "--json"]
            try:
                return vt.main()
            finally:
                log.write_text(json.dumps(record), encoding="utf-8")


def tree_sha256(root):
    """Compare directory entries (including empty dirs/links) and every file byte."""
    result = {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[name] = ("symlink", os.readlink(path))
        elif path.is_dir():
            result[name] = ("directory",)
        else:
            result[name] = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
    return result


class _clean_import_path:
    """import 卫生：用例内还原洁净 sys.path——先序测试（如 test_cef_worker_pixel_ops
    顶层）可能把仓内 scripts/ 常驻注入 sys.path，令同名顶层模块 demo（scripts/demo.py，
    #177 唯一入口）顶替本用例的 external 插件；hooks 落点断言按设计拒绝，属测试
    环境问题而非被测行为，故在用例内还原。"""

    def __enter__(self):
        self.saved = sys.path[:]
        sys.path[:] = [p for p in sys.path
                       if Path(p or '.').resolve() != _SCRIPTS_DIR.resolve()]
        return self

    def __exit__(self, *exc):
        sys.path[:] = self.saved
        return None


def _make_plugin_dir(root: Path, name="demo", **over):
    d = root / name
    d.mkdir(parents=True)
    (d / "cli.py").write_text("print('ok')\n", encoding="utf-8")
    (d / "health.py").write_text("print('healthy')\n", encoding="utf-8")
    (d / "README.md").write_text("# demo\n", encoding="utf-8")
    m = {"name": name, "version": "1.0.0", "kind": "plugin",
         "entry": {"cli": ["cli.py"]},
         "health": {"type": "command", "command": "health.py"},
         "requires": {"core": ">=2.0.0", "binaries": [], "python_packages": [], "platforms": []},
         "config_keys": [],
         "docs": "README.md",
         "extends": {"schema_keys": [{"path": "sources[]", "key": "demo"}],
                     "hooks": ["prepare_source"]}}
    m.update(over)
    (d / "plugin.json").write_text(json.dumps(m), encoding="utf-8")
    return d, m


def small_timeline(root: Path) -> Path:
    """最小合法 timeline（无源无轨——Ctx 构造不触 ffmpeg；对齐 test_cache_state 形态）。"""
    tl = {"project": {"fps": 30, "duration_frames": 90, "width": 640, "height": 360},
          "sources": [], "video_tracks": [], "subtitles": [], "audio_tracks": []}
    p = root / "timeline.json"
    p.write_text(json.dumps(tl), encoding="utf-8")
    return p


def full_hd_timeline(root: Path, **over):
    tl = {"version": 2,
          "project": {"fps": 30, "duration_frames": 180, "width": 1920, "height": 1080},
          "sources": [], "video_tracks": [], "subtitles": [], "audio_tracks": []}
    tl.update(over)
    p = root / "timeline.json"
    p.write_text(json.dumps(tl), encoding="utf-8")
    return p


def cleanup_timeline(root: Path, *, subtitles=()) -> Path:
    """清理用例共用的1秒音频+gfx项目；缓存、交付件及CLI合同仍由各用例负责。"""
    (root / "assets").mkdir()
    with wave.open(str(root / "assets" / "voice.wav"), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(b"\0" * (48000 * 4))
    (root / "gfx").mkdir()
    (root / "gfx" / "title.html").write_text("<canvas></canvas>")
    timeline = root / "timeline.json"
    timeline.write_text(json.dumps({
        "version": 2,
        "project": {"fps": 30, "width": 1920, "height": 1080,
                    "duration_frames": 30},
        "sources": [{"id": "voice", "path": "assets/voice.wav"}],
        "video_tracks": [{"name": "title", "kind": "gfx", "clips": [
            {"src": "gfx/title.html", "dst_in": 0, "dur": 30}]}],
        "audio_tracks": [{"name": "voice", "events": [
            {"src": "voice", "in": 0, "out": 30}]}],
        "subtitles": list(subtitles)
    }))
    return timeline
