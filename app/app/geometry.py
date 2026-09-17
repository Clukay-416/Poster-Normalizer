"""Output geometry, mirrored by static/geometry.js; all positions use output pixels."""
import math
from PIL import Image


def validate_geometry(edit, size):
    crop = edit.get('crop')
    if crop:
        if set(crop) != {'x','y','width','height'} or any(type(v) is not int for v in crop.values()):
            raise ValueError('裁切参数必须为整数像素')
        x,y,w,h = (crop[k] for k in ('x','y','width','height'))
        if min(x,y)<0 or min(w,h)<1 or x+w>size[0] or y+h>size[1]:
            raise ValueError('裁切框超出原图')
    transform = edit.get('background') or {}
    if set(transform)-{'mode','zoom','x','y'} or transform.get('mode','contain') not in ('contain','cover','free'):
        raise ValueError('背景构图模式不合法')
    for k,lo,hi,default in [('zoom',.25,8,1),('x',-32768,32768,0),('y',-32768,32768,0)]:
        v=transform.get(k,default)
        if type(v) not in (int,float) or not math.isfinite(v) or not lo<=v<=hi:
            raise ValueError('背景缩放或位移超出范围')


def placement(size, output, transform=None):
    t=transform or {}
    fit=max if t.get('mode') in ('cover','free') else min
    scale=fit(output[0]/size[0],output[1]/size[1])*t.get('zoom',1)
    w,h=[max(1,math.floor(v*scale+.5)) for v in size]
    x=math.floor((output[0]-w)/2+t.get('x',0)+.5)
    y=math.floor((output[1]-h)/2+t.get('y',0)+.5)
    return x,y,w,h


def background_canvas(image, output, edit):
    validate_geometry(edit,image.size)
    crop=edit.get('crop')
    if crop:
        x,y,w,h=(crop[k] for k in ('x','y','width','height'))
        image=image.crop((x,y,x+w,y+h))
    x,y,w,h=placement(image.size,output,edit.get('background'))
    # Output-sized affine avoids allocating an enormous intermediate at 800% zoom.
    return image.transform(output,Image.Transform.AFFINE,
        (image.width/w,0,-x*image.width/w,0,image.height/h,-y*image.height/h),
        Image.Resampling.BICUBIC,fillcolor='#17191f')
