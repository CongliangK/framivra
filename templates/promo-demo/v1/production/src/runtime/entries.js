'use strict';
const {deepFreeze}=require('./math.js');
const DATA=require('./data.js');
function createEntries(trackMap,timeline) {
  const entries=[],gfxTracks=trackMap.tracks.filter(t=>t.kind==='gfx');
  if(gfxTracks.length!==14)throw new Error('R04 recipe must contain exactly 14 GFX roles');
  for(const track of gfxTracks) {
    const matches=timeline.video_tracks.filter(t=>t.name===track.name && t.kind==='gfx');
    if(matches.length!==1)throw new Error(`Unique real timeline track required: ${track.name}`);
    if(timeline.video_tracks[track.video_array_index]!==matches[0])throw new Error(`Paint order mismatch for ${track.id}`);
    if(matches[0].clips.length!==track.clips.length)throw new Error(`Unexpected timeline clips in ${track.name}`);
    for(const clip of track.clips) {
      const actual=matches[0].clips.filter(c=>c.src===clip.src);
      if(actual.length!==1 || actual[0].dst_in!==clip.dst_in || actual[0].dur!==clip.dur)throw new Error(`Clip recipe drift: ${clip.id}`);
      if(!Number.isInteger(clip.dur)||clip.dur<=0||!Number.isInteger(clip.global_start_frame)||clip.global_start_frame!==clip.dst_in||clip.dst_in<0||clip.dst_in+clip.dur>3600)throw new Error(`Invalid clip window: ${clip.id}`);
      if(!/^gfx\/T\d{2}_\d{4}_\d{4}\.html$/.test(clip.src))throw new Error(`Unexpected entry path: ${clip.src}`);
      entries.push({entryId:clip.id,layerId:track.id,role:track.name,src:clip.src,windowStart:clip.global_start_frame,duration:clip.dur,paintIndex:track.video_array_index});
    }
  }
  if(entries.length!==19||new Set(entries.map(e=>e.entryId)).size!==19||new Set(entries.map(e=>e.src)).size!==19)throw new Error('Expected 19 unique GFX entries');
  return deepFreeze(entries);
}
function bindSources(timeline,recipes) {
  return deepFreeze(recipes.items.map(item=>{
    const tracks=timeline.video_tracks.filter(t=>t.name===item.track_name);
    if(tracks.length!==1)throw new Error(`Source binding track is not unique: ${item.id}`);
    const clips=tracks[0].clips.filter(c=>c.src===item.clip_src);
    if(clips.length!==1)throw new Error(`Source binding clip is not unique: ${item.id}`);
    const fields={};
    for(const key of item.fields) {
      if(!['src','dst_in','dur'].includes(key)||!Object.hasOwn(clips[0],key))throw new Error(`Unsupported binding ${key}`);
      fields[key]=clips[0][key];
    }
    return {...item,fields,displayFields:Object.fromEntries(Object.entries(fields).map(([k,v])=>[k,k==='src'?v.split('/').pop():String(v)]))};
  }));
}
const ENTRIES=createEntries(DATA.trackMap,DATA.actualTimeline);
function getEntry(id) { const found=ENTRIES.find(e=>e.entryId===id); if(!found)throw new RangeError(`Unknown clip entry ${id}`);return found; }
function frameNumber(value,duration) {
  if(!Number.isInteger(duration)||duration<1)throw new RangeError('Positive integer clip duration required');
  if(typeof value!=='string' && typeof value!=='number' && value!==null && value!==undefined)return 0;
  const n=Number(value); return Number.isFinite(n)?Math.min(duration-1,Math.max(0,Math.floor(n))):0;
}
function frameContext(entryId,value) {
  const entry=getEntry(entryId),localFrame=frameNumber(value,entry.duration),globalFrame=entry.windowStart+localFrame;
  const scene=DATA.cues.scenes.find(s=>globalFrame>=s.in&&globalFrame<s.out);
  if(!scene)throw new RangeError('Frame must resolve exactly one scene');
  return deepFreeze({entry,localFrame,globalFrame,localSceneFrame:globalFrame-scene.in,scene});
}
module.exports={ENTRIES,createEntries,bindSources,getEntry,frameNumber,frameContext};
