#!/usr/bin/env python3
"""audit_bundle.py — BGM 正式版分轨审计包（人机协作交付门执行器，2026-09-10 用户裁定默认必做）

背景：用户必须在 Blender 里逐轨屏蔽/独听对比，才能参与到混音平衡与编排的创作裁决中
（voice-ime BGM v2 首例）。因此每一轮**正式版本** BGM 挂载进时间线后，除 vtx 渲染外
必须产出分轨审计包；草稿/内部迭代轮不要求。

一键五步：
  ①按轨道名前缀选中音轨，每个引用源 ffmpeg→320k MP3（试听件；生产素材是正本）
  ②生成审计 timeline：逐轨一列、生产音量/事件窗原样、源指向 MP3、无 voice 轨=无 duck
    ——用于 duck 和总混处理前的逐轨比较，不宣称与成片采样等价
  ③vtx validate 硬门
  ④json2blend → <audit-dir>/audit.blend（Blender 预览工程）
  ⑤verify_blend 结构校验

用法：
  python3 audit_bundle.py -t <项目>/timeline.json --prefix bgm2-
      [--prefix 可多次] [--mp3-dir <dir>] [--audit-dir <dir>] [--force] [--skip-blend]
      [--whole] [--silence-threshold -50dB] [--min-silence 1.0] [--pad 0.25]
  缺省 mp3-dir/audit-dir = <项目>/cache/audit_bgm/{mp3,.}；按源内容、FFmpeg 版本、
  转码规格命名并校验 MP3 内容。--force 强制重转，旧的轨名/mtime 缓存不复用。

静音切分（2026-09-10 用户裁定，默认开启；--whole 关闭）：分轨不是全程有声音，审计
timeline 按每轨实际发声区间切段（ffmpeg silencedetect 反相 + pad 余量 + 帧对齐），
事件仍锚定视频绝对时间——Blender 里每轨只在其发声段出现条带，静音区留白，观演/刮擦
不用再拖过无声段。同源共享 MP3，源时间映射到事件时间；循环/淡变及没有检出声音的
事件保持完整并报告回退，避免切分重启包络或产生无效空轨。--whole 保留所有原事件。

给用户的 Blender 用法（附在交付信息里）：
  blender <项目>/cache/audit_bgm/audit.blend → Sequencer 工作区 → 空格播放；
  通道头小喇叭=整轨静音；Ctrl+I 反选+H=独听该轨；选中条 Y 切开后可屏蔽某段时间区。
  注意：独听比成片"干"是预期（厅堂 wet 只在参考混）；这里无口播 duck，比成片略"满"。
"""
import argparse
import concurrent.futures
import json
import hashlib
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import tempfile
from pathlib import Path

_PRINT_LOCK = threading.Lock()


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def media_path(path, root):
    """Relative where possible; Windows cannot relativize across drives."""
    try:
        return os.path.relpath(path, root)
    except ValueError:
        return str(path.resolve())


def export_mp3(src, directory, profile, force=False):
    """Content-addressed, checked cache; a failed encoder never publishes a file."""
    identity = {'source_sha256': file_hash(src), 'profile': profile}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    dst = directory / f'{key}.mp3'
    receipt = dst.with_suffix('.json')
    if not force and dst.is_file() and receipt.is_file():
        try:
            record = json.loads(receipt.read_text(encoding='utf-8'))
            if record['identity'] == identity and record['output_sha256'] == file_hash(dst):
                return dst
        except (OSError, ValueError, KeyError, TypeError):
            pass
    fd, name = tempfile.mkstemp(prefix='.audit-', suffix='.mp3', dir=directory)
    os.close(fd)
    candidate = Path(name)
    try:
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(src),
                        '-map', '0:a:0', '-vn', '-ar', '48000', '-ac', '2',
                        '-c:a', 'libmp3lame', '-b:a', '320k', str(candidate)],
                       check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
        # Verify decoding too: an encoder exit alone does not certify a usable cache.
        subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(candidate), '-f', 'null', '-'],
                       check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if not candidate.stat().st_size:
            raise ValueError(f'Empty MP3: {src}')
        output_hash = file_hash(candidate)
        os.replace(candidate, dst)
        # Missing/partial receipt is a cache miss on the next run, never a hit.
        receipt.write_text(json.dumps({'identity': identity, 'output_sha256': output_hash},
                                      ensure_ascii=False, indent=2), encoding='utf-8')
        return dst
    finally:
        candidate.unlink(missing_ok=True)


