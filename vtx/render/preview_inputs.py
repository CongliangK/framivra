"""Single-frame input adapter; producers and writes remain preview-scoped.

Native clips use the exact production seg encoder, not a differently sampled
original PNG. Graphics are read-only, one authenticated frame at a time. This
module never starts a graphics producer or restores a whole compressed page.
"""
import copy
import hashlib

from ..cache.policy import policy_for
from ..cache.recovery import media_cache_valid
from ..runtime import fstxn
from .frame_source import PngSequence
from .gfx_environment import _file


def _run_tasks(c, tasks, _description):
    # prepare_layers owns the complete encoder/filter argv. Only its executor is
    # replaced: PreviewCtx.run enforces the namespace and reaps cancelled children.
    from ..media.encoders import nvenc_budget
    for _label, command, nvenc in tasks:
        if nvenc:
            with nvenc_budget(c):
                c.run(command)
        else:
            c.run(command)


class PreviewInputs:
    def __init__(self, c, frame):
        self.c, self.frame = c, frame
        self.policy = policy_for(c)
        self.entries = []
        self.materialized = []
        tracks = []
        for ti, track in enumerate(c.tl['video_tracks']):
            native = []
            for ci, clip in enumerate(track['clips']):
                if not clip['dst_in'] <= frame < clip['dst_in'] + clip['dur']:
                    continue
                gfx = track.get('kind') == 'gfx'
                if gfx and not self.policy.reuse_graphics:
                    raise ValueError('off mode cannot read reusable gfx; cold graphics require a separately authorized gfx operation')
                entry = {'cl': clip, 'ti': ti, 'ci': ci, 'gfx': gfx}
                if gfx:
                    from .gfx_packed_reader import read_frame
                    entry['png'] = read_frame(c, clip, frame - clip['dst_in'])
                else:
                    native.append(clip)
                self.entries.append(entry)
            # Keep track positions and the original clip objects/parameters. The
            # production seg key deliberately does not include clip indices.
            tracks.append({**track, 'clips': native})
        self.native = copy.copy(c)
        self.native.tl = {**c.tl, 'video_tracks': tracks}
        self.native.gfx_cache = getattr(c, 'gfx_cache', c.cache)
        self.native.cache_store = getattr(c, 'cache_store', c.cache)
        # Atomic media inspection runs through the original PreviewCtx.run and
        # its original full-document input pin, never the filtered view's pin.
        self.native.run = c.run
        self.native.verify_inputs = lambda _stage: c.verify_inputs(None)
        self.native.range_window = (frame, frame + 1)
        self.has_native = any(track['clips'] for track in tracks)
        self.warm = None
        if self.has_native and self.policy.reuse_media:
            from ..media.subs import _layer_ready
            warm = _layer_ready(self.native, frame)
            if warm is not None and all(media_cache_valid(layer['seg'], layer['cl']['dur']) for layer in warm):
                self.warm = warm

    def identity(self):
        """Reauthenticate visible gfx/current media before and after consumption."""
        warm = {(layer['ti'], id(layer['cl'])): layer for layer in self.warm or []}
        identities = []
        for entry in self.entries:
            clip = entry['cl']
            if entry['gfx']:
                from .gfx_packed_reader import read_frame
                data = read_frame(self.c, clip, self.frame - clip['dst_in'])
                asset = {'png_sha256': hashlib.sha256(data).hexdigest()}
                if data != entry['png']:
                    raise ValueError('authenticated graphics frame changed during preview')
            else:
                from ..cache.hygiene import seg_key
                from ..media.encoders import seg_encoder_kind, seg_needs_alpha
                alpha = seg_needs_alpha(clip, self.c.probe(self.c.src_path(clip['src'])))
                encoder = 'prores' if alpha else seg_encoder_kind(self.c)
                key = seg_key(self.c, clip, entry['ti'], entry['ci'],
                              any(k in clip for k in ('scale', 'x', 'y')), alpha, encoder)
                asset = {'original': _file(self.c.src_path(clip['src'])), 'seg_key': key}
                layer = warm.get((entry['ti'], id(clip)))
                if layer is not None:
                    if not media_cache_valid(layer['seg'], clip['dur']):
                        raise ValueError('complete current seg changed during preview')
                    asset['seg'] = _file(layer['seg'])
            identities.append({'asset': asset, 'clip': clip, 'track': entry['ti']})
        for layer, expected in self.materialized:
            if layer['gfx']:
                rel = self.frame - layer['cl']['dst_in']
                layer['source'].require_ready(rel, rel + 1)
            elif (not media_cache_valid(layer['seg'], layer['cl']['dur'])
                    or _file(layer['seg']) != expected):
                raise ValueError('prepared preview segment changed during consumption')
        return identities

    def materialize(self, scratch):
        """Create only selected native segs and requested gfx PNGs in scratch."""
        from ..pipeline.composite import prepare_layers
        if self.warm is not None:
            native = self.warm
        elif self.has_native:
            self.native.cache = scratch
            native = prepare_layers(self.native, run_tasks=_run_tasks)
        else:
            native = []
        native = {(layer['ti'], id(layer['cl'])): layer for layer in native}
        layers = []
        for index, entry in enumerate(self.entries):
            clip = entry['cl']
            if not entry['gfx']:
                layer = native[(entry['ti'], id(clip))]
                if not media_cache_valid(layer['seg'], clip['dur']):
                    raise ValueError('preview requires a complete native segment')
                layers.append(layer)
                continue
            rel = self.frame - clip['dst_in']
            directory = scratch / f'gfx-{index}'
            fstxn.mkdir(directory, root=self.c.root)
            fstxn.atomic_write(directory / f'f{rel:04d}.png', entry['png'], root=self.c.root)
            digest = hashlib.sha256(entry['png']).hexdigest()
            source = PngSequence(directory, digest, clip['dur'], self.c.fps, self.c.w, self.c.h,
                                 environment_check=lambda rel=rel, digest=digest: {rel: digest})
            layers.append({'cl': clip, 'ti': entry['ti'], 'gfx': True,
                           'source': source, 'source_stop': rel + 1,
                           'x': clip.get('x', 0), 'y': clip.get('y', 0),
                           'w2': None, 'h2': None})
        self.materialized = [(layer, None if layer['gfx'] else _file(layer['seg'])) for layer in layers]
        return layers
