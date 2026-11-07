import json
import os

import pytest

from maptokens.kitscenes.progress import DownloadProgress


def write_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def transfer(tmp_path):
    root=tmp_path/'disk'
    state=tmp_path/'state';state.mkdir()
    rows=[dict(sequence_id=sid,split='train',disk=0,size=size,archive_path=f'data/train/{sid}.tar')
          for sid,size in [('ready',1000),('fast',2000),('slow',3000)]]
    plan=dict(roots=[str(root)],scenes=rows,hd_only=True)
    for row in rows:
        archive=root/row['archive_path'];archive.parent.mkdir(parents=True,exist_ok=True)
        if row['sequence_id']=='ready':
            archive.write_bytes(b'a'*1000);os.utime(archive,(90,90))
            write_json(state/'verified/ready.json',dict(verified=95))
            write_json(archive.with_suffix('')/'.complete.json',dict(finished=100))
            (archive.with_suffix('')/'maps').mkdir()
            (archive.with_suffix('')/'maps/map.osm').write_text('<osm/>')
        else:archive.with_name(archive.name+'.part').write_bytes(b'a'*(1000 if row['sequence_id']=='fast' else 2000))
    def sample(at, fast, slow, phase='download', started=10, running=True):
        return dict(event='progress',started=started,updated=at,running=running,
                    active=dict(fast=dict(phase=phase,saved_bytes=fast),slow=dict(phase='download',saved_bytes=slow)))
    (state/'download.jsonl').write_text('\n'.join(json.dumps(s) for s in [sample(180,800,1900),sample(200,1000,2000)]))
    return root,state,plan,sample


def test_retained_bytes_ready_count_and_earliest_viewable_scene(transfer):
    root,state,plan,sample=transfer
    # A partial file outside the selected plan must not inflate progress.
    (root/'excluded.tar.part').write_bytes(b'x'*10000)
    progress=DownloadProgress(state,plan).snapshot(sample(200,1000,2000),now=200)
    assert progress['downloaded_bytes']==4000 and progress['total_bytes']==6000
    assert progress['download_percent']==pytest.approx(100*2/3)
    assert progress['ready_scenes']==1 and progress['ready_percent']==pytest.approx(100/3)
    nxt=progress['next']
    assert nxt['id']=='fast' and nxt['percent']==50 and nxt['speed_bps']==10
    assert nxt['download_eta_seconds']==100
    # Verification and extraction add 20 seconds from measured prior I/O rates.
    assert nxt['processing_seconds']==20 and nxt['eta_seconds']==120


def test_full_archive_does_not_mean_ready_and_next_advances(transfer):
    root,state,plan,sample=transfer
    tracker=DownloadProgress(state,plan)
    tracker.snapshot(sample(200,1000,2000),now=200)
    archive=root/'data/train/fast.tar'
    archive.with_name(archive.name+'.part').unlink();archive.write_bytes(b'a'*2000)
    tracker.snapshot(sample(210,2000,2050,phase='verify'),now=210)
    p=tracker.snapshot(sample(215,2000,2075,phase='verify'),now=215)
    assert p['downloaded_bytes']==5000 and p['ready_scenes']==1
    assert p['next']['id']=='fast' and p['next']['phase']=='verify'
    assert p['next']['eta_seconds']==15
    tracker.snapshot(sample(220,2000,2100,phase='extract front + metadata'),now=220)
    p=tracker.snapshot(sample(225,2000,2125,phase='extract front + metadata'),now=225)
    assert p['next']['eta_seconds']==5 and p['ready_scenes']==1
    write_json(archive.with_suffix('')/'.complete.json',dict(finished=230))
    (archive.with_suffix('')/'maps').mkdir();(archive.with_suffix('')/'maps/map.osm').write_text('<osm/>')
    p=tracker.snapshot(sample(230,2000,2150,phase='extract front + metadata'),now=230)
    assert p['ready_scenes']==2 and p['next']['id']=='slow'


def test_stalled_stale_paused_and_process_restart_do_not_invent_eta(transfer):
    root,state,plan,sample=transfer
    tracker=DownloadProgress(state,plan)
    tracker.snapshot(sample(200,1000,2000),now=200)
    p=tracker.snapshot(sample(235,1000,2000),now=235)
    assert p['next']['speed_bps']==0 and p['next']['eta_seconds'] is None
    p=tracker.snapshot(sample(235,1000,2000),now=300)
    assert p['stale'] and not p['running'] and p['next']['eta_seconds'] is None
    p=tracker.snapshot(sample(305,1000,2000,running=False),now=305)
    assert not p['running'] and p['next']['eta_seconds'] is None
    p=tracker.snapshot(sample(305,1000,2000,running=False),now=400)
    assert not p['running'] and not p['stale']
    p=tracker.snapshot(sample(320,1000,2000,started=310),now=320)
    assert p['running'] and p['next']['eta_seconds'] is None


def test_partial_preview_is_already_available_but_not_fully_ready(transfer):
    root,state,plan,sample=transfer
    folder=root/'data/train/fast'
    write_json(folder/'.preview.json',dict(mode='preview'))
    (folder/'maps').mkdir();(folder/'maps/map.osm').write_text('<osm/>')
    p=DownloadProgress(state,plan).snapshot(sample(200,1000,2000),now=200)
    assert p['available_scenes']==2 and p['ready_scenes']==1
    assert p['next']['id']=='slow'


def test_all_ready_is_complete_even_after_downloader_stops(transfer):
    root,state,plan,sample=transfer
    for row in plan['scenes'][1:]:
        archive=root/row['archive_path']
        archive.with_name(archive.name+'.part').unlink();archive.write_bytes(b'a'*row['size'])
        write_json(archive.with_suffix('')/'.complete.json',dict(finished=230))
        (archive.with_suffix('')/'maps').mkdir();(archive.with_suffix('')/'maps/map.osm').write_text('<osm/>')
    p=DownloadProgress(state,plan).snapshot(sample(235,2000,3000,running=False),now=400)
    assert p['ready_percent']==p['download_percent']==100
    assert not p['stale'] and p['next'] is None
