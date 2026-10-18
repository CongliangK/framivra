#!/usr/bin/env python3
"""independence —— ASR 独立性单一接口（issue #141：删除全部 IME 复用链；
issue #142：enabled 门禁 + 进程归属三因子 + 通用 GPU 资源准入）

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


# ---------------------------------------------------------------- enabled 门禁（issue #142）

#: asr.enabled 严格真值集合——其余任何值（缺键/None/坏配置/"maybe"/0/false…）
#: 一律视为禁用（安全默认，issue #142 矩阵「禁用（含未配置/坏配置）」）
ENABLED_TRUTHY = ("1", "true", "yes", "on")


def asr_enabled(cfg):
    """asr.enabled 严格解析：只有显式 true/1/yes/on（含布尔 True）才启用。
    未配置/类型不符/坏值 → False（fail-closed 安全默认）。"""
    if not isinstance(cfg, dict):
        return False
    asr = cfg.get("asr")
    if not isinstance(asr, dict):
        return False
    v = asr.get("enabled")
    if v is True:
        return True
    if isinstance(v, str) and v.strip().lower() in ENABLED_TRUTHY:
        return True
    return False


def require_enabled(cfg, action="该操作"):
    """计算/启动类入口的 enabled 门禁（issue #142）：禁用态不发请求、不拉起进程、
    不预热、不转写、不加载模型。只读 health/status、init 准备（bash
    apps/asr/init.sh，含 --once-health 冒烟）与自有服务 stop 不走此门
    （语义见 README「enabled 门禁语义表」）。违规 raise。"""
    if not asr_enabled(cfg):
        raise AsrIndependenceError(
            "asr.enabled 未启用——%s 被拒绝（issue #142 门禁：禁用态不探测/不启动/"
            "不预热/不转写/不加载模型；未配置/坏配置一律视为禁用）。"
            "只读 sidecar health/status、init 准备与自有服务 stop 不受影响。"
            "启用：config.local.json 写 \"asr\": {\"enabled\": true}" % action)
    return True


# ---------------------------------------------------------------- 通用资源准入（issue #142）

#: 各档模型显存预算（MiB，保守粗粒度：权重 bf16 + 对齐器 + 激活/碎片余量）。
#: 与输入法实现完全无关——只读 nvidia-smi 的总量/占用量，绝不探测/停止/卸载
#: 任何外部服务（含语音输入法）来腾显存。
VRAM_BUDGET_MIB = {"1.7b": 6144, "0.6b": 3072}
VRAM_BUDGET_DEFAULT_MIB = 6144
NVIDIA_SMI = "nvidia-smi"


def estimate_vram_mib(model_dir):
    """按模型目录名估计显存预算（MiB）；asr.vram_budget_mib 配置优先于估计。"""
    name = str(Path(model_dir).name).lower()
    if "0.6" in name or "0_6" in name:
        return VRAM_BUDGET_MIB["0.6b"]
    return VRAM_BUDGET_MIB["1.7b"]


def _run_nvidia_smi():
    """只读 nvidia-smi 查询（每块 GPU 一行 total/used MiB）→ 行列表；不可用/
    失败/任一行畸形 → None（fail-closed，与既有畸形输出语义一致）。
    零写态、零信号、零外部服务操作。"""
    try:
        r = subprocess.run(
            [NVIDIA_SMI, "--query-gpu=memory.total,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    rows = []
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            return None
        try:
            total, used = int(parts[0]), int(parts[1])
        except ValueError:
            return None
        if total <= 0 or used < 0 or used > total:
            return None
        rows.append({"total_mib": total, "used_mib": used,
                     "free_mib": total - used})
    return rows or None


def _parse_device_index(dev):
    """解析 device 字符串（小写）→ (gpu_index, explicit)；解析失败 → None。
      - "cuda" / "mps"（无索引）→ (0, False)：维持首卡语义（沿用历史行为：
        device_map 不带索引时显存门只看首块 GPU——与 accelerate/transformers
        对无索引 cuda 的默认设备一致）；
      - "cuda:N" / "mps:N" → (N, True)：按 N 查对应 GPU 行；
      - "cuda:x" / "cuda:-1" / "cuda:" 等畸形 → None：fail-closed。"""
    head, sep, tail = dev.partition(":")
    if not sep:
        return (0, False)
    if not tail or not tail.isdigit():
        return None
    return (int(tail), True)


def gpu_admission(required_mib, device="cuda:0"):
    """通用 GPU/显存准入（issue #142，与 IME 端点零依赖）。返回决策 dict：
      {allowed, reason, free_mib, total_mib, required_mib, tool}
    语义（fail-closed 且可诊断）：
      - device 为 cpu* → 允许，reason=CPU-only 显存门 N/A（无 GPU 常驻需求）；
      - device 解析失败（如 cuda:x / cuda:-1）→ 拒绝：无法确定目标 GPU 时
        绝不拿首卡数据冒充准入依据；
      - device 带索引（cuda:N）→ 严格按第 N 块 GPU 的 free 判定；索引越界
        （N ≥ nvidia-smi GPU 行数）→ 拒绝（fail-closed，与畸形输出语义一致）；
      - device 无索引（cuda / mps）→ 首卡语义（见 _parse_device_index 注释）；
      - nvidia-smi 不可用/不可读 → 拒绝：无法验证显存预算时绝不加载 GPU 模型，
        不拿“先加载到 OOM 再换小模型”当唯一保护；可诊断地提示改 CPU；
      - free < required → 拒绝：显存不足安全拒绝（绝不停止/卸载任何外部进程，
        含语音输入法；排队/降档由调用方显式选择）。"""
    required_mib = int(required_mib or 0)
    dev = str(device or "").lower()
    if dev.startswith("cpu"):
        return {"allowed": True,
                "reason": "CPU-only 模式：显存门 N/A（无 GPU 常驻需求）",
                "free_mib": None, "total_mib": None,
                "required_mib": None, "tool": None}
    parsed = _parse_device_index(dev)
    if parsed is None:
        return {"allowed": False,
                "reason": "GPU device 字符串无法解析（%r）：fail-closed 拒绝——"
                          "无法确定目标 GPU 时绝不以首卡数据冒充准入依据。"
                          "改用 cuda / cuda:N / mps / cpu 等合法形态"
                          % (device,),
                "free_mib": None, "total_mib": None,
                "required_mib": required_mib, "tool": "device-parse(失败)"}
    gpu_index, explicit = parsed
    rows = _run_nvidia_smi()
    if rows is None:
        return {"allowed": False,
                "reason": "GPU 显存门不可用（nvidia-smi 不存在/不可读/超时）——"
                          "fail-closed：无法验证显存预算时拒绝加载 GPU 模型，"
                          "不以“先 OOM 再换小模型”当唯一保护。"
                          "改 CPU 运行：--device-map cpu 或 config asr.device_map=\"cpu\"",
                "free_mib": None, "total_mib": None,
                "required_mib": required_mib, "tool": "nvidia-smi(不可用)"}
    if gpu_index >= len(rows):
        return {"allowed": False,
                "reason": "GPU 索引越界：device=%r 指向第 %d 块 GPU，但 nvidia-smi "
                          "只报告 %d 块——fail-closed 拒绝（与畸形输出语义一致），"
                          "绝不回退到其他卡的数据"
                          % (device, gpu_index, len(rows)),
                "free_mib": None, "total_mib": None,
                "required_mib": required_mib, "tool": NVIDIA_SMI}
    info = rows[gpu_index]
    which = ("GPU%d" % gpu_index if explicit
             else "GPU%d（device 无索引，首卡语义）" % gpu_index)
    if info["free_mib"] < required_mib:
        return {"allowed": False,
                "reason": "显存不足：%s free %d MiB < 预算 %d MiB（total %d / used %d）——"
                          "安全拒绝。绝不为了本项目停止/卸载/重配任何外部服务"
                          "（含语音输入法）；可等待释放后重试、降档 0.6b 或改 CPU"
                          % (which, info["free_mib"], required_mib,
                             info["total_mib"], info["used_mib"]),
                "free_mib": info["free_mib"], "total_mib": info["total_mib"],
                "required_mib": required_mib, "tool": NVIDIA_SMI}
    return {"allowed": True,
            "reason": "显存准入通过：%s free %d MiB ≥ 预算 %d MiB（nvidia-smi 只读查询，"
                      "零外部服务操作）" % (which, info["free_mib"], required_mib),
            "free_mib": info["free_mib"], "total_mib": info["total_mib"],
            "required_mib": required_mib, "tool": NVIDIA_SMI}


def admission_lease():
    """并发准入互斥（issue #142）：项目状态根下 flock 文件，串行化跨进程的
    「查显存 → 加载模型」临界区，防两路并发同时通过同一份空闲显存检查。
    用法：with admission_lease(): ...（获取失败=锁文件不可写时 fail-closed raise）。
    只锁本项目自有状态文件，不触碰任何外部进程。"""
    import fcntl
    path = project_state_root() / "admission.lock"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+")
    except OSError as exc:
        raise AsrIndependenceError(
            "资源准入锁不可写（%s）——fail-closed 拒绝并发加载：%s"
            % (path, exc)) from None

    class _Lease:
        def __enter__(self):
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            return self

        def __exit__(self, *exc):
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()
            return False

    return _Lease()


# ---------------------------------------------------------------- sidecar 进程归属

def pid_file(port):
    """PID 文件落项目状态根（与 IME 的 pid/状态域完全独立）。"""
    return project_state_root() / ("sidecar_%d.pid" % int(port))


def pid_start_time(pid):
    """/proc/<pid>/stat 的 starttime（field 22，时钟嘀嗒）——进程身份的启动时刻
    因子：PID 被内核回收复用后 starttime 必变（issue #142：陈旧 PID/PID 重用
    不得导致向外部进程发信号）。读不到 → None。"""
    try:
        data = (Path("/proc") / str(int(pid)) / "stat").read_text()
    except (OSError, ValueError):
        return None
    tail = data.rpartition(")")[2].split()   # comm 可含空格/括号：取最后 ')' 之后
    try:
        return int(tail[19])                  # tail[0]=state(field3) → field22=index19
    except (IndexError, ValueError):
        return None


def write_pid_record(port, pid, extra=None):
    """写 pid 记录（JSON）：{pid, start_time, port, ...}。启动时刻入档，
    stop/回收前与 /proc 实测比对，PID 重用即失配。"""
    rec = {"pid": int(pid), "start_time": pid_start_time(pid), "port": int(port)}
    if extra:
        rec.update(extra)
    pf = pid_file(port)
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    return pf


def read_pid_record(port):
    """读 pid 记录；兼容 #141 时代的纯数字旧格式（无 start_time → None，
    归属校验退化为 cmdline 单因子并在 stop 时给出升级提示）。损坏 → None。"""
    pf = pid_file(port)
    try:
        raw = pf.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if raw.startswith("{"):
        try:
            rec = json.loads(raw)
        except ValueError:
            return None
        if isinstance(rec, dict) and isinstance(rec.get("pid"), int):
            return rec
        return None
    try:
        return {"pid": int(raw), "start_time": None, "port": int(port), "legacy": True}
    except ValueError:
        return None


def pid_owns_sidecar(pid, port, start_time=None):
    """pid 是否真的是本项目 sidecar——三因子归属（issue #142）：
    ① /proc cmdline 同时含 sidecar_server.py 与端口；② pid 存活；
    ③ 记录在案的 start_time 与 /proc 实测一致（PID 重用防御；旧格式记录
    无 start_time 时跳过该因子并在调用方提示升级）。
    无法证明即 False——绝不向无法归属的进程发信号。"""
    if not pid or int(pid) <= 1:
        return False
    pid = int(pid)
    cmdline_file = Path("/proc") / str(pid) / "cmdline"
    try:
        raw = cmdline_file.read_bytes()
    except OSError:
        return False
    parts = [p.decode("utf-8", "replace") for p in raw.split(b"\0") if p]
    joined = " ".join(parts)
    if not ("sidecar_server.py" in joined
            and (("--port" in parts and str(int(port)) in parts)
                 or ("--port %d" % int(port)) in joined)):
        return False
    if start_time is not None:
        now = pid_start_time(pid)
        if now is None or int(start_time) != now:
            return False
    return True


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
    # issue #142：资源准入键坏值 fail-closed（不静默按默认跑）
    vb = asr.get("vram_budget_mib")
    if vb is not None and (isinstance(vb, bool)
                           or not isinstance(vb, (int, float))
                           or vb <= 0):
        raise AsrIndependenceError(
            "asr.vram_budget_mib 必须是正数（MiB），当前值：%r" % (vb,))
    dm = asr.get("device_map")
    if dm is not None and not isinstance(dm, str):
        raise AsrIndependenceError("asr.device_map 必须是字符串（如 cuda:0 / cpu），"
                                   "当前值：%r" % (dm,))


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
