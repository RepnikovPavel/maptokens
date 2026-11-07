"""Read-only KITScenes browser API. No dataset credentials reach the service."""
from __future__ import annotations

import io
import json
import math
from decimal import Decimal
import time
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pyproj import Transformer

WEB = Path(__file__).parent/'web'


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def rotation(q):
    x,y,z,w = q
    n = x*x+y*y+z*z+w*w
    if n < 1e-12:
        return np.eye(3)
    s = 2/n
    return np.array([[1-s*(y*y+z*z),s*(x*y-z*w),s*(x*z+y*w)],
                     [s*(x*y+z*w),1-s*(x*x+z*z),s*(y*z-x*w)],
                     [s*(x*z-y*w),s*(y*z+x*w),1-s*(x*x+y*y)]])


def poses(path):
    if not path.exists():
        return np.empty((0,8))
    rows = []
    for line in path.read_text().splitlines():
        if line.strip() and not line.startswith('#'):
            r = [float(s) for s in line.split()]
            if len(r) == 8:
                rows.append(r)
    return np.array(rows, dtype=float).reshape(-1,8)


def map_data(path, origin):
    file = path/'maps/map.osm'
    if not file.exists():
        return [], [], []
    root = ET.parse(file).getroot()
    # Map nodes are WGS84. Use the published origin with local poses,
    # then shift in float64 before sending float32 geometry to the GPU.
    transform = Transformer.from_crs('EPSG:4326','EPSG:32632',always_xy=True)
    map_origin=read_json(path/'maps/origin.json',{})
    map_offset=np.zeros(3)
    if map_origin and abs(origin[0])<100000 and abs(origin[1])<100000:
        map_offset[:2]=transform.transform(map_origin['longitude'],map_origin['latitude'])
    nodes = {}
    for n in root.findall('node'):
        tags = {t.attrib['k']:t.attrib['v'] for t in n.findall('tag')}
        x,y = transform.transform(float(n.attrib['lon']),float(n.attrib['lat']))
        z = float(tags.get('ele',origin[2]))
        if not math.isfinite(z) or abs(z)>1e10:
            z = origin[2]
        nodes[n.attrib['id']] = [x-map_offset[0]-origin[0],y-map_offset[1]-origin[1],z-origin[2]]
    groups, segments, features = {}, [], []
    for way in root.findall('way'):
        tags = {t.attrib['k']:t.attrib['v'] for t in way.findall('tag')}
        pts = [nodes[n.attrib['ref']] for n in way.findall('nd') if n.attrib['ref'] in nodes]
        label = tags.get('type', 'unclassified')
        subtype = tags.get('subtype', '')
        key = label + (':'+subtype if subtype else '')
        if key not in groups:
            groups[key] = len(groups)
        group = groups[key]
        for a,b in zip(pts,pts[1:]):
            segments.append(a+b+[group,len(features)])
        features.append(dict(id=way.attrib['id'], kind=key, tags=tags, nodes=len(pts)))
    for rel in root.findall('relation'):
        features.append(dict(id=rel.attrib['id'], kind='relation', tags={t.attrib['k']:t.attrib['v'] for t in rel.findall('tag')}, members=[m.attrib for m in rel.findall('member')]))
    return segments, list(groups), features


