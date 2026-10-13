#!/usr/bin/env python3
"""CI-only syntax and strict media gates; no changes to ordinary test skips.

Media suites are deliberately enumerated: passing them does not certify model,
Blender, GPU, release, or external-service integrations. Every selected test
must execute (zero skips) and missing tools are failures before discovery.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MEDIA_TESTS = {
    'ffmpeg': (
        'tests.test_filter_threads',
        'tests.test_composite_determinism',
        'tests.test_post_loudness',
        'tests.test_mux_history',
        'tests.test_audio_report.TestEndToEndSynthetic',
        'tests.test_range_render.TestRangeIntegration',
        'tests.test_graphics_portability.SubtitleIntegration',
    ),
    'chrome': (
        'tests.test_graphics_portability.BrowserIntegration',
        'tests.test_graphics_portability.NavfreeSpanIntegration',
        'tests.test_pipeline.TestPipelineIntegration',
    ),
}


def run(command, **kwargs):
    print('+ ' + ' '.join(map(str, command)), flush=True)
    return subprocess.run(command, check=True, timeout=60, **kwargs)


def static_checks():
    paths = subprocess.check_output(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
        cwd=ROOT).decode('utf-8').split('\0')
    counts = {'python': 0, 'bash': 0, 'node': 0}
    for name in sorted(set(paths) - {''}):
        path = ROOT / name
        if path.suffix == '.py':
            # compile in memory: do not write __pycache__ or execute imports.
            compile(path.read_bytes(), name, 'exec')
            counts['python'] += 1
        elif path.suffix == '.sh':
            run(['bash', '-n', str(path)])
            counts['bash'] += 1
        elif path.suffix in ('.js', '.mjs', '.cjs'):
            run(['node', '--check', str(path)])
            counts['node'] += 1
    if not all(counts.values()):
        raise RuntimeError('static gate discovered an empty language scope: ' + str(counts))
    print('Static syntax coverage:', counts)


def executable(name, override=None):
    requested = os.environ.get(override) if override else None
    found = shutil.which(requested or name)
    if not found:
        raise RuntimeError('required executable missing: ' + (requested or name))
    return found


def prepare_media(kind):
    ffmpeg = executable('ffmpeg', 'VTX_TEST_FFMPEG')
    ffprobe = executable('ffprobe')
    # Existing stage implementations invoke these by name; a test override
    # must not hide a missing/different executable used by CLI subprocesses.
    if Path(executable('ffmpeg')).resolve() != Path(ffmpeg).resolve():
        raise RuntimeError('VTX_TEST_FFMPEG must match ffmpeg on PATH')
    os.environ.update(VTX_TEST_FFMPEG=ffmpeg, VTX_NO_NVENC='1',
                      VTX_SEG_NVENC='0', VTX_SAFETY_EXPECT_FIXED='1')
    run([ffmpeg, '-version'])
    run([ffprobe, '-version'])
    run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
         'color=black:s=256x144:r=30:d=0.1', '-c:v', 'libx264',
         '-f', 'null', '-'])
    if kind == 'ffmpeg':
        os.environ.update(VTX_AUDIO_REPORT_INTEG='1', VTX_RANGE_INTEG='1')
    else:
        chrome = executable('google-chrome', 'VTX_TEST_CHROME')
        os.environ.update(VTX_TEST_CHROME=chrome, VTX_CHROME=chrome,
                          VTX_PIPE_INTEG='1')
        run([chrome, '--version'])
        with tempfile.TemporaryDirectory(prefix='vtx-ci-chrome-') as profile:
            result = run([chrome, '--headless=new', '--no-sandbox',
                          '--disable-dev-shm-usage', '--disable-gpu',
                          '--user-data-dir=' + profile, '--dump-dom',
                          'data:text/html,<p>vtx-ci-ready</p>'],
                         capture_output=True, text=True)
            if 'vtx-ci-ready' not in result.stdout:
                raise RuntimeError('Chrome launched but DOM smoke did not complete')


def strict_suite(names, stream=None):
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    # Per-selector discovery gate: an aggregate count would let one empty
    # selector hide behind another nonempty one (any order must be refused).
    for name in names:
        selected = loader.loadTestsFromNames([name])
        if selected.countTestCases() == 0:
            print('CI media gate: selector resolved to zero tests: %s' % name,
                  file=sys.stderr)
            return 1
        suite.addTests(selected)
    if suite.countTestCases() == 0:
        print('CI media gate requires nonempty execution; total=0',
              file=sys.stderr)
        return 1
    result = unittest.TextTestRunner(verbosity=2, stream=stream).run(suite)
    if not result.testsRun or result.skipped:
        print('CI media gate requires nonempty execution and ZERO skips; '
              'executed=%d skipped=%d' % (result.testsRun, len(result.skipped)),
              file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('gate', choices=('static', 'ffmpeg', 'chrome'))
    args = parser.parse_args(argv)
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    try:
        if args.gate == 'static':
            static_checks()
            return 0
        prepare_media(args.gate)  # before imports: decorators see enabled gates
        return strict_suite(MEDIA_TESTS[args.gate])
    except (OSError, RuntimeError, subprocess.SubprocessError, SyntaxError) as error:
        print('CI gate FAILED: %s' % error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
