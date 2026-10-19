#!/usr/bin/env node
'use strict';
// Real bound-runtime STRUCTURE check in Node, not a browser/pixel test.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const assert=require('node:assert/strict'),crypto=require('node:crypto');
const Group=require('../group-pilot.js');
const project=process.argv[2];
if(!project||process.argv.length!==3)throw Error('Usage: node tests/verify_bound.cjs PREPARED_PROJECT');
const read=relative=>fs.readFileSync(path.join(project,relative));
const sha=data=>crypto.createHash('sha256').update(data).digest('hex');
const pin=JSON.parse(fs.readFileSync(path.join(__dirname,'../fixtures/bound-input.json')));
for(const record of pin.bound_payload)assert.equal(sha(read(record.path)),record.sha256,record.path);
const context=vm.createContext({window:{},URLSearchParams});
vm.runInContext(read('runtime/shared.js').toString(),context,{timeout:10000});
const Runtime=context.window.FilmRuntime;
const options=JSON.parse(read('gfx/T07_0000_3600.html').toString().match(/FilmRuntime\.mount\(document,window,"T07.01",(\{.*\})\);/)[1]);
const group=Group.createGroup(Runtime,options),originals=Group.MEMBERS.map(()=>Runtime.createRuntime(options));
const digests=new Map();let activeMembers=0,uniqueIDs=0,localRefs=0;
function check(globalFrame,record) {
  const result=group.render(globalFrame),ids=new Set(),memberDigests=[];
  result.members.forEach((member,index)=>{
    const mapping=Group.MEMBERS[index];
    if(!member.active){assert.equal(globalFrame<18,true);assert.equal(member.result,null);return;}
    const expected=originals[index].renderFrame(mapping.entryId,globalFrame-mapping.windowStart,{paper:false});
    assert.equal(member.result.fragment,expected.fragment,'Original fragment changes at f'+globalFrame);
    const rawIDs=[...member.result.fragment.matchAll(/\bid="([^"]+)"/g)].map(match=>match[1]);
    const localIDs=new Set(rawIDs);assert.equal(localIDs.size,rawIDs.length,'Duplicate member ID');
    for(const id of localIDs){assert.ok(!ids.has(id),'Cross-member duplicate ID '+id);ids.add(id);if(record)uniqueIDs++;}
    for(const match of member.result.fragment.matchAll(/(?:url\(#([^)]*)\)|href="#([^"]+)")/g)){
      assert.ok(localIDs.has(match[1]||match[2]),'Reference crosses member namespace');if(record)localRefs++;
    }
    assert.ok(!/<style\b|\bstyle=/.test(member.result.fragment),'Unexpected role CSS scope');
    memberDigests.push(sha(member.result.fragment));if(record)activeMembers++;
  });
  const fingerprint=sha(JSON.stringify(memberDigests));
  if(record)digests.set(globalFrame,fingerprint);else assert.equal(fingerprint,digests.get(globalFrame));
}
for(let frame=0;frame<3600;frame++)check(frame,true);
// Repeat/seek/scene boundary histories test the adapter without turning on the
// sealed production navfree path. No browser history equivalence is inferred.
for(const frame of [3599,3150,3149,3120,3119,2940,2939,2820,2819,2160,2159,1711,1710,1709,1452,1451,1404,1403,1380,1140,1139,690,689,630,629,330,329,300,299,139,138,19,18,17,0,1380,18,3599])check(frame,false);
assert.equal(activeMembers,7182);
console.log(JSON.stringify({status:'PASS_BOUND_STRUCTURE_ONLY',global_frames:3600,active_member_frames:activeMembers,
  ordered_members:['T08.01','T07.01'],unique_member_id_occurrences:uniqueIDs,member_local_reference_occurrences:localRefs,
  native_bound_bundle_sha256:sha(read('runtime/shared.js')),browser_pixels:'UNRUN',alpha:'UNRUN',n1_compositor:'UNRUN',performance:'UNRUN'}));
