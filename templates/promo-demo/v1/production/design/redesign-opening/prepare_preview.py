#!/usr/bin/env python3
"""Prepare a new 300-frame single-page project; never render or alter an old project.

MIT, 2026 Framivra contributors. Standard library only, macOS/Linux.
The independently reviewed source-manifest SHA is required. By default this only
validates and reports a plan; --write exclusively creates a new output directory.
The four exact full stems remain separate. Their events end at frame 300, so the
normal audio stage measures a new ten-second mix rather than copying the old mix.
"""
import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

HERE = Path(__file__).absolute().parent
PRODUCTION = HERE.parents[1]
SOURCE_NAMES = ("index.html", "geometry.js", "opening.js", "cues.json",
                "preview-timeline.json", "prepare_preview.py", "inspect_preview.py", "run_native_preview.py")
STEMS = ("T17", "T18", "T19", "T20")
LIMIT = 32 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


@contextmanager
def directory(path, parent=None):
    # Same no-follow descriptor pattern as production/scripts/prepare_assets.py.
    require(hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"), "macOS/Linux required")
    path = Path(path).absolute() if parent is None else Path(path)
    require(".." not in path.parts, "Parent traversal is not allowed")
    with ExitStack() as stack:
        if parent is None:
            fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
            stack.callback(os.close, fd)
            parts = path.parts[1:]
        else:
            require(not path.is_absolute(), "Expected relative directory")
            fd, parts = parent, path.parts
        for part in parts:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            stack.callback(os.close, fd)
        yield fd


def read_file(fd, name, limit=LIMIT):
    require(Path(name).name == name and name not in ("", ".", ".."), "Unsafe filename")
    raw = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(raw, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_size <= limit, "Invalid or oversized file: " + name)
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        require(len(data) <= limit and (before.st_size, before.st_mtime_ns) ==
                (after.st_size, after.st_mtime_ns), "File changed while reading: " + name)
        return data


def load_source(expected_manifest_sha256, source=HERE, production=PRODUCTION):
    require(re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256 or ""), "Exact manifest SHA256 required")
    with directory(source) as fd:
        manifest_bytes = read_file(fd, "source-manifest.json", 1024 * 1024)
        require(sha(manifest_bytes) == expected_manifest_sha256, "Source manifest identity mismatch")
        manifest = json.loads(manifest_bytes)
        require(manifest["format"] == "framivra-opening-source-identity/1", "Wrong source manifest format")
        require(set(manifest["files"]) == set(SOURCE_NAMES), "Unexpected source inventory")
        files = {}
        for name in SOURCE_NAMES:
            data = read_file(fd, name, 1024 * 1024)
            require(sha(data) == manifest["files"][name], "Changed source: " + name)
            files[name] = data
    with directory(Path(production) / "audio") as fd:
        stems_bytes = read_file(fd, "stems.json", 1024 * 1024)
    require(sha(stems_bytes) == manifest["production_stems_sha256"], "Changed original stem manifest")
    files["source-manifest.json"] = manifest_bytes
    files["stems.json"] = stems_bytes
    return files


def load_stems(assets, manifest_bytes):
    manifest = json.loads(manifest_bytes)
    require(manifest["format"] == "framivra-lossless-stems/1", "Wrong stem manifest")
    rows = manifest["stems"]
    require(len(rows) == 4 and {r["id"] for r in rows} == set(STEMS), "Four original stems required")
    payloads = {}
    with directory(Path(assets) / "audio") as fd:
        for role in STEMS:
            row = next(r for r in rows if r["id"] == role)
            require(row["path"] == "audio/" + role + ".flac", "Unsafe stem path")
            require((row["sample_rate"], row["channels"], row["sample_frames"]) ==
                    (48000, 2, 5760000), "Changed stem duration/rate/channels")
            require(type(row["bytes"]) is int and 0 < row["bytes"] <= LIMIT, "Invalid stem size")
            data = read_file(fd, role + ".flac", row["bytes"])
            require(len(data) == row["bytes"] and sha(data) == row["sha256"], "Stem hash/size mismatch: " + role)
            payloads[role + ".flac"] = data
    return payloads


def validate_timeline(data):
    tl = json.loads(data)
    require(tl["version"] == 2, "Timeline must use existing version 2")
    p = tl["project"]
    require((p["fps"], p["width"], p["height"], p["duration_frames"]) ==
            (30, 1920, 1080, 300), "Expected full-size 300-frame timeline")
    require(tl["video_tracks"] == [{"name": "paper-swift-single-scene", "kind": "gfx",
            "clips": [{"src": "scene/index.html", "dst_in": 0, "dur": 300}]}], "One source-authored HTML scene required")
    require(tl["subtitles"] == [], "No new captions in this visual study")
    require(tl["sources"] == [{"id": role, "path": "audio/" + role + ".flac"} for role in STEMS], "Original audio sources required")
    tracks = tl["audio_tracks"]
    require(len(tracks) == 4, "Keep the four editable audio roles")
    for track, role in zip(tracks, STEMS):
        require(track["events"] == [{"src": role, "src_in": 0, "in": 0, "out": 300,
                                     "volume": 1, "loop": False}], "Changed audio event/gain")
    return tl


