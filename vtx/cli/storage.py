"""Strict, JSON-only storage CLI adapter (no deletion authority in clients)."""
import sys

from ..storage import (REQUEST_LIMIT, StorageError, _fail, _result, encode,
                       storage_operation)


def run_storage_command(args, parser):
    allowed = {'command', 'timeline', 'subs_extra', 'request_stdin'}
    unused = [key for key, value in vars(args).items()
              if key not in allowed and value != parser.get_default(key)]
    # Namespace/default comparisons alone miss explicit irrelevant defaults
    # such as --align-threshold 1.5. Reject their spellings as well.
    unused += [token for token in sys.argv[1:] if token.startswith('-')
               and token.split('=', 1)[0] not in ('-t', '--timeline', '--request-stdin')
               and not (token.startswith('-t') and not token.startswith('--'))]
    extra = args.subs_extra
    operation = extra[0] if extra else None
    if (unused or len(extra) != 1 or operation not in ('inspect', 'plan', 'clean')
            or args.request_stdin != (operation == 'clean')):
        result = _fail(_result(operation), 'cli.arguments',
                       'use storage inspect | storage plan | storage clean --request-stdin; no other flags')
        code = 2
    else:
        try:
            request = None
            if args.request_stdin:
                from ..timeline_structure import strict_loads
                payload = sys.stdin.buffer.read(REQUEST_LIMIT + 1)
                if len(payload) > REQUEST_LIMIT:
                    raise StorageError('input.limit', 'storage request exceeds 4096 bytes')
                request = strict_loads(payload)
            result = storage_operation(args.timeline, operation, request)
        except (ValueError, OSError, UnicodeError, RecursionError) as error:
            result = _fail(_result(operation), getattr(error, 'code', 'input.request'), str(error))
        code = 0 if result['ok'] else 1
    print(encode(result))
    return code
