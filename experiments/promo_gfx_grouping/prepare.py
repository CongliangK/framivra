#!/usr/bin/env python3
"""Prepare the fixed, unvalidated T08/T07 experiment without rendering.

Only a new output directory is written. The original 109 payload files,
including all seven native-waveform binding changes, are copied unchanged.
The five runtime media files, original cache and out are not copied. This
is a GFX input, not a runnable full-film replacement or acceptance receipt.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).absolute().parent
REPO = HERE.parents[1]
PRODUCTION = REPO / 'templates/promo-demo/v1/production'
SPEC = HERE / 'fixtures/bound-input.json'
PAGE = 'gfx/group_T08_T07_0000_3600.html'
ADAPTER = 'runtime/group-pilot.js'
MEMBERS = [
    {'entryId':'T08.01','layerId':'T08','role':'bird-wings','src':'gfx/T08_0018_3600.html',
     'windowStart':18,'duration':3582,'paintIndex':6},
    {'entryId':'T07.01','layerId':'T07','role':'bird-body-head','src':'gfx/T07_0000_3600.html',
     'windowStart':0,'duration':3600,'paintIndex':7},
]
# Reuse the already-reviewed binder's bounded no-follow reader, exclusive owned
# output and waveform/provenance validators. Do not duplicate these mechanisms.
_spec = importlib.util.spec_from_file_location('group_original_binder', PRODUCTION / 'scripts/bind_native_postmix.py')
BINDER = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BINDER)
require, read, digest = BINDER.require, BINDER.read, BINDER.digest


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def parse(data):
    return json.loads(data)


def verify_payload(project):
    """Pin the reviewed bound snapshot; never execute scripts from the input."""
    spec = parse(read(SPEC))
    payload = {}
    for item in spec['bound_payload']:
        relative = item['path']
        require(not Path(relative).is_absolute() and '..' not in Path(relative).parts, 'Unsafe pinned path')
        # Ensure helper/source dependencies in this checkout still match b287.
        source = read(PRODUCTION / relative)
        require(len(source) == item['original_bytes'] and digest(source) == item['original_sha256'],
                'Checkout production source drift: ' + relative)
        data = read(project / relative)
        require(len(data) == item['bytes'] and digest(data) == item['sha256'],
                'Bound input differs from reviewed native snapshot: ' + relative)
        payload[relative] = data
    require(len(payload) == 109, 'Expected complete original payload')
    timeline, manifest = parse(payload['timeline.json']), parse(payload['entry-manifest.json'])
    require(manifest['timeline_sha256'] == digest(payload['timeline.json']), 'Timeline manifest mismatch')
    require(manifest['bundle_sha256'] == digest(payload['runtime/shared.js']), 'Bound bundle mismatch')
    require(len(manifest['entries']) == 19, 'Expected unchanged 19 original entries')
    for member in MEMBERS:
        require(next((entry for entry in manifest['entries'] if entry['entryId'] == member['entryId']), None) == member,
                'Member entry differs from fixed pair')
        track = timeline['video_tracks'][member['paintIndex']]
        require(track == {'name':member['role'],'kind':'gfx','clips':[
            {'src':member['src'],'dst_in':member['windowStart'],'dur':member['duration']}]},
            'Pair must be adjacent plain GFX overlays without external transforms/blend')
    for dependency in parse(payload['runtime-dependencies.json']):
        require(digest(payload[dependency['path']]) == dependency['sha256'], 'Runtime dependency mismatch')
    waveform, binding, provenance = (parse(payload[name]) for name in
        ('audio/waveform-S5.json','audio/native-postmix.json','audio/native-audio-provenance.json'))
    audio_ids = {event['src'] for track in timeline['audio_tracks'] for event in track['events']}
    relevant = {'project':{k:v for k,v in timeline['project'].items() if k != 'cache_dependencies'},
                'sources':[source for source in timeline['sources'] if source['id'] in audio_ids],
                'audio_tracks':timeline['audio_tracks']}
    require(not BINDER.PREFLIGHT.waveform_errors(waveform,binding), 'Original waveform validator failed')
    require(not BINDER.PREFLIGHT.portable_provenance_errors(provenance,binding,relevant),
            'Original provenance validator failed')
    require(parse(payload['src/assets/media-bindings.json'])['waveform'] == waveform, 'Embedded waveform mismatch')
    return spec, payload, manifest


def original_options(payload):
    options = []
    css = []
    for member in MEMBERS:
        page = payload[member['src']].decode('utf-8')
        match = re.search(r'FilmRuntime\.mount\(document,window,"' + re.escape(member['entryId']) + r'",(\{.*\})\);',page)
        require(match is not None, 'Original mount invocation missing')
        options.append(parse(match.group(1)))
        css.append(re.search(r'<style>(.*?)</style>',page).group(1))
    require(options[0] == options[1] and css[0] == css[1], 'Original member styles/options differ')
    return options[0], css[0]


def make_page(options, css):
    # Separate full-size SVG roots preserve the two original viewports and
    # member-local groups. IDs inside each fragment remain exactly original;
    # film-T08-01-* and film-T07-01-* already have disjoint namespaces.
    roots = ''.join('<svg id="group-' + member['layerId'] + '" xmlns="http://www.w3.org/2000/svg" '
                    'width="1920" height="1080" viewBox="0 0 1280 720" '
                    'aria-label="Framivra editable source candidate"></svg>' for member in MEMBERS)
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<title>Unvalidated T08 T07 grouped export pilot</title><style>' + css +
            'svg{position:absolute;left:0;top:0}'
            '#group-failure{position:absolute;inset:0;background:#ff00ff;color:#000;z-index:1}'
            '</style></head><body>' + roots +
            '<div id="group-failure">GROUP RENDER NOT READY</div>'
            '<script src="../runtime/shared.js"></script><script src="../runtime/group-pilot.js"></script>'
            '<script>PromoGfxGroup.mount(document,window,FilmRuntime,' +
            json.dumps(options,separators=(',',':'),allow_nan=False) + ');</script></body></html>\n').encode()


def cache_identity(plan, environment_sha256):
    """Runner must supply the actual frozen rendering environment identity.

    It must include browser/tool revisions, renderer implementation and actual
    pixel-affecting options. A content hash alone is never a render cache key.
    """
    require(isinstance(environment_sha256,str) and re.fullmatch('[0-9a-f]{64}',environment_sha256),
            'Actual renderer/environment SHA-256 is required')
    require(plan.get('schema') == 'promo-gfx-grouping/1', 'Unsupported group plan')
    expected = digest(json_bytes(plan['content']))
    require(plan.get('content_identity') == expected, 'Group content identity mismatch')
    require(plan.get('page') == PAGE and plan.get('project') == 'project', 'Unexpected group page location')
    require(plan.get('ordered_members') == [dict(m,local_frame_offset=-m['windowStart']) for m in MEMBERS],
            'Group frame mapping differs from fixed pair')
    return digest(json_bytes({'schema':'promo-gfx-grouping-cache/1','content_identity':expected,
                              'plan_sha256':digest(json_bytes(plan)),
                              'renderer_environment_sha256':environment_sha256}))


def prepare(project, output):
    project, output = Path(project).absolute(), Path(output).absolute()
    require(project != output and project not in output.parents, 'Output must be outside the original project')
    spec, payload, manifest = verify_payload(project)
    options, css = original_options(payload)
    generated = {PAGE:make_page(options,css),ADAPTER:read(HERE / 'group-pilot.js')}
    implementation_paths = [HERE/'prepare.py',HERE/'group-pilot.js',SPEC,
                            PRODUCTION/'scripts/bind_native_postmix.py',PRODUCTION/'scripts/prepare_assets.py',
                            PRODUCTION/'scripts/preflight.py']
    implementation = [{'path':p.relative_to(REPO).as_posix(),'sha256':digest(read(p))} for p in implementation_paths]
    content = {'source_commit':spec['source_commit'],'source_tree':spec['source_tree'],
               'group_id':'T08_T07','ordered_members':[dict(member) for member in MEMBERS],'runtime_options':options,
               'width':1920,'height':1080,'fps':30,'window_start':0,'duration':3600,
               'input_dependencies':[{'path':r['path'],'sha256':r['sha256']} for r in spec['bound_payload']],
               'generated_dependencies':[{'path':path,'sha256':digest(data)} for path,data in sorted(generated.items())],
               'implementation':implementation}
    plan = {'schema':'promo-gfx-grouping/1','status':'UNVALIDATED_CANDIDATE',
            'source_commit':spec['source_commit'],'source_tree':spec['source_tree'],
            'project':'project','page':PAGE,'group_id':'T08_T07',
            'width':1920,'height':1080,'fps':30,'window_start':0,'duration':3600,
            'ordered_members':[dict(m,local_frame_offset=-m['windowStart']) for m in MEMBERS],
            'frame_mapping':{'group_local_equals_global':True,'global_frame_domain':[0,3600],
                             'member_active':'windowStart <= global_frame < windowStart + duration',
                             'member_local':'global_frame - windowStart','inactive_member':'no renderFrame call; empty SVG'},
            'original_entry_manifest_sha256':digest(payload['entry-manifest.json']),
            'content_identity':digest(json_bytes(content)),'content':content,
            'cache_contract':{'namespace':'promo-gfx-grouping/1','environment_identity_required':True,
                              'reuse_original_cache':False,'marker_publication':False},
            'comparison':{'rgba':'UNRUN','alpha':'UNRUN','black_white_actual_background':'UNRUN',
                          'full_n1_composition':'UNRUN','performance':'UNRUN'},
            'excluded_runtime_media':spec['excluded_runtime_media'],
            'production_ready':False,'full_film_replacement':False}
    with BINDER.OwnedOutput(output) as owned:
        for item in spec['bound_payload']:
            owned.write('project/' + item['path'],payload[item['path']],int(item['mode'],8))
        for path,data in generated.items():
            owned.write('project/' + path,data)
        # Publish the plan last. No plan exists if any payload write fails.
        owned.write('group-plan.json',json_bytes(plan))
        owned.inventory()
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bound-project',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True,help='New directory; parent must already exist')
    args = parser.parse_args()
    try:
        plan = prepare(args.bound_project,args.output)
    except (OSError,ValueError,KeyError,TypeError) as error:
        print('Group preparation failed: ' + str(error),file=sys.stderr)
        return 1
    print(json.dumps({'status':plan['status'],'group_plan':str(args.output.absolute()/'group-plan.json'),
                      'content_identity':plan['content_identity']},sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
