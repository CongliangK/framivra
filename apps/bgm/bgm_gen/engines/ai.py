"""ai 引擎：ComfyUI HTTP 工作流提交（ACE-Step 1.5 / Music3 等整段素材生成，BG-D10）。

payload：
    workflow   必填，workflow JSON 文件路径（ComfyUI API 格式，同 bgm_gen/legacy/gen.py 用法）
    params     可选，dict：对 workflow 顶层节点字段的点路径覆盖，
               如 {"94.inputs.duration": 16, "98.inputs.seconds": 16,
                   "3.inputs.seed": 42, "104s.inputs.filename_prefix": "w2b/x"}
               （点路径 = 节点id.inputs.字段名；值限 数值/字符串/布尔；路径不存在即报错）
    timeout_s  可选，完成等待上限（默认 600）
    out        可选，输出文件路径（以音频扩展名结尾）或目录；
               缺省 = <仓库根>/out/smoke/ai/（多个音频输出全部落盘，第一个为出参 path）

行为：
    1. GET {base_url}/system_stats 探活——不可达直接 EngineError（service 档语义：
       pytest 应显式 skip，不得静默当绿）；
    2. BG-D8 轻量显存闸：提交前 wait_for_vram(on_timeout="proceed")——显存不足照常
       提交（ComfyUI 自带排队），但与本包本地 GPU 任务（demucs）互斥（文件锁）；
    3. POST /prompt → 轮询 /history/{prompt_id} → outputs 取 audio 文件（与 gen.py
       相同结构：{filename, subfolder, type}）→ /view 下载落盘。
spec.sr 若声明且与实际不符 → ffmpeg 重采样到声明值（envelope 契约：结果必须相等）。

determinism = "nondeterministic"（GPU 扩散采样；seed 固定亦不保证逐位一致）。

v3 R1-W1：workflow 必填等 payload 校验由描述符驱动（base.validate_payload，
descriptors/ai.json）；本引擎保留语义守卫：workflow 文件存在性、点路径覆盖
值类型/路径存在（_apply_overrides）、ComfyUI 探活。
"""

import json
import os
import time
import urllib.parse
import urllib.request

from .base import (BaseEngine, EngineError, default_out, measure_wav,
                   register_engine)
from .gpu_guard import wait_for_vram

AUDIO_EXTS = (".wav", ".flac", ".mp3", ".ogg", ".m4a", ".opus")


