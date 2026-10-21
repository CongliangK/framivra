"""Shared filesystem and plugin fixtures; no test cases or import-time mutation."""
import hashlib
import json
import os
from pathlib import Path
import sys

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / 'scripts'


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
