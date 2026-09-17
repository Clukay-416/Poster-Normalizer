import contextlib
import hashlib
import io
import threading
import zipfile
from pathlib import Path

import pytest
from app.model_center import ModelCenter
from app.paths import child
from app.store import Store
from test_workflow import client, upload, inputs, png


def depot(tmp_path,payload=b'valid-model-data'):
    data=tmp_path/'data';data.mkdir()
    catalog={'models':[{'id':'test','name':'Test','adapter':'test','source_page':'https://huggingface.co/test',
              'files':[{'path':'model.bin','url':'https://huggingface.co/test/model.bin','size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}]}]}
    return ModelCenter(data,Store(data/'state.sqlite'),catalog=catalog,root=tmp_path/'models')


@pytest.mark.parametrize('partial,code',[(0,200),(5,206),(5,200)])
def test_model_download_resume_verify_and_invalidate(tmp_path,monkeypatch,partial,code):
    payload=b'valid-model-data';center=depot(tmp_path,payload);folder=center.root/'test';folder.mkdir()
    if partial:(folder/'model.bin.part').write_bytes(payload[:partial])
    @contextlib.contextmanager
    def stream(url,headers):
        assert headers==({'Range':f'bytes={partial}-'} if partial else {})
        class Response:
            status_code=code
            headers={'content-range':f'bytes {partial}-{len(payload)-1}/{len(payload)}'}
            def iter_bytes(self,size):yield payload[partial:] if code==206 else payload
        yield Response()
    monkeypatch.setattr('app.model_center.stream',stream)
    center.cancels['test']=threading.Event();center.run('test','download')
    center.enable('test',True)
    assert center.inventory()['models'][0]['runtime_ready']
    (folder/'model.bin').write_bytes(b'x'*len(payload))
    assert not center.inventory()['models'][0]['runtime_ready']
    with pytest.raises(ValueError):center.require('test')


def test_bad_hash_not_published(tmp_path,monkeypatch):
    center=depot(tmp_path)
    @contextlib.contextmanager
    def stream(url,headers):
        class Response:
            status_code=200;headers={}
            def iter_bytes(self,size):yield b'x'*16
        yield Response()
    monkeypatch.setattr('app.model_center.stream',stream)
    center.cancels['test']=threading.Event()
    with pytest.raises(ValueError,match='校验'):center.run('test','download')
    assert not (center.root/'test/model.bin').exists()
    assert list((center.root/'test').glob('*.bad-*'))


def test_depot_migration_without_network(tmp_path,monkeypatch):
    center=depot(tmp_path);folder=center.root/'test';folder.mkdir();(folder/'model.bin').write_bytes(b'valid-model-data')
    monkeypatch.setattr('app.model_center.stream',lambda *args:pytest.fail('verification must stay offline'))
    center.cancels['test']=threading.Event();center.run('test','verify');center.enable('test',True)
    assert center.require('test')==folder
    with pytest.raises(ValueError):center.change_root(str(center.data))
    for path in ('../escape','C:/escape','x\\escape','/absolute'):
        with pytest.raises(ValueError):child(center.root,path)


def test_pages_admin_routes_and_model_honesty(client):
    c,app=client
    for page in ('/','/models.html','/tasks.html','/assets.html'):
        assert c.get(page).status_code==200
    rows=c.get('/api/models').json()['models']
    assert all(not m['runtime_ready'] for m in rows)
    assert c.post('/api/models/ocr_det_v5/action',json={'action':'enable','license_ack':True}).status_code==422
    assert c.get('/api/assets/search?provider=tvmaze&q=test').status_code==422
    assert c.get('/api/diagnostics').status_code==200
    c.post('/api/logout');c.post('/api/login',json={'name':'member','code':app.state.codes['member']})
    for path,body in [('/api/models/scan',{}),('/api/queue',{'paused':True}),('/api/models/ocr_det_v5/action',{'action':'download'})]:
        assert c.post(path,json=body).status_code==403
    assert c.get('/api/assets/config').status_code==403


def test_zip_import_and_batch_queue_controls(client):
    c,app=client;blob=io.BytesIO();im,_=inputs()
    with zipfile.ZipFile(blob,'w') as z:
        z.writestr('../outside.png',png(im));z.writestr('folder/good.png',png(im));z.writestr('broken.jpg',b'bad')
    r=c.post('/api/import/zip',data={'profile':'profile_b'},files={'file':('images.zip',blob.getvalue(),'application/zip')})
    assert r.status_code==200
    assert [i['ok'] for i in r.json()['items']]==[False,True,False]
    jid=next(i['id'] for i in r.json()['items'] if i['ok'])
    c.post('/api/queue',json={'paused':True});_,edit=inputs()
    assert c.post(f'/api/jobs/{jid}/submit',json={'revision':0,**edit}).status_code==200
    items=[{'id':jid,'revision':1}]
    for action,state in [('pause','PAUSED'),('resume','QUEUED'),('cancel','CANCELLED')]:
        r=c.post('/api/task-actions',json={'action':action,'items':items})
        assert r.json()['items'][0]['ok'],r.json()
        assert c.get(f'/api/jobs/{jid}').json()['status']==state
    assert c.get('/api/task-center?status=CANCELLED').json()['total']==1
    assert c.get('/api/backup').status_code==200
