"""Operation-owned working sets and certified graphics storage.

Only explicit render operations hydrate/retire their selected pages. Configuration
saves do not delete historical caches. Packs preserve bytes, never confer current
backend/input eligibility. Source/evidence/lock protection wins over retirement.
"""
import contextlib
import json
from pathlib import Path
import stat
import sys

from .policy import graphics_root, policy_for, storage_root
from .cache_state import verify_input_identity
from ..runtime import fstxn


def pack_path(c, clip):
    from .hygiene import gfx_dir
    relative = gfx_dir(c, clip).relative_to(graphics_root(c))
    return storage_root(c) / 'gfx_packs' / relative.with_suffix('.vtxp')


def graphics_pages(c):
    from .hygiene import gfx_dir
    seen = set()
    for track in c.tl.get('video_tracks', []):
        if track.get('kind') == 'gfx':
            for clip in track.get('clips', []):
                directory = gfx_dir(c, clip)
                if directory not in seen:
                    seen.add(directory)
                    yield clip, directory


def _present(path):
    """Broken links are present, not permission to fall back or overwrite."""
    return fstxn.cleanup_identity(path) is not None


def _bound_record(c, clip, directory, record):
    from ..render import gfx_svg
    facts = record.get('input') if gfx_svg.is_svg(clip) else record
    if (not isinstance(facts, dict) or record.get('key') != directory.name
            or any(type(facts.get(field)) is not int or facts[field] != expected
                   for field, expected in
                   ((('frame_count' if gfx_svg.is_svg(clip) else 'frames'), clip['dur']),
                    ('width', c.w), ('height', c.h)))):
        raise ValueError('graphics pack does not match the selected page contract')


def _payload_paths(directory, frames, metadata):
    return [directory / 'complete.json',
            *(directory / f'f{i:04d}.png' for i in range(frames)),
            *(directory / name for name in metadata if name != 'complete.json')]


def _check_working_copy(c, directory, frames, metadata):
    """Preflight the WHOLE retirement before deleting any payload or marker.

    Unknown files, sources, author files and unarchived evidence must not become
    collateral deletion, nor strand a partial page which cannot be hydrated.
    Fixed metadata directories may contain protected locks; they are not deleted.
    """
    from .cleanup_protection import protection
    if not _present(directory):
        return
    expected = {p.relative_to(directory).as_posix() for p in _payload_paths(directory, frames, metadata)}
    parents = {parent.as_posix() for name in metadata for parent in Path(name).parents
               if parent != Path('.')}
    protected = protection(c)
    def visit(path):
        info = path.lstat()
        identity = fstxn.cleanup_identity(path)
        if identity is None or identity[-1][2] != stat.S_IFDIR:
            raise ValueError('graphics working directory is linked or special')
        for child in sorted(path.iterdir()):
            info = child.lstat()
            relative = child.relative_to(directory).as_posix()
            if child.name.endswith('.lock'):
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('graphics working copy has a linked/special lock')
            elif relative in parents and stat.S_ISDIR(info.st_mode):
                if protected.protects(child):
                    raise ValueError('graphics working directory is an explicit source')
                visit(child)
            elif relative in expected and stat.S_ISREG(info.st_mode):
                if protected.protects(child):
                    raise ValueError('graphics payload is a protected source/author file')
            else:
                raise ValueError('graphics working copy contains unarchived files/evidence')
        if fstxn.cleanup_identity(path) != identity:
            raise ValueError('graphics working directory changed during preflight')
    if protected.protects(directory):
        raise ValueError('graphics working directory is an explicit source')
    visit(directory)


def _retire(c, paths, reason):
    from .hygiene import CleanupPlan, cleanup_entries
    entries = []
    for path in paths:
        entries.extend(cleanup_entries(c, path, reason))
    plan = CleanupPlan(c, {'cache_session': entries})
    verify_input_identity(c)
    plan.execute()


