#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/demo.py — framivra standard-demo 唯一入口（#177 首切片 / 父任务 #91）。

三步入口：
    python3 scripts/demo.py preflight --profile quick|standard|gfx-stress
    python3 scripts/demo.py prepare   --profile quick|standard --output NEW_DIR
    python3 scripts/demo.py verify    --project DIR [--pixels]

契约要点（issue #177）：
  - preflight 只读：stdout 一份版本化 JSON；区分 prepare 依赖（python/ffmpeg/ffprobe/
    字体）与 render 依赖（CDP/CEF），不因缺 CEF 把 prepare 误判为同一成败状态；
    不 mkdir、不联网、不写缓存、不启动 renderer。
  - prepare 只在可信父目录下创建全新工程目录（拒绝已有目录/symlink 组件/越界路径，
    拒绝仓内/HOME/缓存内输出）；生成媒体（确定性参数）→ 复制冻结输入 → 以仓内冻结
    benchmark manifest 为锚核对（不接受新输出改锁）→ 调用 vtx.agent_diagnostics
    validate --format agent-json 并保留原始 stdout/stderr/exit code。任一阶段失败
    保留证据目录并以非零退出。
  - verify 只读，以仓内冻结清单为外部锚（绝不从待验目录重算 expected）：未知版本/
    生成器身份不符/工具链不符/重复 JSON 键/非有限数/绝对路径/.. 路径/symlink/
    缺文件/大小或哈希不符 → 非零退出。
  - 渲染/preview/QC/CEF/人工视听一律 UNRUN；backend/renderer 字段 null+reason。
  - freeze-manifest：维护用（重冻结仓内 benchmark manifest；需真实 ffmpeg）。

所有渲染、性能与 CEF 结论保持 UNRUN（本切片不运行/安装 CEF/Electron）。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / 'templates' / 'standard-demo'
MANIFEST_PATH = PACKAGE / 'benchmark' / 'benchmark_manifest.json'
PROFILE_DIRS = {
    'quick': PACKAGE / 'profiles' / 'quick' / 'v1',
    'standard': PACKAGE / 'profiles' / 'standard' / 'v1',
    'gfx-stress': PACKAGE / 'profiles' / 'gfx-stress' / 'v1',
}
BENCHMARK_ID = 'framivra-standard-demo'
SCHEMA_VERSION = 1
GENERATOR_PATH = 'scripts/demo.py'
PREPARABLE = ('quick', 'standard')
UNRUN_FIELDS = ('render', 'preview', 'qc', 'cef', 'visual')

SHA256_RE = re.compile(r'\A[0-9a-f]{64}\Z')


# ---------------------------------------------------------------- 基础工具

class DemoError(Exception):
    """携带结构化失败原因的入口错误（报告后以非零退出）。"""


def _no_duplicate_keys(pairs):
    seen = set()
    for key, _ in pairs:
        if key in seen:
            raise DemoError('duplicate JSON key: %r' % key)
        seen.add(key)
    return dict(pairs)


def _no_nonfinite(text):
    raise DemoError('non-finite JSON number: %s' % text)


def load_json_strict(data):
    """严格 JSON：重复键与非有限数均视为损坏（防篡改自证面）。"""
    return json.loads(data, object_pairs_hook=_no_duplicate_keys,
                      parse_constant=_no_nonfinite)


def load_json_file_strict(path):
    try:
        raw = Path(path).read_text(encoding='utf-8')
    except OSError as error:
        raise DemoError('unreadable JSON %s: %s' % (path, error))
    try:
        return load_json_strict(raw)
    except DemoError:
        raise
    except ValueError as error:
        raise DemoError('invalid JSON %s: %s' % (path, error))


