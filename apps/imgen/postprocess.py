#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""postprocess.py · ffmpeg lanczos cover-crop 后处理（方案 §5.3，离线免 API）

问题：生图 nano 2K 16:9 实出 ~2752×1536 ≠ 3840×2160，而 apps/matting/run_full_bg.sh
的 cv2.imread 直读逐像素合成、无 resize——错尺寸图进抠像链会崩或比例失真。

滤镜链（方案 §5.3 定稿）：
  ffmpeg -y -i <in.png> \\
    -vf "scale=W:H:force_original_aspect_ratio=increase:flags=lanczos,crop=W:H,format=rgb24" \\
    -frames:v 1 <out.png>

- cover 语义：等比缩放至完全覆盖 W×H（lanczos），再中心 crop 到精确 W×H，
  最后 format=rgb24 压平 alpha / 防 16-bit 与调色板 PNG（cv2.imread 要求 8-bit RGB）。
- 常态链路只降采样（--kind 联动矩阵保证源 ≥ 目标）；源 < 目标需放大时打印
  插值倍数警告后照做（离线补救场景，用户自担画质）。
- 后处理后像素级断言（ffprobe width/height/pix_fmt=rgb24），不符即删产物报错，
  绝不让错尺寸图进 assets/bg/。

依赖：仅 ffmpeg/ffprobe + stdlib（本文件不 import requests，postprocess 子命令
离线可跑）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import models

FFMPEG = os.environ.get("IMGEN_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("IMGEN_FFPROBE", "ffprobe")

PIX_FMT_REQUIRED = "rgb24"  # cv2.imread 8-bit RGB 契约


class PostprocessError(RuntimeError):
    """后处理失败（ffmpeg 失败 / 断言不符）。"""


def build_filter(width: int, height: int) -> str:
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={width}:{height},format={PIX_FMT_REQUIRED}"
    )


def build_cover_crop_args(src: Path, dst: Path, width: int, height: int) -> List[str]:
    return [
        FFMPEG, "-y", "-i", str(src),
        "-vf", build_filter(width, height),
        "-frames:v", "1", str(dst),
    ]


def probe_image(path: Path) -> dict:
    """ffprobe 读取 width/height/pix_fmt；失败抛 PostprocessError。"""
    cmd = [
        FFPROBE, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,pix_fmt",
        "-of", "json", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise PostprocessError(f"ffprobe 无法读取图片 {path}: {proc.stderr.strip()[:300]}")
    try:
        stream = json.loads(proc.stdout)["streams"][0]
        width = int(stream["width"])
        height = int(stream["height"])
        pix_fmt = str(stream.get("pix_fmt", ""))
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise PostprocessError(f"ffprobe 输出无法解析: {path}（{proc.stdout[:200]}）") from exc
    if width <= 0 or height <= 0 or not pix_fmt:
        # ffprobe 对无效文件可能 rc=0 且 width/height=0（如伪 PNG 文本），显式拦截
        raise PostprocessError(f"图片无效或非视频流: {path}（ffprobe 报 {width}×{height} {pix_fmt!r}）")
    return {"width": width, "height": height, "pix_fmt": pix_fmt}


def verify_png_size(path: Path, width: int, height: int, pix_fmt: str = PIX_FMT_REQUIRED) -> dict:
    """像素级断言；不符则删除产物并抛错（绝不让错尺寸图进 assets/bg/）。"""
    info = probe_image(path)
    if info["width"] != width or info["height"] != height or info["pix_fmt"] != pix_fmt:
        try:
            Path(path).unlink()
        except OSError:
            pass
        raise PostprocessError(
            f"后处理断言不符，已删除产物 {path}: 期望 {width}×{height} {pix_fmt}，"
            f"实得 {info['width']}×{info['height']} {info['pix_fmt']}"
        )
    return info


def kind_target(kind: str) -> Tuple[int, int]:
    target = models.KIND_TARGETS.get(kind)
    if target is None:
        raise ValueError(f"postprocess 需要 crop 目标 kind（bg4k/bg1080p/vertical），收到: {kind}")
    return target


def cover_crop(
    src: Path,
    dst: Path,
    width: Optional[int] = None,
    height: Optional[int] = None,
    *,
    kind: Optional[str] = None,
    progress=sys.stderr,
) -> Path:
    """lanczos cover-crop src → 精确 W×H rgb24 PNG 到 dst，断言后返回 dst。

    源 < 目标需放大时打印插值倍数警告后照做（离线补救场景）。
    """
    if kind is not None:
        width, height = kind_target(kind)
    if not width or not height:
        raise ValueError("cover_crop 需要 width/height 或 kind")
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        raise PostprocessError(f"输入图片不存在: {src}")

    try:
        src_info = probe_image(src)
    except PostprocessError:
        src_info = None  # 源读不出尺寸不阻断 ffmpeg（让其自报错误），只是少了放大警告
    if src_info is not None:
        factor = models.upscale_factor((src_info["width"], src_info["height"]), (width, height))
        if factor > 1.0:
            print(
                f"⚠ 源 {src_info['width']}×{src_info['height']} 小于目标 {width}×{height}，"
                f"将放大约 {factor:.2f}×（lanczos 插值，画质软化为离线补救自担；"
                "常态链路应保证源≥目标）",
                file=progress,
            )

    dst.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(build_cover_crop_args(src, dst, width, height), capture_output=True, text=True)
    if proc.returncode != 0:
        try:
            dst.unlink()
        except OSError:
            pass
        raise PostprocessError(f"ffmpeg cover-crop 失败: {proc.stderr.strip()[-400:]}")
    verify_png_size(dst, width, height)
    return dst
