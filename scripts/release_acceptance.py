#!/usr/bin/env python3
"""Run the release freeze against an isolated, exact tagged source checkout.

Writes only a NEW output directory. No publish, download, dependency install, or
claim of macOS/Windows/CEF/human visual acceptance. Failed runs retain raw logs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import release_github as release


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tag', type=release.tag_argument)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--candidate', action='store_true', help='test clean HEAD before creating the public tag; uses a private local tag only')
    parser.add_argument('--chrome', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.candidate:
        if release.git(ROOT, 'status', '--porcelain'):
            parser.error('candidate HEAD must be clean')
        commit = release.git(ROOT, 'rev-parse', 'HEAD')
        if (ROOT / 'VERSION').read_text().strip() != args.tag[1:]:
            parser.error('candidate tag must match VERSION')
    else:
        _, commit = release.check_source(ROOT, args.tag)
    out = args.output.resolve()
    if ROOT == out or ROOT in out.parents:
        parser.error('output must be outside the source repository')
    out.mkdir(mode=0o700, parents=False, exist_ok=False)
    home = out / 'home'; home.mkdir()
    env = {k: os.environ[k] for k in ('PATH', 'SYSTEMROOT') if k in os.environ}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'), LC_ALL='C.UTF-8',
               PYTHONUTF8='1', VTX_NO_NVENC='1', VTX_SEG_NVENC='0', VTX_PIPELINE='0',
               VTX_FILTER_THREADS='1', VTX_GFX='cdp', VTX_CHROME=str(args.chrome.resolve()),
               VTX_VIDEO_SEGMENTS='2')
    source = out / 'installed'
    steps = []; gates = {}
    def command(name, argv, *, expected=0, cwd=None, extra=None):
        child = dict(env); child.update(extra or {})
        result = subprocess.run([str(x) for x in argv], cwd=cwd or source, env=child,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        log = out / (name + '.log'); log.write_bytes(result.stdout)
        row = {'argv': [str(x) for x in argv], 'exit_code': result.returncode,
               'expected_exit': expected, 'log': log.name, 'sha256': release.digest(log)}
        steps.append(row)
        write(out / 'steps.json', {'commit': commit, 'tag': args.tag, 'environment': env, 'steps': steps})
        if (result.returncode == 0 if expected == 'nonzero' else result.returncode != expected):
            raise RuntimeError('release acceptance failed: ' + name + '; see ' + str(log))
        return row
    def gate(name, rows):
        path = out / (name + '.json')
        write(path, {'schema': 'framivra.freeze-gate/1', 'commit': commit, 'tag': args.tag,
                     'gate': name, 'status': 'passed', 'steps': rows})
        gates[name] = {'status': 'passed', 'path': path.name, 'sha256': release.digest(path)}
    upstream = ROOT
    if args.candidate:
        upstream = out / 'candidate-repository'
        command('candidate-clone', ['git', 'clone', '--no-hardlinks', str(ROOT), upstream], cwd=out)
        command('candidate-tag', ['git', '-c', 'user.name=Framivra acceptance', '-c', 'user.email=acceptance@example.invalid',
                'tag', '-a', args.tag, commit, '-m', 'Private candidate acceptance; never pushed'], cwd=upstream)
    # Exercise the real standalone bootstrap on a tag, never a moving main checkout.
    gate('isolated_install', [command('bootstrap', ['bash', ROOT / 'scripts/install.sh', '--', '--json'],
         cwd=out, extra={'FRAMIVRA_REPO': str(upstream), 'FRAMIVRA_DIR': str(source), 'FRAMIVRA_REF': args.tag})])
    project = out / 'standard-demo'
    command('demo-prepare', [sys.executable, 'scripts/demo.py', 'prepare', '--profile', 'standard', '--output', project])
    command('demo-input-verify', [sys.executable, 'scripts/demo.py', 'verify', '--project', project, '--pixels'])
    timeline = project / 'timeline.json'
    prefix = [sys.executable, '-m', 'vtx', '-t', timeline]
    gate('public_demo_render', [command('demo-all', prefix + ['all'])])
    gate('public_demo_qc', [command('demo-qc', prefix + ['qc', '--strict'])])
    original = timeline.read_bytes()
    def cache_snapshot():
        return {str(p.relative_to(project)): {'sha256': release.digest(p), 'mtime_ns': p.stat().st_mtime_ns}
                for p in (project / 'cache').rglob('*') if p.is_file() and p.suffix in ('.png', '.mp4', '.wav')}
    baseline_cache = cache_snapshot()
    html = project / 'gfx/canvas.html'; html_original = html.read_bytes()
    html.write_text(html.read_text().replace('rgba(255,255,255,0.15)', 'rgba(80,180,255,0.25)'))
    edit_preview = command('edit-preview', prefix + ['video', '--draft', '--range', '30f', '90f'])
    modified = json.loads(original); modified['subtitles'][0]['text'] = 'Stable source local edit / 局部修改'
    timeline.write_text(json.dumps(modified, ensure_ascii=False, indent=2) + '\n')
    edit_rows = [edit_preview, command('edit-all', prefix + ['all']), command('edit-qc', prefix + ['qc', '--strict'])]
    edit_cache = cache_snapshot()
    reused = [p for p in baseline_cache if p in edit_cache and baseline_cache[p] == edit_cache[p]]
    created = sorted(set(edit_cache) - set(baseline_cache))
    if not reused or not created:
        raise RuntimeError('HTML edit lacks both unrelated cache reuse and invalidated output evidence')
    write(out / 'cache-edit-observation.json', {'reused': reused, 'created': created, 'before': baseline_cache, 'after': edit_cache})
    gate('local_edit', edit_rows)
    html.write_bytes(html_original)
    invalid = json.loads(original); invalid['sources'][0]['path'] = 'missing-source-for-release-negative.mp4'
    timeline.write_text(json.dumps(invalid) + '\n')
    failed = command('expected-invalid-input', prefix + ['all'], expected='nonzero')
    timeline.write_bytes(original)
    cache_media = sorted((project / 'cache/audio').glob('*.wav'))
    if not cache_media:
        raise RuntimeError('no real audio cache available for recovery probe')
    damaged = cache_media[0]; old_hash = release.digest(damaged)
    damaged.write_bytes(b'INTERRUPTED RELEASE ACCEPTANCE CACHE')
    recovered = command('recovery-all', prefix + ['all'])
    if release.digest(damaged) != old_hash:
        raise RuntimeError('damaged cache was not rebuilt to original deterministic media')
    write(out / 'cache-recovery-observation.json', {'path': str(damaged.relative_to(project)), 'before_sha256': old_hash,
         'after_sha256': release.digest(damaged), 'injected_bytes': 'INTERRUPTED RELEASE ACCEPTANCE CACHE'})
    gate('failure_recovery', [failed, recovered, command('recovery-qc', prefix + ['qc', '--strict'])])
    package = out / 'acceptance'
    package_input = out / 'package-input'
    shutil.copytree(project, package_input, ignore=shutil.ignore_patterns('out', 'cache'))
    package_timeline = package_input / 'timeline.json'
    declared = json.loads(package_timeline.read_text()); declared['project']['cache_dependencies'] = ['fonts/NotoSansSC-VF.ttf']
    package_timeline.write_text(json.dumps(declared, ensure_ascii=False, indent=2) + '\n')
    command('package-build', [sys.executable, '-m', 'vtx.acceptance', 'build', package_timeline, package, '--chrome', args.chrome], expected=2)
    manifest = release.digest(package / 'manifest.json')
    gate('independent_package', [command('package-verify', [sys.executable, '-m', 'vtx.acceptance', 'verify', package,
          '--manifest-sha256', manifest, '--source-root', package_input], expected=2)])
    retained = package / 'artifacts/final.mp4'; retained_bytes = retained.read_bytes()
    retained.write_bytes(retained_bytes + b'tamper')
    command('package-tamper-rejected', [sys.executable, '-m', 'vtx.acceptance', 'verify', package,
            '--manifest-sha256', manifest], expected=1)
    retained.write_bytes(retained_bytes)
    source_input = package_input / 'a.mp4'; source_bytes = source_input.read_bytes()
    source_input.write_bytes(source_bytes + b'tamper')
    command('input-tamper-rejected', [sys.executable, '-m', 'vtx.acceptance', 'verify', package,
            '--manifest-sha256', manifest, '--source-root', package_input], expected=1)
    source_input.write_bytes(source_bytes)
    assets = out / 'assets'; assets.mkdir()
    paths = release.build_assets(source, args.tag, commit, assets)
    # Inspect a prefixed copy because the distribution checker requires one root.
    archive = out / 'license-source.tar.gz'
    command('license-archive', ['git', 'archive', '--format=tar.gz', '--prefix=framivra/', commit, '-o', archive])
    audit = command('license-audit', [sys.executable, '-m', 'scripts.distribution_license_audit', '--kind', 'git-source', archive])
    license_report = json.loads((out / audit['log']).read_text())
    if license_report.get('release_blockers') or license_report.get('integrity_checks_passed') is not True:
        raise RuntimeError('distribution license gate blocked; inspect license-audit.log')
    gate('distribution_license', [audit])
    evidence = out / 'freeze.json'
    write(evidence, {'schema': 'framivra.freeze/1', 'commit': commit, 'tag': args.tag,
        'assets': {p.name: release.digest(p) for p in paths}, 'gates': gates,
        'acceptance': {'path': package.name, 'manifest_sha256': manifest}})
    release.verify_freeze_evidence(evidence, commit, args.tag, paths)
    print('Freeze evidence: %s\nSHA256: %s' % (evidence, release.digest(evidence)))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('Release acceptance refused: ' + str(error), file=sys.stderr)
        sys.exit(1)
