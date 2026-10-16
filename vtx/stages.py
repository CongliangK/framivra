"""vtx 阶段实现：validate / audit / prepare / audio / mux（原 vtx.py 各函数整体迁移）。

timeline 契约权威定义互指（三处需同步修改，2026-09-10 定）：
  ① 本文件 validate() —— 机制合法性权威（对照素材实测，硬门）
  ② vtx/timeline.schema.json —— 结构权威（字段/类型）
  ③ vtx/preview.html —— 可视化预览权威（timeline → 浏览器预览）
"""
import hashlib, json, math, os, re, subprocess, sys, tempfile, wave
from pathlib import Path

from .hygiene import (audio_key, manifest_record, rotate_history, versions_check,
                     versions_record)
from .infra import Ctx, run_parallel
from .diagnostics import (emit, key_path, subtitle_location, required_field, indexed,
                          source_structure, mark_handled, operation)
from . import fstxn
from .pluginlib import hooks as _plugin_hooks
from .pluginlib import registry as _plugin_registry

# ---- 命名常量（值不变，仅命名；2026-09-10 收拢）----
TARGET_LUFS = -14.0      # 项目目标响度（EBU R128，与 qc.py 同一权威值）
# sidechaincompress 避让缺省值（可被轨级 duck_params 覆盖，见 build_audio）。
# 2026-09-11 voice-ime 实测校准：缺省 threshold=0.03(-30.5dBFS) 高于口播 RMS 中位
# （约-31dBFS），避让只在语音峰值咬合、句间停顿 0.7s 即弹回全量——实测成片句间
# 床乐仅比人声低 ~6dB（正常应为 13~15dB）。新缺省：threshold=-38dBFS 早咬合、
# ratio 4（语音段压 ~8-14dB）、attack 20ms 快压、release 1500ms 慢回不弹跳。
DUCK_THRESHOLD = 0.0126  # ≈ -38 dBFS
DUCK_RATIO = 4
DUCK_ATTACK_MS = 20
DUCK_RELEASE_MS = 1500
DUCK_MAKEUP = 1

# ---------------------------------------------------------------- v2 插件键三分支（§2.1 不变量 ③）
# 内核已知键清单：timeline.schema.json 全量键的机制侧对照表（结构权威在 schema，
# 三处同步修改约定见本文件模块头；hardcode 一份使 validate 的未知键扫描零 I/O）。
# 插件扩展键不在此列（由 plugins/registry.json 声明，三分支判定见 _scan_plugin_keys）；
# `_meta` 为永久自由命名空间白名单（§2.1——插件/脚本标记不触发任何分支）。
KERNEL_KEYS = {
    "root": {"version", "project", "sources", "video_tracks", "subtitles",
             "subtitles_src", "audio_tracks"},
    "project": {"fps", "width", "height", "duration_frames", "cache_dependencies"},
    "sources[]": {"id", "path", "transform"},
    "audio_tracks[]": {"name", "duck", "duck_by", "duck_params", "events"},
    "audio_tracks[].events[]": {"src", "src_in", "in", "out", "volume",
                                "fade_in_frames", "fade_out_frames", "pan", "loop"},
}


def _scan_plugin_keys(c: Ctx, errs: list) -> list:
    """sources[] / audio_tracks[]（含 events[]）/ project 的未知键与插件键扫描。

    三分支（§2.1 不变量 3）：键在 registry 声明且插件 installed+enabled = 合法
    放行；声明未装 = 报错+安装提示；已装被禁用 = 报错+启用提示（绝不静默忽略——
    「缺省关闭」指不写键无行为，不是写了键可跳过）；完全不认识的键 = **警告不
    阻断**（新行为；对现有 timeline 零影响——现有键全部已知或已声明）。声明键
    的 path 须与出现位置一致（denoise 声明于 sources[] 却写在音轨上 → 按未知键
    警告处理）。返回警告行列表（调用方打印，不进 errs）。
    """
    warns = []

    def scan(scope_path: str, known: set, items, label_fmt: str, paths):
        for i, item in enumerate(items if isinstance(items, list) else []):
            if not isinstance(item, dict):
                continue
            for k in sorted(item):
                if k == "_meta" or k in known:
                    continue
                label = label_fmt.format(i=i)
                path = key_path(paths(i), k)
                before_err, before_warn = len(errs), len(warns)
                name, ki = _plugin_registry.lookup_key(k)
                if name is not None and ki and ki.get("path") == scope_path:
                    state = _plugin_registry.plugin_state(name)
                    if state == "active":
                        continue   # 分支 1：合法放行
                    if state == "not_installed":
                        errs.append(f"{label}: 键 {k!r} 由 plugins/{name} 提供，"
                                    f"未安装——vt plugins list 查看")
                    elif state == "disabled":
                        errs.append(f"{label}: 键 {k!r} 对应插件 plugins/{name} "
                                    f"已安装但被禁用——plugins/registry.json 启用后重跑")
                    else:
                        errs.append(f"{label}: 键 {k!r} 声明于 plugins/{name}，"
                                    f"但插件安装态异常（{state}）")
                elif name is not None:
                    warns.append(f"{label}: 键 {k!r} 声明于 {ki.get('path')}（plugins/{name}），"
                                 f"写在 {scope_path} 位置不符——按未知键处理")
                else:
                    warns.append(f"{label}: 未知键 {k!r}（不在内核 schema，也无插件声明）")

                for message in errs[before_err:]:
                    emit(c, message, json_path=path, rule_id='validate.plugin_state',
                         measured={'plugin': name, 'state': state}, threshold={'state': 'active'},
                         suggested_actions=[{'op': 'review_plugin_installation', 'params': {'plugin': name}}])
                for message in warns[before_warn:]:
                    emit(c, message, json_path=path, rule_id='validate.unknown_key',
                         measured={'key': k, 'scope': scope_path}, threshold={'known_key': True},
                         severity='warning', suggested_actions=[{'op': 'review_key', 'params': {'json_path': path}}])

    # root scope（协议 v1.2，P6 接线）：顶层键纳入扫描——KERNEL_KEYS["root"]
    # 自 v2 起即备而未扫（死键集），blender_bridge 类顶层插件键需此 scope 走
    # 三分支；顶层未知键自此「警告不阻断」（v1 静默，与 §2.1 语义对齐）。
    scan("root", KERNEL_KEYS["root"], [c.tl], "timeline", lambda i: "$")
    scan("project", KERNEL_KEYS["project"],
         [c.tl.get("project")] if isinstance(c.tl.get("project"), dict) else [],
         "project", lambda i: "$.project")
    scan("sources[]", KERNEL_KEYS["sources[]"], c.tl.get("sources", []),
         "sources[{i}]", lambda i: f"$.sources[{i}]")
    tracks = c.tl.get("audio_tracks", [])
    scan("audio_tracks[]", KERNEL_KEYS["audio_tracks[]"], tracks,
         "audio_tracks[{i}]", lambda i: f"$.audio_tracks[{i}]")
    # events[] 扫描标签按轨内索引计（P6 修复 A-P2-3：v1 扁平跨轨索引与其它
    # scope 的「容器内索引」惯例不符，报错定位难读）。
    event_paths = []
    def observed_event(ti, ei, event):
        event_paths.append(f"$.audio_tracks[{ti}].events[{ei}]")
        return event
    scan("audio_tracks[].events[]", KERNEL_KEYS["audio_tracks[].events[]"],
         [observed_event(ti, ei, e) for ti, tr in enumerate(tracks) if isinstance(tr, dict)
          for ei, e in indexed(c, (tr.get("events") or []), f"$.audio_tracks[{ti}].events")],
         "audio_tracks[].events[{i}]", lambda i: event_paths[i])
    return warns


