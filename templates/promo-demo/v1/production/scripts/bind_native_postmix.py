#!/usr/bin/env python3
"""Bind one explicitly selected native audio run into a new local draft project.

No rendering, audio processing, network access or acceptance is performed. Raw
execution proof stays in its original private directory. Python 3.9+, Node and
the same qualified FFmpeg/ffprobe used for the selected native run are required.
"""
import argparse
import copy
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

PREFIX = 'templates/promo-demo/v1/production/'
CORE_FILES = ('vtx/stages.py', 'vtx/infra.py', 'vtx/cache_state.py',
              'vtx/hygiene.py', 'vtx/constraints.py', 'vtx/pluginlib/hooks.py',
              'vtx/pluginlib/registry.py', 'vtx/cli.py')
DERIVED = {'audio/native-audio-provenance.json', 'audio/native-postmix.json',
           'audio/waveform-S5.json', 'src/assets/media-bindings.json',
           'runtime/shared.js', 'entry-manifest.json', 'runtime-dependencies.json'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path, limit=1024 * 1024):
    """Use the asset importer's existing no-follow, bounded regular-file reader."""
    with ASSETS.directory(path.parent) as fd:
        return ASSETS.read_file(fd, path.name, limit)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).absolute().parent
ASSETS = load_module('binding_assets', HERE / 'prepare_assets.py')
PREFLIGHT = load_module('binding_preflight', HERE / 'preflight.py')


def git(repo, *args):
    return subprocess.run(['git', *args], cwd=repo, capture_output=True,
                          check=True, timeout=30).stdout


def json_bytes(value):
    return (json.dumps(value, indent=2, allow_nan=False) + '\n').encode()


def verified_source(repo, project, commit):
    require(re.fullmatch('[0-9a-f]{40}', commit), 'Expected a full source commit')
    require(git(repo, 'rev-parse', 'HEAD').decode().strip() == commit,
            'Checkout differs from the explicit source commit')
    require(not git(repo, 'status', '--porcelain', '--untracked-files=no'),
            'Tracked checkout is not clean')
    records = git(repo, 'ls-tree', '-r', '-z', commit, '--', PREFIX).split(b'\0')
    result = {}
    for row in filter(None, records):
        metadata, name = row.decode().split('\t', 1)
        mode, kind, blob = metadata.split()
        require(kind == 'blob' and mode in ('100644', '100755') and name.startswith(PREFIX),
                'Only regular tracked production source is supported')
        relative = name[len(PREFIX):]
        require(relative and '..' not in Path(relative).parts, 'Unsafe source path')
        data = read(project / relative)
        actual = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        require(actual == blob, 'Project source differs from commit: ' + relative)
        require(bool((project / relative).stat().st_mode & 0o111) == (mode == '100755'),
                'Project source mode differs: ' + relative)
        result[relative] = (data, mode)
    require('timeline.json' in result and 'scripts/preflight.py' in result,
            'Incomplete production source')
    return result


