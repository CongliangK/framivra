"""manifest 加载与校验（§2.3 manifest schema v1，设计权威 docs/ARCHITECTURE.md）。

manifest 只**静态登记** kind + schema_keys（registry.json 的数据源）；hooks 由代码
注册为单一事实源，加载插件包时与 manifest 声明做一致性校验（声明了没注册 /
注册了没声明都报错，§2.3 规则）——该一致性校验属插件包 import 期（P2b hooks
上线），本模块只做纯静态结构校验。

安全节（§2.4）：
  - health.command 必须为插件根内**相对路径**（拒绝对路径与 ..——防 health 探针
    被指向任意可执行；带参数时首 token 为脚本路径）；
  - entry.cli 相对路径存在性校验；
  - 路径统一符号链接解析（resolve strict）后必须仍落在 root.resolve() 内
    （A03 / issue #22：根内符号链接指向根外脚本、断链均拒绝）；
  - manifest 文件本身（plugin.json|app.json）resolve 后必须仍落在插件根
    resolve() 内（A03 追加修复，2026-10-13：manifest 本身是指向插件根外的
    符号链接时数据源已出根——字段级校验都以「根内相对路径」为前提，挡不住
    根外文件本身；插件根相对扫描根的收口见 registry.scan_builtin）。

错误语义：损坏 manifest **收集全部错误不崩**（doctor / registry 需要完整错误清单
而非首个异常）→ load_manifest 返回 (data|None, errors)。
"""
import json
import shlex
from pathlib import Path

import re

from .compatibility import core_requirement_error

NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
# §2.2 v1 两缝 + v1.1 validate + v1.2 缝 C/D（probe_source/finish，2026-09-14
# P6 增补——扩展点冻结面，与 pluginlib.hooks.HOOK_NAMES 同一值域）
KNOWN_HOOKS = ("prepare_source", "audio_events", "validate", "probe_source", "finish")
KINDS = ("app", "plugin")


def _check_rel_inside(root: Path, value: str, label: str, errors: list,
                      must_exist: bool = True):
    """相对路径安全校验：拒绝对路径与 ..；candidate.resolve(strict=True) 统一
    解析符号链接后的真实落点必须落在 root.resolve() 内（白名单根，防根内符号
    链接逃逸到根外脚本）；断链/缺失由 strict 解析直接拒绝；must_exist 时真实
    落点必须是常规文件。返回解析后的绝对真实路径或 None。"""
    p = Path(value)
    if p.is_absolute():
        errors.append(f"{label} 必须为相对路径（插件根内），得到绝对路径: {value}")
        return None
    if ".." in p.parts:
        errors.append(f"{label} 禁止包含 ..（安全节 §2.4）: {value}")
        return None
    root_real = root.resolve()
    candidate = root_real / p
    # strict=True：断链（含指向根外的断链）与缺失文件在此统一拒绝
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        errors.append(f"{label} 指向的文件不存在（含断链）: {value}")
        return None
    if not resolved.is_relative_to(root_real):
        errors.append(
            f"{label} 符号链接解析后落在插件根外（安全节 §2.4）: {value} -> {resolved}")
        return None
    if must_exist and not resolved.is_file():
        errors.append(f"{label} 指向的文件不存在: {value}")
        return None
    return resolved


