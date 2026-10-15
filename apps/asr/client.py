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
  - 探测链（复用本机资产优先）：解释器 config.asr.python → IME .venv-qwen-asr →
    apps/asr/.venv；模型根 config.asr.model_dir → IME vendor/models/qwen3-asr →
    ~/models/qwen3-asr；
  - sidecar 日志 ~/.local/state/video_tools/asr_sidecar.log，pid 文件 /tmp/asr_sidecar_<port>.pid
    （stop 按 pid SIGTERM）；
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
# IME 仓默认位置（~ 展开型，P2a 参数化收编；覆盖链 env VT_ASR_IME_ROOT > config
# asr.ime_root > 本默认，见 ime_root()）
IME_ROOT_DEFAULT = "~/文档/01_活跃项目/type/ibus-voice-ime-github"
SIDECAR_LOG = Path("~/.local/state/video_tools/asr_sidecar.log").expanduser()
TRANSCRIBE_TIMEOUT_DEFAULT = 1800.0     # 长音频（1200s 上限 × RTF）也要能跑完


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


def ime_root(cfg=None):
    """IME 仓根（解释器/模型复用链的锚）：env VT_ASR_IME_ROOT > config asr.ime_root
    > ~ 展开型默认（~/文档/01_活跃项目/type/ibus-voice-ime-github）。统一 expanduser
    归一（默认值含 ~；显式给出的绝对路径 expanduser 幂等无害）。"""
    v = os.environ.get("VT_ASR_IME_ROOT")
    if not v and cfg is not None:
        v = cfg_get(cfg, "asr.ime_root", None)
    return str(Path(v or IME_ROOT_DEFAULT).expanduser())


# ---------------------------------------------------------------- 探测链（§6.5 复用优先）

def py_env(py_bin):
    """为解释器构造环境：venv 整体搬家后自身 site-packages 会失联（IME venv 历史
    路径 ~/文档/type/ibus-ime，现 type/ibus-voice-ime-github），显式补 PYTHONPATH。"""
    env = dict(os.environ)
    sp_root = Path(py_bin).resolve().parent.parent / "lib"
    for sp in sorted(sp_root.glob("python*/site-packages")):
        existing = env.get("PYTHONPATH", "")
        if str(sp) not in existing.split(os.pathsep):
            env["PYTHONPATH"] = (str(sp) + os.pathsep + existing) if existing else str(sp)
        break
    return env


def _py_imports(py_bin, module):
    r = subprocess.run([py_bin, "-c", "import %s" % module],
                       capture_output=True, env=py_env(py_bin))
    return r.returncode == 0


def probe_python(cfg=None):
    """qwen-asr 解释器：config.asr.python → IME .venv-qwen-asr → apps/asr/.venv；
    候选必须能 import qwen_asr（PYTHONPATH 补齐后验证）。"""
    cfg = cfg if cfg is not None else load_config()
    cands = [cfg_get(cfg, "asr.python", None)]
    cands.append(Path(ime_root(cfg)) / ".venv-qwen-asr/bin/python")
    cands.append(ASR_DIR / ".venv/bin/python")
    for c in cands:
        if not c:
            continue
        p = Path(_expand(c))
        if p.exists() and os.access(str(p), os.X_OK) and _py_imports(str(p), "qwen_asr"):
            return str(p)
    return None


def model_roots(cfg=None):
    """模型根目录探测链：config.asr.model_dir → IME vendor → ~/models/qwen3-asr。"""
    cfg = cfg if cfg is not None else load_config()
    roots = []
    c = cfg_get(cfg, "asr.model_dir", None)
    if c:
        roots.append(_expand(c))
    roots.append(str(Path(ime_root(cfg)) / "vendor/models/qwen3-asr"))
    roots.append(str(Path("~/models/qwen3-asr").expanduser()))
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
    """探测 ForcedAligner 目录：config.asr.aligner_dir → 模型根探测链。"""
    cfg = cfg if cfg is not None else load_config()
    c = cfg_get(cfg, "asr.aligner_dir", None)
    if c:
        p = Path(_expand(c))
        if p.exists():
            return str(p)
    for root in model_roots(cfg):
        d = Path(root) / "Qwen3-ForcedAligner-0.6B"
        if (d / "model.safetensors").exists() or (d / "model.safetensors.index.json").exists():
            return str(d)
    return None


