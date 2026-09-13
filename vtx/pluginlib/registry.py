"""组件发现与静态注册表（§2.1 不变量 3 / §2.4 安全节，设计权威 docs/ARCHITECTURE.md）。

发现 = 三路：
  ① 内置扫描 apps/ 与 plugins/（目录存在才扫——P1-2 时点为空/不存在皆正常）；
  ② config 的 external_apps[] / external_plugins[]（resolve 后白名单根校验，
     **默认 disabled**，条目 {"path": ..., "enabled": true} 才启用，§2.4）；
  ③ plugins/registry.json 静态注册表——全量官方插件的**键声明汇总**
     （manifest schema_keys 的登记面），同时是「未装插件报错文案」的数据源
     （报错能给出安装指引而非裸 Unknown key，§2.1 不变量 3）。

安装态判定（§2.1）：plugins/<name>/ 目录存在 且 registry.json 未禁用；registry
条目的 installed 字段为**落地声明**（P2b 装配 plugins/<name>/ 时置 true——
声明与物理存在须同时成立，防只改一边的漂移）。
"""
import json
import sys
from pathlib import Path

from . import config as _config
from .manifest import load_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _root(repo_root=None) -> Path:
    return Path(repo_root) if repo_root else REPO_ROOT


def load_static_registry(repo_root=None) -> dict:
    """plugins/registry.json → {"plugins": {name: entry}}。

    缺文件 → 空注册表（P1-2 前的合规态）；坏 JSON → stderr 警告 + 空表
    （注册表损坏不阻断内核——三分支扫描退化为「未知键警告不阻断」）。
    """
    f = _root(repo_root) / "plugins" / "registry.json"
    if not f.is_file():
        return {"plugins": {}}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"⚠ plugins/registry.json 解析失败（{e}）——按空注册表继续"
              "（插件键三分支退化为未知键警告）", file=sys.stderr)
        return {"plugins": {}}
    if not isinstance(data, dict) or not isinstance(data.get("plugins", {}), dict):
        print("⚠ plugins/registry.json 结构不符（须为 {\"plugins\": {...}}）——"
              "按空注册表继续", file=sys.stderr)
        return {"plugins": {}}
    return {"plugins": {k: v for k, v in data["plugins"].items() if isinstance(v, dict)}}


def _external_cfg(repo_root=None):
    return _config.load_config(repo_root=_root(repo_root))


def scan_builtin(repo_root=None) -> list:
    """内置扫描 apps/ 与 plugins/（目录存在才扫）。返回 entry 列表：

    {name, kind, source: "builtin", dir, enabled, manifest, manifest_errors}
    官方内置件 enabled 缺省 true（「缺省关闭」指不写键无行为，不是写了键可跳过）。
    """
    root = _root(repo_root)
    entries = []
    for kind, folder in (("app", "apps"), ("plugin", "plugins")):
        base = root / folder
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if not d.is_dir() or d.name.startswith((".", "_")):
                continue
            manifest, errors = load_manifest(d)
            name = (manifest or {}).get("name") or d.name
            entries.append({"name": name, "kind": kind, "source": "builtin",
                            "dir": d, "enabled": True,
                            "manifest": manifest, "manifest_errors": errors})
    return entries


