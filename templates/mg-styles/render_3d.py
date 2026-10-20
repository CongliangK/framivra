#!/usr/bin/env python3
"""Render original material/light/mesh MG frames inside Blender (no video encoder).

Invoke with Blender --background --python this_file -- --content data.json
--output NEW_DIRECTORY. Writes scene.blend, frames/f000000.png and evidence.json.
The saved scene contains baked integer-frame transforms and can seek independently.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--content', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--frames', type=int, default=30)
    parser.add_argument('--width', type=int, default=640)
    parser.add_argument('--height', type=int, default=360)
    parser.add_argument('--samples', type=int, default=128)
    parser.add_argument('--denoise', action='store_true', help='Require OpenImageDenoise support; errors are fatal')
    parser.add_argument('--font', type=Path, help='Explicit separately licensed Blender-readable font')
    return parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])


def validate(args):
    if not 3 <= args.frames <= 18000:
        raise ValueError('frames must be in [3, 18000]')
    if not 64 <= args.width <= 3840 or not 64 <= args.height <= 2160:
        raise ValueError('width/height outside supported render bounds')
    if not 1 <= args.samples <= 256:
        raise ValueError('samples must be in [1, 256]')
    data = json.loads(args.content.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('content must be an object')
    for key in ('title', 'subtitle'):
        value = data.get(key, '')
        if not isinstance(value, str) or len(value) > 180:
            raise ValueError(key + ' must be text with at most 180 characters')
    items = data.get('objects', data.get('items'))
    if not isinstance(items, list) or not 1 <= len(items) <= 6:
        raise ValueError('items must contain 1 to 6 entries')
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('label'), str) or len(item['label']) > 80:
            raise ValueError('each item needs a label with at most 80 characters')
        if type(item.get('value')) not in (float, int) or not 0 <= item['value'] <= 100:
            raise ValueError('item value must be finite in [0,100]')
    palette = data.get('palette', ['#faf5e8', '#202730', '#e66b42', '#49a69d'])
    if not isinstance(palette, list) or len(palette) != 4 or any(not isinstance(c, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', c) for c in palette):
        raise ValueError('palette must contain four #RRGGBB colors')
    pace = data.get('pace', 1)
    if type(pace) not in (int, float) or not .5 <= pace <= 2:
        raise ValueError('pace must be finite in [.5,2]')
    for item in items:
        if item.get('shape', 'sphere') not in ('sphere','cube','torus'):
            raise ValueError('shape must be sphere, cube or torus')
        if item.get('material','ceramic') not in ('ceramic','metal','matte'):
            raise ValueError('material must be ceramic, metal or matte')
        roughness = item.get('roughness', .3)
        if type(roughness) not in (int,float) or not 0 <= roughness <= 1:
            raise ValueError('roughness must be finite in [0,1]')
    beats = data.get('beats', {'reveal':0,'settle':max(1,args.frames//3),'resolve':max(2,args.frames*4//5)})
    if not isinstance(beats,dict) or set(beats) != {'reveal','settle','resolve'} or any(type(v) is not int for v in beats.values()) or not 0 <= beats['reveal'] < beats['settle'] < beats['resolve'] < args.frames:
        raise ValueError('beats need ordered integer reveal < settle < resolve < frames')
    transition = data.get('transition','camera-pullback')
    if transition not in ('hold','camera-pullback'):
        raise ValueError('transition must be hold or camera-pullback')
    scene_data = data.get('scene', {'layout':'stage'})
    if not isinstance(scene_data,dict) or set(scene_data) != {'layout'} or scene_data['layout'] not in ('stage','arc'):
        raise ValueError('scene needs layout stage or arc')
    data.update(palette=palette, pace=pace, objects=items, beats=beats, transition=transition, scene=scene_data)
    if args.output.exists():
        raise ValueError('output must be a new directory')
    return data


def main():
    args = arguments()
    data = validate(args)
    import bpy
    from mathutils import Vector

    args.output.mkdir(parents=True)
    frame_dir = args.output / 'frames'
    frame_dir.mkdir()
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = args.samples
    scene.cycles.use_denoising = args.denoise
    scene.cycles.seed = 0
    scene.cycles.use_animated_seed = False
    scene.render.threads_mode = 'FIXED'
    scene.render.threads = 4
    scene.render.resolution_x = args.width
    scene.render.resolution_y = args.height
    scene.render.resolution_percentage = 100
    scene.render.fps = 30
    scene.frame_start = 0
    scene.frame_end = args.frames - 1
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGB'
    scene.render.image_settings.color_depth = '8'
    scene.render.film_transparent = False
    scene.view_settings.view_transform = 'AgX'
    scene.world.use_nodes = True
    scene.world.node_tree.nodes['Background'].inputs['Color'].default_value = (.8, .8, .8, 1)
    scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value = .22

    def rgba(hex_color):
        def linear(v):
            return v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4
        return tuple(linear(int(hex_color[i:i+2], 16) / 255) for i in (1,3,5)) + (1,)

    def material(name, color, roughness=.35, metal=0):
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        shader = mat.node_tree.nodes['Principled BSDF']
        shader.inputs['Base Color'].default_value = rgba(color)
        shader.inputs['Roughness'].default_value = roughness
        shader.inputs['Metallic'].default_value = metal
        return mat

    palette = data['palette']
    floor_mat = material('Matte cyclorama', palette[0], .8)
    ink_mat = material('Extruded type ink', palette[1], .5)
    font_path = args.font
    if font_path is None:
        candidate = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc')
        if candidate.is_file():
            font_path = candidate
    font = bpy.data.fonts.load(str(font_path)) if font_path else None
    all_text = data.get('title','') + data.get('subtitle','') + ''.join(item['label'] for item in data['objects'])
    if font is None and any(ord(c)>127 for c in all_text):
        raise ValueError('non-ASCII labels require --font or installed Noto CJK')
    bpy.ops.mesh.primitive_plane_add(size=200)
    floor = bpy.context.object
    floor.name = 'Shadow receiving ground'
    floor.data.materials.append(floor_mat)

    bpy.ops.object.camera_add(location=(7, -15, 10))
    camera = bpy.context.object
    camera.name = 'Composition camera'
    camera.rotation_euler = (Vector((0,0,2)) - camera.location).to_track_quat('-Z','Y').to_euler()
    camera.data.type = 'ORTHO'
    camera.data.ortho_scale = 15
    scene.camera = camera

    for name, position, energy, size in (
        ('Key softbox', (-5,-6,10), 1800, 7),
        ('Rim softbox', (4,4,8), 1400, 5),
        ('Fill softbox', (6,-4,5), 700, 5),
    ):
        bpy.ops.object.light_add(type='AREA', location=position)
        light = bpy.context.object
        light.name = name
        light.data.energy = energy
        light.data.shape = 'DISK'
        light.data.size = size
        light.rotation_euler = (-light.location).to_track_quat('-Z','Y').to_euler()

    def text(body, location, size, name, max_width):
        curve = bpy.data.curves.new(name, 'FONT')
        curve.body = body
        if font is not None:
            curve.font = font
        curve.align_x = 'CENTER'
        curve.align_y = 'CENTER'
        curve.size = size
        curve.extrude = .012
        curve.bevel_depth = .003
        obj = bpy.data.objects.new(name, curve)
        scene.collection.objects.link(obj)
        obj.location = location
        obj.rotation_euler = camera.rotation_euler
        obj.data.materials.append(ink_mat)
        bpy.context.view_layer.update()
        if obj.dimensions.x > max_width:
            obj.scale *= max_width / obj.dimensions.x
        return obj

    title = text(data.get('title',''), (0,0,5.05), 1.05, 'Editable title', 10)
    subtitle = text(data.get('subtitle',''), (0,0,4.15), .42, 'Editable subtitle', 10)
    objects = []
    count = len(data['objects'])
    spacing = min(3.2, 10 / count)
    for i, item in enumerate(data['objects']):
        x = (i - (count-1)/2) * spacing
        y = .18*x*x if data['scene']['layout'] == 'arc' else 0
        radius = min(.86, spacing*.35) * (.48 + .52*math.sqrt(item['value']/100))
        shape = item.get('shape', 'cube' if i%2 else 'sphere')
        if shape == 'cube':
            bpy.ops.mesh.primitive_cube_add(size=2*radius, location=(x,y,radius))
            obj = bpy.context.object
            modifier = obj.modifiers.new('Rounded ceramic edges', 'BEVEL')
            modifier.width = radius*.25
            modifier.segments = 5
            obj.modifiers.new('Weighted corner normals', 'WEIGHTED_NORMAL')
        elif shape == 'torus':
            bpy.ops.mesh.primitive_torus_add(major_segments=48,minor_segments=16,major_radius=radius*.7,minor_radius=radius*.3, location=(x,y,radius), rotation=(math.pi/2,0,0))
            obj = bpy.context.object
            for face in obj.data.polygons:
                face.use_smooth = True
        else:
            bpy.ops.mesh.primitive_uv_sphere_add(segments=40, ring_count=24, radius=radius, location=(x,y,radius))
            obj = bpy.context.object
            for face in obj.data.polygons:
                face.use_smooth = True
        obj.name = 'Value solid %02d' % i
        kind = item.get('material','ceramic')
        obj.data.materials.append(material('Object material %02d' % i,palette[2+i%2],item.get('roughness', .8 if kind=='matte' else .18 if kind=='metal' else .3), .85 if kind=='metal' else .05))
        text(item['label'], (x,-2.8,.1), .43, 'Editable label %02d' % i, spacing*.9)
        text(str(item['value']), (x,-2,.1), .45, 'Editable value %02d' % i, spacing*.8)
        objects.append((obj, x, y, radius, i, tuple(obj.rotation_euler)))

    def ease(value):
        value = max(0, min(1,value))
        return 1-(1-value)**3

    # Bake the analytic state at every integer frame. Seeking requires no handlers,
    # simulation caches, wall clock, prior frame or browser runtime.
    for frame in range(args.frames):
        phase = max(0,min(1,(frame-data['beats']['reveal'])/(data['beats']['settle']-data['beats']['reveal'])))
        phase = phase**data['pace']
        for obj,x,y,radius,i,base_rotation in objects:
            enter = ease(max(0,min(1,(phase-i*.035)/(1-i*.035))))
            hop = math.sin(enter*math.pi) * (1.3+radius)
            squash = 1-.1*math.sin(max(0,(enter-.8)/.2)*math.pi) if enter>.8 else 1
            obj.location = (x,y,radius*squash + hop)
            obj.scale = (1/math.sqrt(squash),1/math.sqrt(squash),squash)
            obj.rotation_euler = (base_rotation[0],base_rotation[1]+(1-enter)*.45,base_rotation[2]+(1-enter)*.2)
            for prop in ('location','scale','rotation_euler'):
                obj.keyframe_insert(data_path=prop, frame=frame)
        resolve = ease((frame-data['beats']['resolve'])/max(1,args.frames-1-data['beats']['resolve']))
        camera.data.ortho_scale = 15 + (resolve if data['transition']=='camera-pullback' else 0)
        camera.data.keyframe_insert(data_path='ortho_scale',frame=frame)
        for obj in (title,subtitle):
            obj.keyframe_insert(data_path='location', frame=frame)
    for obj,_,_,_,_,_ in objects:
        for curve in obj.animation_data.action.fcurves:
            for point in curve.keyframe_points:
                point.interpolation = 'CONSTANT'
    scene.frame_set(0)
    if font is not None:
        font.pack()
    scene.render.filepath = str(frame_dir / 'f000000.png')
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / 'scene.blend'))
    evidence = {
        'blender': bpy.app.version_string, 'engine': 'CYCLES', 'device':'CPU',
        'samples':args.samples, 'denoising':args.denoise, 'fps':30, 'frames':args.frames,
        'width':args.width, 'height':args.height,
        'scene_layout':data['scene']['layout'], 'beats':data['beats'], 'transition':data['transition'],
        'font':str(font_path) if font_path else 'Blender built-in',
        'font_sha256':hashlib.sha256(font_path.read_bytes()).hexdigest() if font_path else None,
        'content_sha256':hashlib.sha256(args.content.read_bytes()).hexdigest(),
        'scene_sha256':hashlib.sha256((args.output/'scene.blend').read_bytes()).hexdigest(),
        'limitations':['Font licensing remains separate; selected font path/hash recorded, glyph coverage must be visually checked.',
                        'No soundtrack or encoded movie; integrate this sequence through a vtx timeline.',
                        'Fixed-environment determinism only; no cross-device pixel equivalence claim.'],
        'frame_sha256':[],
    }
    for frame in range(args.frames):
        scene.frame_set(frame)
        image = frame_dir / ('f%06d.png' % frame)
        scene.render.filepath = str(image)
        try:
            bpy.ops.render.render(write_still=True)
        except RuntimeError as error:
            (args.output / 'failure.json').write_text(json.dumps({'frame':frame,'error':str(error),'engine':'CYCLES','denoising':args.denoise},indent=2)+'\n',encoding='utf-8')
            raise
        evidence['frame_sha256'].append(hashlib.sha256(image.read_bytes()).hexdigest())
    (args.output / 'evidence.json').write_text(json.dumps(evidence, indent=2)+'\n', encoding='utf-8')
    print('MG_3D_RENDER_OK ' + str(args.output / 'evidence.json'))


if __name__ == '__main__':
    main()