def verify_run(repo, project, projection, run_commit, wav_hash, pcm_hash):
    for value in (wav_hash, pcm_hash):
        require(re.fullmatch('[0-9a-f]{64}', value), 'Explicit selected WAV and PCM hashes are required')
    require(re.fullmatch('[0-9a-f]{40}', run_commit), 'Expected a full native-run commit')
    proof_bytes = read(projection / 'projection-proof.json')
    proof = json.loads(proof_bytes)
    receipt_bytes = read(projection / 'out/receipt.json')
    receipt = json.loads(receipt_bytes)
    wav = projection / 'out/audio.wav'
    # Native WAV is bounded before decoding; this also rejects symlinks/special files.
    wav_bytes = read(wav, 5760000 * 4 + 1024 * 1024)
    require(digest(wav_bytes) == wav_hash == proof['postmix_sha256'],
            'Selected WAV hash differs from the native proof')
    require(digest(receipt_bytes) == proof['receipt_sha256'], 'Native receipt hash mismatch')
    require(proof['format'] == 'framivra-audio-projection-proof/1'
            and proof['status'] == 'NATIVE_AUDIO_ONLY_PROJECTION_COMPLETED'
            and proof['full_project_pass'] is False and proof['browser_or_gfx_called'] is False
            and proof['user_full_render_postmix_check_required'] is True,
            'Require a completed audio-only run, not full-film acceptance')
    require(proof['core_commit'] == run_commit, 'Native-run commit mismatch')
    require(set(proof['core_source_hashes']) == set(CORE_FILES), 'Incomplete native core identity')
    for name in CORE_FILES:
        actual = digest(read(repo / name))
        require(actual == proof['core_source_hashes'][name]
                == digest(git(repo, 'show', run_commit + ':' + name)),
                'Native core source differs: ' + name)
    require(proof['projection_runner_sha256'] == digest(read(project / 'scripts/run_native_audio_projection.py')),
            'Native projection runner differs from the source package')
    require(proof['full_timeline_sha256'] == digest(read(project / 'timeline.json')),
            'Native run belongs to another full timeline')
    full = json.loads(read(project / 'timeline.json'))
    expected = copy.deepcopy(full)
    expected['project'].pop('cache_dependencies', None)
    expected['sources'] = [s for s in full['sources'] if s['id'] in ASSETS.STEMS]
    expected['video_tracks'] = []
    expected['subtitles'] = []
    projection_bytes = read(projection / 'timeline.json')
    require(json.loads(projection_bytes) == expected, 'Projection changed audio-relevant inputs')
    require(digest(projection_bytes) == proof['projection_timeline_sha256'] == receipt['timeline'],
            'Projection timeline identity mismatch')
    relevant = {key: expected[key] for key in ('project', 'sources', 'audio_tracks')}
    signature = digest(json.dumps(relevant, sort_keys=True, separators=(',', ':')).encode())
    require(proof['audio_relevant_inputs'] == relevant and proof['audio_inputs_signature'] == signature,
            'Audio-input signature mismatch')
    stems = json.loads(read(project / 'audio/stems.json'))['stems']
    expected_stems = {s['id']: s['sha256'] for s in stems}
    require(set(expected_stems) == set(ASSETS.STEMS) and proof['source_sha256'] == expected_stems,
            'Native stem manifest mismatch')
    for stem in stems:
        require(stem['path'] == 'audio/' + stem['id'] + '.flac', 'Unsafe native stem path')
        require(digest(read(projection / stem['path'], stem['bytes'])) == stem['sha256'],
                'Native projection stem differs: ' + stem['id'])
    require(proof['active_plugins'] == [] and proof['graph_identical'] is True
            and proof['target_lufs'] == -14.0, 'Unexpected native mix configuration')
    require(digest(json.dumps(proof['graph'], sort_keys=True).encode()) == proof['graph_sha256'],
            'Recorded graph hash mismatch')
    # Reconstruct the same pure graph; Ctx is audio-only and never starts a renderer.
    sys.path.insert(0, str(repo))
    from vtx.infra import Ctx
    from vtx.stages import _audio_graph, TARGET_LUFS
    from vtx.pluginlib.hooks import active_plugins, active_plugins_digest
    from vtx.gfx_backend import selected
    from vtx.hygiene import gfx_cache_ns
    require(selected() == 'cdp' and gfx_cache_ns() == '', 'Use the original no-plugin CDP profile')
    context = Ctx(projection / 'timeline.json')
    whole = copy.copy(context)
    whole.tl = full
    require(active_plugins(context) == active_plugins(whole) == []
            and active_plugins_digest(context) == active_plugins_digest(whole) == '', 'Active plugins differ')
    graph = _audio_graph(context)
    require(graph == _audio_graph(whole) and json.loads(json.dumps(graph)) == proof['graph']
            and context.gfx_backend is None and TARGET_LUFS == proof['target_lufs'],
            'Native graph differs; keep the original run in its original location')
    require(receipt['input_fingerprint'] == context.input_fingerprint, 'Native receipt input identity mismatch')
    commands = proof['commands']
    require(len(commands) == 3, 'Incomplete native command sequence')
    for item, stage in zip(commands, ('validate', 'prepare', 'audio')):
        require(item['exit_code'] == 0 and item['argv'][1:] ==
                ['-m', 'vtx', '-t', str(projection / 'timeline.json'), stage]
                and receipt['stages'][stage]['ok'] is True, 'Native stage did not succeed: ' + stage)
    loudness = receipt['stages']['audio']['post_loudness']
    keys = ('input_i', 'output_i', 'gain_db', 'target', 'tp_dbfs')
    require(all(type(loudness[k]) in (int, float) and math.isfinite(loudness[k]) for k in keys)
            and loudness['sha256'] == wav_hash and loudness['target'] == -14.0,
            'Unbound or invalid native loudness receipt')
    for tool in ('ffmpeg', 'ffprobe'):
        executable = shutil.which(tool)
        require(executable is not None, tool + ' is required')
        # Tool executables may be ordinary platform symlinks; source/media may not.
        require(digest(Path(executable).resolve().read_bytes()) == proof['toolchain'][tool]['sha256'],
                'Use the same qualified ' + tool + ' as the native run')
        version_line = subprocess.run([executable, '-version'], capture_output=True, text=True,
                                      check=True, timeout=30).stdout.splitlines()[0]
        require(version_line == proof['toolchain'][tool]['version'], 'Recorded tool version mismatch: ' + tool)
    version = re.match(r'^ffmpeg version ([0-9]+\.[0-9]+\.[0-9]+)(?:[-\s]|$)',
                       proof['toolchain']['ffmpeg']['version'])
    require(version is not None, 'Unrecognized recorded FFmpeg version')
    pcm, actual_pcm_hash = PREFLIGHT.native_pcm(wav)
    require(actual_pcm_hash == pcm_hash, 'Selected decoded PCM hash mismatch')
    require(digest(read(wav, len(wav_bytes))) == wav_hash, 'Native WAV changed during verification')
    return proof, relevant, {k: loudness[k] for k in (*keys, 'sha256')}, pcm, version.group(1)


