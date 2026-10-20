#!/usr/bin/env python3
"""Render original material/light/mesh MG frames inside Blender (no video encoder).

Invoke with Blender --background --python this_file -- --content data.json
--output NEW_DIRECTORY. Writes scene.blend, frames/f000000.png and evidence.json.
The saved scene contains baked integer-frame transforms and can seek independently.
"""
import argparse
from copy import deepcopy
from types import SimpleNamespace
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
    parser.add_argument('--samples', type=int, default=256)
    parser.add_argument('--engine', choices=('CYCLES','BLENDER_EEVEE_NEXT'), default='CYCLES')
    parser.add_argument('--render-frames', help='Comma-separated integer keyframes for a partial preview; does not claim a complete sequence')
    parser.add_argument('--denoise', action='store_true', help='Require OpenImageDenoise support; errors are fatal')
    parser.add_argument('--font', type=Path, help='Explicit separately licensed Blender-readable font')
    return parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])


def validate_3d_content(data, frames=240, samples=256, width=1920, height=1080, engine='CYCLES'):
    """Normalize and validate semantic content without bpy, I/O or writes."""
    args=SimpleNamespace(frames=frames,samples=samples,width=width,height=height)
    if any(type(value) is not int for value in (frames,samples,width,height)):
        raise ValueError('frames, samples, width and height must be integers')
    if engine not in ('CYCLES','BLENDER_EEVEE_NEXT'):
        raise ValueError('unsupported render engine')
    if not 3 <= args.frames <= 18000:
        raise ValueError('frames must be in [3, 18000]')
    if not 64 <= args.width <= 3840 or not 64 <= args.height <= 2160:
        raise ValueError('width/height outside supported render bounds')
    if not 1 <= args.samples <= 256:
        raise ValueError('samples must be in [1, 256]')
    if not isinstance(data, dict):
        raise ValueError('content must be an object')
    data=deepcopy(data)
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
    story = data.get('scene_story', [
        {'frame':0,'title':data.get('subtitle',''),'layout':'scatter'},
        {'frame':beats['settle'],'title':'Connect','layout':'assemble'},
        {'frame':beats['resolve'],'title':'Together','layout':'result'},
    ])
    if not isinstance(story,list) or not 2 <= len(story) <= 6:
        raise ValueError('scene_story needs 2 to 6 chapters')
    previous = -1
    for chapter in story:
        if not isinstance(chapter,dict) or set(chapter) != {'frame','title','layout'}:
            raise ValueError('each chapter needs frame, title and layout')
        if type(chapter['frame']) is not int or not previous < chapter['frame'] < args.frames:
            raise ValueError('chapter frames must be strictly increasing integer frames')
        if not isinstance(chapter['title'],str) or len(chapter['title']) > 100:
            raise ValueError('chapter title must be text with at most 100 characters')
        if chapter['layout'] not in ('scatter','assemble','result'):
            raise ValueError('chapter layout must be scatter, assemble or result')
        previous = chapter['frame']
    if story[0]['frame'] != 0:
        raise ValueError('first chapter must begin at frame zero')
    links = data.get('links', [[i,i+1] for i in range(len(items)-1)])
    if not isinstance(links,list) or len(links)>15:
        raise ValueError('links must contain at most 15 object index pairs')
    seen = set()
    for edge in links:
        if not isinstance(edge,list) or len(edge)!=2 or any(type(v) is not int or not 0<=v<len(items) for v in edge) or edge[0]==edge[1]:
            raise ValueError('links need two distinct valid object indices')
        canonical = tuple(sorted(edge))
        if canonical in seen:
            raise ValueError('duplicate links are not allowed')
        seen.add(canonical)
    transition_frames=data.get('transition_frames',min(24,max(1,args.frames//8)))
    if type(transition_frames) is not int or not 1<=transition_frames<args.frames:
        raise ValueError('transition_frames must be a positive integer less than frames')
    for index,chapter in enumerate(story):
        end=story[index+1]['frame'] if index+1<len(story) else frames
        # The transition reaches progress=1 at start+transition_frames. Since
        # chapter windows exclude end, that frame must still belong to the chapter.
        if end-chapter['frame'] <= transition_frames:
            raise ValueError('each chapter must include its complete transition and at least one settled frame')
    data.update(palette=palette, pace=pace, objects=items, beats=beats, transition=transition, scene=scene_data,
                scene_story=story,links=links,transition_frames=transition_frames,duration_frames=frames)
    return data


def requested_frames(selection,frames):
    if selection is None:
        return list(range(frames))
    result=[int(value) for value in selection.split(',')]
    if not result or len(set(result))!=len(result) or any(not 0<=frame<frames for frame in result):
        raise ValueError('render-frames must be unique frame indices within the clip')
    return result


def validate(args):
    data=validate_3d_content(json.loads(args.content.read_text(encoding='utf-8')),args.frames,args.samples,args.width,args.height,args.engine)
    requested_frames(args.render_frames,args.frames)
    if args.output.exists():
        raise ValueError('output must be a new directory')
    return data



def ease(value):
    value = max(0,min(1,value))
    return value*value*(3-2*value)


def camera_state(data, frame):
    """Actual orbit/elevation and look-at target, evaluated from one integer frame."""
    phase=frame/max(1,data['duration_frames']-1)
    azimuth=.045*math.sin(phase*math.tau)
    resolve=ease((frame-data['beats']['resolve'])/max(1,data['duration_frames']-1-data['beats']['resolve']))
    return {'location':(17*math.sin(azimuth),-17*math.cos(azimuth),10+.35*math.sin(phase*math.pi)),
            'target':(.2*math.sin(phase*math.tau),0,1.5),
            'ortho_scale':14+(.8*resolve if data['transition']=='camera-pullback' else 0)}


def story_state(data, frame):
    """Pure, seekable chapter geometry; pace > 1 delays a transition."""
    if type(frame) is not int or not 0<=frame<data['duration_frames']:
        raise ValueError('frame must be an integer inside the clip')
    story=data['scene_story']
    chapter_index=max(i for i,chapter in enumerate(story) if chapter['frame']<=frame)
    chapter=story[chapter_index]
    previous=story[max(0,chapter_index-1)]
    progress=ease(max(0,min(1,(frame-chapter['frame'])/data['transition_frames']))**data['pace'])
    count=len(data['objects'])
    spacing=min(3.1,10/max(1,count))
    positions=[]
    scales=[]
    def position(layout,index,radius):
        x=(index-(count-1)/2)*spacing
        scene_y=.12*x*x if data['scene']['layout']=='arc' else 0
        if layout=='scatter':
            return (x+math.sin(index*2.1)*.38, (-1 if index%2 else 1)*.9+scene_y, radius+.25+.35*(index%2))
        if layout=='assemble':
            return (x,scene_y,radius)
        return (x*.82, .3*math.cos(index*1.7)+scene_y, radius+.55+.9*math.sin((index+.5)/count*math.pi))
    for index,item in enumerate(data['objects']):
        radius=min(.86,spacing*.35)*(.48+.52*math.sqrt(item['value']/100))
        start=position(previous['layout'],index,radius)
        end=position(chapter['layout'],index,radius)
        # Pulse only during a layout transition; XY/Z scales preserve volume.
        pulse=math.sin(math.tau*progress)*math.sin(math.pi*progress)
        stretch=1+.2*pulse
        scale=(stretch,stretch,1/(stretch*stretch))
        center=list(a+(b-a)*progress for a,b in zip(start,end))
        center[2]+=radius*(scale[2]-1)
        positions.append(tuple(center))
        scales.append(scale)
    connection_start=story[1]['frame']
    connections=ease(max(0,min(1,(frame-connection_start)/data['transition_frames']))**data['pace'])
    return {'chapter':chapter_index,'progress':progress,'positions':positions,'scales':scales,'connections':connections,'camera':camera_state(data,frame)}


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
    scene.render.engine = args.engine
    scene.cycles.device = 'CPU'
    scene.cycles.samples = args.samples
    scene.cycles.use_denoising = args.denoise
    scene.cycles.seed = 0
    scene.cycles.use_animated_seed = False
    scene.cycles.max_bounces = 6
    scene.cycles.diffuse_bounces = 2
    scene.cycles.glossy_bounces = 3
    if args.engine == 'BLENDER_EEVEE_NEXT':
        scene.eevee.taa_render_samples = args.samples
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
    all_text = data.get('title','') + data.get('subtitle','') + ''.join(item['label'] for item in data['objects']) + ''.join(c['title'] for c in data['scene_story'])
    if font is None and any(ord(c)>127 for c in all_text):
        raise ValueError('non-ASCII labels require --font or installed Noto CJK')
    bpy.ops.mesh.primitive_plane_add(size=200)
    floor = bpy.context.object
    floor.name = 'Shadow receiving ground'
    floor.data.materials.append(floor_mat)

    bpy.ops.object.camera_add(location=(0, -17, 10))
    camera = bpy.context.object
    camera.name = 'Composition camera'
    camera.rotation_euler = (Vector((0,0,1.5)) - camera.location).to_track_quat('-Z','Y').to_euler()
    camera.data.type = 'ORTHO'
    camera.data.ortho_scale = 14
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

    # Camera-space typography has a dedicated safe area, independent of geometry.
    ui_records=[]
    aspect=args.width/args.height
    def screen_point(x,y):
        rotation=camera.rotation_euler.to_quaternion()
        right=rotation @ Vector((1,0,0))
        up=rotation @ Vector((0,1,0))
        forward=rotation @ Vector((0,0,-1))
        span=camera.data.ortho_scale
        return camera.location + forward*8 + right*((x-.5)*span) + up*((y-.5)*span/aspect)
    ui_material=bpy.data.materials.new('Camera-facing typography')
    ui_material.use_nodes=True
    nodes=ui_material.node_tree.nodes
    nodes.clear()
    emission=nodes.new('ShaderNodeEmission')
    emission.inputs['Color'].default_value=rgba(palette[1])
    emission.inputs['Strength'].default_value=1
    output=nodes.new('ShaderNodeOutputMaterial')
    ui_material.node_tree.links.new(emission.outputs[0],output.inputs['Surface'])
    def ui_text(body,x,y,size,name,max_width):
        obj=text(body,screen_point(x,y),size,name,max_width)
        obj.data.materials.clear()
        obj.data.materials.append(ui_material)
        obj.visible_shadow=False
        obj.visible_glossy=False
        obj.visible_diffuse=False
        ui_records.append((obj,x,y,tuple(obj.scale)))
        return obj
    title=ui_text(data.get('title',''),.5,.91,1.15,'Editable title',12)
    chapter_text=[]
    for i,chapter in enumerate(data['scene_story']):
        chapter_text.append(ui_text(chapter['title'],.5,.79,.65,'Chapter %02d'%i,12))
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
        slot=(i+.5)/count
        ui_text(item['label'],slot,.085,.58,'Editable label %02d'%i,12/count*.9)
        ui_text(str(item['value']),slot,.18,.52,'Editable value %02d'%i,12/count*.8)
        objects.append((obj, x, y, radius, i, tuple(obj.rotation_euler)))

    rods=[]
    link_material=material('Relationship rods',palette[1],.4,.1)
    for i,edge in enumerate(data['links']):
        bpy.ops.mesh.primitive_cylinder_add(vertices=20,radius=.035,depth=1)
        rod=bpy.context.object
        rod.name='Relationship %02d'%i
        rod.data.materials.append(link_material)
        rods.append((rod,edge))
    # Bake every requested integer frame. No simulation, prior-state integration,
    # callbacks or wall-clock animation is involved.
    for frame in range(args.frames):
        state=story_state(data,frame)
        camera.location=state['camera']['location']
        camera.rotation_euler=(Vector(state['camera']['target'])-camera.location).to_track_quat('-Z','Y').to_euler()
        camera.data.ortho_scale=state['camera']['ortho_scale']
        camera.keyframe_insert(data_path='location',frame=frame)
        camera.keyframe_insert(data_path='rotation_euler',frame=frame)
        camera.data.keyframe_insert(data_path='ortho_scale',frame=frame)
        for ui,x,y,base_scale in ui_records:
            ui.location=screen_point(x,y)
            ui.rotation_euler=camera.rotation_euler
            ui.scale=Vector(base_scale)*(camera.data.ortho_scale/14)
            for prop in ('location','rotation_euler','scale'):
                ui.keyframe_insert(data_path=prop,frame=frame)
        for obj,x,y,radius,i,base_rotation in objects:
            obj.location=state['positions'][i]
            obj.scale=state['scales'][i]
            obj.keyframe_insert(data_path='scale',frame=frame)
            obj.rotation_euler=(base_rotation[0],base_rotation[1]+.1*math.sin(frame/args.frames*math.pi),base_rotation[2])
            obj.keyframe_insert(data_path='location',frame=frame)
            obj.keyframe_insert(data_path='rotation_euler',frame=frame)
        for rod,edge in rods:
            start=Vector(state['positions'][edge[0]])
            end=Vector(state['positions'][edge[1]])
            end=start+(end-start)*state['connections']
            delta=end-start
            rod.location=(start+end)/2
            rod.rotation_euler=delta.to_track_quat('Z','Y').to_euler() if delta.length>1e-8 else (0,0,0)
            rod.scale=(1,1,max(.0001,delta.length))
            for prop in ('location','rotation_euler','scale'):
                rod.keyframe_insert(data_path=prop,frame=frame)
        for index,obj in enumerate(chapter_text):
            obj.hide_render=index!=state['chapter']
            obj.keyframe_insert(data_path='hide_render',frame=frame)
    for obj in bpy.data.objects:
        if obj.animation_data and obj.animation_data.action:
            for curve in obj.animation_data.action.fcurves:
                for point in curve.keyframe_points:
                    point.interpolation='CONSTANT'
    scene.frame_set(0)
    scene.render.filepath = str(frame_dir / 'f000000.png')
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / 'scene.blend'))
    evidence = {
        'blender': bpy.app.version_string, 'engine': args.engine, 'device':'CPU' if args.engine=='CYCLES' else 'unknown',
        'samples':args.samples, 'denoising':args.denoise, 'fps':30, 'frames':args.frames,
        'width':args.width, 'height':args.height,
        'animation':'per_frame_3d_layout_volume_preserving_scale_and_camera_orbit',
        'ui_binding':'camera_space_position_rotation_scale_baked_per_frame',
        'scene_story':data['scene_story'], 'links':data['links'], 'scene_layout':data['scene']['layout'], 'beats':data['beats'], 'transition':data['transition'],
        'font':str(font_path) if font_path else 'Blender built-in',
        'font_license':'unknown; verify the external font separately; no font bytes embedded',
        'font_embedded':False,
        'font_sha256':hashlib.sha256(font_path.read_bytes()).hexdigest() if font_path else None,
        'content_sha256':hashlib.sha256(args.content.read_bytes()).hexdigest(),
        'scene_sha256':hashlib.sha256((args.output/'scene.blend').read_bytes()).hexdigest(),
        'limitations':['Font licensing remains separate; selected font path/hash recorded, glyph coverage must be visually checked.',
                        'No soundtrack or encoded movie; integrate this sequence through a vtx timeline.',
                        'Fixed-environment determinism only; no cross-device pixel equivalence claim.'],
        'frame_sha256':[],
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    rendered_frames=requested_frames(args.render_frames,args.frames)
    evidence['rendered_frames']=rendered_frames
    evidence['complete_sequence']=rendered_frames==list(range(args.frames))
    for frame in rendered_frames:
        scene.frame_set(frame)
        image = frame_dir / ('f%06d.png' % frame)
        scene.render.filepath = str(image)
        try:
            bpy.ops.render.render(write_still=True)
        except RuntimeError as error:
            (args.output / 'failure.json').write_text(json.dumps({'frame':frame,'error':str(error),'engine':args.engine,'denoising':args.denoise},indent=2)+'\n',encoding='utf-8')
            raise
        evidence['frame_sha256'].append(hashlib.sha256(image.read_bytes()).hexdigest())
    (args.output / 'evidence.json').write_text(json.dumps(evidence, indent=2)+'\n', encoding='utf-8')
    print('MG_3D_RENDER_OK ' + str(args.output / 'evidence.json'))


if __name__ == '__main__':
    main()
