import base64
import io
import json
import time
import uuid
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.imaging import process
from app.main import create_app
from app.store import Store


def png(image):
    stream = io.BytesIO()
    image.save(stream, 'PNG')
    return stream.getvalue()


def data(image):
    return 'data:image/png;base64,'+base64.b64encode(png(image)).decode()


def inputs():
    im = Image.new('RGB',(240,360),'#30485b')
    ImageDraw.Draw(im).rectangle((50,230,180,260), fill='white')
    mask = Image.new('L',im.size)
    ImageDraw.Draw(mask).rectangle((48,228,182,262), fill=255)
    alpha = Image.new('L',im.size)
    ImageDraw.Draw(alpha).rectangle((50,230,180,260), fill=255)
    return im, {'repair_mask':data(mask),'extract_mask':data(alpha),'position':None}


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as c:
        assert c.post('/api/login',json={'name':'tester','code':app.state.codes['admin']}).status_code == 200
        yield c, app


def upload(c,blob=None):
    im,_=inputs()
    r=c.post('/api/jobs/batch',data={'profile':'profile_b'},files=[('files',('海报.png',blob or png(im),'image/png'))])
    assert r.status_code==200
    return r.json()['items'][0]['id']


def wait(c,jid):
    end=time.monotonic()+8
    while time.monotonic()<end:
        j=c.get('/api/jobs/'+jid).json()
        if j['status'] not in ('RUNNING','QUEUED'):
            return j
        time.sleep(.02)
    pytest.fail('worker timed out')


def test_real_roundtrip_and_revisions(client):
    c,app=client
    jid=upload(c)
    _,edit=inputs()
    r=c.post(f'/api/jobs/{jid}/submit',json={'revision':0,**edit})
    assert r.status_code==200
    j=wait(c,jid)
    assert j['status']=='REVIEW',j
    assert j['qc']['outside_mask_changed_channels']==0
    assert j['qc']['margins']['bottom']>=50
    assert j['qc']['title_residual'] is None
    final=Image.open(io.BytesIO(c.get(f'/api/jobs/{jid}/artifact/final.png').content))
    assert final.size==(412,600)
    assert c.post(f'/api/jobs/{jid}/approve',json={'revision':1}).status_code==200
    z=zipfile.ZipFile(io.BytesIO(c.get('/api/export').content))
    assert len(z.namelist())==2
    assert c.post(f'/api/jobs/{jid}/submit',json={'revision':0,**edit}).status_code==409
    assert c.post(f'/api/jobs/{jid}/submit',json={'revision':1,**edit}).status_code==200
    j=wait(c,jid)
    assert j['revision']==2
    assert c.get(f'/api/jobs/{jid}/artifact/final.png?revision=1').status_code==200
    assert c.post(f'/api/jobs/{jid}/approve',json={'revision':1}).status_code==409
    assert c.get('/api/export').status_code==422


def test_ingest_failure_isolated_and_access(client):
    c,app=client
    im,_=inputs()
    r=c.post('/api/jobs/batch',data={'profile':'profile_a'},files=[('files',('bad.png',b'bad','image/png')),('files',('../../good.png',png(im),'image/png'))])
    assert [x['ok'] for x in r.json()['items']]==[False,True]
    jid=r.json()['items'][1]['id']
    assert c.get('/api/jobs/'+jid).json()['name']=='good.png'
    assert c.get(f'/api/jobs/{jid}/artifact/original.bin').status_code==404
    assert c.post('/api/system/gpu-mode',json={'mode':'PAUSE_AI'},headers={'Origin':'http://evil.invalid'}).status_code==403
    c.post('/api/logout')
    assert c.get('/api/jobs').status_code==401
    c.post('/api/login',json={'name':'member','code':app.state.codes['member']})
    assert c.post('/api/system/gpu-mode',json={'mode':'PAUSE_AI'}).status_code==403