def _http_json(url, data=None, timeout=30):
    if data is not None:
        req = urllib.request.Request(
            url, data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _apply_overrides(wf, params):
    """点路径覆盖："94.inputs.duration" → wf["94"]["inputs"]["duration"]。失败即抛。"""
    for path, val in (params or {}).items():
        if not isinstance(val, (int, float, str, bool)):
            raise EngineError(f"ai params 覆盖值仅支持 数值/字符串/布尔：{path}={val!r}")
        parts = path.split(".")
        node = wf.get(parts[0])
        if not isinstance(node, dict):
            raise EngineError(f"ai params 点路径无此节点：{path!r}（workflow 顶层键 "
                              f"{sorted(wf)}）")
        cur = node
        for p in parts[1:-1]:
            if not isinstance(cur, dict) or p not in cur:
                raise EngineError(f"ai params 点路径不存在：{path!r}")
            cur = cur[p]
        last = parts[-1]
        if not isinstance(cur, dict) or last not in cur:
            raise EngineError(f"ai params 点路径不存在：{path!r}")
        cur[last] = val


@register_engine("ai")
class AIEngine(BaseEngine):
    determinism = "nondeterministic"
    summary = "ComfyUI HTTP 工作流提交（ACE/Music3 整段素材生成；service 档依赖 8188）"

    def render(self, spec):
        p = spec.get("payload") or {}
        wf_path = p.get("workflow")
        if not wf_path or not os.path.isfile(wf_path):
            raise EngineError(f"ai payload.workflow 不存在：{wf_path!r}")
        timeout_s = float(p.get("timeout_s", 600))
        base = (self.rt.get("comfyui") or {}).get("base_url", "http://127.0.0.1:8188")

        # 1) 探活（service 档语义：不可达要显式失败，可被上层转为 skip）
        try:
            _http_json(f"{base}/system_stats", timeout=5)
        except Exception as e:
            raise EngineError(
                f"ComfyUI 不可达：{base}（{e.__class__.__name__}: {e}）——"
                f"service 档依赖，pytest 应 skip 而非当绿") from e

        # 2) 加载 workflow + 应用点路径覆盖
        with open(wf_path) as f:
            wf = json.load(f)
        params = p.get("params") or {}
        _apply_overrides(wf, params)

        # 3) BG-D8：与本地 GPU 任务互斥；显存不足照常提交（ComfyUI 自带排队）
        with wait_for_vram(need_mb=int(self.rt.get("gpu", {}).get("ai_need_mb", 4096)),
                           timeout_s=120, on_timeout="proceed") as guard:
            t0 = time.time()
            resp = _http_json(f"{base}/prompt",
                              data={"prompt": wf, "client_id": "bgm_gen"})
            if resp.get("error") or resp.get("node_errors"):
                raise EngineError(f"ComfyUI 拒绝 workflow：{json.dumps(resp)[:1500]}")
            pid = resp["prompt_id"]

            # 4) 轮询
            poll_s = 3.0
            while True:
                try:
                    h = _http_json(f"{base}/history/{pid}").get(pid)
                except Exception:
                    h = None
                if h:
                    st = h.get("status", {})
                    if st.get("status_str") == "error":
                        raise EngineError(
                            f"ComfyUI 执行出错（prompt_id={pid}）："
                            f"{json.dumps(st, ensure_ascii=False)[:1500]}")
                    if st.get("completed"):
                        break
                if time.time() - t0 > timeout_s:
                    raise EngineError(
                        f"ComfyUI 超时（>{timeout_s:.0f}s，prompt_id={pid}）；"
                        f"任务仍在队列/执行，可到 {base} 排查")
                time.sleep(poll_s)
            elapsed = round(time.time() - t0, 1)
            guard_rec = dict(guard)

        # 5) 取回音频（结构同 gen.py：outputs.<node>.audio=[{filename,subfolder,type}]）
        files = []
        for node_id, node_out in (h.get("outputs") or {}).items():
            for a in node_out.get("audio") or []:
                files.append({"node": node_id, "filename": a["filename"],
                              "subfolder": a.get("subfolder", ""),
                              "type": a.get("type", "output")})
        if not files:
            raise EngineError(
                f"ComfyUI 完成但 outputs 无音频（prompt_id={pid}，nodes="
                f"{sorted((h.get('outputs') or {}))}）——检查 workflow 的 SaveAudio 节点")

        # 6) 落盘
        out_arg = p.get("out") or spec.get("out")
        if out_arg and out_arg.lower().endswith(AUDIO_EXTS):
            out_dir, primary = os.path.dirname(os.path.abspath(out_arg)), os.path.abspath(out_arg)
        else:
            out_dir = os.path.abspath(out_arg) if out_arg else default_out("smoke", "ai")
            primary = None
        os.makedirs(out_dir, exist_ok=True)
        saved = []
        for i, meta in enumerate(files):
            url = (f"{base}/view?filename={urllib.parse.quote(meta['filename'])}"
                   f"&subfolder={urllib.parse.quote(meta['subfolder'])}"
                   f"&type={urllib.parse.quote(meta['type'])}")
            dst = primary if (primary and i == 0) else os.path.join(
                out_dir, os.path.basename(meta["filename"]))
            urllib.request.urlretrieve(url, dst)
            saved.append(dst)
        path = saved[0]

        # 7) 度量 + sr 归一（envelope 契约：spec.sr 声明则必须相等）
        m = measure_wav(path)
        orig_sr = m["sr"]
        resampled = False
        want_sr = spec.get("sr")
        if want_sr is not None and orig_sr != int(want_sr):
            import shutil
            import subprocess
            import tempfile
            tmp = tempfile.NamedTemporaryFile(
                suffix=os.path.splitext(path)[1], delete=False)
            tmp.close()
            try:                               # m-1：异常路径确保临时件清场
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path,
                                "-ar", str(int(want_sr)), tmp.name], check=True)
                shutil.move(tmp.name, path)  # 跨设备不能用 os.replace
            finally:
                if os.path.exists(tmp.name):
                    os.unlink(tmp.name)
            resampled = True
            m = measure_wav(path)

        result = {"path": os.path.abspath(path), "sr": m["sr"],
                  "channels": m["channels"], "duration_s": round(m["duration_s"], 3),
                  "rms_db": round(m["rms_db"], 2),
                  "prompt_id": pid, "elapsed_s": elapsed,
                  "files": saved, "params_applied": params,
                  "orig_sr": orig_sr, "resampled": resampled,
                  "gpu_guard": guard_rec}
        return result
