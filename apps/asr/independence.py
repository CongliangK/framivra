#!/usr/bin/env python3
"""independence —— ASR 独立性单一接口（issue #141：删除全部 IME 复用链）

用途：所有 ASR 入口（asr_cli / client.ensure_sidecar / stop_sidecar / sidecar_server
绑定 / init.sh / vt.py doctor）在使用任何外部资产（解释器、模型、URL、进程、信号）
**之前**调用本模块做 fail-closed 校验。任何情况下禁止复用语音输入法（IME）。

铁律（父门禁 #140）：
  - 不探测/连接 IME health/warm/transcribe（18081 一个字节都不发）；
  - 不调用 IME CLI/DBus/systemd，不启停/发信号；
  - 不把「端口上有健康服务」当作可复用——服务身份必须可归属到本项目；
  - 旧键 VT_ASR_IME_ROOT / asr.ime_root 明确报迁移错误，绝不静默转成别的来源；
  - 显式 asr.python / model_dir / aligner_dir / base_url 同样过校验，不能绕过。

项目独立资产布局（与 IME 完全无关的根）：
  - 解释器环境根：apps/asr/.venv（仓内，安装/升级/卸载只作用于该边界）；
  - 模型资产根：~/.local/share/framivra/asr/models（独立可校验，含来源清单）；
  - 运行状态根：~/.local/state/framivra/asr（PID/日志，独立于 IME 的任何目录）。

资产固定化接口（真实下载在关联工单，本模块只固化接口）：
  load_asset_manifest() / verify_asset_entry() —— 记录并校验每个模型目录的
  版本（revision）、来源（source URL）、逐文件 SHA256；权重文件硬链接
  （st_nlink>1）视为借用他人资产，拒绝。

CLI：python3 apps/asr/independence.py check-config [--json <config.local.json>]
     退出码 0=独立合规 / 2=拒绝（中文诊断到 stderr）。
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

ASR_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------- 常量（唯一事实源）

#: 遗留 IME 环境变量 / 配置键——出现即迁移错误，绝不静默换算
LEGACY_ENV = "VT_ASR_IME_ROOT"
LEGACY_CONFIG_KEY = "asr.ime_root"

#: IME sidecar 专用端口——任何 base_url/绑定都不允许指向它（拒绝即 fail-closed，
#: 不是探测它的存活）
IME_PORT = 18081

#: 已知 IME 仓根锚点（含历史搬家路径；~ 展开后 resolve）。落入其下的路径
#: = IME 资产 = 拒绝。这是所有权边界，不是目录名黑名单。
IME_ROOT_ANCHORS = (
    "~/文档/01_活跃项目/type/ibus-voice-ime-github",
    "~/文档/type/ibus-ime",
)

#: 项目模型资产根（独立下载/校验/版本记录的落点；~ 展开）
PROJECT_MODEL_ROOT = "~/.local/share/framivra/asr/models"
#: 项目运行状态根（PID/日志；与 IME 的任何状态目录无关）
PROJECT_STATE_ROOT = "~/.local/state/framivra/asr"
#: sidecar 自报协议版本（client 复用归属校验用，与 sidecar_server.PROTOCOL_VERSION 同步）
PROTOCOL_VERSION = 1

#: 传给自有解释器时必须剥离的外部注入变量（PYTHONPATH/PYTHONHOME/user-site 等）
SANDBOX_STRIP_VARS = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
                      "PYTHONUSERBASE", "PYTHONNOUSERSITE", "PYTHONWARNINGS")


class AsrIndependenceError(RuntimeError):
    """独立性校验失败（fail-closed）。message 为中文可行动诊断。"""


# ---------------------------------------------------------------- 根路径

def project_env_root():
    """项目专属解释器环境根：apps/asr/.venv（仓内边界）。"""
    return ASR_DIR / ".venv"


def project_model_root():
    return Path(PROJECT_MODEL_ROOT).expanduser()


def project_state_root():
    return Path(PROJECT_STATE_ROOT).expanduser()


def ime_anchor_roots():
    """已知 IME 仓根锚点（resolve 后）。"""
    return [Path(p).expanduser().resolve() for p in IME_ROOT_ANCHORS]


# ---------------------------------------------------------------- 路径独立性

def _is_relative_to(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def check_path_independent(path, what):
    """路径不得指向 IME 资产（resolve 追真身，symlink 逃逸无效）。
    返回 resolve 后路径字符串；违规 raise AsrIndependenceError。"""
    if path is None:
        raise AsrIndependenceError("%s：路径为空" % what)
    resolved = Path(str(path)).expanduser()
    try:
        resolved = resolved.resolve()
    except OSError as exc:
        raise AsrIndependenceError("%s：路径无法解析（%s）：%s" % (what, exc, path)) from None
    for anchor in ime_anchor_roots():
        if resolved == anchor or _is_relative_to(resolved, anchor):
            raise AsrIndependenceError(
                "%s 指向语音输入法资产，已拒绝（任何情况下禁止复用输入法）：%s\n"
                "  迁移：删除配置中的 IME 路径，运行 bash apps/asr/init.sh 建立 Framivra "
                "专属环境与模型（默认根 %s）" % (what, resolved, project_model_root()))
    return str(resolved)


# ---------------------------------------------------------------- 遗留配置拒绝

def reject_legacy_config(cfg=None):
    """旧键出现即迁移错误（fail-closed，不静默转成其他路径来源）。"""
    env = os.environ.get(LEGACY_ENV)
    if env:
        raise AsrIndependenceError(
            "环境变量 %s 已废除（ASR 与语音输入法彻底解耦）：请 unset 后重试；"
            "Framivra 专属资产用 bash apps/asr/init.sh 建立或 config asr.model_dir 指定"
            % LEGACY_ENV)
    if cfg is not None:
        node = cfg
        for key in LEGACY_CONFIG_KEY.split("."):
            if not isinstance(node, dict) or key not in node:
                node = None
                break
            node = node[key]
        if node is not None:
            raise AsrIndependenceError(
                "配置键 %s 已废除（ASR 与语音输入法彻底解耦）：请从 config 删除该键；"
                "Framivra 专属资产用 bash apps/asr/init.sh 建立或 config asr.model_dir 指定"
                % LEGACY_CONFIG_KEY)


# ---------------------------------------------------------------- base_url 校验（请求前）

def validate_base_url(base_url):
    """请求/绑定前校验 base_url：scheme 合法、不指向 IME 端口 18081。
    只做 urlparse 静态校验，零 HTTP 请求、零 DNS 探测——绝不能靠试错发请求
    验证身份（localhost 别名/hosts 重定向指到 18081 一律按端口拒绝，不依赖
    域名形态）。违规 raise。"""
    if not base_url or not isinstance(base_url, str):
        raise AsrIndependenceError("asr.base_url 缺失或非字符串")
    u = urlparse(base_url)
    if u.scheme != "http" or not u.hostname:
        raise AsrIndependenceError(
            "asr.base_url 必须是 http://host:port 形式：%s" % base_url)
    try:
        port = u.port or 80
    except ValueError as exc:
        raise AsrIndependenceError("asr.base_url 端口非法（%s）：%s" % (exc, base_url)) from None
    if port == IME_PORT:
        raise AsrIndependenceError(
            "asr.base_url 指向端口 %d——这是语音输入法 sidecar 专用端口，"
            "任何情况下禁止复用；本项目 sidecar 默认 127.0.0.1:18085（%s）"
            % (IME_PORT, base_url))
    return base_url


# ---------------------------------------------------------------- 解释器（依赖来源验证）

def sandbox_env():
    """传给自有解释器的环境：剥离外部 Python 注入（PYTHONPATH/PYTHONHOME/
    user-site/.pth 经由 PYTHON* 变量的注入面）。配合解释器 -I（isolated mode）。"""
    env = dict(os.environ)
    for var in SANDBOX_STRIP_VARS:
        env.pop(var, None)
    return env


def interpreter_module_origin(py_bin, module):
    """在 py_bin 内 import module 并返回其文件真实路径；失败 None。
    依赖来源验证的底料：不仅看 import 成功，还要看模块来自哪里。"""
    code = ("import %s,sys;sys.stdout.write(%s.__file__ or '')" % (module, module))
    try:
        r = subprocess.run([str(py_bin), "-I", "-c", code],
                           capture_output=True, timeout=30, env=sandbox_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    origin = r.stdout.decode("utf-8", "replace").strip()
    return str(Path(origin).resolve()) if origin else None


def verify_interpreter(py_bin, what="asr.python"):
    """解释器路径独立性 + 依赖来源双重校验，返回 resolve 路径。
    依赖来源：qwen_asr 必须装在该解释器自身的环境根内（venv/conda 环境边界），
    而不是 user-site、外部 PYTHONPATH 或 IME site-packages。"""
    resolved = check_path_independent(py_bin, what)
    p = Path(resolved)
    if not p.exists():
        raise AsrIndependenceError("%s 不存在：%s（先跑 bash apps/asr/init.sh 建立专属环境）"
                                   % (what, resolved))
    if not os.access(str(p), os.X_OK):
        raise AsrIndependenceError("%s 不可执行：%s" % (what, resolved))
    return resolved


def verify_module_from_own_env(py_bin, module, what="解释器"):
    """module 必须来自 py_bin 自身环境根（其上两级目录）之内。"""
    origin = interpreter_module_origin(py_bin, module)
    if origin is None:
        raise AsrIndependenceError(
            "%s 内 %s 不可导入（先 bash apps/asr/init.sh --download 安装到专属环境，"
            "绝不借用输入法环境）：%s" % (what, module, py_bin))
    env_root = Path(py_bin).resolve().parent.parent
    origin_path = Path(origin)
    for anchor in ime_anchor_roots():
        if origin_path == anchor or _is_relative_to(origin_path, anchor):
            raise AsrIndependenceError(
                "%s 的 %s 实际来自语音输入法 site-packages（%s）——已拒绝；"
                "请安装到 Framivra 专属环境" % (what, module, origin))
    if not _is_relative_to(origin_path, env_root):
        raise AsrIndependenceError(
            "%s 的 %s 来自其环境根之外（%s；疑似 user-site/外部注入）——已拒绝"
            % (what, module, origin))
    return origin


# ---------------------------------------------------------------- 模型资产（来源/链接关系）
#
# 红队四轮（N1/N1b/N4）判定：任何「权重文件名白名单」结构性不完备——分片
# （model-*-of-*.safetensors）、aligner 别名（pytorch_model.bin/.pt/.ckpt）都不
# 在白名单内。因此目录级审计不再枚举文件名，而是审计**目录内每一个条目**的
# 链接关系与文件类型，另按 index.json 的 weight_map 展开分片逐一确认。

#: 兼容保留（旧引用面）：权重文件名提示，仅用于诊断文案，不再作为枚举边界
WEIGHT_FILES = ("model.safetensors", "model.safetensors.index.json")

_INDEX_NAME = "model.safetensors.index.json"
_SINGLE_WEIGHT_NAME = "model.safetensors"
_WEIGHT_SUFFIXES = (".safetensors", ".bin", ".pt", ".pth", ".ckpt", ".onnx")


def _is_weight_name(name):
    """按形态（而非固定白名单）识别权重承载文件名。"""
    return (name == _INDEX_NAME or name == _SINGLE_WEIGHT_NAME
            or name.endswith(_WEIGHT_SUFFIXES)
            or ".safetensors." in name)   # 分片 model-00001-of-00001.safetensors 等


def _in_ime_anchor(real):
    for anchor in ime_anchor_roots():
        if real == anchor or _is_relative_to(real, anchor):
            return True
    return False


def _reject_entry(resolved_dir, what, name, reason):
    raise AsrIndependenceError(
        "%s 的 %s %s——已拒绝（任何情况下禁止借用输入法/他人权重）；"
        "请独立下载（bash apps/asr/init.sh --download）" % (what, name, reason))


def _audit_single_entry(d, what, name, is_weight):
    """审计目录内一个条目（lstat 级）。违规 raise，正常返回 True（条目存在）。"""
    f = Path(d) / name
    try:
        st = f.lstat()
    except OSError as exc:
        raise AsrIndependenceError(
            "%s 的 %s 无法读取链接信息（%s）——fail-closed 已拒绝"
            % (what, name, exc)) from None
    import stat as _stat
    if (_stat.S_ISFIFO(st.st_mode) or _stat.S_ISCHR(st.st_mode)
            or _stat.S_ISBLK(st.st_mode) or _stat.S_ISSOCK(st.st_mode)):
        _reject_entry(d, what, name, "是特殊文件（FIFO/设备/socket）")
    if _stat.S_ISLNK(st.st_mode):
        try:
            real = f.resolve()
        except OSError as exc:
            raise AsrIndependenceError(
                "%s 的 %s 符号链接无法解析（%s）——fail-closed 已拒绝"
                % (what, name, exc)) from None
        if _in_ime_anchor(real):
            _reject_entry(d, what, name, "符号链接指向语音输入法资产（%s）" % real)
        if is_weight:
            _reject_entry(d, what, name, "是符号链接（指向 %s）" % real)
        return True
    if _stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
        # 保守：模型目录里合法多链接场景不存在，任何多链接常规文件都拒
        _reject_entry(d, what, name, "硬链接数 %d>1（疑似借用输入法/他人权重）"
                      % st.st_nlink)
    return True


def verify_dir_assets(resolved_dir, what, required=True):
    """目录级资产链接审计（#141 五轮结构修法，取代文件名白名单）。

    对目录内**每一个**条目：
      - lstat：symlink → resolve 追真身，落入 IME 锚点即拒；权重承载形态的
        symlink 不论真身一律拒（D3/N4）；
      - st_nlink>1 的常规文件 → 拒（D1/D2：硬链接借用；保守面）；
      - FIFO/设备/socket 等特殊文件 → 拒（红队 N3 观察项收口）；
      - OSError 一律 fail-closed。
    model.safetensors.index.json 若存在且可解析 → 按 weight_map 展开全部分片
    文件名，逐一确认在目录内且通过上述检查（缺分片/指向目录外路径即拒）。
    required=True 时目录必须具备权重承载形态（单文件或 index.json+分片）。
    返回 None，违规 raise。"""
    d = Path(resolved_dir)
    if not d.is_dir():
        raise AsrIndependenceError("%s 不是目录：%s" % (what, d))
    try:
        names = [e.name for e in os.scandir(d)]
    except OSError as exc:
        raise AsrIndependenceError(
            "%s 目录无法枚举（%s）——fail-closed 已拒绝" % (what, exc)) from None
    single_ok = False
    for name in names:
        _audit_single_entry(d, what, name, is_weight=_is_weight_name(name))
        if name == _SINGLE_WEIGHT_NAME:
            single_ok = True
    index_ok = False
    if _INDEX_NAME in names:
        idx = d / _INDEX_NAME
        try:
            data = json.loads(idx.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AsrIndependenceError(
                "%s 的 %s 无法解析（%s）——fail-closed 已拒绝"
                % (what, _INDEX_NAME, exc)) from None
        wm = data.get("weight_map") if isinstance(data, dict) else None
        if not isinstance(wm, dict) or not wm:
            raise AsrIndependenceError(
                "%s 的 %s 缺少非空 weight_map——fail-closed 已拒绝"
                % (what, _INDEX_NAME))
        for shard in sorted(set(str(v) for v in wm.values())):
            p = Path(shard)
            if p.is_absolute() or ".." in p.parts:
                _reject_entry(d, what, _INDEX_NAME,
                              "的 weight_map 指向目录外路径（%s）" % shard)
            try:
                sp = (d / shard).resolve()
            except OSError as exc:
                raise AsrIndependenceError(
                    "%s 的分片 %s 无法解析（%s）——fail-closed 已拒绝"
                    % (what, shard, exc)) from None
            if not (_is_relative_to(sp, d) or sp.parent == d):
                _reject_entry(d, what, _INDEX_NAME,
                              "的 weight_map 分片逃逸出模型目录（%s）" % shard)
            _audit_single_entry(d, what, shard, is_weight=True)
        index_ok = True
    if required and not (single_ok or index_ok):
        raise AsrIndependenceError(
            "%s 缺少权重承载形态（%s 或 %s+分片）："
            "请独立下载（bash apps/asr/init.sh --download）"
            % (what, _SINGLE_WEIGHT_NAME, _INDEX_NAME))


def verify_weight_links(resolved_dir, what, required=True):
    """兼容别名（旧接入面）：委托目录级审计 verify_dir_assets。"""
    return verify_dir_assets(resolved_dir, what, required=required)


def verify_model_dir(path, what="asr.model_dir", check_weights=False):
    """模型目录独立性 + 链接关系校验，返回 resolve 路径。
    - resolve 追真身：symlink 指进 IME 即拒绝；
    - check_weights=True 时过目录级资产链接审计 verify_dir_assets
      （目录内任一条目 symlink 落入 IME 锚点 / 硬链接 nlink>1 / 特殊文件 /
       weight_map 分片缺失或逃逸均拒绝）。"""
    resolved = check_path_independent(path, what)
    if check_weights:
        verify_dir_assets(resolved, what, required=True)
    return resolved


# ---------------------------------------------------------------- 资产固定化接口（版本/来源/SHA256）

def load_asset_manifest(path):
    """资产来源清单：[{name, revision, source, sha256: {relpath: hex}}, ...]。
    真实下载在关联工单；本接口固定「版本/来源/哈希」的记账与校验形态。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = data.get("assets") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise AsrIndependenceError("资产清单格式错误（应为列表或 {\"assets\": []}）：%s" % path)
    return entries


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def verify_asset_entry(entry, model_root):
    """按清单条目校验已就位模型：目录独立性 + 逐文件 SHA256。"""
    name = entry.get("name") or ""
    d = Path(model_root).expanduser() / name
    resolved = verify_model_dir(str(d), "资产 %s" % (name or "<未命名>"), check_weights=bool(entry.get("sha256")))
    bad = []
    for rel, expect in (entry.get("sha256") or {}).items():
        f = Path(resolved) / rel
        if not f.is_file():
            bad.append("%s 缺失" % rel)
        elif sha256_file(f) != expect:
            bad.append("%s 哈希不符" % rel)
    if bad:
        raise AsrIndependenceError("资产 %s 校验失败：%s（来源 %s）"
                                   % (name, "；".join(bad), entry.get("source", "unknown")))
    return resolved