class Store:
    def __init__(self, state):
        self.state = Path(state)
        self.plan = read_json(self.state/'plan.json')
        self.rows = {r['sequence_id']:r for r in self.plan['scenes']}

    def path(self, sid):
        if sid not in self.rows:
            raise HTTPException(404,'Unknown scene')
        row = self.rows[sid]
        allocations = read_json(self.state/'allocations.json',{})
        disk = allocations.get(sid,{}).get('disk',row['disk'])
        planned = Path(self.plan['roots'][disk])/'data'/row['split']/sid
        if not any((planned/name).exists() for name in ('.complete.json','.preview.json')):
            for root in self.plan['roots']:
                existing=Path(root)/'data'/row['split']/sid
                if any((existing/name).exists() for name in ('.complete.json','.preview.json')):return existing
        return planned

    def scene(self, sid):
        path=self.path(sid)
        receipt=path/'.complete.json'
        if not receipt.exists():receipt=path/'.preview.json'
        if not receipt.exists():raise HTTPException(409,'Scene is still downloading')
        return self._scene(sid,receipt.name,receipt.stat().st_mtime_ns)

    @lru_cache(maxsize=8)
    def _scene(self, sid, receipt_name, receipt_version):
        path = self.path(sid)
        camera = next((c for c in self.plan['cameras'] if (path/c).is_dir()),None)
        images = sorted(p.name for p in (path/camera).iterdir() if p.suffix.lower() in ('.jpg','.jpeg','.png')) if camera else []
        pose = poses(path/'poses.txt')
        origin = pose[0,1:4] if len(pose) else np.zeros(3)
        calib = read_json(path/'calibration/calib.json',{})
        entry = calib.get(camera,calib.get(str(camera)+'_pinhole',{}))
        segs, groups, features = map_data(path,origin)
        timestamps = []
        for file in [path/f'timestamp.{camera}.txt',path/'timestamp.reference.txt']:
            if file.exists():
                timestamps = [int(Decimal(x)) if Decimal(x)>10**12 else int(Decimal(x)*10**9) for x in file.read_text().split()]
                break
        image_size = list(Image.open(path/camera/images[0]).size) if images else [1920,1200]
        image_indices = [int(Path(p).stem) for p in images]
        # Missing frame numbers must not shift pose/timestamp alignment.
        frame_ts = [timestamps[i]/1e9 if i < len(timestamps) else (pose[min(i,len(pose)-1),0] if len(pose) else i/10) for i in image_indices]
        pose_indices = [int(np.argmin(abs(pose[:,0]-t))) for t in frame_ts] if len(pose) else []
        speed = np.zeros(len(pose))
        if len(pose)>1:
            dt = np.diff(pose[:,0])
            speed[1:] = np.linalg.norm(np.diff(pose[:,1:4],axis=0),axis=1)/np.maximum(dt,1e-6)*3.6
            speed[0] = speed[1]
        local_pose = pose.copy()
        if len(pose):
            local_pose[:,1:4] -= origin
        distance=float(np.linalg.norm(np.diff(pose[:,1:4],axis=0),axis=1).sum()) if len(pose)>1 else 0
        acceleration=np.zeros(len(pose));yaw_rate=np.zeros(len(pose))
        if len(pose)>1 and np.all(np.diff(pose[:,0])>0):
            acceleration=np.gradient(speed/3.6,pose[:,0])
            yaw=np.unwrap([math.atan2(rotation(p[4:])[1,0],rotation(p[4:])[0,0]) for p in pose])
            yaw_rate=np.rad2deg(np.gradient(yaw,pose[:,0]))
        receipt=read_json(path/receipt_name,{})
        inventory = receipt.get('inventory',[])
        return dict(id=sid, split=self.rows[sid]['split'], camera=camera, images=images,
                    timestamps=[str(timestamps[i]) if i<len(timestamps) else str(round(t*1e9)) for i,t in zip(image_indices,frame_ts)], times=frame_ts,
                    poses=local_pose.tolist(), pose_indices=pose_indices, speeds=speed.tolist(),
                    origin=origin.tolist(), image_size=image_size, calibration=calib,
                    motion=dict(distance_m=distance,acceleration_mps2=acceleration.tolist(),yaw_rate_dps=yaw_rate.tolist()), camera_calibration=entry,
                    segments=segs, groups=groups, features=features,
                    files=[str(p.relative_to(path)) for p in path.rglob('*') if p.is_file() and not p.name.startswith('.')],
                    availability=dict(poses=bool(len(pose)), maps=bool(segs),
                                      can=[p for p in inventory if 'can' in p.lower()],
                                      gnss=[p for p in inventory if any(t in p.lower() for t in ('gnss','gps','ins.'))]),
                    receipt=receipt)


def create_app(state):
    app = FastAPI(title='KITScenes Explorer', docs_url=None, redoc_url=None)
    store = Store(state)

    @app.get('/api/health')
    def health():
        return dict(ok=True,revision=store.plan['revision'])

    @app.get('/api/status')
    def status():
        s = read_json(store.state/'status.json',{})
        s['stale'] = s.get('running',False) and time.time()-s.get('updated',0)>60
        s['benchmark'] = read_json(store.state/'benchmark.json',{})
        s['plan'] = dict(revision=store.plan['revision'], cameras=store.plan['cameras'], mode=store.plan['mode'], archive_bytes_per_disk=store.plan['archive_bytes_per_disk'])
        return s

    @app.get('/api/scenes')
    def scenes():
        allocations = read_json(store.state/'allocations.json',{})
        return [dict(id=sid, split=r['split'], disk=allocations.get(sid,{}).get('disk',r['disk']), archive_bytes=r['size'],
                     ready=any((store.path(sid)/name).exists() for name in ('.complete.json','.preview.json')),
                     maps=(store.path(sid)/'maps/map.osm').exists(),
                     preview=not (store.path(sid)/'.complete.json').exists() and (store.path(sid)/'.preview.json').exists()) for sid,r in store.rows.items()]

    @app.get('/api/scenes/{sid}')
    def scene(sid:str):
        return store.scene(sid)

    @lru_cache(maxsize=96)
    def thumbnail(sid, frame, width):
        meta = store.scene(sid)
        if not meta['camera'] or frame < 0 or frame >= len(meta['images']):
            raise HTTPException(404,'Frame unavailable')
        p = store.path(sid)/meta['camera']/meta['images'][frame]
        with Image.open(p) as im:
            im.draft('RGB',(width,width))
            im.thumbnail((width,width),Image.Resampling.LANCZOS)
            out = io.BytesIO()
            im.convert('RGB').save(out,format='JPEG',quality=86)
            return out.getvalue()

    @app.get('/api/scenes/{sid}/image/{frame}')
    def image(sid:str,frame:int,width:int=Query(1280,ge=320,le=4096)):
        return Response(thumbnail(sid,frame,width), media_type='image/jpeg',headers={'Cache-Control':'private, max-age=86400'})

    @app.get('/api/scenes/{sid}/file')
    def file(sid:str,path:str):
        base = store.path(sid).resolve()
        target = (base/path).resolve()
        if not target.is_relative_to(base) or not target.is_file() or any(p.startswith('.') for p in Path(path).parts):
            raise HTTPException(404)
        return FileResponse(target)

    app.mount('/',StaticFiles(directory=WEB,html=True),name='web')
    return app
