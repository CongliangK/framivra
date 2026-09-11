"""Content-addressed project cache and stage provenance (no mtime shortcuts)."""
import hashlib
import json
from pathlib import Path


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def project_fingerprint(root, timeline):
    """Hash declared inputs and implementation; HTML assets must be declared.

    Each input is streamed once per CLI invocation. Missing paths are recorded so
    validate can retain its normal diagnostic. No previous result is trusted on
    the basis of size, timestamp or frame count alone.
    """
    root = Path(root)
    paths = {root / s['path'] for s in timeline['sources']}
    paths.update(root / cl['src'] for tr in timeline['video_tracks']
                 if tr.get('kind') == 'gfx' for cl in tr['clips'])
    dependencies = timeline['project'].get('cache_dependencies', [])
    if not isinstance(dependencies, list) or not all(isinstance(p, str) for p in dependencies):
        raise ValueError('project.cache_dependencies must be a list of file paths')
    for item in dependencies:
        path = root / item
        if not path.is_file():
            raise ValueError(f'cache dependency is not a file: {item}')
        paths.add(path)
    files = {str(p.resolve()): file_digest(p) if p.is_file() else 'MISSING'
             for p in sorted(paths, key=str)}
    implementation = {p.name: file_digest(p) for p in Path(__file__).parent.glob('*.py')}
    record = {'timeline': timeline, 'inputs': files, 'implementation': implementation}
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# Mark downstream results stale BEFORE running a producer, including failed runs.
DOWNSTREAM = {
    'prepare': ['audio', 'mux', 'mux_draft', 'qc'],
    'gfx': ['video', 'video_draft', 'mux', 'mux_draft', 'qc'],
    'video': ['mux', 'qc'], 'video_draft': ['mux_draft'],
    'audio': ['mux', 'mux_draft', 'qc'], 'mux': ['qc'],
}


def load_receipt(path, fingerprint):
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        record = {}
    if record.get('input_fingerprint') != fingerprint:
        record = {'input_fingerprint': fingerprint, 'stages': {}}
    return record
