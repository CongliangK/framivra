#!/usr/bin/env python3
"""Import the five exact, private production assets. Offline; Python 3.9+.

Run from the production folder: python scripts/prepare_assets.py --from PACK
PACK contains audio/T17.flac ... audio/T20.flac and media/paper-background.mp4.
Requires existing ffmpeg/ffprobe and POSIX no-follow directory operations
(macOS/Linux). Manifests are read only from this script's production package.
All path ancestors must be real directories. On macOS, use /private/tmp or
/private/var paths instead of the /tmp or /var symlink aliases.
"""
import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).absolute().parents[1]
STEMS = ("T17", "T18", "T19", "T20")
PAPER = "media/paper-background.mp4"
PCM_BYTES = 5760000 * 2 * 3
MAX_PACK_BYTES = 64 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


@contextmanager
def directory(path, parent=None):
    """Pin every directory component; never follow a symlink, including parents."""
    require(hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"),
            "This helper requires macOS/Linux no-follow directory operations")
    path = Path(path).absolute() if parent is None else Path(path)
    require(".." not in path.parts, "Parent traversal is not allowed: " + str(path))
    with ExitStack() as stack:
        if parent is None:
            fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
            stack.callback(os.close, fd)
            parts = path.parts[1:]
        else:
            require(not path.is_absolute(), "Expected a relative directory")
            fd, parts = parent, path.parts
        for part in parts:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=fd)
            stack.callback(os.close, fd)
        yield fd


def read_file(fd, name, limit):
    """Read a bounded regular file from a pinned directory, without following links."""
    raw = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(raw, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode), "Not a regular file: " + name)
        require(info.st_size <= limit, "File exceeds expected size: " + name)
        value = stream.read(limit + 1)
        require(len(value) <= limit, "File grew beyond expected size: " + name)
        return value


def digest(value):
    return hashlib.sha256(value).hexdigest()


def manifest_specs(audio_fd, media_fd):
    stems = json.loads(read_file(audio_fd, "stems.json", 1024 * 1024))
    paper = json.loads(read_file(media_fd, "paper-recipe.json", 1024 * 1024))
    require(stems["format"] == "framivra-lossless-stems/1", "Wrong stem manifest")
    rows = stems["stems"]
    require(len(rows) == 4 and {r["id"] for r in rows} == set(STEMS),
            "Expected exactly T17, T18, T19 and T20")
    specs = []
    for role in STEMS:
        row = next(r for r in rows if r["id"] == role)
        require(row["path"] == "audio/" + role + ".flac", "Unsafe stem path")
        require((row["sample_rate"], row["channels"], row["sample_frames"])
                == (48000, 2, 5760000), "Unexpected stem audio contract")
        specs.append(dict(path=row["path"], sha256=row["sha256"],
                          size=row["bytes"], pcm=row["pcm24_sha256"]))
    require(paper["package_path"] == PAPER, "Unsafe paper path")
    specs.append(dict(path=PAPER, sha256=paper["sha256"],
                      size=int(paper["probe"]["format"]["size"]), pcm=None))
    for spec in specs:
        require(type(spec["size"]) is int and 0 < spec["size"] <= MAX_PACK_BYTES,
                "Invalid asset byte count")
        for value in (spec["sha256"], spec["pcm"]):
            require(value is None or (isinstance(value, str)
                    and re.fullmatch(r"[0-9a-f]{64}", value)), "Invalid asset hash")
        require(spec["sha256"] is not None, "Missing file hash")
    require(all(s["pcm"] is not None for s in specs[:4]), "Missing PCM hash")
    require(sum(s["size"] for s in specs) <= MAX_PACK_BYTES, "Pack exceeds limit")
    return specs


def run(command, data, output=subprocess.PIPE, timeout=120):
    result = subprocess.run(command, input=data, stdout=output,
                            stderr=subprocess.PIPE, timeout=timeout, check=False)
    require(result.returncode == 0 and not result.stderr.strip(),
            command[0] + " failed: " + result.stderr.decode(errors="replace")[-2000:])
    return result.stdout


