#!/usr/bin/env python3
"""sidecar_server —— Qwen3-ASR + ForcedAligner 本地转写服务（video_tools 自有）

用途：常驻 HTTP 服务（stdlib http.server），接收 16k wav 路径，返回转写文本与
sidecar 加工的字级 words[]（时间锚定断句的直接输入）。与 IME 输入法 sidecar
（18081）完全解耦，本服务默认 127.0.0.1:18085。

用法（运行于 qwen-asr 解释器——config.asr.python 探测链，本机即 IME .venv-qwen-asr，
Python 3.14.7 + qwen-asr 0.0.6 + torch 2.12.0）：
    <venv>/bin/python sidecar_server.py --port 18085            # 常驻
    <venv>/bin/python sidecar_server.py --once-health           # init 冒烟：起→/health→退
通常由 asr_cli/client.py 自动拉起（日志 ~/.local/state/video_tools/asr_sidecar.log），
不需要手动运行。

机制（方案 §3.3/§3.4）：
  - 协议：GET /health → {status,model,aligner,port,version}；POST /warm → {ok:true}；
    POST /transcribe {audio,language,timestamps} → {text[,words,degraded_ranges,duration_s]}；
    错误统一 {"error":"中文可行动信息"} + 4xx/5xx。
  - words 为 sidecar 加工格式，非 qwen-asr 原生 items（原生为 token 级、无标点、ms 制；
    本服务统一转秒 3 位小数）。text↔items 对齐（build_words）：双流规范化（大小写折叠 +
    剥非 L/N 字符）后 token 在 text 流贪心子串匹配——数字变形容认（"314"↔"3.14"）、
    拉丁连续串跨 text 中 -/. 吸收（"Qwen3-ASR"）、CJK 单字一对一；标点零宽锚 = 前实字
    {s,e}；失配区段线性插值兜底并标注 degraded_ranges；全失配 words=[]。
  - 长音频：任意时长直接透传（库内建 ±5s 低能量边界分块与拼回，MAX_FORCE_ALIGN_
    INPUT_SECONDS=180 是分块目标值非门槛），本服务零切分零拼回。
  - 同卡共驻防护：启动探测 IME sidecar(18081) 存活 → 默认档自动降 0.6B（IME 1.7B +
    本 1.7B + aligner 三者同驻 16GB 4080 必爆显存）；--model 显式指定则尊重并自担风险。
  - 显存换挡与空闲卸载：lazy 加载（首请求上 GPU）；加载 OOM 自动改试另一档
    （1.7B↔0.6B 同根 companion）；空闲超时（默认 300s 可配）卸载到 CPU RAM 释放 VRAM
    （IME ModelManager 实证策略的解耦简化版）。
  - R10 降级：from_pretrained 的 forced_aligner 关键字参数若因库版本演进失败 →
    显式组装（Qwen3ForcedAligner.from_pretrained + 挂回 wrapper.forced_aligner）。
  - build_words/_is_kept_char 等 words 构造纯函数只依赖 stdlib——系统 python3 可直接
    import（tests/test_words.py 即如此测，不加载模型）。

来源：docs/plans/2026-09-11-三大模块整合方案.md v2.1 §3.3/§3.4；
qwen_asr 0.0.6 inference/qwen3_asr.py（from_pretrained/transcribe/分块拼回）、
inference/qwen3_forced_aligner.py（items 结构、tokenize/clean_token、ms→s）、
ibus-voice-ime qwen_asr_server.py（模型管理/换挡/空闲卸载思路，解耦重写）。
"""
import argparse
import json
import os
import sys
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

IME_ROOT_DEFAULT = "~/文档/01_活跃项目/type/ibus-voice-ime-github"   # ~ 展开（P2a 参数化；env VT_ASR_IME_ROOT 可覆盖）
PROTOCOL_VERSION = 1
PORT_DEFAULT = 18085
IME_PORT = 18081          # IME 输入法 sidecar 既有端口，绝不复用（探测它只为共驻降档）

