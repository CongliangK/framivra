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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.release import release_github as release


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
        row = {'role': name, 'source_commit': commit, 'argv': [str(x) for x in argv], 'exit_code': result.returncode,
               'expected_exit': expected, 'log': log.name, 'sha256': release.digest(log)}
        steps.append(row)
        write(out / 'steps.json', {'commit': commit, 'tag': args.tag, 'environment': env, 'steps': steps})
        if result.returncode != expected:
            raise RuntimeError('release acceptance failed: ' + name + '; see ' + str(log))
        return row
    def gate(name, rows, observations=None):
        path = out / (name + '.json')
        write(path, {'schema': 'framivra.freeze-gate/2', 'commit': commit, 'tag': args.tag,
                     'gate': name, 'status': 'passed', 'steps': rows,
                     'observations': {key: {'path': value.name, 'sha256': release.digest(value)} for key, value in (observations or {}).items()}})
        gates[name] = {'status': 'passed', 'path': path.name, 'sha256': release.digest(path)}
    upstream = ROOT
    if args.candidate:
        upstream = out / 'candidate-repository'
        command('candidate-clone', ['git', 'clone', '--no-hardlinks', str(ROOT), upstream], cwd=out)
        command('candidate-tag', ['git', '-c', 'user.name=Framivra acceptance', '-c', 'user.email=acceptance@example.invalid',
                'tag', '-a', args.tag, commit, '-m', 'Private candidate acceptance; never pushed'], cwd=upstream)
    # Exercise the real standalone bootstrap on a tag, never a moving main checkout.
    installed = command('bootstrap', ['bash', ROOT / 'scripts/install.sh', '--', '--json'],
         cwd=out, extra={'FRAMIVRA_REPO': str(upstream), 'FRAMIVRA_DIR': str(source), 'FRAMIVRA_REF': args.tag})
    mount = home / '.agents/skills/video'
    mounted = command('isolated-mount', [sys.executable, 'vt.py', '--fix-links'], extra={'VT_MOUNT_ROOT': str(mount)})
    mount_script = ('import json,sys; from pathlib import Path; m=Path(sys.argv[1]); r=Path(sys.argv[2]); '
       'expected={"SKILL.md":"docs/skill/SKILL.md","assets":"docs/skill/assets","references":"docs/skill/references",'
       '"vtx":"vtx","scripts":"scripts","templates":"templates"}; '
       'rows={n:{"target":str((m/n).resolve()),"ok":(m/n).is_symlink() and (m/n).resolve()==(r/p).resolve()} for n,p in expected.items()}; '
       'print(json.dumps({"links":rows,"bad":sum(not x["ok"] for x in rows.values())})); '
       'sys.exit(0 if all(x["ok"] for x in rows.values()) else 1)')
    checked = command('isolated-mount-check', [sys.executable, '-c', mount_script, mount, source])
    gate('isolated_install', [installed, mounted, checked])
    promo = project = out / 'promo-demo'
    prepared = command('demo-prepare', [sys.executable, 'scripts/demo.py', 'prepare', '--output', project])
    input_verified = command('demo-input-verify', [sys.executable, 'scripts/demo.py', 'verify', '--project', project])
    timeline = project / 'timeline.json'
    prefix = [sys.executable, '-m', 'vtx', '-t', timeline]
    rendered = command('demo-all', prefix + ['all'])
    promo_qc = command('demo-qc', prefix + ['qc', '--strict'])
    media_probe = command('demo-media-probe', release.media_command(project / 'out/final.mp4'))
    decoded = command('demo-decode', release.media_command(project / 'out/final.mp4', decode=True))
    receipt = out / 'demo-receipt.json'
    shutil.copyfile(project / 'out/receipt.json', receipt)
    release.observed_promo(receipt, out / media_probe['log'])
    gate('public_demo_qc', [promo_qc, media_probe, decoded], {'demo_receipt': receipt})
    # Browser/subtitle/ordinary-media capabilities are not attributed to SVG.
    project = out / 'capability-probe'
    probe_prepared = command('capability-prepare', [sys.executable, 'tests/runners/_capability_fixture.py', '--output', project])
    timeline = project / 'timeline.json'
    prefix = [sys.executable, '-m', 'vtx', '-t', timeline]
    probe_rendered = command('capability-all', prefix + ['all'])
    release.observed_cdp(out / probe_rendered['log'])
    gate('public_demo_render', [prepared, input_verified, rendered, probe_prepared, probe_rendered])
    original = timeline.read_bytes()
    def cache_snapshot():
        return {str(p.relative_to(project)): {'sha256': release.digest(p), 'mtime_ns': p.stat().st_mtime_ns}
                for p in (project / 'cache').rglob('*') if p.is_file() and p.suffix in ('.png', '.mp4', '.wav')}
    baseline_cache = cache_snapshot()
    html = project / 'gfx/canvas.html'; html_original = html.read_bytes()
    html.write_text(html.read_text().replace('#2abfa0', '#a06cff'))
    edit_gfx = command('edit-gfx', prefix + ['gfx'])
    edit_preview = command('edit-preview', prefix + ['video', '--draft', '--range', '30f', '90f'])
    modified = json.loads(original); modified['subtitles'][0]['text'] = 'Stable source local edit / 局部修改'
    timeline.write_text(json.dumps(modified, ensure_ascii=False, indent=2) + '\n')
    edit_rows = [edit_gfx, edit_preview, command('edit-all', prefix + ['all']), command('edit-qc', prefix + ['qc', '--strict'])]
    edit_cache = cache_snapshot()
    reused = [p for p in baseline_cache if p in edit_cache and baseline_cache[p] == edit_cache[p]]
    created = sorted(set(edit_cache) - set(baseline_cache))
    if not reused or not created:
        raise RuntimeError('HTML edit lacks both unrelated cache reuse and invalidated output evidence')
    write(out / 'cache-edit-observation.json', {'schema': 'framivra.cache_edit/1', 'commit': commit, 'tag': args.tag, 'reused': reused, 'created': created, 'before': baseline_cache, 'after': edit_cache, 'html_before_sha256': hashlib.sha256(html_original).hexdigest(), 'html_after_sha256': release.digest(html)})
    release.observed_cache_edit(json.loads((out / 'cache-edit-observation.json').read_text()),
                                out / 'capability-all.log', out / 'edit-gfx.log')
    gate('local_edit', edit_rows, {'cache_edit': out / 'cache-edit-observation.json'})
    html.write_bytes(html_original)
    invalid = json.loads(original); invalid['sources'][0]['path'] = 'missing-source-for-release-negative.mp4'
    timeline.write_text(json.dumps(invalid) + '\n')
    failed = command('expected-invalid-input', prefix + ['all'], expected=1)
    timeline.write_bytes(original)
    cache_media = sorted((project / 'cache/audio').glob('*.wav'))
    if not cache_media:
        raise RuntimeError('no real audio cache available for recovery probe')
    damaged = cache_media[0]; old_hash = release.digest(damaged)
    damaged.write_bytes(b'INTERRUPTED RELEASE ACCEPTANCE CACHE')
    corrupted_hash = release.digest(damaged)
    recovered = command('recovery-all', prefix + ['all'])
    if release.digest(damaged) != old_hash:
        raise RuntimeError('damaged cache was not rebuilt to original deterministic media')
    write(out / 'cache-recovery-observation.json', {'schema': 'framivra.cache_recovery/1', 'commit': commit, 'tag': args.tag, 'path': str(damaged.relative_to(project)), 'before_sha256': old_hash, 'corrupted_sha256': corrupted_hash,
         'after_sha256': release.digest(damaged), 'injected_bytes': 'INTERRUPTED RELEASE ACCEPTANCE CACHE'})
    gate('failure_recovery', [failed, recovered, command('recovery-qc', prefix + ['qc', '--strict'])], {'cache_recovery': out / 'cache-recovery-observation.json'})
    packages = {}; package_rows = []
    # Each package is an isolated all -> strict-QC build, not packaging an old movie.
    for role, prefix, original_project, directory, changed_input in (
        ('capability_probe', '', project, 'acceptance', 'source-left.mp4'),
        ('public_demo', 'main-', promo, 'main-acceptance', 'gfx/frames-svg/f0000.svg')):
        package = out / directory
        package_input = out / (prefix + 'package-input')
        shutil.copytree(original_project, package_input, ignore=shutil.ignore_patterns('out', 'cache', '.generated'))
        package_timeline = package_input / 'timeline.json'
        if role == 'public_demo':
            # Declare the manifest's complete frame closure only in this private copy.
            declared = json.loads(package_timeline.read_text())
            frames = release.promo_frames(package_input)
            shutil.copyfile(source / 'LICENSE', package_input / 'LICENSE')
            notices = release.promo_notices(package_input)
            declared['project']['cache_dependencies'] = sorted(set(declared['project'].get('cache_dependencies', []) + frames + notices))
            write(package_timeline, declared)
        chrome = ['--chrome', args.chrome.resolve()] if role == 'capability_probe' else []
        package_rows.append(command(prefix + 'package-build', [sys.executable, '-m', 'vtx.inspection.acceptance',
            'build', package_timeline, package] + chrome, expected=2))
        manifest = release.digest(package / 'manifest.json')
        verify = [sys.executable, '-m', 'vtx.inspection.acceptance', 'verify', package, '--manifest-sha256', manifest]
        live_inputs = ['--source-root', package_input]
        package_rows.append(command(prefix + 'package-verify', verify + live_inputs, expected=2))
        for tamper_role, target, extra in (
            ('package-tamper-rejected', package / 'artifacts/final.mp4', []),
            ('input-tamper-rejected', package_input / changed_input, live_inputs)):
            original_bytes = target.read_bytes()
            target.write_bytes(original_bytes + b'tamper')
            package_rows.append(command(prefix + tamper_role, verify + extra, expected=1))
            target.write_bytes(original_bytes)
        if role == 'public_demo':
            package_rows.append(command('main-package-media-probe', release.media_command(package / 'artifacts/final.mp4')))
        packages[role] = {'path': package.name, 'manifest_sha256': manifest, 'source_root': package_input.name}
    gate('independent_package', package_rows)
    assets = out / 'assets'; assets.mkdir()
    paths = release.build_assets(source, args.tag, commit, assets)
    # Inspect a prefixed copy because the distribution checker requires one root.
    archive = out / 'license-source.tar.gz'
    license_archived = command('license-archive', ['git', 'archive', '--format=tar.gz', '--prefix=framivra/', commit, '-o', archive])
    audit = command('license-audit', [sys.executable, '-m', 'scripts.policy.distribution_license_audit', '--kind', 'git-source', archive])
    license_report = json.loads((out / audit['log']).read_text())
    if license_report.get('release_blockers') or license_report.get('integrity_checks_passed') is not True:
        raise RuntimeError('distribution license gate blocked; inspect license-audit.log')
    gate('distribution_license', [license_archived, audit])
    evidence = out / 'freeze.json'
    write(evidence, {'schema': 'framivra.freeze/2', 'commit': commit, 'tag': args.tag,
        'assets': {p.name: release.digest(p) for p in paths}, 'gates': gates,
        'acceptance': packages})
    release.verify_freeze_evidence(evidence, commit, args.tag, paths)
    summary = release.build_public_summary(evidence, assets)
    print('Public summary for review: ' + str(summary))
    print('Freeze evidence: %s\nSHA256: %s' % (evidence, release.digest(evidence)))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('Release acceptance refused: ' + str(error), file=sys.stderr)
        sys.exit(1)
