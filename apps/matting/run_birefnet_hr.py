#!/usr/bin/env python
"""BiRefNet_HR-matting @2048 生成首帧 mask（严格照搬官方 handler.py 用法）"""
import sys, torch, numpy as np
from PIL import Image
from transformers import AutoModelForImageSegmentation

torch.set_float32_matmul_precision("high")

@torch.inference_mode()
def main(img_path, out_path, res=2048):
    model = AutoModelForImageSegmentation.from_pretrained(
        "BiRefNet_HR", trust_remote_code=True, local_files_only=True).cuda().eval().half()
    img = Image.open(img_path).convert("RGB")
    W, H = img.size
    x = img.resize((res, res), Image.BICUBIC)
    t = torch.from_numpy(np.array(x)).half().permute(2,0,1)/255.
    t = (t - torch.tensor([0.485,0.456,0.406],dtype=torch.half)[:,None,None]) / torch.tensor([0.229,0.224,0.225],dtype=torch.half)[:,None,None]
    pred = model(t.unsqueeze(0).cuda())[-1].sigmoid().cpu()[0].squeeze()
    m = (pred.numpy()*255).astype(np.uint8)
    Image.fromarray(m).resize((W,H), Image.BILINEAR).save(out_path)
    a = np.array(Image.open(out_path))
    print("mask", a.shape, "fg ratio", (a>128).mean().round(4))

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv)>3 else 2048)
