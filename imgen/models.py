#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""models.py · 速创 API 模型/端点/参数定义（内化自 suchuang-image src/slime/core/models.py）

内化对照（上游仓只读，运行时零依赖；行号对应 2026-09 快照）：
  - BASE_URL                 ← core/models.py:10
  - nano 提交端点 POST /api/async/image_nanoBanana2      ← core/models.py:36
  - gpt 提交端点 POST /api/async/image_gpt（无 _image 后缀变体，:43 权威）
                                                            ← core/models.py:43
  - detail 轮询端点 GET /api/async/detail                 ← core/models.py:11
  - nano size 1K/2K/4K + aspectRatio 校验                 ← core/models.py:93-101
  - gpt size=纯比例、不提交 aspectRatio                    ← core/models.py:63-74,102-108
  - 参考图 ≤14 张                                          ← core/models.py:113-114
  - estimate_cost 0.1 元/张                                ← core/models.py:120-121
  - is_data_url                                            ← core/reference_upload.py:57
    （内化时改为本文件内嵌纯函数，避免 models 传递依赖 requests）

本文件新增（上游无此概念，方案 v2.1 §5.2）：
  - --kind 联动矩阵 KIND_LINKAGE 与目标尺寸 KIND_TARGETS、固定文件名 KIND_FILENAME
  - nano 实出尺寸估算锚点（用于画质警告的插值倍数估算，仅提示性质）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

BASE_URL = "https://api.wuyinkeji.com"
NANO_SUBMIT_PATH = "/api/async/image_nanoBanana2"
GPT_SUBMIT_PATH = "/api/async/image_gpt"
DETAIL_PATH = "/api/async/detail"

