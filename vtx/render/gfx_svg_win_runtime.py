"""Owned Windows raster runtime for the first, font-free SVG gfx profile."""
from contextlib import ExitStack, contextmanager
from io import BytesIO
import json
from pathlib import Path
import time
from xml.sax.saxutils import escape

from . import gfx_svg as svg, gfx_svg_win_shell as shell, gfx_svg_win_snapshot as snapshot
from .gfx_cli import _png_stream_invalid_reason
from .gfx_svg_win_publish import anchored


# Leaf DER pins from the reviewed Windows system catalogs in #482. This is
# a narrow Windows servicing profile, not generic WinTrust or an image list.
_SIGNERS = frozenset((
    '1308aad34660d785a76b7360c31308d8835cf5721c364a6f5aedcba85eb5b3de',
    '2900fdf0659d3418d6f6f486bb85aebb18b7b65d900ebab1a6845944b50766f8',
    '1721693d3e23c7abf800ae7b86654ed86dceab48c530a57c00d24ef23ff7407e',
))
_PROFILE = 'windows-amd64-inkscape-1.4.4-font-free/v1'
_LEDGER_LIMIT = svg.MAX_MANIFEST_BYTES


def _publish_json(directory, name, value):
    data = svg.canonical(value)
    if len(data) > _LEDGER_LIMIT:
        raise ValueError('Windows SVG publication record exceeds the read budget')
    directory.publish(name, data)


class _WindowsRasterRuntime:
    def __init__(self, project, context, identity, held_images, recheck):
        self.project = project
        self.context = context
        self.identity = identity
        self.held_images = held_images
        self.recheck = recheck

    def engine_identity(self):
        return self.identity

    def fontconfig_identity(self):
        return {'mode': 'explicitly-font-free', 'profile': _PROFILE}

    def font_faces(self, c, name, sha256, fontconfig):
        raise ValueError('Windows SVG production profile does not yet support declared fonts')

    def verify(self):
        operation, executable, cwd, _, _ = self.context
        if operation.keep:
            raise ValueError('Windows SVG runtime cannot publish with unconfirmed cleanup')
        self.recheck(time.monotonic() + 30)
        if snapshot._tree(operation.inputs.vendor) != executable or any((cwd / 'fonts').iterdir()):
            raise ValueError('Windows SVG private runtime identity changed')

    def update_receipt(self, fingerprint, mutate, *, require_runtime=False):
        from ..cache.cache_state import normalize_receipt

        with anchored(self.project) as root, root.child('out', create=True) as out, out.lock('receipt.json'):
            try:
                raw = out.read('receipt.json', svg.MAX_MANIFEST_BYTES)
            except FileNotFoundError:
                previous = {}
            else:
                try:
                    previous = json.loads(raw)
                except (ValueError, UnicodeError):
                    previous = {}
            if require_runtime:
                self.verify()
            if (isinstance(previous, dict) and previous.get('input_fingerprint') not in (None, fingerprint)
                    and previous.get('stages')):
                _publish_json(out, 'receipt.prev.json', previous)
            value = mutate(normalize_receipt(previous, fingerprint))
            _publish_json(out, 'receipt.json', value)
            return value

    def render_page(self, c, clip, record):
        if c.root != self.project or record['fonts'] or record['engine'] != self.identity:
            raise ValueError('Windows SVG stage does not match the bound font-free runtime')
        if svg.digest(svg.canonical(record)) != getattr(c, '_svg_receipt_record', None):
            raise ValueError('Windows SVG stage record does not match the pinned receipt input')
        count, key = record['frame_count'], svg.record_key(record)
        operation, _, cwd, _, _ = self.context
        limit = 64 * 1024 * 1024
        started = time.monotonic()

        def sources():
            for index, name in enumerate(record['frames']):
                data = svg.read_input(c, name, limit=svg.MAX_SVG_BYTES)
                if svg.digest(data) != record['files'][name]:
                    raise ValueError('Windows SVG stage input changed before rendering')
                yield index, data

        def fingerprint(data):
            reason = _png_stream_invalid_reason(BytesIO(data), len(data), width=c.w, height=c.h)
            if reason:
                raise ValueError(f'Windows SVG invalid output PNG: {reason}')
            return svg.digest(data)

        with ExitStack() as pins:
            page = pins.enter_context(anchored(self.project))
            for part in ('cache', 'gfx_exp', svg.NAMESPACE, key):
                page = pins.enter_context(page.child(part, create=True))
            pins.enter_context(page.lock(svg.COMPLETE_FILE))
            try:
                marker = page.read(svg.COMPLETE_FILE, svg.MAX_MANIFEST_BYTES)
            except FileNotFoundError:
                marker = None
            if marker is not None:
                hashes = svg.verify_completion(json.loads(marker), record, count, key)
                for index, expected in enumerate(hashes):
                    if fingerprint(page.read(f'f{index:04d}.png', limit)) != expected:
                        raise ValueError('Windows SVG cached PNG does not match its completion record')
                self.verify()
                c.verify_inputs('gfx')
                return True

            if any((cwd / 'fonts').iterdir()):
                raise ValueError('Windows SVG font-free runtime has unexpected fonts')
            _, frames, names = shell._export_bound_sources(
                self.context, sources(), c.w, c.h,
                deadline=time.monotonic() + max(45, min(3600, 5 * count)),
                capture_limit=8 * 1024 * 1024, held_images=self.held_images)
            self.verify()
            shell._require_outputs(frames, names)
            hashes = []
            for name in names:
                data = shell._read_png(operation, frames / name, limit=limit)
                hashes.append(fingerprint(data))
                page.publish(name, data)
            c.verify_inputs('gfx')
            if svg.resolve(c, clip) != record:
                raise ValueError('Windows SVG identity changed during rendering')
            _publish_json(page, svg.COMPLETE_FILE, {'format': 'framivra-svg-completion/v1',
                'key': key, 'input': record, 'sha256': hashes, 'experimental': True,
                'batch_count': 1, 'raster_seconds': time.monotonic() - started,
                'export_count': count, 'materialized_count': 0})
            return False


