#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""api_client.py · 生图 API 客户端：异步提交 + 轮询 + 下载 + 错误分类
（由独立参考实现改写内化维护）

协议要点：
  - Authorization 裸 key 头（无 Bearer 前缀）
  - 异步提交 {code:200,data:{id,count}} 解析
  - 轮询 GET /api/async/detail（status 0/1 排队生成、2=成功、
    3=审核拒绝（不退款不重试）、-1=失败）
  - result 四形态防御解析（str / list[str] / list[dict] /
    wrapper dict images|urls|result|data|list）
  - 错误分类：429/5xx 可重试（继续轮询），4xx 立即上抛
  - 「result 已有图即成功」优先于数值 status 的防御策略

相对参考实现的改动：
  - api_base 可注入（默认 models.BASE_URL）：测试用本地 mock http.server、
    未来切网关都不改代码；
  - download_url 增加网络失败重试 ≤2 次后上抛可行动错误（方案 §9.3：
    URL 2 小时失效场景；上游无重试）。
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable, Iterable, List, Optional

import requests

import models

StatusCallback = Callable[[dict], None]


class ImgenApiError(RuntimeError):
    """生图 API 错误（code 附带，供 retryable 分类与 CLI 文案）。"""

    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = _coerce_code(code) if code is not None else _code_from_message(message)

    @property
    def retryable(self) -> bool:
        """detail/查询错误是否值得继续重试：429/5xx 可重试，4xx 立即上抛。"""
        if self.code is None:
            return True
        if self.code == 429:
            return True
        if self.code >= 500:
            return True
        # 参数/鉴权/余额类错误应立即浮出，而不是轮询到超时。
        if 400 <= self.code < 500:
            return False
        return True


class ImgenClient:
    def __init__(self, api_key: str, api_base: Optional[str] = None, session: Optional[requests.Session] = None):
        api_key = (api_key or "").strip()
        if not api_key:
            raise ValueError("API Key 不能为空")
        self.api_key = api_key
        self.api_base = (api_base or models.BASE_URL).rstrip("/")
        self.session = session or requests.Session()

    @property
    def auth_headers(self) -> dict:
        return {"Authorization": self.api_key}

    @property
    def json_headers(self) -> dict:
        return {"Authorization": self.api_key, "Content-Type": "application/json"}

    def submit(
        self,
        prompt: str,
        model_key: str = "nano",
        size: Optional[str] = None,
        aspect_ratio: str = "auto",
        urls: Optional[Iterable[str]] = None,
        quality: Optional[str] = None,
        background: Optional[str] = None,
        mask: Optional[str] = None,
    ) -> dict:
        """提交付费生成任务，返回 {task_id, prompt, count, request, model}。

        调用方必须先完成付费确认门（按模型单价 0.1/0.3 元/张）。
        """
        model = models.require_model(model_key)
        payload = models.build_payload(prompt, model_key, size, aspect_ratio, urls,
                                       quality=quality, background=background, mask=mask)
        endpoint = self.api_base + model.submit_path
        resp = self.session.post(endpoint, headers=self.json_headers, json=payload, timeout=30)
        data = _json_or_raise(resp)
        if data.get("code") != 200:
            raise ImgenApiError(
                "提交失败 [{}]: {}".format(data.get("code"), data.get("msg", resp.text)), code=data.get("code")
            )
        task_id = data.get("data", {}).get("id")
        if not task_id:
            raise ImgenApiError(f"提交成功但响应缺少 task id: {data}")
        return {
            "task_id": task_id,
            "prompt": prompt,
            "count": data.get("data", {}).get("count", "1"),
            "request": payload,
            "model": models.MODELS[model_key].name,
        }

    def detail(self, task_id: str) -> dict:
        url = self.api_base + models.DETAIL_PATH
        resp = self.session.get(url, headers=self.auth_headers, params={"id": task_id}, timeout=30)
        data = _json_or_raise(resp)
        if data.get("code") != 200:
            raise ImgenApiError(
                "查询失败 [{}]: {}".format(data.get("code"), data.get("msg", resp.text)), code=data.get("code")
            )
        return data.get("data", {})

    def poll_result(
        self,
        task_id: str,
        poll_interval: float = 3.0,
        timeout: float = 180.0,
        callback: Optional[StatusCallback] = None,
    ) -> List[str]:
        """轮询直到拿到图片 URL 列表；status=3（审核拒绝）立即抛错不重试。

        防御：result 已出图视为成功（优先于数值 status，字段形态随时间漂移）；
        status=2 但 result 暂空时允许短暂重查（3 次后判失败，防竞态误报）。
        """
        start = time.time()
        empty_success_retries = 0
        max_empty_success_retries = 3
        while True:
            elapsed = time.time() - start
            if elapsed > timeout:
                raise TimeoutError(f"轮询超时（{timeout}s），task_id={task_id}")

            try:
                info = self.detail(task_id)
            except ImgenApiError as exc:
                if not exc.retryable:
                    raise
                if callback:
                    callback({"status": None, "message": f"查询暂不可用，继续重试: {exc}"})
                time.sleep(poll_interval)
                continue
            except requests.RequestException as exc:
                if callback:
                    callback({"status": None, "message": f"网络查询暂不可用，继续重试: {exc}"})
                time.sleep(poll_interval)
                continue

            if callback:
                callback(info)

            images = extract_image_urls(info.get("result"))
            status = info.get("status")

            if images:
                return images

            if _status_is_success(status):
                empty_success_retries += 1
                if empty_success_retries > max_empty_success_retries:
                    raise ImgenApiError(f"任务成功但未返回图片，task_id={task_id}")
                time.sleep(poll_interval)
                continue

            if _status_is_rejected(status):
                # status=3：内容审核拒绝。终态失败：不退款、不重试、不重复提交。
                reason = info.get("message", "unknown")
                raise ImgenApiError(f"任务被内容审核拒绝（不退款不重试）: {reason}", code=3)

            if _status_is_failure(status):
                reason = info.get("message", "unknown")
                raise ImgenApiError(f"任务失败: {reason}")

            time.sleep(poll_interval)

    def download_image(self, url: str, output_path: Path, retries: int = 2) -> Path:
        return download_url(url, output_path, session=self.session, retries=retries)


