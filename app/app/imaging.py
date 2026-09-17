import base64
import io
import json
import time
import warnings

import cv2
import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageOps

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
    return np.asarray(image.convert('L'))


def validate_edit(edit, size):
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


def default_position(profile, logo):
    w, h, m, bottom = profile['width'], profile['height'], profile['margin'], profile['bottom']
    left = profile['layout'] == 'left'
    maxw = min(w - 2*m, w*(.38 if left else .88))
    maxh = min(h-m-bottom, h*(.42 if left else .25))
    scale = min(1, maxw/logo.width, maxh/logo.height)
    lw, lh = max(1, int(logo.width*scale)), max(1, int(logo.height*scale))
    return {'x': m if left else (w-lw)//2, 'y': max(m, min((h-lh)//2, h-bottom-lh)) if left else h-bottom-lh, 'width': lw}


def process(root, job, ai=None):
    started = time.perf_counter()
    folder = root / job['id']
    source = Image.open(folder/'normalized.png').convert('RGB')
    source_array = np.asarray(source)
    edit, profile = job['edit'], job['profile']
    validate_edit(edit, source.size)
    repair = get_mask(edit['repair_mask'], source.size)
    hard = (repair > 0).astype(np.uint8)*255
    # Only process a local ROI. Copy back only the mask, guaranteeing exact outside equality.
    ys, xs = np.nonzero(hard)
    pad = 32
    x0, x1 = max(0, xs.min()-pad), min(source.width, xs.max()+pad+1)
    y0, y1 = max(0, ys.min()-pad), min(source.height, ys.max()+pad+1)
    roi = source_array[y0:y1, x0:x1]
    engine = edit.get('engine','opencv')
    if engine == 'lama':
        if ai is None:
            raise ValueError('LaMa 适配器尚未启动')
        fixed = ai.inpaint(roi, hard[y0:y1,x0:x1])
    elif engine == 'opencv':
        fixed = cv2.inpaint(roi, hard[y0:y1, x0:x1], 3, cv2.INPAINT_TELEA)
    else:
        raise ValueError('未知修补引擎')
    base = source_array.copy()
    region = base[y0:y1, x0:x1]
    selected = hard[y0:y1, x0:x1] > 0
    region[selected] = fixed[selected]
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
    # Protect the full poster; no automatic subject crop in the manual MVP.
    canvas = Image.new('RGB', (w, h), '#17191f')
    background_source=Image.fromarray(base)
    crop=edit.get('crop')
    if crop:
        cx,cy,cw,ch=crop['x'],crop['y'],crop['width'],crop['height']
        if min(cx,cy)<0 or min(cw,ch)<1 or cx+cw>source.width or cy+ch>source.height:
            raise ValueError('裁切框超出原图')
        background_source=background_source.crop((cx,cy,cx+cw,cy+ch))
    background = ImageOps.contain(background_source, (w, h), Image.Resampling.LANCZOS)
    canvas.paste(background, ((w-background.width)//2, (h-background.height)//2))
    logo_scaled = logo.resize((lw, lh), Image.Resampling.LANCZOS)
    canvas.paste(logo_scaled, (x,y), logo_scaled.getchannel('A'))
    outside_diff = int(np.count_nonzero(base[hard == 0] != source_array[hard == 0]))
    qc = {'hard_pass': outside_diff == 0, 'output_size':[w,h], 'outside_mask_changed_channels':outside_diff,
          'margins':{'left':x,'right':w-x-lw,'top':y,'bottom':h-y-lh},
          'title_residual':None,'face_damage':None,'person_count':None,
          'requires_manual_review':True, 'engine':('LaMa ONNX '+ai.center.store.setting('compute_device','CPU')) if engine=='lama' else 'OpenCV Telea CPU',
          'warnings':['未进行残字/人物语义检测；复杂纹理、发光字和人物遮挡需人工检查'],
          'duration_ms':round((time.perf_counter()-started)*1000), 'position':position,
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
    (temp/'qc.json').write_text(json.dumps(qc, ensure_ascii=False, indent=2), encoding='utf-8')
    (temp/'edit.json').write_text(json.dumps(edit, ensure_ascii=False), encoding='utf-8')
    temp.rename(folder/f"r{job['revision']}")
    return qc
