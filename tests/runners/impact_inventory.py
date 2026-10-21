"""Acceptance-only raw-file inventory; mutable metadata is not a cache product.

Everything under cache is a product unless explicitly classified below. Thus
unknown product namespaces/extensions fail exact deltas instead of disappearing.
Main/QC outputs, receipts, lock/completion state, ASS assembly and temporary
runtime files may change; they are observed separately, never claimed no-write.
"""
import hashlib
from pathlib import Path
import re

CATEGORIES = ('products', 'mutable_metadata', 'mutable_outputs', 'temporary_runtime')
POLICY = {
    'products': 'Pass frame-count sidecars remain hash-protected and actual-frame-probed; all cache files except the explicit mutable/runtime classes; SHA256, size and mtime_ns compared.',
    'mutable_metadata': 'Locks, completion markers, cache manifest, loudness receipts, generated ASS, segment concat lists/graph markers, cache reports, out receipts.',
    'mutable_outputs': 'cache/qc_clean.mp4 and out files other than receipts/locks/completion markers; independently validated by HTML runner.',
    'temporary_runtime': 'cache/tmp and cache/tmp_run; observed, excluded from content-addressed comparisons.',
}


def category(relative):
    p = Path(relative)
    parts = p.parts
    if len(parts) >= 2 and parts[0] == 'cache' and parts[1] in ('tmp', 'tmp_run'):
        return 'temporary_runtime'
    if (p.name.endswith(('.lock', '.complete.json')) or p.name == 'complete.json'
            or relative in ('cache/manifest.json', 'cache/subs.ass', 'cache/audio/loudness_receipts.json')
            or (len(parts) >= 3 and parts[:2] == ('cache', 'reports'))
            or (len(parts) == 3 and parts[1] in ('vseg', 'vseg_qc', 'vseg_draft')
                and (re.fullmatch(r'subs_\d+\.ass', p.name) or p.name in ('list.txt', '.graph_digest')))
            or (parts[0] == 'out' and p.name in ('receipt.json', 'receipt.prev.json'))):
        return 'mutable_metadata'
    if parts[0] == 'out' or relative == 'cache/qc_clean.mp4':
        return 'mutable_outputs'
    return 'products'


def observe(root):
    """Record every file in cache/out, with no media-extension allowlist."""
    result = {name: {} for name in CATEGORIES}
    for folder in ('cache', 'out'):
        for path in sorted((root / folder).rglob('*')):
            if path.is_symlink():
                raise AssertionError(f'unexpected symlink in acceptance outputs: {path}')
            if not path.is_file():
                continue
            name = path.relative_to(root).as_posix()
            stat = path.stat()
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            result[category(name)][name] = dict(size=stat.st_size, mtime_ns=stat.st_mtime_ns,
                                              sha256=digest.hexdigest())
    return result


def is_pass_media(name):
    return re.fullmatch(r'cache/vseg_pass/p\d+_[0-9a-f]{12}\.mkv', name) is not None


def is_pass_sidecar(name):
    return name.endswith('.n') and is_pass_media(name[:-2])


def validate_sidecars(root, products, probe, verified):
    """Hash-bound actual decoded frame counts; unchanged media can reuse a probe.

    Sidecars remain protected products: delta rejects old changes/removals and
    permits new ones only alongside a predicted new pass media product.
    """
    checks = {}
    for name in sorted(products):
        if not is_pass_sidecar(name):
            continue
        media = name[:-2]
        if media not in products:
            raise AssertionError(f'orphan frame-count sidecar: {name}')
        declared = int((root / name).read_text())
        if declared <= 0:
            raise AssertionError(f'invalid frame-count sidecar: {name}')
        key = (media, products[media]['sha256'])
        if key not in verified:
            verified[key] = int(probe(root / media))
        actual = verified[key]
        if actual != declared:
            raise AssertionError(f'{name}: declared {declared}, actual decoded frames {actual}')
        checks[name] = dict(media=media, sha256=products[media]['sha256'], declared=declared,
                            actual_decoded_frames=actual)
    return checks


def artifact_paths(products):
    return {Path(p).parent.as_posix() if p.startswith('cache/gfx/')
            else p[:-2] if is_pass_sidecar(p) else p for p in products}


def delta(before, after, predicted, gfx_frames=None):
    """Check both graph paths and their exact expansion into raw frame paths."""
    gfx_frames = gfx_frames or {}
    expected_raw = set()
    for name in predicted:
        if name.startswith('cache/gfx/'):
            if name not in gfx_frames:
                raise AssertionError(f'no declared frame count for predicted gfx directory: {name}')
            expected_raw.update(f'{name}/f{i:04d}.png' for i in range(gfx_frames[name]))
        else:
            expected_raw.add(name)
            if is_pass_media(name):
                expected_raw.add(name + '.n')
    raw_added = set(after) - set(before)
    removed = sorted(set(before) - set(after))
    rewritten = sorted(p for p in before.keys() & after.keys() if before[p] != after[p])
    added = sorted(artifact_paths(after) - artifact_paths(before))
    return dict(passed=(raw_added == expected_raw and added == sorted(predicted)
                        and not removed and not rewritten),
                actual_new=added, actual_raw_added=sorted(raw_added),
                expected_raw_added=sorted(expected_raw),
                unexpected_raw_added=sorted(raw_added - expected_raw),
                missing_raw_added=sorted(expected_raw - raw_added),
                removed=removed, rewritten=rewritten)
