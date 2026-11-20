"""Read-only identity for the built-in music producer (no engine imports).

This is shared by BGM source caches and the pipeline receipt. Small code/config
files are content-bound. Tool executables use stat identity; external library
closures remain explicitly declared by sources[].bgm.extra_deps. No service,
GPU, renderer or downloader is launched while calculating identity.
"""
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from .bgm_gen import paths
from .bgm_gen.ref_slots import scan_ref_file, track_ref_slots
from .bgm_gen.sf2_shorthand import parse_instrument_shorthand

APP_ROOT = Path(__file__).resolve().parent


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def code_identity(root=None):
    """All music implementation/descriptor/schema/workflow bytes, not tests/data."""
    root = APP_ROOT if root is None else Path(root)
    files = list(root.glob("*.py"))
    if not (root / "bgm.py").is_file():
        raise ValueError("内置音乐组件缺少 bgm.py")
    for name in ("bgm_gen", "workflows"):
        base = root / name
        if not base.is_dir():
            raise ValueError(f"内置音乐组件缺少 {name}")
        files.extend(p for p in base.rglob("*")
                     if p.suffix in (".py", ".json")
                     and not {"tests", "__pycache__"}.intersection(p.relative_to(base).parts)
                     and p.name != "runtime.json" and p.is_file())
    records = []
    for p in sorted(set(files)):
        records.append([p.relative_to(root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest()])
    return _digest(records)


def _native_windows_media_tool(command, env, application):
    """Look for the literal .exe used by bare CreateProcess media launches."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    def directory(name):
        getter = getattr(kernel, name)
        getter.argtypes = (wintypes.LPWSTR, wintypes.UINT)
        getter.restype = wintypes.UINT
        buf = ctypes.create_unicode_buffer(32768)
        length = getter(buf, len(buf))
        if not 0 < length < len(buf):
            raise ctypes.WinError(ctypes.get_last_error())
        return Path(buf.value)

    windows = directory("GetWindowsDirectoryW")
    caller = Path(application).absolute()
    # The default BGM child uses this interpreter; a Windows venv redirector
    # loads the base Python image before that child launches any media tool.
    if caller == Path(sys.executable).absolute():
        caller = Path(getattr(sys, "_base_executable", None) or sys.executable)
    search = [caller.parent]
    if not any(name.upper() == "NODEFAULTCURRENTDIRECTORYINEXEPATH" for name in env):
        search.append(Path.cwd())
    search.extend((directory("GetSystemDirectoryW"), windows / "System", windows))
    path = env.get("PATH", os.environ.get("PATH", os.defpath))
    search.extend(Path(value.strip('"')) for value in path.split(os.pathsep) if value)
    for root in search:
        executable = root / (command + ".exe")
        if executable.is_file():
            return executable
    return None


def _tool_identity(command, env, *, application=None):
    if os.name == "nt" and application and command in ("ffmpeg", "ffprobe"):
        resolved = _native_windows_media_tool(command, env, application)
    else:
        resolved = shutil.which(command, path=env.get("PATH")) if command else None
    return _selected_tool_identity(command, resolved)


def _selected_tool_identity(command, resolved):
    if not resolved:
        return {"command": command, "state": "missing"}
    selected = Path(resolved).absolute()
    p = selected.resolve()
    st = p.stat()
    return {"selected": str(selected), "path": str(p), "size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "ctime_ns": st.st_ctime_ns}


def _python_packages(python):
    """Bind installed NumPy distribution records without importing NumPy.

    RECORD includes package content hashes at installation; it is not a proof
    against arbitrary in-place edits or a full dynamic-library closure.
    """
    # Keep the logical venv entry: resolving its symlink would incorrectly
    # inspect the base interpreter's site-packages rather than the selected env.
    executable = Path(python).absolute()
    prefix = executable.parent.parent if executable.parent.name in ("bin", "Scripts") else executable.parent
    records = {}
    for pattern in ("lib/python*/site-packages/numpy-*.dist-info/RECORD",
                    "Lib/site-packages/numpy-*.dist-info/RECORD"):
        for p in sorted(prefix.glob(pattern)):
            records[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return records


def execution_identity(command, env):
    """Return identity and effective runtime; never return private config values."""
    runtime = paths.effective_runtime(environ=env)
    tools = {"python": _tool_identity(command[0], env),
             "ffmpeg": _tool_identity("ffmpeg", env, application=command[0]),
             "ffprobe": _tool_identity("ffprobe", env, application=command[0])}
    # Configured tools are user-owned dependencies. Hash their resolved file
    # identity when present; a shared-library soname may remain loader-resolved.
    configured_tools = {}
    for name, value in (runtime.get("tools") or {}).items():
        if not isinstance(value, str):
            continue
        if os.name == 'nt' and name == 'fluidsynth_lib' and Path(value).is_absolute():
            # ctypes loads the explicitly named file directly. Executable PATH
            # discovery can omit it because .DLL is not normally in PATHEXT.
            library = Path(value)
            configured_tools[name] = _selected_tool_identity(value, library if library.is_file() else None)
        else:
            configured_tools[name] = _tool_identity(value, env)
    # Bind the interpreters actually selected by QC and optional local engines,
    # separately from host tools. Preserve each logical venv entry for RECORD.
    qc_python = runtime.get("python_system")
    if not isinstance(qc_python, str) or not os.path.isfile(qc_python):
        qc_python = command[0]
    child_commands = {
        "qc": qc_python,
        "matchering": (runtime.get("matchering") or {}).get("python") or runtime.get("python_torch"),
        "demucs": (runtime.get("demucs") or {}).get("python") or runtime.get("python_torch"),
    }
    child_pythons = {}
    for name, value in child_commands.items():
        if isinstance(value, str) and value:
            tool = _tool_identity(value, env)
            child_pythons[name] = {
                "tool": tool,
                "numpy_distribution": (_python_packages(tool["selected"])
                                       if tool.get("selected") else {}),
            }
    relevant_env = {key: env.get(key) for key in (
        "MUSIC_RUNTIME", "MUSIC_ASSETS_ROOT", "MUSIC_SAMPLES_ROOT", "XDG_DATA_HOME",
        "PYTHONPATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS")}
    return {"version": 1, "code": code_identity(), "runtime": _digest(runtime),
            "environment": _digest(relevant_env), "tools": tools,
            "configured_tools": configured_tools, "child_pythons": child_pythons,
            "numpy_distribution": _python_packages(command[0])}, runtime


def asset_dependencies(project, runtime):
    """Conservative asset roots for the project's deterministic render engines.

    The normal SFZ root is included wholesale (including includes/samples in
    that root); unusual external include trees still require extra_deps. The
    caller applies the documented bounded directory signature to these roots.
    $ref projects are traversed without executing music code.
    """
    project = Path(project)
    try:
        data = json.loads(project.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # The source file itself is already bound. Schema/probe validation owns
        # the actionable malformed-project diagnostic, not this optional scan.
        return []
    if not isinstance(data, dict) or not isinstance(data.get("tracks", []), list):
        return []
    tracks = []
    refs = dict(track_ref_slots(data))
    for i, track in enumerate(data.get("tracks", [])):
        if isinstance(track, dict) and "$ref" in track:
            if i not in refs:
                continue  # Invalid slots are not inline tracks or dependencies.
            ref_path, exists = scan_ref_file(project, refs[i])
            if not exists:
                continue
            try:
                track = json.loads(ref_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # $ref bytes/missing state are bound by the adapter.
        if isinstance(track, dict):
            tracks.append(track)
    dependencies = set()
    master = data.get("master")
    if isinstance(master, dict) and master.get("chain") == "matchering":
        ref = master.get("ref")
        if isinstance(ref, str) and ref:
            # Matchering consumes this path relative to caller cwd, not project
            # location, and does not expand ~ or environment variables.
            dependencies.add(os.path.abspath(ref))
    for track in tracks:
        engine, instrument = track.get("engine"), track.get("instrument")
        if engine == "sample":
            # Whole samples root also covers files supplied by sample hits.
            dependencies.add(str(runtime["samples_root"]))
            if isinstance(instrument, str) and os.path.isabs(os.path.expanduser(instrument)):
                dependencies.add(os.path.expanduser(instrument))
        elif engine == "sf2":
            if not isinstance(instrument, str):
                continue
            try:
                parsed = parse_instrument_shorthand(instrument)
            except ValueError:
                parsed = None  # Render projection owns the invalid shorthand diagnostic.
            ref = parsed[0] if parsed is not None else instrument
            p = os.path.expanduser(ref)
            if os.path.isabs(p):
                dependencies.add(p)
            elif isinstance(runtime.get("sf2"), dict) and isinstance(runtime["sf2"].get(ref), str):
                dependencies.add(runtime["sf2"][ref])
        elif engine == "sfz":
            dependencies.add(str(Path(runtime["assets_root"]) / "sfz"))
            if isinstance(instrument, str) and os.path.isabs(os.path.expanduser(instrument)):
                dependencies.add(str(Path(instrument).expanduser().parent))
    return sorted(dependencies)
