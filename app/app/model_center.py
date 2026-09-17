"""Movable model depot, pinned checksums, resumable downloads and honest readiness."""
import fnmatch
import hashlib
import json
import os
import queue
import re
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import quote

from .network import fetch_json, stream
from .paths import APP_ROOT, INSTALL_ROOT, atomic_json, child


class Paused(Exception):
    pass


def checksum(path, git=False, cancel=None):
    h = hashlib.sha1() if git else hashlib.sha256()
    if git:
        h.update(f'blob {path.stat().st_size}\0'.encode())
    with path.open('rb') as f:
        while block := f.read(1024*1024):
            if cancel and cancel():
                raise Paused()
            h.update(block)
    return h.hexdigest()


class ModelCenter:
    def __init__(self, data, store, catalog=None, root=None):
        self.data, self.store = Path(data), store
        entries = catalog or json.loads((APP_ROOT/'config/model_catalog.json').read_text(encoding='utf-8'))
        self.catalog = {m['id']: m for m in (entries['models'] if isinstance(entries,dict) else entries)}
        self.lock = threading.RLock()
        self.work = queue.Queue()
        self.stop = threading.Event()
        self.cancels, self.verified = {}, {}
        self.root = Path(root or store.setting('model_root', str(INSTALL_ROOT/'models'))).resolve()
        self._validate_root(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.root/'depot.json').exists():
            atomic_json(self.root/'depot.json', {'format':'poster-model-depot','schema':1})
        self.states = {}
        path = self.data/'model_downloads.json'
        if path.exists():
            self.states = json.loads(path.read_text(encoding='utf-8'))
        for state in self.states.values():
            if state.get('status') in ('QUEUED','DOWNLOADING','VERIFYING','RESOLVING'):
                state.update(status='PAUSED',error='上次服务中断，可继续下载或重新校验')
        self.thread = None

    def _validate_root(self, root):
        root = root.resolve()
        protected = [APP_ROOT.resolve(), self.data.resolve(), (INSTALL_ROOT/'runtime').resolve(), (INSTALL_ROOT/'runtime-ai').resolve()]
        if root == Path(root.anchor) or root == INSTALL_ROOT or any(root.is_relative_to(p) or p.is_relative_to(root) for p in protected):
            raise ValueError('模型目录必须独立于应用、任务数据和运行环境，不能选择盘符根目录')
        if os.name == 'nt' and any(p.lower() in ('windows','program files','program files (x86)') for p in root.parts):
            raise ValueError('请勿将模型写入系统目录')

    def start(self):
        self.thread = threading.Thread(target=self._loop, daemon=True, name='model-download')
        self.thread.start()

    def close(self):
        self.stop.set()
        for event in self.cancels.values():
            event.set()
        if self.thread:
            self.thread.join(timeout=3)

    def _state(self, mid, **fields):
        with self.lock:
            self.states.setdefault(mid, {}).update(fields, updated=time.time())
            atomic_json(self.data/'model_downloads.json', self.states)

    def change_root(self, value):
        root = Path(value)
        if not root.is_absolute():
            raise ValueError('请输入服务器上的绝对目录，例如 D:\\PosterModels')
        root = root.resolve()
        self._validate_root(root)
        with self.lock:
            if self.busy():
                raise ValueError('请先暂停下载/校验，等待当前任务退出后再更换目录')
            if root.exists() and any(root.iterdir()) and not (root/'depot.json').is_file():
                raise ValueError('所选非空目录不是本程序模型仓库，请选择空目录或复制来的 models 目录')
            root.mkdir(parents=True, exist_ok=True)
            if not (root/'depot.json').exists():
                atomic_json(root/'depot.json', {'format':'poster-model-depot','schema':1})
            self.store.set_setting('model_root', str(root))
            self.root = root
            self.states, self.verified = {}, {}
            atomic_json(self.data/'model_downloads.json', {})

    def busy(self):
        return any(s.get('status') in ('QUEUED','RESOLVING','DOWNLOADING','VERIFYING') for s in self.states.values())

    def plan(self, mid, resolve=False):
        model = self.catalog[mid]
        locked=APP_ROOT/'config/model_locks'/f'{mid}.json'
        if locked.is_file():
            return self._check_plan(json.loads(locked.read_text(encoding='utf-8')))
        if model.get('files'):
            return {'model_id':mid,'source':model['source_page'],'files':model['files']}
        cached = self.root/mid/'download_plan.json'
        if cached.exists():
            return self._check_plan(json.loads(cached.read_text(encoding='utf-8')))
        if not resolve:
            return {'model_id':mid,'files':[]}
        resolver = model.get('resolver')
        if not resolver:
            raise ValueError('此模型的兼容资产组合尚未锁定，暂不可下载')
        repo = resolver['repo']
        if resolver['type'] == 'hf':
            meta = fetch_json(f'https://huggingface.co/api/models/{repo}')
            revision = meta['sha']
            if not re.fullmatch('[a-f0-9]{40}', revision):
                raise ValueError('上游未返回固定版本')
            entries = fetch_json(f'https://huggingface.co/api/models/{repo}/tree/{revision}?recursive=true&limit=1000')
            files = []
            for item in entries:
                name = item['path']
                if item['type'] != 'file' or not any(fnmatch.fnmatch(name,p) for p in resolver['patterns']):
                    continue
                files.append({'path':name,'size':item['size'],
                              'url':f'https://huggingface.co/{repo}/resolve/{revision}/{quote(name,safe="/")}',
                              **({'sha256':item['lfs']['oid']} if item.get('lfs') else {'git_sha1':item['oid']})})
        else:
            listing = fetch_json(f'https://www.modelscope.cn/api/v1/models/{repo}/repo/files?Revision=master&Recursive=true')
            data = listing.get('Data') or {}
            if not listing.get('Success', False) or not isinstance(data.get('Files'), list):
                raise ValueError('ModelScope 文件列表格式发生变化，请勿继续下载')
            revision, files = 'master', []
            for item in data['Files']:
                if item.get('Type') != 'blob':
                    continue
                name = item['Path']
                if resolver.get('patterns') and not any(fnmatch.fnmatch(name,p) for p in resolver['patterns']):
                    continue
                # Inference assets only, never remote Python/pickle code imports.
                if not name.lower().endswith(('.ckpt','.safetensors','.pth','.pt','.bin','.json','.txt','.yaml','.yml','.onnx','.ttf','.md')):
                    continue
                digest = item.get('Sha256') or item.get('SHA256')
                if not digest:
                    raise ValueError('上游未给出文件 SHA256，暂停以避免未校验的模型下载')
                files.append({'path':name,'size':item.get('Size',0),'sha256':digest,
                              'url':f'https://www.modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={quote(name,safe="")}'})
        if not files:
            raise ValueError('没有解析出模型资产，请检查上游仓库是否变化')
        plan = self._check_plan({'model_id':mid,'repo':repo,'revision':revision,'files':files})
        atomic_json(cached,plan)
        return plan

    def _check_plan(self, plan):
        if len(plan.get('files',[])) > 1000 or not plan.get('files'):
            raise ValueError('模型清单文件数量不合法')
        total = 0
        for f in plan['files']:
            child(self.root, f['path'])
            if not (re.fullmatch('[a-f0-9]{64}',f.get('sha256','')) or re.fullmatch('[a-f0-9]{40}',f.get('git_sha1',''))):
                raise ValueError('清单缺少可信内容校验值')
            total += f.get('size',0)
        if total > 40*1024**3:
            raise ValueError('单模型资产超过 40GB，请先检查下载清单')
        return plan

    def verify_file(self, folder, f, cancel=None):
        path = child(folder, f['path'])
        if not path.is_file() or (f.get('size') and path.stat().st_size != f['size']):
            return False
        git = not bool(f.get('sha256'))
        digest = f.get('sha256') or f['git_sha1']
        signature = (str(path),path.stat().st_size,path.stat().st_mtime_ns,digest)
        if self.verified.get(str(path)) == signature:
            return True
        if checksum(path,git,cancel) != digest:
            return False
        self.verified[str(path)] = signature
        return True

    def enqueue(self, mid, operation='download'):
        if mid not in self.catalog:
            raise ValueError('模型不存在')
        with self.lock:
            if self.states.get(mid,{}).get('status') in ('QUEUED','RESOLVING','DOWNLOADING','VERIFYING'):
                raise ValueError('此模型已有任务在运行')
            self.cancels[mid] = threading.Event()
            self._state(mid,status='QUEUED',operation=operation,error=None)
            self.work.put((mid,operation))

    def pause(self, mid):
        with self.lock:
            if mid in self.cancels:
                self.cancels[mid].set()

    def _loop(self):
        while not self.stop.is_set():
            try:
                mid, operation = self.work.get(timeout=.3)
            except queue.Empty:
                continue
            try:
                self.run(mid,operation)
            except Paused:
                self._state(mid,status='PAUSED',error='已暂停，分片保留；可继续')
            except Exception as exc:
                # No signed URLs, credentials or local paths in browser errors.
                message = str(exc) if isinstance(exc,ValueError) else type(exc).__name__+'：网络或文件操作失败，请检查连接后重试'
                self._state(mid,status='FAILED',error=message)
            finally:
                self.work.task_done()

    def run(self, mid, operation):
        cancel = lambda: self.stop.is_set() or self.cancels[mid].is_set()
        if cancel():
            raise Paused()
        self._state(mid,status='RESOLVING' if operation=='download' else 'VERIFYING',error=None)
        plan = self.plan(mid, resolve=operation=='download')
        if not plan['files']:
            raise ValueError('此目录缺少下载清单；请先下载或复制完整模型子目录')
        folder = self.root/mid
        folder.mkdir(exist_ok=True)
        atomic_json(folder/'download_plan.json',plan)
        total = sum(f.get('size',0) for f in plan['files'])
        if operation == 'download' and total > shutil.disk_usage(self.root).free + sum(p.stat().st_size for p in folder.rglob('*') if p.is_file()):
            raise ValueError('模型目录剩余空间不足')
        for number,f in enumerate(plan['files']):
            if cancel():
                raise Paused()
            self._state(mid,status='VERIFYING',file=f['path'],file_index=number+1,file_count=len(plan['files']),total_bytes=total)
            if self.verify_file(folder,f,cancel):
                continue
            if operation == 'verify':
                raise ValueError('文件缺失或校验失败：'+f['path'])
            self._download(mid,folder,f,cancel)
            self._state(mid,status='VERIFYING')
            if not self.verify_file(folder,f,cancel):
                raise ValueError('下载文件哈希不符：'+f['path']+'；不会启用，请重新下载')
        atomic_json(folder/'verified_receipt.json',{'model':mid,'verified_at':time.time(),'files':plan['files']})
        self._state(mid,status='VERIFIED',downloaded_bytes=total,error=None)

    def _download(self, mid, folder, f, cancel):
        path = child(folder,f['path']);path.parent.mkdir(parents=True,exist_ok=True)
        part = path.with_name(path.name+'.part')
        start = part.stat().st_size if part.exists() else 0
        # A complete interrupted part can be verified without another network request.
        if f.get('size') and start == f['size']:
            if checksum(part,not bool(f.get('sha256')),cancel) == (f.get('sha256') or f['git_sha1']):
                os.replace(part,path);return
            start = 0
        headers = {'Range':f'bytes={start}-'} if start else {}
        with stream(f['url'],headers) as response:
            if start and response.status_code == 206:
                if not response.headers.get('content-range','').startswith(f'bytes {start}-'):
                    raise ValueError('续传偏移与服务器响应不一致')
                mode = 'ab'
            elif response.status_code == 200:
                start,mode = 0,'wb'
            elif not start and response.status_code == 206 and response.headers.get('content-range','').startswith('bytes 0-'):
                mode = 'wb'
            else:
                raise ValueError('不支持的续传响应')
            size = f.get('size') or (int(response.headers.get('content-length','0'))+start)
            if size and size > shutil.disk_usage(self.root).free+start:
                raise ValueError('模型目录剩余空间不足')
            downloaded,last = start,0
            with part.open(mode) as out:
                for block in response.iter_bytes(1024*1024):
                    if cancel():
                        raise Paused()
                    downloaded += len(block)
                    if downloaded > (size or 16*1024**3):
                        raise ValueError('下载大小超过预期')
                    out.write(block)
                    if time.monotonic()-last > .5:
                        self._state(mid,status='DOWNLOADING',downloaded_bytes=downloaded,file_bytes=size,file=f['path'])
                        last=time.monotonic()
                out.flush();os.fsync(out.fileno())
            if size and downloaded != size:
                raise ValueError('下载长度不足，分片已保留')
        if checksum(part,not bool(f.get('sha256')),cancel) != (f.get('sha256') or f['git_sha1']):
            # Retain the corrupt file for diagnosis but never resume it as valid bytes.
            os.replace(part,part.with_name(part.name+'.bad-'+str(time.time_ns())))
            raise ValueError('来源内容校验不符，下载未发布')
        os.replace(part,path)

    def require(self, mid):
        if not self.catalog[mid].get('adapter'):
            raise ValueError('此模型尚无推理适配器；下载仅用于储备')
        if mid not in self.store.setting('enabled_models',[]):
            raise ValueError('请先在模型中心启用此模型')
        files = self.plan(mid)['files']
        if not files or not all(self.verify_file(self.root/mid,f) for f in files):
            raise ValueError('模型缺失或未通过内容校验，请到模型中心检查')
        return self.root/mid

    def enable(self, mid, enabled):
        if mid not in self.catalog or not self.catalog[mid].get('adapter'):
            raise ValueError('此模型尚无可用推理适配器')
        current = set(self.store.setting('enabled_models',[]))
        if enabled:
            files = self.plan(mid)['files']
            if not files or not all(self.verify_file(self.root/mid,f) for f in files):
                raise ValueError('请先下载并完成校验')
            current.add(mid)
        else:
            current.discard(mid)
        self.store.set_setting('enabled_models',sorted(current))

    def inventory(self):
        enabled = self.store.setting('enabled_models',[])
        models=[]
        for mid,model in self.catalog.items():
            plan = self.plan(mid)
            present = bool(plan['files']) and all(child(self.root/mid,f['path']).is_file() for f in plan['files'])
            state = dict(self.states.get(mid,{}))
            if not state or state.get('status') == 'VERIFIED':
                state['status'] = 'PRESENT' if present else 'MISSING'
                # stat-bound cached validation is invalidated when a copied file changes.
                if present and all(self.verified.get(str(child(self.root/mid,f['path']))) ==
                    (str(child(self.root/mid,f['path'])),child(self.root/mid,f['path']).stat().st_size,
                     child(self.root/mid,f['path']).stat().st_mtime_ns,f.get('sha256') or f.get('git_sha1')) for f in plan['files']):
                    state['status']='VERIFIED'
            profile=model.get('runtime_profile')
            runtime=(INSTALL_ROOT/'runtime-ai'/profile/'python.exe').is_file() if profile else True
            tested=self.store.setting('model_test_'+mid,{})
            ready=bool(model.get('adapter')) and mid in enabled and state.get('status')=='VERIFIED' and runtime
            models.append({**model,'files':plan['files'],'state':state,'enabled':mid in enabled,
                           'runtime_installed':runtime,'self_test':tested,
                           'can_download':bool(model.get('files') or model.get('resolver')),
                           'runtime_ready':ready})
        return {'root':str(self.root),'models':models,'free_bytes':shutil.disk_usage(self.root).free}
