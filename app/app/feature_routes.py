import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import time
import uuid
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image
from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from .paths import APP_ROOT, INSTALL_ROOT, atomic_json, child
from .store import Conflict


def register_features(app,data,store,models,ai,member,admin,get_job,public,normalize_upload,config,choose_profile):
    jobs_root=data/'jobs'

    @app.get('/api/models')
    def model_list(user=Depends(member)):
        result=models.inventory()
        if user['role']!='admin':
            result['root']='请管理员查看或修改模型目录'
        result['compute_device']=store.setting('compute_device','CPU')
        return result

    @app.put('/api/models/root')
    def root(body:dict,user=Depends(admin)):
        if store.has_busy():
            raise HTTPException(409,'请先暂停所选排队任务，并等待运行任务完成后再切换目录')
        try:
            ai.unload();models.change_root(body.get('path',''))
        except ValueError as exc:
            raise HTTPException(422,str(exc))
        return {'ok':True,'root':str(models.root),'note':'只切换目录，不移动或删除原目录'}

    @app.post('/api/models/{mid}/action')
    def model_action(mid:str,body:dict,user=Depends(admin)):
        operation=body.get('action')
        if mid not in models.catalog:
            raise HTTPException(404,'模型不存在')
        try:
            if operation in ('download','verify'):
                models.enqueue(mid,operation)
            elif operation=='pause':
                models.pause(mid)
            elif operation in ('enable','disable'):
                if operation=='enable' and body.get('license_ack') is not True:
                    raise ValueError('启用前请确认已审阅来源和许可说明')
                models.enable(mid,operation=='enable');ai.unload()
            else:
                raise ValueError('未知模型操作')
        except ValueError as exc:
            raise HTTPException(422,str(exc))
        return {'ok':True}

    @app.post('/api/models/scan')
    def scan(user=Depends(admin)):
        queued=[]
        for entry in models.inventory()['models']:
            if entry['state']['status'] in ('PRESENT','VERIFIED'):
                models.enqueue(entry['id'],'verify');queued.append(entry['id'])
        return {'queued':queued}

    @app.put('/api/compute-device')
    def compute_device(body:dict,user=Depends(admin)):
        if body.get('device') not in ('CPU','GPU_AUTO'):
            raise HTTPException(422,'请选择 CPU 或 GPU_AUTO')
        if store.has_busy():
            raise HTTPException(409,'请先暂停所选排队任务，并等待运行任务完成后切换后端')
        ai.unload();store.set_setting('compute_device',body['device'])
        return {'ok':True,'note':'GPU_AUTO 使用 Windows DirectML，可有算子回落 CPU；不等于 CUDA/PyTorch 已安装'}

    @app.get('/api/diagnostics')
    def diagnostics(user=Depends(admin)):
        import platform,sys
        try:
            import onnxruntime as ort
            providers=ort.get_available_providers()
        except ImportError:
            providers=[]
        return {'version':'0.3.1','python':sys.version,'system':platform.platform(),
                'install_root':str(INSTALL_ROOT),'app_root':str(APP_ROOT),'data_root':str(data),
                'model_root':str(models.root),'onnx_providers':providers,'free_bytes':shutil.disk_usage(data).free,
                'inference_network':'禁止：模型推理仅使用本地文件','models_separate':not models.root.is_relative_to(APP_ROOT),
                'gpu_note':'DirectML 需目标机器自检；当前环境未完成 4090/Adobe 共存验收'}

    @app.post('/api/models/{mid}/self-test')
    def self_test(mid:str,user=Depends(admin)):
        started=time.monotonic()
        try:
            image=np.full((128,128,3),128,dtype='uint8')
            if mid=='ocr_det_v5':
                result={'candidates':len(ai.detect(image))}
            elif mid=='lama_onnx':
                if store.setting('compute_device','CPU')=='GPU_AUTO' and store.setting('gpu_mode','BALANCED')=='PAUSE_AI':
                    raise ValueError('GPU AI 当前已暂停，请切换 CPU 或修改调度模式后自检')
                mask=np.zeros((128,128),dtype='uint8');mask[50:70,50:70]=255
                output=ai.inpaint(image,mask)
                result={'shape':list(output.shape),'mean':float(output.mean()),'finite':bool(np.isfinite(output).all())}
            else:
                raise ValueError('此模型尚未实现推理适配器')
        except Exception as exc:
            raise HTTPException(422,str(exc) if isinstance(exc,ValueError) else type(exc).__name__+'：推理失败，请查看环境与模型兼容性')
        return {'ok':True,'duration_ms':round((time.monotonic()-started)*1000),'result':result,
                'note':'自检只确认加载与基本推理，不能代替真实海报质量验收'}

    @app.get('/api/task-center')
    def task_center(q:str='',status:str='',project:str='',batch:str='',limit:int=100,offset:int=0,user=Depends(member)):
        result=store.page(q[:200],status,project,batch,max(1,min(limit,200)),max(0,offset))
        result['items']=[public(j) for j in result['items']]
        result['queue_paused']=store.setting('queue_paused',False)
        return result

    @app.post('/api/queue')
    def queue_control(body:dict,user=Depends(admin)):
        if type(body.get('paused')) is not bool:
            raise HTTPException(422,'paused 必须是布尔值')
        store.set_setting('queue_paused',body['paused'])
        return {'ok':True,'note':'当前原子任务运行完毕后暂停，导入和审核不受影响'}

    @app.post('/api/task-actions')
    def actions(body:dict,user=Depends(member)):
        items=body.get('items',[]);action=body.get('action')
        if not isinstance(items,list) or not 1<=len(items)<=500:
            raise HTTPException(422,'每次选择 1–500 项')
        results=[]
        for item in items:
            jid=item.get('id');revision=item.get('revision')
            try:
                job=get_job(jid)
                if job['revision']!=revision:
                    raise Conflict('版本已变化')
                if action=='retry':
                    if not job['edit']:
                        raise ValueError('没有可重试的参数')
                    store.submit(jid,revision,job['edit'])
                elif action=='approve':
                    path=jobs_root/jid/f'r{revision}'/'qc.json'
                    if not path.exists() or not json.loads(path.read_text(encoding='utf-8'))['hard_pass']:
                        raise ValueError('当前版本没有通过硬性检查')
                    store.approve(jid,revision)
                elif action in ('pause','resume','cancel'):
                    store.action(jid,revision,action)
                elif action=='label':
                    project=str(body.get('project',''))[:100];priority=body.get('priority',0)
                    if type(priority) is not int or priority not in (0,1,2,3):
                        raise ValueError('优先级不合法')
                    with store.connect() as db:
                        db.execute('UPDATE jobs SET project=?,priority=? WHERE id=? AND revision=?',(project,priority,jid,revision))
                else:
                    raise ValueError('未知任务操作')
                results.append({'id':jid,'ok':True})
            except (ValueError,Conflict,HTTPException) as exc:
                results.append({'id':jid,'ok':False,'error':str(exc)})
        return {'items':results}

    @app.post('/api/auto-batch')
    def automatic(body:dict,user=Depends(member)):
        engine=body.get('engine','opencv')
        if engine not in ('opencv','lama'):
            raise HTTPException(422,'未知修补引擎')
        try:
            models.require('ocr_det_v5')
            if engine=='lama':models.require('lama_onnx')
        except ValueError as exc:
            raise HTTPException(422,str(exc))
        items=body.get('items',[])
        if not 1<=len(items)<=500:
            raise HTTPException(422,'每次选择 1–500 项')
        result=[]
        for item in items:
            try:
                get_job(item['id'])
                store.submit(item['id'],item['revision'],{'auto':True,'engine':engine})
                result.append({'id':item['id'],'ok':True})
            except (Conflict,HTTPException) as exc:
                result.append({'id':item['id'],'ok':False,'error':str(exc)})
        return {'items':result,'note':'自动结果仍进入人工复核，歧义样本不会强行生成'}

    @app.post('/api/jobs/{jid}/detect')
    def detect(jid:str,user=Depends(member)):
        get_job(jid)
        try:
            return {'candidates':ai.detect(np.asarray(Image.open(jobs_root/jid/'normalized.png').convert('RGB'))),
                    'note':'候选按文字面积和置信度排序，不等同于片名识别'}
        except ValueError as exc:
            raise HTTPException(422,str(exc))

    def ingest_one(blob,name,batch,profile,user):
        jid=str(uuid.uuid4());folder=jobs_root/jid
        name=name.replace('\\','/').split('/')[-1][:200]
        try:
            if len(blob)>25*1024*1024:
                raise ValueError('单图超过 25MB')
            size,notes=normalize_upload(blob,folder,name)
            profile_key, resolved, orientation = choose_profile(store.setting('profiles',config['profiles']), profile, size)
            store.add({'id':jid,'batch_id':batch,'owner':user['name'],'name':name,'status':'MANUAL',
                       'width':size[0],'height':size[1],'source_hash':hashlib.sha256(blob).hexdigest(),'profile':resolved})
            return {'id':jid,'name':name,'ok':True,'warnings':notes,'orientation':orientation,'profile_key':profile_key}
        except Exception as exc:
            if folder.exists():shutil.rmtree(folder)
            return {'name':name,'ok':False,'error':str(exc) if isinstance(exc,ValueError) else '图片读取失败'}

    def get_profile(key):
        profiles=store.setting('profiles',config['profiles'])
        if key != 'auto' and key not in profiles:raise HTTPException(422,'输出规格不存在')
        return key

    def import_zip(blob,profile,user):
        batch=str(uuid.uuid4());results=[];total=0
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                entries=[i for i in archive.infolist() if not i.is_dir()]
                if len(entries)>500:raise ValueError('压缩包最多包含 500 个文件')
                for entry in entries:
                    try:
                        name=entry.filename
                        if '\\' in name or '..' in Path(name).parts or name.startswith('/') or ':' in name:
                            raise ValueError('跳过不安全的压缩路径')
                        if not name.lower().endswith(('.png','.jpg','.jpeg','.webp')):
                            continue
                        total+=entry.file_size
                        if entry.file_size>25*1024*1024 or total>350*1024*1024:
                            raise ValueError('解压体积超过限制：单图25MB，总计350MB')
                        with archive.open(entry) as f:
                            raw=f.read(25*1024*1024+1)
                        results.append(ingest_one(raw,name,batch,profile,user))
                    except Exception as exc:
                        results.append({'name':entry.filename,'ok':False,'error':str(exc) if isinstance(exc,ValueError) else '该文件无法解压'})
        except (zipfile.BadZipFile,ValueError) as exc:
            raise HTTPException(422,str(exc))
        return {'batch_id':batch,'items':results}

    @app.post('/api/import/zip')
    async def zip_upload(file:UploadFile=File(...),profile:str=Form(...),user=Depends(member)):
        try:
            blob=await file.read(150*1024*1024+1)
            if len(blob)>150*1024*1024:raise HTTPException(413,'ZIP 压缩包不得超过 150MB')
            return await run_in_threadpool(import_zip,blob,get_profile(profile),user)
        finally:
            await file.close()

    @app.get('/api/import/roots')
    def import_roots(user=Depends(admin)):
        return {'roots':store.setting('import_roots',[])}

    @app.put('/api/import/roots')
    def set_roots(body:dict,user=Depends(admin)):
        roots=body.get('roots',[])
        if not isinstance(roots,list) or len(roots)>20:raise HTTPException(422,'最多设置20个目录')
        result=[]
        for value in roots:
            path=Path(value).resolve()
            if not Path(value).is_absolute() or not path.is_dir() or path==Path(path.anchor):
                raise HTTPException(422,'请选择已存在的专用素材目录，不要选盘符根目录')
            if any(path.is_relative_to(p.resolve()) or p.resolve().is_relative_to(path) for p in (APP_ROOT,data,models.root)):
                raise HTTPException(422,'导入目录不能覆盖应用、数据或模型目录')
            result.append(str(path))
        store.set_setting('import_roots',result)
        return {'ok':True}

    @app.post('/api/import/server-folder')
    def server_import(body:dict,user=Depends(admin)):
        selected=Path(body.get('path','')).resolve()
        roots=[Path(p).resolve() for p in store.setting('import_roots',[])]
        if not selected.is_dir() or not any(selected.is_relative_to(root) for root in roots):
            raise HTTPException(403,'目录不在管理员授权的导入范围内')
        profile=get_profile(body.get('profile'));batch=str(uuid.uuid4());results=[];total=0
        iterator=selected.rglob('*') if body.get('recursive') else selected.iterdir()
        for path in iterator:
            if path.suffix.lower() not in ('.jpg','.jpeg','.png','.webp') or not path.is_file():continue
            if not path.resolve().is_relative_to(selected):continue
            if len(results)>=500:break
            total+=path.stat().st_size
            if path.stat().st_size>25*1024*1024 or total>350*1024*1024:
                results.append({'name':path.name,'ok':False,'error':'超过导入大小限制'});continue
            try:results.append(ingest_one(path.read_bytes(),path.name,batch,profile,user))
            except OSError:results.append({'name':path.name,'ok':False,'error':'无法读取此文件'})
        return {'batch_id':batch,'items':results}

    @app.get('/api/backup')
    def backup(user=Depends(admin)):
        # A consistent SQLite backup; original images are deliberately not hidden in a DB-only export.
        folder=data/'backups';folder.mkdir(exist_ok=True)
        path=folder/('tasks-'+str(time.time_ns())+'.sqlite3')
        with store.connect() as db:
            destination=sqlite3.connect(path)
            try:db.backup(destination)
            finally:destination.close()
        return FileResponse(path,filename=path.name)

    @app.get('/api/jobs/{jid}/events')
    def events(jid:str,user=Depends(member)):
        get_job(jid)
        with store.connect() as db:
            return [dict(r) for r in db.execute('SELECT kind,details,created FROM events WHERE job_id=? ORDER BY id DESC LIMIT 100',(jid,))]

    from .asset_routes import register_assets
    register_assets(app,data,store,member,admin,get_job,normalize_upload,config,choose_profile)
