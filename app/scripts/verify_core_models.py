"""Developer QA only. Downloads to an explicit scratch directory, never release ZIP."""
import argparse
import json
import sys
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from app.model_center import ModelCenter
from app.local_ai import LocalAI
from app.store import Store

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('root');args=parser.parse_args()
    root=Path(args.root).resolve();data=root/'qa-data';data.mkdir(parents=True,exist_ok=True)
    center=ModelCenter(data,Store(data/'qa.sqlite'),root=root/'qa-models');ai=LocalAI(center)
    for mid in ('ocr_det_v5','lama_onnx'):
        print('Downloading and verifying',mid,flush=True)
        center.cancels[mid]=threading.Event();center.run(mid,'download');center.enable(mid,True)
        image=np.full((128,128,3),128,dtype='uint8')
        if mid=='ocr_det_v5':
            print(json.dumps({'model':mid,'candidates':ai.detect(image)}),flush=True)
        else:
            mask=np.zeros((128,128),dtype='uint8');mask[50:70,50:70]=255
            result=ai.inpaint(image,mask)
            print(json.dumps({'model':mid,'shape':list(result.shape),'mean':float(result.mean()),'min':int(result.min()),'max':int(result.max())}),flush=True)