def binding_documents(project, proof, relevant, loudness, pcm, version):
    old = json.loads(read(project / 'audio/native-postmix.json'))
    portable = json.loads(read(project / 'audio/native-audio-provenance.json'))
    waveform = json.loads(read(project / 'audio/waveform-S5.json'))
    postmix = {k: v for k, v in old['postmix'].items() if k != 'mix_recipe_sha256'}
    postmix.update(sha256=proof['postmix_sha256'], pcm_sha256=digest(pcm))
    portable.update(core_commit=proof['core_commit'], core_source_hashes=proof['core_source_hashes'],
                    audio_relevant_inputs=relevant, audio_inputs_signature=proof['audio_inputs_signature'],
                    source_sha256=proof['source_sha256'], postmix_pcm_contract=postmix)
    portable['declared_toolchain']['ffmpeg_version'] = version
    portable['reference_execution_summary']['postmix_sha256'] = postmix['sha256']
    portable_bytes = json_bytes(portable)
    postmix = {**postmix, 'mix_recipe_sha256': digest(portable_bytes)}
    waveform['provenance'] = postmix
    waveform['samples'] = PREFLIGHT.pcm_waveform_samples(pcm)
    waveform_bytes = json_bytes(waveform)
    binding = copy.deepcopy(old)
    binding.update(postmix=postmix, waveform_sha256=digest(waveform_bytes),
                   core_commit=proof['core_commit'], post_loudness=loudness,
                   portable_provenance_sha256=digest(portable_bytes),
                   audio_inputs_signature=proof['audio_inputs_signature'], audio_relevant_inputs=relevant,
                   stems=proof['source_sha256'], user_audio_approved=False,
                   user_full_render_postmix_check_required=True)
    binding.pop('aac_192k_diagnostic', None)  # The old encode is not this native run.
    require(not PREFLIGHT.portable_provenance_errors(portable, binding, relevant), 'Invalid portable binding')
    require(not PREFLIGHT.waveform_errors(waveform, binding), 'Invalid computed waveform')
    return {'audio/native-audio-provenance.json': portable_bytes,
            'audio/native-postmix.json': json_bytes(binding), 'audio/waveform-S5.json': waveform_bytes}


def identity(info):
    return info.st_dev, info.st_ino