MODEL_DIRS = {"1.7b": "Qwen3-ASR-1.7B", "0.6b": "Qwen3-ASR-0.6B"}
ALIGNER_DIR = "Qwen3-ForcedAligner-0.6B"


def _log(msg):
    print("[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def ensure_venv_site():
    """venv 整体搬家自愈：venv 的 site-packages 不在 sys.path 时（IME venv 历史上
    从 ~/文档/type/ibus-voice-ime 移动过，pyvenv.cfg 记录旧路径致解析失联）显式补上。
    只依赖 stdlib；幂等（已在 path 则不重复添加）。"""
    venv_lib = Path(sys.executable).resolve().parent.parent / "lib"
    for sp in sorted(venv_lib.glob("python*/site-packages")):
        if str(sp) not in sys.path and (sp / "qwen_asr").exists():
            sys.path.insert(0, str(sp))
            _log("⚠ venv site-packages 失联已自愈：%s" % sp)
            return True
    return False


# ================================================================ §3.4 words[] 构造（纯函数，stdlib only）

def _is_kept_char(ch):
    """对齐保留字符：unicode L*/N* 类与撇号（qwen3_forced_aligner is_kept_char 同口径）。"""
    if ch == "'":
        return True
    cat = unicodedata.category(ch)
    return cat.startswith("L") or cat.startswith("N")


def _to_seconds(items):
    """时间戳单位防御：库 0.0.6 的 align() 已把 ms 转成秒（round(x/1000.0, 3)），
    但旧数据/未来版本若回吐 ms（任一时间戳量级 > 10000；ASR 输入上限 1200s）
    则统一除回秒。"""
    if not items:
        return items
    peak = 0.0
    for _, s, e in items:
        peak = max(peak, abs(s), abs(e))
    if peak > 10000:
        return [(t, s / 1000.0, e / 1000.0) for t, s, e in items]
    return items


def build_words(text, items):
    """text↔items 贪心匹配 → sidecar words[]（§3.4 sidecar 侧核心加工）。

    text:  ASR 文本（含标点、原始形式，如 "3.14" 带点）
    items: [(token, s, e)] 对齐 token（库输出：仅 L/N 字符、CJK 单字/拉丁串单 token、
           变形如 "314"/"Qwen3ASR"、秒制）
    返回 (words, degraded_ranges)：
      words = [{"w": 单字符, "s": 秒, "e": 秒}, ...]——与 text 逐字符对齐、含标点零宽锚
              （标点/非 L/N 字符继承前一实字 {s, e}，不占发音时间）、拉丁连续串拆回单
              字符共享 token 时间窗、3 位小数；
      degraded_ranges = [[起, 止], ...]——失配区段（text 字符索引），行时间已用相邻
              已匹配区段线性插值兜底；全失配 → words=[] 且区间覆盖全文。
    """
    text = str(text or "")
    items = _to_seconds([(str(t), float(s), float(e)) for t, s, e in items if str(t)])
    norm = [(i, ch.lower()) for i, ch in enumerate(text) if _is_kept_char(ch)]
    joined = "".join(c for _, c in norm)
    idx = [i for i, _ in norm]

    # 贪心子串匹配：token 在 norm 流当前位置起首次出现（连续串跨 text 中被剥的
    # -/./空格自动吸收——它们本就不在 norm 流里；CJK 单字即单字符子串查找）
    spans = []          # (text_start, text_end_inclusive, s, e)，pos 单调 → 不重叠且有序
    pos = 0
    for tok, s, e in items:
        t = tok.lower()
        if not t:
            continue
        hit = joined.find(t, pos)
        if hit < 0:
            continue           # 失配 token：ASR 文本与对齐 items 数量/内容漂移
        spans.append((idx[hit], idx[hit + len(t) - 1], s, e))
        pos = hit + len(t)

    if not spans:
        return [], ([[0, max(0, len(text) - 1)]] if text else [])

    anchor = {}        # text_idx -> (s, e)：实字（norm 字符）时间；区间内夹的非 L/N
    span_hit = set()   # 字符（"3.14" 的 '.'）由「前实字锚」逻辑给同值，无需入表
    for a, b, s, e in spans:
        for i in range(a, b + 1):
            anchor[i] = (s, e)
    for a, b, s, e in spans:
        span_hit.update(i for i in range(a, b + 1))

    # 失配实字：相邻已匹配区段线性插值兜底（缺一侧用另一侧，零宽），标注 degraded_ranges
    unmatched = [i for i, _ in norm if i not in span_hit]
    segs = []
    for i in unmatched:
        if segs and i == segs[-1][-1] + 1:
            segs[-1].append(i)
        else:
            segs.append([i])
    degraded = []
    for seg in segs:
        n = len(seg)
        left_e = right_s = None
        for k in range(seg[0] - 1, -1, -1):
            if k in anchor and k in span_hit:
                left_e = anchor[k][1]
                break
        for k in range(seg[-1] + 1, len(text)):
            if k in anchor and k in span_hit:
                right_s = anchor[k][0]
                break
        for j, i in enumerate(seg):
            if left_e is not None and right_s is not None:
                t0 = left_e + (right_s - left_e) * j / n
                t1 = left_e + (right_s - left_e) * (j + 1) / n
            elif left_e is not None:
                t0 = t1 = left_e
            elif right_s is not None:
                t0 = t1 = right_s
            else:
                t0 = t1 = 0.0
            anchor[i] = (t0, t1)
        degraded.append([seg[0], seg[-1]])

    # words[]：与 text 逐字符对齐；非 L/N 字符零宽锚 = 前一实字 {s, e}
    words = []
    last = None
    for i, ch in enumerate(text):
        if i in anchor:
            s, e = anchor[i]
            last = (s, e)
        elif last is not None:
            s, e = last
        else:
            s, e = 0.0, 0.0     # 行首标点：N1 会删，防御值
        words.append({"w": ch, "s": round(s, 3), "e": round(e, 3)})
    return words, degraded


# ================================================================ 模型管理（换挡 + 空闲卸载）

def _ime_root():
    """IME 根三级覆盖链（P2 审查 P2-2 补齐 config 层，与 client.py 对齐）：
    env VT_ASR_IME_ROOT > config asr.ime_root > ~ 展开默认。"""
    env = os.environ.get("VT_ASR_IME_ROOT")
    if env:
        return str(Path(env).expanduser())
    try:
        from vtx.pluginlib.config import load_config
        cfg = (load_config().get("asr") or {}).get("ime_root")
        if cfg:
            return str(Path(str(cfg)).expanduser())
    except Exception:
        pass
    return str(Path(IME_ROOT_DEFAULT).expanduser())


def _models_roots(extra=None):
    """模型根目录探测链（手动运行时；client 拉起时显式传 --model/--aligner）。"""
    ime = _ime_root()
    roots = []
    if extra:
        roots.append(str(Path(extra).expanduser()))
    roots.append(str(Path("~/models/qwen3-asr").expanduser()))
    roots.append(str(Path(ime) / "vendor/models/qwen3-asr"))
    return roots


def resolve_model_dir(spec, models_root=None):
    """--model 别名（1.7b/0.6b/auto）或路径 → (路径或 None, 显式指定与否)。"""
    spec = (spec or "").strip()
    if not spec or spec == "auto":
        return None, False
    if "/" in spec or os.sep in spec:
        return str(Path(spec).expanduser()), True
    key = spec.lower().replace("_", ".")
    for k, d in MODEL_DIRS.items():
        if k in key or d.lower() in key:
            for root in _models_roots(models_root):
                p = Path(root) / d
                if (p / "config.json").exists():
                    return str(p), True
            return None, True
    return None, True


def resolve_aligner_dir(models_root=None):
    """ForcedAligner 目录探测链（config 对齐目录 → ~/models → IME vendor）。"""
    for root in _models_roots(models_root):
        p = Path(root) / ALIGNER_DIR
        if (p / "config.json").exists():
            return str(p)
    return None


def companion_path(model_path):
    """同根另一档目录（IME _companion_path 同思路：OOM 换挡目标）。"""
    p = Path(model_path)
    if not p.exists():
        return None
    alias = "0.6b" if "1.7" in p.name or "1_7" in p.name else "1.7b"
    cand = p.parent / MODEL_DIRS[alias]
    return str(cand) if (cand / "config.json").exists() else None


def _ime_sidecar_alive(timeout=0.5):
    """探测 IME sidecar(18081) 是否存活（R3/R6：共驻 → 默认降 0.6B 档）。"""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % IME_PORT, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


class ModelHolder:
    """单驻模型管理：lazy 加载、OOM 自动换挡（companion 档重试）、空闲卸载 CPU。

    模型对象一旦加载常驻（GPU↔CPU RAM 迁移，不重复读盘）；推理持锁串行；
    watchdog 线程空闲超时后把模型迁回 CPU 释放 VRAM（torch.cuda.empty_cache）。
    """

    def __init__(self, asr_path, aligner_path=None, idle_timeout=300.0,
                 dtype="bfloat16", device_map="cuda:0", max_new_tokens=1024,
                 check_interval=15.0):
        self.asr_path = asr_path
        self.aligner_path = aligner_path
        self.idle_timeout = float(idle_timeout)
        self.dtype = dtype
        self.device_map = device_map
        self.max_new_tokens = int(max_new_tokens)
        self._interval = max(0.5, float(check_interval))
        self._lock = threading.Lock()
        self._model = None          # 已加载 wrapper（常驻）
        self._loaded_path = ""
        self._on_gpu = False
        self._gpu_device = None
        self._last = time.monotonic()
        self._stop = threading.Event()
        self._thread = None

    # ---- 生命周期 ----
    def start(self):
        if self.idle_timeout <= 0:
            return
        self._thread = threading.Thread(target=self._watchdog, name="asr-idle-watchdog", daemon=True)
        self._thread.start()
        _log("空闲卸载已布防：idle_timeout=%.0fs model=%s" % (self.idle_timeout, self.asr_path))

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    # ---- 请求路径 ----
    def acquire(self):
        self._lock.acquire()
        try:
            self._ensure_gpu_locked()
            self._last = time.monotonic()
            return self._model
        except BaseException:
            self._lock.release()      # 加载失败不得持锁（否则后续请求死锁）
            raise

    def release(self):
        self._last = time.monotonic()
        try:
            self._lock.release()
        except RuntimeError:
            pass

    @property
    def active_path(self):
        return self._loaded_path or self.asr_path

    # ---- 内部 ----
    def _watchdog(self):
        while not self._stop.wait(self._interval):
            try:
                with self._lock:
                    if not self._on_gpu:
                        continue
                    if time.monotonic() - self._last < self.idle_timeout:
                        continue
                    self._to_cpu_locked()
            except Exception as exc:      # watchdog 永不死
                _log("⚠ 空闲卸载检查失败（忽略）：%s" % exc)

    def _ensure_gpu_locked(self):
        if self._model is not None and self._on_gpu:
            return
        if self._model is None:
            self._load_locked()
            return
        inner = getattr(self._model, "model", None)
        if inner is not None:
            _log("模型重新上 GPU：%s" % self._loaded_path)
            inner.to(self._gpu_device)
            self._resync_device()
            self._empty_cache_guard()
            self._on_gpu = True

    def _load_locked(self):
        from qwen_asr import Qwen3ASRModel      # 延迟 import：本文件需可被系统 python3 导入测 words
        kw = {"dtype": self._torch_dtype(), "device_map": self.device_map,
              "max_inference_batch_size": 1, "max_new_tokens": self.max_new_tokens}
        attempts = [self.asr_path]
        comp = companion_path(self.asr_path)
        tried = set()
        for path in attempts:
            if path in tried or not path:
                continue
            tried.add(path)
            kw2 = dict(kw)
            if self.aligner_path:
                kw2["forced_aligner"] = self.aligner_path
            _log("加载模型：%s dtype=%s aligner=%s" % (path, self.dtype, self.aligner_path or "无"))
            t0 = time.monotonic()
            try:
                model = Qwen3ASRModel.from_pretrained(path, **kw2)
            except TypeError as exc:
                # R10：forced_aligner 关键字参数签名变动 → 显式组装降级路径
                _log("⚠ from_pretrained 关键字参数失败（%s）→ 显式组装 ForcedAligner" % exc)
                model = Qwen3ASRModel.from_pretrained(path, **kw)
                if self.aligner_path:
                    from qwen_asr.inference.qwen3_forced_aligner import Qwen3ForcedAligner
                    fa_kw = {"dtype": kw["dtype"], "device_map": self.device_map}
                    model.forced_aligner = Qwen3ForcedAligner.from_pretrained(self.aligner_path, **fa_kw)
            except Exception as exc:
                if self._is_oom(exc):
                    if comp and comp not in tried:
                        _log("⚠ 显存不足（%s）→ 自动换挡 %s" % (exc, comp))
                        attempts.append(comp)
                        continue
                    raise
                raise
            self._model = model
            self._loaded_path = path
            inner = getattr(model, "model", None)
            self._gpu_device = getattr(inner, "device", None) or self.device_map
            self._resync_device()
            self._on_gpu = True
            _log("模型就绪：%s（耗时 %.1fs）" % (path, time.monotonic() - t0))
            return
        raise RuntimeError("模型加载失败：%s" % self.asr_path)

    @staticmethod
    def _is_oom(exc):
        import torch
        return isinstance(exc, getattr(torch.cuda, "OutOfMemoryError", ())) \
            or "out of memory" in str(exc).lower() or "cuda oom" in str(exc).lower()

    def _torch_dtype(self):
        import torch
        name = (self.dtype or "bfloat16").lower()
        return {"bf16": torch.bfloat16, "bfloat16": torch.bfloat16,
                "fp16": torch.float16, "float16": torch.float16, "half": torch.float16,
                "fp32": torch.float32, "float32": torch.float32}.get(name, torch.bfloat16)

    def _resync_device(self):
        """迁移后刷新 wrapper/aligner 缓存的 device 属性（IME 同款）。"""
        m = self._model
        if m is None:
            return
        inner = getattr(m, "model", None)
        if inner is not None:
            m.device = getattr(inner, "device", getattr(m, "device", None))
        fa = getattr(m, "forced_aligner", None)
        fa_model = getattr(fa, "model", None) if fa is not None else None
        if fa is not None and fa_model is not None:
            fa.device = getattr(fa_model, "device", getattr(fa, "device", None))

    def _to_cpu_locked(self):
        m = self._model
        if m is None:
            return
        inner = getattr(m, "model", None)
        if inner is not None:
            inner.to("cpu")
        fa = getattr(m, "forced_aligner", None)
        fa_model = getattr(fa, "model", None) if fa is not None else None
        if fa_model is not None:
            fa_model.to("cpu")
        self._resync_device()
        self._empty_cache_guard()
        self._on_gpu = False
        _log("模型空闲卸载至 CPU（VRAM 已释放）：%s" % self._loaded_path)

    @staticmethod
    def _empty_cache_guard():
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass


# ================================================================ HTTP 协议（§3.3）

_HOLDER = None            # type: ModelHolder
_ARGS = None


def _send_json(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json_payload(handler):
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        length = 0
    if length <= 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def _probe_duration(path):
    """音频时长（秒）；librosa 不可用则 None（qwen-asr 解释器内必可用）。"""
    try:
        import librosa
        return round(float(librosa.get_duration(path=path)), 3)
    except Exception:
        return None


def _handle_transcribe(handler):
    payload = _read_json_payload(handler)
    audio = str(payload.get("audio") or "").strip()
    if not audio:
        raise RequestError(400, "缺少 audio（本地 wav 路径）")
    if not Path(audio).exists():
        raise RequestError(400, "音频文件不存在：%s" % audio)
    timestamps = bool(payload.get("timestamps"))
    language = payload.get("language")
    if isinstance(language, str) and not language.strip():
        language = None
    if timestamps and _HOLDER.aligner_path is None:
        raise RequestError(409, "字级时间戳需要 Qwen3-ForcedAligner（未加载）——先跑 bash asr/init.sh 下载，或去掉 timestamps")
    model = _HOLDER.acquire()
    try:
        t0 = time.monotonic()
        results = model.transcribe(audio=audio, language=language,
                                   return_time_stamps=timestamps)
        elapsed = time.monotonic() - t0
        first = results[0] if results else None
        text = str(getattr(first, "text", "") or "")
        if not timestamps:
            _log("转写完成：chars=%d elapsed=%.2fs timestamps=off" % (len(text), elapsed))
            return {"text": text}
        ts = getattr(first, "time_stamps", None)
        items = [(getattr(it, "text", ""), getattr(it, "start_time", 0.0),
                  getattr(it, "end_time", 0.0)) for it in getattr(ts, "items", [])]
        words, degraded = build_words(text, items)
        _log("转写完成：chars=%d words=%d degraded=%d elapsed=%.2fs"
             % (len(text), len(words), len(degraded), elapsed))
        return {"text": text, "words": words, "degraded_ranges": degraded,
                "duration_s": _probe_duration(audio)}
    finally:
        _HOLDER.release()


class RequestError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    server_version = "VideoToolsASR/1.0"

    def log_message(self, fmt, *args):
        _log("%s %s" % (self.address_string(), fmt % args))

    def do_GET(self):
        if self.path.rstrip("/") in ("", "/health"):
            _send_json(self, 200, {
                "status": "ok",
                "model": _HOLDER.active_path if _HOLDER else "",
                "aligner": _HOLDER.aligner_path if _HOLDER else None,
                "port": _ARGS.port if _ARGS else PORT_DEFAULT,
                "version": PROTOCOL_VERSION,
            })
            return
        _send_json(self, 404, {"error": "未知端点：%s（可用：GET /health，POST /warm /transcribe）" % self.path})

    def do_POST(self):
        path = self.path.rstrip("/")
        try:
            if path == "/warm":
                _read_json_payload(self)
                _HOLDER.acquire()
                try:
                    _log("预热完成：%s" % _HOLDER.active_path)
                finally:
                    _HOLDER.release()
                _send_json(self, 200, {"ok": True})
                return
            if path == "/transcribe":
                _send_json(self, 200, _handle_transcribe(self))
                return
            _read_json_payload(self)
            _send_json(self, 404, {"error": "未知端点：%s" % path})
        except RequestError as exc:
            _send_json(self, exc.status, {"error": exc.message})
        except Exception as exc:
            import traceback
            traceback.print_exc()
            _send_json(self, 500, {"error": "转写失败：%s（若为显存不足可 --model 0.6b 降档重启）" % exc})


def _self_health_check(port, timeout=5.0):
    """--once-health 冒烟：自 GET /health，200 即成功。"""
    import urllib.request
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=1.0) as r:
                if r.status == 200:
                    return json.loads(r.read().decode("utf-8"))
        except Exception as exc:
            last = exc
        time.sleep(0.2)
    raise RuntimeError("自检 /health 未就绪：%s" % last)


def main():
    global _HOLDER, _ARGS
    ap = argparse.ArgumentParser(description="video_tools Qwen3-ASR sidecar（运行于 qwen-asr 解释器）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=PORT_DEFAULT)
    ap.add_argument("--model", default="auto",
                    help="1.7b / 0.6b / 模型目录路径；auto=探测（IME 18081 同驻则默认 0.6b）")
    ap.add_argument("--aligner", default="auto", help="ForcedAligner 目录；auto=探测链；none=禁用")
    ap.add_argument("--models-root", default=None, help="模型根目录（缺省走 ~/models 与 IME vendor 探测链）")
    ap.add_argument("--no-aligner", action="store_true", help="禁用 ForcedAligner（timestamps 请求将 409）")
    ap.add_argument("--idle-timeout", type=float, default=300.0, help="空闲卸载秒数（0 关闭）")
    ap.add_argument("--dtype", default="bfloat16")
    ap.add_argument("--device-map", default="cuda:0")
    ap.add_argument("--max-new-tokens", type=int, default=1024)
    ap.add_argument("--once-health", action="store_true", help="init 冒烟：起服务→自检 /health→退出")
    args = ap.parse_args()
    _ARGS = args
    ensure_venv_site()      # venv 搬家自愈（必须在一切延迟 import qwen_asr 之前）

    # 模型档位解析：显式 > auto（同驻降档）；路径直接用
    model_path, explicit = resolve_model_dir(args.model, args.models_root)
    if model_path is None:
        if explicit:
            print("✗ 未找到指定模型目录：%s（先跑 bash asr/init.sh）" % args.model)
            return 2
        if _ime_sidecar_alive():
            cand = resolve_model_dir("0.6b", args.models_root)[0]
            if cand:
                model_path = cand
                print("⚠ 探测到 IME sidecar（18081）同驻——默认降 0.6B 档：%s" % cand)
        if model_path is None:
            model_path = resolve_model_dir("1.7b", args.models_root)[0]
        if model_path is None:
            print("✗ 探测链上没有 Qwen3-ASR 模型（~/models/qwen3-asr 或 IME vendor）——先跑 bash asr/init.sh")
            return 2
    elif args.model == "auto" and _ime_sidecar_alive() and "1.7" in model_path:
        cand = resolve_model_dir("0.6b", args.models_root)[0]
        if cand:
            print("⚠ 探测到 IME sidecar（18081）同驻——默认降 0.6B 档：%s（--model 1.7b 显式覆盖自担风险）" % cand)
            model_path = cand

    aligner_path = None
    if args.no_aligner or args.aligner == "none":
        aligner_path = None
    elif args.aligner == "auto":
        aligner_path = resolve_aligner_dir(args.models_root)
        if aligner_path is None:
            print("⚠ 未找到 %s——timestamps 请求将返回 409；跑 bash asr/init.sh 下载" % ALIGNER_DIR)
    else:
        p = str(Path(args.aligner).expanduser())
        if Path(p).exists():
            aligner_path = p
        else:
            print("✗ 指定的 aligner 目录不存在：%s" % p)
            return 2

    # 端口占用探测（不静默换端口，防配置漂移）
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occ = probe.connect_ex(("127.0.0.1", args.port))
    probe.close()
    if occ == 0:
        print("✗ 端口 %d 已被占用（18081 是 IME 输入法 sidecar 专用；本服务默认 18085）——"
              "请换 --port 或改 config asr.base_url" % args.port)
        return 2

    _HOLDER = ModelHolder(model_path, aligner_path=aligner_path,
                          idle_timeout=args.idle_timeout, dtype=args.dtype,
                          device_map=args.device_map, max_new_tokens=args.max_new_tokens)
    _log("sidecar 启动：http://%s:%d model=%s aligner=%s（模型延迟加载，首请求上 GPU）"
         % (args.host, args.port, model_path, aligner_path or "无"))
    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print("✗ 端口 %d 绑定失败：%s（请换 --port 或改 config asr.base_url）" % (args.port, exc))
        return 2

    if args.once_health:
        # serve_forever 必须先跑起来（构造只 bind+listen，不 accept 请求会超时）
        import threading as _th
        _th.Thread(target=server.serve_forever, daemon=True).start()
        time.sleep(0.3)
        info = _self_health_check(args.port)
        print("✓ /health 自检通过：%s" % json.dumps(info, ensure_ascii=False))
        server.shutdown()
        server.server_close()
        return 0

    _HOLDER.start()
    _log("listening http://%s:%d （Ctrl-C 停止）" % (args.host, args.port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _HOLDER.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
