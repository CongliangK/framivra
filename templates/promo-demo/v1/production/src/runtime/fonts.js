'use strict';
const review=require('../assets/provider-review.json');
function provider(id,raw){const approved=review.providers[id]?.status==='PASS';return {...raw,reviewStatus:approved?'INDEPENDENTLY_APPROVED':raw.reviewStatus};}
module.exports={sourceLabels:provider('sourceLabels',require('../assets/fonts/source-label-glyphs.json')),brand:provider('brand',require('../assets/fonts/brand-glyphs.json'))};
