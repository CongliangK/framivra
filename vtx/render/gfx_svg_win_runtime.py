"""Owned Windows raster runtime for the explicit SVG gfx profiles."""
from contextlib import ExitStack, contextmanager, nullcontext
from io import BytesIO
import json
from pathlib import Path
import secrets
from threading import RLock
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
_FONT_PROFILE = 'windows-amd64-inkscape-1.4.4-portable-v2-fonts/v1'
_PROJECT_FONT_PROFILE = 'windows-amd64-inkscape-1.4.4-project-fonts/v1'
_FONT_PATHS = tuple('assets/fonts/' + name for name in shell._FONT_NAMES)
_LEDGER_LIMIT = svg.MAX_MANIFEST_BYTES
_PNG_LIMIT = 64 * 1024 * 1024


def _png_digest(data, width, height):
    reason = _png_stream_invalid_reason(BytesIO(data), len(data), width=width, height=height)
    if reason:
        raise ValueError(f'Windows SVG invalid output PNG: {reason}')
    return svg.digest(data)


def _publish_json(directory, name, value):
    data = svg.canonical(value)
    if len(data) > _LEDGER_LIMIT:
        raise ValueError('Windows SVG publication record exceeds the read budget')
    directory.publish(name, data)


class _CorruptCache(ValueError):
    """Bad bytes or a missing frame in an otherwise anchored cache page."""


