import fs from 'node:fs';
import assert from 'node:assert/strict';
const {instance:{exports:w}}=await WebAssembly.instantiate(fs.readFileSync(new URL('../src/maptokens/kitscenes/web/project.wasm',import.meta.url)));
const points=new Float32Array(w.memory.buffer,w.points(),12),matrix=new Float32Array(w.memory.buffer,w.matrix(),16),out=new Float32Array(w.memory.buffer,w.result(),8);
// Pinhole x/z,y/z. One line is wholly behind the near plane.
matrix.set([1,0,0,0,0,1,0,0,0,0,1,0,0,0,1,0]);points.set([1,2,2,3,4,4,1,0,-1,2,0,-2]);
assert.equal(w.project(2,.2),1);assert.deepEqual(Array.from(out.slice(0,4)),[.5,1,.75,1]);assert.deepEqual(Array.from(out.slice(4,8)),[4,4,4,4]);
// Crossing the near plane must produce a finite clipped segment.
points.set([0,0,-1,1,1,1]);assert.equal(w.project(1,.2),1);assert.ok(Array.from(out.slice(0,4)).every(Number.isFinite));
console.log('WASM projection / near clipping passed');
