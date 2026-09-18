"""Isolated, offline CUDA workers. No executable paths come from browser requests."""
import json
import os
import subprocess
import tempfile
import shutil
import threading
import time
from pathlib import Path
import numpy as np
from PIL import Image
from .paths import INSTALL_ROOT, APP_ROOT
from .gpu import snapshot

PROFILES={'sdxl_inpaint':'modern','sam2_1_small':'modern','grounding_dino':'modern',
          'powerpaint_v2':'powerpaint','anytext2':'anytext','matting':'modern'}


class CudaRuntime:
    def __init__(self,center):
        self.center=center
        self.lock=threading.Lock()
        self.process=None

    def python(self,profile):
        base=INSTALL_ROOT/'runtime-ai'/profile
        return base/('python.exe' if os.name=='nt' else 'bin/python')

    def inventory(self):
        return {p:{'installed':self.python(p).is_file(),
                   'note':'已安装，需模型自检' if self.python(p).is_file() else '请安装对应的离线 AI 运行包'}
                for p in sorted(set(PROFILES.values()))}

    def guard(self):
        mode=self.center.store.setting('gpu_mode','BALANCED')
        state=snapshot(mode)
        if mode=='PAUSE_AI':
            raise ValueError('GPU AI 已暂停，请切换调度模式后再操作')
        if not state['available']:
            raise ValueError('没有可用 NVIDIA GPU；此模型需要 CUDA，不会调用付费服务')
        if mode=='ADOBE_PRIORITY' and state['adobe']:
            raise ValueError('PR / ME 优先：剪辑软件运行期间暂不启动新的 CUDA 任务')
        if mode=='BALANCED' and (state['free_mb'] is None or state['free_mb']<10000 or (state['utilization'] or 0)>=80):
            raise ValueError('当前显存或 GPU 忙碌程度不适合启动大模型，请稍后再试')

    def run(self,mid,operation,rgb,mask=None,params=None):
        folder=self.center.root if mid=='matting' else self.center.require(mid)
        profile=PROFILES[mid]
        python=self.python(profile)
        if not python.is_file():
            raise ValueError('缺少 '+profile+' 离线 AI 运行环境，请先在模型中心检查安装')
        if not self.lock.acquire(blocking=False):
            raise ValueError('GPU 正在处理另一项请求，请等待完成后重试')
        try:
            self.guard()
            # Files are exchanged in a private temporary directory, never over a network listener.
            with tempfile.TemporaryDirectory(prefix='poster-ai-') as tmp:
                root=Path(tmp)
                Image.fromarray(rgb).save(root/'input.png')
                if mask is not None:Image.fromarray(mask).save(root/'mask.png')
                request={'model':mid,'model_dir':str(folder),'model_root':str(self.center.root),
                         'operation':operation,'params':params or {},'work':str(root),
                         'vendor':str(APP_ROOT/'ai_vendor')}
                (root/'request.json').write_text(json.dumps(request),encoding='utf-8')
                env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1',
                     'HF_HUB_DISABLE_TELEMETRY':'1','DO_NOT_TRACK':'1','PYTHONUTF8':'1',
                     'PYTHONNOUSERSITE':'1','PYTORCH_CUDA_ALLOC_CONF':'expandable_segments:True'}
                env.pop('PYTHONPATH',None)
                with (root/'worker.log').open('w',encoding='utf-8') as log:
                    self.process=subprocess.Popen([str(python),str(APP_ROOT/'ai_worker/worker.py'),str(root/'request.json')],
                        cwd=root,env=env,stdout=log,stderr=log,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                    try:self.process.wait(timeout=1800)
                    except subprocess.TimeoutExpired:
                        self.process.kill();self.process.wait()
                        raise ValueError('模型推理超过 30 分钟，已释放进程；请降低分辨率或步数')
                result_path=root/'result.json'
                logs=self.center.data/'ai_logs';logs.mkdir(exist_ok=True)
                log_id=str(time.time_ns())
                shutil.copyfile(root/'worker.log',logs/(log_id+'.log'))
                for old in sorted(logs.glob('*.log'))[:-20]:old.unlink(missing_ok=True)
                if not result_path.is_file():
                    raise ValueError('AI 运行环境启动失败；详情见 data/ai_logs/'+log_id+'.log')
                result=json.loads(result_path.read_text(encoding='utf-8'))
                if not result.get('ok'):
                    raise ValueError(result.get('error','模型推理失败')+'；日志 '+log_id+'.log')
                outputs=[]
                for name in result.get('images',[]):
                    if Path(name).name!=name:raise ValueError('AI 输出路径不合法')
                    outputs.append(np.array(Image.open(root/name)))
                result['outputs']=outputs
                return result
        finally:
            self.process=None
            self.lock.release()