class _WindowsRasterRuntime:
    def __init__(self, project, context, identity, held_images, recheck, font_records=None, staged=(), *,
                 scope=None, project_fonts=False):
        self.project = project
        self.context = context
        self.identity = identity
        self.held_images = held_images
        self.recheck = recheck
        self.font_records = font_records or {}
        self.staged = tuple(staged)
        self.scope = scope
        self.project_fonts = project_fonts

    def for_fonts(self, c, fonts):
        if self.scope is not None:
            return self.scope.for_fonts(c, fonts)
        self.require_fonts(fonts)
        return self

    def require_fonts(self, fonts):
        if tuple(fonts) != tuple(self.font_records):
            raise ValueError('Windows SVG manifest changed its bound font set')

    def _check_font_directory(self):
        cwd = self.context[2]
        if {path.name for path in (cwd / 'fonts').iterdir()} != {path.name for path, _ in self.staged}:
            raise ValueError('Windows SVG private font directory changed')

    def engine_identity(self):
        return self.identity

    def fontconfig_identity(self):
        if self.font_records:
            return {'mode': 'project-explicit' if self.project_fonts else 'locked-portable-v2',
                    'profile': _PROJECT_FONT_PROFILE if self.project_fonts else _FONT_PROFILE,
                    'version': '2.17.1', 'sha256': shell.fontconfig._DLLS[shell.fontconfig._FONTCONFIG][1]}
        return {'mode': 'explicitly-font-free', 'profile': _PROFILE}

    def font_faces(self, c, name, sha256, fontconfig):
        expected, faces = self.font_records[name]
        if sha256 != expected:
            raise ValueError('Windows SVG declared font changed after staging')
        return [dict(face) for face in faces]

    def verify(self):
        if self.scope is not None:
            with self.scope.lock:
                self._verify_package()
                for runtime in self.scope.runtimes.values():
                    runtime._check_font_directory()
        else:
            self._verify_package()
            self._check_font_directory()

    def _verify_package(self):
        operation, executable, _, _, _ = self.context
        if self.scope is not None and self.scope.closed:
            raise ValueError('Windows SVG runtime scope is unavailable')
        if operation.keep:
            raise ValueError('Windows SVG runtime cannot publish with unconfirmed cleanup')
        self.recheck(time.monotonic() + 30)
        if snapshot._tree(operation.inputs.vendor) != executable:
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

    def _require_record(self, c, clip, record):
        if c.root != self.project or record['engine'] != self.identity:
            raise ValueError('Windows SVG stage does not match the bound runtime')
        self.require_fonts(record['fonts'])
        pinned = getattr(c, '_svg_receipt_records', {}).get(svg.canonical(clip))
        if svg.digest(svg.canonical(record)) != pinned:
            raise ValueError('Windows SVG stage record does not match the pinned receipt input')

    @contextmanager
    def _locked_page(self, key, *, create):
        with ExitStack() as pins:
            page = pins.enter_context(anchored(self.project))
            for part in ('cache', 'gfx_exp', svg.NAMESPACE, key):
                page = pins.enter_context(page.child(part, create=create))
            pins.enter_context(page.lock(svg.COMPLETE_FILE))
            yield page

    @staticmethod
    def _certified_frames(page, marker, record, key, *, indices=None):
        try:
            hashes = svg.verify_completion(json.loads(marker), record, record['frame_count'], key)
        except (ValueError, RecursionError) as error:
            raise _CorruptCache('Windows SVG cache completion record is invalid') from error
        for index in range(len(hashes)) if indices is None else indices:
            expected = hashes[index]
            name = f'f{index:04d}.png'
            try:
                data = page.read(name, _PNG_LIMIT)
            except FileNotFoundError as error:
                raise _CorruptCache('Windows SVG cached PNG is missing') from error
            try:
                observed = _png_digest(data, record['width'], record['height'])
            except ValueError as error:
                raise _CorruptCache(str(error)) from error
            if observed != expected:
                raise _CorruptCache('Windows SVG cached PNG does not match its completion record')
            yield name, data, expected

    @staticmethod
    def _require_frame_index(record, index):
        if type(index) is not int or not 0 <= index < record['frame_count']:
            raise ValueError('Windows SVG frame index is outside the clip')

    def cached_frame(self, c, clip, record, index):
        """Read one certified PNG without opening any other cached frame."""
        self._require_record(c, clip, record)
        self._require_frame_index(record, index)
        key = svg.record_key(record)
        with self._locked_page(key, create=False) as page:
            marker = page.read(svg.COMPLETE_FILE, _LEDGER_LIMIT)
            [(_, data, _)] = self._certified_frames(page, marker, record, key, indices=(index,))
        self.verify()
        c.verify_inputs(None)
        return data

    @contextmanager
    def composition_frames(self, c, clip, record):
        """Freeze only the certified interval for FFmpeg's path-based image2 demuxer."""
        self._require_record(c, clip, record)
        c.verify_inputs('video')
        key = svg.record_key(record)
        directory = self.context[2] / ('composition-frames-' + secrets.token_hex(8))
        directory.mkdir()
        hashes = []
        with ExitStack() as pins:
            pins.enter_context(snapshot._pin_dir(directory))
            with self._locked_page(key, create=False) as page:
                marker = page.read(svg.COMPLETE_FILE, _LEDGER_LIMIT)
                for name, data, expected in self._certified_frames(page, marker, record, key):
                    target = directory / name
                    with target.open('xb') as stream:
                        stream.write(data)
                    handle, info, _, is_directory = pins.enter_context(snapshot._pin_entry(target))
                    if shell._read_pinned(handle, info, is_directory, len(data)) != data:
                        raise ValueError('Windows SVG frozen composition frame changed')
                    hashes.append(expected)
            self.verify()
            c.verify_inputs('video')
            yield directory, tuple(hashes)

    @staticmethod
    def _sources(c, record, indices):
        for index in indices:
            name = record['frames'][index]
            data = svg.read_input(c, name, limit=svg.MAX_SVG_BYTES)
            if svg.digest(data) != record['files'][name]:
                raise ValueError('Windows SVG stage input changed before rendering')
            yield index, data

    def _reset_catalog_cache(self):
        operation, _, cwd, _, _ = self.context
        try:
            shell.fontconfig._empty_catalog_cache(cwd, project=self.project_fonts)
        except BaseException:
            operation.keep = True  # Never substitute recursive cleanup for a rejected cache reset.
            raise

    def _export(self, c, record, indices, *, seconds):
        operation = self.context[0]
        with self.scope.lock if self.scope is not None else nullcontext():
            if self.scope is not None and self.scope.closed:
                raise ValueError('Windows SVG runtime scope is unavailable')
            if operation.keep:
                raise ValueError('Windows SVG runtime has an unconfirmed writer')
            self._check_font_directory()
            if self.staged:
                self._reset_catalog_cache()
            _, directory, names = shell._export_bound_sources(
                self.context, self._sources(c, record, indices), c.w, c.h,
                deadline=time.monotonic() + seconds, capture_limit=8 * 1024 * 1024,
                held_images=self.held_images, export_id=secrets.token_hex(8))
            if self.project_fonts:
                self._reset_catalog_cache()
            shell._require_outputs(directory, names)
            return directory, names

    @contextmanager
    def sparse_frame(self, c, clip, record, index):
        """Export exactly one clip-local frame into owned scratch for preview."""
        self._require_record(c, clip, record)
        self._require_frame_index(record, index)
        c.verify_inputs('frame')
        operation = self.context[0]
        directory, (name,) = self._export(c, record, (index,), seconds=45)
        with ExitStack() as pins:
            try:
                handle, info, _, is_directory = pins.enter_context(snapshot._pin_entry(directory / name))
                data = shell._read_pinned(handle, info, is_directory, _PNG_LIMIT)
            except OSError as error:
                if getattr(error, 'winerror', None) in (32, 33):
                    operation.keep = True
                raise
            sha256 = _png_digest(data, record['width'], record['height'])
            self.verify()
            c.verify_inputs('frame')
            yield directory, sha256

    def render_page(self, c, clip, record):
        self._require_record(c, clip, record)
        count, key = record['frame_count'], svg.record_key(record)
        operation = self.context[0]
        started = time.monotonic()

        with self._locked_page(key, create=True) as page:
            try:
                marker = page.read(svg.COMPLETE_FILE, _LEDGER_LIMIT)
            except FileNotFoundError:
                marker = None
            if marker is not None:
                try:
                    for _ in self._certified_frames(page, marker, record, key):
                        pass
                except _CorruptCache:
                    pass
                else:
                    self.verify()
                    c.verify_inputs('gfx')
                    return True

            # Refuse aliases and known sharing conflicts even when a previous
            # attempt left no marker. No replacement precedes this precheck.
            for index in range(count):
                page.require_replaceable(f'f{index:04d}.png', _PNG_LIMIT)
            if marker is not None:
                page.remove(svg.COMPLETE_FILE)
            frames, names = self._export(c, record, range(count), seconds=max(45, min(3600, 5 * count)))
            self.verify()
            hashes = []
            for name in names:
                data = shell._read_png(operation, frames / name, limit=_PNG_LIMIT)
                hashes.append(_png_digest(data, c.w, c.h))
                page.publish(name, data)
            c.verify_inputs('gfx')
            if svg.resolve(c, clip) != record:
                raise ValueError('Windows SVG identity changed during rendering')
            _publish_json(page, svg.COMPLETE_FILE, {'format': 'framivra-svg-completion/v1',
                'key': key, 'input': record, 'sha256': hashes, 'experimental': True,
                'batch_count': 1, 'raster_seconds': time.monotonic() - started,
                'export_count': count, 'materialized_count': 0})
            return False


