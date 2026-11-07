import assert from 'node:assert/strict';
import {drawingBufferSize,zoomAt,egoSize,EGO_LENGTH_M,EGO_WIDTH_M} from '../src/maptokens/kitscenes/web/view.js';

// Zoom at the pointer must keep its image coordinate fixed, even after a pan.
for(const zoom of [1,2,4]){
 const pan=[91,-63],pointer=[127,54],image=pointer.map((p,i)=>(p-pan[i])/zoom);
 const next=zoomAt(zoom,pan,pointer,zoom*1.5);
 image.forEach((p,i)=>assert.ok(Math.abs(p*next.zoom+next.pan[i]-pointer[i])<1e-9));
}
// The GPU budget is in untransformed CSS pixels, independent of CSS view zoom.
assert.deepEqual(drawingBufferSize(900,600,2,8192,8192),[1800,1200]);
const [w,h]=drawingBufferSize(12000,8000,2,8192,8192);
assert.ok(w<=8192&&h<=8192&&w*h<=16*1024*1024);
assert.ok(Math.abs(w/h-1.5)<.001);
// At close and distant map scales the car occupies the same metres.
for(const metresPerPixel of [.01,.5,4,20]){
 const size=egoSize(1/metresPerPixel);
 assert.ok(Math.abs(size[0]*metresPerPixel-EGO_WIDTH_M)<1e-9);
 assert.ok(Math.abs(size[1]*metresPerPixel-EGO_LENGTH_M)<1e-9);
}
console.log('Camera pointer anchor, GPU buffer budget and metric ego size passed');