def test_masks_and_geometry_fail_closed(client):
    c,app=client
    jid=upload(c)
    _,edit=inputs()
    blank=data(Image.new('L',(240,360)))
    assert c.post(f'/api/jobs/{jid}/submit',json={'revision':0,**edit,'repair_mask':blank}).status_code==422
    assert c.post(f'/api/jobs/{jid}/submit',json={'revision':0,**edit,'repair_mask':data(Image.new('L',(20,20),255))}).status_code==422
    r=c.post(f'/api/jobs/{jid}/submit',json={'revision':0,**edit,'position':{'x':0,'y':0,'width':100}})
    assert r.status_code==200
    assert wait(c,jid)['status']=='FAILED'
    assert c.post(f'/api/jobs/{jid}/approve',json={'revision':1}).status_code==422


def test_profile_snapshots(client):
    c,app=client
    jid=upload(c)
    settings=c.get('/api/settings').json()['profiles']
    settings['profile_b']['width']=500
    assert c.put('/api/settings/profiles',json=settings).status_code==200
    assert c.get('/api/jobs/'+jid).json()['profile']['width']==412
    settings['profile_b']['bottom']=5
    assert c.put('/api/settings/profiles',json=settings).status_code==422


def test_restart_recovery_and_atomic_claim(tmp_path):
    s=Store(tmp_path/'test.db')
    p={'width':412,'height':600,'margin':10,'bottom':50,'layout':'bottom'}
    for i in range(3):
        s.add({'id':str(i),'batch_id':'batch','owner':'A' if i<2 else 'B','name':'image','status':'MANUAL','profile':p})
        s.submit(str(i),0,{'test':1})
    assert s.claim()['id']=='0'
    assert s.claim('A')['id']=='2'
    s.recover()
    assert s.get('0')['status']=='INTERRUPTED'
    assert s.get('1')['status']=='QUEUED'
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(4) as pool:
        claims=list(pool.map(lambda _: s.claim(),range(4)))
    assert sum(j is not None for j in claims)==1


def test_pixel_invariance_and_alpha_padding(tmp_path):
    im,edit=inputs()
    logo=Image.new('RGBA',(160,100))
    ImageDraw.Draw(logo).rectangle((40,40,99,59),fill=(255,255,255,255))
    edit['logo']=data(logo)
    job={'id':'unit','revision':1,'source_hash':'test','edit':edit,'profile':{'width':528,'height':296,'margin':10,'bottom':50,'layout':'left'}}
    folder=tmp_path/'unit';folder.mkdir();im.save(folder/'normalized.png')
    qc=process(tmp_path,job)
    out=np.asarray(Image.open(folder/'r1'/'base.png'))
    mask=np.asarray(Image.open(folder/'r1'/'repair_mask.png'))
    assert np.array_equal(out[mask==0],np.asarray(im)[mask==0])
    assert Image.open(folder/'r1'/'logo.png').size==(60,20)
    assert qc['margins']['left']==10


def test_hundred_jobs_failure_does_not_stop_batch(tmp_path):
    app=create_app(tmp_path)
    im,edit=inputs()
    # Small synthetic fixtures test queue behavior, not real poster quality.
    for i in range(100):
        jid=str(uuid.uuid4()); folder=app.state.jobs_root/jid;folder.mkdir();im.save(folder/'normalized.png')
        app.state.store.add({'id':jid,'batch_id':'100','owner':str(i%3),'name':str(i),'status':'MANUAL','width':240,'height':360,'source_hash':'fixture',
                            'profile':{'width':528,'height':296,'margin':10,'bottom':50,'layout':'left'}})
        params=dict(edit)
        if i==17:
            params['position']={'x':0,'y':0,'width':100}
        app.state.store.submit(jid,0,params)
    with TestClient(app):
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            rows=app.state.store.list()
            if all(j['status'] not in ('RUNNING','QUEUED') for j in rows):break
            time.sleep(.05)
        assert sum(j['status']=='REVIEW' for j in rows)==99
        assert sum(j['status']=='FAILED' for j in rows)==1


def test_non_ascii_login_and_newer_db(tmp_path):
    app=create_app(tmp_path)
    with TestClient(app) as c:
        assert c.post('/api/login',json={'name':'a','code':'不是访问码'}).status_code==401
    import sqlite3
    with sqlite3.connect(tmp_path/'future.db') as db:
        db.execute('PRAGMA user_version=99')
    with pytest.raises(RuntimeError):
        Store(tmp_path/'future.db')
