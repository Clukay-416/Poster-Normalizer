import base64
import io
import cv2
import numpy as np
from PIL import Image


def encode(image):
    buffer=io.BytesIO();image.save(buffer,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()


def prepare(path, ai, engine):
    source=Image.open(path).convert('RGB');rgb=np.asarray(source)
    boxes=ai.detect(rgb)
    if not boxes:
        raise ValueError('未找到可信文字区域，请人工框选标题')
    first=boxes[0]
    if first['confidence']<.6 or (len(boxes)>1 and first['rank_score']-boxes[1]['rank_score']<.05):
        raise ValueError('标题候选有歧义，请人工选择；没有自动重绘')
    x0,y0,x1,y1=first['box']
    roi=rgb[y0:y1,x0:x1]
    gray=cv2.cvtColor(roi,cv2.COLOR_RGB2GRAY)
    _,binary=cv2.threshold(gray,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    if np.count_nonzero(binary)>binary.size/2:
        binary=255-binary
    fraction=np.count_nonzero(binary)/binary.size
    if not .02<fraction<.48:
        raise ValueError('标题与背景难以分离，请人工修正蒙版')
    alpha=np.zeros(rgb.shape[:2],dtype='uint8');alpha[y0:y1,x0:x1]=binary
    repair=cv2.dilate(alpha,np.ones((7,7),dtype='uint8'))
    return {'engine':engine,'extract_mask':encode(Image.fromarray(alpha)),'repair_mask':encode(Image.fromarray(repair)),
            'position':None,'logo':None,'auto_source':'OCR候选+Otsu辅助，须人工审核','candidate':first}
