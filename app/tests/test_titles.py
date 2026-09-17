import io

from PIL import Image

from app.imaging import get_mask
from test_workflow import client, data, inputs, upload, wait


def test_alpha_erasure_and_legacy_masks():
    image = Image.new('RGBA', (3, 1))
    image.putdata([(255,255,255,0), (255,255,255,128), (0,0,0,255)])
    assert get_mask(data(image), image.size).tolist() == [[0,128,0]]
    legacy = Image.new('L', (2,1))
    legacy.putdata([0,255])
    assert get_mask(data(legacy), legacy.size).tolist() == [[0,255]]


def test_titles_save_versions_restore_and_permissions(client):
    c,_=client
    original = Image.new('RGBA',(12,8),(200,120,80,255))
    image=original.copy();image.putpixel((0,0),(200,120,80,0))
    body={'name':'暗刃 标题','image':data(image),'original':data(original)}
    r=c.post('/api/titles',json=body)
    assert r.status_code==200,r.text
    first=r.json()['id']
    body['parent']=first;body['name']='精修版'
    second=c.post('/api/titles',json=body).json()['id']
    assert second!=first
    assert c.get('/api/titles').json()['total']==2
    assert c.get('/api/titles/'+second).json()['parent']==first
    saved=Image.open(io.BytesIO(c.get(f'/api/titles/{first}/logo.png').content))
    assert saved.getpixel((0,0))[3]==0
    raw=Image.open(io.BytesIO(c.get(f'/api/titles/{first}/original.png').content))
    assert raw.getpixel((0,0))[3]==255
    assert c.get(f'/api/titles/{first}/metadata.json').status_code==404
    body['image']=data(Image.new('RGBA',(12,8)))
    assert c.post('/api/titles',json=body).status_code==422
    body['image']=data(image);body['original']=data(Image.new('RGBA',(1,1)))
    assert c.post('/api/titles',json=body).status_code==422
    c.post('/api/logout')
    assert c.get('/api/titles').status_code==401
    assert c.get(f'/api/titles/{first}/logo.png').status_code==401
    assert c.post('/api/titles',json=body).status_code==401


def test_editor_draft_survives_title_detour_and_expires_on_submit(client):
    c,_=client;jid=upload(c);_,edit=inputs()
    assert c.post(f'/api/jobs/{jid}/draft',json={'revision':0,**edit}).status_code==200
    assert c.get(f'/api/jobs/{jid}/edit').json()['repair_mask']==edit['repair_mask']
    assert c.get('/api/jobs/'+jid).json()['revision']==0
    assert c.post(f'/api/jobs/{jid}/draft',json={'revision':9,**edit}).status_code==409
    assert c.post(f'/api/jobs/{jid}/submit',json={'revision':0,**edit}).status_code==200
    job=wait(c,jid)
    assert job['status']=='REVIEW'
    restored = c.get(f'/api/jobs/{jid}/edit').json()
    assert restored['repair_mask']==edit['repair_mask']
    assert restored['engine']=='opencv'


def test_title_discovery_does_not_query_tvmaze(client,monkeypatch):
    c,_=client
    c.put('/api/assets/config',json={'network_enabled':True,'tmdb_token':'test-token'})
    calls=[]
    def fetch(url,headers=None,hosts=None):
        calls.append(url)
        if '/search/multi?' in url:
            return {'results':[{'id':3,'media_type':'tv','name':'测试剧'}]}
        if '/tv/3/images' in url:
            return {'logos':[{'file_path':'/logo.png','width':600,'height':200,'iso_639_1':'zh'}], 'posters':[{'file_path':'/poster.jpg'}]}
        raise AssertionError(url)
    monkeypatch.setattr('app.asset_routes.fetch_json',fetch)
    response=c.get('/api/assets/discover?q=测试&kind=logos')
    assert response.status_code==200
    rows=response.json()['assets'];assert len(rows)==1,response.text
    assert rows[0]['kind']=='logos' and rows[0]['language']=='zh'
    assert not any('tvmaze' in url for url in calls)
    assert c.get('/api/assets/discover?q=测试&kind=invalid').status_code==422
