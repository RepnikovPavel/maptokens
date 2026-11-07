import hashlib
import io
import json
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from maptokens.kitscenes.download import Runner, TarFileView, atomic_json, safe_relative, selected
from maptokens.kitscenes.transport import RemoteTar
from maptokens.kitscenes.server import create_app

SID='00000000-0000-0000-0000-000000000000'


def tar_bytes():
    b=io.BytesIO()
    with tarfile.open(fileobj=b,mode='w',format=tarfile.PAX_FORMAT) as tf:
        for name,data in [('calibration/calib.json',b'{}'),('camera_ring_front/0000000000.jpg',b'front'*999),('camera_ring_front_left/0000000000.jpg',b'left'*10000),('lidar_top/0000000000.parquet',b'lidar'*10000),('maps/map.osm',b'<osm/>'),('annotations/objects.json',b'{"objects":[]}'),('can/speed.csv',b't,s\n'),('gnss/poses.txt',b'1 2 3'),('annotations/'+('long-'*30)+'.json',b'{}')]:
            info=tarfile.TarInfo(SID+'/'+name);info.size=len(data);tf.addfile(info,io.BytesIO(data))
    return b.getvalue()


@pytest.fixture
def range_server():
    payload=tar_bytes();requests=[];ignore=[False]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            if not self.headers.get('Range'):
                self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload);return
            first,last=self.headers['Range'].removeprefix('bytes=').split('-');lo=int(first);hi=int(last) if last else len(payload)-1;requests.append((lo,hi))
            if ignore[0]:
                self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload);return
            b=payload[lo:hi+1];self.send_response(206);self.send_header('Content-Range',f'bytes {lo}-{hi}/{len(payload)}');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield f'http://127.0.0.1:{server.server_port}/archive',payload,requests,ignore
    server.shutdown();thread.join()


def test_select_only_requested_sensor_and_keep_all_metadata():
    for p in ['calibration/calib.json','maps/map.osm','poses.txt','timestamp.lidar_top.txt','labels/lidar_objects.parquet','can/foo.bin','gnss/gps.csv','annotations/objects.json']:
        assert selected(p,['camera_ring_front'])
    assert selected('camera_ring_front/000.jpg',['camera_ring_front'])
    assert not selected('camera_ring_front_left/000.jpg',['camera_ring_front'])
    assert not selected('async/lidar_top/000.parquet',['camera_ring_front'])
    assert not selected('radar_front/000.parquet',['camera_ring_front'])
    assert not selected('maps/map.osm',['camera_ring_front'],exclude=['maps/*'])
    assert selected('radar_front/000.parquet',[],False,include=['radar_front/*'])


@pytest.mark.parametrize('name',['../x','/tmp/x',SID+'/../x','another/scene'])
def test_unsafe_names_rejected(name):
    with pytest.raises(ValueError):safe_relative(name,SID)


def test_tar_ranges_and_pax_headers(range_server):
    url,payload,requests,_=range_server
    r=RemoteTar(url,len(payload));r.direct=url
    with tarfile.open(fileobj=TarFileView(r),mode='r:') as tf:
        members=list(tf)
    assert len(members)==9
    assert 'long-' in members[-1].name
    assert sum(hi-lo+1 for lo,hi in requests)<len(payload)/4
    # Large excluded sensor bodies were skipped through seeks.
    assert all(hi-lo+1<=1024 for lo,hi in requests)
    r.close()


def test_reject_ignored_http_range(range_server,monkeypatch):
    url,payload,requests,ignore=range_server;ignore[0]=True
    r=RemoteTar(url,len(payload));r.direct=url
    monkeypatch.setattr('maptokens.kitscenes.transport.time.sleep',lambda n:None)
    with pytest.raises(IOError):r.get(512,512)
    r.close()


def test_runner_resume_and_data_integrity(tmp_path,range_server,monkeypatch):
    url,payload,requests,_=range_server
    state=tmp_path/'state';root=tmp_path/'disk'
    row=dict(sequence_id=SID,split='val',size=len(payload),disk=0,archive_path='a.tar',archive_sha256=hashlib.sha256(payload).hexdigest())
    plan=dict(roots=[str(root)],scenes=[row],cameras=['camera_ring_front'],metadata=True,include=[],exclude=[],revision='test',mode='selective')
    atomic_json(state/'plan.json',plan)
    monkeypatch.setattr('maptokens.kitscenes.download.resolve_url',lambda *a:url)
    monkeypatch.setattr('maptokens.kitscenes.download.shutil.disk_usage',lambda p:SimpleNamespace(free=10**12,total=10**12))
    Runner(state,'python',1).run()
    out=root/'data/val'/SID
    assert (out/'camera_ring_front/0000000000.jpg').read_bytes()==b'front'*999
    assert not (out/'lidar_top').exists() and not (out/'camera_ring_front_left').exists()
    assert (out/'can/speed.csv').exists() and (out/'annotations/objects.json').exists()
    count=len(requests)
    Runner(state,'python',1).run();assert len(requests)==count
    # A damaged file is repaired when an interrupted scene is resumed.
    (out/'.complete.json').unlink();(out/'camera_ring_front/0000000000.jpg').write_bytes(b'bad')
    Runner(state,'python',1).run()
    assert (out/'camera_ring_front/0000000000.jpg').read_bytes()==b'front'*999


