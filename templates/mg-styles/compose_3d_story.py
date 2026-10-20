#!/usr/bin/env python3
"""Compose settled, genuinely rendered 3D plates with an explicit 2D wipe.

This is a storyboard plate compositor, not a Blender animation renderer. Output
keeps each plate's native dimensions. Titles and labels are never scaled/cropped.
The complete source-rendering route remains in render_3d.py.
"""
import argparse
import hashlib
from types import ModuleType
import json
from pathlib import Path
import shutil

_RENDERER = globals().get('_RENDERER')
_RENDERER_SHA256 = globals().get('_RENDERER_SHA256')


def bind_renderer(renderer, source_sha256):
    """Bind an already captured renderer module; integrations avoid live imports."""
    if not isinstance(source_sha256,str) or len(source_sha256)!=64 or any(c not in '0123456789abcdef' for c in source_sha256):
        raise ValueError('renderer source SHA256 must be a lowercase hex digest')
    if not all(callable(getattr(renderer,name,None)) for name in ('validate_3d_content','ease')):
        raise ValueError('renderer must provide the semantic validator and ease function')
    global _RENDERER, _RENDERER_SHA256
    _RENDERER, _RENDERER_SHA256 = renderer, source_sha256


def _renderer():
    global _RENDERER, _RENDERER_SHA256
    if _RENDERER is None:
        # Standalone CLI captures one exact byte string, hashes and executes it.
        # No loader rereads a changing dependency after this capture.
        path=Path(__file__).with_name('render_3d.py')
        source=path.read_bytes()
        module=ModuleType('mg_storyboard_captured_renderer')
        module.__file__=str(path)
        exec(compile(source,str(path),'exec'),module.__dict__)
        bind_renderer(module,hashlib.sha256(source).hexdigest())
    if _RENDERER_SHA256 is None:
        raise ValueError('injected renderer requires bind_renderer(module, captured_sha256)')
    return _RENDERER


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def story_plate_indices(data, frames):
    """Return fully settled native Blender frames, one per semantic chapter."""
    normalized = _renderer().validate_3d_content(data, frames=frames)
    story = normalized['scene_story']
    indices = []
    for index, chapter in enumerate(story):
        end = story[index + 1]['frame'] if index + 1 < len(story) else frames
        settled = chapter['frame'] + normalized['transition_frames']
        if settled >= end:
            # The final transition frame is reached at start+duration, not +duration-1.
            raise ValueError('each chapter needs a settled frame after its transition')
        indices.append(settled)
    return indices


def _semantic_signature(data):
    keys = ('title', 'subtitle', 'objects', 'palette', 'pace', 'beats', 'transition',
            'scene', 'scene_story', 'links', 'transition_frames')
    return {key: data.get(key) for key in keys}


def inspect_plates(data, frames, plate_dir):
    """Validate every input before creating the composite output directory."""
    from PIL import Image
    plate_dir = Path(plate_dir)
    evidence_path = plate_dir / 'evidence.json'
    evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
    normalized = _renderer().validate_3d_content(
        data, frames=frames, samples=evidence['samples'],
        width=evidence['width'], height=evidence['height'], engine=evidence['engine'])
    if evidence.get('frames') != frames or evidence.get('fps') != 30:
        raise ValueError('plate scene duration/fps does not match the composition')
    if evidence.get('scene_story') != normalized['scene_story'] or evidence.get('links') != normalized['links']:
        raise ValueError('plate story or relationships do not match the content')
    scene_path = plate_dir / 'scene.blend'
    if digest(scene_path) != evidence.get('scene_sha256'):
        raise ValueError('source scene.blend hash does not match its render evidence')
    # Bind all creative fields to the exact JSON used for rendering. Main media
    # projects keep creative.json beside blender-render; independent plates can
    # carry an exact-byte content.json copy alongside evidence.json.
    content_paths = (plate_dir / 'content.json', plate_dir.parent / 'creative.json')
    source_content = next((path for path in content_paths
                           if path.is_file() and digest(path) == evidence.get('content_sha256')), None)
    if source_content is None:
        raise ValueError('an exact source content.json or sibling creative.json is required')
    rendered_content = _renderer().validate_3d_content(
        json.loads(source_content.read_text(encoding='utf-8')), frames=frames,
        samples=evidence['samples'], width=evidence['width'], height=evidence['height'], engine=evidence['engine'])
    if _semantic_signature(normalized) != _semantic_signature(rendered_content):
        raise ValueError('composition content differs from the rendered creative content')
    rendered_frames = evidence.get('rendered_frames')
    hashes = evidence.get('frame_sha256')
    if not isinstance(rendered_frames, list) or not isinstance(hashes, list) or len(rendered_frames) != len(hashes):
        raise ValueError('plate render evidence needs an explicit frame/hash mapping')
    if any(type(f) is not int for f in rendered_frames) or len(set(rendered_frames)) != len(rendered_frames):
        raise ValueError('source frame mapping contains duplicates or noninteger frames')
    source_hashes = dict(zip(rendered_frames, hashes))
    indices = story_plate_indices(normalized, frames)
    images = []
    plates = []
    for frame in indices:
        if frame not in source_hashes:
            raise ValueError('missing settled source plate frame %d' % frame)
        image_path = plate_dir / 'frames' / ('f%06d.png' % frame)
        actual_hash = digest(image_path)
        if actual_hash != source_hashes[frame]:
            raise ValueError('source plate hash mismatch at frame %d' % frame)
        with Image.open(image_path) as image:
            if image.format != 'PNG' or image.size != (evidence['width'], evidence['height']):
                raise ValueError('plate dimensions/format disagree with native render evidence')
            if image.mode == 'RGBA' and image.getchannel('A').getextrema() != (255, 255):
                raise ValueError('storyboard plates must contain a complete opaque scene')
            images.append(image.convert('RGB'))
        plates.append({'source_frame':frame, 'path':str(image_path.resolve()), 'sha256':actual_hash,
                       'width':evidence['width'], 'height':evidence['height']})
    return normalized, evidence, images, plates, evidence_path, source_content


