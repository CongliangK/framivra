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
import tempfile


class ReleaseError(RuntimeError):
    pass


TAG_PATTERN = re.compile(r'v[0-9]+\.[0-9]+\.[0-9]+')


def tag_argument(value):
    if not TAG_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError('version must have form vX.Y.Z')
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
        raise ReleaseError('version must have form vX.Y.Z')
    if git(root, 'status', '--porcelain'):
        raise ReleaseError('working tree is not clean')
    commit = git(root, 'rev-parse', '--verify', 'refs/tags/' + tag + '^{commit}')
    if git(root, 'rev-parse', 'HEAD') != commit:
        raise ReleaseError('HEAD must equal the tagged archive commit')
    origin = repository_from_remote(git(root, 'remote', 'get-url', 'origin'))
    repository = repository or origin
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repository) or repository != origin:
        raise ReleaseError('release repository must match origin')
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
    if release.get('prerelease') or release.get('tag_name') != expected['tag']:
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


def publish(root, tag, repository=None, *, dry_run=False):
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
        expected = {'tag': tag, 'names': {p.name for p in paths}}
        release = find_release(repository, tag, allow_missing=True)
        if release is None:
            gh(repository, 'release', 'create', tag, '--repo', repository,
               '--draft', '--verify-tag', '--target', commit,
               '--title', tag, '--notes', 'Framivra ' + tag)
            release = find_release(repository, tag)
        existing = inspect_assets(release, expected, complete=not release.get('draft'))
        verify_downloads(repository, tag, [p for p in paths if p.name in existing], work)
        if not release.get('draft'):
            # Immutable published releases: never re-upload or move latest on retry.
            print('✓ already published and verified: ' + tag)
            return
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
        # Single GitHub publish operation after all four assets are complete.
        # A network timeout may leave the publication outcome unknown; never
        # delete assets or roll back a possibly published release on error.
        release_id = ready.get('id')
        if type(release_id) is not int or release_id <= 0:
            raise ReleaseError('GitHub release id is invalid')
        gh(repository, 'api', 'repos/' + repository + '/releases/' + str(release_id),
           '--method', 'PATCH', '-F', 'draft=false', '-f', 'make_latest=true')
        final = find_release(repository, tag)
        if final.get('draft'):
            raise ReleaseError('publication not confirmed; inspect GitHub before retry')
        inspect_assets(final, expected, complete=True)
        latest = api(repository, 'releases/latest')
        if latest.get('id') != final.get('id'):
            raise ReleaseError('latest not confirmed; complete release remains available by tag')
        print('✓ published complete verified release: %s @ %s → %s' % (tag, commit, repository))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('version', type=tag_argument,
                        help='existing pushed vX.Y.Z tag; HEAD must match')
    parser.add_argument('--repo', help='GitHub OWNER/REPO; must match origin')
    parser.add_argument('--dry-run', action='store_true', help='local checks only, zero network/write')
    args = parser.parse_args(argv)
    try:
        publish(Path(__file__).resolve().parents[1], args.version, args.repo, dry_run=args.dry_run)
    except (ReleaseError, OSError, subprocess.SubprocessError) as error:
        print('✗ release refused: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
