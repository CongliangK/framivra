#!/usr/bin/env python
"""MatAnyone 本地推理：视频 + 首帧 mask → alpha 序列 PNG
基于 works/MatAnyone/inference_matanyone.py（本地权重版）
"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "MatAnyone"))
import cv2
import numpy as np
from PIL import Image
import torch, torchvision

from matanyone.model.matanyone import MatAnyone
from matanyone.inference.inference_core import InferenceCore
from matanyone.utils.inference_utils import gen_dilate, gen_erosion, read_frame_from_videos

import warnings
warnings.filterwarnings("ignore")

CKPT = "MatAnyone/pretrained_models/hf"

@torch.inference_mode()
def main(input_path, mask_path, out_dir, n_warmup=10, r_erode=10, r_dilate=10):
    os.makedirs(out_dir, exist_ok=True)
    matanyone = MatAnyone.from_pretrained(CKPT).cuda().eval()
    processor = InferenceCore(matanyone, cfg=matanyone.cfg)

    vframes, fps, length, video_name = read_frame_from_videos(input_path)
    print(f"frames={length} fps={fps} res={vframes.shape}")

    mask = Image.open(mask_path).convert('L')
    mask = np.array(mask)
    # 与官方一致：先 dilate 后 erode（闭运算填内部空洞）
    mask = cv2.dilate(mask, np.ones((r_dilate, r_dilate), np.uint8), iterations=1)
    mask = cv2.erode(mask, np.ones((r_erode, r_erode), np.uint8), iterations=1)
    mask = torch.from_numpy(mask).float().cuda()  # (H, W)

    t0 = time.time()
    objects = [1]
    with torch.autocast('cuda'):
        for ti in range(length):
            image = (vframes[ti] / 255.).float().cuda()
            if ti == 0:
                output_prob = processor.step(image, mask, objects=objects)
                output_prob = processor.step(image, first_frame_pred=True)
            elif ti <= n_warmup:
                output_prob = processor.step(image, first_frame_pred=True)
            else:
                output_prob = processor.step(image)
            m = processor.output_prob_to_mask(output_prob)
            pha = (m.squeeze().cpu().numpy() * 255).astype(np.uint8)
            # 热身帧也落盘，保持与源 260 帧一一对应
            Image.fromarray(pha).save(os.path.join(out_dir, f"a{ti:04d}.png"))
            if ti % 50 == 0:
                print(f"frame {ti}/{length} elapsed {time.time()-t0:.1f}s", flush=True)
    print(f"DONE total {time.time()-t0:.1f}s")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
