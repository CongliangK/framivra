#!/usr/bin/env python3
"""client —— sidecar HTTP 客户端 + 音频抽取（系统 python3 ≥3.9 stdlib 即可跑）

用途：asr_cli 的底层通道——健康探测、按配置自动拉起 sidecar（qwen-asr 解释器）、
/transcribe 调用；音视频输入统一 ffmpeg 抽取 16k mono wav（库内 librosa 自动重采样，
容器不限）。零模型依赖、零切分逻辑（长音频透传：库内建分块，见方案 §3.3）。

用法（平铺 import，vtx 同款手法）：
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import client
    cfg = client.load_config()
    client.ensure_sidecar(cfg)
    resp = client.transcribe(client.cfg_get(cfg, "asr.base_url", client.DEFAULT_BASE_URL),
                             "/tmp/a.wav", timestamps=True)

机制：
  - cfg_get：容错读 config.local.json（§7.2，缺键/类型不符 → 默认值，~ 展开）；
  - 独立性铁律（issue #141/#140：任何情况下禁止复用语音输入法）：一切外部资产
    （解释器/模型/URL/进程/信号）使用前经 independence.py fail-closed 校验——
    旧键 VT_ASR_IME_ROOT / asr.ime_root 报迁移错误；探测链只含 Framivra 专属根
    （解释器 config.asr.python → apps/asr/.venv；模型 config.asr.model_dir →
    ~/.local/share/framivra/asr/models）；零 IME 探测、零共驻降档；
  - 端口上「恰好健康」的服务不可复用：必须 pid 文件 + /proc cmdline 归属到本
    项目 sidecar 才算自己人；stop 只对可归属 pid 发 SIGTERM；
  - sidecar 日志 ~/.local/state/framivra/asr/sidecar.log，pid 文件
    ~/.local/state/framivra/asr/sidecar_<port>.pid（项目状态根，与 IME 独立）；
  - 4xx/5xx → ClientError（带服务端中文 error）；超时 → TranscribeTimeout。

来源：docs/plans/2026-09-11-三大模块整合方案.md v2.1 §3.2/§3.3/§7.2/§9.1。
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ASR_DIR = Path(__file__).resolve().parent
REPO_ROOT = ASR_DIR.parent.parent    # v2 迁移后 asr 在 apps/ 下，仓根 = 上两级
DEFAULT_BASE_URL = "http://127.0.0.1:18085"
SIDECAR_LOG = Path("~/.local/state/framivra/asr/sidecar.log").expanduser()
TRANSCRIBE_TIMEOUT_DEFAULT = 1800.0     # 长音频（1200s 上限 × RTF）也要能跑完

sys.path.insert(0, str(ASR_DIR))
import independence    # noqa: E402  独立性单一接口（issue #141）


class ClientError(Exception):
    """sidecar 返回 4xx/5xx（message 为服务端中文可行动信息）。"""

    def __init__(self, status, message):
        super().__init__("[%d] %s" % (status, message))
        self.status = status
        self.message = message


class TranscribeTimeout(Exception):
    pass


# ---------------------------------------------------------------- config（§7.2 容错）

def cfg_get(cfg, path, default):
    """cfg_get(cfg, "asr.base_url", 默认)——逐级 get，缺键/None → default；
    布尔容错："false"/false 一致处理（§7.2）。"""
    node = cfg
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    if node is None:
        return default
    if isinstance(default, bool):
        if isinstance(node, str):
            return node.strip().lower() not in ("0", "false", "no", "off")
        return bool(node)
    if isinstance(default, str) and not isinstance(node, str):
        return default      # 类型不符（如 python=99）→ 默认（P2-3，防探测链 TypeError）
    if isinstance(node, bool):
        return default      # bool 污染非布尔键（true 进数字/字符串键）→ 默认
    return node


def _expand(p):
    return str(Path(p).expanduser()) if p else p


def _pluginlib_config():
    """vtx.pluginlib.config（v2 §5.3 统一加载）——repo 根追加进 sys.path 后导入
    （本模块平铺布局，脚本目录不含 repo 根；append 不抢占既有解析序）。"""
    if str(REPO_ROOT) not in sys.path:
        sys.path.append(str(REPO_ROOT))
    from vtx.pluginlib import config as _cfg
    return _cfg


def load_config(repo_root=None):
    """仓库配置（v2 §5.3 统一加载入口 vtx/pluginlib/config.py）：
    VTX_CONFIG 环境变量 > repo 根 config.local.json；坏 JSON 一律警告 + 空 {}
    继续不阻断（全局化语义）；已知路径型键（asr.python/model_dir/aligner_dir 等）
    自动 ~ 展开。本模块键结构与探测链不变，只统一加载入口（四重复制收敛之一）。"""
    return _pluginlib_config().load_config(repo_root or REPO_ROOT)


# ---------------------------------------------------------------- 探测链（Framivra 专属根，issue #141）

def py_env(py_bin):
    """为解释器构造环境（issue #141）：剥离外部注入（PYTHONPATH/PYTHONHOME/
    user-site 变量），配合解释器 -I isolated mode——不再为复用外部 venv 补
    PYTHONPATH（那是 IME 复用链的一部分，已删除）。"""
    return independence.sandbox_env()


def _py_imports(py_bin, module):
    r = subprocess.run([str(py_bin), "-I", "-c", "import %s" % module],
                       capture_output=True, env=py_env(py_bin))
    return r.returncode == 0


def probe_python(cfg=None):
    """qwen-asr 解释器：config.asr.python → apps/asr/.venv（Framivra 专属根）。
    无 IME 回退；显式 python 过独立性校验 + 依赖来源验证（qwen_asr 必须装在
    解释器自身环境根内，而非 user-site / IME site-packages）。"""
    cfg = cfg if cfg is not None else load_config()
    independence.reject_legacy_config(cfg)
    cands = [cfg_get(cfg, "asr.python", None),
             independence.project_env_root() / "bin/python"]
    for c in cands:
        if not c:
            continue
        try:
            p = independence.verify_interpreter(_expand(c), "asr.python")
        except independence.AsrIndependenceError:
            continue
        if _py_imports(p, "qwen_asr"):
            try:
                independence.verify_module_from_own_env(p, "qwen_asr")
                return str(p)
            except independence.AsrIndependenceError:
                continue
    return None


def model_roots(cfg=None):
    """模型根目录链：config.asr.model_dir → ~/.local/share/framivra/asr/models
    （Framivra 专属根；无 IME vendor、无共享 ~/models）。"""
    cfg = cfg if cfg is not None else load_config()
    independence.reject_legacy_config(cfg)
    roots = []
    c = cfg_get(cfg, "asr.model_dir", None)
    if c:
        roots.append(independence.check_path_independent(_expand(c), "asr.model_dir"))
    roots.append(str(independence.project_model_root()))
    return roots


def probe_model(cfg=None, alias="1.7b"):
    """探测某档 ASR 模型目录（含 model.safetensors(.index.json)）。"""
    name = "Qwen3-ASR-1.7B" if "1.7" in alias else "Qwen3-ASR-0.6B"
    for root in model_roots(cfg):
        d = Path(root) / name
        if (d / "model.safetensors").exists() or (d / "model.safetensors.index.json").exists():
            return str(d)
    return None


def probe_aligner(cfg=None):
    """探测 ForcedAligner 目录：config.asr.aligner_dir → 项目模型根。"""
    cfg = cfg if cfg is not None else load_config()
    independence.reject_legacy_config(cfg)
    c = cfg_get(cfg, "asr.aligner_dir", None)
    if c:
        p = Path(independence.check_path_independent(_expand(c), "asr.aligner_dir"))
        if p.exists():
            return str(p)
    for root in model_roots(cfg):
        d = Path(root) / "Qwen3-ForcedAligner-0.6B"
        if (d / "model.safetensors").exists() or (d / "model.safetensors.index.json").exists():
            return str(d)
    return None


def pick_model(cfg=None):
    """默认档：1.7B 优先，缺则 0.6B（issue #141：共驻降档链已删除——不做任何
    IME 端口探测；GPU 资源准入属 #142 范畴）。"""
    return probe_model(cfg, "1.7b") or probe_model(cfg, "0.6b")