# ---------------------------------------------------------------- validate
def validate(c: Ctx):
    errs = []
    observed = {}
    def observe(key, value):
        observed[key] = value
        return value
    def err(m, path='$', rule='unknown', measured=None, threshold=None, source=None):
        errs.append(m)
        emit(c, m, json_path=path, rule_id=rule, measured=measured,
             threshold=threshold, source=source)
    def field(obj, key, parent):
        return observe(key_path(parent, key), required_field(c, obj, key, parent, rule='validate.required_field'))
    def native(path, fn, description, *, paths=None, source=None):
        return operation(c, fn, path=path, rule='validate.operation',
                         operands={'operation': description, 'paths': paths or [path]},
                         source=source)
    def provider(path, fn, description, *, fact, paths=None):
        return operation(c, fn, path=path, rule='probe.metadata_operation',
                         operands={'operation': description, 'paths': paths or [path]},
                         source={'provider': 'source facts', 'provider_path': fact},
                         status='unknown')
    from .cache_state import validate_cache_dependencies
    try:
        validate_cache_dependencies(c)
    except (ValueError, OSError) as error:
        # The dependency producer supplies precise origins. Retain #52's
        # advisory accumulation without duplicating an already handled record.
        errs.append(str(error))
        if not getattr(error, '_vtx_diagnostic_handled', False):
            emit(c, str(error), rule_id='load.cache_dependency', status='unknown',
                 measured={'exception_type': type(error).__name__})
    sources = field(c.tl, "sources", "$")
    source_indices = {}
    srcs = {}
    for si, s in indexed(c, sources, "$.sources"):
        source_indices[id(s)] = si
        problem = source_structure(s, si)
        if problem is not None:
            err(problem); continue
        srcs[field(s, 'id', f'$.sources[{si}]')] = s
    for sid, s in srcs.items():
        sp = f"$.sources[{source_indices[id(s)]}]"
        p = c.src_path(sid)
        if not p.exists(): err(f"source {sid}: 文件不存在 {p}", sp+".path", 'validate.source_exists', {'path': str(p), 'exists': False}, {'exists': True}); continue
        # 缝 C（协议 v1.2，P6）：虚拟源 facts——插件受理的源（如 bgm-render 的
        # music_tools 工程 JSON）用插件供给的 facts，其余照旧 ffprobe 实测
        i = _plugin_hooks.run_probe_source(c, sid)
        if i is None:
            i = c.probe(p)
        if operation(c, lambda: observe("i['has_video']", i['has_video']), path=sp, rule='probe.metadata_operation', operands={'operation': 'i["has_video"]'}, source={'provider': 'source facts', 'provider_path': "'has_video'"}, status='unknown'):
            if provider(sp+".path", lambda: abs(observe("i['fps'] - c.fps", observe("i['fps']", i['fps']) - c.fps)) > 0.01, 'comparison', fact='$.fps', paths=['$.project.fps']): err(f"source {sid}: fps={operation(c, lambda: observe("i['fps']", i['fps']), path=sp, rule='probe.metadata_operation', operands={'operation': "i['fps']"}, source={'provider': 'source facts', 'provider_path': "'fps'"}, status='unknown')} ≠ {c.fps}（须先 CFR 归一化）", sp+".path", 'validate.source_fps', {'value': observed.get("i['fps']", {'status': 'not_observed'}), 'unit': 'frames/s'}, {'value': c.fps, 'tolerance': 0.01, 'unit': 'frames/s'})
        if not operation(c, lambda: observe("i['has_audio']", i['has_audio']), path=sp, rule='probe.metadata_operation', operands={'operation': 'i["has_audio"]'}, source={'provider': 'source facts', 'provider_path': "'has_audio'"}, status='unknown'): err(f"source {sid}: 无音频轨", sp+".path", 'validate.source_audio', {'has_audio': False}, {'has_audio': True})   # 纯视频源同禁；纯音频源合法（has_video=False）
    # v2 轨道等价：无 base 特权、无覆盖性约束；唯一机制约束 = 同轨 clips 不重叠
    for ti, tr in indexed(c, field(c.tl, "video_tracks", "$"), "$.video_tracks"):
        tp = f"$.video_tracks[{ti}]"
        kind = native(tp, lambda: tr.get('kind', '?'), 'tr.get')
        raw_clips = field(tr, "clips", tp)
        clip_indices = {}
        clip_ordinal = 0
        def clip_key(cl):
            nonlocal clip_ordinal
            ci = clip_ordinal
            clip_ordinal += 1
            clip_indices[id(cl)] = ci
            return field(cl, 'dst_in', f'{tp}.clips[{ci}]')
        clips = native(tp + '.clips', lambda: sorted(raw_clips, key=clip_key), 'sorted')
        if not clips: err(f"轨道 {field(tr, 'name', tp)}: 无 clips", tp + '.clips', 'validate.track_empty', {'value': 0, 'unit': 'clips'}, {'min': 1, 'unit': 'clips'})
        for a, b in zip(clips, clips[1:]):
            if native(tp + '.clips', lambda: observe("field(a, 'dst_in', f'{tp}.clips[{clip_indices[id(a)]}]') + field(a, 'dur', f'{tp}.clips[{clip_indices[id(a)]}]')", field(a, 'dst_in', f'{tp}.clips[{clip_indices[id(a)]}]') + field(a, 'dur', f'{tp}.clips[{clip_indices[id(a)]}]')) > field(b, 'dst_in', f'{tp}.clips[{clip_indices[id(b)]}]'), 'comparison', paths=[key_path(f'{tp}.clips[{clip_indices[id(b)]}]', 'dst_in'), key_path(f'{tp}.clips[{clip_indices[id(a)]}]', 'dst_in'), key_path(f'{tp}.clips[{clip_indices[id(a)]}]', 'dur')]):
                err(f"同轨 clips 重叠: {field(tr, 'name', tp)} @帧 {native(tp + '.clips', lambda: observe("field(a, 'dst_in', f'{tp}.clips[{clip_indices[id(a)]}]') + field(a, 'dur', f'{tp}.clips[{clip_indices[id(a)]}]')", field(a, 'dst_in', f'{tp}.clips[{clip_indices[id(a)]}]') + field(a, 'dur', f'{tp}.clips[{clip_indices[id(a)]}]')), 'arithmetic', paths=[key_path(f'{tp}.clips[{clip_indices[id(a)]}]', 'dst_in'), key_path(f'{tp}.clips[{clip_indices[id(a)]}]', 'dur')])} > {field(b, 'dst_in', f'{tp}.clips[{clip_indices[id(b)]}]')}", f'{tp}.clips[{clip_indices[id(b)]}].dst_in', 'validate.clip_overlap', {'previous_end': observed.get("field(a, 'dst_in', f'{tp}.clips[{clip_indices[id(a)]}]') + field(a, 'dur', f'{tp}.clips[{clip_indices[id(a)]}]')", {'status': 'not_observed'}), 'start': observed.get(key_path(f'{tp}.clips[{clip_indices[id(b)]}]', 'dst_in'), {'status': 'not_observed'}), 'unit': 'frames'}, {'max_previous_end': observed.get(key_path(f'{tp}.clips[{clip_indices[id(b)]}]', 'dst_in'), {'status': 'not_observed'}), 'unit': 'frames'})
        for cl in clips:
            observed.clear()
            cp = f"{tp}.clips[{clip_indices[id(cl)]}]"
            if native(cp, lambda: field(cl, 'dst_in', cp) < 0, 'comparison', paths=[key_path(cp, 'dst_in')]) or native(cp, lambda: observe("field(cl, 'dst_in', cp) + field(cl, 'dur', cp)", field(cl, 'dst_in', cp) + field(cl, 'dur', cp)) > c.dur, 'comparison', paths=['$.project.duration_frames', key_path(cp, 'dst_in'), key_path(cp, 'dur')]):
                err(f"{field(tr, 'name', tp)}: clip 区间越界 @dst_in={field(cl, 'dst_in', cp)}", cp, 'validate.clip_bounds', {'start': observed.get(key_path(cp, 'dst_in'), {'status': 'not_observed'}), 'end': observed.get("field(cl, 'dst_in', cp) + field(cl, 'dur', cp)", {'status': 'not_observed'}), 'unit': 'frames'}, {'min': 0, 'max': c.dur, 'unit': 'frames'})
            if kind == "gfx":
                if not (native(cp, lambda: observe("c.root / field(cl, 'src', cp)", c.root / field(cl, 'src', cp)), 'arithmetic', paths=[key_path(cp, 'src')])).exists(): err(f"gfx clip html 不存在: {field(cl, 'src', cp)}", cp + '.src', 'validate.gfx_exists', {'path': observed.get(key_path(cp, 'src'), {'status': 'not_observed'}), 'exists': False}, {'exists': True})
                continue
            if native(cp, lambda: field(cl, 'src', cp) not in srcs, 'comparison', paths=[key_path(cp, 'src')]): err(f"clip 引用未知 src {field(cl, 'src', cp)}", cp + '.src', 'validate.clip_source', {'value': observed.get(key_path(cp, 'src'), {'status': 'not_observed'})}, {'allowed': list(srcs)}); continue
            if native(cp, lambda: 'src_in' not in cl, 'comparison') or native(cp, lambda: 'src_out' not in cl, 'comparison'):
                err(f"普通 clip 缺 src_in/src_out: {field(tr, 'name', tp)} dst_in={field(cl, 'dst_in', cp)}", cp, 'validate.clip_source_range', {'keys': {'status': 'not_observed'}}, {'required': ['src_in', 'src_out']}); continue
            if native(cp, lambda: observe("field(cl, 'src_out', cp) - field(cl, 'src_in', cp)", field(cl, 'src_out', cp) - field(cl, 'src_in', cp)) != field(cl, 'dur', cp), 'comparison', paths=[key_path(cp, 'dur'), key_path(cp, 'src_out'), key_path(cp, 'src_in')]):
                err(f"EDL 不变量破坏: {field(tr, 'name', tp)} dst_in={field(cl, 'dst_in', cp)}", cp, 'validate.clip_edl', {'value': observed.get("field(cl, 'src_out', cp) - field(cl, 'src_in', cp)", {'status': 'not_observed'}), 'unit': 'frames'}, {'value': observed.get(key_path(cp, 'dur'), {'status': 'not_observed'}), 'unit': 'frames'})
            p = c.src_path(field(cl, "src", cp))
            if p.exists():
                i = c.probe(p)
                if not operation(c, lambda: observe("i['has_video']", i['has_video']), path=cp, rule='probe.metadata_operation', operands={'operation': 'i["has_video"]'}, source={'provider': 'source facts', 'provider_path': "'has_video'"}, status='unknown'):
                    err(f"视频 clip 引用了纯音频源: {field(tr, 'name', tp)} src={field(cl, 'src', cp)}", cp + '.src', 'validate.clip_video', {'has_video': False}, {'has_video': True})
                elif provider(cp+'.src', lambda: field(cl, 'src_out', cp) > observe("i['nb_frames']", i['nb_frames']), 'comparison', fact='$.nb_frames', paths=[key_path(cp, 'src_out')]):
                    err(f"切割越界: {field(tr, 'name', tp)} src={field(cl, 'src', cp)} src_out={field(cl, 'src_out', cp)} > 实测 {operation(c, lambda: observe("i['nb_frames']", i['nb_frames']), path=cp, rule='probe.metadata_operation', operands={'operation': "i['nb_frames']"}, source={'provider': 'source facts', 'provider_path': "'nb_frames'"}, status='unknown')}", cp + '.src_out', 'validate.clip_source_bounds', {'value': observed.get(key_path(cp, 'src_out'), {'status': 'not_observed'}), 'unit': 'frames'}, {'max': observed.get("i['nb_frames']", {'status': 'not_observed'}), 'unit': 'frames'})
            if native(cp, lambda: observe("cl.get('fade_in_frames', 0) + cl.get('fade_out_frames', 0)", cl.get('fade_in_frames', 0) + cl.get('fade_out_frames', 0)) > field(cl, 'dur', cp), 'comparison', paths=[key_path(cp, 'dur')]):
                err(f"fade 总长超过片段: {field(tr, 'name', tp)} dst_in={field(cl, 'dst_in', cp)}", cp, 'validate.clip_fade', {'value': observed.get("cl.get('fade_in_frames', 0) + cl.get('fade_out_frames', 0)", {'status': 'not_observed'}), 'unit': 'frames'}, {'max': observed.get(key_path(cp, 'dur'), {'status': 'not_observed'}), 'unit': 'frames'})
    # v2 subtitles_src（§1.1 条目 6）：加载点解析错误在此 FAIL——互斥/文件缺失/
    # 解析失败（infra.Ctx 构造期记入，语义裁定见 vtx/subtitles_src.py 模块头）。
    for e in getattr(c, "subtitles_errors", []):
        err(e, "$.subtitles_src", 'validate.subtitle_source', None, None)
    for si, s in indexed(c, field(c.tl, "subtitles", "$"), "$.subtitles"):
        observed.clear()
        subpath, subsource = subtitle_location(c, si)
        if not (native(subpath, lambda: 0 <= field(s, 'in', subpath) < field(s, 'out', subpath) <= c.dur, 'comparison', paths=[key_path(subpath, 'in'), key_path(subpath, 'out'), '$.project.duration_frames'], source=subsource)): err(f"字幕区间非法: {native(subpath, lambda: field(s, 'text', subpath)[:10], 'native operation', paths=[key_path(subpath, 'text')], source=subsource)}", subpath, 'validate.subtitle_bounds', {'start': observed.get(key_path(subpath, 'in'), {'status': 'not_observed'}), 'end': observed.get(key_path(subpath, 'out'), {'status': 'not_observed'}), 'unit': 'frames'}, {'min': 0, 'max': c.dur, 'start_before_end': True, 'unit': 'frames'}, subsource)
    for ti, tr in indexed(c, field(c.tl, "audio_tracks", "$"), "$.audio_tracks"):
        ap = f"$.audio_tracks[{ti}]"
        # 容器与必填键防御（P6 修复 B-P2-3）：schema 声明 events[].volume 等必填
        # 但结构权威零强制——缺键 timeline 此前在 _audio_graph 裸 KeyError 崩栈，
        # validate（机制权威）须把缺键挡在渲染前；缺 name/events 的轨在 validate
        # 自身就会 KeyError，一并防御。
        if not isinstance(tr, dict):
            err(f"audio_tracks[{ti}]: 须为对象", ap, 'validate.audio_track_structure', {'value': tr}, {'type': 'object'}); continue
        name = native(ap, lambda: tr.get('name', f'#{ti}'), 'tr.get')
        evs_raw = native(ap, lambda: tr.get('events'), 'tr.get')
        if not isinstance(evs_raw, list) or not all(isinstance(e, dict) for e in evs_raw):
            err(f"轨道 {name}: events 须为对象列表", ap+".events", 'validate.audio_events_structure', {'value': evs_raw}, {'type': 'array of objects'}); continue
        event_indices = {}
        for ei, e in enumerate(evs_raw):
            event_indices[id(e)] = ei
            ep = f"{ap}.events[{ei}]"
            for k in ("src", "in", "out", "volume"):
                if k not in e:
                    err(f"轨道 {name} 事件缺必填键 {k!r}: {e}", key_path(ep,k), 'validate.audio_event_required', {'present': False}, {'present': True})
        if any(any(k not in e for k in ("src", "in", "out")) for e in evs_raw):
            continue   # 缺键事件已报错；排序键不可得，跳过该轨区间校验
        evs = native(ap + '.events', lambda: sorted(evs_raw, key=lambda e: field(e, 'in', f'{ap}.events[{event_indices[id(e)]}]')), 'sorted')
        for e in evs:
            observed.clear()
            ep = f"{ap}.events[{event_indices[id(e)]}]"
            if not native(ep, lambda: 0 <= field(e, 'in', ep) < field(e, 'out', ep) <= c.dur, 'comparison', paths=[key_path(ep, 'in'), key_path(ep, 'out'), '$.project.duration_frames']): err(f"音频事件区间非法: {name}@{field(e, 'in', ep)}", ep, 'validate.audio_event_bounds', {'start': observed.get(key_path(ep, 'in'), {'status': 'not_observed'}), 'end': observed.get(key_path(ep, 'out'), {'status': 'not_observed'}), 'unit': 'frames'}, {'min': 0, 'max': c.dur, 'start_before_end': True, 'unit': 'frames'})
            if native(ep, lambda: field(e, 'src', ep) not in srcs, 'comparison', paths=[key_path(ep, 'src')]): err(f"音频引用未知 src {field(e, 'src', ep)}", ep + '.src', 'validate.audio_source', {'value': observed.get(key_path(ep, 'src'), {'status': 'not_observed'})}, {'allowed': list(srcs)}); continue
            i = _plugin_hooks.run_probe_source(c, field(e, 'src', ep))
            if i is None:
                i = c.probe(c.src_path(field(e, 'src', ep)))
            need = native(ep, lambda: observe("e.get('src_in', 0) + (field(e, 'out', ep) - field(e, 'in', ep))", e.get('src_in', 0) + observe("field(e, 'out', ep) - field(e, 'in', ep)", field(e, 'out', ep) - field(e, 'in', ep))), 'arithmetic', paths=[key_path(ep, 'out'), key_path(ep, 'in')])
            if not native(ep, lambda: e.get('loop'), 'e.get') and provider(ep+'.src', lambda: need > observe("i['nb_frames']", i['nb_frames']), 'comparison', fact='$.nb_frames'):
                err(f"音频源不足: {name}@{field(e, 'in', ep)} 需 {need} 帧 > {operation(c, lambda: observe("i['nb_frames']", i['nb_frames']), path=ep, rule='probe.metadata_operation', operands={'operation': "i['nb_frames']"}, source={'provider': 'source facts', 'provider_path': "'nb_frames'"}, status='unknown')}（或声明 loop）", ep, 'validate.audio_source_bounds', {'value': need, 'unit': 'frames'}, {'max': observed.get("i['nb_frames']", {'status': 'not_observed'}), 'unit': 'frames'})
        for a, b in zip(evs, evs[1:]):
            if native(ap + '.events', lambda: field(a, 'out', f'{ap}.events[{event_indices[id(a)]}]') > field(b, 'in', f'{ap}.events[{event_indices[id(b)]}]'), 'comparison', paths=[key_path(f'{ap}.events[{event_indices[id(a)]}]', 'out'), key_path(f'{ap}.events[{event_indices[id(b)]}]', 'in')]): err(f'音频轨内事件重叠: {name}', f'{ap}.events[{event_indices[id(b)]}].in', 'validate.audio_overlap', {'previous_end': observed.get(key_path(f'{ap}.events[{event_indices[id(a)]}]', 'out'), {'status': 'not_observed'}), 'start': observed.get(key_path(f'{ap}.events[{event_indices[id(b)]}]', 'in'), {'status': 'not_observed'}), 'unit': 'frames'}, {'max_previous_end': observed.get(key_path(f'{ap}.events[{event_indices[id(b)]}]', 'in'), {'status': 'not_observed'}), 'unit': 'frames'})
    # v2 §2.1 不变量 ③：插件键三分支 + 未知键警告（扫描范围 sources[]/audio_tracks[]
    # （含 events[]）/project；`_meta` 永久白名单）。已声明未装/被禁用进 errs FAIL；
    # 未知键只警告不阻断——现有 timeline（键全部已知或已声明）零新输出。
    _plugin_warns = _scan_plugin_keys(c, errs)
    # v2 §2.2 validate 钩子（协议 v1.1 增补）：插件级契约早失败（互斥/_meta 双施/
    # 键值形状），错误行并入 errs 阻断——仅对三分支放行后的生效插件调用。
    for plugin_error in _plugin_hooks.run_validate(c):
        err(plugin_error)
    if errs:
        print(f"校验失败（{len(errs)} 项）："); [print("  ✗", e) for e in errs]
        c.receipt("validate", False, f"{len(errs)} errors")
        failure = SystemExit(1)
        mark_handled(failure)
        raise failure
    for w in _plugin_warns:
        print("  ⚠", w)
    nclips = sum((len(t['clips']) for t in field(c.tl, 'video_tracks', '$')))
    msg = (f"{len(srcs)} 源 / {len(field(c.tl, 'video_tracks', '$'))} 视频轨 {nclips} clips / {len(field(c.tl, 'subtitles', '$'))} 字幕 / {len(field(c.tl, 'audio_tracks', '$'))} 音轨 {sum((len(t['events']) for t in field(c.tl, 'audio_tracks', '$')))} 事件")
    print("✓ 校验通过：" + msg); c.receipt("validate", True, msg)


