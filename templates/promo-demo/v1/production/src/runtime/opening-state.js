'use strict';
// Adapted from Framivra candidate-r3 opening.html (MIT, copyright 2026 ruochongliang).
// Preserve the original opening curve math. This helper is not the S1 scene implementation.
const C=require('../assets/opening-cues.json');
const {keys,mix,smoothstep,progress,deepFreeze,cameraMatrix}=require('./math.js');
const ramp=(f,a,b)=>smoothstep(progress(f,a,b));
const down=(f,[start,hit,end,travel])=>f<=hit?travel*ramp(f,start,hit):travel*(1-ramp(f,hit,end));
function openingState(f) {
  if(!Number.isInteger(f)||f<0||f>299)throw new RangeError('Opening helper accepts explicit reference frame 0..299 only');
  const hx=keys(f,C.keys.hero_x),approach=f>=C.windows_inclusive.approach[0];
  const camera={z:keys(f,C.keys.camera_zoom),x:f<184?hx+20:keys(f,C.keys.camera_x_reveal),y:keys(f,C.keys.camera_y)};
  const stage=f<C.contacts.cut?0:f<C.contacts.body?1:f<C.contacts.trim?2:f<C.contacts.eye?3:4;
  const u=ramp(f,...C.windows_inclusive.approach),scale=mix(.65,35,u*u),t=ramp(f,...C.windows_inclusive.waste_separation);
  return deepFreeze({f,hx,camera,cameraMatrix:cameraMatrix(camera),stage,approach,
    bird:{space:approach?'screen':'world',pose:{x:approach?mix(965,640+41*scale,u):hx,y:approach?mix(272,360+28*scale,u):keys(f,C.keys.bird_y),angle:approach?0:keys(f,C.keys.bird_angle),scale:approach?scale:1},wing:approach||f<194?0:keys(f,C.keys.wing_angle),stage,feet:false,occluders:[]},
    waste:{visible:f>=18&&f<=54,x:350-85*t,y:440+360*t,angle:-12*t},offcuts:{visible:f>=109&&f<139,t:ramp(f,...C.windows_inclusive.head_offcut_fall)},
    presses:{cut:102+down(f,C.presses.cut),body:240+down(f,C.presses.body),trim:down(f,C.presses.trim),eye:265+down(f,C.presses.eye)},wave:f>=219&&f<=245?18*Math.sin(Math.PI*ramp(f,219,245)):0});
}
module.exports={openingState};
