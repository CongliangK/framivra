"""Read one administrator-approved formal A/V archive; never select or render media."""
import hashlib
import os
from pathlib import Path
import re

from ..pipeline.constraints import parse_fps
from ..timeline_structure import strict_loads, structure_errors
from .preview_window_isolation import _pin_binding, _project_mounts, binding_digest, binding_read


SCHEMA = 'framivra.preview-formal-archive/v1'
MANIFEST = 'framivra.preview-formal-archive.manifest/v1'
SCOPE = 'operator-approved-formal-av-archive'
MEDIA_LIMIT = 5 * 1024**2
WIRE_LIMIT = 8 * 1024**2
_SOURCE_LIMIT = 64 * 1024**2
_INPUT_LIMIT = 256 * 1024**2
_TIMELINE_LIMIT = 1024**2
_ASSET = re.compile(r'assets/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\.(?:mp4|wav)')
_HEX = re.compile(r'[0-9a-f]{64}')


def _require(condition):
    if not condition:
        raise ValueError('formal archive identity or profile mismatch')


def _keys(obj, required, optional=''):
    need = set(required.split())
    _require(isinstance(obj, dict) and need <= obj.keys() <= need | set(optional.split()))


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _hex(value):
    return isinstance(value, str) and _HEX.fullmatch(value) is not None


def _facts(obj, limit):
    size, sha = obj['byte_length'], obj['sha256']
    _require(type(size) is int and 0 < size <= limit and _hex(sha))
    return size, sha


def _approved_timeline(doc, profile):
    name = doc['timeline']
    _require(isinstance(name, str) and '\x00' not in name and len(os.fsencode(name)) <= 4095)
    path = Path(name)
    root = path.parent
    _require(path.anchor == '/' and root != Path('/') and str(path) == name and path.suffix == '.json'
             and not {'.', '..', 'cache', 'out'}.intersection(path.parts)
             and root != profile and root not in profile.parents and profile not in root.parents)
    return path


def _manifest(raw, profile, proof):
    doc = strict_loads(raw)
    _keys(doc, 'schema scope timeline content_version start end fps sources sha256 byte_length '
               'wheel_sha256 runtime_projection_sha256')
    _require(doc['schema'] == MANIFEST and doc['scope'] == SCOPE
             and all(doc[k] == proof[k] for k in ('wheel_sha256', 'runtime_projection_sha256')))
    _approved_timeline(doc, profile)
    version = doc['content_version']
    _require(isinstance(version, str) and version.startswith('sha256:') and _hex(version[7:]))
    start, end, fps = doc['start'], doc['end'], doc['fps']
    _keys(fps, 'num den')
    num, den = fps['num'], fps['den']
    _require(type(num) is type(den) is int and (num, den) in ((30, 1), (30000, 1001))
             and type(start) is type(end) is int and 0 <= start < end < 10**16
             and num <= (end - start) * den <= 4 * num)
    sources = doc['sources']
    _require(isinstance(sources, dict) and 1 <= len(sources) <= 12)
    total = 0
    for name, facts in sources.items():
        _require(_ASSET.fullmatch(name) is not None)
        _keys(facts, 'sha256 byte_length')
        total += _facts(facts, _SOURCE_LIMIT)[0]
    _require(total <= _INPUT_LIMIT)
    _facts(doc, MEDIA_LIMIT)
    return doc


def _pin(stack, path, root, *, trusted=False):
    fd, guard = _pin_binding(stack, path, root, single_link=True)
    if trusted:
        info = os.fstat(fd)
        _require(info.st_uid in (0, os.geteuid()) and not info.st_mode & 0o022)
    return fd, guard


def read_bundle(stack, profile, proof, check):
    """Pin and authenticate fixed files, without inspecting the project."""
    bindings = []

    def read(name, limit):
        fd, guard = _pin(stack, profile / name, profile, trusted=True)
        data = binding_read(fd, guard, limit, check)
        facts = len(data), _digest(data)
        bindings.append((fd, guard, facts))
        return data, facts

    pin, _ = read('formal-archive.sha256', 65)
    _require(re.fullmatch(rb'[0-9a-f]{64}\n', pin) is not None)
    raw, (_, manifest_sha) = read('formal-archive.json', 16 * 1024)
    _require(manifest_sha.encode('ascii') == pin[:-1])
    manifest = _manifest(raw, profile, proof)
    media, facts = read('formal-archive.mp4', manifest['byte_length'])
    # This is only an MP4 marker check, not topology, clock or decoded QC.
    _require(facts == _facts(manifest, MEDIA_LIMIT) and len(media) >= 16
             and media[4:8] == b'ftyp' and media[8:12] != b'qt  ')
    return manifest, manifest_sha, media, bindings


def _declared_assets(raw, manifest):
    doc = strict_loads(raw)
    # Fail closed on extensions at every stored scope, including any plugin keys,
    # instead of discovering plugins/configuration or expanding declared paths.
    _keys(doc, 'version project sources video_tracks audio_tracks subtitles', '_vtx')
    _keys(doc['project'], 'fps width height duration_frames')
    _require(not structure_errors(doc, preserve_unmanaged=True))
    project = doc['project']
    rate = parse_fps(project['fps'])
    _require((project['width'], project['height']) == (1920, 1080)
             and manifest['end'] <= project['duration_frames']
             and manifest['fps'] == {'num': rate.numerator, 'den': rate.denominator})
    _require(1 <= len(doc['sources']) <= 12)
    paths = set()
    for source in doc['sources']:
        _keys(source, 'id path', 'transform')
        paths.add(source['path'])
    for track in doc['video_tracks']:
        _keys(track, 'name kind clips', 'id')
        _require(track['kind'] != 'gfx')
        for clip in track['clips']:
            _keys(clip, 'src src_in src_out dst_in dur', 'id fade_in_frames fade_out_frames scale x y opacity')
    for track in doc['audio_tracks']:
        _keys(track, 'name events', 'id duck duck_by duck_params')
        if 'duck_params' in track:
            _keys(track['duck_params'], '', 'threshold ratio attack_ms release_ms makeup')
        for event in track['events']:
            _keys(event, 'src in out volume', 'id src_in fade_in_frames fade_out_frames pan loop')
    for cue in doc['subtitles']:
        _keys(cue, 'in out text', 'id')
    _require(paths == manifest['sources'].keys())
    return paths


def pin_inputs(stack, manifest, profile, check):
    """Read the approved timeline, then only its exact approved source inventory."""
    path = _approved_timeline(manifest, profile)
    root = path.parent
    local = _project_mounts([path, *(root / name for name in manifest['sources'])])
    fd, guard = _pin(stack, path, root)
    raw = binding_read(fd, guard, _TIMELINE_LIMIT, check)
    sha = _digest(raw)
    _require('sha256:' + sha == manifest['content_version'])
    paths = _declared_assets(raw, manifest)
    bindings = [(fd, guard, (len(raw), sha))]
    for name in sorted(paths):
        fd, guard = _pin(stack, root / name, root)
        facts = _facts(manifest['sources'][name], _SOURCE_LIMIT)
        _require(binding_digest(fd, guard, facts[0], check) == facts)
        bindings.append((fd, guard, facts))

    def still_local():
        _require(_project_mounts(local) == local)

    return bindings, still_local


def recheck(bindings, check):
    for fd, guard, facts in bindings:
        _require(binding_digest(fd, guard, facts[0], check) == facts)
    for _, guard, _ in bindings:
        guard()
    check()
