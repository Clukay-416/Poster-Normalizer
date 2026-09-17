import base64
import io
import math
import numpy as np
from PIL import Image
from fastapi import Depends,HTTPException
from .imaging import decode_data,get_mask,validate_repair_params


def data_url(array):
    stream=io.BytesIO();Image.fromarray(array).save(stream,'PNG')
    return 'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()


def register_ai(app,store,ai,member,admin,get_job,jobs_root):
    @app.post('/api/titles/refine-edges')
    def refine_edges(body:dict,user=Depends(member)):
        try:
            image=decode_data(body.get('image')).convert('RGBA')
            radius=body.get('radius',2)
            if type(radius) is not int or not 1<=radius<=8:raise ValueError('边缘半径为 1–8 像素')
            result=ai.cuda.run('matting','refine',np.array(image.convert('RGB')),np.array(image.getchannel('A')),{'radius':radius})
            return {'image':data_url(result['outputs'][0]),'method':result['method']}
        except ValueError as exc:raise HTTPException(422,str(exc))

    @app.post('/api/titles/rebuild')
    def rebuild(body:dict,user=Depends(member)):
        if body.get('confirm_generation') is not True:raise HTTPException(422,'请手动确认后再生成标题')
        try:
            text=body.get('text','');prompt=body.get('prompt','')
            if not isinstance(text,str) or not 1<=len(text)<=20 or '"' in text:raise ValueError('请输入 1–20 字标题，不含双引号')
            if not isinstance(prompt,str) or len(prompt)>1000:raise ValueError('风格描述过长')
            # No translator model or paid API: title text may be Chinese; style prompt should be English.
            if any('\u4e00'<=c<='\u9fff' for c in prompt):raise ValueError('风格描述请使用英文；标题文字可以使用中文')
            params={'text':text,'prompt':prompt,'seed':body.get('seed',0),'steps':30,'candidates':2}
            validate_repair_params({'seed':params['seed']})
            rgb=np.full((512,1024,3),245,dtype='uint8')
            mask=np.zeros((512,1024),dtype='uint8');mask[160:352,64:960]=255
            result=ai.cuda.run('anytext2','rebuild',rgb,mask,params)
            return {'images':[data_url(im) for im in result['outputs']],
                    'warning':result.get('warning',''),'note':'候选包含背景，请继续抠图精修；务必核对汉字笔画'}
        except ValueError as exc:raise HTTPException(422,str(exc))
    @app.get('/api/ai/runtime')
    def runtime(user=Depends(member)):
        return {'profiles':ai.cuda.inventory(),'busy':ai.cuda.process is not None,'network':'offline'}

    @app.post('/api/jobs/{jid}/segment')
    def segment(jid:str,body:dict,user=Depends(member)):
        job=get_job(jid)
        if body.get('revision')!=job['revision']:raise HTTPException(409,'任务版本已变化')
        points=body.get('points',[]);box=body.get('box')
        try:
            if not isinstance(points,list) or len(points)>64:raise ValueError('分割提示点最多 64 个')
            for point in points:
                if set(point)!={'x','y','label'} or point['label'] not in (0,1):raise ValueError('分割提示点无效')
                for k,limit in [('x',job['width']),('y',job['height'])]:
                    if type(point[k]) not in (int,float) or not math.isfinite(point[k]) or not 0<=point[k]<limit:raise ValueError('提示点超出图片')
            if box is not None:
                if not isinstance(box,list) or len(box)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in box):raise ValueError('分割选框无效')
                if not (0<=box[0]<box[2]<=job['width'] and 0<=box[1]<box[3]<=job['height']):raise ValueError('选框超出图片')
            if not points and not box:raise ValueError('请先添加正负提示点或框选区域')
            rgb=np.array(Image.open(jobs_root/jid/'normalized.png').convert('RGB'))
            result=ai.cuda.run('sam2_1_small','segment',rgb,params={'points':points,'box':box})
            return {'masks':[data_url(a) for a in result['outputs']],'scores':result['scores']}
        except ValueError as exc:raise HTTPException(422,str(exc))

    @app.post('/api/jobs/{jid}/protect-objects')
    def protect(jid:str,body:dict,user=Depends(member)):
        job=get_job(jid)
        if body.get('revision')!=job['revision']:raise HTTPException(409,'任务版本已变化')
        prompt=body.get('prompt','person. face.')
        if not isinstance(prompt,str) or len(prompt)>300:raise HTTPException(422,'检测描述过长')
        try:
            rgb=np.array(Image.open(jobs_root/jid/'normalized.png').convert('RGB'))
            result=ai.cuda.run('grounding_dino','detect',rgb,params={'prompt':prompt})
            result.pop('outputs',None)
            return result
        except ValueError as exc:raise HTTPException(422,str(exc))
