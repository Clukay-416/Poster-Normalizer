"""Optional metadata/media retrieval. Never sends uploaded posters to a remote model."""
import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse
from .network import fetch_json, stream, ASSET_HOSTS, checked_url
from .paths import atomic_json
from .imaging import read_image


def register_assets(app,data,store,member,admin,get_job):
    root=data/'assets';root.mkdir(exist_ok=True)
    cache=root/'cache';cache.mkdir(exist_ok=True)
    lock=threading.Lock();last_request={}

    def keys():
        path=data/'provider_keys.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

    def fetch(provider,url,headers=None):
        key=hashlib.sha256((provider+url).encode()).hexdigest()
        path=cache/(key+'.json')
        if path.exists() and time.time()-path.stat().st_mtime<86400:
            return json.loads(path.read_text(encoding='utf-8'))
        with lock:
            wait=.5-(time.monotonic()-last_request.get(provider,0))
            if wait>0:time.sleep(wait)
            last_request[provider]=time.monotonic()
        result=fetch_json(url,headers,ASSET_HOSTS)
        atomic_json(path,result)
        return result

    def remember(provider,url,kind,meta):
        checked_url(url,ASSET_HOSTS)
        aid=hashlib.sha256((provider+url).encode()).hexdigest()
        path=root/(aid+'.json')
        if not path.exists():atomic_json(path,{'id':aid,'provider':provider,'url':url,'kind':kind,'metadata':meta,'created':time.time(),'official_verified':False})
        return {'id':aid,'provider':provider,'kind':kind,**meta}

    def asset(aid):
        if not re.fullmatch('[a-f0-9]{64}',aid):raise HTTPException(404)
        path=root/(aid+'.json')
        if not path.exists():raise HTTPException(404,'素材候选不存在，请先检索')
        return json.loads(path.read_text(encoding='utf-8'))

    @app.get('/api/assets/config')
    def config(user=Depends(admin)):
        k=keys()
        return {'tmdb_configured':bool(k.get('tmdb_token')),'fanart_configured':bool(k.get('fanart_key')),
                'network_enabled':store.setting('asset_network_enabled',False)}

    @app.put('/api/assets/config')
    def configure(body:dict,user=Depends(admin)):
        k=keys()
        for name in ('tmdb_token','fanart_key'):
            value=body.get(name)
            if value is not None:
                if not isinstance(value,str) or len(value)>4096:raise HTTPException(422,'凭据格式错误')
                k[name]=value.strip()
        path=data/'provider_keys.json';atomic_json(path,k)
        try:path.chmod(0o600)
        except OSError:pass
        store.set_setting('asset_network_enabled',body.get('network_enabled') is True)
        return {'ok':True}

    @app.get('/api/assets/search')
    def search(provider:str,q:str='',media_id:str='',media_type:str='movie',user=Depends(member)):
        if not store.setting('asset_network_enabled',False):raise HTTPException(422,'在线素材检索尚未启用；本地处理不受影响')
        if len(q)>150:raise HTTPException(422,'查询太长')
        k=keys()
        try:
            if provider=='tvmaze':
                if media_id:
                    if not media_id.isdecimal():raise ValueError('作品 ID 必须是数字')
                    rows=fetch(provider,f'https://api.tvmaze.com/shows/{media_id}/images')
                    result=[]
                    for row in rows:
                        url=row.get('resolutions',{}).get('original',{}).get('url')
                        if url:result.append(remember(provider,url,row.get('type','image'),{'label':row.get('type','图片'),'media_id':media_id}))
                    return {'assets':result}
                rows=fetch(provider,'https://api.tvmaze.com/search/shows?'+urlencode({'q':q}))
                return {'media':[{'id':str(r['show']['id']),'title':r['show']['name'],'year':r['show'].get('premiered'),'type':'tv'} for r in rows]}
            if provider=='tmdb':
                if not k.get('tmdb_token'):raise ValueError('请管理员设置 TMDB API Read Access Token，并核对用途授权')
                headers={'Authorization':'Bearer '+k['tmdb_token']}
                if media_id:
                    if not media_id.isdecimal() or media_type not in ('movie','tv'):raise ValueError('作品 ID/类型错误')
                    rows=fetch(provider,f'https://api.themoviedb.org/3/{media_type}/{media_id}/images',headers)
                    results=[]
                    for group in ('logos','backdrops','posters'):
                        for r in rows.get(group,[]):
                            results.append(remember(provider,'https://image.tmdb.org/t/p/original'+r['file_path'],group,
                                                    {'label':f"{group} · {r.get('iso_639_1') or '无语言'} · {r.get('width')}×{r.get('height')}",'media_id':media_id}))
                    return {'assets':results}
                rows=fetch(provider,'https://api.themoviedb.org/3/search/multi?'+urlencode({'query':q,'language':'zh-CN'}),headers)
                return {'media':[{'id':str(r['id']),'title':r.get('title') or r.get('name'),'year':r.get('release_date') or r.get('first_air_date'),'type':r['media_type']} for r in rows.get('results',[]) if r['media_type'] in ('movie','tv')]}
            if provider=='fanart':
                if not k.get('fanart_key'):raise ValueError('请管理员设置 Fanart API Key')
                if not media_id.isdecimal():raise ValueError('Fanart 电影请填 TMDB ID，电视剧请填 TVDB ID（不是 TVmaze/TMDB TV ID）')
                category='movies' if media_type=='movie' else 'tv'
                rows=fetch(provider,f'https://webservice.fanart.tv/v3/{category}/{media_id}',{'api-key':k['fanart_key']})
                results=[]
                for group,entries in rows.items():
                    if not isinstance(entries,list):continue
                    for r in entries:
                        if isinstance(r,dict) and r.get('url'):
                            results.append(remember(provider,r['url'],group,{'label':group+' · '+r.get('lang',''),'media_id':media_id}))
                return {'assets':results}
            raise ValueError('不支持的来源')
        except HTTPException:raise
        except Exception as exc:
            raise HTTPException(502,str(exc) if isinstance(exc,ValueError) else '来源请求失败；请检查凭据/网络/限流，或稍后重试。本地任务可继续。')

    @app.post('/api/assets/{aid}/download')
    def download(aid:str,user=Depends(member)):
        meta=asset(aid);path=root/(aid+'.png')
        if path.exists():return {'ok':True,'id':aid,'cached':True}
        if not store.setting('asset_network_enabled',False):raise HTTPException(422,'在线素材已关闭')
        try:
            blob=bytearray()
            with stream(meta['url'],hosts=ASSET_HOSTS) as response:
                for block in response.iter_bytes(65536):
                    blob.extend(block)
                    if len(blob)>25*1024*1024:raise ValueError('素材超过25MB')
            image,notes=read_image(blob)
            temporary=root/(aid+'.'+uuid.uuid4().hex+'.tmp')
            image.convert('RGBA').save(temporary,format='PNG');temporary.replace(path)
            meta.update(downloaded=time.time(),sha256=hashlib.sha256(blob).hexdigest(),warnings=notes)
            atomic_json(root/(aid+'.json'),meta)
            return {'ok':True,'id':aid,'cached':False}
        except Exception as exc:
            raise HTTPException(502,str(exc) if isinstance(exc,ValueError) else '素材下载失败')

    @app.get('/api/assets/{aid}/image')
    def image(aid:str,user=Depends(member)):
        asset(aid);path=root/(aid+'.png')
        if not path.exists():raise HTTPException(404,'请先下载素材')
        return FileResponse(path,filename=aid+'.png')

    @app.get('/api/assets/{aid}/provenance')
    def provenance(aid:str,user=Depends(member)):
        return asset(aid)