class OwnedOutput:
    """One new local output: pinned directories and exclusive no-follow writes.

    Do not modify this private directory concurrently. Before each operation,
    reject replaced entries; cleanup removes only entries still owned by us.
    This is file-integrity checking, not a sandbox for untrusted subprocesses.
    """
    def __init__(self, path):
        self.path = path
        self.stack = ExitStack()
        self.directories = {}
        self.files = {}

    def __enter__(self):
        try:
            self.parent = self.stack.enter_context(ASSETS.directory(self.path.parent))
            os.mkdir(self.path.name, mode=0o700, dir_fd=self.parent)
            root = self.stack.enter_context(ASSETS.directory(self.path.name, self.parent))
            self.directories[''] = (root, self.parent, self.path.name, os.fstat(root))
            return self
        except BaseException:
            self.stack.close()
            raise

    def check(self):
        # Reopen the visible ancestry as well as checking every pinned child.
        with ASSETS.directory(self.path.parent) as current:
            require(identity(os.fstat(current)) == identity(os.fstat(self.parent)),
                    'Output parent directory changed')
        for _, parent, name, original in self.directories.values():
            now = os.stat(name, dir_fd=parent, follow_symlinks=False)
            require(stat.S_ISDIR(now.st_mode) and identity(now) == identity(original),
                    'Owned output directory changed: ' + name)
        for relative, (parent, name, original) in self.files.items():
            now = os.stat(name, dir_fd=parent, follow_symlinks=False)
            require(stat.S_ISREG(now.st_mode) and now.st_nlink == 1
                    and identity(now) == identity(original),
                    'Owned output file changed: ' + relative)

    def folder(self, relative):
        require(not Path(relative).is_absolute() and '..' not in Path(relative).parts,
                'Unsafe output directory')
        current = ''
        for part in Path(relative).parts:
            child = (current + '/' + part).lstrip('/')
            if child not in self.directories:
                self.check()
                parent = self.directories[current][0]
                os.mkdir(part, mode=0o700, dir_fd=parent)
                fd = self.stack.enter_context(ASSETS.directory(part, parent))
                self.directories[child] = (fd, parent, part, os.fstat(fd))
            current = child
        return self.directories[current][0]

    def write(self, relative, data, mode=0o644):
        path = Path(relative)
        parent = self.folder(path.parent.as_posix())
        self.check()
        raw = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                      mode, dir_fd=parent)
        with os.fdopen(raw, 'wb') as output:
            self.files[relative] = (parent, path.name, os.fstat(output.fileno()))
            os.fchmod(output.fileno(), mode)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        self.check()

    def read(self, relative, limit=1024 * 1024):
        self.check()
        parent, name, _ = self.files[relative]
        return ASSETS.read_file(parent, name, limit)

    def inventory(self):
        self.check()
        for relative, (fd, _, _, _) in self.directories.items():
            prefix = relative + '/' if relative else ''
            expected = {p[len(prefix):] for p in (*self.directories, *self.files)
                        if p.startswith(prefix) and p != relative and '/' not in p[len(prefix):]}
            require(set(os.listdir(fd)) == expected, 'Rebuild changed the complete project file inventory')

    def run(self, command, timeout):
        self.inventory()
        project_fd = self.directories['project'][0]
        # The child starts in the pinned project inode, rather than resolving
        # the output's pathname again. Only the verified local scripts run.
        result = subprocess.run(command, pass_fds=(project_fd,),
                                preexec_fn=lambda: os.fchdir(project_fd),
                                capture_output=True, text=True, check=True, timeout=timeout)
        self.inventory()
        return result

    def __exit__(self, kind, value, traceback):
        try:
            if kind is not None:
                # Never recursively remove a pathname that another operation
                # replaced. Unknown/replaced entries leave an incomplete output.
                for parent, name, original in reversed(list(self.files.values())):
                    try:
                        now = os.stat(name, dir_fd=parent, follow_symlinks=False)
                        if identity(now) == identity(original):
                            os.unlink(name, dir_fd=parent)
                    except FileNotFoundError:
                        pass
                for _, parent, name, original in reversed(list(self.directories.values())):
                    try:
                        now = os.stat(name, dir_fd=parent, follow_symlinks=False)
                        if stat.S_ISDIR(now.st_mode) and identity(now) == identity(original):
                            os.rmdir(name, dir_fd=parent)
                    except OSError:
                        pass  # A nonempty/replaced directory is deliberately retained.
        finally:
            self.stack.close()


def canonical_assets(project):
    """Validate all five inputs before creating any destination."""
    with ExitStack() as stack:
        source = {name: stack.enter_context(ASSETS.directory(project / name))
                  for name in ('audio', 'media')}
        loaded = {}
        for spec in ASSETS.manifest_specs(source['audio'], source['media']):
            folder, name = spec['path'].split('/')
            data = ASSETS.read_file(source[folder], name, spec['size'])
            require(len(data) == spec['size'] and digest(data) == spec['sha256'],
                    'File hash/size mismatch: ' + spec['path'])
            ASSETS.validate_payload(spec, data)
            loaded[spec['path']] = data
        return loaded


