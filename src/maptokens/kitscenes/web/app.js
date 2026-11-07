import {zoomAt,egoSize,multiply as mul,imageViewMatrix,niceDistance,formatDistance,EGO_LENGTH_M,EGO_WIDTH_M} from './view.js';
import {Renderer} from './render.js';
import {LayerVisibility,LAYER_CATEGORIES,layerCategory} from './layers.js';
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = t => `${String(Math.floor(Math.max(0,t)/60)).padStart(2,'0')}:${(Math.max(0,t)%60).toFixed(1).padStart(4,'0')}`;
const palette = ['#81bcb0','#e4d494','#80aed2','#dba581','#b496cd','#8aca87','#d78587','#a0bdbe','#c6d79b','#adadc8','#dbba77','#84a999'];
const rgba = hex => [1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)/255).concat(.85);
const toast = msg => { $('#toast').textContent=msg; $('#toast').classList.add('show'); setTimeout(()=>$('#toast').classList.remove('show'),3500); };
async function api(url){const r=await fetch(url);if(!r.ok)throw new Error(`Request failed (${r.status})`);return r.json();}
let wasm, scenes=[], meta=null, frame=0, playing=false, loading=false, loadToken=0, chooseToken=0, activeTab='overview', follow=true;
let center=[0,0], scale=70, mapDirty=true, imageDirty=true, lastTick=0, durationMs=100, lastRenderMs=0;
let mapGL, camGL, bufferCache=new Map(), fetching=new Map();
let zoom=1,pan=[0,0],followOffset=[0,0],selectedFeature=null,loopCount=0;
const layers=new LayerVisibility(),extras={ego:true,trajectory:false,fov:false,grid:false,rings:true};
let visibleCache={},rulerPoints=[],rulerHover=null,qualityTimer;

let fileRequest=0,filePreviewRequest=0,fileOffset=0,fileTimer;

