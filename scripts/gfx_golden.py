#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gfx_golden.py · gfx 帧 golden 像素基线构建/比对（vtx 导出通道改造方案 v2 阶段 0）

为「Chrome 截图导出 → canvas 批量导出」通道改造提供像素一致性硬门槛：
golden = 当前截图路径产物（cache/gfx/<12位键>/f{:04d}.png，1080p 带 alpha）的
逐帧像素 hash 快照。改造页必须逐帧零差异才算过（--full-pages 全帧 hash）；
未改造页抽检（默认 [0, dur//2, dur-1] 均匀补足 --sample 帧，含首中尾）。

hash 口径：PIL 解码 PNG → 统一 RGBA → 原始字节行主序 sha256。像素 hash 而非
文件字节 hash——PNG 编码器（压缩级别/过滤策略）可变，像素不可变。

golden JSON 结构（自包含、无歧义）：
  meta:   构建时间/chrome 版本(可空)/fps/视口/hash 算法/抽样标记/键算法说明/
          env_manifest(--env-manifest 嵌入的环境指纹，可空)
  pages:  { "<clip src 相对路径>":
              { "durs": { "<dur>": { "dur":…, "key":…, "mode":"sample|full",
                                     "frames": { "<帧号%04d>": <sha256hex> } } } } }
  同一 src 多个 clip（不同 dur）→ 不同缓存键（gfx_key 含 dur），在 durs 下并列。

用法（项目根；本机跑 python 必须加 env 前缀，见全局环境注意事项）：
  env python3 scripts/gfx_golden.py build -t timeline.json --golden gfx_golden.json \
      [--full-pages c5a_main_path,c5b_hotkey] [--sample 5] \
      [--env-manifest env_manifest.json]
  env python3 scripts/gfx_golden.py compare -t timeline.json --golden gfx_golden.json \
      [--report report.json] [--pages c5a_main_path] \
      [--env-manifest env_manifest.json]

跨后端环境前置门禁（issue #102）：build --env-manifest 把渲染运行产出的
env_manifest.json（vtx/env_manifest.py，不可伪造的环境指纹）嵌入 golden.meta；
compare --env-manifest 提供当前后端实测 manifest 时，先经
vtx.env_manifest.verify_manifest_compatibility 比对 Chromium 主版本/生效后端
（GPU 静默回退检出）/色彩空间/视口/字体 SHA256——任一不兼容即拒绝像素比对
（exit 1，不比错环境下的像素）。golden 含 manifest 而 compare 未提供 → 显式
警告门禁未激活（不阻断同机常规回环）；compare 提供而 golden 未嵌入 → 显式报错。

实现要点：
  - 缓存键/帧目录定位直接复用 vtx/hygiene.gfx_key（v2 §1.4 包导入，注入 repo 根）——
    golden 与渲染器永远同一套键；键逻辑若变，compare 以「目录缺失」显式失败并提示
    重建 golden，绝不静默错比。
  - 帧缺失/目录缺失在 build 即报错退出非零（golden 必须完整，宁缺毋滥）。
  - 并行：ThreadPoolExecutor（PIL C 解码释放 GIL）；GFX_GOLDEN_WORKERS 可调，
    默认 min(8, cpu)。目标：3530 帧 1080p 解码 hash ≈ 2-3 分钟内。
  - 纯标准库 + PIL；不改 vtx/（只读复用）。
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))   # v2 §1.4 包化：注入 repo 根 + 包导入（不再注入 repo/vtx）
from vtx.infra import Ctx   # noqa: E402
from vtx import hygiene     # noqa: E402
from vtx import env_manifest  # noqa: E402  （issue #102 跨后端环境前置门禁）

HASH_DESC = "sha256(PIL 解码 → RGBA 原始字节, 行主序) 每帧 64 hex"


def make_ctx(tl_path: Path) -> Ctx:
    """与 vtx 主入口同一套 Ctx（fps 契约/声明依赖快速失败全继承）。"""
    return Ctx(tl_path.resolve())


