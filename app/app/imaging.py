import base64
import io
import json
import time
import warnings

import cv2
import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageOps
from .geometry import background_canvas, validate_geometry

Image.MAX_IMAGE_PIXELS = 24_000_000
cv2.setNumThreads(1)


def read_image(blob):
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        image = Image.open(io.BytesIO(blob))
        if image.format not in ('PNG', 'JPEG', 'WEBP'):
            raise ValueError('仅支持 JPEG、PNG、WEBP')
        if image.width * image.height > 24_000_000:
            raise ValueError('图片不得超过 2400 万像素')
        image.load()
    image = ImageOps.exif_transpose(image)
    notes = []
    icc = image.info.get('icc_profile')
    if icc:
        try:
            target = 'RGBA' if 'A' in image.getbands() else 'RGB'
            image = ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(io.BytesIO(icc)), ImageCms.createProfile('sRGB'), outputMode=target)
        except Exception:
            notes.append('ICC 转 sRGB 未成功，按普通 RGB 处理；请核对颜色')
    return image, notes


def decode_data(value):
    if not value or len(value) > 36_000_000:
        raise ValueError('编辑图片为空或超过限制')
    try:
        blob = base64.b64decode(value.split(',', 1)[-1], validate=True)
    except Exception as exc:
        raise ValueError('图片数据不是合法 base64') from exc
    return read_image(blob)[0]


def get_mask(value, size):
    image = decode_data(value)
    if image.size != size:
        raise ValueError('蒙版尺寸必须与规范化原图一致')
    # Canvas masks encode coverage in alpha. Legacy grayscale masks encode it in L.
    if 'A' in image.getbands():
        gray = np.asarray(image.convert('L'), dtype=np.uint16)
        alpha = np.asarray(image.getchannel('A'), dtype=np.uint16)
        return ((gray * alpha + 127) // 255).astype(np.uint8)
    return np.asarray(image.convert('L'))


def validate_edit(edit, size):
    validate_geometry(edit,size)
    validate_repair_params(edit.get('repair_params') or {})
    if edit.get('protection_mask'):
        get_mask(edit['protection_mask'],size)
    mask = get_mask(edit['repair_mask'], size)
    if not mask.any():
        raise ValueError('请先框选或涂画需要去除的原标题')
    if (mask > 0).mean() > .65:
        raise ValueError('修补面积超过整图 65%，请缩小区域')
    if edit.get('logo'):
        logo = decode_data(edit['logo']).convert('RGBA')
    else:
        alpha = get_mask(edit['extract_mask'], size)
        if not alpha.any():
            raise ValueError('标题蒙版为空，请调整提取阈值或上传透明 Logo')
        logo = None
    if logo and not logo.getchannel('A').getbbox():
        raise ValueError('Logo 全透明')


def validate_repair_params(p):
    limits={'candidates':(1,4,True),'seed':(0,2**32-1,True),'steps':(10,80,True),
            'resolution':(512,1536,True),'context':(32,2048,True),'dilate':(0,64,True),
            'feather':(0,32,True),'guidance':(1,20,False),'strength':(.2,1,False)}
    import math
    if set(p)-set(limits)-{'prompt','negative_prompt'}:raise ValueError('未知修补参数')
    for k,v in p.items():
        if k in ('prompt','negative_prompt'):
            if not isinstance(v,str) or len(v)>2000:raise ValueError('提示词最多 2000 字符')
        else:
            lo,hi,integer=limits[k]
            if type(v) not in ((int,) if integer else (int,float)) or not math.isfinite(v) or not lo<=v<=hi:
                raise ValueError('修补参数超出范围：'+k)


def effective_mask(edit,size):
    mask=(get_mask(edit['repair_mask'],size)>0).astype('uint8')*255
    p=edit.get('repair_params') or {}
    r=p.get('dilate',0)
    if r:mask=cv2.dilate(mask,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*r+1,2*r+1)))
    if edit.get('protection_mask'):mask[get_mask(edit['protection_mask'],size)>0]=0
    if not mask.any():raise ValueError('保护区域覆盖了全部修补选区')
    if (mask>0).mean()>.65:raise ValueError('膨胀后的修补区域超过整图 65%')
    return mask