def _font_tuple(fonts, *, standard):
    from .gfx_svg_win_input import relative_parts

    fonts = tuple(fonts)
    if standard:
        if fonts != _FONT_PATHS:
            raise ValueError('Windows SVG requires the ordered portable-v2 font set')
        return fonts
    if len(fonts) > shell.fontconfig._PROJECT_MAX_FONTS:
        raise ValueError('Windows SVG project font list exceeds its bound')
    for name in fonts:
        relative_parts(name)
        if Path(name).suffix.lower() not in ('.ttf', '.otf', '.ttc', '.otc'):
            raise ValueError('SVG fonts must be explicit TTF/OTF/TTC/OTC files')
    if len(set(fonts)) != len(fonts):
        raise ValueError('Windows SVG project fonts must not repeat file paths')
    return fonts


def _locked_payloads(c, fonts):
    # Preserve the locked route's early rejection, before copying the package.
    try:
        expected_fonts = shell._locked_font_digests()
    except FileNotFoundError:
        raise ValueError('Windows SVG portable-v2 fonts require a source checkout or managed editable install') from None
    payloads = []
    for name, expected in zip(fonts, expected_fonts):
        data = svg.read_input(c, name, limit=shell._FONT_LIMIT)
        if svg.digest(data) != expected:
            raise ValueError('Windows SVG portable-v2 font byte identity mismatch')
        payloads.append((data, expected))
    return payloads