def sha256_file(path, _buf=1 << 20):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while True:
            chunk = handle.read(_buf)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sha256_stream(argv):
    """运行子进程并把其 stdout 原始字节流式喂给 sha256（解码指纹用）。"""
    process = subprocess.Popen([str(a) for a in argv], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    digest = hashlib.sha256()
    try:
        while True:
            chunk = process.stdout.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
    finally:
        process.stdout.close()
        stderr = process.stderr.read()
        process.stderr.close()
    code = process.wait()
    if code != 0:
        raise DemoError('stream hash command failed (%d): %s' %
                        (code, stderr.decode('utf-8', 'replace')[:400]))
    return digest.hexdigest()


def run_logged(argv, cwd=None, env=None):
    """执行命令并完整保留分离的 stdout/stderr 与退出码。"""
    started = time.monotonic()
    result = subprocess.run([str(a) for a in argv], capture_output=True,
                            cwd=str(cwd) if cwd else None, env=env)
    return {'argv': [str(a) for a in argv], 'exit_code': result.returncode,
            'stdout': result.stdout.decode('utf-8', 'replace'),
            'stderr': result.stderr.decode('utf-8', 'replace'),
            'seconds': round(time.monotonic() - started, 3)}


def tool_identity(name, override=None, env=None):
    env = os.environ if env is None else env
    found = override or env.get('DEMO_%s' % name.upper()) or shutil.which(name)
    if not found:
        return None
    try:
        version = run_logged([found, '-version'])
    except OSError as error:
        return {'path': found, 'error': str(error)}
    first_line = version['stdout'].splitlines()[0] if version['stdout'] else ''
    identity = {'path': str(Path(found).resolve()),
                'version_line': first_line,
                'binary_sha256': sha256_file(found) if Path(found).is_file() else None}
    if name == 'ffmpeg':
        # build 配置行是工具链身份的一部分，以摘要形态入档
        config_lines = [line.strip() for line in version['stdout'].splitlines()
                        if 'configuration:' in line]
        identity['configuration_sha256'] = (
            hashlib.sha256(config_lines[0].encode()).hexdigest()
            if config_lines else None)
    return identity


def identity_matches(observed, frozen):
    if not observed or not frozen:
        return False
    return (observed.get('version_line') == frozen.get('version_line')
            and observed.get('binary_sha256') == frozen.get('binary_sha256'))


# ------------------------------------------------------- 媒体配方（冻结参数）

CODEC_ARGS = ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
              '-pix_fmt', 'yuv420p', '-threads', '2',
              '-c:a', 'aac', '-b:a', '128k', '-ac', '2', '-ar', '48000',
              '-shortest', '-fflags', '+bitexact', '-flags:v', '+bitexact']

MEDIA_RECIPES = {
    'quick': {
        'v01.mp4': {
            'role': 'quick 唯一源（testsrc2+sine，复用 skill-demo 生成法）',
            'filters': ['testsrc2=size=1920x1080:rate=30',
                        'sine=frequency=440:sample_rate=48000'],
            'frames': 90,
            'extra': ['-frames:v', '90', '-r', '30'],
            'seed_note': 'lavfi testsrc2/sine 为确定信源（无随机态）',
        },
    },
    'standard': {
        'a.mp4': {
            'role': 'standard 第一段 180 帧（color+drawbox+330Hz sine）',
            'filters': ['color=0x708090:s=1920x1080:r=30:d=6,'
                        'drawbox=x=100:y=100:w=400:h=200:color=white:t=fill',
                        'sine=frequency=330:sample_rate=48000:duration=6'],
            'frames': 180,
            'extra': ['-frames:v', '180', '-r', '30'],
            'seed_note': 'lavfi color/sine 为确定信源（无随机态）',
        },
        'b.mp4': {
            'role': 'standard 第二段 180 帧（color+drawbox+550Hz sine）',
            'filters': ['color=0x906040:s=1920x1080:r=30:d=6,'
                        'drawbox=x=100:y=100:w=400:h=200:color=white:t=fill',
                        'sine=frequency=550:sample_rate=48000:duration=6'],
            'frames': 180,
            'extra': ['-frames:v', '180', '-r', '30'],
            'seed_note': 'lavfi color/sine 为确定信源（无随机态）',
        },
    },
}


def generate_media(argv_ffmpeg, profile, project, recipes=None):
    """按冻结参数生成媒体；返回逐文件执行记录（stdout/stderr/exit 原样保留）。"""
    recipes = MEDIA_RECIPES[profile] if recipes is None else recipes
    records = {}
    for name, recipe in sorted(recipes.items()):
        target = project / name
        argv = [argv_ffmpeg, '-y', '-hide_banner', '-loglevel', 'error']
        for stream in recipe['filters']:
            argv += ['-f', 'lavfi', '-i', stream]
        argv += recipe['extra'] + CODEC_ARGS + [str(target)]
        records[name] = run_logged(argv)
    return records


# ------------------------------------------------------------ 解码规格核验

