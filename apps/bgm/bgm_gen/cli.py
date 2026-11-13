"""bgm CLI（PLAN.md BG-D1）。

子命令树：doctor / engines / render-engine / render / stem / master / qc / catalog /
show（R1-W1 起）/ edit（R3-W1：insert/set/move/delete + split/merge/undo）。
入口：python3 -m bgm_gen 或 ~/.local/bin/bgm。
R3 充实 render/stem/master/qc（PLAN.md §5 R3）；doctor/engines/render-engine
逻辑自 R2 起冻结不变。catalog 为 P1 新增（音色目录，bgm_gen/catalog.py）。
"""

import argparse
import json
import sys


def main(argv=None):
    p = argparse.ArgumentParser(prog="bgm", description="bgm_gen 音乐生成终端工具（宿主=CLI，工程=JSON）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="环境体检（探测/校验 runtime.json，BG-D3）")
    sub.add_parser("engines", help="列出已注册引擎（自动发现 engines/ 下模块）")

    pe = sub.add_parser("render-engine", help="单引擎渲染")
    pe.add_argument("--engine", required=True)
    pe.add_argument("--spec", required=True, help="envelope JSON 文件路径（CLI 校验 envelope，引擎校验 payload）")
    pe.add_argument("-o", "--out", default=None, help="覆盖 envelope 输出路径")

    pr = sub.add_parser("render", help="工程渲染：project.json → stems → 混音总线 → 母带 → mix.flac+qc.json")
    pr.add_argument("project", help="project.json 路径（schema v1/v2，见 schema/project_v1.json、project_v2.json）")
    pr.add_argument("-o", "--out", default=None, help="输出目录（默认 <调用者 cwd>/out/<工程名>/，BG-D9）")
    pr.add_argument("-q", "--quiet", action="store_true", help="抑制进度行")
    pr.add_argument("--incremental", action="store_true",
                    help="增量渲染：stem 内容 hash 命中且产物/边车在则跳过重渲"
                         "（默认关闭；BGM_NO_INCREMENTAL=1 环境硬闸优先）")

    ps = sub.add_parser("stem", help="demucs 分轨（输入音频 → htdemucs stems）")
    ps.add_argument("input", help="输入音频路径")
    ps.add_argument("-o", "--out-dir", default=None, help="stems 根目录（默认 <调用者 cwd>/out/smoke/demucs/）")
    ps.add_argument("--model", default="htdemucs")
    ps.add_argument("--device", choices=["auto", "gpu", "cpu"], default="auto",
                    help="auto=显存闸超时降级 cpu（BG-D8）；gpu=超时抛错；cpu=直跑")
    ps.add_argument("--two-stems", default=None, metavar="SRC",
                    help='两分轨模式（如 "vocals"，取 vocals/no_vocals）')
    ps.add_argument("--sr", type=int, default=None, help="声明输出 sr（BG-D5，默认 44100 原样）")

    pm = sub.add_parser("master", help="母带：loudnorm 两遍法 / matchering 参考匹配")
    pm.add_argument("input", help="输入音频路径")
    pm.add_argument("--lufs", type=float, default=-14, help="目标综合响度（默认 -14，B 站 EBU R128）")
    pm.add_argument("--tp", type=float, default=-1, help="真峰值上限 dBTP（默认 -1）")
    pm.add_argument("--mode", choices=["loudnorm", "matchering"], default="loudnorm")
    pm.add_argument("--ref", default=None, help="matchering 模式参考音频")
    pm.add_argument("-o", "--out", default=None, help="输出 wav 路径")

    pq = sub.add_parser("qc", help="QC 测量/对照报告（无声窗/LUFS/TP/高频占比/BPM/调性）")
    pq.add_argument("input", help="输入音频路径")
    pq.add_argument("--project", default=None, help="project.json（对照 master.qc 目标判 pass/fail；缺省全 info）")
    pq.add_argument("-o", "--out", default=None, help="报告 JSON 落盘路径（缺省仅打印）")

    pcap = sub.add_parser("catalog", help="音色目录：扫描 synth/sf2/sfz/sample 音色源（catalog.py）")
    pcap.add_argument("--json", action="store_true", help="输出 JSON（timbres.json 结构）")
    pcap.add_argument("--write", action="store_true",
                      help="写入调用者 cwd/catalog/ 或 -o 指定目录")
    pcap.add_argument("-o", "--out-dir", default=None, help="--write 的输出目录")

    # v3 R1-W1：show 父解析器（检视层，PLAN_v3 §3.5）；engines 叶由 R1 交付，
    # structure/notes/cc/events/diff 叶由 R3-W2 接线（视图实现=core/show.py）
    psh = sub.add_parser("show", help="检视层：结构/音符/cc/事件流/描述符注册表/diff（v3）")
    psh_sub = psh.add_subparsers(dest="show_cmd", required=True)
    pshe = psh_sub.add_parser("engines", help="引擎描述符注册表（7 引擎能力面，PLAN_v3 §3.1）")
    pshe.add_argument("--json", action="store_true",
                      help="全量 JSON 导出（描述符原字段 + determinism/summary 合并）")

    pss = psh_sub.add_parser("structure", help="段落/轨/clip/时长总览（core/show.py）")
    pss.add_argument("project", help="project.json 路径")
    pss.add_argument("--json", action="store_true", help="JSON 输出（缺省 text）")

    psn = psh_sub.add_parser("notes", help="notes 规范排序视图（(beat,pitch) 序；index=编辑寻址）")
    psn.add_argument("project", help="project.json 路径")
    psn.add_argument("--track", default=None, help="按轨名筛选（缺省全部轨）")
    psn.add_argument("--window", default=None, metavar="beat:a-b|sec:a-b",
                     help="半开窗口 [a,b)；sec 按 meta.bpm 换算")
    psn.add_argument("--json", action="store_true", help="JSON 输出（缺省 text）")

    psc = psh_sub.add_parser("cc", help="cc 车道投影（显示编码后值 round-half-up；core/show.py）")
    psc.add_argument("project", help="project.json 路径")
    psc.add_argument("--track", default=None, help="按轨名筛选（缺省全部轨）")
    psc.add_argument("--ctrl", type=int, default=None,
                     help="cc 控制器号 0..127（缺省该轨全部车道）")
    psc.add_argument("--json", action="store_true", help="JSON 输出（缺省 text）")

    psev = psh_sub.add_parser("events", help="事件流规范排序（event_sort_key 全序）+ notes 投影")
    psev.add_argument("project", help="project.json 路径")
    psev.add_argument("--track", default=None, help="按轨名筛选（缺省全部轨）")
    psev.add_argument("--window", default=None, metavar="beat:a-b|sec:a-b",
                     help="半开窗口 [a,b)；sec 按 meta.bpm 换算")
    psev.add_argument("--json", action="store_true", help="JSON 输出（缺省 text）")

    psd = psh_sub.add_parser("diff", help="对比 .bgm_undo 快照栈/当前文件（读 manifest.json）")
    psd.add_argument("project", help="project.json 路径（.bgm_undo 位于其旁）")
    psd.add_argument("--undo", type=int, default=None, metavar="N",
                     help="指定快照 seq（缺省栈顶）")
    psd.add_argument("--json", action="store_true", help="JSON 输出（缺省 text）")

    # v3 R3-W1：edit 父解析器（操作层，PLAN_v3 §3.4；本 worker 交付区）
    pe = sub.add_parser("edit", help="操作层编辑：insert/set/move/delete + split/merge/undo（v3）")
    pes = pe.add_subparsers(dest="edit_cmd", required=True)
    _target_help = ("target 路径：tracks/<name>[/notes[/<i>]|/events[/<i>]|/clip|"
                    "/params[/<key>]]、automation[/<i>]、sections[/<i>]、meta[/<field>]、"
                    "master/…；索引=show 规范排序视图序号——连续编辑必须每次 edit 后"
                    "重新 show 取索引（BG-D11）")
    for _verb, _vhelp in (
            ("insert", "插入：带索引=在索引之前插入（splice）；无索引=append；--copy-of 折叠复制"),
            ("set", "设置：对象深度合并（未知键拒绝）；--window 选择谓词；修饰器批量变换"),
            ("move", "移动：索引/窗口源 → --to 目标（同载体，含跨轨）"),
            ("delete", "删除：索引/窗口/谓词")):
        pv = pes.add_parser(_verb, help=_vhelp)
        pv.add_argument("project", help="project.json 路径（schema v1/v2）")
        pv.add_argument("--target", required=True, help=_target_help)
        pv.add_argument("--window", default=None, metavar="beat:a-b|sec:a-b",
                        help="选择谓词（区间 [a,b)；sec 按 meta.bpm 换算落 beat）")
        pv.add_argument("--payload", default=None, metavar="JSON",
                        help="内容 JSON（对象 target=深度合并；insert=元素内容）")
        pv.add_argument("--copy-of", default=None, metavar="SRC",
                        help="折叠复制源 target（同轨/跨轨、notes/events 载体，跨形式先转换）")
        pv.add_argument("--transpose", default=None, metavar="半音",
                        help="pitch 平移（作用于 pitch 字段）")
        pv.add_argument("--quantize", default=None, metavar="拍格",
                        help="beat 吸附（round(beat/格)*格）")
        pv.add_argument("--scale", default=None, metavar="比",
                        help="beat 时间轴缩放（notes：beat+dur；events：t）")
        pv.add_argument("--json", action="store_true",
                        help="结构化 diff 输出：{changed:[{path, before, after}]}")
        if _verb == "move":
            pv.add_argument("--to", default=None, metavar="DST",
                            help="move 目标 target（同载体；无索引=append）")
    psp = pes.add_parser("split", help="内联 → $ref 双布局（薄包装 split_layout + save）")
    psp.add_argument("project", help="project.json 路径")
    psp.add_argument("--tracks-dir", default="tracks",
                     help="外部轨目录（相对工程目录，缺省 tracks）")
    psp.add_argument("--json", action="store_true")
    pmg = pes.add_parser("merge", help="$ref → 内联展开（薄包装 merge_layout + save）")
    pmg.add_argument("project", help="project.json 路径")
    pmg.add_argument("--json", action="store_true")
    pud = pes.add_parser("undo", help="撤销上一编辑（漂移检测；--force 覆盖；--list 列栈）")
    pud.add_argument("project", help="project.json 路径")
    pud.add_argument("--list", action="store_true", help="列出快照栈（manifest.json）")
    pud.add_argument("--force", action="store_true", help="跳过外部轨漂移检测")
    pud.add_argument("--json", action="store_true")

    a = p.parse_args(argv)

    if a.cmd == "show":
        if a.show_cmd == "engines":
            from .engines import ENGINES
            from .engines.base import DESCRIPTORS, DESCRIPTOR_ERRORS
            rows = []
            for eng in sorted(set(ENGINES) | set(DESCRIPTORS)):
                desc = DESCRIPTORS.get(eng)
                if desc is None:
                    entry = {"engine": eng,
                             "error": DESCRIPTOR_ERRORS.get(eng, "描述符缺失")}
                else:
                    entry = dict(desc)
                cls = ENGINES.get(eng)
                entry["determinism"] = cls.determinism if cls else None
                entry["summary"] = cls.summary if cls else ""
                rows.append(entry)
            if a.json:
                print(json.dumps({"count": len(rows), "engines": rows},
                                 ensure_ascii=False, indent=2))
                return 0
            # text 表格（只读呈现；jq 断言走 --json）
            print(f"{'engine':<7} {'version':<10} {'tier':<5} {'params':<7} "
                  f"{'events':<34} {'determinism':<15} summary")
            for entry in rows:
                cov = entry.get("coverage") or {}
                evs = ",".join(sorted(e.get("type")
                                      for e in entry.get("events_supported") or [])) or "—"
                params = len(entry.get("parameters") or []) if "error" not in entry else "—"
                version = entry.get("version", "—")
                tier = cov.get("tier", "—")
                print(f"{entry['engine']:<7} {version:<10} {tier:<5} {params:<7} "
                      f"{evs:<34} {(entry.get('determinism') or '—'):<15} "
                      f"{entry.get('summary', '')}")
            return 0
        # v3 R3-W2：structure/notes/cc/events/diff 叶（core/show.py 视图族；
        # 错误口径 = stderr + return 1 + 无 Traceback）
        from .core.project import ProjectError, load_project
        from .core.show import ShowError, show_cc, show_diff, show_events, \
            show_notes, show_structure
        try:
            if a.show_cmd == "diff":
                view = show_diff(a.project, undo=a.undo, as_json=a.json)
            else:
                proj = load_project(a.project)
                if a.show_cmd == "structure":
                    view = show_structure(proj, as_json=a.json)
                elif a.show_cmd == "notes":
                    view = show_notes(proj, track=a.track, window=a.window,
                                      as_json=a.json)
                elif a.show_cmd == "cc":
                    view = show_cc(proj, track=a.track, ctrl=a.ctrl,
                                   as_json=a.json)
                else:                                        # events
                    view = show_events(proj, track=a.track, window=a.window,
                                       as_json=a.json)
        except (ProjectError, ShowError, OSError) as e:
            print(f"show {a.show_cmd} 失败：{e}", file=sys.stderr)
            return 1
        if a.json:
            print(json.dumps(view, ensure_ascii=False, indent=2))
        else:
            print(view)
        return 0

    if a.cmd == "edit":
        # R3-W1 交付区：错误出口沿用 stderr + return 1 + 无 Traceback 口径；
        # --payload/--window 解析在 handler/edit 核心内手写报错（argparse type
        # 层报 usage 不合格，PLAN_v3 §3.4）。
        from .core.edit import (EditError, edit_merge, edit_project, edit_split,
                                format_list, format_result, list_undo, undo_edit)
        from .core.project import ProjectError
        try:
            if a.edit_cmd == "split":
                res = edit_split(a.project, tracks_dir=a.tracks_dir)
            elif a.edit_cmd == "merge":
                res = edit_merge(a.project)
            elif a.edit_cmd == "undo":
                if a.list:
                    res = list_undo(a.project)
                else:
                    res = undo_edit(a.project, force=a.force)
            else:
                payload = a.payload
                if payload is not None:
                    def _reject_const(c):
                        # R3 审查 M-2：拒绝裸 NaN/Infinity/-Infinity 字面量入工程
                        raise EditError(f"--payload 含非法常量 {c}（须为有限数值）")
                    try:
                        payload = json.loads(payload, parse_constant=_reject_const)
                    except json.JSONDecodeError as e:
                        raise EditError(f"--payload JSON 解析失败：{e}") from e
                res = edit_project(a.project, a.edit_cmd, target=a.target,
                                   payload=payload, window=a.window,
                                   copy_of=a.copy_of, transpose=a.transpose,
                                   quantize=a.quantize, scale=a.scale,
                                   dest=getattr(a, "to", None))
        except (EditError, ProjectError, OSError) as e:
            # OSError 入捕（R3 审查 m-2）：只读目录等环境性写失败不得裸 Traceback
            print(f"edit {a.edit_cmd} 失败：{e}", file=sys.stderr)
            return 1
        if a.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif a.edit_cmd == "undo" and a.list:
            print(format_list(res))
        else:
            print(format_result(res))
        return 0

    if a.cmd == "engines":
        from . import engines
        for name, cls in sorted(engines.ENGINES.items()):
            print(f"{name}\t{cls.determinism}\t{cls.summary}")
        return 0

    if a.cmd == "render-engine":
        from . import engines
        try:                                    # m-2：spec 装载失败不裸 Traceback
            with open(a.spec) as f:
                spec = json.load(f)
            if not isinstance(spec, dict):
                raise ValueError("spec 顶层必须是 JSON object")
        except (OSError, ValueError) as e:
            print(f"render-engine 失败：无法装载 spec {a.spec}：{e}", file=sys.stderr)
            return 1
        spec["engine"] = a.engine
        if a.out:
            spec["out"] = a.out
        try:
            result = engines.run_engine(spec)
        except engines.EngineError as e:
            print(f"render-engine 失败：{e}", file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if a.cmd == "doctor":
        from .doctor import doctor_report
        report = doctor_report()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    if a.cmd == "render":
        from .core.project import ProjectError, render_project
        from .engines import EngineError
        from .core.resources import ResourceError   # v3.4 验收 M-2
        try:
            result = render_project(a.project, out_dir=a.out, quiet=a.quiet,
                                    incremental=a.incremental)
        except (ProjectError, EngineError, ResourceError) as e:
            print(f"render 失败：{e}", file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if a.cmd == "stem":
        from . import engines
        payload = {"input": a.input, "model": a.model, "device": a.device}
        if a.out_dir:
            payload["out_dir"] = a.out_dir
        if a.two_stems:
            payload["two_stems"] = a.two_stems
        try:
            result = engines.run_engine({"engine": "demucs", "spec_version": "1",
                                         **({"sr": a.sr} if a.sr else {}),
                                         "payload": payload})
        except engines.EngineError as e:
            print(f"stem 失败：{e}", file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if a.cmd == "master":
        from . import engines
        payload = {"input": a.input, "mode": a.mode,
                   "target_lufs": a.lufs, "true_peak": a.tp}
        if a.ref:
            payload["ref"] = a.ref
        if a.out:
            payload["out"] = a.out
        try:
            result = engines.run_engine({"engine": "master", "spec_version": "1",
                                         "payload": payload})
        except engines.EngineError as e:
            print(f"master 失败：{e}", file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if a.cmd == "qc":
        from .core.qc import qc_report, write_report
        from .core.project import load_project
        try:
            proj = load_project(a.project) if a.project else None
            report = qc_report(a.input, proj)
        except Exception as e:                               # noqa: BLE001 — CLI 兜底打印
            print(f"qc 失败：{e}", file=sys.stderr)
            return 1
        if a.out:
            write_report(report, a.out)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    if a.cmd == "catalog":
        from .catalog import build_catalog, render_markdown, write_catalog
        try:                                    # m-2：目录构建失败不裸 Traceback
            cat = build_catalog()
            if a.write:
                paths = write_catalog(cat, out_dir=a.out_dir)
                print(f"已写入：{paths['md']}\n        {paths['json']}")
                return 0
            if a.json:
                print(json.dumps(cat, ensure_ascii=False, indent=2))
            else:
                print(render_markdown(cat), end="")
        except Exception as e:                  # noqa: BLE001 — CLI 兜底打印（同 qc 纪律）
            print(f"catalog 失败：{e}", file=sys.stderr)
            return 1
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
