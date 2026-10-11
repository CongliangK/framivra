#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reference_upload.py · 本地参考图 → 公网 URL（参考实现改写）

生图 API 参考图走 JSON ``urls`` 字段（公网 URL）。本地文件先外链：
  1. 自有服务器通道（可选）：设置 VT_OWN_UPLOAD_SSH_TARGET（ssh 目标）与
     VT_OWN_UPLOAD_BASE_URL（公网基 URL）后启用，scp 上传 → <BASE_URL>/<名>
     公网可读。两个变量任一未设置即整体跳过该通道。
  2. 默认通道 tmpfiles.org（临时外链服务；常被墙，支持
     SC_PROXY/HTTPS_PROXY/HTTP_PROXY 代理与本地 7890 mihomo 兜底重试）。

通道选择：VT_OWN_UPLOAD_SSH_TARGET 已设置 → 先自有服务器，失败自动回退
tmpfiles.org（SC_REF_UPLOAD_TARGET=tmpfiles 可强制只走 tmpfiles）。

注意：本文件是 imgen 内唯一联网上传通道，仅在 --ref 指向本地文件时使用
（URL 形式的 --ref 原样直传，零上传动作）。依赖 requests（本模块软依赖）。
"""
from __future__ import annotations

import mimetypes
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import urlparse

import requests

TMPFILES_UPLOAD_URL = "https://tmpfiles.org/api/v1/upload"

# 自有服务器图床（可选通道，环境变量注入；未配置 VT_OWN_UPLOAD_SSH_TARGET 即跳过）。
OWN_SERVER_SSH_TARGET = os.environ.get("VT_OWN_UPLOAD_SSH_TARGET") or os.environ.get("SC_IMG_SSH_TARGET", "")
OWN_SERVER_REMOTE_DIR = os.environ.get("VT_OWN_UPLOAD_REMOTE_DIR") or os.environ.get("SC_IMG_REMOTE_DIR", "/var/www/sc-img")
OWN_SERVER_BASE_URL = os.environ.get("VT_OWN_UPLOAD_BASE_URL") or os.environ.get("SC_IMG_BASE_URL", "")


def _upload_proxies() -> Optional[dict]:
    proxy_url = (
        os.environ.get("SC_PROXY")
        or os.environ.get("ALL_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
    )
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


class ReferenceUploadError(RuntimeError):
    """本地参考图外链失败。"""


def is_data_url(value: str) -> bool:
    return value.strip().lower().startswith("data:image/")


def looks_like_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def ensure_image_file(path: Path) -> Path:
    image_path = Path(path).expanduser()
    if not image_path.is_file():
        raise ValueError(f"本地参考图不存在或不是文件: {image_path}")
    mime_type, _encoding = mimetypes.guess_type(str(image_path))
    if not mime_type or not mime_type.startswith("image/"):
        raise ValueError(f"无法识别图片类型: {image_path}")
    return image_path


def upload_to_tmpfiles(path: Path, timeout: int = 60) -> str:
    """上传 tmpfiles.org 并返回 /dl/ 直链；网络失败按代理链+本地 7890 兜底重试。"""
    image_path = ensure_image_file(path)
    proxies = _upload_proxies()
    last_exc: Optional[Exception] = None
    attempts = [(proxies, "configured")]
    if proxies is None:
        attempts.append(
            ({"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}, "本地 7890 代理兜底")
        )
    data = None
    for use_proxies, _label in attempts:
        try:
            with image_path.open("rb") as handle:
                response = requests.post(
                    TMPFILES_UPLOAD_URL,
                    files={"file": (image_path.name, handle)},
                    timeout=timeout,
                    proxies=use_proxies,
                )
            response.raise_for_status()
            data = response.json()
            break
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            continue
    if data is None:
        hint = "。可设置 SC_PROXY=http://127.0.0.1:7890 显式启用代理" if proxies is None else ""
        raise ReferenceUploadError(f"上传本地参考图失败: {image_path.name}（{last_exc}）{hint}") from last_exc

    if data.get("status") != "success" or not isinstance(data.get("data"), dict):
        raise ReferenceUploadError(f"上传本地参考图失败: {image_path.name}（{data}）")
    page_url = data["data"].get("url")
    if not isinstance(page_url, str) or not page_url.startswith("https://tmpfiles.org/"):
        raise ReferenceUploadError(f"上传本地参考图失败: {image_path.name}（未返回有效 URL）")
    return page_url.replace("https://tmpfiles.org/", "https://tmpfiles.org/dl/", 1)


def upload_to_own_server(path: Path, timeout: int = 90) -> str:
    """scp 到自有服务器并返回公网 URL；scp/ssh/回验任一失败即抛错（触发回退）。"""
    image_path = ensure_image_file(path)
    ssh_target = OWN_SERVER_SSH_TARGET
    remote_dir = OWN_SERVER_REMOTE_DIR
    base_url = OWN_SERVER_BASE_URL.rstrip("/")
    if not ssh_target or not base_url:
        raise ReferenceUploadError(
            "自有服务器通道未配置（需设置 VT_OWN_UPLOAD_SSH_TARGET 与 VT_OWN_UPLOAD_BASE_URL）"
        )

    stem = re.sub(r"[^0-9A-Za-z_-]+", "-", image_path.stem).strip("-")[:40] or "ref"
    remote_name = "{}-{}-{}{}".format(
        stem, time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:8],
        image_path.suffix.lower() or ".png",
    )
    remote_path = "{}/{}".format(remote_dir, remote_name)
    ssh_opts = ["-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]
    subprocess.run(
        ["scp", *ssh_opts, str(image_path), "{}:{}".format(ssh_target, remote_path)],
        check=True,
        capture_output=True,
        timeout=timeout,
    )
    # scp 保留源权限位，显式 chmod 保证 Caddy（www-data 视角）可读。
    subprocess.run(
        ["ssh", *ssh_opts, ssh_target, "chmod 644 {}".format(shlex.quote(remote_path))],
        check=True,
        capture_output=True,
        timeout=30,
    )
    url = "{}/{}".format(base_url, remote_name)
    # 回验：不走本地代理（裸公网可达性必须与生图 API 视角一致）。
    response = requests.head(url, timeout=20, proxies={"http": None, "https": None})
    response.raise_for_status()
    return url


def upload_reference(path: Path, timeout: int = 90) -> str:
    """自有服务器通道（已配置时）优先，失败/未配置走 tmpfiles.org（不阻塞生成流程）。"""
    if not OWN_SERVER_SSH_TARGET or not OWN_SERVER_BASE_URL:
        return upload_to_tmpfiles(path, timeout=timeout)
    if os.environ.get("SC_REF_UPLOAD_TARGET", "").strip().lower() == "tmpfiles":
        return upload_to_tmpfiles(path, timeout=timeout)
    try:
        return upload_to_own_server(path, timeout=timeout)
    except Exception as exc:
        print(f"⚠ 自有服务器图床上传失败（{exc}），回退 tmpfiles.org", file=sys.stderr)
        return upload_to_tmpfiles(path, timeout=timeout)


def upload_local_images(paths: Iterable[Path]) -> List[str]:
    return [upload_reference(path) for path in paths]
