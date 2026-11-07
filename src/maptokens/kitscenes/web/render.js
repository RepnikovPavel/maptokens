import {drawingBufferSize,segmentDistance} from './view.js';
const HIGHLIGHT=[1,.86,.22,1],HALO=[.025,.04,.06,.95];
export class Renderer {
 constructor(canvas,wasm,clear=null){
  this.canvas=canvas;this.wasm=wasm;this.clear=clear;this.last=null;this.drawCount=0;
  const gl=this.gl=canvas.getContext('webgl2',{alpha:!clear,antialias:true,preserveDrawingBuffer:false});
  if(!gl)throw new Error('WebGL2 is unavailable');
  const max=gl.getParameter(gl.MAX_RENDERBUFFER_SIZE),vp=gl.getParameter(gl.MAX_VIEWPORT_DIMS);
  this.maxWidth=Math.min(max,vp[0]);this.maxHeight=Math.min(max,vp[1]);
  const fragment=`#version 300 es
  precision highp float; in vec4 c; out vec4 frag;
  void main(){if(c.a<.01)discard;frag=c;}`;
  this.lines=this.program(`#version 300 es
  precision highp float; layout(location=0) in vec4 ends; layout(location=1) in vec4 color;
  uniform vec2 viewport; uniform float size; out vec4 c;
  void main(){vec2 d=(ends.zw-ends.xy)*viewport;float len=length(d);vec2 n=len>.0001?vec2(-d.y,d.x)/len:vec2(0);
   float t=gl_VertexID>=2?1.:0.;float side=(gl_VertexID==0||gl_VertexID==2)?-1.:1.;
   gl_Position=vec4(mix(ends.xy,ends.zw,t)+n*side*size/viewport,0,1);c=color;}`,fragment);
  this.dots=this.program(`#version 300 es
  precision highp float;layout(location=0) in vec4 ends;layout(location=1) in vec4 color;
  uniform vec2 viewport;uniform float size;out vec4 c;out vec2 uv;
  void main(){uv=vec2((gl_VertexID>=2)?1.:-1.,(gl_VertexID==0||gl_VertexID==2)?-1.:1.);
   gl_Position=vec4(ends.xy+uv*size/viewport,0,1);c=color;}`,
  `#version 300 es
  precision highp float;in vec4 c;in vec2 uv;out vec4 frag;
  void main(){float r=length(uv);float a=1.-smoothstep(1.-fwidth(r),1.,r);if(a<=0.)discard;frag=vec4(c.rgb,c.a*a);}`);
  this.buffer=gl.createBuffer();this.vao=gl.createVertexArray();gl.bindVertexArray(this.vao);gl.bindBuffer(gl.ARRAY_BUFFER,this.buffer);
  for(let i=0;i<2;i++){gl.enableVertexAttribArray(i);gl.vertexAttribPointer(i,4,gl.FLOAT,false,32,i*16);gl.vertexAttribDivisor(i,1);}
  gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);
 }
 program(vertex,fragment){
  const gl=this.gl,program=gl.createProgram();
  for(const [type,source] of [[gl.VERTEX_SHADER,vertex],[gl.FRAGMENT_SHADER,fragment]]){
   const shader=gl.createShader(type);gl.shaderSource(shader,source);gl.compileShader(shader);
   if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(shader));gl.attachShader(program,shader);
  }
  gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(program));return program;
 }
 project(segments,matrix){
  const w=this.wasm,result=new Float32Array(segments.length*4);
  new Float32Array(w.memory.buffer,w.matrix(),16).set(matrix);
  for(let offset=0;offset<segments.length;offset+=200000){
   const n=Math.min(200000,segments.length-offset),input=new Float32Array(w.memory.buffer,w.points(),n*6);
   for(let i=0;i<n;i++)for(let j=0;j<6;j++)input[i*6+j]=segments[offset+i][j];
   w.project(n,.2);result.set(new Float32Array(w.memory.buffer,w.result(),n*4),offset*4);
  }
  return result;
 }
 pass(projected,colors,size,points=false){
  const n=projected.length/4;if(!n)return;
  const gl=this.gl,data=new Float32Array(n*8),program=points?this.dots:this.lines;
  for(let i=0;i<n;i++){data.set(projected.subarray(i*4,i*4+4),i*8);data.set(colors.length===4&&typeof colors[0]==='number'?colors:colors[i],i*8+4);}
  gl.useProgram(program);gl.bindVertexArray(this.vao);gl.bindBuffer(gl.ARRAY_BUFFER,this.buffer);gl.bufferData(gl.ARRAY_BUFFER,data,gl.DYNAMIC_DRAW);
  gl.uniform2f(gl.getUniformLocation(program,'viewport'),gl.drawingBufferWidth,gl.drawingBufferHeight);
  gl.uniform1f(gl.getUniformLocation(program,'size'),size*this.ratio);
  gl.drawArraysInstanced(gl.TRIANGLE_STRIP,0,4,n);
 }
 render(segments,matrix,colors,{selected=null,thickness=1.8,clip=null,markers=[]}={}){
  const started=performance.now(),gl=this.gl,width=this.canvas.clientWidth,height=this.canvas.clientHeight;if(!width||!height)return;
  const [w,h]=drawingBufferSize(width,height,devicePixelRatio,this.maxWidth,this.maxHeight);
  if(this.canvas.width!==w||this.canvas.height!==h){this.canvas.width=w;this.canvas.height=h;}
  this.ratio=gl.drawingBufferWidth/width;gl.viewport(0,0,gl.drawingBufferWidth,gl.drawingBufferHeight);
  gl.disable(gl.SCISSOR_TEST);gl.clearColor(...(this.clear||[0,0,0,0]));gl.clear(gl.COLOR_BUFFER_BIT);
  if(clip){
   gl.enable(gl.SCISSOR_TEST);
   const left=Math.max(0,clip.left),top=Math.max(0,clip.top),right=Math.min(width,clip.left+clip.width),bottom=Math.min(height,clip.top+clip.height);
   gl.scissor(Math.floor(left*this.ratio),Math.floor((height-bottom)*gl.drawingBufferHeight/height),Math.max(0,Math.ceil((right-left)*this.ratio)),Math.max(0,Math.ceil((bottom-top)*gl.drawingBufferHeight/height)));
  }
  const projected=this.project(segments,matrix);
  this.pass(projected,segments.map(s=>colors[s[6]]||[.8,.8,.8,1]),thickness);
  const chosen=[],points=[],seen=new Set();
  for(let i=0;i<segments.length;i++)if(selected!==null&&segments[i][7]===selected){
   chosen.push(...projected.subarray(i*4,i*4+4));
   for(const p of [segments[i].slice(0,3),segments[i].slice(3,6)])if(!seen.has(p.join(','))){seen.add(p.join(','));points.push([...p,...p]);}
  }
  const line=new Float32Array(chosen),dots=this.project(points,matrix);
  this.pass(line,HALO,7);this.pass(line,HIGHLIGHT,3);
  this.pass(dots,HALO,11,true);this.pass(dots,HIGHLIGHT,7,true);
  if(markers.length){const p=this.project(markers.map(v=>[...v,...v]),matrix);this.pass(p,HALO,11,true);this.pass(p,[1,.65,.3,1],7,true);}
  this.last={segments,projected,width,height,clip,selectedSegments:line.length/4,selectedPoints:points.length};
  this.drawCount++;this.renderMs=performance.now()-started;
 }
 pick(x,y,radius=8){
  const data=this.last;if(!data)return null;
  if(data.clip&&(x<data.clip.left||y<data.clip.top||x>data.clip.left+data.clip.width||y>data.clip.top+data.clip.height))return null;
  let closest=radius,feature=null;
  data.segments.forEach((s,i)=>{
   if(s[7]===undefined)return;const q=data.projected.subarray(i*4,i*4+4);
   const d=segmentDistance(x,y,(q[0]+1)*data.width/2,(1-q[1])*data.height/2,(q[2]+1)*data.width/2,(1-q[3])*data.height/2);
   if(d<closest){closest=d;feature=s[7];}
  });return feature;
 }
}