def ime_sidecar_alive(timeout=0.5):
    """IME sidecar(18081) 存活探测（R3/R6：共驻 → 本 sidecar 默认降 0.6B 档）。"""
    try:
        req = urllib.request.Request("http://127.0.0.1:18081/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def pick_model(cfg=None):
    """默认档：1.7B 优先，缺则 0.6B；IME sidecar(18081) 同驻时反转为 0.6B 优先
    （与 sidecar 启动时 auto 探测同一决策，IME 1.7B + 本 1.7B + aligner 三者
    同驻 16GB 4080 必爆显存，方案 §3.3/R3）。"""
    if ime_sidecar_alive():
        m = probe_model(cfg, "0.6b") or probe_model(cfg, "1.7b")
    else:
        m = probe_model(cfg, "1.7b") or probe_model(cfg, "0.6b")
    return m


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
               timeout=TRANSCRIBE_TIMEOUT_DEFAULT):
    """POST /transcribe。长音频不切分直接透传（库内建分块，方案 §3.3 红队 P1-1）。"""
    payload = {"audio": str(wav_path)}
    if language:
        payload["language"] = language
    if timestamps:
        payload["timestamps"] = True
    return _post_json(base_url, "/transcribe", payload, timeout)


def warm(base_url, timeout=600.0):
    return _post_json(base_url, "/warm", {}, timeout)


# ---------------------------------------------------------------- sidecar 进程管理

def pid_file(port):
    # tempfile.gettempdir() 跨平台（Windows 原生 Python 对 "/tmp" 会解析到当前盘根，
    # 目录不存在写入失败；Git Bash 与 Linux 不变）
    import tempfile
    return Path(tempfile.gettempdir()) / ("asr_sidecar_%d.pid" % port)


def _port_of(base_url):
    from urllib.parse import urlparse
    u = urlparse(base_url)
    return u.port or (443 if u.scheme == "https" else 80)


def ensure_sidecar(cfg=None, base_url=None, start_timeout=180.0, verbose=True):
    """sidecar 不在则按配置拉起并等健康；已健康直接 True。"""
    cfg = cfg if cfg is not None else load_config()
    base_url = base_url or cfg_get(cfg, "asr.base_url", DEFAULT_BASE_URL)
    if health(base_url, timeout=1.0):
        if verbose:
            print("✓ sidecar 已在运行：%s" % base_url, file=sys.stderr)
        return True
    py = probe_python(cfg)
    model = pick_model(cfg)
    aligner = probe_aligner(cfg)
    if py is None or model is None:
        print("✗ 探测不到 qwen-asr 解释器或模型——先跑 bash apps/asr/init.sh（python=%s, model=%s）"
              % (py or "缺", model or "缺"), file=sys.stderr)
        return False
    port = _port_of(base_url)
    SIDECAR_LOG.parent.mkdir(parents=True, exist_ok=True)
    cmd = [py, str(ASR_DIR / "sidecar_server.py"), "--port", str(port), "--model", model]
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
    pid_file(port).write_text(str(proc.pid))
    deadline = time.monotonic() + start_timeout
    while time.monotonic() < deadline:
        if health(base_url, timeout=1.0):
            if verbose:
                print("✓ sidecar 就绪：%s（pid %d）" % (base_url, proc.pid), file=sys.stderr)
            return True
        if proc.poll() is not None:
            print("✗ sidecar 启动即退出（code %s）——查看日志：%s" % (proc.returncode, SIDECAR_LOG),
                  file=sys.stderr)
            return False
        time.sleep(1.0)
    print("✗ sidecar %ds 内未就绪——查看日志：%s" % (start_timeout, SIDECAR_LOG), file=sys.stderr)
    return False


def stop_sidecar(base_url=None, cfg=None):
    """SIGTERM sidecar（按 pid 文件）；返回是否停掉了进程。"""
    cfg = cfg if cfg is not None else load_config()
    base_url = base_url or cfg_get(cfg, "asr.base_url", DEFAULT_BASE_URL)
    port = _port_of(base_url)
    pf = pid_file(port)
    alive_before = health(base_url, timeout=1.0) is not None
    if not pf.exists():
        if alive_before:
            print("⚠ %d 端口有服务但无本机 pid 文件（非本 CLI 拉起？）——未发送停止信号" % port,
                  file=sys.stderr)
            return False
        print("✓ sidecar 未在运行", file=sys.stderr)
        return True
    pid = int(pf.read_text().strip())
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