def hydrate_graphics(c):
    """Restore verified bytes, including interrupted/malformed loose pages.

    Existing archives are fully checked even if loose PNGs are available: a bad
    pack is an explicit error, never silent fallback, recertification or render.
    """
    if c.dry or not policy_for(c).reuse_graphics:
        return
    from ..render.gfx_pack import inspect_pack, restore_page, PagePack
    from ..render.gfx_cache_reader import resolve_frames
    verify_input_identity(c)
    for clip, directory in graphics_pages(c):
        archive = pack_path(c, clip)
        if not _present(archive):
            continue
        record = inspect_pack(archive, root=c.root)['record']
        _bound_record(c, clip, directory, record)
        with PagePack(archive, expected_record=record, root=c.root) as packed:
            packed.verify()
            metadata = packed.metadata_names
        if _present(directory / 'complete.json'):
            try:
                resolve_frames(c, clip)
            except (OSError, ValueError):
                pass  # Missing/corrupt loose payload may be recovered, not adopted.
            else:
                continue
        _check_working_copy(c, directory, clip['dur'], metadata)
        _retire(c, _payload_paths(directory, clip['dur'], metadata),
                'incomplete graphics working copy; fully verified pack retained')
        fstxn.mkdir(directory.parent, root=c.root)
        restore_page(archive, directory, expected_record=record,
                     verify_inputs=lambda: verify_input_identity(c), root=c.root)


def compact_graphics(c):
    """Retire only currently certified, precisely archived page payloads.

    Same-record warm archives are fully verified but not recompressed. Metadata
    bytes must also match, including original completion formatting. Corrupt old
    archives are refused instead of overwritten from otherwise valid bare PNGs.
    """
    if c.dry:
        return
    from .hygiene import manifest_record
    from ..render.gfx_cache_reader import resolve_frames
    from ..render.gfx_pack import pack_page, inspect_pack, PagePack
    from ..render.png_artifact import read_artifact
    verify_input_identity(c)
    for clip, directory in graphics_pages(c):
        if not _present(directory / 'complete.json'):
            continue
        resolve_frames(c, clip)  # Full authoritative backend/environment/frame gate.
        marker = (directory / 'complete.json').read_bytes()
        record = json.loads(marker)
        archive = pack_path(c, clip)
        reuse, metadata = False, None
        if _present(archive):
            previous = inspect_pack(archive, root=c.root)['record']
            _bound_record(c, clip, directory, previous)
            with PagePack(archive, expected_record=previous, root=c.root) as packed:
                packed.verify()
                if (json.dumps(previous, sort_keys=True) == json.dumps(record, sort_keys=True)
                        and packed.read_metadata('complete.json') == marker):
                    reuse = True
                    for name in packed.metadata_names:
                        saved = packed.read_metadata(name)
                        if read_artifact(directory / name, root=c.root,
                                         limit=max(len(saved), 65536 + clip['dur'] * 32768)) != saved:
                            reuse = False
                            break
                    metadata = packed.metadata_names
        if not reuse:
            fstxn.mkdir(archive.parent, root=c.root)
            pack_page(directory, archive, expected_record=record,
                      verify_inputs=lambda: verify_input_identity(c), root=c.root)
            with PagePack(archive, expected_record=record, root=c.root) as packed:
                packed.verify()
                metadata = packed.metadata_names
        try:
            _check_working_copy(c, directory, clip['dur'], metadata)
        except ValueError as error:
            print(f'⚠ graphics payload retained: {error}', file=sys.stderr)
        else:
            # Revoke the marker FIRST; interrupted retirement is recoverable.
            _retire(c, _payload_paths(directory, clip['dur'], metadata),
                    'certified payload retained in fully verified graphics pack')
        if not getattr(c, 'range_window', None):
            manifest_record(c, archive, 'gfx_pack', directory.name)


def _retire_stage_outputs(c, completed):
    """Successful full-build handoffs only; final/history/receipts never selected."""
    from .cleanup_protection import protection
    from .recovery import completion_marker
    paths = []
    protected = protection(c)
    for stage, name in (('audio', 'audio.wav'), ('video', 'video_noaudio.mp4')):
        if stage not in completed:
            continue
        media = c.out / name
        marker = completion_marker(media)
        if not protected.protects(media) and not protected.protects(marker):
            paths.extend((marker, media))
    _retire(c, paths, 'successful full build; temporary stage handoff retired')