function rotation(q){let[x,y,z,w]=q,s=2/(x*x+y*y+z*z+w*w||1);return [1-s*(y*y+z*z),s*(x*y-z*w),s*(x*z+y*w),s*(x*y+z*w),1-s*(x*x+z*z),s*(y*z-x*w),s*(x*z-y*w),s*(y*z+x*w),1-s*(x*x+y*y)];}
function rigidInverse(r,t){return [r[0],r[3],r[6],-(r[0]*t[0]+r[3]*t[1]+r[6]*t[2]),r[1],r[4],r[7],-(r[1]*t[0]+r[4]*t[1]+r[7]*t[2]),r[2],r[5],r[8],-(r[2]*t[0]+r[5]*t[1]+r[8]*t[2]),0,0,0,1];}
function poseNow(){return meta?.poses[meta.pose_indices[frame]];}
function layerColor(group){
 if(/curb|road_border/.test(group))return '#7bcc87';
 if(/stop_line/.test(group))return '#ed8d87';
 if(/pedestrian|crosswalk/.test(group))return '#dfcc82';
 if(/traffic_sign|traffic_light/.test(group))return '#c29ae1';
 if(/line_thin|line_thick/.test(group))return '#8cc3e2';
 let h=0;for(const c of group)h=((h*31+c.charCodeAt(0))>>>0);return palette[h%palette.length];
}
function colors(){return meta.groups.map(g=>rgba(layerColor(g)));}
function visibleSegments(view){return visibleCache[view]??(visibleCache[view]=meta.segments.filter(s=>layers.enabled(meta.groups[s[6]],view)));}
function invalidateLayers(){visibleCache={};mapDirty=imageDirty=true;renderLayerPanels();}
function cameraMatrix(){
 const p=poseNow(),cal=meta.camera_calibration;if(!p||!cal.intrinsics||!cal.T_to_reference)return null;
 const E=cal.T_to_reference.flat(),r=[E[0],E[1],E[2],E[4],E[5],E[6],E[8],E[9],E[10]],t=[E[3],E[7],E[11]];
 const view=mul(rigidInverse(r,t),rigidInverse(rotation(p.slice(4,8)),p.slice(1,4)));
 const intr=cal.intrinsics,[w,h]=meta.image_size,fx=intr.focal_length,cu=intr.principal_point_u,cv=intr.principal_point_v;
 return mul([2*fx/w,0,2*cu/w-1,0,0,-2*fx/h,1-2*cv/h,0,0,0,1,0,0,0,1,0],view);
}
function cameraImageRect(){
 const a=$('#camera-stage').getBoundingClientRect(),b=$('#camera-viewport').getBoundingClientRect();
 return {left:a.left-b.left,top:a.top-b.top,width:a.width,height:a.height};
}
function displayCameraMatrix(){
 const matrix=cameraMatrix();if(!matrix)return null;
 return mul(imageViewMatrix($('#camera-viewport').clientWidth,$('#camera-viewport').clientHeight,cameraImageRect()),matrix);
}
function mapPoint(p){const c=$('#map-canvas');return [c.clientWidth/2+(p[0]-center[0])*c.clientHeight/(2*scale),c.clientHeight/2-(p[1]-center[1])*c.clientHeight/(2*scale)];}
function mapWorld(x,y){const c=$('#map-canvas');return [center[0]+(x-c.clientWidth/2)*2*scale/c.clientHeight,center[1]-(y-c.clientHeight/2)*2*scale/c.clientHeight,0];}
function draw(){
 if(!meta||!wasm||!mapGL||(!mapDirty&&!imageDirty))return;
 if(mapDirty){
  const p=poseNow();if(follow&&p)center=[p[1]+followOffset[0],p[2]+followOffset[1]];
  const canvas=$('#map-canvas'),w=canvas.clientWidth,h=canvas.clientHeight,pixels=h/(2*scale);
  const matrix=[h/w/scale,0,0,-center[0]*h/w/scale,0,1/scale,0,-center[1]/scale,0,0,1,0,0,0,0,1];
  const c=colors(),sg=[],labels=[];
  const addColor=color=>{c.push(color);return c.length-1;};
  if(extras.grid){const grid=addColor([.18,.25,.29,.45]),step=niceDistance(scale/3);
   for(let x=Math.floor((center[0]-scale*w/h)/step)*step;x<center[0]+scale*w/h;x+=step)sg.push([x,center[1]-scale,0,x,center[1]+scale,0,grid]);
   for(let y=Math.floor((center[1]-scale)/step)*step;y<center[1]+scale;y+=step)sg.push([center[0]-scale*w/h,y,0,center[0]+scale*w/h,y,0,grid]);
  }
  if(extras.rings&&p){
   const ring=addColor([.43,.61,.72,.48]),step=10,diagonal=Math.hypot(scale,scale*w/h),offset=Math.hypot(center[0]-p[1],center[1]-p[2]);
   const orientation=rotation(p.slice(4,8)),north=follow?Math.atan2(orientation[3],orientation[0]):Math.PI/2;
   const directions=[['north',north],['east',north-Math.PI/2],['south',north+Math.PI],['west',north+Math.PI/2]];
   const start=Math.max(step,Math.ceil((offset-diagonal)/step)*step),limit=offset+diagonal;
   for(let r=start;r<=limit;r+=step){
    const count=Math.max(64,Math.min(512,Math.ceil(Math.PI*Math.sqrt(Math.max(1,r*pixels)/.2))));
    for(let i=0;i<count;i++){const a=i/count*Math.PI*2,b=(i+1)/count*Math.PI*2;sg.push([p[1]+r*Math.cos(a),p[2]+r*Math.sin(a),0,p[1]+r*Math.cos(b),p[2]+r*Math.sin(b),0,ring]);}
    for(const [direction,a] of directions){
     const [x,y]=mapPoint([p[1]+r*Math.cos(a),p[2]+r*Math.sin(a)]);
     if(x>35&&x<w-35&&y>65&&y<h-32)labels.push(`<span data-direction="${direction}" data-distance="${r}" style="left:${x}px;top:${y}px">${formatDistance(r)}</span>`);
    }
   }
  }
  sg.push(...visibleSegments('map'));
  if(extras.trajectory){const route=addColor([.36,.55,.65,.75]),history=addColor([.85,.92,.5,1]);
   for(let i=1;i<meta.poses.length;i++)sg.push([...meta.poses[i-1].slice(1,4),...meta.poses[i].slice(1,4),i<=meta.pose_indices[frame]?history:route]);
  }
  const car=$('#ego-car');car.toggleAttribute('hidden',!p||!extras.ego);
  if(p){
   const r=rotation(p.slice(4,8)),yaw=Math.atan2(r[3],r[0]),xy=mapPoint(p.slice(1,3));
   car.style.left=xy[0]/w*100+'%';car.style.top=xy[1]/h*100+'%';
   const [cw,ch]=egoSize(pixels);car.style.width=cw+'px';car.style.height=ch+'px';car.style.transform=`translate(-50%,-50%) rotate(${90-yaw*180/Math.PI}deg)`;
   const cal=meta.camera_calibration;
   if(extras.fov&&cal.intrinsics&&cal.T_to_reference){
    const E=cal.T_to_reference.flat(),intr=cal.intrinsics,cp=[p[1]+r[0]*E[3]+r[1]*E[7]+r[2]*E[11],p[2]+r[3]*E[3]+r[4]*E[7]+r[5]*E[11],0],fov=addColor([.5,.75,.88,.5]);
    for(const u of [0,meta.image_size[0]]){const x=(u-intr.principal_point_u)/intr.focal_length,ref=[E[0]*x+E[2],E[4]*x+E[6],E[8]*x+E[10]],dx=r[0]*ref[0]+r[1]*ref[1]+r[2]*ref[2],dy=r[3]*ref[0]+r[4]*ref[1]+r[5]*ref[2],n=Math.hypot(dx,dy),range=40;sg.push([...cp,cp[0]+dx/n*range,cp[1]+dy/n*range,0,fov]);}
   }
  }
  const marks=[];
  if($('#measure').checked&&rulerPoints.length){
   const points=rulerPoints.length===1&&rulerHover?[rulerPoints[0],rulerHover]:rulerPoints;marks.push(...points);
   if(points.length===2){const idx=addColor([1,.65,.3,1]);sg.push([...points[0],...points[1],idx]);$('#ruler-value').textContent=formatDistance(Math.hypot(points[1][0]-points[0][0],points[1][1]-points[0][1]));}
   else $('#ruler-value').textContent='A → B';
  }else $('#ruler-value').textContent='';
  mapGL.render(sg,matrix,c,{selected:selectedFeature,markers:marks});
  $('#range-labels').innerHTML=labels.join('');
  const distance=niceDistance(90/pixels);$('#map-scale').style.width=distance*pixels+'px';$('#map-scale').textContent=formatDistance(distance);
  mapDirty=false;
 }
 if(imageDirty){
  const m=displayCameraMatrix();camGL.render(m&&$('#overlay-toggle').checked?visibleSegments('camera'):[],m||Array(16).fill(0),colors(),{selected:selectedFeature,thickness:2,clip:cameraImageRect()});imageDirty=false;
 }
 lastRenderMs=(mapGL.renderMs||0)+(camGL.renderMs||0);$('#render-state').textContent=`${lastRenderMs.toFixed(1)} ms`;
}

