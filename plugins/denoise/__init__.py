"""denoise 插件（缝 A · prepare 源产物钩子，v2 §2.2；设计权威 docs/ARCHITECTURE.md）。

sources[] 级键 ``"denoise": true | {...可选参数}``——声明即该源的 prepare 音频
走「陷波(ffmpeg) → Resemble 仅降噪(conda) → 回 48kHz」链，产物内容寻址：

    cache/audio/{audio_key}::denoise:{version}:{value_digest}.wav

- audio_key = 内核 prepare 裸提取键（经 vtx.api.audio_key——插件只依赖内核
  API 面，禁捅内部模块）；value_digest = 该源 denoise 键值的排序 JSON sha1[:12]
  （键值变/版本升 → 新产物，旧产物由 gc 回收）。
- **裸键不污染**：cache/audio/{audio_key}.wav 恒为未处理音频（契约 v1.1——插件
  自算产物路径，见 vtx/pluginlib/hooks.py run_prepare_source docstring）。
- 环境缺失 fail-hard（渲染链口径）：conda env / 权重缺 → 打印缺什么 + exit 1，
  不回落裸音频（回落 = 产物标注降噪实未降噪，不可信）；安装期预检走
  health_check.py（doctor health 探针，允许报 unhealthy 不崩）。
- contribute_keys（不变量 ①）：本 timeline 全部声明 denoise 键源的产物键——
  budget_gate 渲染中触发 gc 不误杀。

CLI（离线手动用，命令面不变）：env python3 plugins/denoise/denoise_voice.py
<in.wav> <out.wav>；依赖清单与下载渠道见 README.md / denoise_init.sh。
"""
import hashlib
import json
import sys
from pathlib import Path

from vtx.api import audio_key as _audio_key

from .denoise_voice import chain, conda_python, missing_env, run_dir, weights_dir

VERSION = "1.0.0"
KEY = "denoise"


def value_digest(value) -> str:
    """denoise 键值参数摘要（排序 JSON sha1[:12]）：true 与 {...} 各自稳定，
    键值变 → 摘要变 → 产物键变（旧产物由 gc 回收）。"""
    return hashlib.sha1(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:12]


def _source(c, sid):
    for s in c.tl.get("sources", []):
        if isinstance(s, dict) and s.get("id") == sid:
            return s
    return None


def product_path(c, sid) -> Path:
    """产物路径 cache/audio/{audio_key}::denoise:{version}:{value_digest}.wav。"""
    s = _source(c, sid) or {}
    return c.cache / "audio" / (
        f"{_audio_key(c, sid)}::denoise:{VERSION}:{value_digest(s.get(KEY))}.wav")


def prepare_source(c, sid):
    """缝 A 钩子（契约 v1.1，§2.2）：源声明 denoise 键 → 保证产物在盘并返回
    Path；未声明 → None（调用方直抽裸 wav）。产物缓存命中直返；缺失则经
    denoise_voice.chain 产出（陷波→Resemble→48k，-ac 2 对齐裸提取 stereo 契约）；
    环境缺失打印缺什么 + exit 1（fail-hard，不回落裸音频）。dry-run 不落产物。"""
    s = _source(c, sid)
    if s is None or KEY not in s:
        return None
    out = product_path(c, sid)
    if out.exists():
        return out
    miss = missing_env()
    if miss:
        print("✗ denoise 环境缺失（fail-hard，不回落裸音频——回落=产物标注降噪实未降噪）：",
              file=sys.stderr)
        for m in miss:
            print(f"    - {m}", file=sys.stderr)
        sys.exit(1)
    if getattr(c, "dry", False):
        print(f"  [dry-run] denoise 产物 {out.name}（陷波→Resemble 仅降噪→48k stereo）")
        return out
    print(f"  · denoise: {sid} → {out.name}（陷波→Resemble 仅降噪→48k stereo）")
    chain(c.src_path(sid), out, stereo=True)
    return out


def contribute_keys(c) -> set:
    """不变量 ①（§2.1）：本 timeline 全部声明 denoise 键源的产物键集（gc 白名单）。"""
    return {product_path(c, s["id"]).stem
            for s in c.tl.get("sources", [])
            if isinstance(s, dict) and s.get("id") and KEY in s}