def default_position(profile, logo):
    w, h, m, bottom = profile['width'], profile['height'], profile['margin'], profile['bottom']
    left = profile['layout'] == 'left'
    maxw = min(w - 2*m, w*(.38 if left else .88))
    maxh = min(h-m-bottom, h*(.42 if left else .25))
    scale = min(1, maxw/logo.width, maxh/logo.height)
    lw, lh = max(1, int(logo.width*scale)), max(1, int(logo.height*scale))
    return {'x': m if left else (w-lw)//2, 'y': max(m, min((h-lh)//2, h-bottom-lh)) if left else h-bottom-lh, 'width': lw}


def process(root, job, ai=None):
    if job['edit'].get('operation')=='compose':return process_composition(root,job)
    started = time.perf_counter()
    folder = root / job['id']
    source = Image.open(folder/'normalized.png').convert('RGB')
    source_array = np.asarray(source)
    edit, profile = job['edit'], job['profile']
    validate_edit(edit, source.size)
    hard = effective_mask(edit,source.size)
    # Only process a local ROI. Copy back only the mask, guaranteeing exact outside equality.
    ys, xs = np.nonzero(hard)
    params=edit.get('repair_params') or {}
    pad = params.get('context',128)
    x0, x1 = max(0, xs.min()-pad), min(source.width, xs.max()+pad+1)
    y0, y1 = max(0, ys.min()-pad), min(source.height, ys.max()+pad+1)
    roi = source_array[y0:y1, x0:x1]
    engine = edit.get('engine','opencv')
    if engine == 'lama':
        if ai is None:
            raise ValueError('LaMa 适配器尚未启动')
        candidates = [ai.inpaint(roi, hard[y0:y1,x0:x1])]
    elif engine == 'opencv':
        candidates = [cv2.inpaint(roi, hard[y0:y1, x0:x1], 3, cv2.INPAINT_TELEA)]
    elif engine in ('sdxl','powerpaint'):
        if ai is None:raise ValueError('CUDA 适配器尚未启动')
        mid={'sdxl':'sdxl_inpaint','powerpaint':'powerpaint_v2'}[engine]
        candidates=ai.cuda.run(mid,'inpaint',roi,hard[y0:y1,x0:x1],params)['outputs']
    else:
        raise ValueError('未知修补引擎')
    selected = hard[y0:y1, x0:x1] > 0
    feather=params.get('feather',0)
    weight=np.minimum(1,cv2.distanceTransform(hard[y0:y1,x0:x1],cv2.DIST_L2,3)/max(feather,1)) if feather else selected.astype('float32')
    bases=[]
    for fixed in candidates:
        if fixed.shape!=roi.shape or not np.isfinite(fixed).all():raise ValueError('修补模型输出尺寸或数值不合法')
        base=source_array.copy()
        blended=np.clip(np.rint(fixed*weight[:,:,None]+roi*(1-weight[:,:,None])),0,255).astype('uint8')
        base[y0:y1,x0:x1][selected]=blended[selected]
        bases.append(base)
    if not bases:raise ValueError('模型未返回候选结果')
    base=bases[0]
    if edit.get('logo'):
        logo = decode_data(edit['logo']).convert('RGBA')
        alpha = None
    else:
        alpha = get_mask(edit['extract_mask'], source.size)
        logo = source.convert('RGBA')
        logo.putalpha(Image.fromarray(alpha))
    bbox = logo.getchannel('A').getbbox()
    if not bbox:
        raise ValueError('没有可见标题像素')
    logo = logo.crop(bbox)
    position = edit.get('position') or default_position(profile, logo)
    lw = int(position['width'])
    if lw < 1 or lw > logo.width:
        raise ValueError('标题宽度不合法或超过原 Logo 宽度（不允许默认放大）')
    lh = max(1, round(logo.height * lw/logo.width))
    x, y = int(position['x']), int(position['y'])
    w, h, m, bottom = profile['width'], profile['height'], profile['margin'], profile['bottom']
    if x < m or y < m or x+lw > w-m or y+lh > h-bottom:
        raise ValueError('标题超出安全区：底部至少 50px，其他边距以配置为准')
    canvas = background_canvas(Image.fromarray(base), (w,h), edit)
    logo_scaled = logo.resize((lw, lh), Image.Resampling.LANCZOS)
    canvas.paste(logo_scaled, (x,y), logo_scaled.getchannel('A'))
    outside_diff = int(np.count_nonzero(base[hard == 0] != source_array[hard == 0]))
    qc = {'hard_pass': outside_diff == 0, 'output_size':[w,h], 'outside_mask_changed_channels':outside_diff,
          'margins':{'left':x,'right':w-x-lw,'top':y,'bottom':h-y-lh},
          'title_residual':None,'face_damage':None,'person_count':None,
          'requires_manual_review':True, 'engine':engine,
          'candidate_count':len(bases),'selected_candidate':0,'repair_params':params,
          'warnings':['未进行残字/人物语义检测；复杂纹理、发光字和人物遮挡需人工检查'],
          'duration_ms':round((time.perf_counter()-started)*1000), 'position':position,
          'background':edit.get('background'), 'crop':edit.get('crop'),
          'source_hash':job['source_hash'],'revision':job['revision'],'profile':profile}
    temp = folder/f".r{job['revision']}.tmp"
    if temp.exists():
        # Preserve an interrupted attempt, then write this revision to a clean staging directory.
        temp.rename(folder/(temp.name+'.interrupted-'+str(time.time_ns())))
    temp.mkdir(exist_ok=False)
    Image.fromarray(hard).save(temp/'repair_mask.png')
    if alpha is not None:
        Image.fromarray(alpha).save(temp/'extract_mask.png')
    logo.save(temp/'logo.png')
    Image.fromarray(base).save(temp/'base.png')
    canvas.save(temp/'final.png')
    for i,candidate_base in enumerate(bases):
        candidate_canvas=background_canvas(Image.fromarray(candidate_base),(w,h),edit)
        candidate_canvas.paste(logo_scaled,(x,y),logo_scaled.getchannel('A'))
        candidate_canvas.save(temp/f'final-{i}.png')
        Image.fromarray(candidate_base).save(temp/f'base-{i}.png')
    (temp/'qc.json').write_text(json.dumps(qc, ensure_ascii=False, indent=2), encoding='utf-8')
    (temp/'edit.json').write_text(json.dumps(edit, ensure_ascii=False), encoding='utf-8')
    temp.rename(folder/f"r{job['revision']}")
    return qc


def process_composition(root,job):
    """Geometry/title-only revision, preserving a previously selected repaired background."""
    folder=root/job['id'];edit=job['edit'];profile=job['profile']
    rev=edit.get('base_revision',0)
    if type(rev) is not int or rev<0 or rev>=job['revision']:raise ValueError('基础版本不合法')
    original=Image.open(folder/'normalized.png').convert('RGB')
    base=Image.open(folder/f'r{rev}/base.png').convert('RGB') if rev else original
    canvas=background_canvas(base,(profile['width'],profile['height']),edit)
    logo=None;position=None
    if edit.get('logo'):
        logo=decode_data(edit['logo']).convert('RGBA')
        box=logo.getchannel('A').getbbox()
        if not box:raise ValueError('标题全透明')
        logo=logo.crop(box)
        position=edit.get('position') or default_position(profile,logo)
        w=int(position['width']);h=max(1,round(logo.height*w/logo.width));x=int(position['x']);y=int(position['y'])
        if w<1 or w>logo.width or x<profile['margin'] or y<profile['margin'] or x+w>profile['width']-profile['margin'] or y+h>profile['height']-profile['bottom']:
            raise ValueError('标题位置或大小超出安全范围')
        scaled=logo.resize((w,h),Image.Resampling.LANCZOS);canvas.paste(scaled,(x,y),scaled.getchannel('A'))
    qc={'hard_pass':True,'revision':job['revision'],'source_hash':job['source_hash'],
        'profile':profile,'output_size':list(canvas.size),'operation':'compose','base_revision':rev,
        'requires_manual_review':True,'background':edit.get('background'),'crop':edit.get('crop'),
        'position':position,'candidate_count':1,'selected_candidate':0,
        'note':'仅调整构图/标题，底图像素未重新修补'}
    temp=folder/f".r{job['revision']}.compose-{time.time_ns()}";temp.mkdir()
    base.save(temp/'base.png');canvas.save(temp/'final.png')
    if logo:logo.save(temp/'logo.png')
    (temp/'qc.json').write_text(json.dumps(qc,ensure_ascii=False),encoding='utf-8')
    (temp/'edit.json').write_text(json.dumps(edit,ensure_ascii=False),encoding='utf-8')
    temp.rename(folder/f"r{job['revision']}")
    return qc