def download_url(
    url: str,
    output_path: Path,
    session: Optional[requests.Session] = None,
    retries: int = 2,
) -> Path:
    """下载一张图片落盘。网络失败/HTTP 错误重试 ≤ retries 次后上抛可行动错误。

    （方案 §9.3：图片 URL 2 小时失效；短重试覆盖瞬时抖动，超限即报错。）
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    requester = session or requests
    last_exc: Optional[Exception] = None
    attempts = max(0, int(retries)) + 1
    for attempt in range(1, attempts + 1):
        try:
            resp = requester.get(url, timeout=120)
            resp.raise_for_status()
            output_path.write_bytes(resp.content)
            return output_path
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < attempts:
                time.sleep(min(2.0, 0.5 * attempt))
    raise ImgenApiError(
        f"下载失败（已重试 {attempts - 1} 次）: {url}（{last_exc}）——"
        "图片 URL 约 2 小时失效；可用 gen --task-id <id> 恢复只重下载"
    ) from last_exc


def extract_image_urls(result) -> List[str]:
    """从 API ``result`` 字段防御性提取图片 URL（四形态，上游 :172-204 同款）。

    形态：["url"] / "url" / [{"url":...}] / {"images":[...]}（嵌套 wrapper）。
    """
    if not result:
        return []
    if isinstance(result, str):
        return [result] if result.strip() else []
    if isinstance(result, dict):
        for key in ("images", "urls", "result", "data", "list"):
            nested = result.get(key)
            urls = extract_image_urls(nested)
            if urls:
                return urls
        return []
    if isinstance(result, (list, tuple)):
        urls: List[str] = []
        for item in result:
            if isinstance(item, str) and item.strip():
                urls.append(item)
            elif isinstance(item, dict):
                for key in ("url", "image", "image_url", "src"):
                    val = item.get(key)
                    if isinstance(val, str) and val.strip():
                        urls.append(val)
                        break
        return urls
    return []


_SUCCESS_STATUSES = {2, "2", "success", "SUCCESS", "succeed", "succeeded", "completed", "done", "complete"}
_FAILURE_STATUSES = {-1, "-1", "fail", "failed", "FAIL", "error", "ERROR", "canceled", "cancelled"}
# status=3 = 内容审核拒绝（终态：不退款不重试，必须立即浮出而非轮询到超时）。
_REJECTED_STATUSES = {3, "3", "rejected", "moderated", "policy_violation", "violation"}


def _status_is_success(status) -> bool:
    return status in _SUCCESS_STATUSES


def _status_is_failure(status) -> bool:
    return status in _FAILURE_STATUSES


def _status_is_rejected(status) -> bool:
    return status in _REJECTED_STATUSES


def _json_or_raise(resp: requests.Response) -> dict:
    try:
        return resp.json()
    except ValueError as exc:
        raise ImgenApiError(
            f"API 返回非 JSON 响应: HTTP {resp.status_code} {resp.text[:300]}", code=resp.status_code
        ) from exc


def _coerce_code(code) -> Optional[int]:
    if code is None:
        return None
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


def _code_from_message(message) -> Optional[int]:
    match = re.search(r"\[(\d{3})\]", str(message))
    return _coerce_code(match.group(1)) if match else None