class _WindowsRuntimeScope:
    """One verified package and Job cleanup authority; independent per-tuple shell state."""
    def __init__(self, c, project, operation, images, pins, standard, locked_payloads):
        self.c, self.project, self.operation, self.images = c, project, operation, images
        self.pins, self.standard, self.locked_payloads = pins, standard, locked_payloads
        self.lock = RLock()
        self.runtimes = {}
        self.system = None
        self.next_shell = 0
        self.closed = False
        pins.callback(self._close)

    def _close(self):
        self.closed = True

    def _stage(self, c, fonts, cwd, pins, executable):
        staged, records = [], {}
        remaining = svg.MAX_INPUT_BYTES
        for index, name in enumerate(fonts):
            if self.standard:
                data, expected = self.locked_payloads[index]
            else:
                data = svg.read_input(c, name, limit=min(remaining, shell.fontconfig._MAX_FONT))
                expected = svg.digest(data)
                remaining -= len(data)
            target = cwd / 'fonts' / f'font{index}{Path(name).suffix.lower()}'
            shell._stage_font(data, expected, name, target, pins)
            staged.append((target, expected))
        op = self.operation
        if self.standard:
            shell._require_staged_font_faces(op, executable, cwd, staged, pins, time.monotonic() + 30)
            fields = ('family', 'style', 'index', 'fontconfig_weight', 'file_face_count')
            faces = [(dict(zip(fields, face)),) for face in shell._RASTER_FONT_FACES]
        elif staged:
            try:
                pins.enter_context(snapshot._pin_dir(cwd / 'font-cache'))
                faces = shell.fontconfig._inspect_project_catalog(cwd.parent, cwd, staged, operation=op,
                                                                 deadline=time.monotonic() + 30)
                shell.fontconfig._empty_catalog_cache(cwd, project=True)
            except BaseException:
                op.keep = True  # Never delete an unconfirmed worker's or rejected catalog's scratch.
                raise
        else:
            faces = []
        for name, (_, expected), metadata in zip(fonts, staged, faces):
            records[name] = expected, tuple(metadata)
        return staged, records

    def for_fonts(self, c, fonts):
        from .gfx_environment import _windows_boot

        fonts = _font_tuple(fonts, standard=self.standard)
        op = self.operation
        with self.lock:
            if c is not self.c or self.closed or op.keep:
                raise ValueError('Windows SVG runtime scope is unavailable')
            if fonts in self.runtimes:
                return self.runtimes[fonts]
            executable, cwd = op.inputs.executable, op.inputs.cwd
            if self.next_shell:
                cwd = cwd.parent / f'shell-{self.next_shell}'
                cwd.mkdir()
            self.next_shell += 1
            pins = self.pins.enter_context(ExitStack())
            pins.enter_context(snapshot._pin_dir(cwd))
            env = shell._environment(cwd, executable.parent)
            pins.enter_context(snapshot._pin_dir(cwd / 'fonts'))
            pins.enter_context(snapshot._pin_dir(cwd / 'profile'))
            configs = {}
            for name in ('fonts.conf', 'profile/preferences.xml'):
                handle, info, _, directory = pins.enter_context(snapshot._pin_entry(cwd / name))
                contents = shell._read_pinned(handle, info, directory, 8192)
                configs[name] = svg.digest(contents.replace(escape(cwd.as_posix()).encode(), b'$PRIVATE'))
            context = op, executable, cwd, env, pins
            staged, records = self._stage(c, fonts, cwd, pins, executable)
            if self.system is None:
                self.system = shell._preflight_system_identity(context, self.images, _SIGNERS, time.monotonic() + 120)
            systems, held, recheck = self.system
            profile = (_FONT_PROFILE if self.standard else _PROJECT_FONT_PROFILE) if staged else _PROFILE
            files = ('gfx_svg_win_runtime.py', 'gfx_svg_win_shell.py', 'gfx_svg_win_snapshot.py',
                     'gfx_svg_win_loaded.py', 'gfx_svg_win_system.py', 'gfx_svg_win_catalog.py',
                     'gfx_svg_win_input.py', 'gfx_svg_win_publish.py', 'gfx_svg_win_fontconfig.py',
                     '../runtime/windows_browser.py')
            location = Path(__file__).parent
            identity = {'name': 'inkscape', 'version': shell._INKSCAPE_VERSION, 'profile': profile,
                        'package': snapshot._TREE_SHA256, 'system': [list(row) for row in systems],
                        'boot': _windows_boot(), 'catalog_signers': sorted(_SIGNERS), 'configuration': configs,
                        'implementation': {name: svg.digest((location / name).read_bytes()) for name in files}}
            runtime = _WindowsRasterRuntime(self.project, context, identity, held, recheck, records, staged,
                                            scope=self, project_fonts=bool(staged) and not self.standard)
            self.runtimes[fonts] = runtime
            return runtime


