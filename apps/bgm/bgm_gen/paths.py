"""External runtime and asset paths (stdlib only; no probing or downloads).

MUSIC_RUNTIME selects a user JSON file. No package-local runtime is read.
Relative configuration paths are relative to that JSON, never the installation.
Project paths keep their existing project-format semantics.
"""

import copy
import json
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class RuntimeConfigError(ValueError):
    """An explicitly selected user runtime cannot be loaded."""


def _expand(value, environ):
    if value == "~" or value.startswith("~/"):
        value = environ.get("HOME", os.path.expanduser("~")) + value[1:]
    else:
        value = os.path.expanduser(value)
    return re.sub(r"\$(?:\{([^}]+)\}|([A-Za-z_][A-Za-z0-9_]*))",
                  lambda m: environ.get(m.group(1) or m.group(2), m.group(0)), value)


def data_root(*, environ=None):
    """User-owned persistent data, outside the app checkout."""
    env = os.environ if environ is None else environ
    base = env.get("XDG_DATA_HOME") or "~/.local/share"
    return os.path.abspath(os.path.join(_expand(base, env),
                                       "framivra", "music"))


def runtime_path(*, environ=None):
    env = os.environ if environ is None else environ
    value = env.get("MUSIC_RUNTIME")
    return (os.path.abspath(_expand(value, env))
            if value else os.path.join(data_root(environ=env), "runtime.json"))


def _path(value, base, command=False, *, environ=None):
    if not isinstance(value, str) or not value:
        return value
    value = _expand(value, os.environ if environ is None else environ)
    # Bare executable names / library sonames retain PATH / loader semantics.
    if command and not os.path.isabs(value) and "/" not in value and "\\" not in value:
        return value
    return os.path.abspath(os.path.join(base, value))


def local_runtime(*, environ=None):
    env = os.environ if environ is None else environ
    path = runtime_path(environ=env)
    if not env.get("MUSIC_RUNTIME") and not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            rt = json.load(f)
        if not isinstance(rt, dict):
            raise ValueError("top level must be a JSON object")
        base = os.path.dirname(path)
        for key in ("assets_root", "samples_root", "python_system", "python_torch"):
            if key in rt:
                rt[key] = _path(rt[key], base, command=key.startswith("python"), environ=env)
        for section in ("tools", "sf2"):
            if section in rt:
                if not isinstance(rt[section], dict):
                    raise ValueError(f"{section} must be an object")
                rt[section] = {key: _path(value, base, command=section == "tools", environ=env)
                               for key, value in rt[section].items()}
        for section in ("demucs", "matchering"):
            if section not in rt:
                continue
            if not isinstance(rt[section], dict):
                raise ValueError(f"{section} must be an object")
            for key in ("python", "repo"):
                if key in rt[section]:
                    rt[section][key] = _path(rt[section][key], base, command=key == "python", environ=env)
            child_env = rt[section].get("env", {})
            if not isinstance(child_env, dict):
                raise ValueError(f"{section}.env must be an object")
            for key in ("HF_HOME", "TORCH_HOME", "XDG_CACHE_HOME"):
                if key in child_env:
                    child_env[key] = _path(child_env[key], base, environ=env)
        if "comfyui" in rt and not isinstance(rt["comfyui"], dict):
            raise ValueError("comfyui must be an object")
        return rt
    except (OSError, ValueError, TypeError) as e:
        raise RuntimeConfigError(f"MUSIC_RUNTIME {path}: {e}") from e


def assets_root(runtime=None, *, environ=None):
    env = os.environ if environ is None else environ
    rt = local_runtime(environ=env) if runtime is None else runtime
    value = env.get("MUSIC_ASSETS_ROOT") or rt.get("assets_root")
    return _path(value or os.path.join(data_root(environ=env), "assets"), os.getcwd(), environ=env)


def samples_root(runtime=None, *, environ=None):
    env = os.environ if environ is None else environ
    rt = local_runtime(environ=env) if runtime is None else runtime
    value = env.get("MUSIC_SAMPLES_ROOT") or rt.get("samples_root")
    return _path(value or os.path.join(assets_root(rt, environ=env), "samples"), os.getcwd(), environ=env)


def effective_runtime(runtime=None, *, environ=None):
    """Load once per operation; explicit dicts (including {}) never reload JSON."""
    rt = local_runtime(environ=environ) if runtime is None else copy.deepcopy(runtime)
    rt["assets_root"] = assets_root(rt, environ=environ)
    rt["samples_root"] = samples_root(rt, environ=environ)
    return rt
