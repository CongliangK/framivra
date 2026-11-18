"""Request-local sparse native gfx producers, never normal gfx cache authority.

Trusted HTML retains the saved page's file/network authority, not a sandbox.
Legacy declarations explicitly do not prove a dynamic dependency closure.
"""
import copy
import json
import re
from contextlib import redirect_stdout
from pathlib import Path
import sys

from ..runtime import fstxn


REQUEST_SCHEMA = 'framivra.preview.sparse-request/v1'


def recover_requests(c):
    """Retry only known preview browser ledgers using native lineage safeguards.

    Unknown/unmarked requests and SIGKILL scratch without a ledger are retained
    for explicit project cleanup; this is not a general cache collector.
    """
    from .png_artifact import read_artifact
    from ..runtime.proc_supervisor import reap_run_dir, WORKER_LEDGER
    preview = c.cache / 'preview'
    if not preview.exists():
        return
    for request in preview.iterdir():
        if not re.fullmatch(r'request_[0-9a-f]{32}', request.name):
            continue
        # Pin physical ancestors before any path-based supervisor ledger read.
        try:
            identity = fstxn.cleanup_identity(request)
            marker = json.loads(read_artifact(request / 'request.json', root=c.root,
                                root_identity=c.frame_root_identity, limit=4096))
        except (OSError, ValueError):
            continue
        if marker != {'schema': REQUEST_SCHEMA, 'project': str(c.root)}:
            continue
        ledgers = list(request.glob('producers/producer-*/tmp/*/' + WORKER_LEDGER))
        if not ledgers:
            continue  # Never infer process death from absence of recovery evidence.
        for ledger in ledgers:
            if not re.fullmatch(r'producer-[0-9]+', ledger.parents[2].name):
                raise ValueError('unknown preview producer recovery namespace; evidence preserved')
            fstxn.cleanup_identity(ledger.parent)
            # Native recovery warnings are diagnostics, never frame JSON stdout.
            with redirect_stdout(sys.stderr):
                status = reap_run_dir(ledger.parent)
            if status not in ('killed', 'already_dead', 'stale_boot'):
                raise ValueError('preview browser recovery unsettled (' + status + '); evidence preserved')
        fstxn.remove_pinned(request, identity, preserve=lambda _path: False)


class Producer:
    def __init__(self, c, clip, local):
        from . import gfx_svg
        self.c, self.clip, self.local = c, clip, local
        self.route = gfx_svg.route(c)
        if self.route == 'html':
            from ..runtime.proc_supervisor import supervision_available
            if not supervision_available():
                raise ValueError('preview HTML requires native browser process supervision')
        self.record = self.identity()

    def identity(self):
        if self.route == 'svg-sequence':
            from .gfx_svg import resolve
            return resolve(self.c, self.clip)
        from . import gfx_environment, gfx_dependencies
        certificate = gfx_environment.certificate(self.c)
        if certificate is None or certificate['axes']['backend'] != 'cdp':
            raise ValueError('preview HTML producer requires certified native CDP')
        gfx_environment.recheck(self.c)
        return {'dependencies': gfx_dependencies.resolve(self.c, self.clip),
                'environment': certificate,
                'identity_scope': 'declared inputs; legacy dynamic closure unproven; trusted HTML is not sandboxed'}

    def check(self):
        if self.identity() != self.record:
            raise ValueError('gfx producer input/environment changed during preview')

    def render(self, directory):
        self.check()
        fstxn.mkdir(directory, root=self.c.root)
        if self.route == 'svg-sequence':
            from . import gfx_svg, gfx_svg_stage
            names = [(self.record['frames'][self.local], directory / f'f{self.local:04d}.svg')]
            names.extend((name, directory / 'fonts' / f'font{i}{Path(name).suffix.lower()}')
                         for i, name in enumerate(self.record['fonts']))
            for name, destination in names:
                data = gfx_svg.read_input(self.c, name)
                if gfx_svg.digest(data) != self.record['files'][name]:
                    raise ValueError('SVG pinned input changed before raster')
                fstxn.atomic_write(destination, data, root=self.c.root)
            gfx_svg_stage.run_shell(self.record['engine']['executable'], directory,
                                   count=1, width=self.c.w, height=self.c.h,
                                   root=self.c.root, indices=[self.local])
        else:
            from .gfx_render import render_page
            ctx = copy.copy(self.c)
            ctx.cache = directory
            dependencies = self.record['dependencies']
            kwargs = ({'dependency_record': dependencies}
                      if dependencies['policy'] == 'strict' else {})
            chrome = self.c._gfx_environment[2]  # exact pinned production selector
            with redirect_stdout(sys.stderr):
                stats = render_page(str(self.c.root / self.clip['src']), str(directory),
                                    n_frames=self.clip['dur'], frames=[self.local], tabs=1,
                                    width=self.c.w, height=self.c.h, chrome_path=chrome,
                                    ctx=ctx, **kwargs)
            if stats.get('failed'):
                raise ValueError('native sparse HTML producer failed')
            from ..runtime.proc_supervisor import WORKER_LEDGER
            if any(directory.rglob(WORKER_LEDGER)):
                raise ValueError('unsettled browser recovery ledger remains')
        self.check()
        from .png_artifact import read_png
        _, data = read_png(directory / f'f{self.local:04d}.png', root=self.c.root,
                           root_identity=self.c.frame_root_identity,
                           width=self.c.w, height=self.c.h, limit=8 * 1024 * 1024)
        return data