# ---------------------------------------------------------------- audit
# 指标审计（建议性）：对照主流 UP主 实测基准（叙事风格分析/画面构成与节奏，2026-08-21）
# 检查的是"编排合理性"而非机制合法性——只提示，不阻断渲染。validate 是硬门，audit 是体检。
def _union(wins):
    out = []
    for s, e in sorted(wins):
        if out and s <= out[-1][1]: out[-1] = (out[-1][0], max(out[-1][1], e))
        else: out.append((s, e))
    return out

def _complement(wins, total):
    out, cur = [], 0.0
    for s, e in _union(wins):
        if s > cur: out.append((cur, s))
        cur = max(cur, e)
    if cur < total: out.append((cur, total))
    return out

def audit(c: Ctx):
    def field(obj, key, parent):
        return required_field(c, obj, key, parent, rule='audit.required_field')
    def calc(path, fn, expression, source=None):
        return operation(c, fn, path=path, rule='audit.operation',
                         operands={'operation': expression}, source=source)
    def subfield(sub, key, index):
        path, source = subtitle_location(c, index)
        return required_field(c, sub, key, path, rule='audit.required_field', source=source)
    D = calc('$.project.duration_frames', lambda: c.dur * c.spf, 'duration_frames * seconds_per_frame')
    lines = []
    def metric(message, rule, path, measured, threshold, warn=False, source=None, status=None):
        emit(c, message, rule_id=rule, json_path=path, measured=measured, threshold=threshold,
             severity='warning' if warn else 'info', status=status or ('warn' if warn else 'pass'),
             source=source, suggested_actions=[{'op': 'review_editorial_choice', 'params': {'json_path': path}}])
    def ind(name, status, detail=""):
        lines.append({"name": name, "status": status, "detail": detail})
        print(f"  {'✓' if status=='ok' else ('⚠' if status=='warn' else 'ℹ')} {name}  {detail}")
    # 1) 画面构成（kind 时间占比，同一 kind 多轨取并集）
    kind_wins = {}
    for ti, tr in indexed(c, field(c.tl, "video_tracks", "$"), "$.video_tracks"):
        tp = f"$.video_tracks[{ti}]"
        kd = calc(tp, lambda: tr.get("kind", "other"), "track.get(kind)")
        calc(tp+".kind", lambda: kind_wins.setdefault(kd, []), "group by kind")
        for ci, cl in indexed(c, field(tr, 'clips', tp), tp+'.clips'):
            cp = f'{tp}.clips[{ci}]'
            kind_wins[kd].append(calc(cp, lambda: (field(cl, 'dst_in', cp) * c.spf,
                (field(cl, 'dst_in', cp) + field(cl, 'dur', cp)) * c.spf), 'clip interval in seconds'))
    comp = {kd: sum(e - s for s, e in calc("$.video_tracks", lambda: _union(w), "union intervals")) for kd, w in kind_wins.items()}
    comp_s = " / ".join(f"{k} {calc('$.project.duration_frames', lambda: v/D*100, 'composition percentage'):.0f}%" for k, v in calc("$.video_tracks", lambda: sorted(comp.items()), "sort composition kinds"))
    metric('画面构成', 'audit.composition', '$.video_tracks',
           {'by_kind': comp, 'total': D, 'unit': 's'}, None)
    ind("画面构成", "ok", comp_s)
    if "aroll" not in comp and "gfx" not in comp and "screen" not in comp:
        metric('模块完整性', 'audit.modules', '$.video_tracks', {'kinds': list(comp)},
               {'any_of': ['aroll', 'screen', 'gfx']}, True)
        ind("模块完整性", "warn", "无 aroll/screen/gfx——纯 B-roll 成片，请确认是有意为之")
    for kd in ("broll", "screen", "gfx"):
        if kd not in comp:
            metric(f'缺少 {kd} 模块', 'audit.module_missing', '$.video_tracks', {'kind': kd, 'present': False},
                   {'present': True}, status='info')
            ind(f"缺少 {kd} 模块", "info", "可用该类画面增加视觉变化（主流技术视频均有覆盖）")
    # 2) 黑场窗口（无任何视频层的帧段——机制合法，但多半是编排遗漏）
    all_v = [w for ws in kind_wins.values() for w in ws]
    blacks = [(s, e) for s, e in calc("$.video_tracks", lambda: _complement(all_v, D), "complement video intervals") if e - s > 0.5]
    for start, stop in blacks:
        metric('blacks', 'audit.video_gap', '$.video_tracks',
               {'start': start, 'end': stop, 'unit': 's'},
               {'max': 0.5, 'unit': 's'}, True)
    if blacks:
        fmt = "、".join(f"{s:.1f}-{e:.1f}s" for s, e in blacks[:5])
        ind("黑场窗口（无视频内容）", "warn", f"{len(blacks)} 段：{fmt}（若非有意留黑请补覆盖层）")
    else:
        ind("黑场窗口", "ok", "全程有画面")
    # 3) 无声窗口（BGM 铁律：主流视频口播间隙 100% 有音乐/音效铺底）
    all_a = []
    for ti, tr in indexed(c, field(c.tl, 'audio_tracks', '$'), '$.audio_tracks'):
        ap = f'$.audio_tracks[{ti}]'
        for ei, e in indexed(c, field(tr, 'events', ap), ap+'.events'):
            ep = f'{ap}.events[{ei}]'
            all_a.append(calc(ep, lambda: (field(e, 'in', ep) * c.spf,
                                          field(e, 'out', ep) * c.spf), 'audio interval in seconds'))
    silents = [(s, e) for s, e in calc("$.audio_tracks", lambda: _complement(all_a, D), "complement audio intervals") if e - s > 0.3]
    for start, stop in silents:
        metric('silents', 'audit.audio_gap', '$.audio_tracks',
               {'start': start, 'end': stop, 'unit': 's'},
               {'max': 0.3, 'unit': 's'}, True)
    if silents:
        fmt = "、".join(f"{s:.1f}-{e:.1f}s" for s, e in silents[:5])
        ind("无声窗口（无任何音频事件）", "warn", f"{len(silents)} 段：{fmt}——bed 轨应全程铺底")
    else:
        ind("无声窗口", "ok", "全程有音频（BGM 铺底达标）")
    # 4) 口播覆盖率（voice 轨并集；主流观察 83-99.7%，本项目目标 85-90%）
    voice_entries = [(ti, tr) for ti, tr in indexed(c, field(c.tl, 'audio_tracks', '$'), '$.audio_tracks')
                if field(tr, 'name', f'$.audio_tracks[{ti}]') == 'voice']
    voice_tr = [tr for _, tr in voice_entries]
    if voice_tr:
        voice_index = voice_entries[0][0]
        voice_path = f'$.audio_tracks[{voice_index}]'
        voice_windows = []
        for ei, e in indexed(c, field(voice_tr[0], 'events', voice_path), voice_path+'.events'):
            ep = f'{voice_path}.events[{ei}]'
            voice_windows.append(calc(ep, lambda: (field(e, 'in', ep)*c.spf,
                                                   field(e, 'out', ep)*c.spf), 'voice interval in seconds'))
        vw = calc(voice_path, lambda: _union(voice_windows), 'union voice intervals')
        cov = calc(voice_path, lambda: sum(e - s for s, e in vw) / D, 'voice coverage / project duration')
        st = "ok" if 0.70 <= cov <= 0.98 else "warn"
        metric('口播覆盖率', 'audit.voice_coverage', voice_path, {'value': cov, 'unit': 'ratio'},
               {'min': .70, 'max': .98, 'unit': 'ratio'}, st == 'warn')
        ind("口播覆盖率（voice 轨）", st, f"{cov*100:.1f}%（主流观察 83-99.7%，目标 85-90%）")
        # 5) 语速（字幕字数 ÷ voice 占用秒数；标准值 5.5 字/s＝主流中位，基准见 data/speech_rate.json）
        chars = sum(calc(subtitle_location(c, si)[0], lambda: len(subfield(sub, 'text', si)), 'subtitle text length', subtitle_location(c, si)[1]) for si, sub in indexed(c, field(c.tl, 'subtitles', '$'), '$.subtitles'))
        vsec = sum(e - s for s, e in vw)
        if vsec > 0 and chars > 0:
            rate = chars / vsec
            st = "ok" if 4.8 <= rate <= 6.3 else "warn"
            metric('全片语速', 'audit.voice_rate', voice_path,
                   {'value': rate, 'characters': chars, 'voice_seconds': vsec, 'unit': 'characters/s'},
                   {'min': 4.8, 'max': 6.3, 'unit': 'characters/s'}, st == 'warn')
            ind("全片语速（字/秒）", st,
                f"{rate:.2f} 字/s（标准值 5.5，偏差 {(rate-5.5)/5.5*100:+.0f}%；"
                f"主流中位 5.56、P25-P75 4.95~5.76；<4.8 偏慢拖节奏，>6.3 高密度口播需确认是有意为之）")
    else:
        metric('没有 voice 轨', 'audit.voice_missing', '$.audio_tracks', None,
               {'track_name': 'voice'}, True, status='unknown')
        ind("voice 轨", "warn", "没有名为 voice 的音轨，口播覆盖率/语速无法评估")
    # 6) 字幕质量（阅读速度/长度/最短时长/相互重叠）
    #    瞬时语速基准（15758 行实测）：P50=5.68 P90=7.41 P95=8.02 P99=9.37；>8字/s 占 5.1%
    n_fast = n_long = n_short = 0
    subtitle_indices = {}
    for si, s in indexed(c, field(c.tl, 'subtitles', '$'), '$.subtitles'):
        subtitle_indices[id(s)] = si
        subpath, subsource = subtitle_location(c, si)
        d = calc(subpath, lambda: (subfield(s, 'out', si) - subfield(s, 'in', si)) * c.spf, 'subtitle duration in seconds', subsource)
        n = calc(subpath, lambda: len(subfield(s, 'text', si)), 'subtitle text length', subsource)
        if (cps := calc(subpath, lambda: n / d, 'characters / duration_seconds', subsource)) > 9.0:
            n_fast += 1
            cps_status = 'warn'
        elif (cps := calc(subpath, lambda: n / d, 'characters / duration_seconds', subsource)) > 7.5:
            n_long += 1
            cps_status = 'info'
        else:
            cps_status = 'pass'
        if (short := d < 0.6): n_short += 1
        metric('字幕阅读速度', 'audit.subtitle_cps', subpath,
               {'value': cps, 'unit': 'characters/s'},
               {'warning_above': 9.0, 'advisory_above': 7.5, 'unit': 'characters/s'},
               cps_status == 'warn', subsource, status=cps_status)
        metric('字幕最短时长', 'audit.subtitle_duration', subpath,
               {'value': d, 'unit': 's'}, {'min': .6, 'unit': 's'}, False,
               subsource, status='info' if short else 'pass')
    subs_s = calc('$.subtitles', lambda: sorted(field(c.tl, 'subtitles', '$'), key=lambda x: x['in']), 'sort subtitles by in')
    overlaps = 0
    for left, right in zip(subs_s, subs_s[1:]):
        pair = {}
        def overlap():
            pair['end'] = left['out']
            pair['start'] = right['in']
            return pair['end'] > pair['start']
        if calc('$.subtitles', overlap, 'subtitle overlap comparison'):
            overlaps += 1
            subpath, subsource = subtitle_location(c, subtitle_indices[id(right)])
            metric('字幕相互重叠', 'audit.subtitle_overlap', subpath,
                   {'previous_index': subtitle_indices[id(left)], 'previous_end': pair['end'],
                    'start': pair['start'], 'unit': 'frames'},
                   {'max_previous_end': pair['start'], 'unit': 'frames'}, True, subsource)
    st = "ok" if (n_fast == 0 and overlaps == 0) else "warn"
    ind("字幕阅读速度", st,
        f"过快(>9字/s≈P99) {n_fast} 条 / 偏快(>7.5≈P92) {n_long} 条 / 过短(<0.6s) {n_short} 条 / 相互重叠 {overlaps} 条")
    rep = {"benchmarks": "叙事风格分析与稿子方向.md / 画面构成与节奏.md（2026-08-21 实测）",
           "indicators": lines}
    # 体积体检（2026-09-11 治理升级）：cache/out 占用与 tmp 孤儿常驻审计报告——
    # 体积异常从人眼发现变体检常驻项
    from .hygiene import df as _df, tmp_orphans
    vol = _df(c, quiet=True)
    rep["volume"] = {"cache_bytes": vol["cache"], "out_bytes": vol["out"],
                     "total_bytes": vol["total"],
                     "total_h": f"{vol['total'] / 1024**3:.2f}G",
                     "tmp_orphans": [str(x) for x in tmp_orphans(c)],
                     "note": "详细分项见 `vtx df`；回收见 `vtx gc --dry`"}
    if not c.dry:   # dry-run 不落 out/ 产物（audit_report 属交付诊断件，2026-09-11 门控）
        fstxn.atomic_write(c.out / "audit_report.json", json.dumps(rep, ensure_ascii=False, indent=2))
    warns = sum(1 for x in lines if x["status"] == "warn")
    print(f"审计：{len(lines)} 项指标，{warns} 条建议"
          + ("（dry-run，报告未落盘）" if c.dry else "（详见 out/audit_report.json，建议性不阻断）"))
    c.receipt("audit", True, f"{len(lines)} indicators, {warns} advisories")


