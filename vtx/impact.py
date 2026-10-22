"""Read-only, serializable input snapshots for cache-impact reconciliation.

Snapshots preserve *before* identities, unlike re-reading an old timeline after
its assets were edited. This is input-key analysis, not proof that files on disk
are complete or a substitute for runtime cache validation.
"""
import copy
import contextlib
import io
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from .cache_state import file_digest, media_stat_digest
from .composite import (RenderPlan, _graph_code_digest, choose_cutpoints,
                        segment_content_key, pass_content_key)
from .hygiene import audio_key, seg_key

SCHEMA = 'framivra-impact-snapshot-v2'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                    ensure_ascii=False).encode()).hexdigest()


def capture(timeline, root, *, nseg=8, draft=False, gpu=False, encoder='x264',
            probe=None):
    """Capture identities without rendering or writing project state.

    The caller explicitly pins execution options. Windowed media require a
    read-only dimension probe; missing probes become unknown, never guessed.
    Current source-media identity remains Fast (stat + head/tail samples).
    """
    if encoder not in ('x264', 'nvenc') or not isinstance(nseg, int) or nseg < 1:
        raise ValueError('invalid impact execution contract')
    tl = copy.deepcopy(timeline)
    root = Path(root).resolve()
    p = tl['project']
    from .constraints import fps_error
    error = fps_error(p.get('fps'))
    if error:
        raise ValueError(error)
    c = SimpleNamespace(tl=tl, root=root, cache=root / 'cache',
                        fps=int(p['fps']), w=p['width'], h=p['height'], gpu=gpu)
    sources = {s['id']: s for s in tl['sources']}
    c.src_path = lambda sid: root / sources[sid]['path']
    from .media.subtitles_src import resolve_subtitles_src
    resolve_subtitles_src(c)
    if getattr(c, 'subtitles_errors', []):
        raise ValueError('; '.join(c.subtitles_errors))
    nodes, layers, warnings = {}, [], []
    observed_files = {}
    source_ids = {}
    for sid in sources:
        try:
            source_ids[sid] = media_stat_digest(c.src_path(sid))
            key = audio_key(c, sid)
        except OSError:
            source_ids[sid], key = None, None
        nodes[f'audio:{sid}'] = dict(stage='prepare', key=key,
            artifact=f'cache/audio/{key}.wav' if key else None,
            inputs={'source': sources[sid]['path'], 'identity': source_ids[sid]},
            parents=[])
    for ti, track in enumerate(tl['video_tracks']):
        for ci, clip in enumerate(track['clips']):
            ident = f'clip:{ti}:{ci}'
            gfx = track.get('kind') == 'gfx'
            inputs = {'clip': clip, 'canvas': [c.fps, c.w, c.h]}
            x, y = clip.get('x', 0), clip.get('y', 0)
            w2 = h2 = None
            try:
                if not gfx and 'cache_dependencies' in clip:
                    raise ValueError('clip.cache_dependencies is only valid on gfx tracks')
                if gfx:
                    from . import gfx_svg
                    if gfx_svg.is_svg(clip):
                        svg_record = gfx_svg.resolve(c, clip)
                        dependencies = {str(root / path): value for path, value in svg_record['files'].items()}
                        dependency_record = {'closure': 'static-svg-explicit'}
                        inputs.update(svg_sequence=svg_record, dependencies=dependencies)
                    else:
                        from .gfx_dependencies import resolve
                        dependency_record = resolve(c, clip)
                        dependencies = {entry['path']: entry['sha256']
                                        for entry in dependency_record['dependencies']}
                        inputs.update(html=dependency_record['html_sha256'],
                                      dependencies=dependencies,
                                      dependency_record=dependency_record)
                        observed_files[root / clip['src']] = inputs['html']
                    for path, value in dependencies.items():
                        observed_files[Path(path)] = value
                    from .frame_source import png_source
                    source = png_source(c, clip)
                    key = source.directory.name
                    artifact = str(source.directory.relative_to(root))
                    upstream = source.identity
                    # Declared closure is not runtime proof of dynamic JS/network.
                    if dependency_record['closure'] == 'unproven':
                        warnings.append({'node': ident, 'reason': 'gfx_dependency_closure_unproven'})
                else:
                    windowed = any(k in clip for k in ('scale', 'x', 'y'))
                    alpha = bool(clip.get('opacity', 1) < 1 or
                                 clip.get('fade_in_frames') or clip.get('fade_out_frames'))
                    if windowed:
                        if probe is None:
                            raise ValueError('windowed media dimensions unavailable')
                        dims = probe(c.src_path(clip['src']))
                        w2 = int(dims['w'] * clip.get('scale', 1.0)) // 2 * 2
                        h2 = int(dims['h'] * clip.get('scale', 1.0)) // 2 * 2
                    else:
                        x = y = 0
                    inputs['source'] = source_ids[clip['src']]
                    key = seg_key(c, clip, ti, ci, windowed, alpha,
                                  'prores' if alpha else encoder)
                    upstream = key + ('.mov' if alpha else '.mp4')
                    artifact = 'cache/seg/' + upstream
            except (OSError, ValueError, KeyError, RuntimeError) as error:
                key = artifact = upstream = None
                warnings.append({'node': ident, 'reason': 'identity_unavailable',
                                 'detail': str(error)})
            nodes[ident] = dict(stage='gfx' if gfx else 'video', key=key,
                                artifact=artifact, inputs=inputs, parents=[])
            layers.append((ident, clip, upstream, x, y, w2, h2))
    f = .5 if draft else 1
    width, height = (((c.w // 2) // 2 * 2, (c.h // 2) // 2 * 2)
                     if draft else (c.w, c.h))
    plan = RenderPlan(c, draft, f, width, height, nseg=nseg)
    graph = _graph_code_digest()

    def add_segment_nodes(layer_set, tag, with_subs, prefix=''):
        # Match each render_segmented invocation independently: QC only sees
        # the bottom track, so its cutpoints can differ from the main graph.
        bounds = [(cl['dst_in'], cl['dst_in'] + cl['dur']) for _, cl, *_ in layer_set]
        with contextlib.redirect_stdout(io.StringIO()):
            cuts = choose_cutpoints(bounds, p['duration_frames'], nseg)
        edges = [0] + cuts + [p['duration_frames']]
        for i, (a, b) in enumerate(zip(edges, edges[1:])):
            active = [L for L in layer_set if L[1]['dst_in'] < b and
                      L[1]['dst_in'] + L[1]['dur'] > a]
            ordered = [(up, cl, x, y, w, h) for _, cl, up, x, y, w, h in active]
            subtitle_bytes = plan.segment_ass_bytes(a, b) if with_subs else b''
            key = (segment_content_key(a, b, width, height, draft, gpu, ordered,
                                       subtitle_bytes, graph)
                   if all(L[2] is not None for L in active) else None)
            nodes[f'{prefix}vseg:{i}'] = dict(stage='video', key=key,
                artifact=f'cache/{tag}/seg{i:02d}_{a:06d}_{key}.mp4' if key else None,
                inputs={'range': [a, b], 'layers': ordered,
                        'subtitles': hashlib.sha256(subtitle_bytes).hexdigest(),
                        'canvas': [width, height, draft, gpu], 'graph': graph},
                parents=[L[0] for L in active])
            previous = f"{a}|{b}|{width}x{height}|{int(bool(draft))}|{int(bool(with_subs))}"
            if len(active) > 1 and key is not None:
                for k, layer in enumerate(ordered[:-1]):
                    previous = pass_content_key(previous, k, layer, graph)
                    nodes[f'{prefix}pass:{i}:{k}'] = dict(stage='video', key=previous,
                        artifact=f'cache/vseg_pass/p{k}_{previous}.mkv',
                        inputs={'range': [a, b], 'layers': ordered[:k + 1],
                                'canvas': [width, height, draft], 'with_subs': with_subs,
                                'graph': graph},
                        parents=[L[0] for L in active[:k + 1]])

    if nseg == 1:
        warnings.append({'node': 'video', 'reason': 'unsegmented_runtime_has_no_vseg_cache'})
    else:
        add_segment_nodes(layers, 'vseg_draft' if draft else 'vseg', True)
        tracks = tl['video_tracks']
        if not draft and tracks and tracks[0].get('kind') == 'gfx':
            bottom = [L for L in layers if L[0].startswith('clip:0:')]
            add_segment_nodes(bottom, 'vseg_qc', False, 'qc:')
    from .pluginlib.hooks import active_plugins_digest
    if active_plugins_digest(c):
        warnings.extend({'node': node, 'reason': 'active_plugin_dependencies_unmodeled'}
                        for node in nodes)
    audio_refs = {e['src'] for t in tl.get('audio_tracks', []) for e in t.get('events', [])}
    for path, digest in observed_files.items():
        if file_digest(path) != digest:
            raise ValueError(f'input changed during impact capture: {path}')
    for sid, digest in source_ids.items():
        if digest is not None and media_stat_digest(c.src_path(sid)) != digest:
            raise ValueError(f'source changed during impact capture: {sid}')
    return {'schema': SCHEMA, 'root': str(root), 'nodes': nodes,
            'execution': {'nseg': nseg, 'draft': draft, 'gpu': gpu, 'encoder': encoder},
            'audio_mix': _digest([tl.get('audio_tracks', []), p['duration_frames'],
                                  {s: source_ids.get(s) for s in sorted(audio_refs)}]),
            'warnings': warnings,
            'limitations': ['source_media_fast_sampled_identity_not_verified',
                            'cache_product_integrity_and_presence_not_checked',
                            'plugin_outputs_not_modeled',
                            'runtime_options_must_match_snapshot_execution',
                            'inputs_must_remain_unchanged_during_capture']}


def compare(before, after):
    """Machine-readable changed nodes, reasons and exact new cache paths.

    `new_artifacts` excludes a changed node whose key already belonged to any
    old node. `conservative_nodes` are uncertainty, not asserted actual misses.
    """
    if before.get('schema') != SCHEMA or after.get('schema') != SCHEMA:
        raise ValueError('unsupported impact snapshot schema')
    if before['root'] != after['root']:
        raise ValueError('impact snapshots must refer to the same project root')
    old, new = before['nodes'], after['nodes']
    changed = []
    for node in sorted(set(old) | set(new)):
        a, b = old.get(node), new.get(node)
        reasons = []
        if a is None:
            reasons.append('node_added')
        elif b is None:
            reasons.append('node_removed')
        elif a['key'] is None or b['key'] is None:
            reasons.append('identity_unavailable')
        elif a['key'] != b['key'] or a['artifact'] != b['artifact']:
            reasons = ['input_changed:' + k for k in sorted(set(a['inputs']) | set(b['inputs']))
                       if a['inputs'].get(k) != b['inputs'].get(k)] or ['execution_identity_changed']
        if reasons:
            changed.append({'node': node, 'stage': (b or a)['stage'],
                            'old_key': a['key'] if a else None,
                            'new_key': b['key'] if b else None, 'reasons': reasons})
    old_paths = {v['artifact'] for v in old.values() if v['artifact']}
    new_paths = {v['artifact'] for v in new.values() if v['artifact']}
    uncertain = {w['node'] for snapshot in (before, after) for w in snapshot['warnings']}
    for node, value in new.items():
        if any(p in uncertain for p in value['parents']):
            uncertain.add(node)
    stages = {n['stage'] for n in changed}
    audio_changed = before['audio_mix'] != after['audio_mix']
    if audio_changed:
        stages.add('audio')
    if 'gfx' in stages:
        stages.add('video')
    if stages & {'video', 'audio'}:
        stages.update(('mux', 'qc'))
    uncertainties = [w for snapshot in (before, after) for w in snapshot['warnings']]
    return {'schema': 'framivra-impact-v1', 'affected_nodes': changed,
            'new_artifacts': sorted(new_paths - old_paths),
            'retired_artifacts': sorted(old_paths - new_paths),
            'conservative_nodes': sorted(uncertain),
            'audio_mix_changed': audio_changed,
            'suggested_stages': [s for s in ('prepare', 'gfx', 'video', 'audio', 'mux', 'qc')
                                 if s in stages],
            'uncertainties': list({json.dumps(w, sort_keys=True): w for w in uncertainties}.values()),
            'limitations': sorted(set(before['limitations'] + after['limitations'])),
            'basis': 'input identities; assumes complete warm artifacts for the before snapshot'}