def write_new_tree(out, payloads):
    # Pin the parent and new directories; all writes are exclusive and descriptor-relative.
    # On failure preserve the incomplete new folder for diagnosis, never delete unfamiliar data.
    with ExitStack() as stack:
        parent = stack.enter_context(directory(out.parent))
        parent_identity = os.fstat(parent)
        os.mkdir(out.name, dir_fd=parent)
        root = stack.enter_context(directory(out.name, parent))
        original = os.fstat(root)
        dirs = {"": root}
        for folder in sorted({str(Path(name).parent) for name in payloads} - {"."}):
            require(len(Path(folder).parts) == 1, "Only flat project folders are supported")
            os.mkdir(folder, dir_fd=root)
            dirs[folder] = stack.enter_context(directory(folder, root))
        for name, data in payloads.items():
            path = Path(name)
            folder = "" if path.parent == Path(".") else str(path.parent)
            raw = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                          0o666, dir_fd=dirs[folder])
            with os.fdopen(raw, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        for folder, fd in dirs.items():
            if folder:
                current = os.stat(folder, dir_fd=root, follow_symlinks=False)
                pinned = os.fstat(fd)
                require((current.st_dev, current.st_ino) == (pinned.st_dev, pinned.st_ino), "New project subdirectory was replaced")
        for name, data in payloads.items():
            path = Path(name)
            folder = "" if path.parent == Path(".") else str(path.parent)
            require(read_file(dirs[folder], path.name, len(data)) == data, "New project file changed during preparation")
        with directory(out.parent) as current_parent:
            current = os.fstat(current_parent)
            require((current.st_dev, current.st_ino) == (parent_identity.st_dev, parent_identity.st_ino), "Output parent path was replaced")
        now = os.stat(out.name, dir_fd=parent, follow_symlinks=False)
        require((now.st_dev, now.st_ino) == (original.st_dev, original.st_ino), "New project path was replaced")


def prepare(assets, out, expected_manifest_sha256, write=False, source=HERE, production=PRODUCTION):
    source, production, assets, out = map(lambda p: Path(p).absolute(), (source, production, assets, out))
    require(".." not in out.parts, "Parent traversal is not allowed")
    # Refuse source/asset/repository descendants: all generated projects live separately.
    repo = Path(production).parents[3]
    for protected in (source, production, assets, repo):
        require(out != protected and protected not in out.parents, "Output must be outside source/asset/repository roots")
    with directory(out.parent) as fd:
        try:
            os.stat(out.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError("Use a new project directory; existing destinations are never reused")
    files = load_source(expected_manifest_sha256, source, production)
    validate_timeline(files["preview-timeline.json"])
    stems = load_stems(assets, files["stems.json"])
    payloads = {"scene/" + name: files[name] for name in ("index.html", "geometry.js", "opening.js")}
    payloads.update({"audio/" + name: data for name, data in stems.items()})
    payloads.update({"metadata/" + name: files[name] for name in ("cues.json", "stems.json", "source-manifest.json")})
    payloads["timeline.json"] = files["preview-timeline.json"]
    binding = {"format": "framivra-isolated-opening-binding/1", "source_manifest_sha256": expected_manifest_sha256,
               "files": {name: sha(data) for name, data in payloads.items()}, "frames": 300, "fps": 30,
               "visual_entries": 1, "audio_roles": list(STEMS), "original_mix_reused_verbatim": False,
               "audio_note": "Original exact stems and unity event gain; formal audio stage measures/normalizes the independent ten-second mix. New PCM and listening required.",
               "source_byte_validation": True, "stem_decode_in_this_helper": False,
               "HTML_render": "UNRUN", "audio_render": "UNRUN", "user_acceptance": "PENDING"}
    payloads["source-binding.json"] = json_bytes(binding)
    if write:
        write_new_tree(out, payloads)
    return {"status": "PREPARED_NOT_RENDERED" if write else "READ_ONLY_PLAN_VALIDATED",
            "project": str(out), "binding": binding,
            "native_commands_from_reviewed_engine_checkout": [
                [sys.executable, "-m", "vtx", "-t", str(out / "timeline.json"), "all"],
                [sys.executable, "-m", "vtx", "-t", str(out / "timeline.json"), "qc", "--strict"]],
            "write": write}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--assets", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--expected-source-manifest-sha256", required=True)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    try:
        result = prepare(args.assets, args.out, args.expected_source_manifest_sha256, args.write)
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        print("Preview preparation failed: " + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
