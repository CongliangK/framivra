#!/usr/bin/env python3
"""Publish a verified immutable GitHub draft; no server or global index writes.

Checksums prove transfer integrity, not independent publisher identity (#41).
Draft assets may be resumed only if their bytes match this exact tagged commit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ReleaseError(RuntimeError):
    pass


TAG_PATTERN = re.compile(r'v[0-9]+\.[0-9]+\.[0-9]+(?:-rc\.[1-9][0-9]*)?')


def tag_argument(value):
    if not TAG_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError('version must have form vX.Y.Z or vX.Y.Z-rc.N')
    return value


def run(args, *, cwd=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                            timeout=120)
    if result.returncode:
        raise ReleaseError('%s failed: %s' % (args[0], result.stderr.strip()))
    return result.stdout


def git(root, *args):
    return run(['git', *args], cwd=root).strip()


def repository_from_remote(remote):
    match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?/?', remote)
    if not match:
        raise ReleaseError('origin must identify a GitHub repository')
    return match[1]


def check_source(root, tag, repository=None):
    if not TAG_PATTERN.fullmatch(tag):
        raise ReleaseError('version must have form vX.Y.Z or vX.Y.Z-rc.N')
    if git(root, 'status', '--porcelain'):
        raise ReleaseError('working tree is not clean')
    commit = git(root, 'rev-parse', '--verify', 'refs/tags/' + tag + '^{commit}')
    if git(root, 'rev-parse', 'HEAD') != commit:
        raise ReleaseError('HEAD must equal the tagged archive commit')
    origin = repository_from_remote(git(root, 'remote', 'get-url', 'origin'))
    repository = repository or origin
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repository) or repository != origin:
        raise ReleaseError('release repository must match origin')
    if git(root, 'cat-file', '-t', 'refs/tags/' + tag) != 'tag':
        raise ReleaseError('release requires an annotated tag')
    version = tag[1:]
    raw = git(root, 'show', commit + ':VERSION')
    project = git(root, 'show', commit + ':pyproject.toml')
    package = git(root, 'show', commit + ':vtx/__init__.py')
    # Restrict the match to [project], never a different TOML section.
    section = re.search(r'(?ms)^\[project\]\s*\n(.*?)(?=^\[|\Z)', project)
    declared = re.findall(r'(?m)^version\s*=\s*"([^"\n]+)"\s*$', section[1]) if section else []
    fallback = re.findall(r'(?m)^_FALLBACK_VERSION\s*=\s*"([^"\n]+)"\s*$', package)
    if raw != version or declared != [version] or fallback != [version]:
        raise ReleaseError('tag and archived VERSION/pyproject/vtx versions disagree')
    # Require a frozen installer in the actual archive, not the invoking tree.
    git(root, 'cat-file', '-e', commit + ':scripts/install.sh')
    return repository, commit


def gh(repository, *args):
    args = list(args)
    if args[0] == 'api':
        args += ['--hostname', 'github.com']
    elif args[0] == 'release':
        # Do not let ambient GH_HOST/GH_REPO redirect an origin-verified publish.
        args[args.index('--repo') + 1] = 'github.com/' + repository
    return run(['gh', *args])


def api(repository, suffix):
    text = gh(repository, 'api', 'repos/' + repository + '/' + suffix)
    try:
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ReleaseError('GitHub returned an invalid response object')
        return value
    except ValueError as error:
        raise ReleaseError('GitHub returned invalid JSON') from error


def find_release(repository, tag, *, allow_missing=False):
    # REST /releases/tags/{tag} cannot find a draft's pending tag. This is the
    # same GraphQL-to-REST-id lookup used by gh release view/download.
    owner, name = repository.split('/')
    query = ('query($owner:String!,$name:String!,$tag:String!){'
             'repository(owner:$owner,name:$name){release(tagName:$tag){databaseId}}}')
    text = gh(repository, 'api', 'graphql', '-f', 'query=' + query,
              '-f', 'owner=' + owner, '-f', 'name=' + name, '-f', 'tag=' + tag)
    try:
        payload = json.loads(text)
        repo = payload['data']['repository']
        if payload.get('errors') or not isinstance(repo, dict):
            raise ReleaseError('GitHub repository lookup failed')
        release = repo.get('release')
        if release is None:
            if allow_missing:
                return None
            raise ReleaseError('expected release is missing')
        release_id = release.get('databaseId')
        if type(release_id) is not int or release_id <= 0:
            raise ReleaseError('GitHub release lookup id is invalid')
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise ReleaseError('GitHub returned invalid release lookup data') from error
    return api(repository, 'releases/' + str(release_id))


def verify_archive_payload(root, commit, payload):
    """Prove the produced archive equals the tagged tree, entry by entry.

    `git archive` also applies .git/info/attributes, core.attributesFile and
    system gitattributes (untracked by `status --porcelain`), so export-ignore
    can silently drop any tracked file and export-subst can rewrite its bytes
    while every Git-object read still matches (#38/#39). Compare the real tar
    payload against `ls-tree` of the tagged commit instead of trusting the
    source gate plus an asset count.
    """
    expected = {}
    for entry in git(root, 'ls-tree', '-r', '-z', commit).split('\0'):
        if not entry:
            continue
        meta, path = entry.split('\t', 1)
        mode, kind, sha = meta.split(' ', 2)
        if kind != 'blob':
            # Never fake submodule contents; fail closed on unsupported trees.
            raise ReleaseError('cannot verify archive entry: ' + path)
        blob = subprocess.run(['git', 'cat-file', 'blob', sha], cwd=root,
                              capture_output=True, check=True).stdout
        expected[path] = (mode == '100755', mode == '120000', blob)
    with tarfile.open(payload, 'r:gz') as archive:
        actual = {}
        for member in archive:
            if member.isdir():
                continue  # Directory entries carry no payload to verify.
            if member.name in actual:
                raise ReleaseError('duplicate archive entry: ' + member.name)
            if member.issym():
                actual[member.name] = (False, True, member.linkname.encode())
            elif member.isfile():
                data = archive.extractfile(member).read()
                actual[member.name] = (bool(member.mode & 0o111), False, data)
            else:
                raise ReleaseError('unsupported archive entry type: ' + member.name)
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise ReleaseError('archive payload differs from tagged tree '
                           '(missing=%s extra=%s)' % (missing[:5], extra[:5]))
    for path, (exec_bit, symlink, blob) in expected.items():
        if actual[path] != (exec_bit, symlink, blob):
            raise ReleaseError('archive payload differs from tagged tree: ' + path)


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def build_assets(root, tag, commit, work):
    version = tag[1:]
    payload = work / ('video-tools-' + version + '.tar.gz')
    run(['git', 'archive', '--format=tar.gz', commit, '-o', str(payload)], cwd=root)
    # Prove the real payload equals the tagged tree before any remote write.
    verify_archive_payload(root, commit, payload)
    # git show bytes: do not strip the installer or change its frozen content.
    installer = subprocess.run(['git', 'show', commit + ':scripts/install.sh'],
                               cwd=root, capture_output=True, check=True).stdout
    (work / 'install.sh').write_bytes(installer)
    metadata = {'latest': version, 'releases': [
        {'version': version, 'commit': commit,
         'tar': tag + '/' + payload.name, 'install_sh': tag + '/install.sh'}]}
    (work / 'releases.json').write_text(json.dumps(metadata, sort_keys=True, indent=2) + '\n')
    files = [payload, work / 'install.sh', work / 'releases.json']
    (work / 'SHA256SUMS').write_text(''.join(digest(p) + '  ' + p.name + '\n' for p in files))
    return files + [work / 'SHA256SUMS']


def inspect_assets(release, expected, *, complete):
    if release.get('prerelease') is not ('-rc.' in expected['tag']) or release.get('tag_name') != expected['tag']:
        raise ReleaseError('release tag/prerelease identity is invalid')
    assets = release.get('assets')
    if not isinstance(assets, list):
        raise ReleaseError('release has no asset inventory')
    names = [entry.get('name') for entry in assets]
    if len(names) != len(set(names)) or not set(names) <= expected['names']:
        raise ReleaseError('unexpected or duplicate release assets')
    if complete and set(names) != expected['names']:
        raise ReleaseError('release asset set is incomplete')
    if any(entry.get('state') != 'uploaded' for entry in assets):
        raise ReleaseError('release contains an unfinished asset')
    return set(names)


def verify_downloads(repository, tag, paths, work):
    # gh authenticates draft downloads; no public latest endpoint is touched.
    with tempfile.TemporaryDirectory(prefix='verify-', dir=work) as folder:
        folder = Path(folder)
        for path in paths:
            gh(repository, 'release', 'download', tag, '--repo', repository,
               '--pattern', path.name, '--dir', str(folder))
            copy = folder / path.name
            if not copy.is_file() or digest(copy) != digest(path):
                raise ReleaseError('uploaded asset bytes differ: ' + path.name)


# Required freeze gates are policy, never inferred from whatever rows a producer emits.
REQUIRED_FREEZE_GATES = frozenset(('isolated_install', 'public_demo_render', 'public_demo_qc',
    'local_edit', 'failure_recovery', 'independent_package', 'distribution_license'))


def verify_freeze_evidence(path, commit, tag, assets):
    """Seal evidence bytes and strict required outcomes; human truth remains review.

    保 commit/tag/asset/evidence 字节绑定及 required 全通过；不保生产者诚实、
    未声明平台或人类视听质量。独立验收包由 vtx.acceptance.verify 复验。
    """
    if path is None:
        raise ReleaseError('freeze evidence is required before staging/publishing')
    from vtx.acceptance import read_json, relative, regular, verify
    path = regular(path)
    data = read_json(path)
    if set(data) != {'schema', 'commit', 'tag', 'assets', 'gates', 'acceptance'} or data['schema'] != 'framivra.freeze/1':
        raise ReleaseError('invalid freeze evidence schema')
    if data['commit'] != commit or data['tag'] != tag:
        raise ReleaseError('freeze evidence belongs to a different commit/tag')
    if data['assets'] != {p.name: digest(p) for p in assets}:
        raise ReleaseError('freeze evidence asset digest mismatch')
    gates = data['gates']
    if not isinstance(gates, dict) or set(gates) != REQUIRED_FREEZE_GATES:
        raise ReleaseError('freeze evidence required gates missing or unknown')
    for name, row in gates.items():
        if not isinstance(row, dict) or set(row) != {'status', 'path', 'sha256'} or row['status'] != 'passed':
            raise ReleaseError('required freeze gate did not pass: ' + name)
        evidence_file = regular(path.parent / relative(row['path']))
        if digest(evidence_file) != row['sha256']:
            raise ReleaseError('freeze gate evidence bytes changed: ' + name)
        report = read_json(evidence_file)
        if (set(report) != {'schema', 'commit', 'tag', 'gate', 'status', 'steps'} or
            report['schema'] != 'framivra.freeze-gate/1' or report['commit'] != commit or
            report['tag'] != tag or report['gate'] != name or report['status'] != 'passed' or
            not isinstance(report['steps'], list) or not report['steps']):
            raise ReleaseError('invalid freeze gate report: ' + name)
        for step in report['steps']:
            if (not isinstance(step, dict) or set(step) != {'argv', 'exit_code', 'expected_exit', 'log', 'sha256'} or
                not isinstance(step['argv'], list) or not step['argv'] or type(step['exit_code']) is not int):
                raise ReleaseError('invalid freeze command evidence: ' + name)
            expected = step['expected_exit']
            if expected == 'nonzero':
                if name != 'failure_recovery' or step['exit_code'] == 0:
                    raise ReleaseError('negative recovery probe did not fail')
            elif type(expected) is not int or expected not in (0, 2) or step['exit_code'] != expected or (expected == 2 and name != 'independent_package'):
                raise ReleaseError('required freeze command failed: ' + name)
            if digest(regular(path.parent / relative(step['log']))) != step['sha256']:
                raise ReleaseError('freeze raw log bytes changed: ' + name)
        if name == 'distribution_license':
            audit = read_json(path.parent / relative(report['steps'][-1]['log']))
            if audit.get('integrity_checks_passed') is not True or audit.get('release_blockers'):
                raise ReleaseError('distribution license blockers remain')
    package = data['acceptance']
    if not isinstance(package, dict) or set(package) != {'path', 'manifest_sha256'}:
        raise ReleaseError('independent acceptance package is required')
    package_root = path.parent / relative(package['path'])
    result = verify(package_root, package['manifest_sha256'])
    environment = read_json(package_root / 'environment.json')
    if environment.get('git', {}).get('status') != 'passed' or environment['git']['stdout'].strip() != commit:
        raise ReleaseError('acceptance package was built by a different commit')
    if environment.get('git_status', {}).get('status') != 'passed' or environment['git_status']['stdout'].strip():
        raise ReleaseError('acceptance package renderer working tree was not clean')
    # Existing acceptance explicitly leaves visual_content unknown. Required stage
    # checks remain strict; visual scope is disclosed by the stable support policy.
    for name in ('validate', 'prepare', 'gfx', 'video', 'audio', 'mux', 'qc', 'process',
                 'input_stability', 'tool_versions', 'media'):
        if result['gates'].get(name, {}).get('status') != 'passed':
            raise ReleaseError('required acceptance gate did not pass: ' + name)
    return data


def build_public_summary(evidence, work):
    """Only fixed public fields; never copy logs, paths, accounts or environment.

    The complete acceptance package remains local. This summary is not a public
    replay bundle and does not upgrade unknown visual/platform qualifications.
    """
    from vtx.acceptance import read_json, relative, regular
    evidence = regular(evidence)
    data = read_json(evidence)
    package = evidence.parent / relative(data['acceptance']['path'])
    env = read_json(package / 'environment.json')
    versions = {'python': env['python']}
    for tool in ('ffmpeg', 'ffprobe'):
        text = env['tools'][tool]['stdout']
        match = re.match(tool + r' version ([0-9][0-9A-Za-z._+~-]*)', text)
        if not match:
            raise ReleaseError('cannot form safe public tool version: ' + tool)
        versions[tool] = match[1]
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', versions['python']):
        raise ReleaseError('invalid public Python version')
    summary = {'schema': 'framivra.public-freeze-summary/1', 'version': data['tag'][1:],
       'tag': data['tag'], 'source_commit': data['commit'], 'core_asset_sha256': data['assets'],
       'gates': {name: data['gates'][name]['status'] for name in sorted(REQUIRED_FREEZE_GATES)},
       'tool_versions': versions, 'support_scope': ['Linux', 'CPU encoding', 'CDP', 'public standard-demo', 'kernel CLI'],
       'limitations': ['Full acceptance package and raw logs retained locally; summary is not a replay bundle',
           'Visual content remains unknown; no human audiovisual approval claimed',
           'macOS, Windows, CEF, Electron, GPU and optional model applications are unqualified',
           'License integrity is not comprehensive legal clearance; optional provenance warnings remain'],
       'local_freeze_sha256': digest(evidence), 'reproduction_guide': 'docs/releases.md'}
    path = work / 'public-freeze-summary.json'
    path.write_text(json.dumps(summary, sort_keys=True, indent=2) + '\n')
    return path


def publish(root, tag, repository=None, *, dry_run=False, phase='publish', evidence=None):
    root = Path(root).resolve()
    repository, commit = check_source(root, tag, repository)
    if dry_run:
        print('[dry-run] verified %s @ %s → %s; no build/upload/publish' % (tag, commit, repository))
        return
    remote = api(repository, 'commits/' + tag)
    if remote.get('sha') != commit:
        raise ReleaseError('remote tag commit differs from the archived commit')
    with tempfile.TemporaryDirectory(prefix='framivra-release-') as folder:
        work = Path(folder)
        paths = build_assets(root, tag, commit, work)
        verify_freeze_evidence(evidence, commit, tag, paths)
        summary = build_public_summary(evidence, work)
        if summary is not None:
            paths.append(summary)
        expected = {'tag': tag, 'names': {p.name for p in paths}}
        release = find_release(repository, tag, allow_missing=True)
        if release is None:
            if phase == 'verify':
                raise ReleaseError('verify requires an existing complete draft')
            gh(repository, 'release', 'create', tag, '--repo', repository,
               '--draft', '--verify-tag', '--target', commit,
               '--title', tag, '--notes', 'Framivra source baseline ' + tag,
               *(['--prerelease'] if '-rc.' in tag else []))
            release = find_release(repository, tag)
        existing = inspect_assets(release, expected, complete=not release.get('draft'))
        verify_downloads(repository, tag, [p for p in paths if p.name in existing], work)
        if not release.get('draft'):
            # Immutable published releases: never re-upload or move latest on retry.
            from verify_release import verify_artifact
            for path in paths:
                verify_artifact(path, expect_ref='refs/tags/' + tag, expect_digest=commit)
            print('✓ already published and verified: ' + tag)
            return
        if phase == 'verify' and existing != expected['names']:
            raise ReleaseError('verify requires a complete asset set')
        for path in paths:
            if path.name not in existing:
                gh(repository, 'release', 'upload', tag, str(path), '--repo', repository)
        ready = find_release(repository, tag)
        if not ready.get('draft'):
            raise ReleaseError('release became public before verification')
        inspect_assets(ready, expected, complete=True)
        verify_downloads(repository, tag, paths, work)
        if api(repository, 'commits/' + tag).get('sha') != commit:
            raise ReleaseError('remote tag moved during staging')
        if phase == 'prepare':
            print('✓ complete verified draft; sign at refs/tags/' + tag + ' before publish')
            return
        # Verification must precede the draft→public mutation; no unsigned fallback.
        from verify_release import verify_artifact
        for path in paths:
            verify_artifact(path, expect_ref='refs/tags/' + tag, expect_digest=commit)
        if phase == 'verify':
            print('✓ draft provenance and freeze evidence verified: ' + tag)
            return
        # Single GitHub publish operation after all four assets are complete.
        # A network timeout may leave the publication outcome unknown; never
        # delete assets or roll back a possibly published release on error.
        release_id = ready.get('id')
        if type(release_id) is not int or release_id <= 0:
            raise ReleaseError('GitHub release id is invalid')
        gh(repository, 'api', 'repos/' + repository + '/releases/' + str(release_id),
           '--method', 'PATCH', '-F', 'draft=false', '-f',
           'make_latest=' + ('false' if '-rc.' in tag else 'true'))
        final = find_release(repository, tag)
        if final.get('draft'):
            raise ReleaseError('publication not confirmed; inspect GitHub before retry')
        inspect_assets(final, expected, complete=True)
        latest = api(repository, 'releases/latest') if '-rc.' not in tag else final
        if latest.get('id') != final.get('id'):
            raise ReleaseError('latest not confirmed; complete release remains available by tag')
        print('✓ published complete verified release: %s @ %s → %s' % (tag, commit, repository))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('version', type=tag_argument,
                        help='existing pushed vX.Y.Z tag; HEAD must match')
    parser.add_argument('--phase', choices=('prepare', 'verify', 'publish'), default='publish',
                        help='prepare unsigned draft; sign workflow at tag; verify; publish')
    parser.add_argument('--evidence', type=Path, help='externally anchored freeze evidence JSON (required for remote operations)')
    parser.add_argument('--repo', help='GitHub OWNER/REPO; must match origin')
    parser.add_argument('--dry-run', action='store_true', help='local checks only, zero network/write')
    args = parser.parse_args(argv)
    try:
        publish(Path(__file__).resolve().parents[1], args.version, args.repo, dry_run=args.dry_run, phase=args.phase, evidence=args.evidence)
    except (ReleaseError, OSError, ValueError, subprocess.SubprocessError) as error:
        print('✗ release refused: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
