#!/usr/bin/env python3
"""Local, paired CEF benchmark: plan (read only), run (new output only), report.

This is a trusted-local-input developer tool, not a renderer sandbox or the SVG
release baseline. It never builds, installs, checks out, retries, or cleans an
existing project. Only a new output directory is written. Raw local evidence is
not suitable for publication without review. See --help for the execution scope.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import subprocess
import sys
# Helper imports must not write bytecode into either benchmark checkout.
sys.dont_write_bytecode = True
import time
from datetime import datetime, timezone

# The reporter reuses this checkout's pure execution-event validator. Measured
# commands still execute each role's explicit vtx/vtx.py, never this import path.
_TOOL_REPO = str(Path(__file__).resolve().parents[2])
sys.path[:] = [entry for entry in sys.path if entry != _TOOL_REPO]
sys.path.insert(0, _TOOL_REPO)

SCHEMA = "framivra.cef-benchmark/1"
EXCLUDED_ROOTS = frozenset({"cache", "out", ".git"})
FIXED_ENV = {
    "VTX_GFX": "cef", "VTX_CEF_MODE": "a0", "VTX_CEF_SYNC_MODE": "n",
    "VTX_CEF_REFERENCE_CONTEXT": "shared", "VTX_GFX_PAGES": "1",
    "VTX_GFX_SPLIT_FRAMES": "0", "VTX_GFX_SPLIT_MAX": "1",
    "VTX_CEF_PNG_ENCODER": "spng", "VTX_CEF_PNG_LEVEL": "1",
    "VTX_CEF_PNG_FILTER": "sub_up", "VTX_CEF_ENCODE_THREADS": "2",
    "VTX_CEF_CAPTURE_FPS": "120", "VTX_PIPELINE": "1",
    "VTX_VIDEO_SEGMENTS": "8", "VTX_FILTER_THREADS": "1",
    "VTX_NO_NVENC": "1", "VTX_SEG_NVENC": "0",
}
CHOICES = {
    "VTX_CEF_PNG_ENCODER": {"spng", "fpng", "zlib"},
    "VTX_CEF_PNG_FILTER": {"none", "sub", "up", "sub_up", "all"},
    "VTX_CEF_SYNC_MODE": {"n", "p"},
    "VTX_CEF_REFERENCE_CONTEXT": {"shared", "isolated"},
    "VTX_CEF_MODE": {"a0", "a1"},
}
INTEGERS = {
    "VTX_CEF_PNG_LEVEL": (1, 3), "VTX_CEF_ENCODE_THREADS": (1, 16),
    "VTX_CEF_CAPTURE_FPS": (1, 240), "VTX_GFX_PAGES": (1, 32),
    "VTX_GFX_SPLIT_FRAMES": (0, 65536), "VTX_GFX_SPLIT_MAX": (1, 32),
}


class BenchmarkError(ValueError):
    """A validation or execution failure, never an invitation to retry."""


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _physical(path, kind, executable=False):
    path = Path(path).expanduser().absolute()
    # Tool symlinks (e.g. Homebrew's bin) are resolved to the actual executable.
    # Project entries, unlike explicit executable selectors, may not be links.
    if kind == "directory" and path.is_symlink():
        raise BenchmarkError(f"symlink directory is not accepted: {path}")
    path = path.resolve(strict=True)
    info = path.stat()
    valid = stat.S_ISDIR(info.st_mode) if kind == "directory" else stat.S_ISREG(info.st_mode)
    if not valid or (executable and not os.access(path, os.X_OK)):
        raise BenchmarkError(f"expected {'executable ' if executable else ''}{kind}: {path}")
    return path


def _file_identity(path, keep_symlink=False):
    physical = _physical(path, "file", executable=True)
    logical = Path(path)
    if not keep_symlink or not logical.is_symlink():
        logical = physical
    elif not logical.is_absolute():
        logical = Path.cwd() / logical
    return {"path": str(logical), "sha256": _sha256(physical), "size": physical.stat().st_size}


def _tool_identities(args):
    # venv 入口是对解释器的符号链接；物理化会丢掉 venv 身份及其安装包
    # 清单（gfx 环境核验依赖 importlib.metadata RECORD），故 python 保留
    # 逻辑路径，身份哈希仍按物理文件计算。ffmpeg/ffprobe 不受影响。
    return {"python": _file_identity(args.python, keep_symlink=True),
            "ffmpeg": _file_identity(args.ffmpeg),
            "ffprobe": _file_identity(args.ffprobe)}


def _base_env(tools):
    dirs = [str(Path(tools[name]["path"]).parent) for name in ("ffmpeg", "ffprobe")]
    dirs += ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    path = os.pathsep.join(dict.fromkeys(dirs))
    for name in ("ffmpeg", "ffprobe"):
        selected = shutil.which(name, path=path)
        if not selected or Path(selected).resolve() != Path(tools[name]["path"]):
            raise BenchmarkError(f"PATH cannot resolve {name} to the explicitly selected executable")
    # Preserve the real HOME for host font identity. Do NOT copy the ambient
    # environment: Python shadow paths, proxy variables, credentials and VTX_*
    # options are deliberately absent. VTX_CONFIG is set privately at run time.
    return {"PATH": path, "HOME": str(Path.home()), "LANG": "C", "LC_ALL": "C",
            "TZ": "UTC", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}


def _git(repo, *args):
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(Path.home()),
           "LANG": "C", "LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1"}
    git = shutil.which("git", path=env["PATH"])
    if not git:
        raise BenchmarkError("git is unavailable in the fixed system PATH")
    result = subprocess.run([git, "--no-optional-locks", "-c", "core.fsmonitor=false",
                             "-c", "core.untrackedCache=false", "-C", str(repo), *args],
                            env=env, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise BenchmarkError(f"git {' '.join(args)} failed for {repo}: {result.stderr.strip()}")
    return result.stdout.strip()


def _source_identity(repo, worker):
    repo = _physical(repo, "directory")
    if Path(_git(repo, "rev-parse", "--show-toplevel")).resolve() != repo:
        raise BenchmarkError(f"repository must name its checkout root: {repo}")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise BenchmarkError(f"repository must be clean (tracked and untracked files): {repo}")
    entry = repo / "vtx" / "vtx.py"
    if (repo / "vtx").is_symlink() or entry.is_symlink() or not entry.is_file():
        raise BenchmarkError(f"actual vtx/vtx.py entry point is required: {entry}")
    return {"repo": str(repo), "head": _git(repo, "rev-parse", "HEAD"),
            "tree": _git(repo, "rev-parse", "HEAD^{tree}"), "clean": True,
            "worker": _file_identity(worker)}


def _overlaps(first, second):
    first, second = Path(first), Path(second)
    return first == second or first in second.parents or second in first.parents


def input_manifest(root):
    """Hash the full local input tree, excluding ONLY top-level cache/out/.git."""
    root = _physical(root, "directory")
    entries = []
    def walk_error(error):
        raise BenchmarkError(f"unreadable input tree: {error}")
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        parent = Path(directory)
        for name in sorted(dirs + files):
            item = parent / name
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise BenchmarkError(f"symlink or special input is forbidden: {item}")
        if parent == root:
            dirs[:] = [name for name in dirs if name not in EXCLUDED_ROOTS]
            files = [name for name in files if name not in EXCLUDED_ROOTS]
        for name in sorted(dirs):
            entries.append({"path": (parent / name).relative_to(root).as_posix(), "type": "directory"})
        for name in sorted(files):
            item = parent / name
            info = item.stat()
            entries.append({"path": item.relative_to(root).as_posix(), "type": "file",
                            "size": info.st_size, "mode": stat.S_IMODE(info.st_mode),
                            "sha256": _sha256(item)})
    entries.sort(key=lambda row: row["path"])
    return {"schema": "framivra.cef-benchmark-inputs/1", "entries": entries,
            "sha256": hashlib.sha256(_json_bytes(entries)).hexdigest()}


def _local_input(root, value, label):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise BenchmarkError(f"{label} must name a relative project-local input")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or ":" in value or relative.parts[0] in EXCLUDED_ROOTS:
        raise BenchmarkError(f"{label} must not reference external or excluded inputs: {value}")
    path = root.joinpath(*relative.parts)
    if not path.is_file() or path.is_symlink():
        raise BenchmarkError(f"{label} is not a regular existing project input: {value}")
    return path


def _workload(project):
    timeline = project / "timeline.json"
    if timeline.is_symlink() or not timeline.is_file():
        raise BenchmarkError("project requires a regular root timeline.json")
    data = json.loads(timeline.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("project"), dict):
        raise BenchmarkError("timeline must be an object with a project object")
    geometry = data["project"]
    for name, value in {"fps": 30, "duration_frames": 900, "width": 1920, "height": 1080}.items():
        actual = geometry.get(name)
        if isinstance(actual, bool) or actual != value:
            raise BenchmarkError(f"CEF workload requires {name}={value}; got {actual!r}")
    html_clips = 0
    tracks = data.get("video_tracks", [])
    if not isinstance(tracks, list):
        raise BenchmarkError("video_tracks must be a list")
    for track in tracks:
        if not isinstance(track, dict) or not isinstance(track.get("clips"), list):
            raise BenchmarkError("each video track requires a clips list")
        for clip in track["clips"]:
            if not isinstance(clip, dict):
                raise BenchmarkError("each clip must be an object")
            if clip.get("format") == "svg-sequence":
                raise BenchmarkError("SVG route is not a CEF benchmark; supply an original HTML project")
            if track.get("kind") == "gfx":
                if clip.get("format", "html") != "html":
                    raise BenchmarkError("CEF workload accepts only HTML gfx clips")
                source = _local_input(project, clip.get("src"), "gfx clip src")
                if source.suffix.lower() not in {".html", ".htm"}:
                    raise BenchmarkError("gfx clip src must be an HTML file")
                html_clips += 1
    if not html_clips:
        raise BenchmarkError("CEF workload requires at least one HTML gfx clip")
    sources = data.get("sources", [])
    if not isinstance(sources, list) or any(not isinstance(source, dict) for source in sources):
        raise BenchmarkError("sources must be a list of objects")
    for source in sources:
        _local_input(project, source.get("path"), "source path")
    # Scan only contract-defined path fields; IDs/text are not file paths. The
    # product's actual `all` command remains the full timeline validator.
    def dependencies(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "cache_dependencies":
                    if not isinstance(child, list):
                        raise BenchmarkError("cache_dependencies must be a list")
                    for item in child:
                        _local_input(project, item, "cache dependency")
                elif key == "subtitles_src" and child not in (None, ""):
                    _local_input(project, child, "subtitles_src")
                else:
                    dependencies(child)
        elif isinstance(value, list):
            for child in value:
                dependencies(child)
    dependencies(data)
    return {"timeline": "timeline.json", "duration_frames": 900, "fps": 30,
            "width": 1920, "height": 1080, "seconds": 30, "html_clips": html_clips,
            "scope": "CEF HTML workload; not the SVG release baseline"}


def _overrides(items):
    output = {}
    for item in items:
        key, separator, value = item.partition("=")
        if not separator or key in output:
            raise BenchmarkError(f"invalid or repeated environment assignment: {item}")
        if key in CHOICES:
            valid = value in CHOICES[key]
        elif key in INTEGERS:
            low, high = INTEGERS[key]
            valid = value.isascii() and value.isdigit() and len(value) <= 5 and low <= int(value) <= high
        else:
            raise BenchmarkError(f"environment key is not allowlisted: {key}")
        if not valid:
            raise BenchmarkError(f"invalid benchmark environment value: {item}")
        output[key] = value
    return output


def make_plan(args):
    if type(args.rounds) is not int or not 1 <= args.rounds <= 10:
        raise BenchmarkError("rounds must be 1..10")
    _positive_seconds(args.timeout)
    _sample_interval(args.sample_interval)
    if os.name != "posix":
        raise BenchmarkError("CEF benchmark process/resource support currently requires macOS or Linux")
    project = _physical(args.project, "directory")
    workload = _workload(project)
    manifest = input_manifest(project)
    baseline = _source_identity(args.baseline_repo, args.baseline_worker)
    candidate = _source_identity(args.candidate_repo, args.candidate_worker)
    if _overlaps(baseline["repo"], candidate["repo"]):
        raise BenchmarkError("baseline and candidate require independent, non-overlapping checkouts")
    for source in (baseline, candidate):
        if _overlaps(project, source["repo"]):
            raise BenchmarkError("original project must be separate from both checkouts")
    tools = _tool_identities(args)
    environment = _base_env(tools)
    common = dict(FIXED_ENV, **_overrides(args.common_set))
    candidate_env = _overrides(args.candidate_set)
    if candidate_env and not args.ablation:
        raise BenchmarkError("--candidate-set requires a declared --ablation label")
    if args.ablation and (not args.ablation.strip() or not candidate_env
                          or len(args.ablation) > 200 or any(ord(c) < 32 for c in args.ablation)):
        raise BenchmarkError("--ablation requires candidate overrides and a short printable label")
    return {"workload": workload, "project": str(project), "input_digest": manifest["sha256"],
            "baseline": baseline, "candidate": candidate, "common_env": common,
            "candidate_env": candidate_env, "ablation": args.ablation, "tools": tools,
            "environment": environment, "rounds": args.rounds,
            "timeout_seconds": args.timeout, "sample_interval_seconds": args.sample_interval,
            "platform": {"system": platform.system(), "release": platform.release(),
                         "machine": platform.machine(), "python": "driver " + platform.python_version(),
                         "driver_python": platform.python_version()},
            "order": "AB/BA alternating by round; each role cold then warm in one private project",
            "timing_scope": "all-command wall only; preparation, separate strict QC and media verification excluded",
            "limitations": ["Trusted local HTML and declared local dependencies only; no sandbox guarantee",
                            "Native worker SHA does not attest its entire CEF SDK/distribution",
                            "Cold means empty project cache/out, not OS page cache eviction",
                            "CPU sampling is a lower bound; summed sampled RSS double-counts shared pages and may miss peaks",
                            "Controlled environment is not a reproduction of unrecorded historical runs"]}, manifest


def _write_json(path, value):
    # Only called for paths below the exclusively-created private output root.
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    os.replace(temporary, path)


def _copy_inputs(source, destination, expected):
    if input_manifest(source)["sha256"] != expected["sha256"]:
        raise BenchmarkError("input identity changed before copy")
    def ignore(directory, names):
        return EXCLUDED_ROOTS.intersection(names) if Path(directory) == source else ()
    shutil.copytree(source, destination, symlinks=False, ignore=ignore)
    copied = input_manifest(destination)
    if copied["sha256"] != expected["sha256"] or input_manifest(source)["sha256"] != expected["sha256"]:
        raise BenchmarkError("input identity changed during copy")


def _rel(path, root):
    return Path(path).relative_to(root).as_posix()


def _native_hashes(project):
    native = project / "cache" / "reports" / "cef"
    if not native.exists():
        return {}
    if native.is_symlink() or not native.is_dir():
        raise BenchmarkError(f"unsafe native evidence directory: {native}")
    result = {}
    for path in native.rglob("*"):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise BenchmarkError(f"unsafe native evidence: {path}")
        if stat.S_ISREG(info.st_mode):
            result[path.relative_to(native).as_posix()] = _sha256(path)
    return result


def _snapshot(project, evidence, root, prefix="", prior_native=None):
    evidence.mkdir(parents=True, exist_ok=True)
    result = {}
    for name, key in (("receipt.json", "receipt_path"), ("qc_report.json", "qc_path")):
        source = project / "out" / name
        if source.exists():
            if source.is_symlink() or not source.is_file():
                raise BenchmarkError(f"unsafe evidence file: {source}")
            target = evidence / (prefix + name)
            shutil.copy2(source, target)
            result[key] = _rel(target, root)
        else:
            result[key] = None
    native = project / "cache" / "reports" / "cef"
    result["native_paths"] = []
    result["native_inherited_paths"] = []
    if native.exists() and not prefix:
        if native.is_symlink() or not native.is_dir():
            raise BenchmarkError(f"unsafe native evidence directory: {native}")
        for directory, dirs, files in os.walk(native, followlinks=False):
            for name in dirs + files:
                source = Path(directory) / name
                info = source.lstat()
                if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    raise BenchmarkError(f"unsafe native evidence: {source}")
            for name in sorted(files):
                source = Path(directory) / name
                target = evidence / "native" / source.relative_to(native)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                digest = _sha256(target)
                inherited = (prior_native or {}).get(source.relative_to(native).as_posix()) == digest
                field = "native_inherited_paths" if inherited else "native_paths"
                result[field].append({"path": _rel(target, root), "sha256": digest})
    return result


def _evidence_files(evidence, root):
    rows = {}
    for path in sorted(evidence.rglob("*")):
        if path.is_symlink():
            raise BenchmarkError(f"unsafe evidence link: {path}")
        if path.is_file():
            rows[_rel(path, root)] = {"sha256": _sha256(path), "size": path.stat().st_size}
    return rows


def _succeeded(command):
    elapsed = command.get("elapsed_seconds")
    return (type(command.get("exit_code")) is int and command["exit_code"] == 0
            and command.get("timed_out") is False and not command.get("error")
            and not command.get("cleanup", {}).get("errors")
            and type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed > 0)


def _publish_report(record, root):
    from _bench_report import build_report, render_markdown
    _write_json(root / "run.json", record)
    record["execution_status"] = record["status"]
    summary = build_report(record, root)
    if record["status"] == "passed" and summary.get("execution_valid") is not True:
        record["status"] = "failed"
        record["errors"].append("independent evidence verification failed; see report qualification blockers")
        _write_json(root / "run.json", record)
        summary = build_report(record, root)
    else:
        _write_json(root / "run.json", record)
    _write_json(root / "summary.json", summary)
    (root / "report.md").write_text(render_markdown(summary), encoding="utf-8")
    return summary


def _new_output(value, plan):
    raw = Path(value).expanduser().absolute()
    if raw.exists() or raw.is_symlink():
        raise BenchmarkError("--output must be a new, nonexistent directory; nothing will be deleted")
    parent = raw.parent.resolve(strict=True)
    root = parent / raw.name
    for protected in (plan["project"], plan["baseline"]["repo"], plan["candidate"]["repo"]):
        if _overlaps(root, protected):
            raise BenchmarkError("output must be outside the original project and both repositories")
    root.mkdir(mode=0o700)
    return root


def run_benchmark(args):
    from _bench_runtime import run_command, verify_media
    plan, manifest = make_plan(args)
    root = _new_output(args.output, plan)
    record = {"schema": SCHEMA, "status": "running", "started_utc": utc_now(),
              "plan": plan, "runs": [], "errors": [], "original_input_valid": None}
    _write_json(root / "run.json", record)
    _write_json(root / "input_manifest.json", manifest)
    frozen = root / "inputs"
    try:
        started = time.monotonic()
        _copy_inputs(Path(plan["project"]), frozen, manifest)
        record["freeze_seconds"] = time.monotonic() - started
        private = root / "private"
        private.mkdir(mode=0o700)
        config = private / "config.json"
        config.write_text("{}\n", encoding="utf-8")
        config.chmod(0o600)
        for round_number in range(1, args.rounds + 1):
            roles = ("baseline", "candidate") if round_number % 2 else ("candidate", "baseline")
            for role in roles:
                project = root / "projects" / f"round-{round_number:03d}" / role
                project.parent.mkdir(parents=True, exist_ok=True)
                started = time.monotonic()
                _copy_inputs(frozen, project, manifest)
                preparation_seconds = time.monotonic() - started
                for state in ("cold", "warm"):
                    evidence = root / "evidence" / f"round-{round_number:03d}" / role / state
                    evidence.mkdir(parents=True)
                    temporary = private / f"round-{round_number:03d}-{role}-{state}"
                    temporary.mkdir(mode=0o700)
                    row = {"round": round_number, "role": role, "cache_state": state,
                           "status": "running", "input_digest": manifest["sha256"],
                           "preparation_seconds": preparation_seconds if state == "cold" else 0.0,
                           "snapshot_seconds": 0.0, "qa_seconds": 0.0,
                           "command": None, "strict_command": None, "media": None,
                           "events_path": _rel(evidence / "all.events.jsonl", root),
                           "strict_events_path": _rel(evidence / "strict.events.jsonl", root),
                           "receipt_path": None, "qc_path": None, "native_paths": [],
                           "source_identity_valid": False, "tools_identity_valid": False}
                    record["runs"].append(row)
                    _write_json(root / "run.json", record)
                    failure = None
                    prior_native = {}
                    try:
                        identity = plan[role]
                        row["identity_before"] = _source_identity(identity["repo"], identity["worker"]["path"])
                        row["tools_before"] = _tool_identities(args)
                        if row["identity_before"] != identity or row["tools_before"] != plan["tools"]:
                            raise BenchmarkError("source or tool identity changed before command")
                        if input_manifest(project)["sha256"] != manifest["sha256"]:
                            raise BenchmarkError("private project inputs changed before command")
                        prior_native = _native_hashes(project)
                        env = dict(plan["environment"], **plan["common_env"])
                        if role == "candidate":
                            env.update(plan["candidate_env"])
                        env.update(VTX_CONFIG=str(config), VTX_CEF_WORKER=identity["worker"]["path"],
                                   TMPDIR=str(temporary), TMP=str(temporary), TEMP=str(temporary))
                        entry = [plan["tools"]["python"]["path"], "-B", str(Path(identity["repo"]) / "vtx/vtx.py"),
                                 "-t", str(project / "timeline.json")]
                        argv = entry + ["--events-jsonl", str(root / row["events_path"]), "all"]
                        row["command"] = run_command(argv, str(project), env, str(evidence / "all.log"),
                                                     args.timeout, args.sample_interval)
                        row["command"]["argv"] = argv
                        started = time.monotonic()
                        all_snapshot = _snapshot(project, evidence, root, prefix="all.")
                        row["all_receipt_path"], row["all_qc_path"] = all_snapshot["receipt_path"], all_snapshot["qc_path"]
                        row["snapshot_seconds"] += time.monotonic() - started
                        if not _succeeded(row["command"]):
                            raise BenchmarkError("vtx all failed; strict QC, media and dependent runs not started")
                        row["identity_after_all"] = _source_identity(identity["repo"], identity["worker"]["path"])
                        row["tools_after_all"] = _tool_identities(args)
                        if (row["identity_after_all"] != identity or row["tools_after_all"] != plan["tools"]
                                or input_manifest(project)["sha256"] != manifest["sha256"]):
                            raise BenchmarkError("identity changed during vtx all; dependent steps not started")
                        started = time.monotonic()
                        strict_argv = entry + ["--events-jsonl", str(root / row["strict_events_path"]), "qc", "--strict"]
                        row["strict_command"] = run_command(strict_argv, str(project), env, str(evidence / "strict.log"),
                                                            args.timeout, args.sample_interval)
                        row["strict_command"]["argv"] = strict_argv
                        row["qa_seconds"] += time.monotonic() - started
                        if not _succeeded(row["strict_command"]):
                            raise BenchmarkError("strict QC failed; media and dependent runs not started")
                        started = time.monotonic()
                        row["media"] = verify_media(project, evidence / "media", plan["tools"]["ffmpeg"]["path"],
                                                    plan["tools"]["ffprobe"]["path"], args.timeout, env)
                        row["qa_seconds"] += time.monotonic() - started
                        row["media"]["evidence"] = {name: _rel(Path(path), root)
                            for name, path in row["media"].get("evidence", {}).items()}
                        if row["media"].get("status") != "passed":
                            raise BenchmarkError("independent media verification failed; dependent runs not started")
                    except (Exception, KeyboardInterrupt) as error:
                        failure = error
                        row["error"] = f"{type(error).__name__}: {error}"
                    finally:
                        started = time.monotonic()
                        try:
                            row.update(_snapshot(project, evidence, root, prior_native=prior_native))
                            row["evidence_files"] = _evidence_files(evidence, root)
                            identity = plan[role]
                            row["identity_after"] = _source_identity(identity["repo"], identity["worker"]["path"])
                            row["tools_after"] = _tool_identities(args)
                            row["source_identity_valid"] = (row.get("identity_before") == identity == row["identity_after"]
                                and input_manifest(project)["sha256"] == manifest["sha256"]
                                and input_manifest(frozen)["sha256"] == manifest["sha256"])
                            row["tools_identity_valid"] = row.get("tools_before") == plan["tools"] == row["tools_after"]
                            if not row["source_identity_valid"] or not row["tools_identity_valid"]:
                                raise BenchmarkError("source, input or tool identity changed during command")
                        except (Exception, KeyboardInterrupt) as error:
                            row["identity_error"] = f"{type(error).__name__}: {error}"
                            failure = failure or error
                        row["snapshot_seconds"] += time.monotonic() - started
                        row["status"] = "failed" if failure else "passed"
                        if failure is None:
                            started = time.monotonic()
                            try:
                                from _bench_report import build_report
                                partial = build_report(record, root)
                                current = partial["runs"][-1]
                                if current.get("correctness") != "passed":
                                    row["verification_errors"] = current.get("issues", [])
                                    raise BenchmarkError("current run evidence is invalid; dependent runs not started")
                                for kind in ("video_digest", "audio_digest", "audio_timeline_digest"):
                                    observed = {r["media"].get(kind) for r in record["runs"] if r.get("media")}
                                    if len(observed) > 1:
                                        raise BenchmarkError(f"{kind} differs across completed runs; dependent runs not started")
                            except (Exception, KeyboardInterrupt) as error:
                                failure = error
                                row["status"] = "failed"
                                row["error"] = f"{type(error).__name__}: {error}"
                            row["analysis_seconds"] = time.monotonic() - started
                        _write_json(root / "run.json", record)
                    if failure:
                        raise BenchmarkError(f"round {round_number} {role} {state}: {failure}")
        record["original_input_valid"] = input_manifest(Path(plan["project"]))["sha256"] == manifest["sha256"]
        if not record["original_input_valid"]:
            raise BenchmarkError("original project inputs changed during benchmark")
        record["status"] = "passed"
    except (Exception, KeyboardInterrupt) as error:
        record["status"] = "failed"
        record["errors"].append(f"{type(error).__name__}: {error}")
    finally:
        try:
            record["original_input_valid"] = input_manifest(Path(plan["project"]))["sha256"] == manifest["sha256"]
            if not record["original_input_valid"]:
                record["status"] = "failed"
                record["errors"].append("original project inputs changed during benchmark")
        except Exception as error:
            record["status"] = "failed"
            record["errors"].append(f"original input recheck failed: {type(error).__name__}: {error}")
        record["finished_utc"] = utc_now()
        # A report is attempted even for a partial/failed run. If analysis fails,
        # run.json remains complete evidence and no successful result is claimed.
        try:
            _publish_report(record, root)
        except Exception as error:
            record["status"] = "failed"
            record["errors"].append(f"report failed: {type(error).__name__}: {error}")
            _write_json(root / "run.json", record)
            (root / "report.md").write_text("# CEF benchmark: FAILED\n\nReport generation failed; inspect run.json.\n", encoding="utf-8")
    print(str(root / "report.md"))
    return 0 if record["status"] == "passed" else 1


def _bounded_int(value):
    result = int(value)
    if not 1 <= result <= 10:
        raise argparse.ArgumentTypeError("rounds must be 1..10")
    return result


def _positive_seconds(value):
    result = float(value)
    if not math.isfinite(result) or not 0.05 <= result <= 86400:
        raise argparse.ArgumentTypeError("seconds must be finite and between 0.05 and 86400")
    return result


def _sample_interval(value):
    result = float(value)
    if not math.isfinite(result) or not 0.05 <= result <= 60:
        raise argparse.ArgumentTypeError("sampling interval must be finite and between 0.05 and 60 seconds")
    return result


def parser():
    result = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        command = commands.add_parser(name, allow_abbrev=False,
            help="read-only validated plan" if name == "plan" else "execute trusted local inputs; writes only new --output")
        command.add_argument("--project", required=True, help="original input directory with root timeline.json; never modified")
        command.add_argument("--baseline-repo", required=True)
        command.add_argument("--baseline-worker", required=True)
        command.add_argument("--candidate-repo", required=True)
        command.add_argument("--candidate-worker", required=True)
        command.add_argument("--ffmpeg", required=True, help="existing executable; never installed or wrapped")
        command.add_argument("--ffprobe", required=True, help="existing executable; never installed or wrapped")
        command.add_argument("--python", default=sys.executable)
        command.add_argument("--rounds", type=_bounded_int, default=3)
        command.add_argument("--timeout", type=_positive_seconds, default=3600.0, help="per-command timeout seconds (no retry)")
        command.add_argument("--sample-interval", type=_sample_interval, default=1.0)
        command.add_argument("--common-set", action="append", default=[], metavar="KEY=VALUE", help="allowlisted CEF runtime/scheduling settings, both roles")
        command.add_argument("--candidate-set", action="append", default=[], metavar="KEY=VALUE", help="allowlisted candidate-only setting; requires --ablation")
        command.add_argument("--ablation", help="declare candidate-only environment ablation; not same-settings revision comparison")
        if name == "run":
            command.add_argument("--output", required=True, help="new directory; existing paths refused; no cleanup or resume")
    report = commands.add_parser("report", allow_abbrev=False, help="read saved evidence, never replay commands")
    report.add_argument("output", help="existing benchmark output directory")
    report.add_argument("--json", action="store_true", help="emit JSON instead of Markdown to stdout (no writes)")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "plan":
            plan, _ = make_plan(args)
            print(json.dumps({"schema": SCHEMA, "status": "planned", "plan": plan}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "run":
            return run_benchmark(args)
        from _bench_report import build_report, render_markdown
        root = _physical(args.output, "directory")
        record = json.loads((root / "run.json").read_text(encoding="utf-8"))
        summary = build_report(record, root)
        print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) if args.json else render_markdown(summary))
        return 0
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as error:
        print(f"CEF benchmark BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
