'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const Group=require('../group-pilot.js'),{fixture,dom}=require('./pair-fixture.cjs');
test('strict global frame input cannot clamp, coerce or duplicate a requested frame',()=>{
  for(const value of [0,17,18,3599,'0','18','3599'])assert.equal(Group.frameNumber(value),Number(value));
  for(const value of [-1,3600,1.5,NaN,Infinity,null,undefined,false,true,{},[],['18'],'',' ','01','+1','1.0','1e1','-1','3600'])
    assert.throws(()=>Group.frameNumber(value),RangeError);
});
test('ordered half-open mapping and original render options are preserved',()=>{
  const {FilmRuntime,calls}=fixture(),options={diagnostic:true,effects:{translucent_trails:true}};
  const group=Group.createGroup(FilmRuntime,options);
  assert.equal(calls.length,2);assert.ok(calls.every(call=>call.create===options));
  for(const f of [0,17,18,19,1404,1451,1452,2940,3089,3090,3599]) {
    calls.length=0;
    const result=group.render(f);
    assert.equal(result.globalFrame,f);
    assert.deepEqual(result.members.map(m=>m.entryId),['T08.01','T07.01']);
    assert.deepEqual(calls,f<18?[{entryId:'T07.01',localFrame:f,paper:false}]:[
      {entryId:'T08.01',localFrame:f-18,paper:false},{entryId:'T07.01',localFrame:f,paper:false}]);
    assert.equal(result.members[0].active,f>=18);
  }
});
test('repeat, reverse and shuffled requests use their actual current global frame',()=>{
  const {FilmRuntime}=fixture(),group=Group.createGroup(FilmRuntime,{});
  const sequence=[3599,0,18,1380,17,18,1407,2940,1380,0];
  const seen=new Map();
  for(const f of sequence) {
    const result=JSON.stringify(group.render(f));
    if(seen.has(f))assert.equal(result,seen.get(f));seen.set(f,result);
  }
});
test('mount keeps roots ordered, clear inactive content and original fragment references',()=>{
  const {FilmRuntime}=fixture(),{document,elements}=dom(),window={location:{search:'?f=17'}};
  Group.mount(document,window,FilmRuntime,{});
  assert.equal(elements['group-T08'].innerHTML,'');
  assert.match(elements['group-T07'].innerHTML,/mask="url\(#T07\.01-mask\)"/);
  assert.equal(elements['group-T07'].attributes['data-frame'],'17');
  assert.equal(elements['group-failure'].style.display,'none');
  assert.deepEqual(window.__promo_gfx_group_status,{status:'RENDERED_UNVERIFIED',globalFrame:17});
  for(const key of ['__vtx_render','__vtx_export_png','__vtx_static_spans'])assert.equal(Object.hasOwn(window,key),false);
});
test('invalid query and renderer exceptions remain visible failures, never blank success',()=>{
  for(const search of ['', '?f=18&f=19','?f=18&paper=1','?f=3600','?f=NaN']){
    const {FilmRuntime}=fixture(),{document,elements}=dom(),window={location:{search}};
    assert.throws(()=>Group.mount(document,window,FilmRuntime,{}));
    assert.equal(elements['group-failure'].style.display,'block');
    assert.equal(window.__promo_gfx_group_status.status,'ERROR');
  }
  const {FilmRuntime}=fixture(),create=FilmRuntime.createRuntime;
  FilmRuntime.createRuntime=(options)=>({...create(options),renderFrame(){throw Error('fixture render failure');}});
  const {document,elements}=dom(),window={location:{search:'?f=18'}};
  assert.throws(()=>Group.mount(document,window,FilmRuntime,{}),/fixture render failure/);
  assert.equal(elements['group-failure'].style.display,'block');
});
test('member drift and wrong returned frame are rejected',()=>{
  const {FilmRuntime}=fixture(),create=FilmRuntime.createRuntime;
  FilmRuntime.createRuntime=options=>({...create(options),entries:[]});
  assert.throws(()=>Group.createGroup(FilmRuntime,{}),/member differs/);
  FilmRuntime.createRuntime=options=>{const r=create(options);return {...r,renderFrame(...args){return {...r.renderFrame(...args),globalFrame:99};}};};
  assert.throws(()=>Group.createGroup(FilmRuntime,{}).render(18),/unexpected frame/);
});
