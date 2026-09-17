"""AnyText2 local editing. Returns opaque RGB candidates for deliberate alpha editing."""
import os
import shutil
import sys
from pathlib import Path
import numpy as np


def generate(request,rgb,mask):
    vendor=Path(request['vendor'])/'anytext2'
    folder=Path(request['model_dir']);work=Path(request['work']);p=request['params']
    sys.path.insert(0,str(vendor))
    # Upstream OCR dictionary lookup is relative to cwd; copy only this small text asset.
    (work/'ocr_recog').mkdir(exist_ok=True)
    shutil.copyfile(vendor/'ocr_recog/ppocr_keys_v1.txt',work/'ocr_recog/ppocr_keys_v1.txt')
    from ms_wrapper import AnyText2Model
    font=Path(request['vendor'])/'fonts/NotoSansSC.ttf'
    model=AnyText2Model(model_dir=str(folder),use_fp16=True,use_translator=False,
        font_path=str(font),cfg_path=str(vendor/'models_yaml/anytext2_sd15.yaml'),
        model_path=str(folder/'anytext_v2.0.ckpt')).cuda(0)
    text=p['text']
    if not 1<=len(text)<=20 or '"' in text:raise ValueError('标题限制为 1–20 字，不含双引号')
    results,code,warning,_=model({'img_prompt':p.get('prompt','cinematic title design'),
        'text_prompt':'"'+text+'"','seed':p.get('seed',0),'draw_pos':np.repeat(mask[:,:,None],3,axis=2),
        'ori_image':rgb},mode='edit',image_count=p.get('candidates',2),ddim_steps=p.get('steps',30),
        image_width=rgb.shape[1],image_height=rgb.shape[0],font_hint_image=[],font_hint_mask=[],
        glyline_font_path=[str(font)],text_colors=p.get('color','500,500,500'),show_debug=False)
    if code<0:raise ValueError(warning)
    return list(results),{'warning':warning,'note':'生成结果不是透明标题；请人工精修并核对文字'}
