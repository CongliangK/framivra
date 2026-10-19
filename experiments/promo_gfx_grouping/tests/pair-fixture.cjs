'use strict';
const {MEMBERS}=require('../group-pilot.js');
function fixture() {
  const calls=[];
  const FilmRuntime={createRuntime(options) {
    calls.push({create:options});
    return {entries:MEMBERS,renderFrame(entryId,localFrame,{paper}) {
      calls.push({entryId,localFrame,paper});
      const member=MEMBERS.find(member=>member.entryId===entryId);
      return Object.freeze({entryId,layerId:member.layerId,localFrame,
        globalFrame:member.windowStart+localFrame,sceneId:'fixture',profile:'diagnostic',implemented:true,
        fragment:`<defs><mask id="${entryId}-mask"></mask></defs><g mask="url(#${entryId}-mask)" opacity="0.09"><path d="M0 0L1 1"></path></g>`});
    }};
  }};
  return {FilmRuntime,calls};
}
function dom() {
  const elements=Object.fromEntries(['group-T08','group-T07','group-failure'].map(id=>[id,{
    innerHTML:'',attributes:{},style:{display:'block'},setAttribute(k,v){this.attributes[k]=v;}
  }]));
  return {elements,document:{getElementById:id=>elements[id]}};
}
module.exports={fixture,dom};