# ---------------------------------------------------------------- sidecar 进程归属

def pid_file(port):
    """PID 文件落项目状态根（与 IME 的 pid/状态域完全独立）。"""
    return project_state_root() / ("sidecar_%d.pid" % int(port))


def pid_owns_sidecar(pid, port):
    """pid 是否真的是本项目 sidecar（Linux /proc cmdline 双因子：脚本名 + 端口）。
    无法证明即 False——绝不向无法归属的进程发信号。"""
    if not pid or int(pid) <= 1:
        return False
    cmdline_file = Path("/proc") / str(int(pid)) / "cmdline"
    try:
        raw = cmdline_file.read_bytes()
    except OSError:
        return False
    parts = [p.decode("utf-8", "replace") for p in raw.split(b"\0") if p]
    joined = " ".join(parts)
    return ("sidecar_server.py" in joined
            and (("--port" in parts and str(int(port)) in parts)
                 or ("--port %d" % int(port)) in joined))


# ---------------------------------------------------------------- 总 preflight

def preflight_config(cfg):
    """所有 ASR 入口的统一前置校验（请求/绑定/拉起/下载之前调用）：
    遗留键拒绝 + 已配置的 python/model_dir/aligner_dir/base_url 逐项校验。
    校验只读配置与文件系统元数据，零 HTTP、零进程启动（除解释器 import 探测
    仅当 asr.python 已配置时执行）。"""
    reject_legacy_config(cfg)
    if not isinstance(cfg, dict):
        return
    asr = cfg.get("asr") if isinstance(cfg.get("asr"), dict) else {}
    py = asr.get("python")
    if py:
        verify_interpreter(py)
    for key in ("model_dir", "aligner_dir"):
        v = asr.get(key)
        if v:
            check_path_independent(v, "asr.%s" % key)
    if asr.get("base_url"):
        validate_base_url(asr["base_url"])


# ---------------------------------------------------------------- CLI

def _read_cfg(path):
    if path and Path(path).is_file():
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def main(argv=None):
    ap = argparse.ArgumentParser(description="ASR 独立性校验（issue #141：禁止复用语音输入法）")
    ap.add_argument("action", choices=["check-config"])
    ap.add_argument("--json", default=None, help="config.local.json 路径（默认环境 VTX_CONFIG > 仓根）")
    args = ap.parse_args(argv)
    cfg_path = args.json or os.environ.get("VTX_CONFIG") or str(ASR_DIR.parent.parent / "config.local.json")
    try:
        preflight_config(_read_cfg(cfg_path))
    except AsrIndependenceError as exc:
        print("✗ ASR 独立性校验失败：%s" % exc, file=sys.stderr)
        return 2
    print("✓ ASR 配置独立性校验通过（无输入法复用）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
