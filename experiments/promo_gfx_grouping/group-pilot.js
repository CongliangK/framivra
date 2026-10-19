/* Isolated T08 -> T07 export adapter. Navigation only; no production hooks. */
(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.PromoGfxGroup = factory();
})(typeof window === 'undefined' ? globalThis : window, function () {
  'use strict';
  const MEMBERS = Object.freeze([
    Object.freeze({entryId:'T08.01',layerId:'T08',windowStart:18,duration:3582,paintIndex:6}),
    Object.freeze({entryId:'T07.01',layerId:'T07',windowStart:0,duration:3600,paintIndex:7})
  ]);
  function frameNumber(value) {
    // Unlike the original entry's friendly preview clamp, an experiment must
    // reject an invalid requested frame instead of reporting another frame.
    if (typeof value === 'string' && /^(0|[1-9][0-9]*)$/.test(value)) value = Number(value);
    if (typeof value !== 'number' || !Number.isInteger(value) || value < 0 || value >= 3600)
      throw new RangeError('Group frame must be an integer 0..3599');
    return value;
  }
  function createGroup(FilmRuntime, options) {
    // Keep one original runtime per member. Do not combine state calculations,
    // cameras, masks, opacity, effects or original entry-ID namespaces.
    const runtimes = MEMBERS.map(() => FilmRuntime.createRuntime(options));
    runtimes.forEach(runtime => MEMBERS.forEach(member => {
      const entry = runtime.entries.find(item => item.entryId === member.entryId);
      if (!entry || Object.keys(member).some(key => entry[key] !== member[key]))
        throw new Error('Group member differs from the fixed original entry');
    }));
    return Object.freeze({render(value) {
      const globalFrame = frameNumber(value);
      const members = MEMBERS.map((member, index) => {
        const active = globalFrame >= member.windowStart && globalFrame < member.windowStart + member.duration;
        if (!active) return Object.freeze({entryId:member.entryId,active:false,result:null});
        const localFrame = globalFrame - member.windowStart;
        const result = runtimes[index].renderFrame(member.entryId, localFrame, {paper:false});
        if (result.entryId !== member.entryId || result.layerId !== member.layerId ||
            result.globalFrame !== globalFrame || result.localFrame !== localFrame || typeof result.fragment !== 'string')
          throw new Error('Original renderer returned an unexpected frame');
        return Object.freeze({entryId:member.entryId,active:true,result});
      });
      return Object.freeze({globalFrame,members:Object.freeze(members)});
    }});
  }
  function mount(document, window, FilmRuntime, options) {
    const roots = MEMBERS.map(member => document.getElementById('group-' + member.layerId));
    const failure = document.getElementById('group-failure');
    if (roots.some(root => !root) || !failure) throw new Error('Missing group DOM roots');
    // The initially visible error plate prevents a thrown script from silently
    // becoming a successful transparent image in a screenshot-only consumer.
    try {
      const query = new URLSearchParams(window.location.search);
      if (query.getAll('f').length !== 1 || [...query.keys()].some(key => key !== 'f'))
        throw new Error('Exactly one f query parameter is required');
      const result = createGroup(FilmRuntime, options).render(query.get('f'));
      result.members.forEach((member,index) => {
        const element = roots[index];
        element.innerHTML = member.active ? member.result.fragment : '';
        element.setAttribute('data-active', String(member.active));
        element.setAttribute('data-global-frame', String(result.globalFrame));
        if (member.active) {
          element.setAttribute('data-frame', String(member.result.localFrame));
          element.setAttribute('data-scene', member.result.sceneId);
          element.setAttribute('data-profile', member.result.profile);
          element.setAttribute('data-status', member.result.implemented ? 'scene-implemented-unverified' : 'candidate-not-production');
        }
      });
      window.__promo_gfx_group_status = Object.freeze({status:'RENDERED_UNVERIFIED',globalFrame:result.globalFrame});
      failure.style.display = 'none';
      return result;
    } catch (error) {
      roots.forEach(element => { element.innerHTML = ''; });
      failure.style.display = 'block';
      window.__promo_gfx_group_status = Object.freeze({status:'ERROR'});
      throw error;
    }
  }
  return Object.freeze({MEMBERS,frameNumber,createGroup,mount});
});
