import test from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { parseJSON, stringifyJSON } from '../src/json.mjs'

// Advanced settings UI is retired; retain the actual pure kernel operation contract.
test('explicit settings contracts run through real pure kernel transform; invalid shrink is rejected without writes', () => {
  const operations = [
    {op:'project.resize',duration_frames:1000},
    {op:'parameters.update',target:'project',path:['gfx_dependency_policy'],value:'strict'},
    {op:'parameters.update',target:'project',path:['cache_dependencies'],value:['font.ttf']},
    {op:'parameters.update',id:'s',path:['denoise'],value:parseJSON('{"future":{"off":false,"nil":null,"big":9007199254740993}}')},
    {op:'parameters.update',id:'s',path:['bgm'],value:false},
    {op:'track.references',id:'a',set:{duck_strategy:{voice_track:'voice',unknown:null}}},
  ]
  const script = `import json,sys\nfrom vtx.timeline_edit import transform_timeline, EditError\nops=json.load(sys.stdin)\ndoc={"version":2,"project":{"fps":30,"width":1920,"height":1080,"duration_frames":900},"sources":[{"id":"s","path":"missing.wav","untouched":{"x":False}}],"video_tracks":[],"audio_tracks":[{"id":"a","name":"bed","events":[{"id":"e","src":"s","in":0,"out":90,"volume":1}]},{"id":"v","name":"voice","events":[{"id":"ve","src":"s","in":0,"out":90,"volume":1}]}],"subtitles":[],"_vtx":{"protocol":1,"project_id":"p","head":"h"},"blender_bridge":None,"unknown_root":{"a":[False,None]}}\nfinal,summary=transform_timeline(doc,ops)\nassert doc["project"]["duration_frames"]==900\nassert final["project"]["duration_frames"]==1000\nassert final["project"]["gfx_dependency_policy"]=="strict"\nassert final["project"]["cache_dependencies"]==["font.ttf"]\nassert final["sources"][0]["denoise"]["future"]["big"]==9007199254740993\nassert final["sources"][0]["bgm"] is False\nassert final["sources"][0]["untouched"]==doc["sources"][0]["untouched"]\nassert final["audio_tracks"][0]["duck_strategy"]=={"voice_track":"voice","unknown":None}\nassert final["blender_bridge"] is None and final["unknown_root"]==doc["unknown_root"]\ntry:\n transform_timeline(final,[{"op":"project.resize","duration_frames":1}])\nexcept EditError:\n pass\nelse:\n raise AssertionError("invalid shrink accepted")\nprint("pure kernel settings transform passed")`
  const outcome = spawnSync('python3', ['-B', '-c', script], { cwd: fileURLToPath(new URL('../..', import.meta.url)), input: stringifyJSON(operations), encoding: 'utf8', env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' } })
  assert.equal(outcome.status, 0, outcome.stderr || outcome.error?.message)
  assert.match(outcome.stdout, /pure kernel settings transform passed/)
})