def validate_payload(spec, data):
    """Probe native formats, then fully decode; never resample or normalize."""
    is_audio = spec["pcm"] is not None
    demuxer = "flac" if is_audio else "mov"
    input_args = ["-protocol_whitelist", "pipe", "-f", demuxer, "-i", "pipe:0"]
    fields = ("codec_type,codec_name,sample_rate,channels,bits_per_raw_sample,"
              "width,height,avg_frame_rate,nb_read_frames")
    probe = ["ffprobe", "-v", "error"] + input_args
    if not is_audio:
        probe += ["-count_frames"]
    streams = json.loads(run(probe + ["-show_entries", "stream=" + fields,
                                     "-of", "json"], data, timeout=60))["streams"]
    command = ["ffmpeg", "-nostdin", "-v", "error", "-xerror",
               "-err_detect", "explode", "-threads", "1"] + input_args
    if is_audio:
        require(len(streams) == 1, "Expected one FLAC stream: " + spec["path"])
        stream = streams[0]
        require((stream.get("codec_type"), stream.get("codec_name"),
                 stream.get("sample_rate"), stream.get("channels"),
                 stream.get("bits_per_raw_sample")) == ("audio", "flac", "48000", 2, "24"),
                "Expected native 48 kHz stereo PCM24 FLAC: " + spec["path"])
        with tempfile.TemporaryFile() as pcm:
            # One extra second bounds bad input while still rejecting every extra sample.
            run(command + ["-map", "0:a:0", "-t", "121", "-c:a", "pcm_s24le", "-f", "s24le", "-"],
                data, output=pcm)
            require(pcm.tell() == PCM_BYTES, "Wrong decoded sample count: " + spec["path"])
            pcm.seek(0)
            h = hashlib.sha256()
            for block in iter(lambda: pcm.read(1024 * 1024), b""):
                h.update(block)
            require(h.hexdigest() == spec["pcm"], "PCM hash mismatch: " + spec["path"])
    else:
        require(len(streams) == 2, "Expected video and silent audio in paper media")
        video, audio = streams
        require((video.get("codec_type"), video.get("codec_name"), video.get("width"),
                 video.get("height"), video.get("avg_frame_rate"), video.get("nb_read_frames"))
                == ("video", "h264", 1920, 1080, "30/1", "300"), "Wrong paper video format")
        require((audio.get("codec_type"), audio.get("codec_name"), audio.get("sample_rate"),
                 audio.get("channels")) == ("audio", "aac", "48000", 2), "Wrong paper audio format")
        run(command + ["-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
            data, output=subprocess.DEVNULL)


def existing_identical(fd, name, data):
    try:
        old = read_file(fd, name, len(data))
    except FileNotFoundError:
        return False
    require(old == data, "Refusing to overwrite differing existing file: " + name)
    return True


def prepare_assets(pack, root=ROOT):
    """Validate everything before installing; roll back our new files on failure."""
    with ExitStack() as stack:
        # Hold directory descriptors throughout, even if a directory is renamed.
        root_fd = stack.enter_context(directory(root))
        pack_fd = stack.enter_context(directory(pack))
        target = {name: stack.enter_context(directory(name, root_fd))
                  for name in ("audio", "media")}
        source = {name: stack.enter_context(directory(name, pack_fd))
                  for name in ("audio", "media")}
        specs = manifest_specs(target["audio"], target["media"])
        loaded = []
        for spec in specs:
            folder, name = spec["path"].split("/")
            data = read_file(source[folder], name, spec["size"])
            require(len(data) == spec["size"] and digest(data) == spec["sha256"],
                    "File hash/size mismatch: " + spec["path"])
            existing_identical(target[folder], name, data)
            loaded.append((spec, data, target[folder], name))
        for spec, data, _, _ in loaded:
            validate_payload(spec, data)

        created = []
        retained = []
        try:
            for spec, data, fd, name in loaded:
                try:
                    raw = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                  0o666, dir_fd=fd)
                except FileExistsError:
                    require(existing_identical(fd, name, data), "Destination disappeared: " + name)
                    retained.append(spec["path"])
                    continue
                with os.fdopen(raw, "wb") as output:
                    created.append((fd, name, os.fstat(output.fileno()), spec["path"]))
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
        except BaseException:
            for fd, name, original, _ in reversed(created):
                try:
                    now = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if (now.st_dev, now.st_ino) == (original.st_dev, original.st_ino):
                        os.unlink(name, dir_fd=fd)
                except FileNotFoundError:
                    pass
            raise
        return {"installed": [row[3] for row in created], "retained": retained,
                "validated": [spec["path"] for spec in specs]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="pack", required=True, type=Path,
                        help="Unpacked private asset folder (audio/ and media/)")
    args = parser.parse_args()
    try:
        result = prepare_assets(args.pack)
    except (OSError, ValueError, KeyError, TypeError, StopIteration,
            subprocess.TimeoutExpired) as error:
        print("Asset preparation failed: " + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