def fixture_scene(tmp_path):
    sid=SID;root=tmp_path/'disk';p=root/'data/val'/sid;p.mkdir(parents=True)
    atomic_json(tmp_path/'state/plan.json',dict(roots=[str(root)],revision='test',scenes=[dict(sequence_id=sid,split='val',disk=0,size=1234)],cameras=['camera_ring_front'],mode='selective',archive_bytes_per_disk=[1234]))
    atomic_json(p/'.complete.json',dict(inventory=['poses.txt']))
    (p/'camera_ring_front').mkdir();Image.new('RGB',(800,600),'blue').save(p/'camera_ring_front/0000000000.jpg')
    Image.new('RGB',(800,600),'red').save(p/'camera_ring_front/0000000002.jpg')
    (p/'timestamp.reference.txt').write_text('1000000000000000000\n1000000000100000000\n1000000000200000000\n')
    (p/'poses.txt').write_text('1000000000 450000 5400000 100 0 0 0 1\n1000000000.1 450001 5400000 100 0 0 0 1\n1000000000.2 450002 5400000 100 0 0 0 1\n')
    return tmp_path/'state'


def test_web_api_metadata_frames_and_traversal(tmp_path):
    state=fixture_scene(tmp_path)
    with TestClient(create_app(state)) as client:
        assert client.get('/').status_code==200
        assert client.get('/project.wasm').content[:4]==b'\0asm'
        assert client.get('/api/scenes').json()[0]['ready']
        meta=client.get(f'/api/scenes/{SID}').json()
        assert meta['pose_indices']==[0,2]
        assert meta['poses'][0][1:4]==[0,0,0]
        assert meta['image_size']==[800,600]
        assert client.get(f'/api/scenes/{SID}/image/0?width=400').status_code==200
        assert client.get(f'/api/scenes/{SID}/image/-1').status_code==404
        assert client.get(f'/api/scenes/{SID}/image/22').status_code==404
        assert client.get(f'/api/scenes/{SID}/file',params={'path':'../../../../etc/passwd'}).status_code==404
        assert client.get(f'/api/scenes/{SID}/file',params={'path':'.complete.json'}).status_code==404
        assert client.get('/api/scenes/nope').status_code==404

@pytest.mark.parametrize('method',['python','curl','wget'])
def test_transports_return_identical_partial_bytes(range_server,method):
    import shutil
    if method!='python' and not shutil.which(method):pytest.skip(f'{method} is not installed')
    url,payload,requests,_=range_server;r=RemoteTar(url,len(payload));r.direct=url
    assert r.get(2048,1024,method)==payload[2048:3072]
    r.close()


@pytest.mark.parametrize('method',['wget','curl'])
def test_full_mirror_native_resume_sha_and_archive_retention(tmp_path,range_server,monkeypatch,method):
    import shutil
    from maptokens.kitscenes.mirror import Mirror
    if not shutil.which(method):pytest.skip(f'{method} unavailable')
    url,payload,requests,_=range_server
    root=tmp_path/'disk';state=tmp_path/'full'
    row=dict(sequence_id=SID,split='val',size=len(payload),disk=0,archive_path=f'data/val/{SID}.tar',archive_sha256=hashlib.sha256(payload).hexdigest())
    plan=dict(roots=[str(root)],scenes=[row],cameras=['camera_ring_front'],metadata=True,include=[],exclude=[],revision='test',mode='full',archive_bytes_per_disk=[len(payload)])
    atomic_json(state/'plan.json',plan)
    archive=root/row['archive_path'];archive.parent.mkdir(parents=True)
    archive.with_name(archive.name+'.part').write_bytes(payload[:2048])
    monkeypatch.setattr('maptokens.kitscenes.mirror.resolve_url',lambda *a:url)
    Mirror(state,method,1).run()
    assert archive.read_bytes()==payload
    assert (archive.with_suffix('')/'camera_ring_front/0000000000.jpg').exists()
    assert not (archive.with_suffix('')/'lidar_top').exists()
    assert Mirror(state,method,1).verified(row)
    mirror=Mirror(state,method,1);mirror.run();assert mirror.skipped==1


