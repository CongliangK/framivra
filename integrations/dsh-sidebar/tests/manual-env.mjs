import { mkdir, mkdtemp, writeFile, readFile, realpath, stat } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { createPreviewHost } from '../src/host.mjs'
import { parseJSON, stringifyJSON } from '../src/json.mjs'
export const enabled = Boolean(process.env.SIDEBAR_DSH_ROOT && process.env.SIDEBAR_REAL_VTX && process.env.SIDEBAR_EVIDENCE_DIR)
export const skip = enabled ? false : 'Set SIDEBAR_DSH_ROOT, SIDEBAR_REAL_VTX, SIDEBAR_EVIDENCE_DIR for actual installed write kernel/public provider'
export function fixture() { return { version:2, project:{ fps:30,width:1920,height:1080,duration_frames:120 }, sources:[{id:'s',path:'missing.mov',extension:{id:'s',nested:[null,false,1.25]}}],video_tracks:[{id:'v',name:'main',kind:'aroll',clips:[{id:'c',src:'s',src_in:10,src_out:40,dst_in:0,dur:30,extension:{ref:'c',nested:{x:[1,2]}}},{id:'c2',src:'s',src_in:50,src_out:80,dst_in:60,dur:30}]},{id:'g',name:'graphics',kind:'gfx',clips:[{id:'gc',src:'missing.html',dst_in:0,dur:30}]}],audio_tracks:[{id:'a',name:'voice',events:[{id:'e',src:'s',src_in:7,in:0,out:90,volume:1,loop:true}]},{id:'bed',name:'bed',duck:true,duck_strategy:{voice_track:'voice',extra:'voice'},events:[{id:'be',src:'s',in:0,out:100,volume:0.2}],_meta:{bed_static_duck:{params:{voice_track:'voice'}}}}],subtitles:[{id:'sub',in:0,out:30,text:'你好',extension:{preserve:'sub'}}],unknown_root:{references:['c','voice','s'],nested:{anything:[false,null,{id:'v'}]}} } }
// Test-only public-fs-shaped local adapter, NOT a production filesystem bypass.
export function localFs() { return { async resolve(path,{cwd='/'}={}) { const absolute=resolve(cwd,path); let key;try{key=await realpath(absolute)}catch(e){if(e.code!=='ENOENT')throw e;key=absolute}return {targetKey:key} },processPath:t=>t.targetKey,processPathFromHostPath:p=>p,contains:(a,b)=>b.targetKey===a.targetKey||b.targetKey.startsWith(`${a.targetKey}/`),async stat(t){try{const s=await stat(t.targetKey);return {type:s.isDirectory()?'directory':s.isFile()?'file':'other',version:`${s.ino}:${s.mtimeMs}:${s.size}`,size:s.size}}catch(e){if(e.code!=='ENOENT')throw e}} } }
const providers=new WeakMap()
export async function realProvider(t) {
 if(providers.has(t))return providers.get(t)
 const root=process.env.SIDEBAR_DSH_ROOT
 const {Context}=await import(pathToFileURL(join(root,'vendor/cordis/lib/index.js')))
 const {LocalSubprocessRuntime}=await import(pathToFileURL(join(root,'packages/subprocess/subprocess-local/lib/index.js')))
 const ctx=new Context(), fiber=ctx.plugin(LocalSubprocessRuntime);await fiber.await();t.after(()=>fiber.dispose());const runtime=ctx.get('subprocess');providers.set(t,runtime);return runtime
}
export async function environment(t, label='manual', document=fixture(), { legacy=false,createHost=createPreviewHost }={}) {
 const evidence=process.env.SIDEBAR_EVIDENCE_DIR;await mkdir(evidence,{recursive:true});const dir=await mkdtemp(join(evidence,`${label}-`)), path=join(dir,'timeline.json')
 const runtime=await realProvider(t), handles=[], specs=[], faults={}
 // Only process-result fault injection: real commands still execute in the actual
 // installed kernel. This never manufactures a successful CLI response.
 const subprocess={resolveExecutable:(...a)=>runtime.resolveExecutable(...a),spawn(spec){specs.push(spec);const child=runtime.spawn(spec);handles.push(child);faults.onSpawn?.(spec,child);if(faults.dropCommitOutput && spec.argv.includes('--request-stdin') && !spec.argv.includes('plan'))return new Proxy(child,{get(target,key){if(key==='collected')return {...target.collected,stdout:{readFrom:offset=>({...target.collected.stdout.readFrom(offset),lossy:true})}};const value=Reflect.get(target,key);return typeof value==='function'?value.bind(target):value}});return child}}
 const session={id:'one'}, sessions=new Map([['one',session],['two',{id:'two'}]]),cwd={value:dir}
 const host=createHost({subprocess,fs:localFs(),sessions,workingDirectory:{get:()=>cwd.value}},{vtxExecutable:process.env.SIDEBAR_REAL_VTX})
 t.after(()=>host.dispose())
 const wire=[]
 async function call(sessionId,data,signal=new AbortController().signal){const response=await host.control(new Request('http://owner/api/framivra/control',{method:'POST',body:stringifyJSON({sessionId,...data}),signal}));const text=await response.text();const answer=parseJSON(text);wire.push({request:data,status:response.status,response:answer});if(!response.ok){const e=new Error(`${answer.error}: ${answer.message}`);e.payload=answer;e.status=response.status;throw e}return answer}
 // Test fixtures only: unmanaged input is written explicitly, managed create is
 // an actual core transaction via the same public batch stdin as production.
 if(legacy)await writeFile(path,stringifyJSON(document));else {
 const child=runtime.spawn({argv:[process.env.SIDEBAR_REAL_VTX,'-t',path,'store','--request-stdin'],cwd:'/',graceMs:1000,env:{PYTHONPATH:undefined,PYTHONHOME:undefined,PYTHONDONTWRITEBYTECODE:'1',PYTHONSAFEPATH:'1'},stdio:{stdin:{data:stringifyJSON({operation:'create',op_id:'seed',base_revision:null,document})},stdout:{maxBytes:8388608},stderr:{maxBytes:16384}}})
 try{const outcome=await child.done;if(outcome.exitCode!==0)throw new Error(child.collected.stdout.readFrom(0).text+child.collected.stderr.readFrom(0).text)}finally{child.terminate();await child.waitForExit(AbortSignal.timeout(5000))}
 }
 const opened=await call('one',{op:'open',timeline:'timeline.json'}),result=await call('one',{op:'query',grant:opened.grant,query:{}})
 return {host,call,opened,result,path,dir,handles,specs,faults,cwd,sessions,wire,read:()=>readFile(path,'utf8'),async save(name,data){await writeFile(join(dir,name),typeof data==='string'?data:stringifyJSON(data,2))}}
}
