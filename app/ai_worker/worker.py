"""CLI worker protocol v1. Executed by a dedicated, bundled Python environment."""
import json
import os
import socket
import sys
import traceback
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))


def offline(*args,**kwargs):
    raise RuntimeError('推理期间禁止联网；请在模型中心准备完整模型')


def main(request):
    # Defense in depth for upstream loaders, including accidental implicit downloads.
    socket.socket.connect=offline
    socket.socket.connect_ex=offline
    socket.create_connection=offline
    import torch
    import numpy as np
    from PIL import Image
    if not torch.cuda.is_available():
        raise ValueError('CUDA 不可用，请检查 NVIDIA 驱动和离线 AI 运行包')
    torch.set_grad_enabled(False)
    torch.backends.cuda.matmul.allow_tf32=True
    root=Path(request['work']);folder=Path(request['model_dir'])
    rgb=np.array(Image.open(root/'input.png').convert('RGB'))
    mask=np.array(Image.open(root/'mask.png').convert('L')) if (root/'mask.png').exists() else None
    mid=request['model'];params=request['params']
    if mid=='matting':
        import cv2
        from pymatting import estimate_alpha_cf,estimate_foreground_ml
        height,width=mask.shape;ratio=min(1,1024/max(height,width))
        small=cv2.resize(rgb,(max(1,round(width*ratio)),max(1,round(height*ratio))))/255.
        alpha=cv2.resize(mask,(small.shape[1],small.shape[0]),interpolation=cv2.INTER_NEAREST)
        r=params.get('radius',2);kernel=np.ones((2*r+1,2*r+1),dtype='uint8')
        fg=cv2.erode((alpha>220).astype('uint8'),kernel)>0
        bg=cv2.dilate((alpha>5).astype('uint8'),kernel)==0
        if not fg.any() or not bg.any():raise ValueError('标题蒙版需要明确的前景和透明背景，请先擦除背景或缩小边缘半径')
        trimap=np.full(alpha.shape,.5);trimap[fg]=1;trimap[bg]=0
        matte=estimate_alpha_cf(small.astype('float64'),trimap)
        foreground=estimate_foreground_ml(small.astype('float64'),matte)
        rgba=np.dstack([foreground,matte])
        output=np.clip(cv2.resize(rgba,(width,height))*255,0,255).astype('uint8')
        return [output],{'method':'closed-form alpha + multilevel foreground estimation'}
    if mid=='sam2_1_small':
        sys.path.insert(0,str(Path(request['vendor'])/'sam2'))
        from sam2.build_sam import build_sam2
        from sam2.sam2_image_predictor import SAM2ImagePredictor
        files=list(folder.glob('*.pt'))
        if len(files)!=1:raise ValueError('SAM 权重文件必须唯一')
        predictor=SAM2ImagePredictor(build_sam2('configs/sam2.1/sam2.1_hiera_s.yaml',str(files[0]),device='cuda',apply_postprocessing=False))
        points=params.get('points',[])
        box=params.get('box')
        with torch.inference_mode(),torch.autocast('cuda',dtype=torch.bfloat16):
            predictor.set_image(rgb)
            masks,scores,_=predictor.predict(
                point_coords=np.array([[p['x'],p['y']] for p in points]) if points else None,
                point_labels=np.array([p['label'] for p in points]) if points else None,
                box=np.array(box) if box else None,multimask_output=True)
        order=np.argsort(scores)[::-1]
        return [(masks[i]*255).astype('uint8') for i in order],{'scores':[float(scores[i]) for i in order]}
    if mid=='grounding_dino':
        from transformers import AutoProcessor,AutoModelForZeroShotObjectDetection
        processor=AutoProcessor.from_pretrained(str(folder),local_files_only=True)
        model=AutoModelForZeroShotObjectDetection.from_pretrained(str(folder),local_files_only=True).to('cuda')
        inputs=processor(images=Image.fromarray(rgb),text=params.get('prompt','person. face.'),return_tensors='pt').to('cuda')
        with torch.inference_mode():outputs=model(**inputs)
        result=processor.post_process_grounded_object_detection(outputs,inputs.input_ids,box_threshold=.3,text_threshold=.25,target_sizes=[rgb.shape[:2]])[0]
        return [],{'boxes':result['boxes'].cpu().tolist(),'scores':result['scores'].cpu().tolist(),'labels':list(result.get('text_labels',result.get('labels',[])))}
    if mid in ('sdxl_inpaint','powerpaint_v2'):
        from inpaint import generate
        return generate(request,rgb,mask),{}
    if mid=='anytext2':
        from text_generation import generate
        return generate(request,rgb,mask)
    raise ValueError('未知模型')


if __name__=='__main__':
    request=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    root=Path(request['work'])
    try:
        from PIL import Image
        outputs,metadata=main(request)
        names=[]
        for i,array in enumerate(outputs):
            name=f'output-{i}.png';Image.fromarray(array).save(root/name);names.append(name)
        result={'ok':True,'images':names,**metadata}
    except Exception as exc:
        traceback.print_exc()
        message=str(exc) if isinstance(exc,(ValueError,RuntimeError,FileNotFoundError)) else type(exc).__name__+'：模型与运行环境不兼容，请查看自检结果'
        result={'ok':False,'error':message[:1000]}
    (root/'result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