@contextmanager
def owned_runtime(c, portable, scratch, fonts, *, media_bin=None):
    standard = getattr(c, '_svg_standard', False)
    fonts = _font_tuple(fonts, standard=standard)
    project, portable, scratch = map(snapshot._local_path, (c.root, portable, scratch))
    if media_bin is not None and '%' in str(scratch):
        raise ValueError('Windows SVG media scratch must not contain %')
    locked_payloads = _locked_payloads(c, fonts) if standard else ()
    with ExitStack() as roots:
        pri = snapshot._pin_dirs(roots, project)
        sci = snapshot._pin_dirs(roots, scratch)
        if pri[-1] in sci or sci[-1] in pri:
            raise ValueError('Windows SVG scratch overlaps the project')
        vendor = snapshot._pin_dirs(roots, portable / 'inkscape')
        if pri[-1] in vendor or vendor[-1] in pri:
            raise ValueError('Windows SVG project overlaps the portable package')
        if media_bin is not None:
            media_bin = snapshot._local_path(media_bin)
            tools = snapshot._pin_dirs(roots, media_bin)
            if any(other[-1] in tools or tools[-1] in other for other in (pri, sci, vendor)):
                raise ValueError('Windows SVG media tool directory overlaps project, scratch or package')
        if standard:
            if media_bin is not None:
                from .gfx_svg_win_media import bind_standard_audio
                c._svg_audio_digests = bind_standard_audio(roots, project)
        else:
            from .gfx_svg_win_media import bind_project_inputs
            c._svg_source_digests = bind_project_inputs(roots, c)
        with snapshot._verified_snapshot(portable, scratch=scratch, retain_package_images=True) as (op, images):
            with ExitStack() as pins:
                scope = _WindowsRuntimeScope(c, project, op, images, pins, standard, locked_payloads)
                runtime = scope.for_fonts(c, fonts)
                media_state = None
                if media_bin is not None:
                    from types import SimpleNamespace
                    from .gfx_svg_win_media import WindowsSvgMedia, _STANDARD_TOOLS
                    media_state = SimpleNamespace(keep=False)
                    media_scratch = op.inputs.cwd / 'media'
                    media_scratch.mkdir()
                    c._windows_svg_media = WindowsSvgMedia(pins, project, media_scratch, media_state,
                                                          media_bin, _STANDARD_TOOLS, ctx=c)
                try:
                    yield runtime
                finally:
                    if media_state is not None and media_state.keep:
                        op.keep = True