# ---------------------------------------------------------------- prepare
def source_audio_extraction(c: Ctx, sid: str):
    """§2.2 缝 A 单一实现：prepare 逐源提取与 range `_ensure_audio_wavs` 冷填充
    收敛为**同函数同命令**（对抗审查 P0 复审 P1-5 裁定——两处各自为政时区间预览
    将绕过降噪裸抽 wav）。

    返回 (wav_path, cmd|None)：wav 已存在或缝 A 插件已产出 → cmd=None（无须提取）；
    否则 cmd = 与 v1 逐字节相同的裸提取命令，由调用方执行（prepare 经 run_parallel
    线程池并行；range 冷填充顺序 c.run——执行策略归调用方，提取语义归本函数）。
    pluginlib prepare_source 钩子在本函数内统一触发（契约 v1.1：插件自算产物路径
    并保证在盘，`cache/audio/{audio_key}::<plugin>:...wav`——裸提取键不被插件产物
    污染；未声明键/无插件 = 恒直抽裸 wav；插件环境缺失的 fail-hard 语义由钩子
    实现并在此生效，§2.2）。插件产物**串行内联产出**（conda 推理链非单条 ffmpeg
    命令，不入 run_parallel 任务表；降噪是瓶颈本身，串行可接受）。
    """
    wav = c.cache / "audio" / f"{audio_key(c, sid)}.wav"
    from .recovery import audio_cache_valid
    if audio_cache_valid(c, wav, sid):
        from .events import cache_hit
        cache_hit(c, "audio_cache_valid")
        return wav, None
    p = _plugin_hooks.run_prepare_source(c, sid)
    if p is not None:
        return p, None         # 缝 A：源声明了插件键（如 denoise）→ 插件产物
    fstxn.mkdir(wav.parent)
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(c.src_path(sid)),
           "-vn", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(wav)]
    return wav, cmd


