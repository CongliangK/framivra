#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""secrets.py · 生图 API key 解析链（参考实现改写）

优先级链（方案 §5.5）：
  --key（值或文件路径） > SC_API_KEY > NANOBANANA2_API_KEY > 系统 keyring
  > ~/.config/vtx-imgen/secrets.json（0600）
key 永不入仓不入日志；对外展示只打印尾 4 位（mask_key）。

相对参考实现的删减：save/delete（本模块 CLI 只读消费，写入手工建文件）；
新增 describe_key_chain() 供 `key status` / init.sh 做只本地探测（不触网）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional, Tuple

SERVICE_NAME = "vtx-imgen"
ACCOUNT_NAME = "api-key"
SECRET_FILE = "secrets.json"
CONFIG_DIR_NAME = "vtx-imgen"


def _config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / CONFIG_DIR_NAME


def _secret_path() -> Path:
    return _config_dir() / SECRET_FILE


def _try_keyring_get() -> Optional[str]:
    try:
        import keyring  # type: ignore

        value = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
        return value.strip() if value else None
    except Exception:
        return None


def _read_secret_file() -> Optional[str]:
    path = _secret_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data.get("api_key")
        return value.strip() if isinstance(value, str) and value.strip() else None
    except Exception:
        return None


def get_env_api_key() -> Optional[str]:
    value = os.environ.get("SC_API_KEY") or os.environ.get("NANOBANANA2_API_KEY")
    return value.strip() if value else None


def get_stored_api_key() -> Optional[str]:
    return _try_keyring_get() or _read_secret_file()


def mask_key(api_key: str) -> str:
    """对外展示掩码：仅尾 4 位（短 key 全掩）。"""
    api_key = (api_key or "").strip()
    if len(api_key) <= 4:
        return "****"
    return "****" + api_key[-4:]


def _looks_like_path(value: str) -> bool:
    return (
        value.startswith((".", "~"))
        or "/" in value
        or "\\" in value
        or Path(value).is_absolute()
    )


def resolve_api_key(key_or_file: Optional[str] = None, allow_stored: bool = True) -> str:
    """按优先级链解析 key；全缺抛 ValueError（中文可行动提示）。

    空白串视为未提供（上游对 "  " 会误当路径读目录，内化时修正）。
    """
    key_or_file = (key_or_file or "").strip() or None
    if key_or_file:
        raw = key_or_file.strip()
        possible_path = Path(raw).expanduser()
        if possible_path.exists() or _looks_like_path(raw):
            if not possible_path.exists():
                raise ValueError(f"API Key 文件不存在: {possible_path}")
            return possible_path.read_text(encoding="utf-8").strip()
        return raw

    env_key = get_env_api_key()
    if env_key:
        return env_key

    if allow_stored:
        stored = get_stored_api_key()
        if stored:
            return stored

    raise ValueError(
        "未提供 API Key，请通过 --key（值或文件）、环境变量 SC_API_KEY / "
        "NANOBANANA2_API_KEY、keyring 或 ~/.config/vtx-imgen/secrets.json 提供。"
    )


def describe_key_chain(key_or_file: Optional[str] = None) -> Tuple[List[Tuple[str, Optional[str]]], Optional[Tuple[str, str]]]:
    """逐级探测 key 链（纯本地，不触网）。

    返回 ([(级别名, 命中值或 None), ...], (最终使用级别名, 最终key) or None)。
    供 `key status` 子命令与 init.sh 打印。
    """
    levels: List[Tuple[str, Optional[str]]] = []

    cli_key: Optional[str] = None
    if key_or_file:
        try:
            cli_key = resolve_api_key(key_or_file, allow_stored=False)
        except ValueError:
            cli_key = None
    levels.append(("--key 参数", cli_key))

    sc = os.environ.get("SC_API_KEY", "").strip() or None
    levels.append(("SC_API_KEY 环境变量", sc))

    nb = os.environ.get("NANOBANANA2_API_KEY", "").strip() or None
    if not sc:  # 上游 env 链语义：SC_API_KEY 命中则 NANOBANANA2 不再取
        levels.append(("NANOBANANA2_API_KEY 环境变量", nb))
    else:
        levels.append(("NANOBANANA2_API_KEY 环境变量（被 SC_API_KEY 遮蔽）", None))

    stored = None
    if not cli_key and not sc and not nb:
        stored = _try_keyring_get() or _read_secret_file()
        levels.append(("keyring（vtx-imgen/api-key）", _try_keyring_get()))
        levels.append(("~/.config/vtx-imgen/secrets.json", _read_secret_file()))
    else:
        levels.append(("keyring（vtx-imgen/api-key，被高级别遮蔽）", None))
        levels.append(("~/.config/vtx-imgen/secrets.json（被高级别遮蔽）", None))

    chosen: Optional[Tuple[str, str]] = None
    if cli_key:
        chosen = ("--key", cli_key)
    elif sc:
        chosen = ("SC_API_KEY", sc)
    elif nb:
        chosen = ("NANOBANANA2_API_KEY", nb)
    elif stored:
        chosen = ("keyring/secrets.json", stored)
    return levels, chosen
