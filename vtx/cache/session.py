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


def _pack_path_for_page(c, directory):
    relative = directory.relative_to(graphics_root(c))
    return storage_root(c) / 'gfx_packs' / relative.with_suffix('.vtxp')


def pack_path(c, clip):
    from .hygiene import gfx_dir
    return _pack_path_for_page(c, gfx_dir(c, clip))


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


def hydrate_graphics(c, *, allow_svg_rebuild=False):
    """Restore verified bytes; only non-SVG may retire incomplete loose pages.

    Existing archives are fully checked even if loose PNGs are available: a bad
    pack is an explicit error, never silent fallback, recertification or render.
    """
    if c.dry or not policy_for(c).reuse_graphics:
        return
    from ..render import gfx_svg
    from ..render.gfx_pack import inspect_pack, restore_page, PagePack
    from ..render.gfx_cache_reader import resolve_frames
    verify_input_identity(c)
    for clip, directory in graphics_pages(c):
        archive = pack_path(c, clip)
        if not _present(archive):
            continue
        record = inspect_pack(archive, root=c.root)['record']
        _bound_record(c, clip, directory, record)
        svg = gfx_svg.is_svg(clip)
        if svg:
            gfx_svg.verify_completion(record, gfx_svg.resolve(c, clip), clip['dur'], directory.name)
        with PagePack(archive, expected_record=record, root=c.root) as packed:
            packed.verify()
            metadata = packed.metadata_names
            if svg and _present(directory / gfx_svg.COMPLETE_FILE):
                try:
                    packed.verify_svg_working_copy(directory, exact_marker=False)
                except (OSError, ValueError):
                    if not allow_svg_rebuild:
                        raise
                    # The caller is already executing gfx: leave the loose page
                    # untouched so the original producer applies its own gate.
                continue
        if svg:
            if allow_svg_rebuild and any(_present(directory / f'f{i:04d}.png') for i in range(clip['dur'])):
                continue  # An interrupted producer left PNGs but no marker.
            # Restore only into a new or lock-only page; never retire loose SVG.
            fstxn.mkdir(directory.parent, root=c.root)
            restore_page(archive, directory, expected_record=record, svg_marker=True,
                         verify_inputs=lambda: verify_input_identity(c), root=c.root)
            continue
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
    """Archive currently certified pages; retire eligible non-SVG payloads.

    Same-record warm archives are fully verified but not recompressed. Metadata
    bytes must also match, including original completion formatting. Corrupt old
    archives are refused instead of overwritten from otherwise valid bare PNGs.
    """
    if c.dry:
        return
    from .hygiene import manifest_record
    from ..render import gfx_svg
    from ..render.gfx_cache_reader import resolve_frames
    from ..render.gfx_pack import GfxPackIndexLimitError, PACK_INDEX_LIMIT, pack_page, inspect_pack, PagePack
    from ..render.png_artifact import ArtifactError, read_artifact
    verify_input_identity(c)
    for clip, directory in graphics_pages(c):
        svg = gfx_svg.is_svg(clip)
        marker_path = directory / (gfx_svg.COMPLETE_FILE if svg else 'complete.json')
        if not _present(marker_path):
            continue

        def read_svg_marker():
            return read_artifact(marker_path, root=c.root, limit=PACK_INDEX_LIMIT)

        if svg:
            inputs = gfx_svg.resolve(c, clip)
            try:
                marker = read_svg_marker()
            except ArtifactError as error:
                if error.code != 'image.limit':
                    raise
                # Cannot fit the archive index. Any existing archive still
                # needs its normal gate; do not hide corruption behind this.
                record = None
            else:
                record = json.loads(marker)
                # Authenticate the record whose hashes validate every PNG.
                gfx_svg.verify_completion(record, inputs, clip['dur'], directory.name)
        else:
            resolve_frames(c, clip)  # Full authoritative backend/environment/frame gate.
            marker = marker_path.read_bytes()
            record = json.loads(marker)

        def recheck():
            verify_input_identity(c)
            if svg and read_svg_marker() != marker:
                raise ValueError('SVG completion changed during compaction')

        archive = pack_path(c, clip)
        reuse, metadata = False, None
        if _present(archive):
            previous = inspect_pack(archive, root=c.root)['record']
            _bound_record(c, clip, directory, previous)
            if svg:
                gfx_svg.verify_completion(previous, inputs, clip['dur'], directory.name)
            with PagePack(archive, expected_record=previous, root=c.root) as packed:
                packed.verify()
                if (record is not None and json.dumps(previous, sort_keys=True) == json.dumps(record, sort_keys=True)
                        and packed.read_metadata('complete.json') == marker):
                    reuse = True
                    if svg:
                        packed.verify_svg_working_copy(directory, exact_marker=True)
                    for name in packed.metadata_names:
                        if svg and name == 'complete.json':
                            continue  # The native marker bytes were compared above.
                        saved = packed.read_metadata(name)
                        if read_artifact(directory / name, root=c.root,
                                         limit=max(len(saved), 65536 + clip['dur'] * 32768)) != saved:
                            reuse = False
                            break
                    metadata = packed.metadata_names
        if svg and record is None:
            print('⚠ SVG graphics payload retained: completion exceeds pack index budget', file=sys.stderr)
            continue
        if not reuse:
            fstxn.mkdir(archive.parent, root=c.root)
            try:
                pack_page(directory, archive, expected_record=record, svg_marker=svg,
                          verify_inputs=recheck, root=c.root)
            except GfxPackIndexLimitError:
                print('⚠ SVG graphics payload retained: index exceeds pack reader budget', file=sys.stderr)
                continue
            with PagePack(archive, expected_record=record, root=c.root) as packed:
                packed.verify()
                metadata = packed.metadata_names
        elif svg:
            recheck()
        if not svg:
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
    from ._formal_video import proof_name
    paths = []
    protected = protection(c)
    for stage, name in (('audio', 'audio.wav'), ('video', 'video_noaudio.mp4')):
        if stage not in completed:
            continue
        media = c.out / name
        markers = [completion_marker(media)]
        proof = proof_name(name)
        if proof is not None:
            markers.insert(0, c.out / proof)
        pair = [*markers, media]
        if not any(protected.protects(path) for path in pair):
            paths.extend(pair)
    _retire(c, paths, 'successful full build; temporary stage handoff retired')


