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