function clearImages(){for(const b of bufferCache.values())URL.revokeObjectURL(b.url);bufferCache.clear();fetching.clear();}
async function fetchImage(i,scene=meta,native=false){
 const key=scene.id+':'+i+(native?':native':'');if(bufferCache.has(key))return bufferCache.get(key);if(fetching.has(key))return fetching.get(key);
 const promise=(async()=>{const requestUrl=native?`/api/scenes/${scene.id}/file?${new URLSearchParams({path:scene.camera+'/'+scene.images[i]})}`:`/api/scenes/${scene.id}/image/${i}?width=1600`;const r=await fetch(requestUrl);if(!r.ok)throw new Error('Camera frame unavailable');const blob=await r.blob(),url=URL.createObjectURL(blob),im=new Image();im.src=url;await im.decode();
 if(meta!==scene){URL.revokeObjectURL(url);return null;}const item={url,im};bufferCache.set(key,item);
 for(const[k,b]of bufferCache)if(bufferCache.size>12&&k!==key){URL.revokeObjectURL(b.url);bufferCache.delete(k);}return item;})();
 fetching.set(key,promise);try{return await promise;}finally{fetching.delete(key);}
}
async function setFrame(i){
 if(!meta?.images.length)return;
 i=Math.max(0,Math.min(meta.images.length-1,i));const scene=meta,ticket=++loadToken;loading=true;
 try{const item=await fetchImage(i,scene);if(ticket!==loadToken||meta!==scene||!item)return;frame=i;
 $('#camera-image').src=item.url;$('#camera-image').hidden=false;$('#camera-empty').style.display='none';
 $('#scrub').value=frame;$('#frame-counter').textContent=`${frame+1}/${meta.images.length}`;
 $('#frame-stamp').textContent=`${meta.timestamps[frame]||'—'} ns`;
 $('#elapsed').textContent=fmt(meta.times[frame]-meta.times[0]);$('#speed').textContent=meta.speeds[meta.pose_indices[frame]]?.toFixed(0)||'—';
 mapDirty=imageDirty=true;drawSpeedChart();renderMoment();draw();
 for(let j=1;j<=Math.min(4,meta.images.length-1);j++){const next=i+j;if(next<meta.images.length||$('#loop').checked)fetchImage(next%meta.images.length,scene).catch(()=>{});}
 if(!playing&&zoom>1.2)scheduleImageUpgrade();
 }catch(e){if(ticket===loadToken){toast(e.message);setPlaying(false);}}finally{if(ticket===loadToken)loading=false;}
}
function setPlaying(value){playing=value;$('#play').textContent=playing?'Ⅱ':'▶';lastTick=performance.now();if(!playing)scheduleImageUpgrade();}
function renderCatalog(){
 const q=$('#search').value.toLowerCase(),split=$('#split').value,ready=$('#ready-only').checked;
 const filtered=scenes.filter(s=>(!ready||s.ready)&&(!$('#hd-only').checked||s.maps)&&(!split||s.split===split)&&s.id.includes(q));
 const select=$('#scene-select');select.innerHTML=filtered.length?filtered.map(s=>`<option value="${s.id}" ${meta?.id===s.id?'selected':''} ${s.ready?'':'disabled'}>${s.id} · ${s.split}${s.maps?' · HD':''}${s.preview?' · preview':''}${s.ready?'':' · pending'}</option>`).join(''):'<option value="">No ready scenes</option>';
 $('#ready-count').textContent=`${scenes.filter(s=>s.ready).length}/${scenes.length}`;return filtered;
}
async function choose(sid,initial=0){
 const chooseTicket=++chooseToken;setPlaying(false);++loadToken;clearImages();$('#scene-title').textContent='Loading journey…';
 try{const data=await api(`/api/scenes/${sid}`);if(chooseTicket!==chooseToken)return;meta=data;frame=0;selectedFeature=null;$('#feature-inspector').hidden=true;follow=true;followOffset=[0,0];$('#follow').checked=true;visibleCache={};rulerPoints=[];rulerHover=null;loopCount=0;scale=70;zoom=1;pan=[0,0];cameraTransform();
 $('#scene-title').textContent=sid;$('#scene-split').textContent=`${data.split.toUpperCase()} / HDD ${scenes.find(s=>s.id===sid).disk+1}`;
 $('#scene-subtitle').textContent=`${data.images.length} f · ${data.features.length} map elements`;
 $('#camera-name').textContent=data.camera||'Camera unavailable';$('#map-label').textContent=data.availability.maps?'HD map':'Trajectory · HD map unavailable';$('#overlay-toggle').disabled=!data.availability.maps;fitCamera();
 $('#scrub').max=Math.max(0,data.images.length-1);$('#duration').textContent=fmt(data.times.at(-1)-data.times[0]);
 renderCatalog();renderDetails();renderLayerPanels();mapDirty=imageDirty=true;
 if(data.images.length)await setFrame(initial);else{$('#camera-empty').style.display='flex';$('#camera-empty').textContent='No camera frames';draw();}
 history.replaceState(null,'',`?scene=${sid}&frame=${initial}`);
 }catch(e){toast(e.message);$('#scene-title').textContent='Journey unavailable';}
}
function renderDetails(){
 if(!meta)return;let html='';
 if(activeTab==='overview'){
 const a=meta.availability;
 html=`<div class="facts"><div><label>RECORDING</label><strong>${fmt(meta.times.at(-1)-meta.times[0])}</strong></div><div><label>FRONT FRAMES</label><strong>${meta.images.length}</strong></div><div><label>MAP FEATURES</label><strong>${meta.features.length}</strong></div><div><label>PATH</label><strong>${meta.motion?.distance_m.toFixed(1)||'—'} m</strong></div><div><label>CAN FILES</label><strong>${a.can_file_count||'—'}</strong></div><div><label>GNSS FILES</label><strong>${a.gnss_file_count||'—'}</strong></div></div>${meta.receipt.mode==='preview'?'<p class="note">Preview · full archive SHA256 pending</p>':''}`;
 }else if(activeTab==='layers'){
 html=layerControls();
 }else if(activeTab==='calibration'){
 html=`<pre>${esc(JSON.stringify(meta.camera_calibration,null,2))}</pre>`;
 }else{
 html=`<div class="file-tools"><select id="file-group" aria-label="Metadata directory"><option value="">all metadata · ${meta.metadata_file_count}</option>${Object.entries(meta.file_groups).filter(([g])=>g).map(([g,n])=>`<option value="${esc(g)}/" ${g==='maps'?'selected':''}>${esc(g)} · ${n}</option>`).join('')}</select><input id="file-query" placeholder="filter files" aria-label="Filter metadata filenames"></div><div id="file-list"></div><div class="file-pages"><button id="files-prev" disabled>‹</button><span id="file-page"></span><button id="files-next" disabled>›</button></div><div id="file-preview"></div><details><summary>Archive receipt & integrity</summary><pre>${esc(JSON.stringify(meta.receipt,null,2))}</pre></details>`;
 }
 $('#detail-body').innerHTML=html;
 if(activeTab==='files')loadFiles(0);
}
async function loadFiles(offset=0){
 const sid=meta?.id,group=$('#file-group'),query=$('#file-query');if(!group||!query)return;
 const ticket=++fileRequest,params=new URLSearchParams({prefix:group.value,q:query.value,offset,limit:100});
 $('#files-prev').disabled=$('#files-next').disabled=true;
 try{
  const page=await api(`/api/scenes/${sid}/files?${params}`);
  if(ticket!==fileRequest||meta?.id!==sid||activeTab!=='files'||!$('#file-list'))return;
  fileOffset=page.offset;
  $('#file-list').innerHTML=page.paths.map(p=>`<div class="file-row"><a data-view-file="${esc(p)}" href="/api/scenes/${sid}/file?path=${encodeURIComponent(p)}" target="_blank" rel="noopener">${esc(p)}</a></div>`).join('');
  $('#file-page').textContent=page.total?`${page.offset+1}–${Math.min(page.total,page.offset+page.paths.length)} / ${page.total}`:'0 files';
  $('#files-prev').disabled=offset===0;$('#files-next').disabled=offset+page.paths.length>=page.total;
 }catch(e){if(ticket===fileRequest&&$('#file-list'))$('#file-list').textContent=e.message;}
}
async function openMetadataFile(path){
 const sid=meta?.id,ticket=++filePreviewRequest;
 try{
  const data=await api(`/api/scenes/${sid}/text?${new URLSearchParams({path})}`);
  if(ticket!==filePreviewRequest||meta?.id!==sid||activeTab!=='files'||!$('#file-preview'))return;
  $('#file-preview').innerHTML=`<a href="/api/scenes/${sid}/file?path=${encodeURIComponent(path)}" target="_blank" rel="noopener">${esc(path)} ↗</a>${data.truncated?'<span class="muted"> · first 256 KiB</span>':''}<pre>${esc(data.text)}</pre>`;
  $('#file-preview').scrollIntoView({block:'nearest'});
 }catch(e){toast(e.message);}
}
async function refresh(){
 try{
 const [status,list]=await Promise.all([api('/api/status'),api('/api/scenes')]);scenes=list;
 const n=list.filter(s=>s.ready).length,total=list.length;
 $('#download-state').textContent=status.stale?'stale':status.running?'running':Object.keys(status.errors||{}).length?'errors':n===total?'done':'stopped';
 $('#dl-speed').textContent=(status.speed_mbps||status.average_mbps||0).toFixed(1);$('#dl-done').textContent=`${status.mode==='full'?(status.scenes_done+status.scenes_skipped):n}/${total}`;
 $('#dl-progress').max=total;$('#dl-progress').value=status.mode==='full'?(status.scenes_done+status.scenes_skipped):n;$('#dl-bytes').textContent=`${((status.network_bytes||0)/1e9).toFixed(2)} GB this run`;
 const active=Object.values(status.active||{});$('#dl-active').textContent=`${active.length} active`;
 const indexing=active.filter(s=>s.phase==='index').length;
 $('#dl-detail').textContent=status.mode==='full'?`Full archives · ${((status.archive_bytes_done||0)/1e9).toFixed(1)}/${((status.archive_bytes_total||0)/1e9).toFixed(1)} GB · ${status.method} · ETA ${status.eta_seconds?(status.eta_seconds/3600).toFixed(1)+' h':'—'}`:`${indexing} indexing tar headers · ${active.length-indexing} transferring data. ${Object.keys(status.errors||{}).length} errors. Transport: ${status.benchmark?.winner||'Python'}.`;
 $('#revision').textContent=`REV ${status.plan.revision.slice(0,12)}`;
 const splits=[...new Set(list.map(s=>s.split))].sort();if($('#split').options.length===1)for(const s of splits){const o=new Option(s,s);$('#split').add(o);}
 renderCatalog();
 const current=list.find(s=>s.id===meta?.id);
 if(current?.ready&&current.view_version!==meta.receipt_version){
  const sid=meta.id,updated=await api(`/api/scenes/${sid}`);
  if(meta.id===sid){const id=selectedFeature===null?null:meta.features[selectedFeature]?.id;meta=updated;visibleCache={};const index=id===null?-1:meta.features.findIndex(f=>f.id===id);selectFeature(index<0?null:index);renderDetails();renderLayerPanels();await setFrame(frame);}
 }
 if(!meta&&n){const params=new URLSearchParams(location.search),id=params.get('scene');if(list.some(s=>s.id===id&&s.ready&&!s.maps))$('#hd-only').checked=false;await choose(list.find(s=>s.id===id&&s.ready)?.id||list.find(s=>s.ready&&s.maps)?.id||list.find(s=>s.ready).id,Number(params.get('frame')||0));}
 }catch(e){$('#download-state').textContent='Server connection interrupted';}
}

