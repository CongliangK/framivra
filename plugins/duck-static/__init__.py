"""duck-static 插件（缝 B · 音轨事件内存变换钩子 + validate 钩子，v2 §2.2；
设计权威 docs/ARCHITECTURE.md）。

音轨级键 ``"duck_strategy": {"mode": "static", ...}``（对象参数缺省化，缺省对齐
bed_static_duck CLI 默认：global_db -8 / duck_db -4 / voice_track "voice" /
merge_gap 20 / attack 5 / release 8）——声明即该轨在混音 graph 构建前做**纯内存**
events 变换（床乐全局增益 + 口播窗静态避让，D32/D34/D35 决策），**不写回
timeline**（幂等天然成立）；区间渲染走同一缝（窗口模式自动正确）。

- 变换核心 = bed_static_duck.py 的 transform_events/voice_windows（同一实现——
  脚本烘焙与管线内存变换永不分叉）；本包零 vtx 依赖（帧域数学）。
- validate 钩子（协议 v1.1）：① duck:true 与 duck_strategy 同轨互斥（§2.2 冻结
  要求报错）；② `_meta.bed_static_duck` 存在即报错（防现役 timeline 已用脚本
  烘焙过静态避让再挂插件键双施——提示先从 timeline.pre-duck.json 备份恢复）；
  ③ 键值形状（对象/mode=static/数值参数类型/voice_track 存在）早失败。
- CLI（离线手动烘焙 timeline，命令面与 _meta 标记语义不变）：
  env python3 plugins/duck-static/bed_static_duck.py（scripts/ 留 symlink 兼容）。
"""
from .bed_static_duck import DEFAULTS, transform_events, voice_windows

KEY = "duck_strategy"

# 数值参数（int 帧数 / float dB）与字符串参数的形状表（validate 用）
_NUMERIC = ("global_db", "duck_db")
_INTLIKE = ("merge_gap", "attack", "release")


def _params(strategy) -> dict:
    """键值 → 生效参数（DEFAULTS 缺省化；未知键忽略——前向兼容）。"""
    p = dict(DEFAULTS)
    if isinstance(strategy, dict):
        for k, v in strategy.items():
            if k in DEFAULTS:
                p[k] = v
    return p


def validate(c) -> list:
    """validate 钩子（协议 v1.1，§2.2）：返回错误行（空 = 通过）。

    ① duck:true × duck_strategy 同轨互斥；② _meta.bed_static_duck 双施防护；
    ③ 键值形状（对象 / mode=static / 数值参数类型 / voice_track 轨存在）。"""
    errs = []
    tl = c.tl if hasattr(c, "tl") else {}
    meta = tl.get("_meta")
    if isinstance(meta, dict) and "bed_static_duck" in meta:
        errs.append("_meta.bed_static_duck 已存在——该 timeline 已用脚本烘焙过静态避让，"
                    "再挂 duck_strategy 键会双施避让；请先从 timeline.pre-duck.json 备份恢复"
                    "（或手动清理标记）后再用插件键")
    names = set()
    for tr in tl.get("audio_tracks", []):
        if isinstance(tr, dict):
            names.add(tr.get("name"))
    for i, tr in enumerate(tl.get("audio_tracks", [])):
        if not isinstance(tr, dict) or KEY not in tr:
            continue
        label = f"audio_tracks[{i}]（{tr.get('name', '?')}）"
        if tr.get("duck"):
            errs.append(f"{label}: duck:true 与 duck_strategy 同轨互斥——sidechaincompress "
                        f"动态避让与静态分段避让不可叠加（§2.2）")
        v = tr[KEY]
        if not isinstance(v, dict):
            errs.append(f"{label}: duck_strategy 须为对象 {{\"mode\":\"static\",...}}，得到 {v!r}")
            continue
        if v.get("mode", "static") != "static":
            errs.append(f"{label}: duck_strategy.mode 仅支持 \"static\"，得到 {v.get('mode')!r}")
        unknown = [k for k in v if k not in DEFAULTS]
        if unknown:   # P2 审查 P2-5：未知参数键不静默吞——警告但不阻断
            print(f"⚠ {label}: duck_strategy 未知参数 {', '.join(map(repr, sorted(unknown)))}"
                  f"（可用：{sorted(DEFAULTS)}）——已忽略")
        for k in _NUMERIC:
            if k in v and (isinstance(v[k], bool) or not isinstance(v[k], (int, float))):
                errs.append(f"{label}: duck_strategy.{k} 须为数值（dB），得到 {v[k]!r}")
        for k in _INTLIKE:
            if k in v and (isinstance(v[k], bool) or not isinstance(v[k], int)):
                errs.append(f"{label}: duck_strategy.{k} 须为整数（帧数），得到 {v[k]!r}")
        vt = v.get("voice_track", DEFAULTS["voice_track"])
        if not isinstance(vt, str) or vt not in names:
            errs.append(f"{label}: duck_strategy.voice_track {vt!r} 不是本 timeline 的音轨名"
                        f"（现有：{sorted(n for n in names if n)}）")
    return errs


def audio_events(c, tracks) -> list:
    """缝 B 钩子：对声明 duck_strategy 的轨做纯内存 events 变换（不写回
    timeline、不改性入参——返回全新轨列表；口播窗取 voice_track 轨的**原始**
    events，即使该轨同时声明键也用变换前的事件）。"""
    by_name = {tr.get("name"): tr for tr in tracks
               if isinstance(tr, dict) and isinstance(tr.get("name"), str)}
    out = []
    for tr in tracks:
        if not isinstance(tr, dict) or KEY not in tr:
            out.append(tr)
            continue
        p = _params(tr[KEY])
        vtr = by_name.get(p["voice_track"]) or {}
        wins = voice_windows(vtr.get("events", []), p["merge_gap"])
        new_tr = dict(tr)
        new_tr["events"] = transform_events(
            tr.get("events", []), wins,
            p["global_db"], p["duck_db"], p["attack"], p["release"])
        out.append(new_tr)
    return out
