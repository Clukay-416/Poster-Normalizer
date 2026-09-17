from pathlib import Path
import numpy as np
import torch
from PIL import Image,ImageOps


def generate(request,rgb,mask):
    folder=Path(request['model_dir']);p=request['params']
    image=Image.fromarray(rgb);alpha=Image.fromarray(mask)
    # Preserve aspect ratio; pad to a bounded model canvas and unpad after inference.
    side=p.get('resolution',1024)
    ratio=min(side/image.width,side/image.height)
    nw,nh=max(8,round(image.width*ratio/8)*8),max(8,round(image.height*ratio/8)*8)
    image=image.resize((nw,nh),Image.Resampling.LANCZOS)
    alpha=alpha.resize((nw,nh),Image.Resampling.NEAREST)
    if request['model']=='sdxl_inpaint':
        from diffusers import AutoPipelineForInpainting
        pipe=AutoPipelineForInpainting.from_pretrained(str(folder),torch_dtype=torch.float16,variant='fp16',local_files_only=True)
        pipe.enable_model_cpu_offload()
        pipe.enable_vae_tiling()
    else:
        import sys
        sys.path.insert(0,str(Path(request['vendor'])/'powerpaint'))
        from diffusers import UniPCMultistepScheduler
        from transformers import CLIPTextModel
        from safetensors.torch import load_model
        from powerpaint.models.BrushNet_CA import BrushNetModel
        from powerpaint.models.unet_2d_condition import UNet2DConditionModel
        from powerpaint.pipelines.pipeline_PowerPaint_Brushnet_CA import StableDiffusionPowerPaintBrushNetPipeline
        from powerpaint.utils.utils import TokenizerWrapper,add_tokens
        base=folder/'realisticVisionV60B1_v51VAE'
        # Architecture and initial weights are supplied by the local base, then replaced by trained task weights.
        unet=UNet2DConditionModel.from_pretrained(str(base),subfolder='unet',torch_dtype=torch.float16,local_files_only=True)
        brushnet=BrushNetModel.from_unet(unet)
        text=CLIPTextModel.from_pretrained(str(base),subfolder='text_encoder',torch_dtype=torch.float16,local_files_only=True)
        pipe=StableDiffusionPowerPaintBrushNetPipeline.from_pretrained(str(base),unet=unet,brushnet=brushnet,
            text_encoder_brushnet=text,torch_dtype=torch.float16,local_files_only=True,low_cpu_mem_usage=False,safety_checker=None)
        pipe.tokenizer=TokenizerWrapper(from_pretrained=str(base),subfolder='tokenizer',local_files_only=True)
        add_tokens(tokenizer=pipe.tokenizer,text_encoder=pipe.text_encoder_brushnet,
            placeholder_tokens=['P_ctxt','P_shape','P_obj'],initialize_tokens=['a','a','a'],num_vectors_per_token=10)
        load_model(pipe.brushnet,str(folder/'PowerPaint_Brushnet/diffusion_pytorch_model.safetensors'))
        pipe.text_encoder_brushnet.load_state_dict(torch.load(folder/'PowerPaint_Brushnet/pytorch_model.bin',map_location='cpu',weights_only=True),strict=True)
        pipe.scheduler=UniPCMultistepScheduler.from_config(pipe.scheduler.config)
        pipe.enable_model_cpu_offload()
    results=[]
    for i in range(p.get('candidates',2)):
        generator=torch.Generator('cuda').manual_seed((p.get('seed',0)+i)%2**32)
        common={'num_inference_steps':p.get('steps',30),'guidance_scale':p.get('guidance',7.5),
                'generator':generator,'width':nw,'height':nh}
        with torch.inference_mode():
            if request['model']=='sdxl_inpaint':
                result=pipe(prompt=p.get('prompt','continuous natural background, no text'),negative_prompt=p.get('negative_prompt','text, letters, watermark, logo'),
                    image=image,mask_image=alpha,strength=p.get('strength',.99),**common).images[0]
            else:
                erased=Image.fromarray((np.array(image)*(1-np.array(alpha)[:,:,None]/255)).astype('uint8'))
                result=pipe(promptA=' P_ctxt',promptB=' P_ctxt',promptU=p.get('prompt','empty scene'),
                    negative_promptA=' P_obj',negative_promptB=' P_obj',negative_promptU=p.get('negative_prompt','text, letters, watermark'),
                    tradoff=1.,tradoff_nag=1.,image=erased,mask=alpha.convert('RGB'),brushnet_conditioning_scale=1.,**common).images[0]
        results.append(np.array(result.resize((rgb.shape[1],rgb.shape[0]),Image.Resampling.LANCZOS)))
    return results