def decoded_specs(ffprobe, ffmpeg, media):
    """解码级核验（非容器元数据）：帧数/尺寸/帧率/像素格式/PTS 摘要/PCM 指纹。"""
    probe = run_logged([ffprobe, '-v', 'error', '-select_streams', 'v:0',
                        '-count_frames', '-show_entries',
                        'stream=nb_read_frames,width,height,r_frame_rate,'
                        'pix_fmt,sample_rate,channels',
                        '-of', 'json', str(media)])
    if probe['exit_code'] != 0:
        raise DemoError('ffprobe failed on %s: %s' % (media, probe['stderr']))
    info = json.loads(probe['stdout'])['streams'][0]
    aprobe = run_logged([ffprobe, '-v', 'error', '-select_streams', 'a:0',
                         '-show_entries',
                         'stream=sample_rate,channels,sample_fmt,duration_ts',
                         '-of', 'json', str(media)])
    audio = {}
    if aprobe['exit_code'] == 0 and aprobe['stdout'].strip():
        streams = json.loads(aprobe['stdout']).get('streams') or []
        if streams:
            audio = streams[0]
    pts = sha256_stream([ffprobe, '-v', 'error', '-select_streams', 'v:0',
                         '-show_entries', 'frame=pts', '-of', 'csv=p=0', media])
    pcm = sha256_stream([ffmpeg, '-v', 'error', '-i', media, '-map', '0:a:0',
                         '-f', 's16le', '-c:a', 'pcm_s16le', '-'])
    return {'frames': int(info['nb_read_frames']),
            'width': info['width'], 'height': info['height'],
            'r_frame_rate': info['r_frame_rate'],
            'pix_fmt': info['pix_fmt'],
            'audio_sample_rate': audio.get('sample_rate'),
            'audio_channels': audio.get('channels'),
            'audio_sample_fmt': audio.get('sample_fmt'),
            'audio_duration_ts': audio.get('duration_ts'),
            'pts_sha256': pts, 'pcm_s16le_sha256': pcm}


