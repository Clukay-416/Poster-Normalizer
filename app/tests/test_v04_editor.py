import json
import subprocess
from pathlib import Path
import pytest
from app.geometry import placement, validate_geometry
from app.store import Conflict
from test_workflow import client, upload, inputs, wait


def test_confirmation_is_idempotent_and_version_guarded(client):
    c,app=client
    jid=upload(c);_,edit=inputs()
    assert c.post(f'/api/jobs/{jid}/submit',json={'revision':0,**edit}).status_code==200
    assert wait(c,jid)['status']=='REVIEW'
    for _ in range(2):
        assert c.post(f'/api/jobs/{jid}/approve',json={'revision':1}).status_code==200
    assert c.post(f'/api/jobs/{jid}/approve',json={'revision':0}).status_code!=200
    with app.state.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM events WHERE kind='APPROVED'").fetchone()[0]==1


def test_crop_draft_roundtrip_and_validation(client):
    c,app=client;jid=upload(c);_,edit=inputs()
    params={'revision':0,**edit,'crop':{'x':10,'y':20,'width':200,'height':300},
            'background':{'mode':'free','zoom':1.25,'x':-20,'y':5}}
    assert c.post(f'/api/jobs/{jid}/draft',json=params).status_code==200
    draft=c.get(f'/api/jobs/{jid}/edit').json()
    assert draft['_draft'] and draft['background']==params['background']
    assert c.request('DELETE',f'/api/jobs/{jid}/draft',json={'revision':0}).status_code==200
    assert '_draft' not in c.get(f'/api/jobs/{jid}/edit').json()
    assert c.post(f'/api/jobs/{jid}/submit',json={**params,'crop':{'x':239,'y':0,'width':2,'height':20}}).status_code==422
    assert c.post(f'/api/jobs/{jid}/submit',json=params).status_code==200
    result=wait(c,jid)
    assert result['status']=='REVIEW'
    assert result['qc']['background']==params['background']
    assert result['qc']['crop']==params['crop']


def test_trash_restore_running_guard_and_purge(client):
    c,app=client;jid=upload(c);store=app.state.store
    def act(action):
        return c.post('/api/task-actions',json={'action':action,'items':[{'id':jid,'revision':0}]}).json()['items'][0]
    assert act('trash')['ok']
    assert not c.get('/api/jobs').json()
    assert c.get('/api/task-center?status=TRASHED').json()['total']==1
    with pytest.raises(Conflict):store.submit(jid,0,{})
    assert act('restore')['ok'] and store.get(jid)['status']=='MANUAL'
    with store.connect() as db:db.execute("UPDATE jobs SET status='RUNNING' WHERE id=?",(jid,))
    assert act('trash')['ok'] and store.get(jid)['status']=='TRASH_PENDING'
    assert not act('purge')['ok']
    assert not act('restore')['ok']
    store.finish(jid,0,'REVIEW')
    assert store.get(jid)['status']=='TRASHED'
    assert act('purge')['ok']
    assert store.get(jid) is None and not (app.state.jobs_root/jid).exists()


def test_frontend_backend_geometry_agree():
    script=Path(__file__).resolve().parents[1]/'static/geometry.js'
    cases=[([241,359],[412,600],{'mode':mode,'zoom':zoom,'x':-13.5,'y':7.25})
           for mode in ('contain','cover','free') for zoom in (.25,1,1.57,8)]
    code='const {backgroundPlacement}=require(process.argv[1]); console.log(JSON.stringify(JSON.parse(process.argv[2]).map(c=>backgroundPlacement(...c))));'
    result=json.loads(subprocess.check_output(['node','-e',code,str(script),json.dumps(cases)]))
    for args,actual in zip(cases,result):
        assert list(placement(*args))==[actual[k] for k in ('x','y','w','h')]
    with pytest.raises(ValueError):validate_geometry({'background':{'zoom':float('nan')}},(10,10))


def test_compose_only_without_masks_or_title(client):
    from test_workflow import data
    from PIL import Image
    c,app=client;jid=upload(c)
    request={'revision':0,'operation':'compose','repair_mask':data(Image.new('L',(240,360))),
             'background':{'mode':'cover','zoom':2,'x':5,'y':-10}}
    assert c.post(f'/api/jobs/{jid}/submit',json=request).status_code==200
    first=wait(c,jid);assert first['status']=='REVIEW'
    before=c.get(f'/api/jobs/{jid}/artifact/base.png').content
    request.update(revision=1,base_revision=1,background={'mode':'contain','zoom':1,'x':0,'y':0})
    assert c.post(f'/api/jobs/{jid}/submit',json=request).status_code==200
    assert wait(c,jid)['status']=='REVIEW'
    assert c.get(f'/api/jobs/{jid}/artifact/base.png').content==before


def test_multiple_candidates_preserve_protection_and_history(client,monkeypatch):
    import io
    import numpy as np
    from PIL import Image,ImageDraw
    from test_workflow import data
    c,app=client;jid=upload(c);image,edit=inputs()
    protected=Image.new('L',image.size);ImageDraw.Draw(protected).rectangle((70,220,90,270),fill=255)
    # Pipeline contract fake, intentionally NOT a model inference/quality test.
    monkeypatch.setattr(app.state.models,'require',lambda mid:app.state.models.root/mid)
    monkeypatch.setattr(app.state.ai.cuda,'run',lambda mid,op,rgb,mask,params:{'outputs':[np.full_like(rgb,20),np.full_like(rgb,220)]})
    app.state.store.set_setting('gpu_mode','POSTER_MAX')
    request={'revision':0,**edit,'engine':'sdxl','protection_mask':data(protected),
             'repair_params':{'candidates':2,'dilate':4,'feather':3}}
    assert c.post(f'/api/jobs/{jid}/submit',json=request).status_code==200
    job=wait(c,jid);assert job['status']=='REVIEW',job
    assert job['qc']['candidate_count']==2
    source=np.array(image);mask=np.array(Image.open(io.BytesIO(c.get(f'/api/jobs/{jid}/artifact/repair_mask.png').content)))
    for i in (0,1):
        out=np.array(Image.open(io.BytesIO(c.get(f'/api/jobs/{jid}/artifact/base-{i}.png').content)))
        assert np.array_equal(out[mask==0],source[mask==0])
        assert np.array_equal(out[np.array(protected)>0],source[np.array(protected)>0])
    old=c.get(f'/api/jobs/{jid}/artifact/final.png?revision=1').content
    chosen=c.get(f'/api/jobs/{jid}/artifact/final-1.png').content
    r=c.post(f'/api/jobs/{jid}/candidate',json={'revision':1,'index':1})
    assert r.status_code==200 and r.json()['revision']==2
    assert c.get(f'/api/jobs/{jid}/artifact/final.png').content==chosen
    assert c.get(f'/api/jobs/{jid}/artifact/final.png?revision=1').content==old
    assert c.post(f'/api/jobs/{jid}/approve',json={'revision':1}).status_code==409
    assert c.post(f'/api/jobs/{jid}/candidate',json={'revision':1,'index':0}).status_code==409


def test_generation_requires_explicit_confirmation(client):
    c,app=client
    assert c.post('/api/titles/rebuild',json={'text':'测试'}).status_code==422
    assert c.post('/api/models/anytext2/self-test',json={}).status_code==422