def scan_external(repo_root=None) -> list:
    """config external_apps[] / external_plugins[]（默认 disabled，§2.4）。

    白名单根校验：条目 resolve（~ 展开）后必须是存在的目录，manifest 必须落在
    该目录内（load_manifest 只读 <dir>/plugin.json|app.json，天然满足）；health
    命令的根内相对校验由 manifest.py 安全节把关。坏条目产出 errors entry 不崩。
    """
    root = _root(repo_root)
    cfg = _external_cfg(root)
    entries = []
    for kind, key in (("app", "external_apps"), ("plugin", "external_plugins")):
        for item in _config.external_entries(cfg, key):
            raw = item.get("path")
            entry = {"name": None, "kind": kind, "source": "external",
                     "dir": None, "enabled": bool(item.get("enabled")),
                     "manifest": None, "manifest_errors": []}
            if item.get("malformed") or not isinstance(raw, str):
                entry["manifest_errors"] = [
                    f"config {key} 条目形状非法（须为路径字符串或 {{path, enabled}}）: {raw!r}"]
                entry["name"] = "<malformed>"
                entries.append(entry)
                continue
            d = Path(raw).expanduser()
            if not d.is_absolute():
                d = (root / d)
            d = d.resolve()
            entry["dir"] = d
            if not d.is_dir():
                entry["manifest_errors"] = [f"external 目录不存在: {raw}"]
                entry["name"] = Path(str(raw)).name or "<external>"
                entries.append(entry)
                continue
            manifest, errors = load_manifest(d)
            entry["name"] = (manifest or {}).get("name") or d.name
            entry["manifest"] = manifest
            entry["manifest_errors"] = errors
            entries.append(entry)
    return entries


def discover(repo_root=None) -> list:
    """全量组件清单（内置 + external），按 (kind, name) 排序。entry 结构见上。"""
    entries = scan_builtin(repo_root) + scan_external(repo_root)
    return sorted(entries, key=lambda e: (e["kind"], str(e["name"])))


def _external_by_name(repo_root=None) -> dict:
    return {e["name"]: e for e in scan_external(repo_root) if e.get("name")}


def plugin_state(name: str, repo_root=None) -> str:
    """插件安装态 → active | not_installed | disabled | unknown（三分支判定核心）。

    installed = registry 声明 installed:true **且** plugins/<name>/ 目录存在
    （或 external 实体目录存在）；未进静态注册表的实体（external/第三方）目录
    在即 installed，enabled 取 config 条目（external 默认 disabled，§2.4）。
    """
    root = _root(repo_root)
    reg = load_static_registry(root)["plugins"].get(name)
    builtin_dir = root / "plugins" / name
    phys = builtin_dir.is_dir()
    if not phys:
        ext = _external_by_name(root).get(name)
        phys = ext is not None and ext["dir"] is not None and ext["dir"].is_dir()
    if reg is not None:
        installed = bool(reg.get("installed", False)) and phys
    else:
        installed = phys
    if not installed:
        return "not_installed"
    if reg is not None:
        enabled = bool(reg.get("enabled", True))
    else:
        ext = _external_by_name(root).get(name)
        enabled = bool(ext and ext.get("enabled"))
    return "active" if enabled else "disabled"


def lookup_key(key: str, repo_root=None):
    """键反查 → (plugin_name, key_info) | (None, None)。

    检索序：plugins/registry.json 静态注册表的 keys[]（官方插件）→ 已发现实体
    插件 manifest 的 extends.schema_keys（external/第三方）。key_info 含声明
    path（如 "sources[]"），调用方须校验出现位置与声明 path 一致。
    """
    root = _root(repo_root)
    for name, entry in sorted(load_static_registry(root)["plugins"].items()):
        for ki in entry.get("keys", []):
            if isinstance(ki, dict) and ki.get("key") == key:
                return name, ki
    for e in discover(root):
        if e["kind"] != "plugin" or not e["manifest"]:
            continue
        for ki in (e["manifest"].get("extends", {}) or {}).get("schema_keys", []):
            if isinstance(ki, dict) and ki.get("key") == key:
                return e["name"], ki
    return None, None


def registry_key_map(repo_root=None) -> dict:
    """静态注册表键声明汇总（vt plugins list / doctor 组件段展示用）：
    {key: {"plugin": name, "path": path}}。"""
    out = {}
    for name, entry in sorted(load_static_registry(repo_root)["plugins"].items()):
        for ki in entry.get("keys", []):
            if isinstance(ki, dict) and isinstance(ki.get("key"), str):
                out[ki["key"]] = {"plugin": name,
                                  "path": ki.get("path", "?")}
    return out


def declared_plugins(repo_root=None) -> dict:
    """静态注册表条目原样（name → entry），list/check 展示用。"""
    return load_static_registry(repo_root)["plugins"]
