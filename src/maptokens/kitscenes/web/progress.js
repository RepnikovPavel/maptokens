export function countdown(seconds){
 const n=Math.max(0,Math.ceil(seconds)),days=Math.floor(n/86400),hours=Math.floor(n/3600)%24;
 return (days?`${days}d `:'')+[hours,Math.floor(n/60)%60,n%60].map(v=>String(v).padStart(2,'0')).join(':');
}

export class DownloadMeters {
 constructor(){this.data=null;this.failed=false;setInterval(()=>this.tick(),1000);}
 update(data){
  this.data=data;this.received=performance.now();this.failed=false;
  const $=id=>document.getElementById(id);
  $('dl-progress').max=100;$('dl-progress').value=data.download_percent;
  $('dl-percent').textContent=`${data.download_percent.toFixed(2)}%`;
  $('dl-volume').textContent=`${(data.downloaded_bytes/1e9).toFixed(1)} / ${(data.total_bytes/1e9).toFixed(1)} GB`;
  $('dl-ready-progress').max=Math.max(1,data.total_scenes);$('dl-ready-progress').value=data.ready_scenes;
  $('dl-ready-percent').textContent=`${data.ready_percent.toFixed(2)}%`;
  $('dl-ready-count').textContent=`${data.ready_scenes}/${data.total_scenes}`;
  const next=data.next,bar=$('next-trip-progress');
  bar.value=next?next.percent:data.ready_scenes===data.total_scenes?100:0;
  $('next-trip-phase').textContent=next?(next.phase==='download'?`${next.percent.toFixed(1)}%`:next.phase==='verify'?'verifying':'unpacking'):'';
  $('next-trip-meter').title=next?`${next.id} · ${next.split} · ${(next.saved_bytes/1e9).toFixed(2)}/${(next.archive_bytes/1e9).toFixed(2)} GB${next.speed_bps?` · ${(next.speed_bps/1e6).toFixed(2)} MB/s`:''}. ETA includes verification and extraction when measured preparation rates are available.`:'';
  this.tick();
 }
 disconnect(){this.failed=true;this.tick();}
 tick(){
  if(!this.data)return;
  const data=this.data,next=data.next,age=(performance.now()-this.received)/1000;
  let label;
  if(this.failed)label='offline';
  else if(data.stale||age>65)label='stale';
  else if(data.ready_scenes===data.total_scenes&&data.total_scenes>0)label='all ready';
  else if(!data.running)label='paused';
  else if(!next)label='starting…';
  else if(next.eta_seconds!==null&&Number.isFinite(next.eta_seconds)){
   const seconds=next.eta_seconds-age;
   label=seconds>0?`~${countdown(seconds)}`:next.phase==='download'?'updating ETA…':'preparing…';
  }else if(next.download_eta_seconds!==null&&Number.isFinite(next.download_eta_seconds)){
   const seconds=next.download_eta_seconds-age;
   label=seconds>0?`~${countdown(seconds)} + prep`:'updating ETA…';
  }else label=next.phase!=='download'?'preparing…':next.speed_bps===0?'no transfer':'estimating…';
  document.getElementById('next-trip-eta').textContent=label;
 }
}
