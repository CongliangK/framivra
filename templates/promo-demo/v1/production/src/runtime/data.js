'use strict';
const {deepFreeze}=require('./math.js');
module.exports=deepFreeze({
  cues:require('../assets/cues.json'), geometry:require('../assets/geometry.json'), trackMap:require('../assets/track-map.json'),
  sourceBindings:require('../assets/source-bindings.json'), actualTimeline:require('../assets/timeline.json'), timelineBlueprint:require('../assets/timeline-blueprint.json'),
  boundaries:require('../assets/boundaries.json'), motion:require('../assets/motion-contract.json'), workload:require('../assets/gfx-workload.json'),
  waveform:require('../assets/media-bindings.json').waveform, fonts:require('./fonts.js')
});