# ---------------------------------------------------------------- HTTP

def health(base_url, timeout=2.0):
    """GET /health → dict；不通返回 None。"""
    try:
        req = urllib.request.Request(base_url.rstrip("/") + "/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _post_json(base_url, path, payload, timeout):
    req = urllib.request.Request(base_url.rstrip("/") + path,
                                 data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json; charset=utf-8"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
            msg = body.get("error", "")
        except Exception:
            msg = ""
        raise ClientError(exc.code, msg or ("HTTP %d" % exc.code)) from None
    except (TimeoutError, socket.timeout) as exc:
        # urlopen 超时在部分路径裸抛 socket.timeout（不经 URLError 包装）
        raise TranscribeTimeout("sidecar 请求超时（%.0fs）：%s" % (timeout, exc)) from None
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), TimeoutError) or "timed out" in str(exc):
            raise TranscribeTimeout("sidecar 请求超时（%.0fs）：%s" % (timeout, exc)) from None
        raise ConnectionError("sidecar 连接失败（%s）——先 asr_cli sidecar start" % exc) from None


def transcribe(base_url, wav_path, timestamps=False, language=None,
                timeout=TRANSCRIBE_TIMEOUT_DEFAULT, cfg=None):
    """POST /transcribe。长音频不切分直接透传（库内建分块，方案 §3.3 红队 P1-1）。
    issue #142：转写=计算入口，受 asr.enabled 门禁约束（禁用态不发请求）。"""
    cfg = cfg if cfg is not None else load_config()
    independence.require_enabled(cfg, "转写（transcribe）")
    payload = {"audio": str(wav_path)}
    if language:
        payload["language"] = language
    if timestamps:
        payload["timestamps"] = True
    return _post_json(base_url, "/transcribe", payload, timeout)