@contextmanager
def owned_runtime(project, portable, scratch):
    from .gfx_environment import _windows_boot

    project, portable, scratch = map(snapshot._local_path, (project, portable, scratch))
    with ExitStack() as roots:
        pri = snapshot._pin_dirs(roots, project)
        sci = snapshot._pin_dirs(roots, scratch)
        if pri[-1] in sci or sci[-1] in pri:
            raise ValueError('Windows SVG scratch overlaps the project')
        with snapshot._verified_snapshot(portable, scratch=scratch, retain_package_images=True) as (op, images):
            with ExitStack() as pins:
                executable, cwd = op.inputs.executable, op.inputs.cwd
                pins.enter_context(snapshot._pin_dir(cwd))
                env = shell._environment(cwd, executable.parent)
                pins.enter_context(snapshot._pin_dir(cwd / 'fonts'))
                pins.enter_context(snapshot._pin_dir(cwd / 'profile'))
                configs = {}
                for name in ('fonts.conf', 'profile/preferences.xml'):
                    handle, info, _, directory = pins.enter_context(snapshot._pin_entry(cwd / name))
                    contents = shell._read_pinned(handle, info, directory, 8192)
                    # Paths are private per invocation. The implementation digest
                    # binds the policy; hold these exact generated bytes for use.
                    configs[name] = svg.digest(contents.replace(escape(cwd.as_posix()).encode(), b'$PRIVATE'))
                context = op, executable, cwd, env, pins
                systems, held, recheck = shell._preflight_system_identity(
                    context, images, _SIGNERS, time.monotonic() + 120)
                files = ('gfx_svg_win_runtime.py', 'gfx_svg_win_shell.py', 'gfx_svg_win_snapshot.py',
                         'gfx_svg_win_loaded.py', 'gfx_svg_win_system.py', 'gfx_svg_win_catalog.py',
                         'gfx_svg_win_input.py', 'gfx_svg_win_publish.py', '../runtime/windows_browser.py')
                location = Path(__file__).parent
                identity = {'name': 'inkscape', 'version': shell._INKSCAPE_VERSION, 'profile': _PROFILE,
                            'package': snapshot._TREE_SHA256, 'system': [list(row) for row in systems],
                            'boot': _windows_boot(), 'catalog_signers': sorted(_SIGNERS), 'configuration': configs,
                            'implementation': {name: svg.digest((location / name).read_bytes()) for name in files}}
                yield _WindowsRasterRuntime(project, context, identity, held, recheck)
