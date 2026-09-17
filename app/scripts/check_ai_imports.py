"""No weights / no images generated. CI validates actual upstream import compatibility."""
import json
import sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'ai_worker'))
profile=sys.argv[1]
import torch,numpy,PIL
if profile=='modern':
    sys.path.insert(0,str(root/'ai_vendor/sam2'))
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor
    from diffusers import AutoPipelineForInpainting
    from transformers import AutoProcessor,AutoModelForZeroShotObjectDetection
    from pymatting import estimate_alpha_cf,estimate_foreground_ml
elif profile=='powerpaint':
    sys.path.insert(0,str(root/'ai_vendor/powerpaint'))
    from powerpaint.models.BrushNet_CA import BrushNetModel
    from powerpaint.models.unet_2d_condition import UNet2DConditionModel
    from powerpaint.pipelines.pipeline_PowerPaint_Brushnet_CA import StableDiffusionPowerPaintBrushNetPipeline
    from powerpaint.utils.utils import TokenizerWrapper,add_tokens
elif profile=='anytext':
    sys.path.insert(0,str(root/'ai_vendor/anytext2'))
    from ms_wrapper import AnyText2Model
else:raise ValueError('Unknown profile')
print(json.dumps({'profile':profile,'torch':torch.__version__,'cuda_build':torch.version.cuda,
                  'imports_passed':True,'gpu_available':torch.cuda.is_available()}))