def warm(base_url, timeout=600.0, cfg=None):
    """POST /warm。issue #142：预热=计算入口，受 asr.enabled 门禁约束。"""
    cfg = cfg if cfg is not None else load_config()
    independence.require_enabled(cfg, "预热（warm）")
    return _post_json(base_url, "/warm", {}, timeout)


# ---------------------------------------------------------------- sidecar 进程管理

def pid_file(port):
    """PID 文件落项目状态根 ~/.local/state/framivra/asr/（issue #141：与 IME
    的任何 pid/状态域完全独立；/tmp 下的旧文件不再是信号依据）。"""
    return independence.pid_file(port)


def _port_of(base_url):
    from urllib.parse import urlparse
    u = urlparse(base_url)
    return u.port or (443 if u.scheme == "https" else 80)


def _sidecar_attribution(base_url):
    """端口上的健康服务是否可归属到本项目 sidecar（三因子，issue #142）：
    pid 记录存在、pid 存活、/proc cmdline 同时含 sidecar_server.py 与端口、
    记录的启动时刻与 /proc 实测一致（PID 重用防御）。无法证明即 False——
    绝不复用「恰好健康」的任意服务（issue #140 铁律）。"""
    try:
        port = _port_of(base_url)
    except Exception:
        return False
    rec = independence.read_pid_record(port)
    if rec is None:
        return False
    return independence.pid_owns_sidecar(rec["pid"], port,
                                         start_time=rec.get("start_time"))


def preflight(cfg=None):
    """入口统一 fail-closed 前置校验（请求/绑定/拉起之前）：遗留键拒绝 +
    已配置 python/model_dir/aligner_dir/base_url 独立性校验 + 资源键坏值拒绝。"""
    cfg = cfg if cfg is not None else load_config()
    independence.preflight_config(cfg)
    return cfg


def device_map_of(cfg):
    """sidecar 设备映射：config asr.device_map（默认 cuda:0；CPU-only 用 "cpu"）。"""
    return cfg_get(cfg, "asr.device_map", "cuda:0")


