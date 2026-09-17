import contextlib
import io

from PIL import Image

from test_workflow import client, png, inputs


def test_upload_auto_picks_portrait_or_landscape(client):
    c,_=client;portrait,_=inputs()
    r=c.post('/api/jobs/batch',data={'profile':'auto'},files=[('files',('vertical.png',png(portrait),'image/png'))])
    assert r.status_code==200
    item=r.json()['items'][0]
    assert item['orientation']=='竖版' and item['profile_key']=='profile_b'
    landscape=Image.new('RGB',(360,240),'#345')
    r=c.post('/api/jobs/batch',data={'profile':'auto'},files=[('files',('horizontal.png',png(landscape),'image/png'))])
    item=r.json()['items'][0]
    assert item['orientation']=='横版' and item['profile_key']=='profile_a'


def test_discover_two_sources_and_import_to_editor(client,monkeypatch):
    c,_=client
    assert c.put('/api/assets/config',json={'network_enabled':True,'tmdb_token':'test-token'}).status_code==200
    def response(url,headers=None,hosts=None):
        if 'tvmaze.com/search' in url:
            return [{'show':{'id':8,'name':'示例剧','premiered':'2025-01-01','image':{'original':'https://static.tvmaze.com/uploads/a.jpg','width':600,'height':900}}}]
        if 'tvmaze.com/shows/8/images' in url:
            return [{'type':'poster','resolutions':{'original':{'url':'https://static.tvmaze.com/uploads/b.jpg','width':600,'height':900}}}]
        if '/search/multi?' in url:
            return {'results':[{'id':9,'media_type':'movie','title':'示例电影','release_date':'2024-01-01','poster_path':'/p.jpg'}]}
        if '/movie/9/images' in url:
            return {'posters':[{'file_path':'/p.jpg','width':1200,'height':675,'iso_639_1':'zh'}]}
        raise AssertionError(url)
    monkeypatch.setattr('app.asset_routes.fetch_json',response)
    result=c.get('/api/assets/discover?q=示例').json()
    assert len(result['assets'])==3,result
    assert {s['provider'] for s in result['sources']}=={'TVmaze','TMDB'}
    portrait=next(x for x in result['assets'] if x['provider']=='tvmaze')
    image=Image.new('RGB',(240,360),'#385')
    @contextlib.contextmanager
    def stream(url,headers=None,hosts=None):
        class Response:
            def iter_bytes(self,size):yield png(image)
        yield Response()
    monkeypatch.setattr('app.asset_routes.stream',stream)
    imported=c.post(f"/api/assets/{portrait['id']}/import",json={'profile':'auto'})
    assert imported.status_code==200,imported.text
    body=imported.json();assert body['orientation']=='竖版' and body['profile_key']=='profile_b'
    job=c.get('/api/jobs/'+body['job_id']).json()
    assert job['name'].endswith('.png') and job['profile']['width']==412