def prepare(c: Ctx):
    fstxn.mkdir(c.cache / "audio")
    tasks = []
    for s in c.tl["sources"]:
        # 内容寻址键（2026-09-11 治理升级）：源变了键就变，旧 wav 由 gc 回收；
        # 提取语义（键/命令/缝 A 触发）收敛在 source_audio_extraction（§2.2 缝 A）
        _wav, cmd = source_audio_extraction(c, s["id"])
        if cmd is not None:
            tasks.append((f"audio {s['id']}", cmd))
    run_parallel(c, tasks, "音频提取")
    # Do not re-resolve source keys (or invoke plugins) after an input swap.
    c.verify_inputs("prepare")
    for s in c.tl["sources"]:
        # 记账键 = 产物名 stem（裸提取即 audio_key；插件产物即其内容寻址键——
        # manifest 只记账不做删除判据，两形态同栏即可）
        wav, _cmd = source_audio_extraction(c, s["id"])
        if _cmd is not None and not c.dry:
            c.receipt("prepare", False, "incomplete extracted audio; rerun prepare")
            print(f"✗ incomplete extracted audio: {wav}", file=sys.stderr)
            sys.exit(1)
        if wav.exists():
            manifest_record(c, wav, "prepare", wav.stem)
    msg = f"{len(c.tl['sources'])} → 48kHz stereo PCM"
    print("✓ 音频提取：" + msg); c.receipt("prepare", True, msg)