NANO_SIZES = ("1K", "2K", "4K")
ASPECT_RATIOS = ("auto", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9")
# gpt 的 size 字段按官方文档是输出比例（16:9 等）而非像素，故与比例集同源。
GPT_RATIO_CHOICES = (
    "auto", "1:1", "3:2", "2:3", "16:9", "9:16", "4:3", "3:4",
    "21:9", "9:21", "1:3", "3:1", "2:1", "1:2",
)
GPT_SIZE_CHOICES = GPT_RATIO_CHOICES  # 兼容名（上游 core/models.py:18）


@dataclass(frozen=True)
class ModelDefinition:
    key: str
    name: str
    submit_path: str
    description: str
    price_label: str = "0.1元/张"
    supports_urls: bool = True
    supports_aspect_ratio: bool = False


MODELS: Dict[str, ModelDefinition] = {
    "nano": ModelDefinition(
        key="nano",
        name="NanoBanana2 (Gemini 3.1 Flash Image)",
        submit_path=NANO_SUBMIT_PATH,
        description="Gemini 底座，速度快，支持 1K/2K/4K 和 aspectRatio",
        supports_aspect_ratio=True,
    ),
    "gpt-image": ModelDefinition(
        key="gpt-image",
        name="GPT-Image-2 (OpenAI)",
        submit_path=GPT_SUBMIT_PATH,
        description="OpenAI 底座，适合写实/文字，使用 size 字段控制比例，不提交 aspectRatio",
        supports_aspect_ratio=False,
    ),
}


def require_model(model_key: str) -> ModelDefinition:
    try:
        return MODELS[model_key]
    except KeyError as exc:
        raise ValueError(f"未知模型: {model_key}. 可用模型: {', '.join(MODELS)}") from exc


def normalize_urls(urls: Optional[Iterable[str]]) -> List[str]:
    if not urls:
        return []
    return [str(url).strip() for url in urls if str(url).strip()]


def is_data_url(value: str) -> bool:
    """是否 data:image/* 内嵌数据（本地文件须先外链，gpt 不收 data url）。"""
    return value.strip().lower().startswith("data:image/")


def gpt_size_to_api(size: Optional[str]) -> Optional[str]:
    """gpt 的 size 字段 = 输出比例；auto 为文档默认值，省略不交（同 nano auto aspect）。"""
    if not size or size == "auto":
        return None
    if size not in GPT_RATIO_CHOICES:
        raise ValueError(f"GPT-Image-2 size/比例 必须是: {', '.join(GPT_RATIO_CHOICES)}")
    return size


def build_payload(
    prompt: str,
    model_key: str = "nano",
    size: Optional[str] = None,
    aspect_ratio: str = "auto",
    urls: Optional[Iterable[str]] = None,
) -> dict:
    """构建速创 API 提交载荷（模型相关参数差异集中在此校验，付费提交前先过一遍）。"""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("prompt 不能为空")

    require_model(model_key)
    payload = {"prompt": prompt}
    clean_urls = normalize_urls(urls)

    if model_key == "nano":
        nano_size = size or "1K"
        if nano_size not in NANO_SIZES:
            raise ValueError(f"NanoBanana2 size 必须是: {', '.join(NANO_SIZES)}")
        if aspect_ratio not in ASPECT_RATIOS:
            raise ValueError(f"aspectRatio 必须是: {', '.join(ASPECT_RATIOS)}")
        payload["size"] = nano_size
        if aspect_ratio != "auto":
            payload["aspectRatio"] = aspect_ratio
    elif model_key == "gpt-image":
        if any(is_data_url(url) for url in clean_urls):
            raise ValueError("GPT-Image-2 的参考图需要公网图片 URL；请用本地图片自动上传，或输入远程图片 URL")
        api_size = gpt_size_to_api(size or "auto")
        if api_size:
            payload["size"] = api_size
        # gpt-image 明确不提交 aspectRatio（上游 core/models.py:108 同款纪律）。
    else:  # 防御；require_model 已校验。
        raise ValueError(f"未知模型: {model_key}")

    if clean_urls:
        if len(clean_urls) > 14:
            raise ValueError("参考图 urls 最多 14 张")
        payload["urls"] = clean_urls

    return payload


def estimate_cost(task_count: int) -> float:
    return round(max(0, int(task_count)) * 0.1, 2)


# ---------------------------------------------------------------------------
# --kind 联动矩阵（方案 v2.1 §5.2 强制；上游无此概念）
# ---------------------------------------------------------------------------

KIND_CHOICES = ("bg4k", "bg1080p", "vertical", "raw")

# cover-crop 目标精确尺寸（像素）。
KIND_TARGETS = {
    "bg4k": (3840, 2160),
    "bg1080p": (1920, 1080),
    "vertical": (1080, 1920),
}

# kind → 默认生成档位（模型/分辨率/比例）。raw 不联动（用户指定）。
KIND_LINKAGE = {
    "bg4k": {"model": "nano", "size": "4K", "ratio": "16:9"},
    "bg1080p": {"model": "nano", "size": "2K", "ratio": "16:9"},
    "vertical": {"model": "nano", "size": "4K", "ratio": "9:16"},
}

# kind → 项目内固定落位文件名（目录 assets/bg/ 由 CLI 负责自建）。
KIND_FILENAME = {
    "bg4k": "background_4k.png",
    "bg1080p": "background_1080p.png",
    "vertical": "background_vertical.png",
}

# nano 实出尺寸估算锚点：2K 16:9 实证 ~2752×1536（方案 §5.2），其余档位按长边等比外推。
# 仅用于提交前的画质警告（插值倍数提示），非精确契约。
_ANCHOR_LONG_EDGE = {"1K": 1376, "2K": 2752, "4K": 5504}
_ANCHOR_EXACT = {("2K", "16:9"): (2752, 1536)}


def estimate_nano_output(size: str, ratio: str) -> Tuple[int, int]:
    """估算 nano 各档位实出像素（长边锚点 + 比例换算；auto 当 1:1 处理）。"""
    long_edge = _ANCHOR_LONG_EDGE.get(size)
    if long_edge is None:
        raise ValueError(f"未知 nano size: {size}")
    if (size, ratio) in _ANCHOR_EXACT:
        return _ANCHOR_EXACT[(size, ratio)]
    if ratio == "auto":
        return (long_edge, long_edge)
    try:
        a_str, b_str = ratio.split(":")
        a, b = int(a_str), int(b_str)
    except ValueError as exc:
        raise ValueError(f"无法解析比例: {ratio}") from exc
    if a >= b:
        return (long_edge, long_edge * b // a)
    return (long_edge * a // b, long_edge)


def upscale_factor(src_wh: Tuple[int, int], dst_wh: Tuple[int, int]) -> float:
    """cover-crop 时源图相对目标的放大倍数（>1.0 表示需插值放大）。"""
    sw, sh = src_wh
    dw, dh = dst_wh
    if sw <= 0 or sh <= 0:
        return 1.0
    return max(dw / sw, dh / sh)
