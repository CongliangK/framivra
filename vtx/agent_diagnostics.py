"""Equivalent opt-in entry point for the existing validate/audit/qc stages."""
import argparse
import contextlib
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from .runtime.diagnostics import emit, mark_handled
from .infra import Ctx, ProjectPathError, ensure_inside_project
from .stages import audit, validate
from .qc import qc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-t', '--timeline', required=True)
    parser.add_argument('command', choices=('validate', 'audit', 'qc'))
    parser.add_argument('--format', choices=('text', 'agent-json'), default='text')
    parser.add_argument('--strict', action='store_true')
    args = parser.parse_args(argv)
    records = []
    output, errors = io.StringIO(), io.StringIO()
    structured = args.format == 'agent-json'
    c = SimpleNamespace(diagnostics=records)
    exit_code = 0
    with contextlib.ExitStack() as stack:
        if structured:
            stack.enter_context(contextlib.redirect_stdout(output))
            stack.enter_context(contextlib.redirect_stderr(errors))
        try:
            path = Path(os.path.abspath(os.path.expanduser(args.timeline)))
            c = Ctx(path, diagnostics=records if structured else None)
            c.strict = args.strict
            c.draft = False
            c.frames = None
            c.gpu = False
            c.keep_versions = 2
            for target in (c.cache, c.out):
                try:
                    ensure_inside_project(target, c.root)
                except ProjectPathError as error:
                    emit(c, str(error), rule_id='runtime.write_path',
                         measured={'path': str(target)}, threshold={'inside_project': str(c.root)})
                    mark_handled(error)
                    raise
            print(f'== {args.command} ==')
            c.begin_stage(args.command)
            {'validate': validate, 'audit': audit, 'qc': qc}[args.command](c)
            print('完成：', [args.command])
        except SystemExit as error:
            exit_code = error.code if isinstance(error.code, int) else (0 if error.code is None else 1)
            if exit_code and not getattr(error, '_vtx_diagnostic_handled', False):
                emit(c, str(error), rule_id='runtime.unknown', status='unknown',
                     measured={'exception': type(error).__name__, 'interrupted': True},
                     suggested_actions=[{'op': 'inspect_legacy_output', 'params': {}}])
        except Exception as error:
            exit_code = 1
            if not getattr(error, '_vtx_diagnostic_handled', False):
                emit(c, str(error), rule_id='runtime.unknown', status='unknown',
                     measured={'exception': type(error).__name__, 'interrupted': True},
                     suggested_actions=[{'op': 'inspect_input', 'params': {}}])
            print(f'✗ {error}', file=sys.stderr)
    if structured:
        print(json.dumps({'schema_version': 1, 'command': args.command,
                          'exit_code': exit_code, 'diagnostics': records,
                          'legacy_output': {'stdout': output.getvalue(), 'stderr': errors.getvalue()}},
                         # ASCII escapes preserve every JSON string, including
                         # lone surrogates, on strict UTF-8/ASCII stdout streams.
                         ensure_ascii=True, allow_nan=False))
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