def compose_3d_story(data, frames, plate_dir, output):
    """Write a complete integer-frame *2D plate composite*, never fake 3D motion."""
    output = Path(output)
    if output.exists():
        raise ValueError('output must be a new directory')
    normalized, source, images, plates, source_evidence, source_content = inspect_plates(data, frames, plate_dir)
    width, height = source['width'], source['height']
    output.mkdir(parents=True)
    target = output / 'frames'
    target.mkdir()
    story = normalized['scene_story']
    duration = normalized['transition_frames']
    provenance = {
        'mode':'storyboard_2d_plate_composite', 'fps':30, 'frames':frames,
        'width':width, 'height':height, 'complete_composite_sequence':True,
        'complete_blender_sequence':False, 'source_render_mode':'selected_native_blender_frames',
        'transition':'deterministic_horizontal_2d_mask_wipe', 'spatial_resampling':False,
        'source_evidence':str(source_evidence.resolve()), 'source_evidence_sha256':digest(source_evidence),
        'source_content':str(source_content.resolve()), 'source_content_sha256':digest(source_content),
        'source_scene_sha256':source['scene_sha256'], 'source_engine':source['engine'],
        'source_samples':source['samples'], 'source_denoising':source.get('denoising',False),
        'source_plates':plates, 'scene_story':story, 'transition_frames':duration,
        'composer_sha256':digest(__file__),
        'renderer_validator_sha256':_RENDERER_SHA256,
        'limitations':['Only the listed plates were rendered in Blender.',
                       'Between plates, motion is a 2D mask wipe followed by a held image.',
                       'No 3D camera interpolation, collision simulation or per-frame Blender rendering is claimed.',
                       'Plate sampling noise is retained; no denoising or resolution upscaling is applied.'],
        'frame_map':[], 'frame_sha256':[],
    }
    for frame in range(frames):
        chapter = max(index for index, item in enumerate(story) if item['frame'] <= frame)
        local = frame - story[chapter]['frame']
        destination = target / ('f%06d.png' % frame)
        mapping = {'frame':frame, 'chapter':chapter, 'source_frame':plates[chapter]['source_frame']}
        if chapter and local < duration:
            progress = _renderer().ease(min(1,(local+1)/duration) ** normalized['pace'])
            split = round(width * progress)
            composed = images[chapter-1].copy()
            if split:
                composed.paste(images[chapter].crop((0,0,split,height)), (0,0))
            composed.save(destination)
            mapping.update(outgoing_source_frame=plates[chapter-1]['source_frame'], wipe_pixels=split)
        else:
            # Copying preserves the plate exactly; all held-frame provenance stays explicit.
            shutil.copyfile(plates[chapter]['path'], destination)
            mapping['wipe_pixels'] = width
        provenance['frame_map'].append(mapping)
        provenance['frame_sha256'].append(digest(destination))
    (output / 'evidence.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--content', type=Path, required=True)
    parser.add_argument('--frames', type=int, default=240)
    parser.add_argument('--plates', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    compose_3d_story(json.loads(args.content.read_text(encoding='utf-8')), args.frames, args.plates, args.output)
    print('MG_3D_STORYBOARD_OK ' + str(args.output / 'evidence.json'))


if __name__ == '__main__':
    main()
