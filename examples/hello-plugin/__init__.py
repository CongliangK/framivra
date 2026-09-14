"""hello-plugin —— 音频插件完整纪律范本（缝 A · prepare 源产物钩子）。

v2 ARCHITECTURE §4.2 / §2.2 缝 A / §2.1 三不变量——第三方音频插件的起手模板，
也是验收门 6（examples 外部注册）的对照实现。sources[] 级键：

    "hello_gain": {"db": N}

声明即该源的 prepare 音频被施加 ``volume=N dB``（ffmpeg volume 滤镜，无重依赖），
产物内容寻址（与内核裸提取同格式：48kHz stereo pcm_s16le）：

    cache/audio/{audio_key}::hello:{version}:{value_digest}:{code_digest}.wav

- audio_key = 内核 prepare 裸提取键（经 ``vtx.api.audio_key``——插件只依赖内核
  API 面，禁捅内部模块）；value_digest = hello_gain 键值排序 JSON sha1[:12]；
  code_digest = 插件代码内容摘要（经 ``vtx.api.plugin_code_digest``——P6 起产物
  键掺代码摘要，改代码不 bump version 时产物键同样翻转，防旧产物被缓存命中
  「洗白」）。键值变/版本升/代码变 → 产物键变，旧产物由 gc 回收。
- **裸键不污染**：``cache/audio/{audio_key}.wav`` 恒为未处理音频（契约 v1.1——
  插件自算产物路径，见 vtx/pluginlib/hooks.py run_prepare_source docstring）；
  声明了键的源不再抽裸 wav，直接产出本插件键。
- **环境缺失 fail-hard**（§2.2 缝 A）：无 ffmpeg → 打印缺什么 + exit 1，不回落
  裸音频（回落 = 产物标注增益实未增益，不可信）；安装期预检走 health_check.py
  （doctor health 探针，允许报 unhealthy 不崩）。
- **contribute_keys**（不变量 ① §2.1）：本 timeline 全部声明 hello_gain 源的
  产物键——并入 gc 存留白名单，渲染中 budget_gate 触发 gc 不误杀。
- **卸载后缓存失效**（验收门 6 语义）：external 注册移除 → 插件不生效 → 产物
  键出白名单（gc 可回收）+ 回执指纹的插件段消失（带段录制的旧回执对无段重算
  失配被拒）——详见 README.md「卸载后缓存为何失效」。
"""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from vtx.api import audio_key as _audio_key
from vtx.api import plugin_code_digest

VERSION = "1.0.0"
KEY = "hello_gain"


def value_digest(value) -> str:
    """hello_gain 键值参数摘要（排序 JSON sha1[:12]）：键值变 → 摘要变 → 产物
    键变（旧产物出白名单由 gc 回收）。"""
    return hashlib.sha1(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:12]


def _source(c, sid):
    for s in c.tl.get("sources", []):
        if isinstance(s, dict) and s.get("id") == sid:
            return s
    return None


def _gain_db(value):
    """键值形状解析 → {db: N} 的 N（float）。形状错 fail-hard（prepare 期早失败，
    打印缺什么；不静默默认——静默默认 = 产物标注增益实为另一个增益）。"""
    if (isinstance(value, dict) and isinstance(value.get("db"), (int, float))
            and not isinstance(value.get("db"), bool)):
        return float(value["db"])
    print(f"✗ hello: 键 {KEY} 形状非法（须为 {{\"db\": <number>}}），得到 {value!r}",
          file=sys.stderr)
    sys.exit(1)


def product_path(c, sid) -> Path:
    """产物路径 cache/audio/{audio_key}::hello:{version}:{value_digest}:{code_digest}.wav。"""
    s = _source(c, sid) or {}
    return c.cache / "audio" / (
        f"{_audio_key(c, sid)}::hello:{VERSION}:{value_digest(s.get(KEY))}"
        f":{plugin_code_digest('hello-plugin')}.wav")


def prepare_source(c, sid):
    """缝 A 钩子（契约 v1.1，§2.2）：源声明 hello_gain 键 → 保证产物在盘并返回
    Path；未声明 → None（调用方直抽裸 wav）。缓存命中直返；缺失则从**源文件**
    单遍产出（提取 + volume=N dB 一条 ffmpeg 完成，输出格式与裸提取键一致：
    48kHz stereo pcm_s16le）；环境缺失打印缺什么 + exit 1（fail-hard）。dry-run
    不落产物。"""
    s = _source(c, sid)
    if s is None or KEY not in s:
        return None
    out = product_path(c, sid)
    if out.exists():
        return out
    db = _gain_db(s[KEY])
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        print("✗ hello 环境缺失（fail-hard，不回落裸音频——回落=产物标注增益实未增益）：",
              file=sys.stderr)
        print("    - ffmpeg（PATH 未找到；安装指引见 vt.py init）", file=sys.stderr)
        sys.exit(1)
    if getattr(c, "dry", False):
        print(f"  [dry-run] hello 产物 {out.name}（volume={db:+g}dB，48k stereo）")
        return out
    print(f"  · hello: {sid} → {out.name}（volume={db:+g}dB）")
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-v", "error", "-y", "-i", str(c.src_path(sid)),
           "-vn", "-af", f"volume={db}dB",
           "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("✗ hello 产出失败：" + " ".join(cmd)[:200], file=sys.stderr)
        print(r.stderr[-1500:], file=sys.stderr)
        sys.exit(1)
    return out


def contribute_keys(c) -> set:
    """不变量 ①（§2.1）：本 timeline 全部声明 hello_gain 源的产物键集（gc 白名单）。"""
    return {product_path(c, s["id"]).stem
            for s in c.tl.get("sources", [])
            if isinstance(s, dict) and s.get("id") and KEY in s}
