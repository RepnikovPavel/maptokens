"""Full-archive mirror with native transport resume and balanced disk placement."""
from __future__ import annotations

import concurrent.futures
from contextlib import ExitStack
import fcntl
import json
import os
import shutil
import subprocess
import tarfile
import threading
import time
from pathlib import Path

from .download import atomic_json, safe_relative, selected, _hash_file
from .transport import REPO, session, token, resolve_url


class Mirror:
    def __init__(self,state,method='wget',jobs=8):
        self.state=Path(state)
        self.plan=json.loads((self.state/'plan.json').read_text())
        self.method,self.jobs=method,jobs
        self.lock=threading.Lock()
        self.stop=threading.Event()
        self.active,self.errors={},{}
        self.network_bytes=0
        self.done,self.skipped=0,0
        self.started=time.time()
        self.completed_bytes=0
        self.total_bytes=sum(r['size'] for r in self.plan['scenes'])

    def archive(self,row):
        return Path(self.plan['roots'][row['disk']])/row['archive_path']

    def verified(self,row):
        path=self.state/'verified'/(row['sequence_id']+'.json')
        archive=self.archive(row)
        if not path.exists() or not archive.exists():return False
        receipt=json.loads(path.read_text());st=archive.stat()
        return receipt.get('sha256')==row['archive_sha256'] and receipt.get('size')==st.st_size==row['size'] and receipt.get('mtime_ns')==st.st_mtime_ns

    def log(self,event,**kw):
        row=dict(time=time.time(),event=event,**kw)
        with self.lock:
            with (self.state/'download.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
        print(json.dumps(row),flush=True)

    def report(self,final=False):
        with self.lock:
            now=time.time();duration=max(1,now-self.started)
            active=dict((k,dict(v)) for k,v in self.active.items())
            pending_partial=sum(min(v.get('saved_bytes',0),v['archive_bytes']) for v in active.values() if v['phase']=='download')
            speed=self.network_bytes/duration
            remaining=max(0,self.total_bytes-self.completed_bytes-pending_partial)
            result=dict(running=not final,mode='full',method=self.method,pid=os.getpid(),updated=now,started=self.started,
                        network_bytes=self.network_bytes,speed_mbps=getattr(self,'recent_speed',0)/1e6,
                        average_mbps=speed/1e6,archive_bytes_done=self.completed_bytes,archive_bytes_total=self.total_bytes,
                        eta_seconds=remaining/speed if speed>0 else None,scenes_done=self.done,scenes_skipped=self.skipped,
                        scenes_total=len(self.plan['scenes']),active=active,errors=dict(self.errors))
        result['disks']=[]
        for i,root in enumerate(self.plan['roots']):
            usage=shutil.disk_usage(root)
            result['disks'].append(dict(root=root,total_bytes=usage.total,free_bytes=usage.free,
                                       planned_bytes=self.plan['archive_bytes_per_disk'][i]))
        atomic_json(self.state/'status.json',result)
        self.log('finished' if final else 'progress',**result)

    def reporter(self):
        previous=0;tprev=self.started
        while not self.stop.wait(5):
            now=time.time()
            self.recent_speed=(self.network_bytes-previous)/max(.01,now-tprev)
            previous,tprev=self.network_bytes,now
            self.report()

    def run(self):
        with (self.state/'run.lock').open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            pending=[]
            for row in self.plan['scenes']:
                if self.verified(row) and self.archive(row).with_suffix('').joinpath('.complete.json').exists():
                    self.skipped+=1;self.completed_bytes+=row['size']
                else:pending.append(row)
            # Make one mapped validation scene available early, then stream large
            # archives first for stable sustained throughput on both HDDs.
            pending.sort(key=lambda r:-r['size'])
            preview=min((r for r in pending if r['split']=='val'),key=lambda r:r['size'],default=None)
            if preview:pending.remove(preview);pending.insert(0,preview)
            self.log('start',method=self.method,jobs=self.jobs,archives=len(pending),bytes=self.total_bytes)
            t=threading.Thread(target=self.reporter,daemon=True);t.start()
            try:
                with ExitStack() as stack:
                    disks=len(self.plan['roots'])
                    if self.jobs<disks:
                        pool=stack.enter_context(concurrent.futures.ThreadPoolExecutor(max_workers=self.jobs))
                        pools=[pool]*disks
                    else:
                        pools=[stack.enter_context(concurrent.futures.ThreadPoolExecutor(max_workers=self.jobs//disks+(i<self.jobs%disks))) for i in range(disks)]
                    futures={pools[r['disk']].submit(self.one,r):r for r in pending}
                    for future in concurrent.futures.as_completed(futures):
                        row=futures[future];sid=row['sequence_id']
                        try:
                            future.result()
                            with self.lock:self.done+=1;self.completed_bytes+=row['size']
                        except Exception as e:
                            with self.lock:self.errors[sid]=type(e).__name__
                            self.log('archive_error',scene=sid,error_type=type(e).__name__)
                        finally:
                            with self.lock:self.active.pop(sid,None)
            finally:
                self.stop.set();t.join();self.report(final=True)
            if self.errors:raise RuntimeError(f'{len(self.errors)} archive transfers failed; rerun to resume')

    def one(self,row):
        dst=self.archive(row);dst.parent.mkdir(parents=True,exist_ok=True)
        sid=row['sequence_id']
        if shutil.disk_usage(dst.parent).free<row['size']+5*10**9:raise IOError('Insufficient disk space')
        with self.lock:self.active[sid]=dict(disk=row['disk'],phase='download',archive_bytes=row['size'],saved_bytes=0)
        if not self.verified(row):
            if not dst.exists():self.fetch(row,dst)
            with self.lock:self.active[sid]['phase']='verify'
            if dst.stat().st_size!=row['size'] or _hash_file(dst)!=row['archive_sha256']:
                # Preserve a bad artifact for inspection; allow a fresh retry.
                dst.rename(dst.with_name(dst.name+f'.bad-{int(time.time())}'))
                raise IOError('Archive SHA256 mismatch')
            atomic_json(self.state/'verified'/(sid+'.json'),dict(size=row['size'],sha256=row['archive_sha256'],
                        mtime_ns=dst.stat().st_mtime_ns,revision=self.plan['revision'],verified=time.time()))
        with self.lock:self.active[sid]['phase']='extract front + metadata'
        self.extract(row,dst)
        self.log('archive_complete',scene=sid,disk=row['disk'],bytes=row['size'],sha256=row['archive_sha256'])

    def fetch(self,row,dst):
        sid=row['sequence_id'];finished=threading.Event()
        part=dst.with_name(dst.name+'.part')
        cache=dst.parents[2]/'.cache/huggingface/download'/Path(row['archive_path']).parent
        def size_now():
            if dst.exists():return dst.stat().st_size
            if self.method=='hf':
                return sum(p.stat().st_size for p in cache.glob(dst.name+'.*.incomplete')) if cache.exists() else 0
            return part.stat().st_size if part.exists() else 0
        previous=size_now()
        def observe():
            nonlocal previous
            while not finished.wait(1):
                n=size_now()
                with self.lock:
                    self.network_bytes+=max(0,n-previous);self.active[sid]['saved_bytes']=n
                previous=n
        monitor=threading.Thread(target=observe,daemon=True);monitor.start()
        try:
            for attempt in range(5):
                try:
                    if self.method=='hf':
                        from huggingface_hub import hf_hub_download
                        hf_hub_download(REPO,row['archive_path'],repo_type='dataset',revision=self.plan['revision'],
                                        local_dir=Path(self.plan['roots'][row['disk']]),token=token())
                    else:
                        with session() as s:
                            with s.get(resolve_url(self.plan['revision'],row['archive_path']),stream=True,timeout=(20,90)) as r:
                                r.raise_for_status();url=r.url
                        # Tokens/signed URLs go to child stdin only, not argv/logs.
                        if self.method=='wget':
                            command=['wget','--quiet','--continue','--timeout=90','--tries=3','-O',str(part),'-i','-']
                            stdin=(url+'\n').encode()
                        elif self.method=='curl':
                            command=['curl','--config','-','--silent','--fail','--location','--connect-timeout','20','--speed-limit','1024','--speed-time','120','--retry','3','--continue-at','-']
                            stdin=f'url = "{url}"\noutput = "{part}"\n'.encode()
                        else:raise ValueError('Full mode supports hf, wget or curl')
                        p=subprocess.run(command,input=stdin,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=24*3600)
                        if p.returncode:raise IOError('Full archive transport failed')
                        if part.stat().st_size!=row['size']:raise IOError('Truncated archive')
                        os.replace(part,dst)
                    break
                except Exception as e:
                    # HF network exceptions can contain signed URLs: log only type.
                    self.log('archive_retry',scene=sid,attempt=attempt+1,error_type=type(e).__name__)
                    if attempt==4:raise IOError('Archive download failed after retries') from None
                    time.sleep(min(30,2**attempt))
        finally:
            finished.set();monitor.join()
            n=size_now()
            with self.lock:
                self.network_bytes+=max(0,n-previous);self.active[sid]['saved_bytes']=n

    def extract(self,row,archive):
        out=archive.with_suffix('');marker=out/'.complete.json'
        if marker.exists():return
        out.mkdir(parents=True,exist_ok=True);inventory=[];count=retained=0
        with tarfile.open(archive,mode='r:') as tf:
            for m in tf:
                path=safe_relative(m.name,row['sequence_id'])
                if m.issym() or m.islnk() or m.issparse():raise ValueError('Unsafe tar member')
                if not m.isfile():continue
                inventory.append(path)
                if selected(path,self.plan['cameras'],self.plan['metadata'],self.plan['include'],self.plan['exclude']):
                    dst=out/path;dst.parent.mkdir(parents=True,exist_ok=True);part=dst.with_name(dst.name+'.part')
                    with tf.extractfile(m) as src,part.open('wb') as f:shutil.copyfileobj(src,f)
                    os.replace(part,dst);count+=1;retained+=m.size
        atomic_json(marker,dict(scene=row['sequence_id'],split=row['split'],mode='full',revision=self.plan['revision'],
                    archive_sha256=row['archive_sha256'],archive_bytes=row['size'],retained_bytes=retained,
                    files=count,inventory=inventory,finished=time.time()))