def test_map_projection_origin_and_feature_identity(tmp_path):
    import numpy as np
    from pyproj import Transformer
    from maptokens.kitscenes.server import map_data
    folder=tmp_path/'maps';folder.mkdir()
    atomic_json(folder/'origin.json',dict(latitude=49.0,longitude=8.0))
    (folder/'map.osm').write_text('<osm><node id="1" lat="49" lon="8"><tag k="ele" v="2"/></node><node id="2" lat="49.00001" lon="8"><tag k="ele" v="2"/></node><way id="3"><nd ref="1"/><nd ref="2"/><tag k="type" v="line_thin"/><tag k="subtype" v="solid"/></way></osm>')
    segments,groups,features=map_data(tmp_path,np.array([0.,0.,2.]))
    assert np.allclose(segments[0][:3],[0,0,0])
    assert .9<segments[0][4]<1.2
    assert features[segments[0][7]]['id']=='3'
    assert groups==['line_thin:solid']


def test_exact_source_timestamp_and_motion(tmp_path):
    state=fixture_scene(tmp_path)
    path=tmp_path/'disk/data/val'/SID/'timestamp.reference.txt'
    path.write_text('1000000000000000001\n1000000000100000001\n1000000000200000001\n')
    with TestClient(create_app(state)) as client:
        m=client.get(f'/api/scenes/{SID}').json()
        assert m['timestamps'][0]=='1000000000000000001'
        assert m['motion']['distance_m']==2
        assert len(m['motion']['acceleration_mps2'])==3


def test_prepare_partial_preview_and_upgrade_receipt(tmp_path):
    from maptokens.kitscenes.preview import prepare_preview
    jpeg=io.BytesIO();Image.new('RGB',(640,400),'gray').save(jpeg,format='JPEG')
    body=io.BytesIO()
    files=[('calibration/calib.json',b'{}'),('camera_ring_front/0000000000.jpg',jpeg.getvalue()),
           ('lidar_top/big.bin',b'x'*(2*1024*1024)),('maps/map.osm',b'<osm/>'),
           ('maps/origin.json',b'{"latitude":49,"longitude":8}'),
           ('poses.txt',b'1 0 0 0 0 0 0 1\n'),('timestamp.reference.txt',b'1000000000\n')]
    with tarfile.open(fileobj=body,mode='w') as tf:
        for name,data in files:
            m=tarfile.TarInfo(SID+'/'+name);m.size=len(data);tf.addfile(m,io.BytesIO(data))
    payload=body.getvalue();state=tmp_path/'state';root=tmp_path/'disk';archive=root/f'data/val/{SID}.tar';archive.parent.mkdir(parents=True)
    archive.with_suffix('.tar.part').write_bytes(payload[:16384])
    row=dict(sequence_id=SID,split='val',size=len(payload),disk=0,archive_path=f'data/val/{SID}.tar',archive_sha256=hashlib.sha256(payload).hexdigest())
    plan=dict(roots=[str(root)],scenes=[row],cameras=['camera_ring_front'],metadata=True,include=[],exclude=[],revision='test',mode='full',archive_bytes_per_disk=[len(payload)])
    atomic_json(state/'plan.json',plan)
    start=(len(payload)-1024*1024)//512*512;cache=state/'previews'/SID;cache.mkdir(parents=True)
    (cache/'tail.bin').write_bytes(payload[start:])
    atomic_json(cache/'tail.json',dict(start=start,size=len(payload)-start,revision='test',archive_sha256=row['archive_sha256']))
    result=prepare_preview(state,SID,1)
    assert result['images']==1 and not result['archive_verified']
    out=archive.with_suffix('')
    assert (out/'.preview.json').exists() and not (out/'.complete.json').exists()
    assert (out/'maps/map.osm').read_bytes()==b'<osm/>'
    assert not (out/'lidar_top').exists()
    with TestClient(create_app(state)) as client:
        assert client.get('/api/scenes').json()[0]['preview']
        assert client.get(f'/api/scenes/{SID}').json()['receipt']['mode']=='preview'
        atomic_json(out/'.complete.json',dict(mode='full',inventory_complete=True))
        assert not client.get('/api/scenes').json()[0]['preview']
        assert client.get(f'/api/scenes/{SID}').json()['receipt']['mode']=='full'
