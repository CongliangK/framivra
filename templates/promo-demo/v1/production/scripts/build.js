'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const ROOT=path.resolve(__dirname,'..'),SRC=path.join(ROOT,'src');
const sha=buffer=>crypto.createHash('sha256').update(buffer).digest('hex');
const read=p=>JSON.parse(fs.readFileSync(path.join(ROOT,p),'utf8'));
function walk(root){return fs.readdirSync(root,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name)).flatMap(e=>e.isDirectory()?walk(path.join(root,e.name)):[path.join(root,e.name)]);}
function local(p){if(typeof p!=='string'||p.startsWith('/')||p.split('/').some(x=>x==='..'||!x)||p.includes('\\'))throw new Error('Unsafe project dependency: '+p);return path.join(ROOT,p);}
function productionBlockers(registry){
 const missing=['INDEPENDENT_COMBINED_SOURCE_REVIEW_REQUIRED','USER_AUDIO_AUDITION_APPROVAL_REQUIRED','ACTUAL_ENGINE_AND_CAPTION_CONSUMPTION_UNVERIFIED'];
 for(const [id,scene] of Object.entries(registry))if(!scene.implemented)missing.push(`${id}_READINESS_UNAPPROVED`);
 for(const id of ['T17','T18','T19','T20'])if(!fs.existsSync(local(`audio/${id}.flac`)))missing.push(`${id}_AUDIO_MISSING`);
 if(!fs.existsSync(local('media/paper-background.mp4')))missing.push('PAPER_SOURCE_MISSING');
 if(!fs.existsSync(local('audio/waveform-S5.json')))missing.push('ACTUAL_PCM_WAVEFORM_MISSING');
 for(const file of ['source-label-glyphs.json','brand-glyphs.json']){
  const p=local('src/assets/fonts/'+file);
  if(!fs.existsSync(p))missing.push('FONT_PROVIDER_MISSING:'+file);
  else if(!Object.values(read('src/assets/provider-review.json').providers).some(r=>r.status==='PASS'&&r.provider_sha256===sha(fs.readFileSync(p))))missing.push('FONT_PROVIDER_REVIEW_REQUIRED:'+file);
 }
 return missing;
}
function build({draft=false,effects={},variant=null,outDir=null}={}){
 const flags=require('../src/runtime/effects.js').options(effects,!!variant||draft);
 if(variant!==null&&!/^[a-z][a-z0-9-]*$/.test(variant))throw new Error('Unsafe diagnostic variant name');
 if(Object.values(flags).some(v=>!v)&&!variant)throw new Error('Effect-off build requires a separate named diagnostic variant');
 const OUT=outDir?path.resolve(outDir):variant?path.join(ROOT,'diagnostics',variant):ROOT;
 // The emitted timeline, not a copied expected projection, is the binding source.
 for(const name of ['cues.json','source-bindings.json','track-map.json'])fs.copyFileSync(local('production/'+name),path.join(SRC,'assets',name));
 fs.copyFileSync(local('design/geometry.json'),path.join(SRC,'assets/geometry.json'));
 const timeline=read('timeline.json'),recipes=read('production/source-bindings.json');
 const media=read('src/assets/media-bindings.json');media.waveform=fs.existsSync(local('audio/waveform-S5.json'))?read('audio/waveform-S5.json'):null;media.audio_approved=false;
 fs.writeFileSync(path.join(SRC,'assets/media-bindings.json'),JSON.stringify(media,null,2)+'\n');
 if(timeline.version!==2||timeline.project.fps!==30||timeline.project.duration_frames!==3600||timeline.video_tracks.length!==15||timeline.audio_tracks.length!==4||timeline.subtitles.length!==8)throw new Error('Full-film timeline identity/role count drift');
 const dependencies=['runtime/shared.js','runtime/effects.json','production/cues.json','production/source-bindings.json','design/geometry.json','design/shapes.svg','production/caption-font.json','src/assets/provider-review.json','production/reviews/font-outlines.json','src/assets/fonts/source-label-glyphs.json','notices/Noto-Mono-OFL-copyright.txt','notices/Noto-CJK-copyright.txt'];
 for(const file of ['audio/stems.json','audio/native-postmix.json','audio/waveform-S5.json','audio/native-audio-provenance.json'])if(fs.existsSync(local(file)))dependencies.push(file);
 if(fs.existsSync(path.join(SRC,'assets/fonts/brand-glyphs.json')))dependencies.push('src/assets/fonts/brand-glyphs.json','notices/Noto-SC-OFL.txt');
 timeline.project.cache_dependencies=dependencies;
 // Sync exact emitted JSON before loading the CommonJS registry and generating its bundle.
 const timelineBytes=JSON.stringify(timeline,null,2)+'\n';
 fs.writeFileSync(path.join(ROOT,'timeline.json'),timelineBytes);
 fs.writeFileSync(path.join(SRC,'assets/timeline.json'),timelineBytes);
 for(const key of Object.keys(require.cache))if(key.startsWith(SRC+path.sep))delete require.cache[key];
 const registry=require('../src/scenes/index.js'),Entry=require('../src/runtime/entries.js');
 const review=read('src/assets/provider-review.json');
 if(Object.keys(review.providers).length&&(!review.receipt_path||sha(fs.readFileSync(local(review.receipt_path)))!==review.receipt_sha256))throw new Error('Independent font receipt identity drift');
 for(const [id,item] of Object.entries(review.providers)){
  if(item.status!=='PASS'||sha(fs.readFileSync(local(item.provider_path)))!==item.provider_sha256||sha(fs.readFileSync(local(item.license_path)))!==item.license_sha256||!item.report_sha256)throw new Error('Invalid exact provider review binding: '+id);
 }
 const entries=Entry.createEntries(read('src/assets/track-map.json'),timeline),fields=Entry.bindSources(timeline,recipes),blockers=productionBlockers(registry);
 if(!draft&&blockers.length)throw new Error('Production release blocked: '+blockers.join(', '));
 const sources=walk(SRC).filter(p=>/\.(js|json)$/.test(p));
 const modules=sources.map(p=>{
  const id=path.relative(SRC,p).split(path.sep).join('/'),data=fs.readFileSync(p,'utf8');
  return `${JSON.stringify(id)}: function(module,exports,require){\n${p.endsWith('.json')?`module.exports = ${JSON.stringify(JSON.parse(data))};`:data}\n}`;
 }).join(',\n');
 const license=fs.readFileSync(path.join(ROOT,'notices/FRAMIVRA-MIT-LICENSE.txt'),'utf8');
 const bundle=`/* Framivra 120-second editable source candidate. Independent review and actual engine acceptance remain separate.\n${license}*/\n(function(global){'use strict';\nconst factories={${modules}};\nconst loaded=Object.create(null);\nfunction resolve(parent,request){if(!request.startsWith('.'))throw new Error('Only bundled local modules');const parts=parent.split('/').slice(0,-1);for(const item of request.split('/')){if(item==='.'||item==='')continue;if(item==='..')parts.pop();else parts.push(item);}return parts.join('/');}\nfunction load(id){if(loaded[id])return loaded[id].exports;if(!factories[id])throw new Error('Unknown bundled module '+id);const module={exports:{}};loaded[id]=module;factories[id](module,module.exports,request=>load(resolve(id,request)));return module.exports;}\nglobal.FilmRuntime=Object.freeze(load('runtime/runtime.js'));\n})(window);\n`;
 fs.mkdirSync(path.join(OUT,'runtime'),{recursive:true});
 fs.writeFileSync(path.join(OUT,'runtime/shared.js'),bundle);
 fs.writeFileSync(path.join(OUT,'runtime/effects.json'),JSON.stringify(flags,null,2)+'\n');
 for(const e of entries){
  const dst=path.join(OUT,e.src);fs.mkdirSync(path.dirname(dst),{recursive:true});
  const options={diagnostic:!!variant||draft,allowScaffold:draft,effects:flags};
  fs.writeFileSync(dst,`<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>Framivra ${e.entryId} source candidate</title><style>html,body{margin:0;width:1920px;height:1080px;overflow:hidden;background:transparent}svg{display:block;width:1920px;height:1080px}</style></head><body><svg id="scene" xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1280 720" aria-label="Framivra editable source candidate"></svg><script src="../runtime/shared.js"></script><script>FilmRuntime.mount(document,window,${JSON.stringify(e.entryId)},${JSON.stringify(options)});</script></body></html>\n`);
 }
 if(OUT!==ROOT){
  for(const rel of dependencies.filter(p=>!p.startsWith('runtime/'))){const dst=path.join(OUT,rel);fs.mkdirSync(path.dirname(dst),{recursive:true});fs.copyFileSync(local(rel),dst);}
  fs.writeFileSync(path.join(OUT,'timeline.json'),timelineBytes);
 }
 const bindings={status:'ACTUAL_EMITTED_CANDIDATE_TIMELINE',timeline_sha256:sha(timelineBytes),items:fields};
 fs.mkdirSync(path.join(OUT,'production'),{recursive:true});
 fs.writeFileSync(path.join(OUT,'production/source-bindings.actual.json'),JSON.stringify(bindings,null,2)+'\n');
 const record={status:'DRAFT_SOURCE_NOT_PRODUCTION_READY',variant,effects:flags,production_ready:false,blockers,timeline_sha256:sha(timelineBytes),bundle_sha256:sha(bundle),entry_count:entries.length,gfx_roles:14,role_accounting:{planned:20,visual_tracks:15,native_caption_streams:1,audio_roles:4,verified_complete_contributions:null},entries,scene_flags:Object.fromEntries(Object.entries(registry).map(([id,s])=>[id,{implemented:s.implemented,geometryImplemented:s.geometryImplemented===true?true:null}])),cache_dependencies:dependencies,claims:{node_source_only:true,browser:false,actual_engine:false,performance:false,audio_approval:false}};
 fs.writeFileSync(path.join(OUT,'entry-manifest.json'),JSON.stringify(record,null,2)+'\n');
 fs.writeFileSync(path.join(OUT,'runtime-dependencies.json'),JSON.stringify(dependencies.map(p=>({path:p,sha256:sha(fs.readFileSync(path.join(OUT,p)))})),null,2)+'\n');
 return record;
}
if(require.main===module){try{const off=process.argv.find(x=>x.startsWith('--off='))?.slice(6);console.log(JSON.stringify(build({draft:process.argv.includes('--draft'),...(off?{effects:{[off]:false},variant:off.replaceAll('_','-')+'-off'}:{})}),null,2));}catch(e){console.error(e.message);process.exitCode=1;}}
module.exports={build,productionBlockers};
