import hashlib
import io
import json
import logging
import os
import secrets
import shutil
import threading
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .gpu import MODES, snapshot
from .imaging import read_image, process, validate_edit, get_mask, decode_data
from .store import Conflict, Store
from .paths import default_data, atomic_json
from .model_center import ModelCenter
from .local_ai import LocalAI

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger('poster')


class Login(BaseModel):
    code: str = Field(max_length=200)
    name: str = Field(min_length=1, max_length=40)


class Edit(BaseModel):
    revision: int = Field(ge=0)
    repair_mask: str
    extract_mask: str | None = None
    logo: str | None = None
    position: dict | None = None
    engine: str = 'opencv'
    crop: dict | None = None


class Revision(BaseModel):
    revision: int = Field(ge=0)


def create_app(data_path=None, run_worker=True):
    data = Path(data_path or default_data())
    data.mkdir(parents=True, exist_ok=True)
    jobs_root = data/'jobs'
    jobs_root.mkdir(exist_ok=True)
    config = json.loads((ROOT/'config/default.json').read_text(encoding='utf-8'))
    store = Store(data/'poster.db')
    models = ModelCenter(data, store, root=(data.parent/(data.name+'-models')) if data_path else None)
    ai = LocalAI(models)
    secret_path = data/'access.json'
    if not secret_path.exists():
        # A local operator can distribute the separate member code.
        secret_path.write_text(json.dumps({'admin':secrets.token_urlsafe(20),'member':secrets.token_urlsafe(20)}, indent=2), encoding='utf-8')
        try:
            secret_path.chmod(0o600)
        except OSError:
            pass
    codes = json.loads(secret_path.read_text(encoding='utf-8'))
    sessions = {}
    attempts = {}
    stop = threading.Event()

    def worker():
        owner = None
        last_gpu_busy = 0
        while not stop.is_set():
            if store.setting('queue_paused',False):
                stop.wait(.3)
                continue
            mode=store.setting('gpu_mode',config['gpu_mode'])
            skip_lama=False
            if store.setting('compute_device','CPU')=='GPU_AUTO':
                state=snapshot(mode)
                skip_lama=mode=='PAUSE_AI'
                if mode=='BALANCED':
                    skip_lama=state['utilization'] is None or state['utilization']>=80 or state['free_mb'] is None or state['free_mb']<6000
                if mode=='ADOBE_PRIORITY':
                    if state['adobe'] and (state['utilization'] is None or state['utilization']>=20):
                        last_gpu_busy=time.monotonic()
                    skip_lama=time.monotonic()-last_gpu_busy<20
            job = store.claim(owner,skip_lama)
            if not job:
                stop.wait(.3)
                continue
            owner = job['owner']
            try:
                if job['edit'].get('auto'):
                    from .automation import prepare
                    job['edit']=prepare(jobs_root/job['id']/'normalized.png',ai,job['edit']['engine'])
                    with store.connect() as db:
                        db.execute("UPDATE jobs SET edit=? WHERE id=? AND revision=? AND status='RUNNING'",(json.dumps(job['edit']),job['id'],job['revision']))
                process(jobs_root, job, ai)
                store.finish(job['id'], job['revision'], 'REVIEW')
            except Exception as exc:
                log.exception('job=%s revision=%s processing failed', job['id'], job['revision'])
                message = str(exc) if isinstance(exc, ValueError) else '处理失败，请查看控制台日志后重试'
                store.finish(job['id'], job['revision'], 'FAILED', message)
            finally:
                # Free sessions at job boundaries in Adobe-priority/pause modes; no CUDA is interrupted.
                if store.setting('gpu_mode',config['gpu_mode']) in ('ADOBE_PRIORITY','PAUSE_AI'):
                    ai.unload()

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        models.start()
        thread = None
        if run_worker:
            thread = threading.Thread(target=worker, name='poster-cpu-worker', daemon=True)
            thread.start()
        yield
        stop.set()
        models.close()
        if thread:
            thread.join(timeout=10)

    app = FastAPI(title='海报规范化工作台', version='0.3.2', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.codes = codes
    app.state.jobs_root = jobs_root
    app.state.models = models
    app.state.ai = ai

    @app.middleware('http')
    async def boundary(request, call_next):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if origin and urlparse(origin).netloc != request.headers.get('host'):
                return JSONResponse({'detail':'不允许跨站请求'}, status_code=403)
            try:
                length = int(request.headers.get('content-length', '0'))
            except ValueError:
                return JSONResponse({'detail':'请求长度不合法'}, status_code=400)
            max_bytes = (400 if request.url.path in ('/api/jobs/batch','/api/import/zip') else 80)*1024*1024
            if length > max_bytes:
                return JSONResponse({'detail':'请求数据过大（批量上传 400MB，编辑 80MB）'}, status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def member(request: Request):
        token = request.cookies.get('poster_session')
        value = sessions.get(token)
        if not value or value['expires'] < time.time():
            if token:
                sessions.pop(token, None)
            raise HTTPException(401, '请先输入访问码登录')
        return value

    def admin(user=Depends(member)):
        if user['role'] != 'admin':
            raise HTTPException(403, '此操作需要管理员访问码')
        return user

    def get_job(jid):
        job = store.get(jid)
        if not job:
            raise HTTPException(404, '任务不存在')
        return job

    def public(job):
        # Masks can be large; list responses must remain small.
        value = {k:v for k,v in job.items() if k != 'edit'}
        if job['revision']:
            path = jobs_root/job['id']/f"r{job['revision']}"/'qc.json'
            if path.exists():
                value['qc'] = json.loads(path.read_text(encoding='utf-8'))
        return value

    @app.exception_handler(Conflict)
    async def conflict_handler(request, exc):
        return JSONResponse({'detail':str(exc)}, status_code=409)

    @app.get('/api/health')
    def health():
        return {'application':'poster-normalizer','version':'0.3.2','ready':True}

    @app.post('/api/login')
    def login(body: Login, request: Request, response: Response):
        key = request.client.host
        recent = [t for t in attempts.get(key, []) if t > time.time()-60]
        attempts[key] = recent
        if len(recent) >= 10:
            raise HTTPException(429, '尝试次数过多，请稍后再试')
        role = next((role for role, code in codes.items() if secrets.compare_digest(code.encode(), body.code.encode())), None)
        if not role:
            attempts[key].append(time.time())
            raise HTTPException(401, '访问码不正确')
        token = secrets.token_urlsafe(32)
        sessions[token] = {'name':body.name, 'role':role, 'expires':time.time()+12*3600}
        response.set_cookie('poster_session', token, httponly=True, samesite='strict', max_age=12*3600)
        return {'name':body.name,'role':role}

    @app.post('/api/logout')
    def logout(request: Request, response: Response):
        sessions.pop(request.cookies.get('poster_session'), None)
        response.delete_cookie('poster_session')
        return {'ok':True}

    @app.get('/api/me')
    def me(user=Depends(member)):
        return {'name':user['name'],'role':user['role']}

    @app.get('/api/settings')
    def settings(user=Depends(member)):
        return {'profiles':store.setting('profiles', config['profiles']),
                'gpu_mode':store.setting('gpu_mode', config['gpu_mode'])}

    @app.put('/api/settings/profiles')
    def profiles(body: dict, user=Depends(admin)):
        if not 1 <= len(body) <= 10:
            raise HTTPException(422, '规格数量必须为 1–10')
        for key, p in body.items():
            try:
                if not key.isidentifier() or not isinstance(p['name'], str) or len(p['name']) > 80:
                    raise ValueError()
                if any(type(p[f]) is not int for f in ('width','height','margin','bottom')):
                    raise ValueError()
                if not (128 <= p['width'] <= 4096 and 128 <= p['height'] <= 4096 and 10 <= p['margin'] <= 200 and 50 <= p['bottom'] <= 400):
                    raise ValueError()
                if p['width'] <= 2*p['margin'] or p['height'] <= p['bottom']+p['margin'] or p['layout'] not in ('left','bottom'):
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                raise HTTPException(422, '规格字段或安全边距不合法')
        store.set_setting('profiles', body)
        return {'ok':True}

    @app.get('/api/system')
    def system(user=Depends(member)):
        result=snapshot(store.setting('gpu_mode', config['gpu_mode']))
        result['inference_backend']=store.setting('compute_device','CPU')+' · OpenCV / enabled ONNX models'
        return result

    @app.post('/api/system/gpu-mode')
    def gpu_mode(body: dict, user=Depends(admin)):
        if body.get('mode') not in MODES:
            raise HTTPException(422, '未知模式')
        store.set_setting('gpu_mode', body['mode'])
        if body['mode'] in ('ADOBE_PRIORITY','PAUSE_AI'):
            # Acquisition waits for a currently running atomic ONNX operation to finish.
            ai.unload()
        return {'ok':True,'note':'GPU 任务在安全边界应用调度；暂停 GPU 不暂停 CPU。可在任务中心暂停全部队列。'}

    def normalize_upload(blob, folder, name):
        image, notes = read_image(blob)
        folder.mkdir()
        (folder/'original.bin').write_bytes(blob)
        normalized = image.convert('RGBA')
        from PIL import Image
        background = Image.new('RGBA', normalized.size, '#17191f')
        background.alpha_composite(normalized)
        background.convert('RGB').save(folder/'normalized.png')
        (folder/'ingest.json').write_text(json.dumps({'warnings':notes,'source_name':name},ensure_ascii=False),encoding='utf-8')
        return image.size, notes

    def choose_profile(profiles, key, size):
        """Resolve an explicit profile or select the closest landscape/portrait standard."""
        if key != 'auto':
            if key not in profiles:
                raise ValueError('输出规格不存在')
            return key, profiles[key], '横版' if profiles[key]['width'] > profiles[key]['height'] else '竖版'
        source_ratio = size[0] / size[1]
        landscape = size[0] > size[1]
        candidates = [(name, profile) for name, profile in profiles.items()
                      if (profile['width'] > profile['height']) == landscape]
        if not candidates:
            candidates = list(profiles.items())
        name, profile = min(candidates, key=lambda item: abs(item[1]['width'] / item[1]['height'] - source_ratio))
        return name, profile, '横版' if landscape else '竖版'

    @app.get('/api/jobs')
    def list_jobs(limit: int=200, offset: int=0, user=Depends(member)):
        return [public(j) for j in store.list(max(1,min(limit,500)),max(0,offset))]

    @app.post('/api/jobs/batch')
    async def upload(files: list[UploadFile]=File(...), profile: str=Form(...), user=Depends(member)):
        ps = store.setting('profiles', config['profiles'])
        if profile != 'auto' and profile not in ps:
            raise HTTPException(422, '输出规格不存在')
        if not 1 <= len(files) <= 100:
            raise HTTPException(422, '每批请上传 1–100 张')
        batch, result, total = str(uuid.uuid4()), [], 0
        for f in files:
            jid = str(uuid.uuid4())
            name = (f.filename or 'untitled').replace('\\','/').split('/')[-1][:200]
            folder = jobs_root/jid
            try:
                blob = await f.read(25*1024*1024+1)
                total += len(blob)
                if len(blob) > 25*1024*1024 or total > 350*1024*1024:
                    raise ValueError('单图限制 25 MB，单批总计限制 350 MB')
                size, notes = await run_in_threadpool(normalize_upload, blob, folder, name)
                profile_key, resolved, orientation = choose_profile(ps, profile, size)
                store.add({'id':jid,'batch_id':batch,'owner':user['name'],'name':name,'status':'MANUAL',
                           'width':size[0],'height':size[1],'source_hash':hashlib.sha256(blob).hexdigest(),'profile':resolved})
                result.append({'id':jid,'name':name,'ok':True,'warnings':notes,'orientation':orientation,'profile_key':profile_key})
            except Exception as exc:
                if folder.exists():
                    shutil.rmtree(folder)
                reason = str(exc) if isinstance(exc, ValueError) else '不是有效图片或图片解码失败'
                result.append({'name':name,'ok':False,'error':reason})
            finally:
                await f.close()
        return {'batch_id':batch,'items':result}

    @app.get('/api/jobs/{jid}')
    def detail(jid: str, user=Depends(member)):
        return public(get_job(jid))

    @app.get('/api/jobs/{jid}/edit')
    def edit_state(jid: str, user=Depends(member)):
        job = get_job(jid)
        path = jobs_root/jid/'draft.json'
        if path.is_file():
            draft = json.loads(path.read_text(encoding='utf-8'))
            if draft['revision'] == job['revision']:
                return draft['edit']
        return job['edit'] or {}

    @app.post('/api/jobs/{jid}/draft')
    def save_draft(jid: str, body: Edit, user=Depends(member)):
        job = get_job(jid)
        if job['revision'] != body.revision or job['status'] in ('RUNNING', 'QUEUED'):
            raise HTTPException(409, '任务已变化或正在处理，请刷新后保存草稿')
        edit = body.model_dump(exclude={'revision'})
        try:
            get_mask(edit['repair_mask'], (job['width'],job['height']))
            if edit.get('extract_mask'):
                get_mask(edit['extract_mask'], (job['width'],job['height']))
            if edit.get('logo'):
                decode_data(edit['logo'])
            if edit['engine'] not in ('opencv','lama'):
                raise ValueError('修补引擎不合法')
        except Exception as exc:
            raise HTTPException(422, str(exc) if isinstance(exc,ValueError) else '草稿图片无法读取')
        atomic_json(jobs_root/jid/'draft.json', {'revision': body.revision, 'edit': edit})
        return {'ok': True}

    @app.post('/api/jobs/{jid}/submit')
    def submit(jid: str, body: Edit, user=Depends(member)):
        job = get_job(jid)
        edit = body.model_dump(exclude={'revision'})
        try:
            if edit['engine'] not in ('opencv','lama'):
                raise ValueError('修补引擎不合法')
            if edit['engine']=='lama':
                models.require('lama_onnx')
            crop=edit.get('crop')
            if crop and (set(crop)!={'x','y','width','height'} or any(type(v) is not int for v in crop.values())):
                raise ValueError('裁切参数不合法')
            validate_edit(edit, (job['width'],job['height']))
            pos = edit.get('position')
            if pos and (set(pos) != {'x','y','width'} or any(type(v) is not int or v<0 or v>8192 for v in pos.values())):
                raise ValueError('位置必须是合法整数像素')
        except Exception as exc:
            raise HTTPException(422, str(exc) if isinstance(exc,ValueError) else '编辑数据无法读取')
        store.submit(jid, body.revision, edit)
        return public(get_job(jid))

    @app.post('/api/jobs/{jid}/retry')
    def retry(jid: str, body: Revision, user=Depends(member)):
        job = get_job(jid)
        if not job['edit']:
            raise HTTPException(422, '先完成标题与蒙版编辑')
        store.submit(jid, body.revision, job['edit'])
        return public(get_job(jid))

    @app.post('/api/jobs/{jid}/approve')
    def approve(jid: str, body: Revision, user=Depends(member)):
        job = get_job(jid)
        path = jobs_root/jid/f'r{body.revision}'/'qc.json'
        if not path.exists() or not json.loads(path.read_text(encoding='utf-8'))['hard_pass']:
            raise HTTPException(422, '当前版本未通过几何/像素检查')
        store.approve(jid, body.revision)
        return public(get_job(jid))

    @app.get('/api/jobs/{jid}/artifact/{name}')
    def artifact(jid: str, name: str, revision: int | None=None, user=Depends(member)):
        job = get_job(jid)
        if name == 'normalized.png':
            path = jobs_root/jid/name
        elif name in ('final.png','base.png','logo.png','repair_mask.png','extract_mask.png','qc.json'):
            rev = job['revision'] if revision is None else revision
            if rev < 1 or rev > job['revision']:
                raise HTTPException(404)
            path = jobs_root/jid/f'r{rev}'/name
        else:
            raise HTTPException(404)
        if not path.is_file():
            raise HTTPException(404,'该版本产物还未生成')
        return FileResponse(path)

    @app.get('/api/export')
    def export(batch_id: str | None=None, user=Depends(member)):
        # Disk spool bounds RAM for large exports; no raw local paths are exposed.
        import tempfile
        handle = tempfile.TemporaryFile()
        count = 0
        with zipfile.ZipFile(handle,'w',zipfile.ZIP_DEFLATED) as archive:
            offset = 0
            while True:
                page = store.list(500,offset)
                if not page:
                    break
                offset += len(page)
                for job in page:
                    if job['status'] != 'COMPLETED' or (batch_id and job['batch_id'] != batch_id):
                        continue
                    root = jobs_root/job['id']/f"r{job['revision']}"
                    for name in ('final.png','qc.json'):
                        archive.write(root/name, f"{job['id']}_r{job['revision']}/{name}")
                    count += 1
        if not count:
            handle.close()
            raise HTTPException(422,'没有已确认的结果，请先复核并确认')
        handle.seek(0)
        def chunks():
            try:
                while chunk := handle.read(1024*1024):
                    yield chunk
            finally:
                handle.close()
        return StreamingResponse(chunks(), media_type='application/zip', headers={'Content-Disposition':'attachment; filename="poster-results.zip"'})

    from .title_routes import register_titles
    register_titles(app, data, member, get_job)
    from .feature_routes import register_features
    register_features(app,data,store,models,ai,member,admin,get_job,public,normalize_upload,config,choose_profile)
    app.mount('/', StaticFiles(directory=ROOT/'static', html=True), name='static')
    return app