# ---------------------------------------------------------------- audio
def _audio_graph(c: Ctx, win=None, margin_frames: int = 60):
    """构建多轨混音 filtergraph（区间渲染与整片 build_audio 共用，唯一实现）。

    win=None：与现行全片行为逐字一致（全部事件、apad 补齐成片全长）。
    win=(fa, fb)（区间渲染预览，方案五阶段1）：只纳入与 [fa-margin, fb+margin)
    相交的音频事件——margin（默认 60 帧=2s@30fps）兜住 sidechaincompress 的
    release 尾巴与淡出；事件 adelay 仍用全局时间（不平移），终混后由调用方在
    mix_head 尾部按采样网格 atrim 到 [fa,fb)。窗口内无事件的 duck/sidechain 轨
    自动跳过（sidechaincompress 需要双流齐备，缺流会让 ffmpeg 挂起解析）。
    返回 (cmd_inputs, chains, n_ev, duck_used)：cmd_inputs = 输入参数片段列表。
    """
    atracks = c.tl["audio_tracks"]
    # v2 §2.2 缝 B 挂载点：构建 filtergraph 前对全量 tracks 的纯内存 events 变换
    # （duck-static 型插件；无插件注册 = 原样恒等，P1-2 空转零行为变化）。
    # 不写回 timeline（幂等天然成立）；区间渲染走同一缝（win 过滤在变换之后，
    # 窗口模式自动正确，无需特判）。
    atracks = _plugin_hooks.run_audio_events(c, atracks)
    fa, fb = (0, c.dur) if win is None else win
    inside = (lambda e: True) if win is None else \
        (lambda e: e["in"] < fb + margin_frames and e["out"] > fa - margin_frames)
    cmd = ["ffmpeg", "-v", "error", "-y"]
    # §2.2 缝 A 读取点：事件源 wav 经 resolve_source_wav 统一解析（声明了插件键
    # 的源读插件产物，其余读裸提取键）——与 source_audio_extraction 产出侧同一
    # 解析，prepare 与 graph 两处恒一致；逐源 memo 防同源多事件重复解析。
    wav_of = {}

    def _wav(sid: str) -> Path:
        if sid not in wav_of:
            p = _plugin_hooks.resolve_source_wav(c, sid)
            wav_of[sid] = Path(p) if p is not None else \
                c.cache / "audio" / f"{audio_key(c, sid)}.wav"
        return wav_of[sid]

    evs = [(ti, tr, e) for ti, tr in enumerate(atracks) for e in tr["events"] if inside(e)]
    for ti, tr, e in evs:
        if e.get("loop"): cmd += ["-stream_loop", "-1"]
        cmd += ["-i", str(_wav(e["src"]))]
    # 事件链（atrim 采样网格/音量/声像/淡变/adelay）→ 轨内子混音 → 补齐成片全长
    chains = []
    trk_lbl = {}
    k = 0
    present = set()
    for ti, tr, e in evs:
        present.add(ti)
    for ti, tr in enumerate(atracks):
        ev_lbls = []
        for _ti, _tr, e in [x for x in evs if x[0] == ti]:
            dur = e["out"] - e["in"]; sin = e.get("src_in", 0)
            # fade 钳制 ≤ 事件时长（D-A 2026-10-05：fade>时长 → afade st<0 报错；
            # duck-static 插件已在上游切段时钳制，此处兜底手写 timeline 的同类错）
            fi = min(e["fade_in_frames"], dur) if e.get("fade_in_frames") else 0
            fo = min(e["fade_out_frames"], dur) if e.get("fade_out_frames") else 0
            fl = [f"atrim=start_sample={sin*c.spf_samples}:end_sample={(sin+dur)*c.spf_samples}", "asetpts=PTS-STARTPTS"]
            fl.append(f"volume={e['volume']}")
            if e.get("pan"):
                p = e["pan"]; lg = min(1.5, 1 - p * .5); rg = min(1.5, 1 + p * .5)
                fl.append(f"pan=stereo|c0={lg:.3f}*c0|c1={rg:.3f}*c1")
            if fi: fl.append(f"afade=t=in:st=0:d={fi*c.spf:.6f}")
            if fo:
                fl.append(f"afade=t=out:st={(dur-fo)*c.spf:.6f}:d={fo*c.spf:.6f}")
            fl.append(f"adelay=delays={e['in']/c.fps*1000:.3f}:all=1")
            chains.append(f"[{k}:a]" + ",".join(fl) + f"[e{k}]")
            ev_lbls.append(f"[e{k}]")
            k += 1
        if not ev_lbls: continue     # 窗口内无事件的轨：不进混音（静音轨无贡献）
        pad = (f"apad=whole_dur={c.dur*c.spf:.3f},"
               f"atrim=end_sample={c.dur*c.spf_samples},asetpts=PTS-STARTPTS")
        if len(ev_lbls) == 1:
            chains.append(f"{ev_lbls[0]}{pad}[t{ti}]")
        else:
            chains.append(f"{''.join(ev_lbls)}amix=inputs={len(ev_lbls)}:duration=longest:normalize=0,{pad}[t{ti}]")
        trk_lbl[ti] = f"[t{ti}]"
    # 音乐避让：duck:true 的轨被 duck_by（默认 voice）的混音经 sidechaincompress 压低
    duck_tis = [ti for ti in present if atracks[ti].get("duck")]
    duck_used = False
    if duck_tis:
        by_names = {n for ti in duck_tis for n in atracks[ti].get("duck_by", ["voice"])}
        sc_tis = [ti for ti in present if trk_lbl.get(ti) and atracks[ti]["name"] in by_names
                  and ti not in duck_tis]
        if sc_tis:
            duck_used = True
            if len(sc_tis) == 1:
                # 修复（2026-09-10，voice-ime BGM v2 轮定位）：asplit=恰 N 路把 sidechain 轨
                # （voice）整轨吞尽，终混仍引用原标签→该 ffmpeg 版本静默解析为空流=口播
                # 整轨缺失（复现：最小三轨 bed+voice+duck bgm）。多分一路并重绑原轨标签，
                # 终混引用恢复有效。
                chains.append(f"{trk_lbl[sc_tis[0]]}asplit={len(duck_tis) + 1}" +
                              "".join(f"[sc{j}]" for j in range(len(duck_tis))) +
                              trk_lbl[sc_tis[0]])
            else:
                # 同族修复：多 sidechain 轨先各自 asplit 保一路原标签再 amix 做 key，
                # 否则 amix 消费原标签后终混同样丢这些轨。
                for ti in sc_tis:
                    chains.append(f"{trk_lbl[ti]}asplit=2[scx{ti}]{trk_lbl[ti]}")
                chains.append(f"{''.join(f'[scx{ti}]' for ti in sc_tis)}"
                              f"amix=inputs={len(sc_tis)}:duration=longest:normalize=0[scm]")
                chains.append(f"[scm]asplit={len(duck_tis)}" + "".join(f"[sc{j}]" for j in range(len(duck_tis))))
            for j, ti in enumerate(duck_tis):
                # 轨级 duck_params 覆盖缺省（2026-09-11）：threshold 线性值 / ratio /
                # attack_ms / release_ms / makeup，单位与 sidechaincompress 一致
                dp = atracks[ti].get("duck_params", {})
                thr = dp.get("threshold", DUCK_THRESHOLD)
                rat = dp.get("ratio", DUCK_RATIO)
                atk = dp.get("attack_ms", DUCK_ATTACK_MS)
                rel = dp.get("release_ms", DUCK_RELEASE_MS)
                mkup = dp.get("makeup", DUCK_MAKEUP)
                chains.append(f"{trk_lbl[ti]}[sc{j}]sidechaincompress=threshold={thr}:ratio={rat}:"
                              f"attack={atk}:release={rel}:makeup={mkup}[td{ti}]")
                trk_lbl[ti] = f"[td{ti}]"
                print(f"  duck[{atracks[ti]['name']}]: threshold={thr} ratio={rat} "
                      f"attack={atk}ms release={rel}ms makeup={mkup}")
        else:
            print("⚠ duck 声明无效：找不到 duck_by 指向的非 duck 轨，跳过避让")
    # 总混音头（不归一，保留音量比例）：全片补齐到 dur；窗口模式多一截 atrim 到
    # [fa,fb) 采样网格（apad 定长在上游已保证窗口尾不足时有静音可截）。
    tail = (f"atrim=end_sample={c.dur*c.spf_samples},asetpts=PTS-STARTPTS"
            if win is None else
            f"atrim=start_sample={fa*c.spf_samples}:end_sample={fb*c.spf_samples},"
            f"asetpts=PTS-STARTPTS")
    mix_head = (f"{''.join(trk_lbl[ti] for ti in sorted(trk_lbl))}"
                f"amix=inputs={len(trk_lbl)}:duration=longest:normalize=0,aresample=48000,{tail}")
    chains.append(mix_head)
    return cmd, chains, len(evs), duck_used


