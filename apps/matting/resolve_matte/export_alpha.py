#!/usr/bin/env python3
"""逐轨导出 grid9 为 ProRes4444（带阿尔法）。

用户确认的方案：在 grid9 时间线上，每次禁用其余 8 轨只留 1 轨，
并按该轨片段长度设置渲染 in/out（MarkIn=108000, MarkOut=108000+帧数-1）。
专用于 grid9 的恰好 9 条视频轨；缺失目标、身份不明或额外视频轨均拒绝导出。
注意：达芬奇只能渲染到家目录下的文件夹。
用法: env python3 export_alpha.py [工程名] [输出目录]
渲染、清理或产物校验失败均非零退出；目标文件要求本进程为唯一写入者。
未知 Resolve 完成状态报 UNKNOWN，不将旧产物或未经验证的状态视为成功。
"""
import os
import re
import subprocess
import sys
import time

N_TRACKS = 9
# 有实际 API 回读依据的有限成功值；其他版本/语言明确报 UNKNOWN。
# https://github.com/samuelgursky/davinci-resolve-mcp/issues/191
COMPLETED_JOB_STATUSES = {"Complete", "Concluso"}


def file_signature(path):
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return None
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def checked_run(args, label):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{label} 失败 (exit {result.returncode}): {result.stderr.strip()}")
    return result


def alpha_stats(path, tail=False):
    args = ["ffmpeg", "-nostdin", "-hide_banner", "-xerror"]
    if tail:
        args += ["-sseof", "-2"]
    args += ["-i", path, "-vf", "extractplanes=a,signalstats,metadata=mode=print",
             "-f", "null", "-"]
    stats = checked_run(args, "尾部 alpha 解码" if tail else "alpha 解码").stderr
    # signalstats 写帧 metadata；metadata=print 才会输出可读取的数值。
    mins = [int(value) for value in re.findall(r"lavfi\.signalstats\.YMIN=(\d+)\b", stats)]
    maxs = [int(value) for value in re.findall(r"lavfi\.signalstats\.YMAX=(\d+)\b", stats)]
    if not mins or not maxs:
        raise RuntimeError(f"{'尾部 ' if tail else ''}alpha 元数据缺失: {stats.strip()}")
    return min(mins), max(maxs)


