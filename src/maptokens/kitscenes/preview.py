"""Prepare a camera/map preview while a complete tar download is in progress."""
from __future__ import annotations

import json
import os
import tarfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .download import atomic_json, safe_relative, selected
from .transport import RemoteTar, resolve_url


def prepare_preview(state, sid, tail_mib=32):
    state=Path(state)
    plan=json.loads((state/'plan.json').read_text())
    row=next((r for r in plan['scenes'] if r['sequence_id']==sid),None)
    if row is None:raise ValueError('Scene is not in this plan')
    archive=Path(plan['roots'][row['disk']])/row['archive_path']
    prefix=archive if archive.exists() else archive.with_name(archive.name+'.part')
    if not prefix.exists():raise ValueError('The full archive download has not started')
    out=archive.with_suffix('');out.mkdir(parents=True,exist_ok=True)
    if (out/'.complete.json').exists():return dict(scene=sid,complete=True)
    cache=state/'previews'/sid;cache.mkdir(parents=True,exist_ok=True)
    start=max(0,(row['size']-tail_mib*1024*1024)//512*512)
    tail=cache/'tail.bin';tail_meta=cache/'tail.json'
    expected=dict(start=start,size=row['size']-start,revision=plan['revision'],archive_sha256=row['archive_sha256'])
    if not tail.exists() or not tail_meta.exists() or json.loads(tail_meta.read_text())!=expected or tail.stat().st_size!=expected['size']:
        remote=RemoteTar(resolve_url(plan['revision'],row['archive_path']),row['size'])
        try:data=remote.get(start,row['size']-start)
        finally:remote.close()
        tmp=tail.with_suffix('.tmp');tmp.write_bytes(data);os.replace(tmp,tail);atomic_json(tail_meta,expected)
    data=tail.read_bytes();inventory=set();saved=set()

    def wanted(path):return selected(path,plan['cameras'],plan['metadata'],plan['include'],plan['exclude'])
    def save(path,body):
        dst=out/path;dst.parent.mkdir(parents=True,exist_ok=True)
        tmp=dst.with_name(dst.name+'.preview-part');tmp.write_bytes(body);os.replace(tmp,dst);saved.add(path)

    # Snapshot the prefix boundary: the downloader can keep appending safely.
    boundary=prefix.stat().st_size
    with prefix.open('rb') as source:
        try:
            with tarfile.open(fileobj=source,mode='r:') as tf:
                for member in tf:
                    if member.offset_data+member.size>boundary:break
                    path=safe_relative(member.name,sid)
                    if member.isfile():
                        inventory.add(path)
                        if wanted(path):
                            with tf.extractfile(member) as f:save(path,f.read())
        except (EOFError,tarfile.ReadError):pass
    # Locate real, checksum-valid tar headers at 512-byte boundaries. We only
    # accept short regular member names inside this scene with complete bodies.
    for offset in range(0,len(data)-511,512):
        if data[offset+257:offset+262]!=b'ustar':continue
        try:member=tarfile.TarInfo.frombuf(data[offset:offset+512],'utf-8','surrogateescape')
        except tarfile.TarError:continue
        if not member.name.startswith(sid+'/') or not member.isfile():continue
        if member.size<0 or offset+512+member.size>len(data):continue
        path=safe_relative(member.name,sid);inventory.add(path)
        if wanted(path):save(path,data[offset+512:offset+512+member.size])
    for required in ('maps/map.osm','maps/origin.json','calibration/calib.json','poses.txt','timestamp.reference.txt'):
        if not (out/required).is_file():raise ValueError(f'Preview data not available yet: {required}')
    ET.parse(out/'maps/map.osm')
    json.loads((out/'maps/origin.json').read_text())
    images=[p for camera in plan['cameras'] for p in (out/camera).glob('*') if p.suffix in ('.jpg','.jpeg','.png')]
    if not images:raise ValueError('Camera frames have not arrived yet')
    receipt=dict(scene=sid,split=row['split'],revision=plan['revision'],mode='preview',
                 archive_bytes=row['size'],source_archive_sha256_expected=row['archive_sha256'],
                 integrity='Preview from downloaded prefix and authenticated HTTP tail range; full archive SHA256 verification pending',
                 inventory=sorted(inventory),inventory_complete=False,files=len(saved),front_images=len(images),prepared=time.time())
    # A preview never creates .complete.json. Full-archive verification and
    # regular extraction will replace it in the browser when downloading ends.
    atomic_json(out/'.preview.json',receipt)
    return dict(scene=sid,images=len(images),map_bytes=(out/'maps/map.osm').stat().st_size,archive_verified=False)