_MISSING = object()
_CONTEXT_FIELDS = ('cache_session', 'cache_store', 'gfx_cache', 'preview_cache', 'pass_cache')


class CacheSession:
    """One operation; dry construction writes nothing. Nested sessions are refused."""
    def __init__(self, c, *, retire_stage_outputs=False, allow_svg_rebuild=False):
        self.c, self.policy = c, policy_for(c)
        self.retire_stage_outputs = retire_stage_outputs
        self.allow_svg_rebuild = allow_svg_rebuild
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
            hydrate_graphics(self.c, allow_svg_rebuild=self.allow_svg_rebuild)
            self.graphics_touched = True

    def ensure_stage_inputs(self, name):
        """Reproduce discarded working files AFTER the CLI's existing receipt gate."""
        c = self.c
        if c.dry:
            return
        if name in ('gfx', 'video', 'qc'):
            self.ensure_graphics()
        if self.policy.reuse_media:
            if name == 'qc' and getattr(c, '_windows_svg_media', None) is None:
                self._ensure_qc_clean(reuse=True)
            return
        if name == 'audio' and 'prepare' not in self.completed:
            from ..pipeline.stages import prepare
            prepare(c)
            self.completed.add('prepare')
        if name in ('video', 'qc') and 'gfx' not in self.completed:
            from ..cli.commands import gfx
            gfx(c)
            self.completed.add('gfx')
        if name == 'qc':
            self._ensure_qc_clean(reuse=False)

    def _ensure_qc_clean(self, *, reuse):
        if self.completed.intersection(('video', 'qc_clean')):
            return
        from ..pipeline.composite import (prepare_layers, RenderPlan, render_qc_clean,
                                          needs_qc_clean, DEFAULT_SEGMENTS)
        from ..render.frame_source import png_consumer_scope
        from .recovery import media_cache_valid
        import os
        c = self.c
        needed = needs_qc_clean(c)
        if reuse and needed:
            bottom = c.tl['video_tracks'][0]
            if bottom.get('kind') != 'gfx':
                # The legacy completion record doesn't prove display geometry.
                # A current in-process video/reference is handled above.
                sources = dict.fromkeys(cl['src'] for cl in bottom['clips'])
                reuse = not any(c.probe(c.src_path(sid)).get('quarter_turn') for sid in sources)
        if needed and not (reuse and media_cache_valid(c.cache / 'qc_clean.mp4', c.dur)):
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