def bind(args):
    require(not any(k.startswith(('DYLD_', 'LD_')) for k in os.environ),
            'Use the qualified clean toolchain, without dynamic-loader overrides')
    repo, project, projection, out = (p.absolute() for p in (args.repo, args.project, args.projection, args.out))
    for folder in (repo, project, projection, out.parent):
        with ASSETS.directory(folder):
            pass
    require(not os.path.lexists(out), 'Use a new destination; existing outputs are never overwritten')
    require(all(out != p and p not in out.parents and out not in p.parents for p in (repo, project, projection)),
            'Output must be outside the repository, source project and native run')
    files = verified_source(repo, project, args.expected_commit)
    evidence_hashes = {p: digest(read(projection / p))
                       for p in ('projection-proof.json', 'out/receipt.json')}
    proof, relevant, loudness, pcm, version = verify_run(repo, project, projection,
        args.expected_run_commit, args.expected_wav_sha256, args.expected_pcm_sha256)
    documents = binding_documents(project, proof, relevant, loudness, pcm, version)
    del pcm
    assets = canonical_assets(project)
    with OwnedOutput(out) as owned:
        # Binding documents are substituted during their sole, exclusive initial
        # write. There is no later pathname-based document overwrite.
        for relative, (data, mode) in files.items():
            owned.write('project/' + relative, documents.get(relative, data),
                        0o755 if mode == '100755' else 0o644)
        for relative, data in assets.items():
            owned.write('project/' + relative, data)
        build = owned.run(['node', 'scripts/build.js', '--draft'], timeout=60)
        owned.write('build.json', build.stdout.encode())
        check = owned.run([sys.executable, 'scripts/preflight.py', '--source-only',
                           '--postmix', str(projection / 'out/audio.wav')], timeout=180)
        result = json.loads(check.stdout)
        require(not result['source_errors'] and result['waveform']['supplied_postmix_pcm_verified']
                and result['waveform']['supplied_postmix_bins_verified'] and result['production_ready'] is False,
                'Paired project/PCM verification did not pass')
        owned.write('preflight.json', check.stdout.encode())
        require(all(bool(os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode & 0o111)
                    == (mode == '100755')
                    for p, (_, mode) in files.items()
                    for parent, name, _ in [owned.files['project/' + p]]), 'Rebuild changed source file modes')
        changed = sorted(p for p, (data, _) in files.items() if owned.read('project/' + p) != data)
        require(set(changed) <= DERIVED, 'Rebuild changed unrelated project files')
        require(all(owned.read('project/' + p, len(data)) == data for p, data in assets.items()),
                'Rebuild changed canonical media')
        require(verified_source(repo, project, args.expected_commit) == files, 'Original source changed during binding')
        report = {'format': 'framivra-local-postmix-binding/1', 'status': 'BOUND_DRAFT_REQUIRES_REVIEW',
                  'source_commit': args.expected_commit, 'source_tree': git(repo, 'rev-parse', 'HEAD^{tree}').decode().strip(),
                  'native_run_commit': args.expected_run_commit, 'native_proof_sha256': evidence_hashes['projection-proof.json'],
                  'native_receipt_sha256': evidence_hashes['out/receipt.json'],
                  'selected_wav_sha256': args.expected_wav_sha256, 'selected_pcm_sha256': args.expected_pcm_sha256,
                  'changed_payload_paths': changed, 'derived_sha256': {p: digest(owned.read('project/' + p)) for p in sorted(DERIVED)},
                  'production_ready': False, 'audio_approved': False, 'full_render_postmix_check_required': True,
                  'execution_attestation': False, 'aac_diagnostic': 'NOT_RUN_FOR_THIS_BINDING'}
        require(all(digest(read(projection / p)) == h for p, h in evidence_hashes.items()),
                'Native proof or receipt changed during binding')
        require(digest(read(projection / 'out/audio.wav', 5760000 * 4 + 1024 * 1024))
                == args.expected_wav_sha256, 'Selected native WAV changed during binding')
        owned.inventory()
        owned.write('binding-result.json', json_bytes(report))
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'project', 'projection', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('expected-commit', 'expected-run-commit', 'expected-wav-sha256', 'expected-pcm-sha256'):
        parser.add_argument('--' + name, required=True)
    try:
        print(json.dumps(bind(parser.parse_args()), indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print('Native binding failed: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
