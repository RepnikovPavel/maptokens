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
