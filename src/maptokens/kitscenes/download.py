"""Selective tar downloads, reproducible plans, durable receipts and telemetry."""
from __future__ import annotations

import concurrent.futures
import csv
import fnmatch
import hashlib
import io
import json
import os
import shutil
import tarfile
import threading
import time
from pathlib import Path, PurePosixPath

from .transport import REPO, RemoteTar, resolve_url, session


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


def safe_relative(name, scene):
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] != scene:
        raise ValueError("Unsafe archive member")
    return PurePosixPath(*p.parts[1:]).as_posix()


def selected(path, cameras, metadata=True, include=(), exclude=()):
    if any(fnmatch.fnmatchcase(path, p) for p in exclude):
        return False
    parts = PurePosixPath(path).parts
    # Raw sensor directories can also occur under async/. Metadata and labels
    # outside these directories are retained, including timestamps for all sensors.
    sensors = [p for p in parts[:-1] if p.startswith(("camera_", "lidar_", "radar_"))]
    if sensors:
        return any(s in cameras for s in sensors) or any(fnmatch.fnmatchcase(path, p) for p in include)
    return metadata or any(fnmatch.fnmatchcase(path, p) for p in include)


class TarFileView(io.RawIOBase):
    def __init__(self, remote):
        self.remote, self.pos = remote, 0

    def tell(self):
        return self.pos

    def seek(self, pos, whence=0):
        self.pos = pos if whence == 0 else self.pos + pos if whence == 1 else self.remote.size + pos
        return self.pos

    def read(self, n=-1):
        n = min(n if n >= 0 else self.remote.size-self.pos, self.remote.size-self.pos)
        data = self.remote.read_at(self.pos, n)
        self.pos += len(data)
        return data


def build_plan(args):
    state = Path(args.state)
    state.mkdir(parents=True, exist_ok=True)
    if (state / "plan.json").exists():
        raise ValueError("Plan exists. Reuse it with run, or choose another state directory.")
    roots = [str(Path(x).resolve()) for x in args.roots]
    if len(set(roots)) != len(roots):
        raise ValueError("Disk roots must be distinct")
    for root in roots:
        Path(root).mkdir(parents=True, exist_ok=True)
    with session() as s:
        r = s.get(f"https://huggingface.co/api/datasets/{REPO}/revision/{args.revision}", timeout=60)
        r.raise_for_status()
        revision = r.json()["sha"]
        r = s.get(resolve_url(revision, "data/sequence_archives.csv"), timeout=90)
        r.raise_for_status()
        manifest = r.text
    (state / "sequence_archives.csv").write_text(manifest)
    rows = list(csv.DictReader(io.StringIO(manifest)))
    rows = [r for r in rows if (not args.splits or r['split'] in args.splits) and (not args.scenes or r['sequence_id'] in args.scenes)]
    if args.limit:
        rows = sorted(rows, key=lambda r: int(r['archive_size_bytes']))[:args.limit]
    if not rows:
        raise ValueError("No matching scenes")
    # LPT estimates selected size from archive size. Downloaded retained bytes
    # are measured separately; plans remain stable across restarts.
    loads = [0] * len(roots)
    for row in sorted(rows, key=lambda r: -int(r['archive_size_bytes'])):
        row['disk'] = min(range(len(roots)), key=lambda i: loads[i])
        row['size'] = int(row['archive_size_bytes'])
        loads[row['disk']] += row['size']
    plan = dict(version=1, repo=REPO, revision=revision, roots=roots, cameras=args.cameras,
                metadata=not args.no_metadata, include=args.include, exclude=args.exclude,
                mode=args.mode, scenes=rows, archive_bytes_per_disk=loads, created=time.time())
    atomic_json(state / "plan.json", plan)
    print(json.dumps(dict(scenes=len(rows), archive_gb=sum(loads)/1e9, cameras=args.cameras, roots=roots, mode=args.mode, revision=revision), indent=2))