_MISSING = object()
_CONTEXT_FIELDS = ('cache_session', 'cache_store', 'gfx_cache', 'preview_cache', 'pass_cache')


class CacheSession:
    """One operation; dry construction writes nothing. Nested sessions are refused."""
    def __init__(self, c, *, retire_stage_outputs=False):
        self.c, self.policy = c, policy_for(c)
        self.retire_stage_outputs = retire_stage_outputs
        self.stack = contextlib.ExitStack()
        self.completed, self.graphics_touched = set(), False
        self.original_cache, self.original_graphics = c.cache, graphics_root(c)
        self.saved = {name: getattr(c, name, _MISSING) for name in _CONTEXT_FIELDS}
        self.entered = False

    def __enter__(self):
        c = self.c
        if c.dry:
            return self
        if getattr(c, 'cache_session', None) is not None:
            raise ValueError('nested cache sessions are not supported')
        try:
            self.entered = True
            c.cache_store = storage_root(c)
            self.stack.enter_context(fstxn.file_lock(self.original_cache / '.cache-policy.lock', root=c.root))
            if not self.policy.reuse_media:
                from .hygiene import tmp_run
                directory = self.stack.enter_context(tmp_run(c, 'cache-' + self.policy.mode, unique=True))
                c.cache = directory / 'work'
                fstxn.mkdir(c.cache, root=c.root)
            c.gfx_cache = self.original_graphics if self.policy.reuse_graphics else c.cache
            c.preview_cache = self.original_cache / 'preview'
            c.cache_session, c.pass_cache = self, self.policy.pass_retention
            return self
        except BaseException:
            self._restore_context()
            self.stack.close()
            raise

    def ensure_graphics(self):
        if not self.graphics_touched and not self.c.dry:
            hydrate_graphics(self.c)
            self.graphics_touched = True

    def ensure_stage_inputs(self, name):
        """Reproduce discarded working files AFTER the CLI's existing receipt gate."""
        c = self.c
        if c.dry:
            return
        if name in ('gfx', 'video', 'qc'):
            self.ensure_graphics()
        if self.policy.reuse_media:
            return
        if name == 'audio' and 'prepare' not in self.completed:
            from ..pipeline.stages import prepare
            prepare(c)
            self.completed.add('prepare')
        if name in ('video', 'qc') and 'gfx' not in self.completed:
            from ..cli.commands import gfx
            gfx(c)
            self.completed.add('gfx')
        if name == 'qc' and not self.completed.intersection(('video', 'qc_clean')):
            from ..pipeline.composite import (prepare_layers, RenderPlan, render_qc_clean,
                                              needs_qc_clean, DEFAULT_SEGMENTS)
            from ..render.frame_source import png_consumer_scope
            import os
            if needs_qc_clean(c):
                with png_consumer_scope(prepare_layers(c)) as layers:
                    plan = RenderPlan(c, False, 1.0, c.w, c.h, layers=layers,
                                      nseg=int(os.environ.get('VTX_VIDEO_SEGMENTS', str(DEFAULT_SEGMENTS))))
                    render_qc_clean(c, plan, layers)
            self.completed.add('qc_clean')

    def _restore_context(self):
        if not self.entered:
            return
        self.c.cache = self.original_cache
        for name, value in self.saved.items():
            if value is _MISSING:
                self.c.__dict__.pop(name, None)
            else:
                setattr(self.c, name, value)
        self.entered = False

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if not self.c.dry and self.policy.compress_graphics and self.graphics_touched:
                if sys.platform == 'linux' and fstxn.write_capability() == 'fd-anchored':
                    try:
                        compact_graphics(self.c)
                    except Exception as error:
                        if exc_type is None:
                            raise
                        print(f'⚠ graphics compaction failed; existing payloads protected: {error}', file=sys.stderr)
                else:
                    print('⚠ core cache: compact storage requires Linux fd-anchored I/O; '
                          'certified PNG pages retained on this platform', file=sys.stderr)
            if (exc_type is None and not self.c.dry and self.retire_stage_outputs
                    and not self.policy.reuse_media):
                _retire_stage_outputs(self.c, self.completed)
        finally:
            self._restore_context()
            self.stack.__exit__(exc_type, exc_value, traceback)
        return False
