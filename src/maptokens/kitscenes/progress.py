"""Progress for retained archives and the next viewable scene."""
from __future__ import annotations

import json
import statistics
import threading
import time
from collections import deque
from pathlib import Path


def percent(value, total):
    return min(100., max(0., 100.*value/total)) if total else 0.


class DownloadProgress:
    def __init__(self, state, plan):
        self.state=Path(state)
        self.plan=plan
        self.rows={r['sequence_id']:r for r in plan['scenes']}
        self.samples=deque(maxlen=64)
        self.started=None
        self.profiled=set()
        self.verify_rates=[]
        self.extract_rates=[]
        self.lock=threading.Lock()

    def record(self, status):
        if status.get('started')!=self.started:
            self.samples.clear()
            self.started=status.get('started')
        at=status.get('updated',0)
        if at and (not self.samples or at>self.samples[-1]['updated']):
            self.samples.append(dict(updated=at,active=status.get('active',{})))

    def seed(self, status):
        self.started=status.get('started')
        try:
            with (self.state/'download.jsonl').open('rb') as f:
                f.seek(0,2)
                f.seek(max(0,f.tell()-512*1024))
                lines=f.read().splitlines()
            for line in lines:
                try:sample=json.loads(line)
                except (ValueError,UnicodeError):continue
                if sample.get('event')=='progress' and sample.get('started')==self.started:
                    self.record(sample)
        except OSError:pass

    def rate(self, sid, phase, now):
        history=[]
        for snapshot in self.samples:
            entry=snapshot['active'].get(sid)
            if not entry or entry.get('phase')!=phase:continue
            at=snapshot['updated']
            if now-at>125:continue
            saved=entry.get('saved_bytes',0)
            if history and saved<history[-1][1]:history=[]
            history.append((at,saved))
        if len(history)<2:return None
        changed=[b[0] for a,b in zip(history,history[1:]) if b[1]>a[1]]
        if not changed or now-changed[-1]>30:return 0.
        dt=history[-1][0]-history[0][0]
        return max(0.,(history[-1][1]-history[0][1])/dt) if dt>0 else None

    def phase_age(self, sid, phase, now):
        started=now
        for snapshot in reversed(self.samples):
            entry=snapshot['active'].get(sid)
            if not entry or entry.get('phase')!=phase:break
            started=snapshot['updated']
        return max(0.,now-started)

    def profile(self, sid, archive, receipt):
        if sid in self.profiled:return
        try:
            verified=json.loads((self.state/'verified'/(sid+'.json')).read_text())
            ready=json.loads(receipt.read_text())
            stat=archive.stat()
            verify=verified['verified']-stat.st_mtime
            extract=ready['finished']-verified['verified']
            if .1<=verify<=3600:self.verify_rates.append(stat.st_size/verify)
            if .1<=extract<=3600:self.extract_rates.append(stat.st_size/extract)
            self.profiled.add(sid)
        except (OSError,ValueError,KeyError):pass

    def snapshot(self, status, now=None):
        with self.lock:
            now=time.time() if now is None else now
            if not self.samples or status.get('started')!=self.started:self.seed(status)
            self.record(status)
            downloaded=ready=0
            available=set()
            for sid,row in self.rows.items():
                archive=Path(self.plan['roots'][row['disk']])/row['archive_path']
                folder=archive.with_suffix('')
                complete=folder/'.complete.json'
                has_map=(folder/'maps/map.osm').is_file()
                if has_map and (complete.exists() or (folder/'.preview.json').exists()):available.add(sid)
                if complete.exists() and (has_map or not self.plan.get('hd_only')):
                    ready+=1
                    self.profile(sid,archive,complete)
                for path in (archive,archive.with_name(archive.name+'.part')):
                    try:downloaded+=min(row['size'],path.stat().st_size);break
                    except FileNotFoundError:pass
            total=sum(r['size'] for r in self.rows.values())
            fresh=now-status.get('updated',0)<=60
            running=bool(status.get('running')) and fresh
            stale=bool(status.get('running')) and not fresh
            candidates=[]
            for sid,entry in status.get('active',{}).items():
                if sid not in self.rows or sid in available:continue
                phase=entry.get('phase','download')
                size=self.rows[sid]['size']
                saved=min(size,max(0,entry.get('saved_bytes',0))) if phase=='download' else size
                rate=self.rate(sid,'download',now) if running and phase=='download' else None
                age=self.phase_age(sid,phase,now)
                verify=size/statistics.median(self.verify_rates) if self.verify_rates else None
                extract=size/statistics.median(self.extract_rates) if self.extract_rates else None
                eta=download_eta=processing=None
                if verify is not None and extract is not None:
                    processing=verify+extract
                    if phase=='verify':eta=max(0.,verify-age)+extract
                    elif phase.startswith('extract'):eta=max(0.,extract-age)
                if phase=='download' and rate and rate>0:
                    download_eta=max(0.,size-saved)/rate
                    if processing is not None:eta=download_eta+processing
                if not running:eta=download_eta=None
                candidates.append(dict(id=sid,split=self.rows[sid]['split'],phase=phase,
                                       archive_bytes=size,saved_bytes=saved,percent=percent(saved,size),
                                       speed_bps=rate,eta_seconds=eta,download_eta_seconds=download_eta,
                                       processing_seconds=processing))
            candidates.sort(key=lambda c:(c['eta_seconds'] is None,c['eta_seconds'] if c['eta_seconds'] is not None else 0,
                                          c['phase']=='download',c['archive_bytes']-c['saved_bytes'],c['id']))
            return dict(downloaded_bytes=downloaded,total_bytes=total,download_percent=percent(downloaded,total),
                        ready_scenes=ready,total_scenes=len(self.rows),ready_percent=percent(ready,len(self.rows)),
                        available_scenes=len(available),running=running,stale=stale,
                        next=candidates[0] if candidates else None)
