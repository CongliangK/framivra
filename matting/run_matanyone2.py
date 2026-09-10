#!/usr/bin/env python
"""MatAnyone2 本地推理：帧目录 + 首帧 mask → alpha 序列，再过 guided filter 收边输出
用法: run_matanyone2.py <frames_dir> <mask.png> <out_raw_dir> <out_refined_dir>
"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "MatAnyone2"))
import cv2
import numpy as np
from PIL import Image
import torch

from matanyone2.model.matanyone2 import MatAnyone2
from matanyone2.inference.inference_core import InferenceCore

import warnings
warnings.filterwarnings("ignore")

CKPT = "MatAnyone2/pretrained_models/hf2/model.safetensors"

@torch.inference_mode()
def main(frames_dir, mask_path, out_raw, out_refined, n_warmup=10, r_erode=10, r_dilate=10):
    os.makedirs(out_raw, exist_ok=True)
    os.makedirs(out_refined, exist_ok=True)
    # 官方加载路径：hydra 配置 + load_weights
    from omegaconf import open_dict
    from hydra import compose, initialize
    from safetensors.torch import load_file
    initialize(version_base='1.3.2', config_path="MatAnyone2/matanyone2/config", job_name="eval")
    cfg = compose(config_name="eval_matanyone_config")
    with open_dict(cfg):
        cfg['weights'] = CKPT
    matanyone2 = MatAnyone2(cfg, single_object=True).cuda().eval()
    matanyone2.load_weights(load_file(CKPT))
    processor = InferenceCore(matanyone2, cfg=cfg)

    files = sorted(f for f in os.listdir(frames_dir) if f.endswith(".png"))
    length = len(files)
    print(f"frames={length}")

    mask = np.array(Image.open(mask_path).convert('L'))
    mask = cv2.dilate(mask, np.ones((r_dilate, r_dilate), np.uint8), iterations=1)
    mask = cv2.erode(mask, np.ones((r_erode, r_erode), np.uint8), iterations=1)
    mask = torch.from_numpy(mask).float().cuda()  # (H, W)

    t0 = time.time()
    objects = [1]
    with torch.autocast('cuda'):
        for ti, fn in enumerate(files):
            img_np = cv2.imread(os.path.join(frames_dir, fn))[..., ::-1]  # RGB
            image = (torch.from_numpy(img_np.copy()).permute(2,0,1).float() / 255.).cuda()
            if ti == 0:
                output_prob = processor.step(image, mask, objects=objects)
                output_prob = processor.step(image, first_frame_pred=True)
            elif ti <= n_warmup:
                output_prob = processor.step(image, first_frame_pred=True)
            else:
                output_prob = processor.step(image)
            m = processor.output_prob_to_mask(output_prob)
            alpha = (m.squeeze().cpu().numpy() * 255).astype(np.uint8)
            Image.fromarray(alpha).save(os.path.join(out_raw, f"a{ti:04d}.png"))
            # guided filter：原图引导收边
            ref = cv2.ximgproc.guidedFilter(guide=img_np, src=alpha, radius=6, eps=1e-4)
            Image.fromarray(np.clip(ref,0,255).astype(np.uint8)).save(os.path.join(out_refined, f"a{ti:04d}.png"))
            if ti % 50 == 0:
                print(f"frame {ti}/{length} elapsed {time.time()-t0:.1f}s", flush=True)
    print(f"DONE total {time.time()-t0:.1f}s")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