def validate_outputs(results):
    failures = []
    print("\n=== 校验 ===")
    for k, path, frames in results:
        if not os.path.isfile(path):
            failures.append(f"V{k}: 缺失! {path}")
            print(failures[-1])
            continue
        try:
            probe = checked_run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=pix_fmt,nb_frames", "-of", "csv=p=0", path],
                "ffprobe").stdout.strip()
            ymin, ymax = alpha_stats(path)
            _, tmax = alpha_stats(path, tail=True)
            nb = probe.split(",")[-1]
            frame_ok = nb == str(frames)
            tail_ok = tmax > 0
            print(f"V{k}: {probe}  alpha[{ymin},{ymax}]  尾部alphaMax={tmax}"
                  f"  帧数{'✓' if frame_ok else '✗ 预期'+str(frames)}"
                  f"  尾部{'✓' if tail_ok else '✗ 全透明!'}")
            if not frame_ok:
                failures.append(f"V{k}: 帧数 {nb!r}，预期 {frames}")
            if not tail_ok:
                failures.append(f"V{k}: 尾部 alpha 全透明 (alphaMax={tmax})")
        except (OSError, RuntimeError) as exc:
            failures.append(f"V{k}: {type(exc).__name__}: {exc}")
    if failures:
        raise RuntimeError("导出校验失败:\n" + "\n".join(failures))


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from resolve_ctl import get_resolve, ensure_project

    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "out", "alpha_v8")
    r = get_resolve()
    p = ensure_project(r.GetProjectManager(), sys.argv[1] if len(sys.argv) > 1 else "matte_auto_v8")
    tl = p.GetCurrentTimeline()
    needs_switch = tl is None or tl.GetName() != "grid9"
    if needs_switch:
        tl = None
        for i in range(1, (p.GetTimelineCount() or 0) + 1):
            candidate = p.GetTimelineByIndex(i)
            if candidate is not None and candidate.GetName() == "grid9":
                tl = candidate
                break
        if tl is None:
            raise RuntimeError("缺失 grid9 时间线，拒绝导出其他时间线")

    # v21 Timeline.GetUniqueId 返回唯一字符串 ID；回读可能是另一 Python 代理。
    get_id = getattr(tl, "GetUniqueId", None)
    target_id = get_id() if callable(get_id) else None
    if not isinstance(target_id, str) or not target_id:
        raise RuntimeError(f"grid9 时间线身份 UNKNOWN: GetUniqueId={target_id!r}")
    if needs_switch:
        switched = p.SetCurrentTimeline(tl)
        if switched is not True:
            raise RuntimeError(f"grid9 SetCurrentTimeline 失败/UNKNOWN: {switched!r} (需要 True)")
    current = p.GetCurrentTimeline()
    current_name = current.GetName() if current is not None else None
    get_id = getattr(current, "GetUniqueId", None)
    current_id = get_id() if callable(get_id) else None
    if current_name != "grid9" or current_id != target_id:
        raise RuntimeError(f"grid9 时间线身份回读不符: 预期 {target_id!r}，"
                           f"实际 name={current_name!r}, id={current_id!r}")
    tl = current
    track_count = tl.GetTrackCount("video")
    if not isinstance(track_count, int) or track_count != N_TRACKS:
        raise RuntimeError(f"grid9 专用导出要求恰好 {N_TRACKS} 条视频轨，实际 {track_count!r}")
    print("时间线:", tl.GetName())

    clips = {}
    for k in range(1, N_TRACKS + 1):
        items = tl.GetItemsInTrack("video", k)
        it = items[1] if items else None
        clips[k] = (it.GetName(), it.GetEnd() - it.GetStart())
        print(f"V{k}: {clips[k][0]} {clips[k][1]} 帧")

    if not p.SetCurrentRenderFormatAndCodec("mov", "ProRes4444"):
        raise RuntimeError("ProRes4444 渲染格式设置失败")
    os.makedirs(out_dir, exist_ok=True)
    out_dir = os.path.abspath(out_dir)

    results = []
    try:
        for k in range(1, N_TRACKS + 1):
            frames = clips[k][1]
            # v21 OpenPage 应返回 Bool；旧版 None 等未知返回不视为成功。
            # 轨道启用切换必须在剪辑页进行（交付页调用不生效）
            opened = r.OpenPage("edit")
            if opened is not True:
                raise RuntimeError(f"V{k} OpenPage('edit') 失败/UNKNOWN: {opened!r} (需要 True)")
            time.sleep(1)
            for i in range(1, N_TRACKS + 1):
                if not tl.SetTrackEnable("video", i, i == k):
                    raise RuntimeError(f"V{k}: V{i} 轨道切换失败")
            opened = r.OpenPage("deliver")
            if opened is not True:
                raise RuntimeError(f"V{k} OpenPage('deliver') 失败/UNKNOWN: {opened!r} (需要 True)")
            time.sleep(1)
            ok = p.SetRenderSettings({
                "SelectAllFrames": False,
                "MarkIn": 108000,              # 时间线起始时码 01:00:00:00 的帧基准
                "MarkOut": 108000 + frames - 1,
                "TargetDir": out_dir,
                "CustomName": f"matte_V{k}",
                "UniqueFilenameStyle": 0,
                "ExportVideo": True,
                "ExportAudio": False,
                "ExportAlpha": True,
                "AlphaMode": 1,
            })
            if not ok:
                raise RuntimeError(f"V{k} 渲染设置失败")
            path = os.path.join(out_dir, f"matte_V{k}.mov")
            # 单写入者前提：不删除旧文件，只拒绝本次 job 未更新的旧产物。
            previous_output = file_signature(path)
            job = p.AddRenderJob()
            if not job:
                raise RuntimeError(f"V{k} 创建渲染任务失败")
            try:
                if not p.StartRendering(job):
                    raise RuntimeError(f"V{k} 启动渲染失败 (job={job})")
                t0 = time.time()
                while p.IsRenderingInProgress():
                    time.sleep(2)
                st = p.GetRenderJobStatus(job)
                if not isinstance(st, dict):
                    raise RuntimeError(f"V{k} UNKNOWN 渲染状态 (job={job}): {st!r}")
                print(f"V{k}: {st.get('JobStatus')} {st.get('CompletionPercentage')}% 用时 {time.time()-t0:.0f}s (预期 {frames} 帧)", flush=True)
                if str(st.get("CompletionPercentage")) not in ("100", "100.0") or st.get("Error"):
                    raise RuntimeError(f"V{k} 渲染任务未成功 (job={job}): {st!r}")
                if st.get("JobStatus") not in COMPLETED_JOB_STATUSES:
                    raise RuntimeError(f"V{k} UNKNOWN 渲染状态 (job={job}): {st!r}")
                if previous_output is not None and file_signature(path) == previous_output:
                    raise RuntimeError(f"V{k} 本次渲染未更新旧输出 (job={job}): {path}")
            finally:
                if not p.DeleteRenderJob(job):
                    raise RuntimeError(f"V{k} 删除渲染任务失败 (job={job})")
            results.append((k, path, frames))
    finally:
        # 即使渲染失败也尝试恢复每一轨；清理失败不得掩盖原异常或返回成功。
        cleanup_errors = []
        try:
            opened = r.OpenPage("edit")
            if opened is not True:
                raise RuntimeError(f"OpenPage('edit') 恢复失败/UNKNOWN: {opened!r} (需要 True)")
            time.sleep(1)
        except Exception as exc:
            cleanup_errors.append(f"{type(exc).__name__}: {exc}")
        for i in range(1, N_TRACKS + 1):
            try:
                if not tl.SetTrackEnable("video", i, True):
                    raise RuntimeError("SetTrackEnable 返回失败")
            except Exception as exc:
                cleanup_errors.append(f"V{i}: {type(exc).__name__}: {exc}")
        if cleanup_errors:
            raise RuntimeError("恢复轨道失败: " + "; ".join(cleanup_errors))

    validate_outputs(results)


if __name__ == "__main__":
    main()
