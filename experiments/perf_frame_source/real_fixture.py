#!/usr/bin/env python3
"""Replay the pinned b287 PNG fixtures without rendering or writing their source.

This is an experiment, not production gfx-environment certification. All writes
are beneath a new owned output root. The same runner drives both pinned arms.
"""
import argparse
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import inspect
import json
import marshal
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time
import traceback
import zlib

BASE = ('1c01574dc7ba6625aa2b4768d046bc639351e803', '667f8ce6b606c98908bb845dd9f5abfa53ccb7b2')
CANDIDATE = ('23de3d2d43f24c36bdd9bbfbf5933051a04104f3', '625a651e909f7f1c8e05f7a15b86d431b5253c31')
DOCUMENTS = {
    'inventory': 'ab510c56fbf2beb2f39328dca1adf66d5fc7bc2f853f3f39c8e636039b91f11e',
    'summary': '46e455be67e14a09cb8c2075c5d0403af8ed6cb53c4c920eaf933d55f86ba4f9',
    'bound_inventory': 'ac7a6a3c3b9b276fd6ec544ba21aadd37b57302fe35072238e01802cd8e38ef0',
    'historical_environment': '0cd195c0c0799ace20a25b340a626b585933d6edeaf8af8fe1cccaffc0caea63',
}
PAGES = {
    'ed5a6c51c903': dict(frames=3582, width=1920, height=1080, rgb=(278, 290), small=(270, 302)),
    '5e40dd8253fb': dict(frames=300, width=1920, height=1080, rgb=(156, 184), small=(148, 192)),
}
LIMITS = dict(total_seconds=1200, prepare_seconds=180, decode_seconds=300,
              rss_bytes=1 << 30, disk_bytes=2 << 30, free_bytes=4 << 30,
              evidence_bytes=8 << 20)
SCHEMA = 'vtx.historical-png-fixture/1'


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    path = Path(path)
    before = stat_token(path)
    remaining = before[2]
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while remaining:
            block = stream.read(min(65536, remaining))
            if not block:
                raise ValueError('file shortened while hashing: ' + str(path))
            remaining -= len(block)
            digest.update(block)
        if stream.read(1) or stat_token(path) != before:
            raise ValueError('file changed while hashing: ' + str(path))
    return digest.hexdigest()