function fitCamera(){
 const box=$('#camera-viewport'),stage=$('#camera-stage'),ratio=meta?meta.image_size[0]/meta.image_size[1]:1.6;
 const w=Math.min(box.clientWidth,box.clientHeight*ratio);stage.style.width=w+'px';stage.style.height=w/ratio+'px';
}
function drawSpeedChart(){
 const c=$('#speed-chart'),r=c.getBoundingClientRect(),d=Math.min(devicePixelRatio,2);c.width=r.width*d;c.height=r.height*d;const ctx=c.getContext('2d');ctx.scale(d,d);ctx.clearRect(0,0,r.width,r.height);
 if(!meta?.pose_indices.length)return;const v=meta.pose_indices.map(i=>meta.speeds[i]||0),max=Math.max(10,...v);
 ctx.beginPath();ctx.moveTo(0,r.height);v.forEach((s,i)=>ctx.lineTo(i/Math.max(1,v.length-1)*r.width,r.height-s/max*(r.height-3)));ctx.lineTo(r.width,r.height);ctx.closePath();ctx.fillStyle='#759fac40';ctx.fill();
 const x=frame/Math.max(1,v.length-1)*r.width;ctx.strokeStyle='#b5d7e6';ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,r.height);ctx.stroke();
}
function renderMoment(){
 const p=poseNow();if(!p)return;const i=meta.pose_indices[frame],r=rotation(p.slice(4,8)),heading=Math.atan2(r[3],r[0])*180/Math.PI;
 const dt=(meta.times[frame]-p[0])*1000;
 $('#moment').innerHTML=`t ${meta.times[frame].toFixed(6)} s · pose Δ ${dt.toFixed(2)} ms<br>xyz ${p.slice(1,4).map(v=>v.toFixed(2)).join(' ')} m<br>yaw ${heading.toFixed(1)}° · v ${meta.speeds[i].toFixed(1)} km/h<br>a ${meta.motion?.acceleration_mps2[i].toFixed(2)||'—'} m/s² · ω ${meta.motion?.yaw_rate_dps[i].toFixed(2)||'—'} °/s`;
}
function fitTrack(){
 if(!meta?.poses.length)return;let xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity;
 for(const p of meta.poses){xmin=Math.min(xmin,p[1]);xmax=Math.max(xmax,p[1]);ymin=Math.min(ymin,p[2]);ymax=Math.max(ymax,p[2]);}
 const r=$('#map-canvas').getBoundingClientRect();center=[(xmin+xmax)/2,(ymin+ymax)/2];scale=Math.max(12,(ymax-ymin)*.65,(xmax-xmin)*r.height/r.width*.65);mapFollow(false);
}
function selectFeature(index){
 selectedFeature=index;mapDirty=imageDirty=true;
 const panel=$('#feature-inspector');panel.hidden=index===null;
 if(index===null)return;const f=meta.features[index];
 $('#feature-title').textContent=`${f.kind} #${f.id}`;
 $('#feature-fields').innerHTML=`<div class="note">${f.nodes??'—'} points</div><pre>${esc(JSON.stringify(f.tags,null,2))}</pre>`;
}
function pickMap(e){
 if(!meta)return;draw();const r=$('#map-canvas').getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;
 if($('#measure').checked){const world=mapWorld(x,y);rulerPoints=rulerPoints.length===1?[rulerPoints[0],world]:[world];rulerHover=null;mapDirty=true;return;}
 const feature=mapGL.pick(x,y);if(feature!==null){selectFeature(feature);return;}
 if(extras.trajectory){const world=mapWorld(x,y);let nearest=Infinity,index=-1;meta.pose_indices.forEach((pi,i)=>{const p=meta.poses[pi],d=Math.hypot(p[1]-world[0],p[2]-world[1]);if(d<nearest){nearest=d;index=i;}});if(index>=0&&nearest<12*2*scale/r.height){setPlaying(false);setFrame(index);return;}}
 selectFeature(null);
}
function pickCamera(e){if(!meta)return;draw();const r=$('#camera-viewport').getBoundingClientRect();selectFeature(camGL.pick(e.clientX-r.left,e.clientY-r.top));}
function scheduleImageUpgrade(){clearTimeout(qualityTimer);qualityTimer=setTimeout(async()=>{if(playing||!meta||zoom<=1.2)return;const scene=meta,i=frame,ticket=loadToken;try{const item=await fetchImage(i,scene,true);if(item&&meta===scene&&frame===i&&ticket===loadToken&&!playing)$('#camera-image').src=item.url;}catch{}},120);}
function cameraTransform(){$('#camera-stage').style.transform=`translate(${pan[0]}px,${pan[1]}px) scale(${zoom})`;imageDirty=true;scheduleImageUpgrade();}
function mapFollow(value,reset=false){follow=value;$('#follow').checked=value;if(reset)followOffset=[0,0];mapDirty=true;}
function layerControls(){
 if(!meta)return '';
 const row=(kind,label,category=false)=>`<tr><td>${esc(label)}</td>${['camera','map'].map(view=>{const values=category?meta.groups.filter(g=>layerCategory(g)===kind).map(g=>layers.enabled(g,view)):[layers.enabled(kind,view)];const checked=values.length?values.every(Boolean):!!layers.defaults[view][kind];return `<td><input type="checkbox" data-${category?'category':'kind'}="${esc(kind)}" data-view="${view}" ${checked?'checked':''} ${values.some(Boolean)&&!checked?'data-mixed="true"':''} aria-label="${esc(label)} ${view}"></td>`;}).join('')}</tr>`;
 return `<table class="layer-table"><thead><tr><th>Element</th><th>Camera</th><th>HD map</th></tr></thead><tbody>${LAYER_CATEGORIES.map(([k,label])=>row(k,label,true)).join('')}</tbody></table><details><summary>Types</summary><table class="layer-table"><thead><tr><th>Type</th><th>Camera</th><th>HD map</th></tr></thead><tbody>${meta.groups.map(g=>row(g,g)).join('')}</tbody></table></details><div class="map-extras">${[['ego','Ego car'],['trajectory','Ego trajectory'],['fov','Camera FOV'],['grid','Grid'],['rings','Distance rings']].map(([key,label])=>`<label><input type="checkbox" data-extra="${key}" ${extras[key]?'checked':''}>${label}</label>`).join('')}</div>`;
}
function renderLayerPanels(){
 $('#rings-toggle').checked=extras.rings;
 $('#layer-controls').innerHTML=layerControls();if(activeTab==='layers')$('#detail-body').innerHTML=layerControls();
 document.querySelectorAll('input[data-mixed]').forEach(el=>el.indeterminate=true);
}
function layerChanged(e){const el=e.target,view=el.dataset.view;
 if(el.dataset.category){layers.setCategory(el.dataset.category,view,el.checked);invalidateLayers();}
 else if(el.dataset.kind){layers.setType(el.dataset.kind,view,el.checked);invalidateLayers();}
 else if(el.dataset.extra){extras[el.dataset.extra]=el.checked;mapDirty=true;renderLayerPanels();}
}
function setup(){
 $('#scene-select').onchange=e=>{if(e.target.value)choose(e.target.value);};
 for(const [id,dir] of [['#prev-scene',-1],['#next-scene',1]])$(id).onclick=()=>{const list=scenes.filter(s=>s.ready&&(!$('#split').value||s.split===$('#split').value)),i=list.findIndex(s=>s.id===meta?.id),n=list[i+dir];if(n)choose(n.id);};
 $('#metadata-toggle').onclick=()=>{$('#inspector').hidden=!$('#inspector').hidden;$('#metadata-toggle').classList.toggle('active',!$('#inspector').hidden);};
 $('#layers-toggle').onclick=()=>$('#layer-panel').hidden=!$('#layer-panel').hidden;$('#layers-close').onclick=()=>$('#layer-panel').hidden=true;$('#layer-controls').onchange=layerChanged;$('#feature-close').onclick=()=>selectFeature(null);
 $('#download-toggle').onclick=()=>$('#download-popover').hidden=!$('#download-popover').hidden;
 $('#rings-toggle').onchange=layerChanged;
 $('#overlay-toggle').onchange=()=>imageDirty=true;$('#whole-trip').onclick=fitTrack;$('#measure').onchange=()=>{mapDirty=true;$('#map-canvas').classList.toggle('measuring',$('#measure').checked);};$('#ruler-clear').onclick=()=>{rulerPoints=[];rulerHover=null;mapDirty=true;};
 for(const s of ['#search','#split','#ready-only','#hd-only'])$(s).addEventListener('input',()=>{const list=renderCatalog();if(meta&&!list.some(s=>s.id===meta.id)&&list.some(s=>s.ready))choose(list.find(s=>s.ready).id);});
 $('#play').onclick=()=>{if(meta)setPlaying(!playing);};$('#prev').onclick=()=>{setPlaying(false);setFrame(frame-1);};$('#next').onclick=()=>{setPlaying(false);setFrame(frame+1);};
 $('#scrub').oninput=e=>{setPlaying(false);setFrame(Number(e.target.value));};
 $('.tabs').onclick=e=>{if(e.target.dataset.tab){activeTab=e.target.dataset.tab;document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('active',b===e.target));renderDetails();if(activeTab==='layers')renderLayerPanels();}};
 $('#detail-body').onchange=e=>{if(e.target.id==='file-group')loadFiles(0);else layerChanged(e);};
 $('#detail-body').oninput=e=>{if(e.target.id==='file-query'){clearTimeout(fileTimer);++fileRequest;fileTimer=setTimeout(()=>loadFiles(0),250);}};
 $('#detail-body').onclick=e=>{if(e.target.id==='files-prev')loadFiles(Math.max(0,fileOffset-100));else if(e.target.id==='files-next')loadFiles(fileOffset+100);else if(e.target.dataset.viewFile&&!e.ctrlKey&&!e.metaKey){e.preventDefault();openMetadataFile(e.target.dataset.viewFile);}};
 $('#follow').onchange=e=>mapFollow(e.target.checked,e.target.checked);$('#reset-map').onclick=()=>{scale=70;mapFollow(true,true);};
 $('#zoom-in').onclick=()=>{scale=Math.max(8,scale/1.3);mapDirty=true;};$('#zoom-out').onclick=()=>{scale=Math.min(2000,scale*1.3);mapDirty=true;};
 const map=$('#map-canvas');let drag=null;
 map.onpointerdown=e=>{if(e.button!==0)return;drag=[e.clientX,e.clientY,...center];map.setPointerCapture(e.pointerId);};
 map.onpointermove=e=>{if($('#measure').checked&&rulerPoints.length===1){const r=map.getBoundingClientRect();rulerHover=mapWorld(e.clientX-r.left,e.clientY-r.top);mapDirty=true;}if(!drag||Math.hypot(e.clientX-drag[0],e.clientY-drag[1])<3)return;center=[drag[2]-(e.clientX-drag[0])*2*scale/map.clientHeight,drag[3]+(e.clientY-drag[1])*2*scale/map.clientHeight];if(follow&&poseNow())followOffset=[center[0]-poseNow()[1],center[1]-poseNow()[2]];mapDirty=true;};
 map.onpointerup=e=>{if(drag&&Math.hypot(e.clientX-drag[0],e.clientY-drag[1])<4)pickMap(e);drag=null;};map.onpointercancel=()=>drag=null;map.ondblclick=()=>mapFollow(true,true);
 map.addEventListener('wheel',e=>{e.preventDefault();scale=Math.max(8,Math.min(2000,scale*Math.exp(e.deltaY*.001)));mapDirty=true;},{passive:false});
 const cam=$('#camera-viewport');let cdrag=null;
 cam.onpointerdown=e=>{if(e.button!==0||e.target.closest('button,input,select,a'))return;cdrag=[e.clientX,e.clientY,...pan];cam.setPointerCapture(e.pointerId);};cam.onpointermove=e=>{if(cdrag&&Math.hypot(e.clientX-cdrag[0],e.clientY-cdrag[1])>=3){pan=[cdrag[2]+e.clientX-cdrag[0],cdrag[3]+e.clientY-cdrag[1]];cameraTransform();}};
 cam.onpointerup=e=>{if(cdrag&&Math.hypot(e.clientX-cdrag[0],e.clientY-cdrag[1])<4)pickCamera(e);cdrag=null;};cam.onpointercancel=()=>cdrag=null;cam.ondblclick=()=>{zoom=1;pan=[0,0];cameraTransform();};
 cam.addEventListener('wheel',e=>{e.preventDefault();const r=cam.getBoundingClientRect(),view=zoomAt(zoom,pan,[e.clientX-r.left-r.width/2,e.clientY-r.top-r.height/2],zoom*Math.exp(-e.deltaY*.001));zoom=view.zoom;pan=view.pan;cameraTransform();},{passive:false});
 $('#fullscreen').onclick=()=>{if(document.fullscreenElement)document.exitFullscreen();else cam.requestFullscreen().catch(()=>toast('Fullscreen unavailable'));};
 $('#copy-link').onclick=async()=>{const url=`${location.origin}/?scene=${meta?.id||''}&frame=${frame}`;try{await navigator.clipboard.writeText(url);toast('Scene link copied');}catch{const input=document.createElement('textarea');input.value=url;document.body.append(input);input.select();document.execCommand('copy');input.remove();toast('Scene link copied');}};
 $('#help').onclick=()=>$('#help-dialog').showModal();$('#close-help').onclick=()=>$('#help-dialog').close();
 document.onkeydown=e=>{if(e.target.closest('input,select,button')||$('#help-dialog').open)return;if(e.code==='Space'){e.preventDefault();if(meta)setPlaying(!playing);}else if(e.key==='ArrowRight'){setPlaying(false);setFrame(frame+1);}else if(e.key==='ArrowLeft'){setPlaying(false);setFrame(frame-1);}else if(e.key==='Home'){setPlaying(false);setFrame(0);}else if(e.key==='End'&&meta){setPlaying(false);setFrame(meta.images.length-1);}else if(e.key==='Escape'){selectFeature(null);$('#layer-panel').hidden=true;}};
 new ResizeObserver(()=>{fitCamera();mapDirty=imageDirty=true;drawSpeedChart();}).observe($('.workspace'));
 new ResizeObserver(()=>{fitCamera();mapDirty=imageDirty=true;}).observe($('#camera-viewport'));
 new ResizeObserver(()=>{mapDirty=true;}).observe($('.map-viewport'));
}
function tick(now){
 if(playing&&meta&&!loading){let interval=meta.times[frame+1]-meta.times[frame];if(!Number.isFinite(interval)||interval<=0)interval=.1;
 if(now-lastTick>=interval*1000/Number($('#rate').value)){lastTick=now;if(frame+1<meta.images.length)setFrame(frame+1);else if($('#loop').checked){loopCount++;setFrame(0);}else setPlaying(false);}}
 draw();requestAnimationFrame(tick);
}
setup();
try{
 const r=await fetch('/project.wasm');const module=await WebAssembly.instantiate(await r.arrayBuffer());wasm=module.instance.exports;
 mapGL=new Renderer($('#map-canvas'),wasm,[.045,.07,.08,1]);camGL=new Renderer($('#camera-overlay'),wasm);
 $('#render-state').textContent='GPU ready';window.explorerDiagnostics=()=>({wasm:!!wasm,webgl:!!mapGL,scene:meta?.id,frame,renderMs:lastRenderMs,cacheFrames:bufferCache.size,playing,loading,mapSegments:meta?.segments.length,center,scale,follow,followOffset,loopCount,extras:{...extras},selectedFeature:selectedFeature===null?null:{index:selectedFeature,id:meta.features[selectedFeature].id},visible:{camera:meta?visibleSegments('camera').length:0,map:meta?visibleSegments('map').length:0},selectionRender:{camera:camGL?.last?{segments:camGL.last.selectedSegments,points:camGL.last.selectedPoints}:null,map:mapGL?.last?{segments:mapGL.last.selectedSegments,points:mapGL.last.selectedPoints}:null},cameraDrawCount:camGL?.drawCount,pose:poseNow()?.slice(1,4),cameraView:{zoom,pan},egoSizeMeters:[EGO_WIDTH_M,EGO_LENGTH_M],cameraProjection:meta?displayCameraMatrix():null,cameraBuffer:camGL?{width:camGL.gl.drawingBufferWidth,height:camGL.gl.drawingBufferHeight,cssWidth:camGL.canvas.clientWidth,cssHeight:camGL.canvas.clientHeight}:null,posePixels:poseNow()?{x:$('#map-canvas').clientWidth/2+(poseNow()[1]-center[0])*$('#map-canvas').clientHeight/(2*scale),y:$('#map-canvas').clientHeight/2-(poseNow()[2]-center[1])*$('#map-canvas').clientHeight/(2*scale)}:null});
}catch(e){$('#render-state').textContent='Graphics unavailable';toast(e.message);}
await refresh();setInterval(refresh,10000);requestAnimationFrame(tick);