def split_event(event, spans):
    """Map source-domain spans into this event, without restarting envelopes."""
    if event.get('loop') or event.get('fade_in_frames') or event.get('fade_out_frames'):
        return [dict(event)], 'loop_or_fade_preserved'
    start = event.get('src_in', 0)
    stop = start + event['out'] - event['in']
    events = []
    for a, b in spans:
        lo, hi = max(start, a), min(stop, b)
        if lo < hi:
            events.append({**event, 'src_in': lo,
                           'in': event['in'] + lo - start,
                           'out': event['in'] + hi - start})
    # Keep a valid event/track for silent or below-threshold material; do not
    # silently discard a creator's audio just because it missed the detector.
    return (events, None) if events else ([dict(event)], 'no_detected_content_preserved')


def tprint(msg):
    with _PRINT_LOCK:
        print(msg, flush=True)


def run(cmd, cwd=None, tail=3):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = "\n".join(r.stdout.strip().splitlines()[-tail:]) if r.stdout.strip() else ""
    print(f"[exit={r.returncode}] {' '.join(map(str, cmd[:4]))}…")
    if out:
        print(out)
    if r.returncode != 0:
        print(r.stderr[-1500:], file=sys.stderr)
        sys.exit(1)
    return r


def content_spans(src: Path, dur_s: float, fps: int, thr: str, min_sil: float, pad: float):
    """silencedetect 反相求发声区间：[silence_start, silence_end) 的补集，加 pad 余量，
    合并后按帧对齐。返回 [(in_f, out_f), ...]。"""
    r = subprocess.run(
        ["ffmpeg", "-nostats", "-i", str(src), '-map', '0:a:0', '-vn',
         "-af", f"silencedetect=noise={thr}:d={min_sil}", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    sils = []
    for line in r.stderr.splitlines():
        if "silence_start:" in line:
            sils.append([float(line.split("silence_start:")[1].split()[0]), None])
        elif "silence_end:" in line and sils:
            sils[-1][1] = float(line.split("silence_end:")[1].split()[0])
    if sils and sils[-1][1] is None:          # 片尾静默无 end
        sils[-1][1] = dur_s
    # 反相：相邻静默之间即发声段
    spans = []
    if not sils:
        spans = [(0.0, dur_s)]
    else:
        edges = [0.0]
        for s in sils:
            edges += [s[0], s[1]]
        edges.append(dur_s)
        for a, b in zip(edges[0::2], edges[1::2]):
            if b - a > 1e-6:
                spans.append((a, b))
    # pad + 合并 + 帧对齐
    out = []
    for a, b in spans:
        a, b = max(0.0, a - pad), min(dur_s, b + pad)
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    frames = []
    for a, b in out:
        if b <= a:
            continue
        lo, hi = math.floor(a * fps), math.ceil(b * fps)
        if frames and lo <= frames[-1][1]:
            frames[-1] = (frames[-1][0], max(hi, frames[-1][1]))
        else:
            frames.append((lo, hi))
    return frames


def main():
    ap = argparse.ArgumentParser(description="BGM 分轨审计包生成器")
    ap.add_argument("-t", "--timeline", required=True, type=Path, help="项目生产 timeline.json")
    ap.add_argument("-p", "--prefix", action="append", default=None,
                    help="音轨名前缀，可多次（默认 bgm-；注意是 timeline 轨道名非源 ID）")
    ap.add_argument("--mp3-dir", type=Path, default=None)
    ap.add_argument("--audit-dir", type=Path, default=None)
    ap.add_argument("--force", action="store_true", help="强制重转全部 MP3")
    ap.add_argument("--skip-blend", action="store_true", help="只做 MP3+审计 timeline，不跑 Blender")
    ap.add_argument("--whole", action="store_true", help="关闭静音切分，保留每个生产事件及全部事件字段")
    ap.add_argument("--silence-threshold", default="-50dB", help="silencedetect 噪声门（默认 -50dB）")
    ap.add_argument("--min-silence", type=float, default=1.0, help="最短静默段秒数（默认 1.0）")
    ap.add_argument("--pad", type=float, default=0.25, help="每段两端余量秒数（默认 0.25）")
    a = ap.parse_args()

    tl_path = a.timeline.resolve()
    root = tl_path.parent
    tl = json.loads(tl_path.read_text(encoding="utf-8"))
    audit_dir = (a.audit_dir or root / "cache" / "audit_bgm").resolve()
    mp3_dir = (a.mp3_dir or audit_dir / "mp3").resolve()
    if (audit_dir / 'timeline.json') == tl_path:
        ap.error('audit-dir 不能覆盖生产 timeline.json')
    mp3_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)

    prefixes = a.prefix or ["bgm-"]
    sel = [tr for tr in tl["audio_tracks"] if tr["name"].startswith(tuple(prefixes))]
    if not sel:
        sys.exit(f"无音轨匹配前缀 {prefixes}（现有轨：{[t['name'] for t in tl['audio_tracks']]}）")

    if not math.isfinite(a.pad) or a.pad < 0 or not math.isfinite(a.min_silence) or a.min_silence <= 0:
        ap.error('pad 必须非负，min-silence 必须为正有限数')
    if any(not tr['events'] for tr in sel):
        ap.error('选中的音轨不能没有事件')

    # ① 每个源都保留原 ID；相同内容只转一次，不把轨名当文件身份。
    t0 = time.perf_counter()
    src_of = {s["id"]: root / s["path"] for s in tl["sources"]}
    selected_ids = list(dict.fromkeys(e['src'] for tr in sel for e in tr['events']))
    version = subprocess.run(['ffmpeg', '-version'], check=True, capture_output=True,
                             text=True, encoding='utf-8', errors='replace').stdout
    profile = {'version': 1, 'encoder': 'libmp3lame', 'bitrate': '320k',
               'stream': '0:a:0', 'rate': 48000, 'channels': 2, 'ffmpeg': version}
    grouped = {}
    for sid in selected_ids:
        grouped.setdefault(file_hash(src_of[sid]), []).append(sid)

    def _transcode(ids):
        dst = export_mp3(src_of[ids[0]], mp3_dir, profile, a.force)
        tprint(f"[mp3 ] {', '.join(ids)} → {dst.name}")
        return ids, dst

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as ex:
        mp3_of = {sid: dst for ids, dst in ex.map(_transcode, grouped.values()) for sid in ids}
    print(f"[timing] step1 transcode: {time.perf_counter() - t0:.1f}s")

    # ② 检测原素材第一音轨，避免 MP3 编码补齐影响源时间。
    t1 = time.perf_counter()
    fps = tl["project"]["fps"]
    sources = [{'id': sid, 'path': media_path(mp3_of[sid], audit_dir)} for sid in selected_ids]
    tracks, report, fallbacks = [], [], []

    def _analyze(sid):
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "csv=p=0", str(src_of[sid])],
                           capture_output=True, text=True, check=True, encoding="utf-8", errors="replace")
        dur = float(r.stdout.strip())
        if not math.isfinite(dur) or dur <= 0:
            raise ValueError(f'无法测量音频时长: {sid}')
        return sid, content_spans(src_of[sid], dur, fps, a.silence_threshold, a.min_silence, a.pad)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as ex:
        spans_of = dict(ex.map(_analyze, selected_ids)) if not a.whole else {}
    for tr in sel:
        evs = []
        for index, event in enumerate(tr['events']):
            parts, reason = ([dict(event)], None) if a.whole else split_event(event, spans_of[event['src']])
            evs.extend(parts)
            if reason:
                fallbacks.append({'track': tr['name'], 'event': index, 'reason': reason})
                print(f"[whole] {tr['name']} event {index}: {reason}")
        tracks.append({'name': tr['name'], 'events': evs})
        report.append((tr['name'], sorted({e.get('volume', 1.0) for e in evs}), len(evs),
                       sum(e['out']-e['in'] for e in evs)/fps))
    print(f"[timing] step2 analyze: {time.perf_counter() - t1:.1f}s")

    project = dict(tl['project'])
    removed_dependencies = project.pop('cache_dependencies', [])
    audit = {
        "version": 2,
        "_meta": {
            "note": "逐源 MP3 试听件、事件级音量/偏移/声像/循环/淡变保留。"
                    "这是 duck 和总混响度处理前的审计，不等同于成片采样。"
                    "默认静音切分为阈值近似；loop/fade 和未检出声音的事件保留整段。",
            "generated_by": "audit_bundle.py",
            "silence": f"threshold={a.silence_threshold} min_silence={a.min_silence}s pad={a.pad}s"
                       if not a.whole else "whole（未切分）",
            "from_timeline": str(tl_path),
            "whole_fallbacks": fallbacks,
            "excluded_visual_dependencies": removed_dependencies,
            "excluded_duck_tracks": [tr['name'] for tr in sel if tr.get('duck')],
        },
        "project": project,
        "sources": sources,
        "video_tracks": [],
        "subtitles": [],
        "audio_tracks": tracks,
    }
    at_path = audit_dir / "timeline.json"
    at_path.write_text(json.dumps(audit, ensure_ascii=False, indent=1), encoding="utf-8")
    n_ev = sum(len(t["events"]) for t in tracks)
    print(f"[json ] {at_path}（{len(tracks)} 轨 / {n_ev} 事件）")
    for sid, vol, n, voiced in report:
        tail = f" 保留 {voiced:.1f}s" if voiced is not None else ""
        vs = "/".join(str(v) for v in vol) if isinstance(vol, list) else str(vol)
        print(f"       {sid:16s} vol={vs:<9} {n} 段{tail}")

    # ③ validate
    t2 = time.perf_counter()
    # vtx.py 解析顺序（2026-09-11；v2 P2a 定位链重算 §6.2）：VTX_VTX_PY 环境变量 >
    # 本仓权威副本 > 技能安装路径 fallback。此前无条件用技能路径，本仓副本与其
    # 漂移时会审到旧实现。迁移 apps/blender-bridge/ 后上级不再是仓根——改为自本
    # 文件向上找 repo 根（含 vt.py 标记的祖先目录）下的 vtx/vtx.py。
    vtx_env = os.environ.get("VTX_VTX_PY")
    vtx = None
    if vtx_env:
        vtx = Path(vtx_env)
    else:
        for anc in Path(__file__).resolve().parents:
            if (anc / "vt.py").is_file() and (anc / "vtx" / "vtx.py").is_file():
                vtx = anc / "vtx" / "vtx.py"
                break
    if vtx is None or not vtx.exists():
        vtx = Path.home() / ".agents/skills/video/vtx/vtx.py"
    # P2 审查 P2-4：本机 AppImage 有 argv[0] 劫持，sys.executable 指向错误解释器
    py3 = shutil.which("python3") or "python3"
    run([py3, str(vtx), "-t", str(at_path), "validate"], tail=2)
    print(f"[timing] step3 validate: {time.perf_counter() - t2:.1f}s")
    if a.skip_blend:
        print("[done] --skip-blend：审计 blend 未生成")
        return

    # ④⑤ json2blend + verify_blend（cwd=bridge：两脚本按自身位置工作）
    #     合并为单次 blender 会话（bundle_session.py 内 runpy 依次以 __main__ 执行
    #     两脚本，零逻辑复制；verify 自行 open_mainfile 打开产物，合并安全）
    t3 = time.perf_counter()
    bridge = Path(__file__).resolve().parent
    blend = audit_dir / "audit.blend"
    run(["blender", "--background", "--factory-startup",
         "--python", str(bridge / "bundle_session.py"), "--",
         str(at_path), str(blend),
         str(bridge / "json2blend.py"), str(bridge / "verify_blend.py")],
        cwd=bridge, tail=6)
    print(f"[timing] step4+5 blend+verify: {time.perf_counter() - t3:.1f}s")
    print(f"[done] 打开：blender {blend}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"FATAL: {getattr(exc, 'stderr', None) or exc}", file=sys.stderr)
        sys.exit(1)