def read_json(path, expected=None, maximum=8 << 20):
    path = Path(path)
    before = stat_token(path)
    if before[2] > maximum:
        raise ValueError('evidence JSON exceeds size limit: ' + str(path))
    with path.open('rb') as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError('evidence JSON exceeds size limit: ' + str(path))
    if stat_token(path) != before or len(data) != before[2]:
        raise ValueError('evidence JSON changed while reading: ' + str(path))
    if expected is not None and sha_bytes(data) != expected:
        raise ValueError('evidence digest mismatch: ' + str(path))
    return json.loads(data)


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def stat_token(path):
    info = Path(path).stat()
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def version_output(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=15,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', GIT_OPTIONAL_LOCKS='0'))
    if result.returncode:
        raise ValueError('tool preflight failed: ' + repr(command))
    return result.stdout.strip()


def verify_repo(path, expected):
    path = Path(path).resolve(strict=True)
    identity = version_output(['git', '-C', str(path), 'rev-parse', 'HEAD', 'HEAD^{tree}']).splitlines()
    if identity != list(expected):
        raise ValueError('repository identity does not match pinned arm: ' + str(path))
    if version_output(['git', '-C', str(path), 'status', '--porcelain', '--untracked-files=no']):
        raise ValueError('tracked repository changes: ' + str(path))
    names = version_output(['git', '-C', str(path), 'ls-files', '--', 'vtx']).splitlines()
    modules = {name: file_hash(path / name) for name in names if name.endswith('.py')}
    if not {'vtx/__init__.py', 'vtx/frame_source.py', 'vtx/gfx_cli.py', 'vtx/fstxn.py', 'vtx/composite.py'} <= modules.keys():
        raise ValueError('pinned repository is missing required source modules')
    return dict(path=str(path), commit=identity[0], tree=identity[1], modules=modules)


def current_environment(ffmpeg):
    if sys.version_info[:2] != (3, 9):
        raise ValueError('this real Mac replay requires the previously approved Python 3.9')
    if platform.system() != 'Darwin':
        raise ValueError('the real-input CLI requires the authorized Mac execution window')
    binary = Path(ffmpeg).resolve(strict=True)
    text = version_output([str(binary), '-version'])
    if not re.match(r'ffmpeg version (?:n)?9\.', text):
        raise ValueError('this replay requires the previously approved FFmpeg 9')
    return dict(platform=platform.platform(), architecture=platform.machine(), python=sys.version,
                python_executable=str(Path(sys.executable).resolve()), python_sha256=file_hash(sys.executable),
                zlib=zlib.ZLIB_RUNTIME_VERSION, ffmpeg=str(binary), ffmpeg_sha256=file_hash(binary),
                ffmpeg_version=text, policy='current execution metadata; not historical gfx certification')


def load_inputs(paths, source_root):
    docs = {name: read_json(paths[name], expected) for name, expected in DOCUMENTS.items()}
    source_root = Path(source_root).resolve(strict=True)
    summary_pages = docs['summary'].get('pages')
    if not isinstance(summary_pages, list) or not isinstance(docs['inventory'], list):
        raise ValueError('unexpected pinned source evidence schema')
    out = {}
    for page, spec in PAGES.items():
        summaries = [row for row in summary_pages if row.get('page') == page]
        if len(summaries) != 1:
            raise ValueError('missing or duplicate source page summary')
        summary = summaries[0]
        if summary.get('frame_source') is not None or any(summary.get(k) != spec[k] for k in ('frames', 'width', 'height')):
            raise ValueError('source page dimensions/count/role mismatch')
        rows = [row for row in docs['inventory'] if row.get('page') == page]
        if len(rows) != spec['frames']:
            raise ValueError('source frame inventory count mismatch')
        names = [row['filename'] for row in rows]
        expected_names = ['f%04d.png' % i for i in range(spec['frames'])]
        if sorted(names) != expected_names:
            raise ValueError('source numbering is missing, duplicated or unexpected')
        rows = sorted(rows, key=lambda row: row['filename'])
        for row in rows:
            if type(row.get('bytes')) is not int or not 0 < row['bytes'] <= 32 << 20:
                raise ValueError('source frame compressed size outside replay budget')
            if not isinstance(row.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', row['sha256']):
                raise ValueError('source frame has no exact digest')
        directory = source_root / page
        marker = directory / 'complete.json'
        record = read_json(marker, summary['complete_sha256'], maximum=2 << 20)
        if (record.get('schema') != 1 or record.get('key') != page
                or any(record.get(k) != spec[k] for k in ('frames', 'width', 'height'))
                or record.get('sha256') != [row['sha256'] for row in rows]
                or record.get('mixed_producer')):
            raise ValueError('original page certificate does not match pinned frame inventory')
        if not isinstance(record.get('environment'), dict) or record['environment'].get('status') != 'input-bound':
            raise ValueError('original environment certificate is missing')
        actual_names = sorted(p.name for p in directory.iterdir() if re.fullmatch(r'f\d+\.png', p.name))
        if actual_names != expected_names:
            raise ValueError('original source EOF/numbering does not match inventory')
        out[page] = dict(spec=spec, directory=str(directory), rows=rows,
                         marker=str(marker), marker_sha256=summary['complete_sha256'],
                         marker_stat=stat_token(marker))
    return out


def tree_bytes(root):
    return sum(p.stat().st_size for p in Path(root).rglob('*') if p.is_file())


class Budget:
    """Sample only owned processes and files; never change OS resource settings."""
    def __init__(self, root, limits=None, started=None):
        self.root = Path(root)
        self.limits = dict(LIMITS if limits is None else limits)
        self.deadline = (time.monotonic() if started is None else started) + self.limits['total_seconds']
        self.processes = []
        self.sampled_peak_disk_bytes = 0

    def check_time(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError('total replay deadline exceeded')

    def check(self):
        self.check_time()
        size = tree_bytes(self.root)
        self.sampled_peak_disk_bytes = max(self.sampled_peak_disk_bytes, size)
        if size > self.limits['disk_bytes']:
            raise ValueError('owned scratch disk budget exceeded')

    def run(self, command, directory, name, seconds, *, require_empty_stderr=False):
        self.check()
        directory = Path(directory)
        stdout, stderr = directory / (name + '.stdout'), directory / (name + '.stderr')
        inactive_bytes = tree_bytes(self.root) - tree_bytes(directory)
        start = time.monotonic()
        deadline = min(self.deadline, start + seconds)
        peak, samples, next_sample = 0, [], start
        with stdout.open('xb') as output, stderr.open('xb') as errors:
            process = subprocess.Popen(command, stdout=output, stderr=errors,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), cwd=str(directory))
            failure = None
            try:
                while process.poll() is None:
                    now = time.monotonic()
                    if now >= deadline:
                        raise TimeoutError('owned process exceeded deadline')
                    if now >= next_sample:
                        size = inactive_bytes + tree_bytes(directory)
                        self.sampled_peak_disk_bytes = max(self.sampled_peak_disk_bytes, size)
                        if size > self.limits['disk_bytes']:
                            raise ValueError('owned scratch disk budget exceeded')
                        if stdout.stat().st_size > self.limits['evidence_bytes'] or stderr.stat().st_size > self.limits['evidence_bytes']:
                            raise ValueError('process evidence output exceeded limit')
                        probe = subprocess.run(['ps', '-o', 'rss=', '-p', str(process.pid)],
                                               capture_output=True, text=True, timeout=2)
                        if probe.returncode == 0 and probe.stdout.strip():
                            rss = int(probe.stdout.strip()) * 1024  # ps RSS is KiB on macOS/Linux
                            peak = max(peak, rss)
                            samples.append([now - start, rss])
                            if rss > self.limits['rss_bytes']:
                                raise ValueError('owned process sampled RSS exceeds budget')
                        elif process.poll() is None:
                            raise ValueError('cannot observe owned process RSS')
                        next_sample = time.monotonic() + 1.0
                    time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
                if process.returncode:
                    raise RuntimeError('owned process failed with exit %s' % process.returncode)
                if require_empty_stderr and stderr.stat().st_size:
                    raise ValueError('FFmpeg -v error emitted diagnostics despite exit 0')
                if stdout.stat().st_size > self.limits['evidence_bytes'] or stderr.stat().st_size > self.limits['evidence_bytes']:
                    raise ValueError('process evidence output exceeded limit')
                self.check()
            except BaseException as error:
                failure = str(error)
                if process.poll() is None:
                    process.kill()  # Only this directly owned process; workers spawn no children.
                process.wait(timeout=10)
                raise
            finally:
                self.processes.append(dict(argv=command, pid=process.pid, returncode=process.poll(),
                    wall_s=time.monotonic() - start, sampled_peak_rss_bytes=peak, rss_samples=samples,
                    require_empty_stderr=require_empty_stderr, sample_interval_s=1.0, limitation='sampled RSS, not an instantaneous hard-memory cap',
                    stdout=str(stdout.relative_to(self.root)), stderr=str(stderr.relative_to(self.root)),
                    stdout_sha256=file_hash(stdout), stderr_sha256=file_hash(stderr), failure=failure))
        return stdout


def copy_case(page, source, interval, directory, budget, guards):
    directory.mkdir(parents=True, exist_ok=False)
    target = directory / 'source'
    target.mkdir()
    a, b = interval
    selected = source['rows'][a:b]
    if not (0 <= a < b <= len(source['rows'])):
        raise ValueError('invalid source fixture interval')
    budget.check()
    if tree_bytes(budget.root) + sum(row['bytes'] for row in selected) > budget.limits['disk_bytes']:
        raise ValueError('insufficient scratch budget for source copy')
    mapping = []
    for local, row in enumerate(selected):
        origin = Path(source['directory']) / row['filename']
        before = stat_token(origin)
        previous = guards.setdefault(str(origin), dict(stat=before, sha256=row['sha256']))
        if before != previous['stat'] or before[2] != row['bytes']:
            raise ValueError('original source metadata changed before copy')
        digest, length = hashlib.sha256(), 0
        filename = 'f%04d.png' % local
        with origin.open('rb') as src, (target / filename).open('xb') as dst:
            while True:
                block = src.read(min(65536, row['bytes'] - length + 1))
                if not block:
                    break
                length += len(block)
                if length > row['bytes']:
                    raise ValueError('original source grew during copy')
                digest.update(block)
                dst.write(block)
        if length != row['bytes'] or digest.hexdigest() != row['sha256'] or stat_token(origin) != before:
            raise ValueError('original source changed or did not match pinned digest')
        mapping.append(dict(local=local, original=a + local, filename=filename,
                            bytes=length, sha256=row['sha256'], rgb=source['spec']['rgb'][0] <= a + local < source['spec']['rgb'][1]))
        if local % 64 == 0:
            budget.check()
    marker = Path(source['marker'])
    if stat_token(marker) != source['marker_stat'] or file_hash(marker) != source['marker_sha256']:
        raise ValueError('original certificate changed')
    shutil.copyfile(marker, directory / 'origin-complete.json')
    spec = source['spec']
    fixture = dict(schema=SCHEMA, page=page, origin_commit='b287b86d95565e308eaf8fa64900f9bc6d62ae8e',
        origin_marker_sha256=source['marker_sha256'], inventory_sha256=DOCUMENTS['inventory'],
        interval=[a, b], width=spec['width'], height=spec['height'], fps=30, frames=mapping,
        policy='historical fixed PNG bytes; not current gfx-environment certification')
    identity_data = json.dumps(fixture, sort_keys=True, separators=(',', ':')).encode()
    fixture['identity'] = 'historical-b287-fixture-' + sha_bytes(identity_data)
    write_json(directory / 'fixture.json', fixture)
    return fixture


def audit_page(directory, fixture):
    directory = Path(directory)
    count = len(fixture['frames'])
    names = ['f%04d.png' % i for i in range(count)]
    if sorted(p.name for p in directory.iterdir() if re.fullmatch(r'f\d+\.png', p.name)) != names:
        raise ValueError('canonical/source EOF mismatch')
    return {name: dict(sha256=file_hash(directory / name), stat=stat_token(directory / name)) for name in names}


class PinnedSourceLoader(importlib.machinery.SourceFileLoader):
    """Compile the verified bytes directly; get_code never reads or writes .pyc."""
    def __init__(self, name, path, expected, records):
        super().__init__(name, str(path))
        self.expected, self.records = expected, records

    def get_code(self, fullname):
        path = Path(self.path)
        before = stat_token(path)
        if before[2] > 8 << 20:
            raise ValueError('production source exceeds bounded module size')
        with path.open('rb') as stream:
            data = stream.read((8 << 20) + 1)
        if (len(data) != before[2] or stat_token(path) != before
                or sha_bytes(data) != self.expected):
            raise ValueError('production source changed before compilation: ' + self.path)
        code = compile(data, self.path, 'exec', dont_inherit=True, optimize=0)
        self.records[fullname] = dict(path=self.path, source_sha256=sha_bytes(data),
                                     code_sha256=sha_bytes(marshal.dumps(code)))
        return code


class PinnedSourceFinder(importlib.abc.MetaPathFinder):
    def __init__(self, repo, modules):
        self.repo, self.modules, self.records = Path(repo), modules, {}

    def find_spec(self, fullname, path=None, target=None):
        if fullname != 'vtx' and not fullname.startswith('vtx.'):
            return None
        relative = fullname.replace('.', '/')
        choices = [name for name in (relative + '.py', relative + '/__init__.py')
                   if name in self.modules]
        if len(choices) != 1:
            raise ImportError('module is not a unique pinned source: ' + fullname)
        name = choices[0]
        source = self.repo / name
        loader = PinnedSourceLoader(fullname, source, self.modules[name], self.records)
        return importlib.util.spec_from_file_location(fullname, str(source), loader=loader,
            submodule_search_locations=[str(source.parent)] if name.endswith('/__init__.py') else None)


def worker(plan_path):
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        raise ValueError('worker requires -I -S -B isolated source execution')
    plan = read_json(plan_path)
    repo = Path(plan['repo']).resolve(strict=True)
    if any(name == 'vtx' or name.startswith('vtx.') for name in sys.modules):
        raise ValueError('worker already contains unverified production imports')
    loader = PinnedSourceFinder(repo, plan['modules'])
    sys.meta_path.insert(0, loader)
    from vtx.frame_source import PngSequence
    from vtx import frame_source
    if Path(frame_source.__file__).resolve() != repo / 'vtx/frame_source.py':
        raise ValueError('wrong FrameSource implementation imported')
    for name, expected in plan['modules'].items():
        if file_hash(repo / name) != expected:
            raise ValueError('production module source digest drift: ' + name)
    from vtx.composite import _graph_code_digest
    graph_digest = _graph_code_digest()
    directory = Path(plan_path).parent
    fixture_path = directory / 'fixture.json'
    fixture = read_json(fixture_path, plan['fixture_sha256'])
    if fixture['schema'] != SCHEMA:
        raise ValueError('unknown fixture schema')
    source_directory = directory / 'source'
    initial = audit_page(source_directory, fixture)
    if [initial[row['filename']]['sha256'] for row in fixture['frames']] != [row['sha256'] for row in fixture['frames']]:
        raise ValueError('fixture bytes differ from pinned mapping')
    checks = 0
    def check_fixture():
        nonlocal checks
        checks += 1
        current = read_json(fixture_path, plan['fixture_sha256'])
        if file_hash(directory / 'origin-complete.json') != fixture['origin_marker_sha256']:
            raise ValueError('historical source certificate bytes changed')
        return [row['sha256'] for row in current['frames']]
    source = PngSequence(source_directory, fixture['identity'], len(fixture['frames']),
                         fixture['fps'], fixture['width'], fixture['height'], environment_check=check_fixture)
    phases = {}
    cold_audit = cold_marker = output_directory = None
    for phase in ('cold', 'warm'):
        if phase == 'cold' and list(directory.glob('rgba8-*')):
            raise ValueError('cold canonical namespace is not empty')
        calls = checks
        started, cpu = time.perf_counter(), time.process_time()
        argv = source.input_args()
        timing = dict(wall_s=time.perf_counter() - started, cpu_s=time.process_time() - cpu,
                      fixture_check_calls=checks - calls)
        canonical = Path(argv[-1]).parent
        if canonical.parent != directory or canonical == source_directory or not canonical.name.startswith('rgba8-'):
            raise ValueError('fixture did not use its own mixed-format derivative')
        if timing['fixture_check_calls'] != 2:
            raise ValueError('unexpected fixture-check boundary count')
        current = audit_page(canonical, fixture)
        marker = dict(sha256=file_hash(canonical / 'complete.json'), stat=stat_token(canonical / 'complete.json'))
        for row in fixture['frames']:
            if not row['rgb'] and current[row['filename']]['sha256'] != row['sha256']:
                raise ValueError('RGBA original bytes were changed')
        if phase == 'cold':
            cold_audit, cold_marker, output_directory = current, marker, canonical
        elif current != cold_audit or marker != cold_marker or canonical != output_directory:
            raise ValueError('warm preparation changed its canonical PNGs/marker')
        if audit_page(source_directory, fixture) != initial:
            raise ValueError('preparation changed its source fixture')
        phases[phase] = dict(timing, argv=argv, canonical=str(canonical), marker=marker)
    for name, expected in plan['modules'].items():
        if file_hash(repo / name) != expected:
            raise ValueError('production source drift after preparation: ' + name)
    runtime = dict(loader='pinned-source-only', isolated=bool(sys.flags.isolated), no_site=bool(sys.flags.no_site), validated_signature=str(inspect.signature(PngSequence._validated)),
                   loaded_sources=loader.records, bytecode_policy='existing pyc never read or modified')
    print(json.dumps(dict(schema=SCHEMA, runtime=runtime, module_sha256=plan['modules']['vtx/frame_source.py'], graph_code_digest=graph_digest, fixture_identity=fixture['identity'],
        python=sys.version, zlib=zlib.ZLIB_RUNTIME_VERSION, phases=phases, canonical=cold_audit), indent=2))


def parse_framehash(path, frames, width, height, channels=4):
    text = Path(path).read_text(encoding='utf-8')
    lines = text.splitlines()
    if ([line for line in lines if line.startswith('#tb 0:')] != ['#tb 0: 1/30']
            or [line for line in lines if line.startswith('#dimensions 0:')] != ['#dimensions 0: %dx%d' % (width, height)]):
        raise ValueError('decoded time base or dimensions mismatch')
    rows = []
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        cells = [part.strip() for part in line.split(',')]
        if len(cells) != 6 or not re.fullmatch('[0-9a-fA-F]{64}', cells[5]):
            raise ValueError('invalid decoded framehash row')
        values = [int(value) for value in cells[:5]]
        index = len(rows)
        if values != [0, index, index, 1, width * height * channels]:
            raise ValueError('decoded frame order/count/PTS/duration/size mismatch')
        rows.append(cells[5].lower())
    if len(rows) != frames:
        raise ValueError('decoded frame count mismatch')
    return rows


def ffmpeg_hash(ffmpeg, pattern, output_directory, name, fixture, budget, single=False, alpha=False):
    command = [ffmpeg, '-nostdin', '-v', 'error', '-threads', '1', '-f', 'image2', '-framerate', '30',
               '-start_number', '0', '-i', str(pattern), '-map', '0:v:0', '-an']
    if single:
        command += ['-frames:v', '1']
    if alpha:
        command += ['-vf', 'format=rgba,alphaextract']
    command += ['-c:v', 'rawvideo', '-pix_fmt', 'gray' if alpha else 'rgba', '-threads', '1',
                '-fps_mode', 'passthrough', '-f', 'framehash', '-hash', 'sha256', '-']
    output = budget.run(command, output_directory, name, budget.limits['decode_seconds'], require_empty_stderr=True)
    rows = parse_framehash(output, 1 if single else len(fixture['frames']),
                           fixture['width'], fixture['height'], 1 if alpha else 4)
    return rows


def compare_arms(left, right, fixture):
    if left['fixture_identity'] != fixture['identity'] or left['fixture_identity'] != right['fixture_identity']:
        raise ValueError('two arms did not use identical fixture identity')
    hashes = lambda value: {name: row['sha256'] for name, row in value['canonical'].items()}
    if hashes(left) != hashes(right):
        raise ValueError('A/B canonical PNG bytes differ')
    for phase in ('cold', 'warm'):
        a, b = left['phases'][phase], right['phases'][phase]
        if a['argv'][:-1] != b['argv'][:-1] or Path(a['argv'][-1]).name != Path(b['argv'][-1]).name:
            raise ValueError('A/B input arguments differ beyond isolated directory')
        if a['marker']['sha256'] != b['marker']['sha256']:
            raise ValueError('A/B canonical provenance/marker bytes differ')
    if len(left['canonical']) != len(fixture['frames']):
        raise ValueError('A/B canonical count mismatch')


def case_manifest(directory, budget):
    """Enumerate every owned case artifact; hashes are outside preparation timing."""
    records = []
    for path in sorted(Path(directory).rglob('*')):
        if path.is_file():
            budget.check_time()
            records.append(dict(path=str(path.relative_to(directory)), bytes=path.stat().st_size,
                                sha256=file_hash(path)))
    write_json(Path(directory) / 'files.json', records)


def source_guards(inputs, guards, budget=None):
    for path, record in guards.items():
        if budget is not None:
            budget.check_time()
        if stat_token(path) != record['stat'] or file_hash(path) != record['sha256']:
            raise ValueError('original source changed across the replay')
    for source in inputs.values():
        names = sorted(p.name for p in Path(source['directory']).iterdir() if re.fullmatch(r'f\d+\.png', p.name))
        if names != [row['filename'] for row in source['rows']]:
            raise ValueError('original source EOF changed across the replay')
        if stat_token(source['marker']) != source['marker_stat'] or file_hash(source['marker']) != source['marker_sha256']:
            raise ValueError('original source certificate changed across the replay')


def timing_summary(runs):
    out = {}
    for phase in ('cold', 'warm'):
        out[phase] = {}
        for arm in 'AB':
            rows = [row for row in runs if row['stage'] == 'full' and row['arm'] == arm]
            samples = [sum(row['phases'][phase]['wall_s'] for row in rows if row['round'] == i)
                       for i in range(3)]
            out[phase][arm] = dict(paired_round_page_sum_wall_s=samples,
                                  median_page_sum_wall_s=statistics.median(samples))
        baseline, candidate = (out[phase][arm]['median_page_sum_wall_s'] for arm in 'AB')
        out[phase]['candidate_change_percent'] = (candidate / baseline - 1) * 100 if baseline else None
    return out


def attempt_failure(args, error, phase, *, result_path=None):
    """Structured stderr is the outer receipt when no safe writable root exists."""
    print(json.dumps(dict(schema=SCHEMA + '/attempt', status='CANCELLED' if isinstance(error, KeyboardInterrupt) else 'FAILED',
        phase=phase, declared_arguments=vars(args) if isinstance(args, argparse.Namespace) else args,
        exception=dict(type=type(error).__name__, message=str(error)),
        result_path=None if result_path is None else str(result_path),
        result_receipt_written=result_path is not None)), file=sys.stderr, flush=True)


def create_output_boundary(args):
    root = Path(args.output).absolute()
    if root.exists():
        raise ValueError('output must be a new, previously absent directory')
    parent = root.parent.resolve(strict=True)
    root = parent / root.name
    # Resolve declared boundaries before opening evidence. Source/repo validation
    # happens inside the receipt scope; no receipt is written into rejected roots.
    protected = [Path(name).resolve() for name in (args.original_project_root,
                 args.source_root, args.baseline_repo, args.candidate_repo)]
    if any(root == path or root.is_relative_to(path) or path.is_relative_to(root) for path in protected):
        raise ValueError('output must be separate from original project, sources and checkouts')
    root.mkdir()
    return root


def replay(args):
    started = time.monotonic()
    try:
        root = create_output_boundary(args)
    except BaseException as error:
        attempt_failure(args, error, 'output-boundary')
        raise
    budget, guards, inputs = Budget(root, started=started), {}, {}
    result = dict(schema=SCHEMA, status='RUNNING', phase='preflight:start',
                  scope='historical PNG canonical preparation only', declared_arguments=vars(args),
                  current_environment=None, repos={}, limits=LIMITS, runs=[], quality=[],
                  input_documents={}, observer='B observer disabled', preflight_completed=False,
                  original_guard_status='UNRUN: preflight incomplete',
                  timing_definition='PngSequence.input_args only, including fixture callback; copy/audit/decode/import excluded',
                  performance_claim='measurements only; no speed threshold or whole-film claim')
    reference = {}
    failure = None
    try:
        result['runner_sha256'] = file_hash(__file__)
        write_json(root / 'attempt.json', dict(schema=SCHEMA + '/attempt', status='STARTED',
            declared_arguments=vars(args), runner_sha256=result['runner_sha256']))
        result['phase'] = 'preflight:documents'
        paths = {name: Path(getattr(args, name)).resolve(strict=True) for name in DOCUMENTS}
        result['input_documents'] = {name: dict(expected_sha256=DOCUMENTS[name], path=str(path), verified=False)
                                     for name, path in paths.items()}
        inputs = load_inputs(paths, args.source_root)
        for record in result['input_documents'].values():
            record['verified'] = True
        result['phase'] = 'preflight:binding'
        original_project = Path(args.original_project_root).resolve(strict=True)
        if not Path(args.source_root).resolve().is_relative_to(original_project):
            raise ValueError('manifest-resolved source root is not in the declared original project')
        protected_files = [(Path(args.binding).resolve(strict=True), args.binding_sha256)]
        for value in args.protected_file:
            name, digest = value.rsplit('=', 1)
            protected_files.append((Path(name).resolve(strict=True), digest))
        for name, path in paths.items():
            guards[str(path)] = dict(stat=stat_token(path), sha256=DOCUMENTS[name])
        for path, expected in protected_files:
            if not re.fullmatch('[0-9a-f]{64}', expected) or file_hash(path) != expected:
                raise ValueError('original binding/protected-file digest mismatch')
            guards[str(path)] = dict(stat=stat_token(path), sha256=expected)
        result['phase'] = 'preflight:repositories'
        repos = dict(A=verify_repo(args.baseline_repo, BASE), B=verify_repo(args.candidate_repo, CANDIDATE))
        result['repos'] = repos
        result['phase'] = 'preflight:environment'
        environment = current_environment(args.ffmpeg)
        result['current_environment'] = environment
        result['phase'] = 'preflight:resources'
        if shutil.disk_usage(root).free < LIMITS['free_bytes']:
            raise ValueError('at least 4 GiB free scratch space is required')
        budget.check()
        result['preflight_completed'] = True
        result['phase'] = 'replay'
        for stage, orders in (('small', ('AB',)), ('full', ('AB', 'BA', 'AB'))):
            for repetition, order in enumerate(orders):
                for page, source in inputs.items():
                    pair = {}
                    interval = source['spec']['small'] if stage == 'small' else (0, source['spec']['frames'])
                    for arm in order:
                        directory = root / stage / ('round-%d' % repetition) / page / arm
                        fixture = copy_case(page, source, interval, directory, budget, guards)
                        verify_repo(repos[arm]['path'], BASE if arm == 'A' else CANDIDATE)
                        plan = dict(repo=repos[arm]['path'], modules=repos[arm]['modules'],
                                    fixture_sha256=file_hash(directory / 'fixture.json'))
                        write_json(directory / 'worker-plan.json', plan)
                        output = budget.run([sys.executable, '-I', '-S', '-B', str(Path(__file__).resolve()), '--worker',
                                             str(directory / 'worker-plan.json')], directory, 'prepare',
                                            budget.limits['prepare_seconds'])
                        record = read_json(output)
                        pair[arm] = record
                        result['runs'].append(dict(stage=stage, round=repetition, page=page, arm=arm,
                            fixture=str((directory / 'fixture.json').relative_to(root)),
                            fixture_sha256=plan['fixture_sha256'], preparation=str(output.relative_to(root)),
                            preparation_sha256=file_hash(output), phases=record['phases']))
                        if (stage, page) in reference:
                            compare_arms(reference[(stage, page)], record, fixture)
                    compare_arms(pair['A'], pair['B'], fixture)
                    if repetition == 0:
                        quality = {}
                        for arm in 'AB':
                            directory = root / stage / 'round-0' / page / arm
                            canonical = Path(pair[arm]['phases']['cold']['canonical'])
                            quality[arm] = ffmpeg_hash(environment['ffmpeg'], canonical / 'f%04d.png',
                                directory, 'canonical-rgba', fixture, budget)
                        if quality['A'] != quality['B']:
                            raise ValueError('A/B decoded full RGBA or PTS differ')
                        directory = root / stage / 'round-0' / page / 'A'
                        canonical = Path(pair['A']['phases']['cold']['canonical'])
                        expected_alpha = sha_bytes(b'\xff' * (fixture['width'] * fixture['height']))
                        for row in fixture['frames']:
                            if not row['rgb']:
                                continue
                            name = row['filename']
                            original = ffmpeg_hash(environment['ffmpeg'], directory / 'source' / name,
                                directory, 'rgb-%04d' % row['local'], fixture, budget, single=True)
                            derived = quality['A'][row['local']]
                            if original != [derived]:
                                raise ValueError('RGB conversion changed full RGBA pixels')
                            alpha = ffmpeg_hash(environment['ffmpeg'], directory / 'source' / name,
                                directory, 'alpha-%04d' % row['local'], fixture, budget, single=True, alpha=True)
                            if alpha != [expected_alpha]:
                                raise ValueError('RGB input did not decode to opaque alpha')
                        reference[(stage, page)] = pair['A']
                        result['quality'].append(dict(stage=stage, page=page, frames=len(fixture['frames']),
                            rgba_full_hashes_equal=True, pts_exact=True,
                            rgb_conversions_checked=sum(row['rgb'] for row in fixture['frames'])))
                    for arm in 'AB':
                        case_manifest(root / stage / ('round-%d' % repetition) / page / arm, budget)
            if stage == 'small':
                source_guards(inputs, guards, budget)
                write_json(root / 'small-stage-passed.json', dict(status='PASS', qualification='small fixture only',
                    quality=list(result['quality']), no_full_page_claim=True))
        source_guards(inputs, guards, budget)
        verify_repo(repos['A']['path'], BASE)
        verify_repo(repos['B']['path'], CANDIDATE)
        budget.check()
        result['timing_summary'] = timing_summary(result['runs'])
        result['original_guard_status'] = 'PASS: selected inputs and registered files only'
        result['phase'] = 'completed'
        result['status'] = 'PASS'
    except BaseException as error:
        failure = error
        result['status'] = 'FAILED_INCOMPLETE'
        result['failure'] = dict(type=type(error).__name__, message=str(error), traceback=traceback.format_exc())
        result['cancelled'] = isinstance(error, KeyboardInterrupt)
        if result['cancelled']:
            result['original_guard_status'] = 'UNRUN: cancellation'
        elif result['preflight_completed']:
            try:
                source_guards(inputs, guards)
                result['original_guard_status'] = 'PASS: selected inputs and registered files only'
            except Exception as guard_error:
                result['original_guard_status'] = 'FAIL'
                result['original_guard_failure'] = str(guard_error)
    finally:
        result['processes'] = budget.processes
        result['sampled_peak_disk_bytes_before_receipts'] = budget.sampled_peak_disk_bytes
        result['owned_disk_bytes_before_receipts'] = tree_bytes(root)
        result['elapsed_s_before_receipts'] = time.monotonic() - started
        result['original_source_guards'] = guards
        result['not_qualified'] = ['current production gfx environment', 'browser rendering',
                                  'full video composition', 'audio', 'whole-film speedup']
        receipt_written = False
        try:
            write_json(root / 'result.json', result)
            receipt_written = True
            # Failed attempts keep partial files and have no completeness claim.
            receipts = [root / 'result.json', *sorted(root.glob('*/round-*/*/*/files.json'))]
            if (root / 'attempt.json').exists():
                receipts.append(root / 'attempt.json')
            if (root / 'small-stage-passed.json').exists():
                receipts.append(root / 'small-stage-passed.json')
            write_json(root / 'MANIFEST.json', dict(complete=result['status'] == 'PASS',
                files=[dict(path=str(path.relative_to(root)), bytes=path.stat().st_size,
                            sha256=file_hash(path)) for path in receipts]))
        except BaseException as receipt_error:
            attempt_failure(args, receipt_error, 'receipt-write',
                            result_path=root / 'result.json' if receipt_written else None)
            if failure is None:
                failure = receipt_error
        if failure is not None:
            attempt_failure(args, failure, result['phase'],
                            result_path=root / 'result.json' if receipt_written else None)
    if failure is not None:
        raise failure
    return root


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == '--worker':
        worker(argv[1])
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-repo', required=True)
    parser.add_argument('--candidate-repo', required=True)
    parser.add_argument('--original-project-root', required=True, help='Original project root resolved from its manifest; no writes are allowed within it.')
    parser.add_argument('--binding', required=True, help='Existing original binding file; read-only.')
    parser.add_argument('--binding-sha256', required=True, help='Expected binding digest from the existing verified manifest.')
    parser.add_argument('--protected-file', action='append', default=[], metavar='PATH=SHA256', help='Original timeline/config/out file and its verified manifest digest; repeat as needed.')
    parser.add_argument('--source-root', required=True, help='Manifest-resolved parent of the two original page keys; read-only.')
    for name in DOCUMENTS:
        parser.add_argument('--' + name.replace('_', '-'), required=True)
    parser.add_argument('--ffmpeg', required=True)
    parser.add_argument('--output', required=True, help='New owned scratch root, outside original pages and source checkouts.')
    try:
        args = parser.parse_args(argv)
    except (SystemExit, KeyboardInterrupt) as error:
        if isinstance(error, KeyboardInterrupt) or error.code:
            attempt_failure(dict(argv=argv), error, 'arguments')
        if isinstance(error, KeyboardInterrupt):
            return 130
        raise
    try:
        root = replay(args)
    except KeyboardInterrupt:
        return 130
    except Exception:
        return 1
    print(str(root / 'result.json'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
