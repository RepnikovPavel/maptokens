import assert from 'node:assert/strict';
import {drawingBufferSize,zoomAt,egoSize,imageViewMatrix,segmentDistance,niceDistance,formatDistance,EGO_LENGTH_M,EGO_WIDTH_M} from '../src/maptokens/kitscenes/web/view.js';
import {LayerVisibility,layerCategory} from '../src/maptokens/kitscenes/web/layers.js';

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

// Source-image corners stay registered in a viewport overlay after zoom/pan.
for(const image of [{left:0,top:100,width:800,height:400},{left:-1300,top:-700,width:4800,height:2400}]){
 const m=imageViewMatrix(800,600,image);
 for(const [x,y] of [[-1,-1],[1,1],[.4,-.7]]){
  assert.ok(Math.abs((m[0]*x+m[3]+1)*400-(image.left+(x+1)*image.width/2))<1e-9);
  assert.ok(Math.abs((1-m[5]*y-m[7])*300-(image.top+(1-y)*image.height/2))<1e-9);
 }
}
assert.equal(segmentDistance(3,4,0,0,6,0),4);
assert.equal(segmentDistance(8,0,0,0,6,0),2);
assert.equal(segmentDistance(3,4,0,0,0,0),5);
assert.deepEqual([3,9,24,70,900].map(niceDistance),[2,5,20,50,500]);
assert.equal(formatDistance(2.5),'2.5 m');assert.equal(formatDistance(2000),'2 km');

const layers=new LayerVisibility();
for(const kind of ['line_thin:dashed','stop_line','curbstone:low','road_border','arrow:straight','bike_marking:dashed']){
 assert.equal(layers.enabled(kind,'camera'),true);assert.equal(layers.enabled(kind,'map'),true);
}
for(const kind of ['traffic_sign:de306','traffic_light:red','pole','building']){
 assert.equal(layers.enabled(kind,'camera'),false);assert.equal(layers.enabled(kind,'map'),false);
}
assert.equal(layerCategory('symbol:bike'),'paint');
layers.setCategory('hd','camera',false);
assert.equal(layers.enabled('stop_line','camera'),false);assert.equal(layers.enabled('stop_line','map'),true);
layers.setType('stop_line','camera',true);assert.equal(layers.enabled('stop_line','camera'),true);
layers.setCategory('hd','camera',false);assert.equal(layers.enabled('stop_line','camera'),false);
console.log('Viewport projection, metric distances and independent layer defaults passed');