def mix_audio_wav(c: Ctx, out_wav: Path, win=None, gain_override=None):
    """两遍响度混音（共用执行体）：第一遍 ebur128 实测原始混音 I，第二遍施加恒定
    静态增益到目标响度（单遍 dynamic loudnorm 长节目塌陷，见 build_audio 注）。
    输出链内置 alimiter 采样限幅（v2 §1.2，2026-09-13 吸收 post_loudness.py）：
    `volume={gain}dB,aformat=sample_fmts=s16,alimiter=limit=0.599:level=false:
    latency=true`——build_audio 与区间预览（range.py 直调本函数）统一过 limiter，
    out/audio.wav 恒为「归一+限幅」终态，杜绝「成片有限幅、预览无限幅」的分叉。
    **s16 量化中间步（P5 门 8 实测裁定 2026-09-14）**：v1 终链是「float 混音→
    写 s16 audio.wav→post 进程 volume+alimiter」两段——中间量化不是可删细节：
    纯浮点单段链对近阈值峰的限幅处置有系统性偏移，bench_p2 实测集成响度
    -15.5(v1)→-15.7(v2)，边界内容 G2 直接翻车。aformat 复刻 v1 域（吸收契约
    「逐条照搬」），双重量化音质差不可闻（v1 按此上线数月）。
    gain_override：外部指定静态增益（区间预览用——窗口局部内容响度 ≠ 全片响度，
    窗口自测增益会把安静段放大到削波；复用整片回执里的 gain_db 保持听感一致，
    且省一遍 ebur128 测量）。返回 (gain, raw_i, n_ev, duck_used)。win 语义见 _audio_graph。"""
    cmd, chains, n_ev, duck_used = _audio_graph(c, win)
    gain = 0.0
    raw_i = None
    if gain_override is not None:
        gain = gain_override
    elif not c.dry:
        meas_cmd = list(cmd)
        meas_cmd[meas_cmd.index("error")] = "info"  # -v error → info：ebur128 Summary 是 info 级
        r = c.run(meas_cmd + ["-filter_complex", ";".join(chains[:-1] + [chains[-1] + ",ebur128=framelog=quiet[amet]"]),
                              "-map", "[amet]", "-f", "null", "-"])
        mI = re.search(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
        if not mI: print("✗ 原始混音响度测量失败"); sys.exit(1)
        raw_i = float(mI.group(1)); gain = TARGET_LUFS - raw_i
        print(f"  原始混音 I={raw_i:.1f} LUFS → 静态增益 {gain:+.1f}dB")
    c.run(cmd + ["-filter_complex", ";".join(chains[:-1] + [chains[-1] + f",volume={gain:+.2f}dB,aformat=sample_fmts=s16,alimiter=limit=0.599:level=false:latency=true[aout]"]),
                 "-map", "[aout]", "-c:a", "pcm_s16le", "-ar", "48000", str(out_wav)])
    return gain, raw_i, n_ev, duck_used


def _ebur128_i_from_log(stderr: str) -> float:
    """ebur128 Summary 的整合响度 I 解析（post_loudness.py 原实现照搬）：取末个 I 值，
    静音/不可测（-inf / ≤-70）抛 ValueError（无法归一）。"""
    values = re.findall(r"I:\s+(-?[\d.]+|[-+]?inf) LUFS", stderr)
    if not values or not math.isfinite(float(values[-1])) or float(values[-1]) <= -70:
        raise ValueError("Cannot normalize silent or unmeasurable audio")
    return float(values[-1])


def _wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


def build_audio(c: Ctx):
    atracks = c.tl["audio_tracks"]
    out_wav = c.out / "audio.wav"
    gain, raw_i, _n_ev, duck_used = mix_audio_wav(c, out_wav)
    msg = f"{len(atracks)} 轨 {sum(len(t['events']) for t in atracks)} 事件（帧网格对齐"
    if duck_used:
        msg += f"，{sum(1 for tr in atracks if tr.get('duck'))} 轨 duck 避让"
    msg += f"，响度 {TARGET_LUFS:g} LUFS（两遍静态增益 + alimiter 限幅）"
    print("✓ 音频混音：" + msg)
    extra = {"gain_db": round(gain, 2), "target": TARGET_LUFS}
    if raw_i is not None:
        # raw_i 归档（勘误 §1.2：全仓无代码消费回执 raw_i——post_loudness 时代每次
        # 实测，旧注释「免首测复用」失修已删；gain_db 的真实消费者 = range.py 区间
        # 渲染复用整片增益）
        extra = {"raw_i": raw_i, **extra}
    if not c.dry:
        # —— 终态校验（v2 §1.2；分级裁定 2026-09-13 影子复验期：机制/内容分离）——
        # 一遍 ebur128 同时取 I 与 TP：
        #   硬 FAIL（机制错误）：TP > -1 dBFS（limiter 失效）；时长漂移；I 偏离 >3 LU
        #     （增益链断裂——如增益未施 I 会停在 raw_i 量级）；
        #   WARN（内容级）：I 偏离 ∈ (1.5, 3]——限幅响度损耗属配乐结构/原始混音问题，
        #     发布侧内容门 G2 拦截，提示调 bed 音量重渲；
        #   ≤1.5 静默通过（v1 post_loudness ±1.5 语义原样保留）。
        # 裁定证据：live voice-ime v1 回执 post 字段 input_i=-14.0 → output_i=-15.5
        # （|偏离|=1.5 恰压线通过并已交付）；同内容 v2 浮点域限幅实测 -15.6——边界
        # 内容 + 0.1 LU 级测量抖动不得成为管线 flaky 硬 FAIL。
        r = c.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(out_wav),
                   "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"])
        try:
            after = _ebur128_i_from_log(r.stderr)
        except ValueError as e:
            print(f"✗ 响度终态复测失败：{e}"); sys.exit(1)
        m_tp = re.search(r"Peak:\s*(-?[\d.]+) dBFS", r.stderr)   # 与 qc.py TP 门同一解析口径
        after_tp = float(m_tp.group(1)) if m_tp else None
        dev = abs(after - TARGET_LUFS)
        print(f"  终态复测 I={after:.1f} LUFS"
              + (f" / TP={after_tp:.1f} dBFS" if after_tp is not None else "")
              + f"（目标 {TARGET_LUFS:g}）")
        if after_tp is None:
            print("✗ 终态复测未捕获真峰（ebur128 peak 解析失败）"); sys.exit(1)
        if after_tp > -1.0:
            print(f"✗ 终态真峰 {after_tp:.1f} dBFS > -1.0（limiter 失效——检查输出链）"); sys.exit(1)
        if dev > 3.0:
            print(f"✗ 终态响度偏离目标 {dev:.1f} LU（>3，疑增益链断裂）"); sys.exit(1)
        if dev > 1.5:
            print(f"⚠ 终态响度 {after:.1f} LUFS 偏离目标 {dev:.1f} LU（>1.5）——限幅响度损耗"
                  "属内容级问题（配乐密集/原始混音偏 quiet），发布侧内容门 G2 会拦截；"
                  "如需硬过请调 bed 音量后重渲")
        with wave.open(str(out_wav), "rb") as w:
            n_samples = w.getnframes()
        expect = c.dur * c.spf_samples
        if abs(n_samples - expect) > 1:
            print(f"✗ 音频时长校验失败：{n_samples} 采样 ≠ 期望 {expect}（±1 采样）"); sys.exit(1)
        extra["post_loudness"] = {
            "input_i": raw_i, "output_i": after, "gain_db": gain,
            "target": TARGET_LUFS, "tp_dbfs": round(after_tp, 2),
            "sha256": hashlib.sha256(out_wav.read_bytes()).hexdigest()}
    c.receipt("audio", True, msg, extra=extra)


