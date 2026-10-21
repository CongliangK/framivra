"""config 统一加载（§5.3，设计权威 docs/ARCHITECTURE.md）。

四重复制收敛的单一入口（原各自实现：asr/client.py、tts/tts_cli.py、tts/spec.py、
imgen/imgen_cli.py；init.py 的读取随 vt.py 吸收）。只统一**加载入口**——各消费方
的键结构与缺省探测链不变。

语义（对抗审查 P0 复审 P2-3 全局化裁定）：
  - 路径：VTX_CONFIG 环境变量 > repo 根 config.local.json；
  - 坏 JSON：一律 **警告不阻断**、按空 {} 继续（asr 现行为推广全局——tts/imgen
    原静默 {} 收敛到此单一语义）；缺文件不警告（走默认探测链是常态）；
  - ~ 展开：**已知路径型键清单**自动展开（清单 = config.example.json 的路径型
    键：asr.python/model_dir/aligner_dir、tts.repo/python/voice_ref/edge_bin）。
    裁定：对全部字符串值做 ~ 展开的键语义过宽（URL/文本值会被误伤），改为
    清单内自动展开 + expand_path() 工具供其余键自理（消费方调用点的 expanduser
    幂等无害，保留不动）；
  - 不读取旧组件配置环境变量；各 app 使用同一配置路径规则。
"""
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# 已知路径型键（自动 ~ 展开；与 config.example.json 的路径型键同步维护）。
# asr.ime_root 已废除（issue #141：ASR 与输入法彻底解耦），不再收录。
PATH_KEYS = ("asr.python", "asr.model_dir", "asr.aligner_dir",
             "tts.repo", "tts.python", "tts.voice_ref", "tts.edge_bin",
             "bgm.repo", "bgm.cli")   # P6：bgm-render 插件 CLI 解析的两路径键


def expand_path(value):
    """~ 展开（非字符串原样返回；expanduser 对已是绝对路径的串幂等）。"""
    if isinstance(value, str):
        return os.path.expanduser(value)
    return value


def config_path(repo_root=None) -> Path:
    """生效配置路径：VTX_CONFIG 环境变量 > repo 根 config.local.json。"""
    override = os.environ.get("VTX_CONFIG")
    if override:
        return Path(override).expanduser()
    return (Path(repo_root) if repo_root else REPO_ROOT) / "config.local.json"


def cfg_get(cfg, path: str, default):
    """逐级容错读（asr/tts/imgen §7.2 同款语义的单一实现）：
    缺键/None → default；布尔容错（"false"/false 一致处理）；类型不符
    （默认 str 实得非 str、或 bool 污染非布尔键）→ default。
    例：cfg_get(cfg, "imgen.api_base", "https://...")。"""
    node = cfg
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    if node is None:
        return default
    if isinstance(default, bool):  # 布尔容错："false"/false 一致处理
        if isinstance(node, str):
            return node.strip().lower() in ("1", "true", "yes", "on")
        return bool(node)
    if isinstance(default, str) and not isinstance(node, str):
        return default      # 类型不符 → 默认（P2-3）
    if isinstance(node, bool):
        return default      # bool 污染非布尔键 → 默认
    return node


def load_config(repo_root=None) -> dict:
    """统一加载入口（路径、容错与展开语义见模块头）。"""
    path = config_path(repo_root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"⚠ 配置解析失败（{path}: {e}），按空配置继续", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        print(f"⚠ 配置顶层不是 JSON 对象（{path}），按空配置继续", file=sys.stderr)
        return {}
    # 已知路径型键 ~ 展开（就地；清单见模块头裁定）
    for dotted in PATH_KEYS:
        node = data
        for part in dotted.split(".")[:-1]:
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, dict):
            k = dotted.split(".")[-1]
            if isinstance(node.get(k), str):
                node[k] = expand_path(node[k])
    return data


def external_entries(cfg: dict, key: str):
    """config 的 external_apps[] / external_plugins[] 规范化 → [{path, enabled}]。

    条目形态：字符串（路径，**默认 disabled**，§2.4 安全节）或
    {"path": ..., "enabled": true}（显式启用）。坏条目保留原样交由 registry
    报错（此函数只做形状规范化，不抛异常）。
    """
    raw = cfg.get(key) if isinstance(cfg, dict) else None
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, str):
            out.append({"path": item, "enabled": False})
        elif isinstance(item, dict) and isinstance(item.get("path"), str):
            out.append({"path": item["path"], "enabled": item.get("enabled") is True})
        else:
            out.append({"path": item, "enabled": False, "malformed": True})
    return out