def gfx_clips(c: Ctx):
    """timeline 全部 gfx clip 的 (src, dur) 去重序列（保持出现序）。

    同页同 dur 的重复 clip 指向同一缓存目录（gfx_key 只含 src/dur/fps/视口/内容），
    golden 只记一份；同页不同 dur → 不同键，golden 中并列。
    """
    seen, out = set(), []
    for tr in c.tl.get("video_tracks", []):
        if tr.get("kind") != "gfx":
            continue
        for cl in tr.get("clips", []):
            k = (cl["src"], int(cl["dur"]))
            if k not in seen:
                seen.add(k)
                out.append(k)
    return out


def _match(name: str, src: str) -> bool:
    """名单匹配：src 相对路径全等 / src 文件名 stem 全等 / 子串包含，任一命中。"""
    return name == src or name == Path(src).stem or name in src


def _split_names(s: str):
    return [x.strip() for x in s.split(",") if x.strip()]


def sample_frames(dur: int, sample: int):
    """抽样帧号：[0, dur//2, dur-1] + 均匀补足到 ≥sample（含首中尾），升序去重。"""
    idx = {0, dur // 2, dur - 1}
    if sample > len(idx) and dur > 1:
        idx.update(round(k * (dur - 1) / (sample - 1)) for k in range(sample))
    return sorted(i for i in idx if 0 <= i < dur)


def workers_n() -> int:
    v = os.environ.get("GFX_GOLDEN_WORKERS", "")
    if v.isdigit() and int(v) > 0:
        return int(v)
    return max(1, min(8, os.cpu_count() or 8))


def hash_frame(path: Path) -> str:
    """单帧像素 hash：PNG → RGBA → 行主序原始字节 sha256。"""
    from PIL import Image
    with Image.open(path) as im:
        im.load()
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        return hashlib.sha256(im.tobytes()).hexdigest()


def hash_frames(tasks, what: str):
    """并行 hash。tasks=[(label, path)]，label 任意可哈希元组。

    返回 ({label: hex}, [(label, path, exc)])；调用方决定失败语义。进度每 200 帧
    打一行（3530 帧 × 1080p 不刷屏）。
    """
    out, fails = {}, []
    n = len(tasks)
    done = 0
    with ThreadPoolExecutor(workers_n()) as ex:
        futs = {ex.submit(hash_frame, p): (label, p) for label, p in tasks}
        for fu in as_completed(futs):
            label, p = futs[fu]
            done += 1
            try:
                out[label] = fu.result()
            except Exception as e:   # 解码失败/文件损坏：交调用方定语义
                fails.append((label, p, e))
            if done % 200 == 0 or done == n:
                print(f"  {what} {done}/{n}（{workers_n()} 线程）")
    return out, fails


def chrome_version():
    """构建环境佐证（可空）：best-effort 探测 chrome --version，失败不阻塞。"""
    cands = [os.environ.get("VTX_CHROME"), "google-chrome", "google-chrome-stable",
             "chromium", "chromium-browser"]
    for name in cands:
        if not name:
            continue
        exe = shutil.which(name)
        if not exe:
            continue
        try:
            r = subprocess.run([exe, "--version"], capture_output=True, text=True,
                               timeout=15)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
    return None


def load_env_manifest(path_str: str):
    """env manifest JSON 读取（issue #102 门禁入参）。空参 → (None, None)；
    文件缺失/损坏 JSON/内容为 null → (None, 错误句)——门禁入参宁拒收不静默。"""
    if not path_str:
        return None, None
    p = Path(path_str)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"env manifest 读取失败: {p}（{e}）"
    if not isinstance(data, dict):
        return None, f"env manifest 不是 JSON 对象: {p}（得到 {type(data).__name__}）"
    return data, None


def env_manifest_gate(meta: dict, path_str: str) -> dict:
    """跨后端环境前置门禁（issue #102）。返回 state 机：

      error → 入参不可用（manifest 读不到 / 提供了当前 manifest 但 golden 未
              嵌入基线 / golden 内嵌基线非 JSON 对象）→ compare exit 1，
              绝不比错环境下的像素；
      fail  → verify_manifest_compatibility 不兼容项非空 → exit 1 列全项；
      pass  → 环境一致（Chromium 主版本/生效后端/色彩空间/视口/字体 SHA256）；
      warn  → golden 嵌有 manifest 但本次未提供（同机常规回环不阻断，显式警告
              门禁未激活）；off → 双方均无 manifest（存量 golden，行为同旧版）。
    """
    cur, err = load_env_manifest(path_str)
    if err:
        return {"state": "error", "error": err, "items": []}
    if cur is None:
        if meta.get("env_manifest"):
            return {"state": "warn", "error": None, "items": []}
        return {"state": "off", "error": None, "items": []}
    ref = meta.get("env_manifest")
    if ref is not None and not isinstance(ref, dict):
        return {"state": "error",
                "error": f"golden.meta.env_manifest 不是 JSON 对象（得到 "
                         f"{type(ref).__name__}）——golden 损坏或被手改，环境门禁"
                         "基线不可信，拒绝像素比对",
                "items": []}
    if not ref:
        return {"state": "error",
                "error": "golden 未嵌入环境 manifest（build 时未提供 --env-manifest），"
                         "无法做跨后端环境门禁；请以 --env-manifest 重建 golden",
                "items": []}
    items = env_manifest.verify_manifest_compatibility(ref, cur)
    state = "fail" if items else "pass"
    return {"state": state, "error": None, "items": items}


# ---------------------------------------------------------------- build

def cmd_build(args) -> int:
    c = make_ctx(Path(args.timeline))
    clips = gfx_clips(c)
    if not clips:
        print("✗ timeline 中没有 gfx 轨 clip（video_tracks kind==gfx），无需 golden")
        return 1
    full_names = _split_names(args.full_pages)
    sample = max(1, args.sample)

    # ---- 环境指纹（issue #102）：嵌入 golden 供 compare 跨后端门禁（可空）
    env_m, env_err = load_env_manifest(args.env_manifest)
    if env_err:
        print(f"✗ {env_err}")
        return 1

    # ---- 名单防线（B2）：--full-pages 逐项必须至少命中一个 gfx 页，防拼错静默降级抽检
    unmatched = [n for n in full_names if not any(_match(n, src) for src, _ in clips)]
    if unmatched:
        print(f"✗ --full-pages 名单逐项零匹配: {unmatched}"
              f"（timeline 共 {len(clips)} 个 gfx 实例，检查拼写/是否已渲）")
        return 1

    # ---- 定位 + 完整性预检：任一目录/帧缺失 → 列全问题后退出非零（golden 必须完整）
    errors, plan = [], []
    for src, dur in clips:
        html = c.root / src
        if not html.exists():
            errors.append(f"html 缺失: {src}")
            continue
        key = hygiene.gfx_key(c, {"src": src, "dur": dur})
        d = c.cache / "gfx" / key
        if not d.is_dir():
            errors.append(f"帧目录缺失: {src} dur={dur} → cache/gfx/{key}（先完成 gfx 渲染）")
            continue
        mode = "full" if any(_match(n, src) for n in full_names) else "sample"
        frames = list(range(dur)) if mode == "full" else sample_frames(dur, sample)
        missing = [i for i in frames if not (d / f"f{i:04d}.png").is_file()]
        if missing:
            errors.append(f"帧缺失: {src} dur={dur} 缺 {len(missing)} 帧"
                          f"（如 f{missing[0]:04d}.png；mode={mode}）")
            continue
        plan.append((src, dur, key, mode, frames))
    if errors:
        print(f"✗ build 预检失败 {len(errors)} 项（golden 必须完整，不落半份）：")
        for e in errors:
            print(f"  - {e}")
        return 1

    # ---- 并行像素 hash
    tasks = []
    for src, dur, key, mode, frames in plan:
        d = c.cache / "gfx" / key
        for i in frames:
            tasks.append(((src, dur, i), d / f"f{i:04d}.png"))
    t0 = time.time()
    hashes, fails = hash_frames(tasks, "hash")
    if fails:
        print(f"✗ {len(fails)} 帧解码失败：")
        for label, p, e in fails[:10]:
            print(f"  - {label[0]} dur={label[1]} f{label[2]:04d}: {e}")
        return 1

    pages = {}
    for src, dur, key, mode, frames in plan:
        inst = {"dur": dur, "key": key, "mode": mode,
                "frames": {f"{i:04d}": hashes[(src, dur, i)] for i in frames}}
        pages.setdefault(src, {"durs": {}})["durs"][str(dur)] = inst
    golden = {
        "meta": {
            "tool": "gfx_golden",
            "built": time.strftime("%F %T"),
            "chrome": chrome_version(),
            "fps": c.fps,
            "viewport": f"{c.w}x{c.h}",
            "hash": HASH_DESC,
            "frame_naming": "f{:04d}.png（帧号从 0）",
            "key_algo": "cache/gfx/<键> 键 = vtx/hygiene.gfx_key = "
                        "sha1(html字节+显式依赖+fps+视口+时长)[:12]",
            "sample": sample,
            "full_pages": full_names,
            "pages": len(pages),
            "instances": len(plan),
            "frames_hashed": len(hashes),
            "elapsed_s": round(time.time() - t0, 1),
            "env_manifest": env_m,   # None=构建时未提供（compare 门禁据此显形区分）
        },
        "pages": pages,
    }
    out = Path(args.golden)
    # 落盘原子化（B4）：同目录 tmp + os.replace，杜绝半份 golden 被下次 compare 读到
    tmp = out.parent / (out.name + ".tmp")
    tmp.write_text(json.dumps(golden, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, out)
    n_full = sum(1 for *_, m, _f in plan if m == "full")
    print(f"✓ golden 已写入 {out}：{len(pages)} 页 / {len(plan)} 个实例"
          f"（full {n_full}），{len(hashes)} 帧，{time.time() - t0:.0f}s")
    return 0


# ---------------------------------------------------------------- compare

def cmd_compare(args) -> int:
    c = make_ctx(Path(args.timeline))
    gp = Path(args.golden)
    if not gp.exists():
        print(f"✗ golden 不存在: {gp}（先 build）")
        return 1
    g = json.loads(gp.read_text(encoding="utf-8"))
    meta = g.get("meta", {})
    # ---- 跨后端环境前置门禁（issue #102）：先证环境一致，再比像素。
    # 任一不兼容（字体 SHA256/GPU 静默回退/Chromium 主版本/色彩空间/视口）即拒比。
    gate = env_manifest_gate(meta, args.env_manifest)
    if gate["state"] == "error":
        print(f"✗ {gate['error']}")
        return 1
    if gate["state"] == "fail":
        for it in gate["items"]:
            print(f"  ✗ 环境门禁 {it}")
        print(f"✗ 跨后端环境前置门禁未通过：{len(gate['items'])} 项不兼容，"
              "拒绝像素比对（vtx/env_manifest，issue #102）")
        return 1
    if gate["state"] == "pass":
        print("✓ 环境前置门禁通过：Chromium 主版本/生效后端/色彩空间/视口/"
              "字体 SHA256 一致（vtx/env_manifest）")
    elif gate["state"] == "warn":
        print("⚠ golden 含环境 manifest 但未提供 --env-manifest：跨后端环境"
              "一致性门禁未激活（跨后端比对务必提供）")
    # 基线同一性：fps/视口不一致说明 golden 不是本项目的（缓存键也必然对不上）
    if (str(meta.get("fps")) != str(c.fps)
            or str(meta.get("viewport")) != f"{c.w}x{c.h}"):
        print(f"✗ golden 与当前 timeline 项目参数不符：golden fps={meta.get('fps')} "
              f"viewport={meta.get('viewport')}，当前 fps={c.fps} {c.w}x{c.h}")
        return 1

    names = _split_names(args.pages)
    # 名单防线（B2）：--pages 逐项必须至少命中一个 golden 页，防拼错时「其余名单」
    # 静默通过让人误以为全量比对
    unmatched = [n for n in names if not any(_match(n, src) for src in g.get("pages", {}))]
    if unmatched:
        print(f"✗ --pages 名单在 golden 中逐项零匹配: {unmatched}"
              f"（golden 共 {len(g.get('pages', {}))} 页）")
        return 1
    sel = {src: v for src, v in g.get("pages", {}).items()
           if not names or any(_match(n, src) for n in names)}
    if not sel:
        print(f"✗ --pages 名单在 golden 中无匹配页（golden 共 "
              f"{len(g.get('pages', {}))} 页）")
        return 1

    # ---- 交叉核对 timeline 页集（B3）：timeline 有而 golden 未记录的 gfx 页 → 警告列出
    unrecorded = []
    for src, dur in gfx_clips(c):
        pv = g.get("pages", {}).get(src)
        if pv is None or str(dur) not in pv.get("durs", {}):
            unrecorded.append({"src": src, "dur": dur})
    if unrecorded:
        print(f"  ⚠ timeline 中有 {len(unrecorded)} 个 gfx 实例未记录在 golden（不在本次比对"
              f"范围，需要覆盖请重建 golden）：")
        for u in unrecorded[:20]:
            print(f"    - {u['src']} dur={u['dur']}")
        if len(unrecorded) > 20:
            print(f"    ... 另有 {len(unrecorded) - 20} 个")

    # ---- 收集待比任务（抽检页只比 golden 已记录帧；full 页=全帧）
    tasks, dir_missing, unreadable = [], [], []
    for src, pv in sel.items():
        for durs, inst in pv.get("durs", {}).items():
            dur = int(inst["dur"])
            d = c.cache / "gfx" / hygiene.gfx_key(c, {"src": src, "dur": dur})
            if not d.is_dir():
                dir_missing.append({"src": src, "dur": dur,
                                    "key": hygiene.gfx_key(c, {"src": src, "dur": dur})})
                continue
            for fkey in inst.get("frames", {}):
                p = d / f"f{int(fkey):04d}.png"
                if not p.is_file():
                    unreadable.append({"src": src, "dur": dur, "frame": fkey,
                                       "why": "帧文件缺失"})
                else:
                    tasks.append(((src, dur, fkey), p))

    cur, fails = hash_frames(tasks, "比对")
    for label, _p, e in fails:
        unreadable.append({"src": label[0], "dur": label[1], "frame": label[2],
                           "why": f"解码失败: {e}"})

    # ---- 新鲜度防线（B1）：被比帧 mtime 全部 ≤ golden 构建时间 → 缓存自 golden 构建
    # 后未重渲，比对恒等、无意义。默认显式警告不阻断；--strict 判失败（验收口径）。
    # 容差 2s：meta.built 字符串秒级截断，帧可能写在同一秒内但 mtime 略晚。
    stale = False
    try:
        built = time.mktime(time.strptime(meta.get("built", ""), "%Y-%m-%d %H:%M:%S"))
        newest = max(p.stat().st_mtime for _l, p in tasks)
        stale = bool(tasks) and newest <= built + 2
    except (ValueError, TypeError, OSError):
        stale = False    # built 缺失/不可解析或 stat 失败：无法判定，不拦截

    # ---- 逐实例比对
    identical, differ = [], []
    for src, pv in sel.items():
        for _durs, inst in sorted(pv.get("durs", {}).items()):
            dur = int(inst["dur"])
            if any(m["src"] == src and m["dur"] == dur for m in dir_missing):
                continue
            diffs = [(fk, inst["frames"][fk], cur[(src, dur, fk)])
                     for fk in sorted(inst.get("frames", {}), key=int)
                     if (src, dur, fk) in cur and cur[(src, dur, fk)] != inst["frames"][fk]]
            if diffs:
                differ.append({"src": src, "dur": dur, "mode": inst.get("mode"),
                               "frame": diffs[0][0], "golden": diffs[0][1],
                               "current": diffs[0][2], "n_diff_frames": len(diffs)})
            else:
                identical.append({"src": src, "dur": dur, "mode": inst.get("mode"),
                                  "n_frames": len(inst.get("frames", {}))})

    ok = not (differ or dir_missing or unreadable)
    n_full = sum(1 for x in identical if x.get("mode") == "full")
    n_cmp = sum(x["n_frames"] for x in identical) + sum(x["n_diff_frames"] for x in differ)
    print(f"== gfx golden 比对（{len(sel)} 页，{n_cmp} 帧，golden 构建于 "
          f"{meta.get('built', '?')}）==")
    for x in identical:
        print(f"  ✓ 全零差异  {x['src']} dur={x['dur']}（{x['mode']}，{x['n_frames']} 帧）")
    for x in differ:
        print(f"  ✗ 像素差异  {x['src']} dur={x['dur']} 首个差异帧 f{x['frame']}.png "
              f"golden={x['golden'][:12]} current={x['current'][:12]}"
              f"（共 {x['n_diff_frames']} 帧不同）")
    for m in dir_missing:
        print(f"  ✗ 目录缺失  {m['src']} dur={m['dur']} → cache/gfx/{m['key']}"
              f"（内容/键已变？重建 golden 或先渲染）")
    for u in unreadable:
        print(f"  ✗ 帧缺失/损坏  {u['src']} dur={u['dur']} f{u['frame']}.png（{u['why']}）")
    if stale and args.strict:
        ok = False    # 新鲜度防线在 --strict 下判失败（验收口径）
    if ok and stale:
        print("⚠ 新鲜度防线：被比帧 mtime 全部 ≤ golden 构建时间——缓存未重渲，比对无意义"
              "（仅警告，验收请加 --strict）")
    if ok:
        print(f"✓ 通过：{len(identical)} 个实例全零差异")
    else:
        print(f"✗ 未通过：差异 {len(differ)} / 目录缺失 {len(dir_missing)} / "
              f"帧缺失损坏 {len(unreadable)}"
              + ("；缓存未重渲（--strict）" if stale else ""))
    if args.report:
        report = {"ok": ok, "stale": stale, "golden": str(gp), "timeline": str(Path(args.timeline)),
                  "compared_frames": n_cmp, "identical": identical, "differ": differ,
                  "dir_missing": dir_missing, "missing_or_unreadable": unreadable,
                  "unrecorded": unrecorded, "env_gate": gate}
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
        print(f"  报告已写入 {args.report}")
    return 0 if ok else 1


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="gfx 帧 golden 像素基线构建/比对（导出通道改造的像素一致性硬门槛）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="从现有 cache/gfx 帧产物构建 golden 像素基线")
    b.add_argument("-t", "--timeline", required=True, help="timeline.json 路径")
    b.add_argument("--golden", required=True, help="输出 golden JSON 路径")
    b.add_argument("--full-pages", default="",
                   help="全帧 hash 页名单（逗号分隔，按 src 相对路径/stem/子串匹配）；"
                        "名单外页抽检")
    b.add_argument("--sample", type=int, default=5,
                   help="抽检页每 clip 帧数下限（默认 5；[0, dur//2, dur-1]+均匀补足）")
    b.add_argument("--env-manifest", default="",
                   help="环境指纹 manifest JSON（渲染运行产出，vtx/env_manifest.py 采集）；"
                        "嵌入 golden.meta 供 compare 跨后端前置门禁（issue #102，可空）")

    p = sub.add_parser("compare", help="对当前 cache 重算 hash 并与 golden 逐帧比对")
    p.add_argument("-t", "--timeline", required=True, help="timeline.json 路径")
    p.add_argument("--golden", required=True, help="golden JSON 路径")
    p.add_argument("--report", default="", help="比对结果 JSON 输出路径（可选）")
    p.add_argument("--pages", default="",
                   help="只比名单内页（逗号分隔，匹配规则同 --full-pages；缺省=golden 全部）")
    p.add_argument("--env-manifest", default="",
                   help="当前后端实测 env manifest JSON：与 golden 内嵌基线先过环境一致性"
                        "门禁（任一不兼容拒绝像素比对，issue #102）")
    p.add_argument("--strict", action="store_true",
                   help="新鲜度防线判失败：被比帧 mtime 全部 ≤ golden 构建时间（缓存未重渲）"
                        "时 exit 1（默认仅显式警告）")

    args = ap.parse_args(argv)
    return cmd_build(args) if args.cmd == "build" else cmd_compare(args)


if __name__ == "__main__":
    sys.exit(main())
