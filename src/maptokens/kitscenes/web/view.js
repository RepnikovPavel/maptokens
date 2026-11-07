// All view navigation is separate from the dataset's camera calibration.
export const EGO_LENGTH_M=4.7;
export const EGO_WIDTH_M=2.35;

export function drawingBufferSize(width,height,dpr,maxWidth,maxHeight){
 const ratio=Math.min(2,dpr,maxWidth/Math.max(1,width),maxHeight/Math.max(1,height),
                      Math.sqrt(16*1024*1024/Math.max(1,width*height)));
 return [Math.max(1,Math.floor(width*ratio)),Math.max(1,Math.floor(height*ratio))];
}

export function zoomAt(zoom,pan,pointer,nextZoom){
 const next=Math.max(1,Math.min(6,nextZoom)),ratio=next/zoom;
 return {zoom:next,pan:[pointer[0]-(pointer[0]-pan[0])*ratio,
                        pointer[1]-(pointer[1]-pan[1])*ratio]};
}

export function egoSize(pixelsPerMeter){
 return [EGO_WIDTH_M*pixelsPerMeter,EGO_LENGTH_M*pixelsPerMeter];
}

export function multiply(a,b){
 const c=Array(16).fill(0);
 for(let i=0;i<4;i++)for(let j=0;j<4;j++)for(let k=0;k<4;k++)c[i*4+j]+=a[i*4+k]*b[k*4+j];
 return c;
}
// Put source-image NDC into an untransformed, viewport-sized overlay.
export function imageViewMatrix(width,height,image){
 return [image.width/width,0,0,2*(image.left+image.width/2)/width-1,
         0,image.height/height,0,1-2*(image.top+image.height/2)/height,
         0,0,1,0,0,0,0,1];
}
export function segmentDistance(x,y,a,b,c,d){
 const dx=c-a,dy=d-b,length=dx*dx+dy*dy;
 const t=length?Math.max(0,Math.min(1,((x-a)*dx+(y-b)*dy)/length)):0;
 return Math.hypot(x-a-t*dx,y-b-t*dy);
}
export function niceDistance(value){
 const power=10**Math.floor(Math.log10(Math.max(.001,value))),v=value/power;
 return (v>=5?5:v>=2?2:1)*power;
}
export function formatDistance(value){return value>=1000?`${+(value/1000).toFixed(2)} km`:`${+value.toFixed(value<10?2:value<100?1:0)} m`;}