def post_loudness_compat(project, ffmpeg="ffmpeg", target=TARGET_LUFS):
    """v2 兼容重跑（scripts/post_loudness.py 薄壳的内核实现，2026-09-13）：
    响度终链已并入 vtx audio（ARCHITECTURE §1.2），`vtx audio` 产出的 out/audio.wav
    恒为「归一+限幅」终态，R4 链不再需要独立 post 步。本函数保留旧 CLI 命令面的
    行为：对项目 out/audio.wav 再走一遍「测 I → volume={gain}dB,alimiter → 复测
    ±1.5 LU → 时长 ±1 采样 → 原子替换」。**幂等性收窄声明（P1 对抗审查 F1 修正）**：
    真幂等仅两种情形——①audio.wav 与回执 post_loudness.sha256 一致（已是终态，
    直接跳过返回已有测量）；②当前 I 已在 target（gain≈0）。对因限幅损耗停在
    target-1.5~target-2 的产物（voice-ime 类配乐密集内容，实测重跑一次 +1.9dB
    蠕升），本函数会按 v1 旧语义把响度再推向 target 并**改变交付响度**——打印
    蠕升警告；v2 正常链路无需本命令。与旧实现的差异（v2 单写者语义）：不做回执
    摘除-回写手术（audio 阶段回执照常有效），返回测量 dict（含 tp_dbfs）由调用
    方打印。"""
    if not math.isfinite(target):
        raise ValueError("Target must be finite")
    source = Path(project).resolve() / "out" / "audio.wav"
    if not source.is_file():
        raise FileNotFoundError(f"audio source not found: {source}")
    sha_now = hashlib.sha256(source.read_bytes()).hexdigest()
    receipt = source.parent / "receipt.json"
    if receipt.is_file():
        try:
            rec = json.loads(receipt.read_text(encoding="utf-8"))
            prev = ((rec.get("stages") or {}).get("audio") or {}).get("post_loudness")
        except Exception:
            prev = None
        if isinstance(prev, dict) and prev.get("sha256") == sha_now:
            print("v2: audio.wav 与回执 post_loudness.sha256 一致（已是 vtx audio 终态），"
                  "跳过兼容重跑")
            return prev
    print("v2: 兼容重跑（v1 post_loudness 语义；v2 正常链路无需本命令）")

    def measure(path):
        r = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", str(path),
                            "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode:
            raise RuntimeError(r.stderr[-3000:])
        m_tp = re.search(r"Peak:\s*(-?[\d.]+) dBFS", r.stderr)
        return _ebur128_i_from_log(r.stderr), (float(m_tp.group(1)) if m_tp else None)

    before, _before_tp = measure(source)   # 现文件实测（v1 教训：不读回执 raw_i，防双施 gain）
    seconds = _wav_seconds(source)
    gain = target - before
    if abs(gain) > 0.1:
        print(f"⚠ 本次将再施加 {gain:+.1f}dB 增益（v1 旧语义：把当前响度推回 target）——"
              "对限幅损耗产物重跑会改变交付响度；v2 正常链路（vtx audio）已内置限幅，"
              "如需调整响度请改 timeline 后重渲")
    # ffmpeg 子进程写者：locked_staging 给同目录随机名真实路径（子进程不能
    # 经 /proc 锚定），成功后原子替换回 source。
    with fstxn.locked_staging(source) as candidate:
        r = subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(source), "-af",
                            f"volume={gain:.3f}dB,alimiter=limit=0.599:level=false:latency=true",
                            "-c:a", "pcm_s16le", "-ar", "48000", '-f', 'wav', str(candidate)],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode:
            raise RuntimeError(r.stderr[-3000:])
        after, after_tp = measure(candidate)
        if abs(after - target) > 1.5:
            raise ValueError(f"Loudness {after} outside target {target} +/- 1.5 LU")
        if abs(_wav_seconds(candidate) - seconds) > 1 / 48000 + 1e-9:
            raise ValueError("Post-processing changed audio duration")
        result = {"input_i": before, "output_i": after, "gain_db": gain, "target": target,
                  "tp_dbfs": round(after_tp, 2) if after_tp is not None else None,
                  "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest()}
    return result


def load_timeline_check(timeline_path):
    """timeline 加载 + 契约校验（vtx.api 暴露的稳定入口）：构造 Ctx（fps 契约 /
    cache_dependencies 快速失败 + 输入指纹计算）后跑全量 validate（对照素材实测的
    硬门）。校验失败 validate 自身打印并 sys.exit(1)（内核 CLI 语义，调用方按需
    捕获 SystemExit）；成功返回 (ctx, timeline_dict)。"""
    c = Ctx(Path(timeline_path))
    validate(c)
    return c, c.tl


def mux(c: Ctx):
    if getattr(c, "draft", False):
        c.run(["ffmpeg", "-v", "error", "-y", "-i", str(c.out / "draft.mp4"),
               "-i", str(c.out / "audio.wav"), "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
               "-movflags", "+faststart", str(c.out / "draft_final.mp4")])
        print("✓ 封装 draft_final.mp4（草稿）"); c.receipt("mux_draft", True); return
    # D31 坑位：原生 aac 对真人素材（波峰因子 23-25dB 密集瞬态）解码真峰过冲实测可达 +8.5dB——正式封装用 libfdk_aac
    encs = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout
    acodec = "libfdk_aac" if "libfdk_aac" in encs else "aac"
    if acodec == "aac":
        print("⚠ libfdk_aac 不可用，回落原生 aac——终检必须复测编码后解码 TP（edit.md 坑表）")
    # 写临时路径再原子替换（P0-2 修复，2026-09-13 阶段2审查）：rotate_history 用
    # os.link 把旧 final 轮转进 out/history/——历史副本与 live 件共享 inode，若让
    # ffmpeg -y 直接写 final 路径（原地 O_TRUNC 同一 inode），每次渲染会把 live 件
    # 与全部 history「保留」副本一并改写为最新内容（2026-09-12 04:08 事故实锤：三名
    # 一 inode，历史保留只剩文件名里的 md5）。ffmpeg 先写 .final.tmp.mp4，成功后
    # os.replace 到 final——replace 只换目录项，旧 inode（含 history 硬链接组）内容
    # 原样保留。失败（c.run 内 sys.exit）时 finally 清理半写 tmp。
    # 版本记账（2026-09-11 治理升级）：覆盖前把旧 final 轮转进 out/history/
    # （--keep-versions，默认 2）并做 md5 覆盖检测——final 被并行工程静默改写时显著
    # 告警 + 旧条目标 superseded（D39/BGM-v2 事故类从此自动留痕）。
    final = c.out / "final.mp4"
    tmp = c.out / ".final.tmp.mp4"
    mux_cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(c.out / "video_noaudio.mp4"),
               "-i", str(c.out / "audio.wav"), "-c:v", "copy", "-c:a", acodec, "-b:a", "192k",
               "-movflags", "+faststart", str(tmp)]
    if not c.dry:   # dry-run 不轮转/删除 out/history/ 真实成片（2026-09-11 门控，
                    # 与下方 versions_record 门控同口径——预览运行不得动真实产物链）
        rotate_history(c, final, getattr(c, "keep_versions", 2))
        warn = versions_check(c, final)
        if warn: print("⚠⚠ 建议核对 out/history/ 与 VERSIONS.json 确认旧版归属后再继续分发")
    try:
        c.run(mux_cmd)
        if not c.dry:   # dry-run 不落产物：不做 replace（tmp 只存在于 [dry-run] 打印）
            fstxn.replace(tmp, final)
    finally:
        if not c.dry:
            fstxn.unlink(tmp)   # 成功路径 tmp 已被 replace 移走（无操作）；
                                          # 失败路径清理半写 tmp，不留占 out/ 的残件
    if not c.dry:   # dry-run 无产物可记账（versions_record 读 final.mp4，2026-09-11 门控）
        versions_record(c, final, "mux")
    print(f"✓ 封装 final.mp4（{acodec} 192k）"); c.receipt("mux", True, f"{acodec} 192k")