def pixel_digest(ffmpeg, media):
    return sha256_stream([ffmpeg, '-v', 'error', '-i', media, '-map', '0:v:0',
                          '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])


# ------------------------------------------------- benchmark manifest（锚）

def generator_sha256():
    return sha256_file(Path(__file__).resolve())


def load_frozen_manifest():
    if not MANIFEST_PATH.is_file():
        raise DemoError('frozen benchmark manifest missing: %s' % MANIFEST_PATH)
    manifest = load_json_file_strict(MANIFEST_PATH)
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest):
    """manifest/schema 验证实现：结构、版本、哈希形态、profile 覆盖。"""
    problems = []
    if manifest.get('benchmark_id') != BENCHMARK_ID:
        problems.append('benchmark_id mismatch')
    if manifest.get('schema_version') != SCHEMA_VERSION:
        problems.append('unsupported manifest schema_version')
    generator = manifest.get('generator') or {}
    if generator.get('path') != GENERATOR_PATH:
        problems.append('generator path mismatch')
    if not SHA256_RE.match(str(generator.get('sha256') or '')):
        problems.append('generator sha256 malformed')
    for tool in ('ffmpeg', 'ffprobe'):
        chain = (manifest.get('toolchain') or {}).get(tool) or {}
        if not chain.get('version_line') or not SHA256_RE.match(
                str(chain.get('binary_sha256') or '')):
            problems.append('toolchain %s identity malformed' % tool)
    profiles = manifest.get('profiles') or {}
    for profile in ('quick', 'standard'):
        node = profiles.get(profile) or {}
        if node.get('version') != 1:
            problems.append('profile %s missing version 1' % profile)
        files = node.get('files') or {}
        if not files:
            problems.append('profile %s has no frozen files' % profile)
        for relpath, meta in files.items():
            if Path(relpath).is_absolute() or '..' in Path(relpath).parts:
                problems.append('profile %s path escape: %s' % (profile, relpath))
            if not SHA256_RE.match(str((meta or {}).get('sha256') or '')):
                problems.append('profile %s file %s sha256 malformed'
                                % (profile, relpath))
        for relpath, decoded in (node.get('decoded') or {}).items():
            if relpath not in files:
                problems.append('decoded spec for non-frozen file: %s' % relpath)
            if not decoded.get('pts_sha256') or not decoded.get('pcm_s16le_sha256'):
                problems.append('decoded spec incomplete: %s' % relpath)
    gfx = profiles.get('gfx-stress') or {}
    reference = gfx.get('reference') or {}
    if not SHA256_RE.match(str(reference.get('suite_manifest_sha256') or '')):
        problems.append('gfx-stress suite_manifest_sha256 malformed')
    if problems:
        raise DemoError('manifest validation failed: %s' % '; '.join(problems))
    return True


def manifest_sha256():
    return sha256_file(MANIFEST_PATH)


# ------------------------------------------------------------ 输出目录边界

def _symlink_components(path):
    """返回路径中真实存在且为符号链接的组件（未解析原路径逐级检查）。"""
    links = []
    current = Path(path)
    while True:
        if current.exists() and current.is_symlink():
            links.append(str(current))
        if current.parent == current:
            break
        current = current.parent
    return links


def check_output_path(raw):
    """prepare 输出目录边界：新目录、无 symlink 组件、可信父目录、仓/HOME/缓存之外。"""
    if not raw or not str(raw).strip():
        raise DemoError('empty --output')
    candidate = Path(os.path.abspath(os.path.expanduser(str(raw))))
    if candidate.exists():
        raise DemoError('output path already exists: %s' % candidate)
    links = _symlink_components(candidate)
    if links:
        raise DemoError('symlink in output path components: %s' % links)
    parent = candidate.parent
    if not parent.is_dir():
        raise DemoError('parent directory does not exist: %s' % parent)
    home = Path(os.path.expanduser('~')).resolve()
    cache = Path(os.environ.get('XDG_CACHE_HOME', home / '.cache')).resolve()
    for forbidden, label in ((ROOT, 'repo'), (home, 'HOME'), (cache, 'cache')):
        if candidate == forbidden or forbidden in candidate.parents:
            raise DemoError('output escapes boundary (inside %s): %s'
                            % (label, candidate))
    return candidate


def safe_relpath(base, relpath):
    """相对路径安全检查：拒绝绝对路径/..逃逸，返回 base 下解析后的路径。"""
    rel = Path(relpath)
    if rel.is_absolute() or '..' in rel.parts:
        raise DemoError('path escape rejected: %s' % relpath)
    target = base / rel
    # 防 symlink 逃逸：逐组件 lstat
    current = base.resolve()
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise DemoError('symlink rejected in project: %s' % current)
    return target


# ----------------------------------------------------------------- preflight

def cmd_preflight(args):
    profile = args.profile
    if profile not in PROFILE_DIRS:
        raise DemoError('unknown profile: %s' % profile)
    report = {'schema_version': SCHEMA_VERSION, 'benchmark_id': BENCHMARK_ID,
              'command': 'preflight', 'profile': profile,
              'read_only': True, 'checks': {}, 'unrun': list(UNRUN_FIELDS)}

    def check(name, fn):
        try:
            report['checks'][name] = fn()
        except DemoError as error:
            report['checks'][name] = {'ok': False, 'error': str(error)}

    check('python', lambda: {'ok': True,
                             'executable': sys.executable,
                             'version': sys.version.split()[0]})
    ffmpeg = tool_identity('ffmpeg', args.ffmpeg)
    ffprobe = tool_identity('ffprobe', args.ffprobe)
    manifest = None
    try:
        manifest = load_frozen_manifest()
        manifest_state = {'ok': True, 'sha256': manifest_sha256(),
                          'generator_current': generator_sha256(),
                          'generator_frozen': manifest['generator']['sha256'],
                          'generator_match':
                              generator_sha256() == manifest['generator']['sha256']}
    except DemoError as error:
        manifest_state = {'ok': False, 'error': str(error)}
    report['checks']['benchmark_manifest'] = manifest_state

    def ffmpeg_check():
        if not ffmpeg:
            return {'ok': False, 'error': 'ffmpeg not found'}
        node = {'ok': True, **ffmpeg}
        if manifest_state.get('ok'):
            frozen = manifest['toolchain']['ffmpeg']
            node['frozen_version_line'] = frozen['version_line']
            node['frozen_binary_sha256'] = frozen['binary_sha256']
            node['frozen_match'] = identity_matches(ffmpeg, frozen)
            node['ok'] = node['frozen_match']
        return node

    def ffprobe_check():
        if not ffprobe:
            return {'ok': False, 'error': 'ffprobe not found'}
        node = {'ok': True, **ffprobe}
        if manifest_state.get('ok'):
            frozen = manifest['toolchain']['ffprobe']
            node['frozen_version_line'] = frozen['version_line']
            node['frozen_binary_sha256'] = frozen['binary_sha256']
            node['frozen_match'] = identity_matches(ffprobe, frozen)
            node['ok'] = node['frozen_match']
        return node

    def profile_check():
        directory = PROFILE_DIRS[profile]
        if not directory.is_dir():
            return {'ok': False, 'error': 'profile dir missing: %s' % directory}
        node = {'ok': True, 'dir': str(directory.relative_to(ROOT)),
                'profile_json': True}
        try:
            load_json_file_strict(directory / 'profile.json')
        except DemoError as error:
            node.update(ok=False, error=str(error))
        return node

    def font_check():
        if profile != 'standard':
            return {'ok': True, 'note': 'profile 无随包字体依赖'}
        font = PROFILE_DIRS['standard'] / 'fonts' / 'NotoSansSC-VF.ttf'
        if not font.is_file():
            return {'ok': False, 'error': 'font missing: %s' % font}
        digest = sha256_file(font)
        node = {'ok': True, 'path': str(font.relative_to(ROOT)),
                'sha256': digest, 'license': 'OFL-1.1'}
        if manifest_state.get('ok'):
            frozen = (manifest['profiles']['standard']['files']
                      .get('fonts/NotoSansSC-VF.ttf') or {}).get('sha256')
            node['frozen_sha256'] = frozen
            node['frozen_match'] = frozen == digest
            node['ok'] = node['frozen_match']
        return node

    check('ffmpeg', ffmpeg_check)
    check('ffprobe', ffprobe_check)
    check('profile', profile_check)
    check('fonts', font_check)

    if profile == 'gfx-stress':
        # 引用型 profile：无 prepare 阶段，不判 render 依赖
        report['prepare_supported'] = False
        report['prepare_ready'] = None
        report['render'] = {'ready': None, 'reason':
                            'gfx-stress 为引用型 profile，本切片不执行 stress/Golden（UNRUN）'}
    else:
        prepare_keys = ('python', 'ffmpeg', 'ffprobe', 'profile', 'fonts',
                        'benchmark_manifest')
        ready = all(report['checks'].get(k, {}).get('ok') for k in prepare_keys)
        # CEF/Electron 本切片不安装不运行；Chrome CDP 仅为 render 阶段依赖探测
        chrome = shutil.which(os.environ.get('VTX_TEST_CHROME') or 'google-chrome')
        report['prepare_supported'] = True
        report['prepare_ready'] = ready
        report['render'] = {
            'ready': False,
            'reason': 'render 阶段未在本切片运行；CEF/Electron 明确不安装不运行',
            'cdp_chrome': chrome,
            'cef': None,
            'cef_reason': '本切片不运行/安装 CEF（issue #177 禁令）',
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if profile == 'gfx-stress':
        return 0
    return 0 if report.get('prepare_ready') else 1


# ------------------------------------------------------------------- prepare

def _copy_profile_inputs(profile, project):
    copied = []
    source = PROFILE_DIRS[profile]
    for item in sorted(source.rglob('*')):
        if item.is_dir():
            continue
        rel = item.relative_to(source)
        target = project / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, target)
        copied.append(str(rel))
    return copied


def cmd_prepare(args):
    if args.profile not in PREPARABLE:
        raise DemoError('prepare unsupported for profile: %s '
                        '(gfx-stress 为引用型，UNRUN)' % args.profile)
    project = check_output_path(args.output)
    ffmpeg = args.ffmpeg or os.environ.get('DEMO_FFMPEG') or shutil.which('ffmpeg')
    ffprobe = args.ffprobe or os.environ.get('DEMO_FFPROBE') or shutil.which('ffprobe')
    if not ffmpeg or not Path(ffmpeg).is_file():
        raise DemoError('ffmpeg executable not found (prepare dependency)')
    if not ffprobe or not Path(ffprobe).is_file():
        raise DemoError('ffprobe executable not found (prepare dependency)')
    manifest = load_frozen_manifest()
    ffmpeg_id, ffprobe_id = tool_identity('ffmpeg', ffmpeg), tool_identity('ffprobe', ffprobe)
    for name, observed, in (('ffmpeg', ffmpeg_id), ('ffprobe', ffprobe_id)):
        if not identity_matches(observed, manifest['toolchain'][name]):
            raise DemoError('toolchain %s identity != frozen manifest '
                            '(版本或二进制哈希不符；未列入的工具链为 unsupported)'
                            % name)
    if generator_sha256() != manifest['generator']['sha256']:
        raise DemoError('generator (scripts/demo.py) sha256 != frozen manifest；'
                        '须先重冻结 benchmark manifest，不得自动接受新输出')

    profile = args.profile
    stages = []

    def stage(name, fn):
        record = {'stage': name}
        try:
            record.update(fn())
        except DemoError as error:
            record.update(exit_code=1, error=str(error))
        except Exception as error:  # 保留证据，不吞错
            record.update(exit_code=1, error='unexpected: %r' % (error,))
        stages.append(record)
        return record

    project.mkdir(parents=False)
    logs = project / 'logs'
    logs.mkdir()

    def do_copy():
        copied = _copy_profile_inputs(profile, project)
        return {'exit_code': 0, 'files': copied}

    def do_generate():
        records = generate_media(ffmpeg, profile, project)
        for name, record in records.items():
            (logs / ('generate-%s.stdout.txt' % name)).write_text(
                record['stdout'], encoding='utf-8')
            (logs / ('generate-%s.stderr.txt' % name)).write_text(
                record['stderr'], encoding='utf-8')
        failed = {n: r for n, r in records.items() if r['exit_code'] != 0}
        return {'exit_code': 1 if failed else 0,
                'generated': sorted(records), 'failed': sorted(failed),
                'records': {n: {k: r[k] for k in ('argv', 'exit_code', 'seconds')}
                            for n, r in records.items()}}

    def do_hash_anchor():
        files = manifest['profiles'][profile]['files']
        mismatches, digests = [], {}
        for relpath in sorted(files):
            target = safe_relpath(project, relpath)
            if not target.is_file():
                mismatches.append('%s: missing' % relpath)
                continue
            digest = sha256_file(target)
            digests[relpath] = digest
            if digest != files[relpath]['sha256']:
                mismatches.append('%s: sha256 mismatch (got %s want %s)'
                                  % (relpath, digest, files[relpath]['sha256']))
            elif target.stat().st_size != files[relpath]['size']:
                mismatches.append('%s: size mismatch' % relpath)
        return {'exit_code': 1 if mismatches else 0,
                'mismatches': mismatches, 'sha256': digests}

    def do_validate():
        record = run_logged([sys.executable, '-X', 'utf8', '-m',
                             'vtx.agent_diagnostics', '-t',
                             str(project / 'timeline.json'), 'validate',
                             '--format', 'agent-json'], cwd=ROOT)
        (logs / 'validate.stdout.json').write_text(record['stdout'], encoding='utf-8')
        (logs / 'validate.stderr.txt').write_text(record['stderr'], encoding='utf-8')
        summary = {'exit_code': record['exit_code'], 'seconds': record['seconds'],
                   'argv': record['argv']}
        try:
            payload = load_json_strict(record['stdout'])
            summary['agent_json'] = {'schema_version': payload.get('schema_version'),
                                     'exit_code': payload.get('exit_code'),
                                     'diagnostics_count':
                                         len(payload.get('diagnostics') or []),
                                     'legacy_stdout':
                                         (payload.get('legacy_output') or {})
                                         .get('stdout', '')[:2000]}
        except (DemoError, ValueError, KeyError) as error:
            summary['parse_error'] = str(error)
        return summary

    stage('copy-frozen-inputs', do_copy)
    stage('generate-media', do_generate)
    stage('hash-anchor-verify', do_hash_anchor)
    stage('validate', do_validate)

    git_commit = run_logged(['git', 'rev-parse', 'HEAD'], cwd=ROOT)
    git_tree = run_logged(['git', 'rev-parse', 'HEAD^{tree}'], cwd=ROOT)
    failed = [s for s in stages if s.get('exit_code')]
    realization = {
        'schema_version': SCHEMA_VERSION,
        'benchmark_id': BENCHMARK_ID,
        'command': 'prepare',
        'profile': profile,
        'profile_version': 1,
        'generator': {'path': GENERATOR_PATH, 'sha256': generator_sha256()},
        'source': {
            'git_commit': git_commit['stdout'].strip() or None,
            'git_tree': git_tree['stdout'].strip() or None,
            'manifest_sha256': manifest_sha256(),
            'command': [GENERATOR_PATH, 'prepare', '--profile', profile,
                        '--output', str(project)],
            'exit_codes': {s['stage']: s.get('exit_code') for s in stages},
        },
        'toolchain': {'ffmpeg': ffmpeg_id, 'ffprobe': ffprobe_id,
                      'python': sys.version.split()[0]},
        'files': stages[2].get('sha256') if len(stages) > 2 else {},
        'stages': stages,
        'resources': {'disk_output_bytes':
                      sum(f.stat().st_size for f in project.rglob('*')
                          if f.is_file()), 'note':
                      '磁盘测量仅生成阶段诊断，非 #63/#107 性能分数'},
        'backend_requested': None,
        'backend_requested_reason': '本切片只做 preflight/prepare/verify，未运行渲染',
        'backend_observed': None,
        'renderer': None,
        'renderer_reason': 'render 未运行（UNRUN）',
        'runtime': None,
        'results': {field: 'UNRUN' for field in UNRUN_FIELDS},
        'pass': not failed,
    }
    (project / 'realization.json').write_text(
        json.dumps(realization, ensure_ascii=False, indent=2), encoding='utf-8')
    shutil.copyfile(MANIFEST_PATH, project / 'benchmark_manifest.instance.json')
    print(json.dumps({'schema_version': SCHEMA_VERSION,
                      'project': str(project), 'pass': realization['pass'],
                      'stages': {s['stage']: s.get('exit_code') for s in stages},
                      'unrun': list(UNRUN_FIELDS)},
                     ensure_ascii=False, indent=2))
    return 0 if not failed else 1


# -------------------------------------------------------------------- verify

def cmd_verify(args):
    project = Path(os.path.abspath(os.path.expanduser(args.project)))
    if not project.is_dir():
        raise DemoError('project dir missing: %s' % project)
    manifest = load_frozen_manifest()
    failures = []

    realization_path = project / 'realization.json'
    if not realization_path.is_file():
        raise DemoError('realization.json missing (not a prepare output?)')
    realization = load_json_file_strict(realization_path)

    if realization.get('benchmark_id') != BENCHMARK_ID:
        failures.append('unknown benchmark_id: %r' % realization.get('benchmark_id'))
    profile = realization.get('profile')
    if profile not in PREPARABLE or profile not in manifest['profiles']:
        failures.append('unknown profile: %r' % profile)
    else:
        frozen = manifest['profiles'][profile]
        if realization.get('profile_version') != frozen.get('version'):
            failures.append('unknown profile version: %r'
                            % realization.get('profile_version'))
    generator = realization.get('generator') or {}
    if generator.get('sha256') != manifest['generator']['sha256']:
        failures.append('generator identity mismatch: %r'
                        % generator.get('sha256'))
    if generator.get('sha256') != generator_sha256():
        failures.append('generator sha256 != current scripts/demo.py（锁漂移）')
    for tool in ('ffmpeg', 'ffprobe'):
        observed = (realization.get('toolchain') or {}).get(tool) or {}
        if not identity_matches(observed, manifest['toolchain'][tool]):
            failures.append('toolchain %s identity mismatch（recorded %r）'
                            % (tool, observed.get('version_line')))
    instance = project / 'benchmark_manifest.instance.json'
    if not instance.is_file():
        failures.append('benchmark_manifest.instance.json missing')
    elif sha256_file(instance) != manifest_sha256():
        failures.append('manifest instance hash != 仓内冻结清单（防自证锚失效）')

    if profile in PREPARABLE and profile in manifest['profiles']:
        for relpath, meta in sorted(manifest['profiles'][profile]['files'].items()):
            try:
                target = safe_relpath(project, relpath)
            except DemoError as error:
                failures.append(str(error))
                continue
            if not target.is_file():
                failures.append('%s: missing' % relpath)
                continue
            stat = target.stat()
            if stat.st_size != meta['size']:
                failures.append('%s: size %d != frozen %d'
                                % (relpath, stat.st_size, meta['size']))
            digest = sha256_file(target)
            if digest != meta['sha256']:
                failures.append('%s: sha256 %s != frozen %s'
                                % (relpath, digest, meta['sha256']))

    decoded_report = {'status': 'skipped', 'reason': 'ffprobe/ffmpeg 不可用'}
    ffprobe = args.ffprobe or os.environ.get('DEMO_FFPROBE') or shutil.which('ffprobe')
    ffmpeg = args.ffmpeg or os.environ.get('DEMO_FFMPEG') or shutil.which('ffmpeg')
    if profile in PREPARABLE and ffprobe and ffmpeg:
        checks = {}
        for relpath, frozen_decoded in sorted(
                manifest['profiles'][profile].get('decoded', {}).items()):
            try:
                observed = decoded_specs(ffprobe, ffmpeg, project / relpath)
            except (DemoError, KeyError, IndexError, ValueError) as error:
                failures.append('%s: decoded probe failed: %s' % (relpath, error))
                continue
            diffs = [k for k in ('frames', 'width', 'height', 'r_frame_rate',
                                 'pix_fmt', 'audio_sample_rate',
                                 'audio_channels', 'audio_sample_fmt',
                                 'pts_sha256', 'pcm_s16le_sha256')
                     if observed.get(k) != frozen_decoded.get(k)]
            checks[relpath] = {'observed': observed,
                               'mismatched_fields': diffs}
            for key in diffs:
                failures.append('%s: decoded %s mismatch (%r != frozen %r)'
                                % (relpath, key, observed.get(key),
                                   frozen_decoded.get(key)))
            if args.pixels:
                digest = pixel_digest(ffmpeg, project / relpath)
                checks[relpath]['pixel_rgb24_sha256'] = digest
                if digest != frozen_decoded.get('pixel_rgb24_sha256'):
                    failures.append('%s: decoded pixel digest mismatch' % relpath)
        decoded_report = {'status': 'checked', 'media': checks}

    report = {'schema_version': SCHEMA_VERSION, 'command': 'verify',
              'benchmark_id': BENCHMARK_ID, 'project': str(project),
              'profile': profile,
              'anchor': {'repo_manifest': str(MANIFEST_PATH.relative_to(ROOT)),
                         'sha256': manifest_sha256()},
              'decoded_checks': decoded_report,
              'failures': failures, 'ok': not failures,
              'unrun': list(UNRUN_FIELDS)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


# ---------------------------------------------------------- freeze-manifest

def cmd_freeze(args):
    """维护用：真实生成于临时目录，测得 encoded/decoded 指纹后重冻结仓内 manifest。"""
    ffmpeg = args.ffmpeg or os.environ.get('DEMO_FFMPEG') or shutil.which('ffmpeg')
    ffprobe = args.ffprobe or os.environ.get('DEMO_FFPROBE') or shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise DemoError('freeze requires real ffmpeg/ffprobe')
    ffmpeg_id = tool_identity('ffmpeg', ffmpeg)
    ffprobe_id = tool_identity('ffprobe', ffprobe)
    profiles_out = {}
    with tempfile.TemporaryDirectory(prefix='demo177-freeze-') as tmp:
        work = Path(tmp)
        for profile in PREPARABLE:
            project = work / profile
            project.mkdir()
            copied = _copy_profile_inputs(profile, project)
            generate_media(ffmpeg, profile, project)
            files, decoded = {}, {}
            for relpath in sorted(copied) + sorted(MEDIA_RECIPES[profile]):
                target = project / relpath
                files[relpath] = {'sha256': sha256_file(target),
                                  'size': target.stat().st_size,
                                  'role': 'input'}
            for name in MEDIA_RECIPES[profile]:
                spec = decoded_specs(ffprobe, ffmpeg, project / name)
                spec['pixel_rgb24_sha256'] = pixel_digest(ffmpeg, project / name)
                decoded[name] = spec
                files[name]['role'] = MEDIA_RECIPES[profile][name]['role']
            profiles_out[profile] = {
                'version': 1,
                'files': files,
                'decoded': decoded,
                'media_generation': MEDIA_RECIPES[profile],
                'codec_args': CODEC_ARGS,
                'timeline': 'timeline.json（files 已锁哈希；配置即文件）',
                'coverage': json.loads((PROFILE_DIRS[profile] /
                                        'profile.json').read_text('utf-8'))['coverage'],
                'cache_dependencies': json.loads(
                    (PROFILE_DIRS[profile] / 'profile.json')
                    .read_text('utf-8'))['cache_dependencies'],
            }
    gfx_profile = load_json_file_strict(PROFILE_DIRS['gfx-stress'] / 'profile.json')
    profiles_out['gfx-stress'] = {'version': 1, 'mode': 'reference-only',
                                  'reference': gfx_profile['reference']}
    manifest = {
        'benchmark_id': BENCHMARK_ID,
        'schema_version': SCHEMA_VERSION,
        'owner_issue': 'CongliangK/framivra#177（父任务 #91）',
        'generator': {'path': GENERATOR_PATH, 'sha256': generator_sha256()},
        'toolchain': {'ffmpeg': ffmpeg_id, 'ffprobe': ffprobe_id},
        'determinism_note': [
            '同工具链（version_line+binary_sha256 双锚）两份全新输出 encoded sha256 必须相等；',
            '跨 FFmpeg 版本不承诺容器字节相同；未列入工具链 = unsupported/unverified；',
            '标准输入为先冻结期望值，生成时只允许验证，不得自动接受新输出改锁。',
        ],
        'fonts': {'Noto Sans SC': {
            'file': 'profiles/standard/v1/fonts/NotoSansSC-VF.ttf',
            'license': 'OFL-1.1', 'license_file': 'fonts/OFL.txt',
            'source_url': 'https://github.com/google/fonts/tree/main/ofl/notosanssc'}},
        'profiles': profiles_out,
        'unrun': list(UNRUN_FIELDS),
    }
    validate_manifest(manifest)
    target = Path(args.output) if args.output else MANIFEST_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                      encoding='utf-8')
    print('frozen manifest -> %s (sha256 %s)' % (target, sha256_file(target)))
    return 0


# ---------------------------------------------------------------------- CLI

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('preflight', 'prepare'):
        node = sub.add_parser(name)
        node.add_argument('--profile', required=True,
                          choices=('quick', 'standard', 'gfx-stress')
                          if name == 'preflight' else PREPARABLE)
        if name == 'prepare':
            node.add_argument('--output', required=True)
        node.add_argument('--ffmpeg')
        node.add_argument('--ffprobe')
        node.set_defaults(func=cmd_preflight if name == 'preflight' else cmd_prepare)
    verify = sub.add_parser('verify')
    verify.add_argument('--project', required=True)
    verify.add_argument('--pixels', action='store_true',
                        help='附加解码原始像素 rgb24 全量指纹核验（F2 证据）')
    verify.add_argument('--ffmpeg')
    verify.add_argument('--ffprobe')
    verify.set_defaults(func=cmd_verify)
    freeze = sub.add_parser('freeze-manifest')
    freeze.add_argument('--output')
    freeze.add_argument('--ffmpeg')
    freeze.add_argument('--ffprobe')
    freeze.set_defaults(func=cmd_freeze)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except DemoError as error:
        print(json.dumps({'schema_version': SCHEMA_VERSION, 'command':
                          args.command, 'ok': False, 'error': str(error)},
                         ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == '__main__':
    sys.exit(main())