class Runner:
    def __init__(self, state, method, jobs):
        self.state, self.method, self.jobs = Path(state), method, jobs
        self.plan = json.loads((self.state / 'plan.json').read_text())
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.started = time.time()
        self.bytes = 0
        self.active, self.errors = {}, {}
        self.done = self.skipped = 0
        self.logpath = self.state / 'download.jsonl'
        self.allocations = json.loads((self.state/'allocations.json').read_text()) if (self.state/'allocations.json').exists() else {}
        for row in self.plan['scenes']:
            if row['sequence_id'] in self.allocations:
                row['disk'] = self.allocations[row['sequence_id']]['disk']
            else:
                completed = self.output(row)/'.complete.json'
                if completed.exists():
                    receipt = json.loads(completed.read_text())
                    self.allocations[row['sequence_id']] = dict(disk=row['disk'],bytes=receipt.get('retained_bytes',row['size']))

    def log(self, event, **kw):
        with self.lock:
            row = dict(time=time.time(), event=event, **kw)
            with self.logpath.open('a') as f:
                f.write(json.dumps(row) + '\n')
            print(json.dumps(row), flush=True)

    def add_bytes(self, n):
        with self.lock:
            self.bytes += n

    def output(self, row):
        return Path(self.plan['roots'][row['disk']]) / 'data' / row['split'] / row['sequence_id']

    def progress(self):
        prev, tprev = 0, self.started
        while not self.stop.wait(5):
            now = time.time()
            with self.lock:
                speed = (self.bytes-prev)/(now-tprev)
                status = dict(running=True, pid=os.getpid(), updated=now, started=self.started,
                              network_bytes=self.bytes, speed_mbps=speed/1e6,
                              average_mbps=self.bytes/max(1,now-self.started)/1e6,
                              scenes_done=self.done, scenes_skipped=self.skipped,
                              scenes_total=len(self.plan['scenes']), active=dict(self.active), errors=dict(self.errors))
                prev, tprev = self.bytes, now
            status['disks'] = []
            for root in self.plan['roots']:
                usage = shutil.disk_usage(root)
                status['disks'].append(dict(root=root, free_bytes=usage.free, total_bytes=usage.total))
            atomic_json(self.state / 'status.json', status)
            self.log('progress', **status)

    def run(self):
        # Prevent two containers/processes from writing the same plan.
        import fcntl
        with (self.state / 'run.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._run()

    def _run(self):
        pending = []
        for row in self.plan['scenes']:
            if (self.output(row)/'.complete.json').exists():
                self.skipped += 1
            else:
                pending.append(row)
        reporter = threading.Thread(target=self.progress, daemon=True)
        reporter.start()
        # Small scenes yield reviewable data early, spread over both disks.
        pending.sort(key=lambda r: r['size'])
        self.log('start', scenes=len(pending), jobs=self.jobs, method=self.method, mode=self.plan['mode'])
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.jobs) as pool:
                futures = {pool.submit(self.one, r): r for r in pending}
                for fut in concurrent.futures.as_completed(futures):
                    row = futures[fut]
                    sid = row['sequence_id']
                    try:
                        fut.result()
                        self.done += 1
                    except Exception as e:
                        # Exceptions from transports may contain signed URLs.
                        self.errors[sid] = type(e).__name__
                        self.log('scene_error', scene=sid, error_type=type(e).__name__)
                    finally:
                        with self.lock:
                            self.active.pop(sid, None)
        finally:
            self.stop.set()
            reporter.join()
        atomic_json(self.state/'status.json', dict(running=False, updated=time.time(), scenes_done=self.done,
                    scenes_skipped=self.skipped, scenes_total=len(self.plan['scenes']), errors=self.errors,
                    network_bytes=self.bytes, average_mbps=self.bytes/max(1,time.time()-self.started)/1e6))
        self.log('finished', scenes_done=self.done, errors=len(self.errors))
        if self.errors:
            raise RuntimeError(f"{len(self.errors)} scenes failed; rerun resumes verified files")

    def one(self, row):
        sid = row['sequence_id']
        out = self.output(row)
        if (out/'.complete.json').exists():
            return
        out.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(out).free < row['size'] + 5*10**9:
            raise IOError('Insufficient free disk space')
        remote = RemoteTar(resolve_url(self.plan['revision'], row['archive_path']), row['size'], self.method, self.add_bytes)
        with self.lock:
            self.active[sid] = dict(disk=row['disk'], phase='index', members=0, saved_bytes=0)
        try:
            if self.plan['mode'] == 'full':
                self.full(row, out, remote)
                return
            idx = self.state / 'indexes' / (sid+'.json')
            if idx.exists():
                members = json.loads(idx.read_text())
            else:
                checkpoint = idx.with_suffix('.checkpoint.json')
                saved = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
                members = saved.get('members', [])
                view = TarFileView(remote)
                view.seek(saved.get('next_offset', 0))
                with tarfile.open(fileobj=view, mode='r:', pax_headers=saved.get('pax_headers', {})) as tf:
                    for m in tf:
                        path = safe_relative(m.name, sid)
                        if m.issym() or m.islnk() or m.issparse():
                            raise ValueError('Links and sparse tar members are unsupported')
                        if m.isfile():
                            members.append(dict(path=path, offset=m.offset_data, size=m.size))
                        with self.lock:
                            self.active[sid].update(members=len(members), archive_position=tf.offset)
                        if len(members) % 64 == 0:
                            atomic_json(checkpoint,dict(members=members,next_offset=tf.offset,pax_headers=tf.pax_headers))
                atomic_json(idx, members)
                checkpoint.unlink(missing_ok=True)
            chosen = [m for m in members if selected(m['path'], self.plan['cameras'], self.plan['metadata'], self.plan['include'], self.plan['exclude'])]
            with self.lock:
                # Assign using exact retained bytes once the index is available.
                # This keeps both HDDs balanced even when modality ratios differ.
                if sid not in self.allocations:
                    loads = [sum(a['bytes'] for a in self.allocations.values() if a['disk']==i) for i in range(len(self.plan['roots']))]
                    disk = min(range(len(loads)),key=lambda i:loads[i])
                    self.allocations[sid] = dict(disk=disk,bytes=sum(m['size'] for m in chosen))
                    atomic_json(self.state/'allocations.json',self.allocations)
                    row['disk'] = disk
                    out = self.output(row)
                    out.mkdir(parents=True,exist_ok=True)
                self.active[sid].update(disk=row['disk'],phase='download', members=len(members), selected=len(chosen), selected_bytes=sum(m['size'] for m in chosen))
            receipt = out / '.files.json'
            records = json.loads(receipt.read_text()) if receipt.exists() else {}
            for m in chosen:
                dst = out / m['path']
                old = records.get(m['path'])
                if old and dst.exists() and dst.stat().st_size == m['size'] and hashlib.sha256(dst.read_bytes()).hexdigest() == old['sha256']:
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                part = dst.with_name(dst.name+'.part')
                offset = part.stat().st_size if part.exists() else 0
                if offset > m['size']:
                    part.unlink()
                    offset = 0
                with part.open('ab') as f:
                    while offset < m['size']:
                        n = min(16*1024*1024, m['size']-offset)
                        f.write(remote.get(m['offset']+offset, n))
                        offset += n
                os.replace(part, dst)
                records[m['path']] = dict(size=m['size'], sha256=hashlib.sha256(dst.read_bytes()).hexdigest())
                atomic_json(receipt, records)
                with self.lock:
                    self.active[sid]['saved_bytes'] += m['size']
            atomic_json(out/'.complete.json', dict(scene=sid, split=row['split'], revision=self.plan['revision'],
                        mode='selective', cameras=self.plan['cameras'], files=len(records),
                        retained_bytes=sum(v['size'] for v in records.values()), archive_bytes=row['size'],
                        source_archive_sha256=row['archive_sha256'],
                        integrity='HTTP TLS + tar header checksums + local per-file SHA256; full archive SHA256 cannot be checked with ranges',
                        inventory=sorted(m['path'] for m in members), finished=time.time()))
            self.log('scene_complete', scene=sid, disk=row['disk'], files=len(records), bytes=sum(v['size'] for v in records.values()))
        finally:
            remote.close()

    def full(self, row, out, remote):
        # Explicit full mode retains the original archive for future modalities.
        archive = out.parent / (row['sequence_id']+'.tar')
        part = archive.with_suffix('.tar.part')
        if not archive.exists():
            offset = part.stat().st_size if part.exists() else 0
            with part.open('ab') as f:
                while offset < row['size']:
                    b = remote.get(offset, min(32*1024*1024,row['size']-offset))
                    f.write(b)
                    offset += len(b)
                    with self.lock:
                        self.active[row['sequence_id']].update(phase='full archive', saved_bytes=offset)
            h = hashlib.file_digest(part.open('rb'), 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else _hash_file(part)
            if h != row['archive_sha256']:
                part.unlink()
                raise IOError('Archive checksum mismatch')
            os.replace(part, archive)
        if archive.stat().st_size != row['size'] or _hash_file(archive) != row['archive_sha256']:
            raise IOError('Existing archive checksum mismatch')
        with tarfile.open(archive) as tf:
            for m in tf:
                path = safe_relative(m.name, row['sequence_id'])
                if m.isfile() and selected(path, self.plan['cameras'], self.plan['metadata'], self.plan['include'], self.plan['exclude']):
                    dst = out/path
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    with tf.extractfile(m) as src, dst.open('wb') as f:
                        shutil.copyfileobj(src, f)
        atomic_json(out/'.complete.json', dict(scene=row['sequence_id'], mode='full', archive_sha256=row['archive_sha256'], finished=time.time()))
        self.log('scene_complete', scene=row['sequence_id'], disk=row['disk'], full_archive=True)


def _hash_file(path):
    h = hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda: f.read(8*1024*1024), b''):
            h.update(b)
    return h.hexdigest()


