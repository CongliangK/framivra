"""Opt-in in-process observation; no renderer defaults or return values changed."""
import json
from pathlib import Path
import sys
import threading
import time

from vtx.render import gfx_render
from vtx.cache import session as cache_session


def main():
    output = Path(sys.argv[1])
    sys.argv = [sys.argv[0], *sys.argv[2:]]
    origin = time.monotonic()
    records = []
    lock = threading.Lock()

    def wrap(owner, name, label):
        original = getattr(owner, name)

        def measured(*args, **kwargs):
            start = time.monotonic() - origin
            row = {'operation': label, 'start_s': start,
                   'thread': threading.get_ident(), 'status': 'failed'}
            if label in ('cdp', 'navigate', 'browser_start', 'browser_stop'):
                row['object_id'] = id(args[0])
            if label == 'cdp':
                row['method'] = args[1]
            if label == 'navigate':
                row['url'] = args[1]
            if label == 'png_write':
                row['path'] = str(args[0])
            if label == 'render_page':
                row['page'] = Path(args[0]).name
                row['frames'] = kwargs.get('frames')
            try:
                value = original(*args, **kwargs)
                row['status'] = 'ok'
                if label == 'render_page':
                    row['stats'] = value
                if label == 'png_write':
                    row['bytes'] = len(args[1])
                return value
            except BaseException as exc:
                row['error'] = str(exc)
                raise
            finally:
                row['end_s'] = time.monotonic() - origin
                with lock:
                    records.append(row)
        setattr(owner, name, measured)

    wrap(gfx_render.Browser, 'start', 'browser_start')
    wrap(gfx_render.Browser, 'stop', 'browser_stop')
    wrap(gfx_render.Tab, 'call', 'cdp')
    wrap(gfx_render.Tab, 'navigate', 'navigate')
    wrap(gfx_render, '_atomic_write_bytes', 'png_write')
    wrap(gfx_render, 'render_page', 'render_page')
    wrap(cache_session, 'hydrate_graphics', 'hydrate_graphics')
    wrap(cache_session, 'compact_graphics', 'compact_graphics')
    # Import the actual CLI after installing observations; run the complete all chain.
    from vtx.cli.commands import main as cli
    code = 1
    try:
        code = cli() or 0
        return code
    except BaseException as exc:
        from vtx.runtime.events import exit_code
        code = exit_code(exc)
        raise
    finally:
        with output.open('x', encoding='utf-8') as stream:
            json.dump({'schema': 'html-workflow-observation/1', 'exit_code': code,
                       'wall_s': time.monotonic() - origin,
                       'records': sorted(records, key=lambda row: row['start_s'])}, stream)


if __name__ == '__main__':
    sys.exit(main())
