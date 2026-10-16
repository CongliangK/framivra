#!/usr/bin/env python3
"""Inspect exact local archive bytes, without extraction, networking or legal clearance.

Guarantees archive member/hash inventory and known payload/notice checks; does not
prove upstream permissions, unknown renamed payloads or transitive license rights.
"""
import argparse
import hashlib
import json
import posixpath
from pathlib import Path, PurePosixPath
import tarfile
import zipfile

from scripts import model_license_policy as policy

BANNED = {'.pth', '.pt', '.ckpt', '.safetensors', '.onnx', '.gguf', '.bin', '.exe', '.dll', '.so', '.dylib', '.whl'}


def inspect_archive(path, kind, inventory=None):
    data = policy.load_inventory() if inventory is None else inventory
    path = Path(path)
    members = {}
    def record(name, content, member_type="file"):
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name or name in members:
            raise policy.LicensePolicyError('unsafe/duplicate archive member: ' + name)
        members[name] = {'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content), 'type': member_type}
    if kind == 'wheel':
        with zipfile.ZipFile(path) as archive:
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise policy.LicensePolicyError('archive symlink: ' + entry.filename)
                record(entry.filename, archive.read(entry))
    else:
        with tarfile.open(path) as archive:
            entries = archive.getmembers()
            # Source tarballs have one enclosing directory; callers must supply
            # that form (git archive --prefix or normal setuptools sdist).
            roots = {PurePosixPath(e.name).parts[0] for e in entries if e.name}
            if len(roots) != 1:
                raise policy.LicensePolicyError('source archive must have one enclosing directory')
            for entry in entries:
                if entry.isdir():
                    continue
                if not (entry.isfile() or entry.issym()):
                    raise policy.LicensePolicyError('unsupported archive member: ' + entry.name)
                parts = PurePosixPath(entry.name).parts
                if len(parts) < 2 or entry.name.startswith('/') or '..' in parts:
                    raise policy.LicensePolicyError('unsafe archive member: ' + entry.name)
                name = '/'.join(parts[1:])
                if entry.issym():
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(name), entry.linkname))
                    if entry.linkname.startswith('/') or target == '..' or target.startswith('../') or '\\' in entry.linkname:
                        raise policy.LicensePolicyError('escaping archive symlink: ' + name)
                    record(name, entry.linkname.encode('utf-8'), 'symlink')
                    members[name]['symlink_target'] = entry.linkname
                else:
                    record(name, archive.extractfile(entry).read())
    if not members:
        raise policy.LicensePolicyError('empty archive')
    registered_files = {name for asset in data['assets'].values() for name in asset.get('files_sha256', {})}
    for name in members:
        suffix = PurePosixPath(name).suffix.lower()
        if suffix in policy.MEDIA_FONT_SUFFIXES and (kind == 'wheel' or name not in registered_files):
            raise policy.LicensePolicyError('unreviewed media/font member: ' + name)
        if suffix in BANNED:
            raise policy.LicensePolicyError('unreviewed model/binary member: ' + name)
        if kind == 'wheel' and not (name == 'vt.py' or name.startswith('vtx/') or
                                    (name.split('/')[0].startswith('framivra-') and name.split('/')[0].endswith('.dist-info'))):
            raise policy.LicensePolicyError('non-kernel wheel member: ' + name)
    root_license = data['evidence']['framivra']['sha256']
    if kind == 'wheel':
        notices = [v for k,v in members.items() if k.endswith('.dist-info/licenses/LICENSE') or k.endswith('.dist-info/LICENSE')]
        if not notices or any(v['type'] != 'file' or v['sha256'] != root_license for v in notices):
            raise policy.LicensePolicyError('missing/changed wheel root license')
    elif (members.get('LICENSE', {}).get('type') != 'file' or
          members.get('LICENSE', {}).get('sha256') != root_license):
        raise policy.LicensePolicyError('missing/changed source root license')
    found, blockers = [], {}
    for ident, asset in data['assets'].items():
        lock = asset.get('version_home')
        if lock and asset.get('lock_sha256'):
            if kind == 'git-source' and lock not in members:
                raise policy.LicensePolicyError('missing external dependency lock: ' + ident)
            if lock in members and (members[lock]['type'] != 'file' or
                                    members[lock]['sha256'] != asset['lock_sha256']):
                raise policy.LicensePolicyError('changed external dependency lock: ' + ident)
        files = asset.get('files_sha256', {})
        present = set(files) & members.keys()
        if kind == 'git-source' and asset['default_payload'] and files and present != set(files):
            raise policy.LicensePolicyError('missing inventoried source payload: ' + ident)
        if present:
            found.append(ident)
            if asset.get('kind') == 'vendored-code' and present != set(files):
                raise policy.LicensePolicyError('incomplete vendored source/notices: ' + ident)
            for name in present:
                if members[name]['type'] != 'file' or members[name]['sha256'] != files[name]:
                    raise policy.LicensePolicyError('changed inventoried archive member: ' + name)
            if asset.get('distribution_blocker'):
                blockers[ident] = asset['distribution_blocker']
    return {'artifact': path.name, 'artifact_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'kind': kind, 'integrity_checks_passed': True, 'legal_clearance': False,
            'scope': 'Exact local archive only; no published release, renamed binary, upstream rights or transitive dependency clearance.',
            'inventoried_assets_present': found, 'release_blockers': blockers,
            'provenance_warnings': [w for w in data.get('provenance_warnings', []) if w['path'] in members],
            'members': members}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', required=True, choices=('git-source', 'sdist', 'wheel'))
    parser.add_argument('archive', type=Path)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(inspect_archive(args.archive, args.kind), ensure_ascii=False, indent=2))
    except (policy.LicensePolicyError, OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as error:
        parser.exit(4, str(error) + '\n')


if __name__ == '__main__':
    main()