def benchmark(args):
    plan = json.loads((Path(args.state)/'plan.json').read_text())
    row = min(plan['scenes'], key=lambda r: r['size'])
    r = RemoteTar(resolve_url(plan['revision'],row['archive_path']),row['size'])
    results = []
    try:
        # Find a selected camera member without downloading excluded payloads.
        with tarfile.open(fileobj=TarFileView(r),mode='r:') as tf:
            m = next(m for m in tf if m.isfile() and selected(safe_relative(m.name,row['sequence_id']),plan['cameras'],False) and m.size > 100000)
        for method in ('python','curl','wget'):
            durations = []
            try:
                for _ in range(3):
                    t = time.perf_counter()
                    body = r.get(m.offset_data, m.size, method)
                    durations.append(time.perf_counter()-t)
                results.append(dict(method=method, bytes=len(body), seconds=durations, median_mbps=m.size/sorted(durations)[1]/1e6))
            except IOError:
                results.append(dict(method=method, error='range transfer failed', median_mbps=0))
    finally:
        r.close()
    winner = max(results,key=lambda x:x['median_mbps'])['method']
    report = dict(results=results, winner=winner, measured=time.time(),
                  hf='huggingface_hub only fetches whole files; no tar-member range API, so it cannot preserve selective network transfer')
    atomic_json(Path(args.state)/'benchmark.json',report)
    print(json.dumps(report,indent=2))
