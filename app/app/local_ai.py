"""Local ONNX adapters. Model absence never causes an online auto-download."""
import gc
import threading
import cv2
import numpy as np


class LocalAI:
    def __init__(self, center):
        self.center = center
        self.lock = threading.RLock()
        self.sessions = {}

    def unload(self):
        with self.lock:
            self.sessions.clear()
        gc.collect()

    def session(self, mid):
        folder = self.center.require(mid)
        import onnxruntime as ort
        model_file = 'det.onnx' if mid == 'ocr_det_v5' else 'lama_fp32.onnx'
        path = folder/model_file
        gpu=mid=='lama_onnx' and self.center.store.setting('compute_device','CPU')=='GPU_AUTO'
        key = (str(path),path.stat().st_size,path.stat().st_mtime_ns,gpu)
        if key not in self.sessions:
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            options.enable_mem_pattern = False
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            providers=['CPUExecutionProvider']
            if gpu:
                if 'DmlExecutionProvider' not in ort.get_available_providers():
                    raise ValueError('此运行环境没有 DirectML GPU 后端，请切换 CPU；不会静默回退整次推理')
                providers=['DmlExecutionProvider','CPUExecutionProvider']
            self.sessions.clear()
            self.sessions[key] = ort.InferenceSession(str(path),sess_options=options,providers=providers)
        return self.sessions[key]

    def detect(self, rgb):
        with self.lock:
            session = self.session('ocr_det_v5')
            h,w = rgb.shape[:2]
            ratio = min(1.,960/max(h,w))
            nh,nw = max(32,round(h*ratio/32)*32),max(32,round(w*ratio/32)*32)
            image = cv2.resize(rgb[:,:,::-1],(nw,nh)).astype('float32')/255
            image = (image-np.array([.485,.456,.406],dtype='float32'))/np.array([.229,.224,.225],dtype='float32')
            tensor = image.transpose(2,0,1)[None]
            prob = session.run(None,{session.get_inputs()[0].name:tensor})[0].squeeze()
            if prob.ndim != 2:
                raise ValueError('检测模型输出不符合约定')
            bitmap = (prob>.3).astype('uint8')*255
            contours,_ = cv2.findContours(bitmap,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
            candidates=[]
            for contour in contours:
                x,y,cw,ch = cv2.boundingRect(contour)
                if cw*ch<15:
                    continue
                score=float(prob[y:y+ch,x:x+cw].mean())
                if score<.45:
                    continue
                # Bounding-box expansion is an assistive region, not a perfect glyph alpha.
                pad=max(2,int(min(cw,ch)*.2))
                x0,y0=max(0,x-pad),max(0,y-pad)
                x1,y1=min(prob.shape[1],x+cw+pad),min(prob.shape[0],y+ch+pad)
                box=[round(x0*w/prob.shape[1]),round(y0*h/prob.shape[0]),round(x1*w/prob.shape[1]),round(y1*h/prob.shape[0])]
                area=(box[2]-box[0])*(box[3]-box[1])/(w*h)
                candidates.append({'box':box,'confidence':round(score,3),'rank_score':round(score+min(area*10,.6),3)})
            return sorted(candidates,key=lambda c:c['rank_score'],reverse=True)[:30]

    def inpaint(self, rgb, mask):
        with self.lock:
            session = self.session('lama_onnx')
            inputs = session.get_inputs()
            names={i.name for i in inputs}
            if names != {'image','mask'}:
                raise ValueError('LaMa ONNX 输入不是预期的 image/mask，请勿混用其他导出版')
            # The selected exporter uses 512x512 tensors; validate instead of silently guessing.
            dims = next(i.shape for i in inputs if i.name=='image')
            nh,nw=(int(dims[2]),int(dims[3])) if isinstance(dims[2],int) and isinstance(dims[3],int) else (512,512)
            small=cv2.resize(rgb,(nw,nh)).astype('float32')/255
            binary=cv2.resize(mask,(nw,nh),interpolation=cv2.INTER_NEAREST)
            output=session.run(None,{'image':small.transpose(2,0,1)[None],
                                     'mask':(binary>0).astype('float32')[None,None]})[0]
            if output.ndim!=4 or output.shape[1]!=3 or not np.isfinite(output).all():
                raise ValueError('LaMa 输出格式不正确')
            result=output[0].transpose(1,2,0)
            # This adapter is specific to the selected Carve fp32 graph, not arbitrary LaMa exports.
            result=np.clip(result,0,255).astype('uint8')
            return cv2.resize(result,(rgb.shape[1],rgb.shape[0]),interpolation=cv2.INTER_CUBIC)