def validate_manifest(dir_path: Path, data) -> list:
    """结构校验（纯函数，不读盘以外的环境）。返回错误清单（空 = 通过）。

    规则（§2.3）：name ^[a-z][a-z0-9-]*$ 且与目录名一致；version 非空串；kind
    app|plugin；entry.cli[] 相对路径存在；health{type: command|config_key}
    （command 须插件根内相对脚本路径）；requires{core,binaries,python_packages,
    platforms}；config_keys[]；docs 相对路径。plugin 额外 extends.schema_keys
    [{path,key}] 与 hooks[]（值域 = 已冻结扩展点）。
    """
    errors = []
    if not isinstance(data, dict):
        return ["manifest 顶层必须是 JSON 对象"]
    root = Path(dir_path)

    name = data.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name or ""):
        errors.append(f"name {name!r} 不合规则 ^[a-z][a-z0-9-]*$")
    if not isinstance(name, str) or name != root.name:
        errors.append(f"name {name!r} 与目录名 {root.name!r} 不一致")

    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        errors.append("version 必须为非空字符串（语义化版本串）")

    kind = data.get("kind")
    if kind not in KINDS:
        errors.append(f"kind 必须为 {'|'.join(KINDS)}，得到 {kind!r}")

    entry = data.get("entry")
    if not isinstance(entry, dict):
        errors.append("entry 必须为对象（含 cli 数组）")
    else:
        cli = entry.get("cli")
        if not isinstance(cli, list) or not all(isinstance(x, str) for x in cli):
            errors.append("entry.cli 必须为字符串数组（纯路由声明型 app 可为空数组）")
        else:
            # bgm 类纯路由声明 app 允许空数组；声明了的路径必须存在且在根内
            for x in cli:
                _check_rel_inside(root, x, f"entry.cli[{x!r}]", errors)

    health = data.get("health")
    if not isinstance(health, dict):
        errors.append("health 必须为对象（{type: command|config_key, ...}）")
    else:
        htype = health.get("type")
        if htype not in ("command", "config_key"):
            errors.append(f"health.type 必须为 command|config_key，得到 {htype!r}")
        elif htype == "command":
            cmd = health.get("command")
            if not isinstance(cmd, str) or not cmd.strip():
                errors.append("health.type=command 须给出 command（插件根内相对脚本路径，可带参数）")
            else:
                try:
                    argv = shlex.split(cmd)
                except ValueError as e:
                    errors.append(f"health.command 无法解析: {e}")
                    argv = []
                if argv:
                    # 首 token = 脚本路径（白名单根校验）；其余为参数原样保留
                    _check_rel_inside(root, argv[0], "health.command 脚本路径", errors)
        else:
            key = health.get("key")
            if not isinstance(key, str) or not key.strip():
                errors.append("health.type=config_key 须给出 key（config 键名）")

    requires = data.get("requires")
    if not isinstance(requires, dict):
        errors.append("requires 必须为对象（core/binaries/python_packages/platforms）")
    else:
        if "core" in requires:
            error = core_requirement_error(requires["core"])
            if error:
                errors.append(error)
        for field in ("binaries", "python_packages", "platforms"):
            v = requires.get(field)
            if v is None:
                continue
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                errors.append(f"requires.{field} 必须为字符串数组")

    config_keys = data.get("config_keys")
    if not isinstance(config_keys, list) or not all(isinstance(x, str) for x in config_keys):
        errors.append("config_keys 必须为字符串数组")

    docs = data.get("docs")
    if not isinstance(docs, str) or not docs.strip():
        errors.append("docs 必须为非空字符串（README 相对路径）")
    else:
        _check_rel_inside(root, docs, "docs", errors)

    if kind == "plugin":
        extends = data.get("extends")
        if not isinstance(extends, dict):
            errors.append("plugin 额外要求 extends 对象（schema_keys/hooks）")
        else:
            schema_keys = extends.get("schema_keys")
            if not isinstance(schema_keys, list):
                errors.append("extends.schema_keys 必须为数组 [{path, key}]")
            else:
                for i, sk in enumerate(schema_keys):
                    if (not isinstance(sk, dict) or not isinstance(sk.get("path"), str)
                            or not isinstance(sk.get("key"), str)):
                        errors.append(f"extends.schema_keys[{i}] 必须为 {{path, key}} 字符串对")
            hooks = extends.get("hooks")
            if not isinstance(hooks, list) or not all(isinstance(x, str) for x in hooks):
                errors.append("extends.hooks 必须为字符串数组")
            else:
                for x in hooks:
                    if x not in KNOWN_HOOKS:
                        errors.append(f"extends.hooks 值 {x!r} 不在已冻结扩展点 {KNOWN_HOOKS}")
    return errors


def load_manifest(dir_path: Path):
    """读 <dir>/plugin.json 或 app.json 并校验 → (manifest|None, errors)。

    坏 JSON / 缺文件 / 顶层非对象 → errors 非空不崩（doctor 与 registry 的完整
    错误清单语义）。kind 决定首选文件名，两者都存在时 kind 对应者优先。
    """
    dir_path = Path(dir_path)
    candidates = []
    for probe in dir_path.glob("*.json"):
        if probe.name in ("plugin.json", "app.json"):
            candidates.append(probe)
    if not candidates:
        return None, [f"缺少 manifest（{dir_path}/plugin.json 或 app.json）"]
    f = sorted(candidates,
               key=lambda p: 0 if p.name == "plugin.json" else 1)[0]
    # A03 追加修复（2026-10-13）：manifest 文件本身是指向插件根外的符号链接时，
    # 数据源已出根（后续字段校验只认「根内相对路径」，对此全盲）。与
    # _check_rel_inside 同一白名单根语义：f.resolve() 必须落在 dir_path.resolve()
    # 内——根内符号链接（如 plugin.json -> manifest.real.json）仍放行；断链指向
    # 根外时 resolve 出根在此拒绝，指向根内则由下方 read_text 的 OSError 拒绝。
    root_real = dir_path.resolve()
    f_real = f.resolve()
    if not f_real.is_relative_to(root_real):
        return None, [f"manifest 文件符号链接解析后落在插件根外（安全节 §2.4）: "
                      f"{f} -> {f_real}"]
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, [f"manifest 解析失败（{f.name}）: {e}"]
    errors = validate_manifest(dir_path, data)
    return (None, errors) if errors else (data, [])