def vram_budget_of(cfg, model_dir):
    """显存预算（MiB）：config asr.vram_budget_mib 优先，否则按模型目录估计。"""
    v = cfg_get(cfg, "asr.vram_budget_mib", None)
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
        return int(v)
    return independence.estimate_vram_mib(model_dir)


def ensure_sidecar(cfg=None, base_url=None, start_timeout=180.0, verbose=True):
    """sidecar 不在则按配置拉起并等健康；已健康且可归属本项目直接 True。
    issue #141：健康但无法归属（无 pid 记录/非本项目进程/启动时刻不符）→ 拒绝复用。
    issue #142：enabled 门禁（禁用态不发请求不拉起）+ 拉起前通用显存准入
    （nvidia-smi 只读；不足/不可测即安全拒绝，绝不触碰外部服务）。"""
    cfg = cfg if cfg is not None else load_config()
    preflight(cfg)
    independence.require_enabled(cfg, "启动/复用 sidecar（ensure_sidecar）")
    base_url = base_url or cfg_get(cfg, "asr.base_url", DEFAULT_BASE_URL)
    independence.validate_base_url(base_url)
    if health(base_url, timeout=1.0):
        if _sidecar_attribution(base_url):
            if verbose:
                print("✓ sidecar 已在运行：%s（本项目 pid 记录三因子归属通过）" % base_url,
                      file=sys.stderr)
            return True
        print("✗ %s 端口上已有健康服务但无法归属到本项目 sidecar（无本项目 pid 记录、"
              "进程 cmdline 不符或启动时刻不符/PID 已被复用）——拒绝复用任意健康服务"
              "（issue #140 铁律）。若是遗留进程请手动清理后重试" % base_url, file=sys.stderr)
        return False
    py = probe_python(cfg)
    model = pick_model(cfg)
    aligner = probe_aligner(cfg)
    if py is None or model is None:
        print("✗ 探测不到 Framivra 专属 qwen-asr 解释器或模型（绝不借用输入法资产）"
              "——先跑 bash apps/asr/init.sh（python=%s, model=%s）"
              % (py or "缺", model or "缺"), file=sys.stderr)
        return False
    model = independence.verify_model_dir(model, "model", check_weights=True)
    if aligner:
        # aligner 目录同样过目录级资产链接审计（红队 D3/N4：symlink 借用 IME 资产）
        aligner = independence.verify_model_dir(aligner, "aligner")
        independence.verify_dir_assets(aligner, "aligner", required=False)
    port = _port_of(base_url)
    device = device_map_of(cfg)
    if verbose:
        print("… 资源准入检查（device=%s，预算 %d MiB）…" % (
            device, vram_budget_of(cfg, model)), file=sys.stderr)
    decision = independence.gpu_admission(vram_budget_of(cfg, model), device=device)
    if not decision["allowed"]:
        print("✗ %s" % decision["reason"], file=sys.stderr)
        return False
    SIDECAR_LOG.parent.mkdir(parents=True, exist_ok=True)
    cmd = [py, "-I", str(ASR_DIR / "sidecar_server.py"), "--port", str(port),
           "--model", model, "--device-map", str(device)]
    if aligner:
        cmd += ["--aligner", aligner]
    else:
        print("⚠ 未找到 ForcedAligner：字级时间戳（srt/timeline）将不可用——跑 bash apps/asr/init.sh 下载", file=sys.stderr)
    if verbose:
        print("… 拉起 sidecar：%s（日志 %s）" % (" ".join(cmd), SIDECAR_LOG), file=sys.stderr)
    with open(SIDECAR_LOG, "a", encoding="utf-8") as log:
        log.write("\n[%s] ===== 拉起：%s =====\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), " ".join(cmd)))
        log.flush()
        proc = subprocess.Popen(cmd, stdout=log, stderr=log, start_new_session=True,
                                cwd=str(ASR_DIR), env=py_env(py))
    independence.write_pid_record(port, proc.pid)
    deadline = time.monotonic() + start_timeout
    while time.monotonic() < deadline:
        if health(base_url, timeout=1.0):
            if verbose:
                print("✓ sidecar 就绪：%s（pid %d）" % (base_url, proc.pid), file=sys.stderr)
            return True
        if proc.poll() is not None:
            print("✗ sidecar 启动即退出（code %s）——查看日志：%s" % (proc.returncode, SIDECAR_LOG),
                  file=sys.stderr)
            pid_file(port).unlink(missing_ok=True)
            return False
        time.sleep(1.0)
    print("✗ sidecar %ds 内未就绪——查看日志：%s" % (start_timeout, SIDECAR_LOG), file=sys.stderr)
    return False


def stop_sidecar(base_url=None, cfg=None):
    """SIGTERM sidecar（项目状态根 pid 记录 + /proc 三因子归属：cmdline、端口、
    启动时刻）；无法归属（陈旧 PID/PID 重用/伪造记录/外部进程）则拒绝发信号，
    只报错（issue #141/#142：绝不向外部/输入法进程发信号）。

    语义（issue #142）：停止自有服务**不受 enabled 门禁限制**——禁用态下
    回收本项目残留 sidecar 是安全收敛动作；只读 health 探测用于判断收尾态。"""
    cfg = cfg if cfg is not None else load_config()
    preflight(cfg)
    base_url = base_url or cfg_get(cfg, "asr.base_url", DEFAULT_BASE_URL)
    independence.validate_base_url(base_url)
    port = _port_of(base_url)
    pf = pid_file(port)
    alive_before = health(base_url, timeout=1.0) is not None
    rec = independence.read_pid_record(port)
    if rec is None:
        if pf.exists():
            print("✗ pid 记录损坏（%s）——已清除且未发任何信号" % pf, file=sys.stderr)
            pf.unlink(missing_ok=True)
            return False
        if alive_before:
            print("⚠ %d 端口有服务但无本项目 pid 记录——无法归属，未发送停止信号" % port,
                  file=sys.stderr)
        else:
            print("✓ sidecar 未在运行", file=sys.stderr)
        return not alive_before
    pid = rec["pid"]
    if not independence.pid_owns_sidecar(pid, port,
                                         start_time=rec.get("start_time")):
        detail = "进程不存在或 cmdline 不符"
        if rec.get("start_time") is None:
            detail += "；旧格式 pid 记录无启动时刻，请升级记录（重启 sidecar 自动生成）"
        else:
            detail += "或启动时刻不符（PID 可能已被复用）"
        print("✗ pid %d 无法归属到本项目 sidecar（%s）——拒绝发信号；"
              "pid 记录保留供排查：%s" % (pid, detail, pf), file=sys.stderr)
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if health(base_url, timeout=0.5) is None:
            break
        time.sleep(0.3)
    pf.unlink(missing_ok=True)
    print("✓ sidecar 已停止（pid %d）" % pid, file=sys.stderr)
    return True


# ---------------------------------------------------------------- 音频抽取

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
              ".mp4", ".mov", ".mkv", ".webm", ".avi"}


def extract_wav16k(src, out_dir, runid="x"):
    """音视频 → 16k mono wav（ffmpeg）；返回 wav 路径。out_dir 不存在则建。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / ("asr16k_%s.wav" % runid)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src),
           "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError("ffmpeg 抽取 16k wav 失败：%s\n%s" % (src, r.stderr.strip()[:500]))
    return str(out)


def probe_duration_s(path):
    """ffprobe 时长（秒）；失败 None。"""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return None


if __name__ == "__main__":
    print("client 为 asr_cli 内部模块，无独立 CLI；探测结果：")
    cfg = load_config()
    print("  python: %s" % (probe_python(cfg) or "✗ 缺"))
    print("  model 1.7b: %s" % (probe_model(cfg, "1.7b") or "✗ 缺"))
    print("  model 0.6b: %s" % (probe_model(cfg, "0.6b") or "✗ 缺"))
    print("  aligner: %s" % (probe_aligner(cfg) or "✗ 缺"))
    print("  health: %s" % health(cfg_get(cfg, "asr.base_url", DEFAULT_BASE_URL)))
