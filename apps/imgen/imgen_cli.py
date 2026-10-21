#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""imgen_cli.py · 生图 API 生图 + ffmpeg cover-crop 后处理 总入口（imgen 模块）

子命令（方案 v2.1 §5.2）：
  gen "<prompt>" --out <项目目录> [--model nano|gpt-image] [--size ...] [--ratio ...]
      [--ref <图片/URL> ...] [--kind bg4k|bg1080p|vertical|raw] [--yes] [--key ...]
      [--task-id <id>]
      # --task-id：断线恢复——按 job id 只重轮询+下载+后处理，不产生新提交（URL 2h 失效内）
  batch <prompts.json> --out <项目目录> [--yes] [--key ...]
      # prompts.json = [{prompt, kind, model, size, ratio, ref}, ...]
  postprocess <in.png> --kind bg4k|bg1080p|vertical -o <out.png>
      # 离线后处理（免 API 重跑）
  key status
      # 只本地链探测，不触网不扣费

--kind 联动矩阵（§5.2 强制：kind 不但决定落位，还联动生成档位，保证
cover-crop 前源图 ≥ 目标、只降采样不放大）：
  bg4k     → nano / 4K / 16:9 → assets/bg/background_4k.png    （3840×2160）
  bg1080p  → nano / 2K / 16:9 → assets/bg/background_1080p.png （1920×1080）
  vertical → nano / 4K / 9:16 → assets/bg/background_vertical.png（1080×1920）
  raw      → 不联动 → assets/imgen/<UTC>-<n>.png（原图留档，仅尺寸校验不 crop）
用户显式 --model/--size 覆盖联动档位且低于安全档时，打印画质警告（含插值
倍数）后照做（用户主权）。

付费确认硬门：提交前按模型单价打印费用（0.1/0.3 元/张）+「审核拒绝
（status=3）不退款」提示；无 --yes 时交互 y/N；非 TTY 且无 --yes 直接中止
（0 次提交）；batch > 50 张二次确认（即使 --yes）。

落位纪律（§5.6）：下载临时件 → <项目>/cache/tmp/<runid>/（后处理完成即删）；
成品 → assets/bg/、assets/imgen/；job 记账 → <项目>/cache/reports/imgen_jobs_<runid>.json。
产物目录自建（new_project.sh 不建 assets/bg/、assets/imgen/，写前 mkdir -p）。
进度走 stderr、产物路径走 stdout（管道友好，随上游 cli.py）。

编排由独立参考实现改写内化（提交/轮询/下载线程池 10/10/5，
单张时退化为串行）；config 容错 cfg_get 见 §7.2；key 链见 secrets.py。
运行时零外部仓依赖。依赖：requests（仅 gen/batch 且 --ref 本地图时触网）、
ffmpeg/ffprobe（postprocess）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import models  # noqa: E402（无 requests 依赖；script 目录已在 sys.path[0]）

# 本目录 secrets.py key 链：app 根注入 sys.path[0] 后普通 import（v2 §1.4 禁
# spec_from_file_location——app 目录注入不涉内核双模块，允许）。stdlib 虽有同名
# secrets 模块，但 sys.path[0] 优先且本 CLI 的 import 链（argparse/uuid/...）不
# 触发 stdlib secrets 预缓存，解析确定落到本目录；仍留 fail-hard 哨兵防未来
# import 序变化导致的静默串味。
import secrets as keychain  # noqa: E402

if not str(getattr(keychain, "__file__", "")).startswith(str(Path(__file__).resolve().parent)):
    raise ImportError(
        "secrets 模块解析到非本 app 目录（stdlib 同名污染）：%r——请检查 import 序"
        % getattr(keychain, "__file__", None)
    )

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PAID_ABORT = 2  # 付费门中止（未提交未扣费），与一般错误区分便于脚本判断

BATCH_WARN_THRESHOLD = 50  # 单次 batch 超过此张数需二次确认


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# config 容错与加载共用 vtx.pluginlib.config；保持惰性导入，--help 不载入内核。
# ---------------------------------------------------------------------------

def _pluginlib_config():
    """vtx.pluginlib.config（v2 §5.3 统一加载）——repo 根追加进 sys.path 后导入
    （本模块平铺布局，脚本目录不含 repo 根；append 不抢占既有解析序）。"""
    root = str(Path(__file__).resolve().parent.parent.parent)  # v2 迁移后 imgen 在 apps/ 下，仓根 = 上两级
    if root not in sys.path:
        sys.path.append(root)
    from vtx.pluginlib import config as _cfg
    return _cfg


def resolve_api_base(cfg: dict) -> str:
    """优先级：env IMGEN_API_BASE（测试/mock 用）> config imgen.api_base > 默认。"""
    env = os.environ.get("IMGEN_API_BASE")
    if env:
        return env.rstrip("/")
    return str(_pluginlib_config().cfg_get(cfg, "imgen.api_base", models.BASE_URL)).rstrip("/")


# ---------------------------------------------------------------------------
# kind 联动与参数解析
# ---------------------------------------------------------------------------

def resolve_kind_params(
    kind: str,
    model_arg: Optional[str],
    size_arg: Optional[str],
    ratio_arg: Optional[str],
    model_default: str,
) -> dict:
    """按 §5.2 联动矩阵解析生效参数；显式覆盖低于安全档时产出画质警告（照做）。"""
    link = models.KIND_LINKAGE.get(kind)
    model = model_arg or (link["model"] if link else model_default)
    if link:
        size = size_arg or link["size"]
        ratio = ratio_arg or link["ratio"]
    else:  # raw 不联动
        size = size_arg or ("1K" if model == "nano" else "auto")
        ratio = ratio_arg or "auto"

    warnings: List[str] = []
    if model == "gpt-image" and ratio_arg and ratio_arg != "auto":
        warnings.append("⚠ gpt-image 不提交 aspectRatio（比例请用 --size 传，如 --size 16:9）；--ratio 已忽略")
        ratio = "auto"

    if kind in models.KIND_TARGETS:
        tw, th = models.KIND_TARGETS[kind]
        if model == "nano":
            est_w, est_h = models.estimate_nano_output(size, ratio)
            factor = models.upscale_factor((est_w, est_h), (tw, th))
            if factor > 1.0:
                advice = f"建议 {link['size']} 档" if link else "建议提高档位"
                warnings.append(
                    f"⚠ {kind} 目标 {tw}×{th}，nano {size}（{ratio}）实出约 {est_w}×{est_h}，"
                    f"cover-crop 需放大约 {factor:.2f}×（插值软化会固化进抠像合成链画质，{advice}）；"
                    "按显式指定照做"
                )
        elif model in models.GPT25_FAMILY:
            # 2.5 系：比例串按官方 1K 档锚点估算（基础版只有 1K）；要 2K/4K 请显式
            # 传像素串 aspectRatio（如 --ratio 3840x2160，sunburst/flare 支持）。
            est_w, est_h = models.estimate_gpt25_output(ratio)
            factor = models.upscale_factor((est_w, est_h), (tw, th))
            if factor > 1.0:
                advice = "（sunburst/flare 可 --ratio 3840x2160 显式 4K）" if model != "gpt-2.5" else "（基础版仅 1K 档）"
                warnings.append(
                    f"⚠ {kind} 目标 {tw}×{th}，{model}（{ratio}）按 1K 锚点估算约 {est_w}×{est_h}，"
                    f"cover-crop 需放大约 {factor:.2f}×{advice}；按显式指定照做"
                )
        else:
            warnings.append(
                f"⚠ {kind} 配 gpt-image：输出分辨率不受控，无法保证 ≥ {tw}×{th}，可能插值放大"
            )
    return {"model": model, "size": size, "ratio": ratio, "warnings": warnings}


def prepare_ref_urls(refs: Optional[List[str]], *, upload=True) -> List[str]:
    """--ref 处理：URL 直传；本地文件外链上传（自有服务器图床→tmpfiles 回退）。

    >14 张在确认门前前置报错（生图 API 硬限制，上游 core/models.py:113-114），
    不浪费一次交互确认。
    """
    if not refs:
        return []
    if len(refs) > 14:
        raise ValueError(f"参考图最多 14 张（生图 API 硬限制），当前 {len(refs)} 张")
    urls: List[str] = []
    for value in refs:
        value = value.strip()
        if not value:
            continue
        path = Path(value).expanduser()
        from reference_upload import looks_like_url  # 纯 urlparse 函数（无网络动作）

        if path.is_file():
            if not upload:
                urls.append(value)
                continue
            from reference_upload import upload_reference  # 惰性 import（requests 软依赖）

            eprint(f"… 参考图 {path.name} → 外链上传（自有服务器图床，失败回退 tmpfiles.org）")
            urls.append(upload_reference(path))
        elif looks_like_url(value) or models.is_data_url(value):
            urls.append(value)
        else:
            raise ValueError(f"参考图既不是本地文件也不是 http(s) URL: {value}")
    return urls


# ---------------------------------------------------------------------------
# 付费确认门（硬门）
# ---------------------------------------------------------------------------

class PaidAbortError(RuntimeError):
    """付费确认未通过（未提交、未扣费）。"""


def confirm_paid_submit(
    count: int,
    price: float,
    assume_yes: bool,
    is_tty: bool,
    stdin=None,
    stderr=sys.stderr,
    breakdown: Optional[dict] = None,
) -> bool:
    """打印费用与审核拒绝不退款提示，按 y/N 确认。

    规则：--yes 跳过；无 --yes 需 TTY 交互，非 TTY 直接中止；
    batch >50 张即使 --yes 也需二次确认（此时非 TTY 同样中止）。
    breakdown（可选）：{model_key: (张数, 单价)}——混合模型 batch 按模型逐行列价。
    """
    total = count * price if breakdown is None else sum(n * p for n, p in breakdown.values())
    if breakdown is None:
        print(f"⚠ 将提交 {count} 张 × {price} 元/张 = {total:.1f} 元（生图 API 实付）", file=stderr)
    else:
        print(f"⚠ 将提交 {count} 张，合计 {total:.1f} 元（生图 API 实付）", file=stderr)
        for model_key, (n, unit) in breakdown.items():
            print(f"  · {model_key} × {n} 张 @ {unit} 元/张", file=stderr)
    print("⚠ 内容审核拒绝（status=3）不退款、不重试；请确认 prompt 与参考图合规", file=stderr)

    over_threshold = count > BATCH_WARN_THRESHOLD
    if over_threshold:
        print(f"⚠ 单次 batch {count} 张 > {BATCH_WARN_THRESHOLD} 张上限保护，共 {total:.1f} 元，需二次确认", file=stderr)

    need_input = over_threshold or not assume_yes
    if not need_input:
        return True
    if not is_tty:
        print("✗ 非交互终端且未获确认（--yes），已中止：未产生任何提交、未扣费", file=stderr)
        return False
    stdin = stdin or sys.stdin
    try:
        answer = input("确认提交？[y/N] ").strip().lower() if stdin is sys.stdin else stdin.readline().strip().lower()
    except EOFError:
        # Windows NUL may report isatty=True, but supplies no confirmation.
        answer = ""
    if answer in ("y", "yes"):
        return True
    print("✗ 已取消：未产生任何提交、未扣费", file=stderr)
    return False


# ---------------------------------------------------------------------------
# job 记账（cache/reports/imgen_jobs_<runid>.json；断线可按 task_id 恢复）
# ---------------------------------------------------------------------------

def new_runid() -> str:
    return time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:6]


def utc_ts() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


class JobReport:
    def __init__(self, path: Path, meta: dict):
        self.path = path
        self.data = {"version": 1, **meta, "entries": [], "submitted_count": 0, "done_count": 0}
        self._lock = threading.Lock()

    def add_entry(self, entry: dict) -> None:
        with self._lock:
            self.data["entries"].append(entry)

    def touch_entry(self, entry: dict, **fields) -> None:
        with self._lock:
            entry.update(fields)
            self._recount()

    def _recount(self) -> None:
        self.data["submitted_count"] = sum(1 for e in self.data["entries"] if e.get("task_id"))
        self.data["done_count"] = sum(1 for e in self.data["entries"] if e.get("status") == "done")

    def flush(self) -> None:
        with self._lock:
            self._recount()
            payload = json.dumps(self.data, ensure_ascii=False, indent=2)
            tmp = self.path.with_name(f".{self.path.name}.tmp-{os.getpid()}")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.path)

    def finish(self, _price: float = None) -> None:
        with self._lock:
            # 按条目模型单价合计（0.1/0.3 元/张随模型而异）
            self.data["estimated_cost_yuan"] = round(sum(
                models.model_price(e.get("model", "nano"))
                for e in self.data["entries"] if e.get("task_id")
            ), 2)
            self.data["finished_utc"] = utc_ts()
            payload = json.dumps(self.data, ensure_ascii=False, indent=2)
            tmp = self.path.with_name(f".{self.path.name}.tmp-{os.getpid()}")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.path)


# ---------------------------------------------------------------------------
# 生成管线（提交 → 轮询 → 下载 → 后处理落位；内化上游 cli.py 线程池 10/10/5）
# ---------------------------------------------------------------------------

_STATUS_LABEL = {0: "排队中", 1: "生成中", 2: "成功", 3: "审核拒绝", -1: "失败"}


def _make_poll_callback(label: str):
    def callback(info: dict) -> None:
        status = info.get("status")
        label_text = _STATUS_LABEL.get(status, str(status) if status is not None else "查询中")
        message = info.get("message")
        suffix = f"：{message}" if message else ""
        eprint(f"… {label} 轮询 status={status}（{label_text}）{suffix}")
    return callback


def _deliver_images(job: dict, tmp_files: List[Path], out_root: Path, raw_seq) -> List[Path]:
    """按 kind 后处理落位（raw 仅尺寸校验留档；其余 cover-crop 精确尺寸断言）。"""
    import postprocess as pp

    kind = job["kind"]
    finals: List[Path] = []

    def archive_raw(src: Path) -> Path:
        info = pp.probe_image(src)  # 仅尺寸校验（读得出即有效 PNG；pix_fmt 原样保留）
        target_dir = out_root / "assets" / "imgen"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{utc_ts()}-{next(raw_seq)}.png"
        shutil.move(str(src), str(target))
        eprint(f"✓ 原图留档 → {target}（{info['width']}×{info['height']} {info['pix_fmt']}）")
        return target

    if kind == "raw":
        for tmp_file in tmp_files:
            finals.append(archive_raw(tmp_file))
        return finals

    tw, th = models.KIND_TARGETS[kind]
    target = out_root / "assets" / "bg" / models.KIND_FILENAME[kind]
    if target.exists():
        eprint(f"⚠ {target} 已存在，将覆盖")
    pp.cover_crop(tmp_files[0], target, tw, th)
    eprint(f"✓ 后处理 → {target}（{tw}×{th} rgb24，断言通过）")
    finals.append(target)
    for tmp_file in tmp_files[1:]:  # 单任务多图时附图留档
        finals.append(archive_raw(tmp_file))
    return finals


def _run_pool(items, worker, max_workers: int):
    """items 逐个丢入线程池；返回 [(item, result_or_exception)]。单条退化为串行。"""
    results = []
    if len(items) <= 1:
        for item in items:
            try:
                results.append((item, worker(item)))
            except Exception as exc:  # noqa: BLE001（单 job 失败不中断批次）
                results.append((item, exc))
        return results
    with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as executor:
        futures = {executor.submit(worker, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                results.append((item, future.result()))
            except Exception as exc:  # noqa: BLE001
                results.append((item, exc))
    return results


def run_generation_pipeline(jobs: List[dict], out_root: Path, api_key: str, api_base: str,
                            report: JobReport) -> int:
    """jobs: [{prompt, model, size, ratio, urls, kind, entry, task_id?}]。

    提交/轮询/下载 = 上游 cli.py 线程池 10/10/5（单张退化为串行）；
    后处理落位主线程串行（ffmpeg 重活不并发）。
    """
    from api_client import ImgenClient

    client = ImgenClient(api_key, api_base=api_base)
    runid = report.data["runid"]
    tmp_dir = out_root / "cache" / "tmp" / runid
    tmp_dir.mkdir(parents=True, exist_ok=True)
    raw_seq = iter(range(1, 1_000_000))
    poll_interval = float(os.environ.get("IMGEN_POLL_INTERVAL", "3"))
    poll_timeout = float(os.environ.get("IMGEN_POLL_TIMEOUT", "180"))
    exit_code = EXIT_OK

    # ---- 阶段 1：提交（池 10）----
    def submit_one(job):
        result = client.submit(job["prompt"], job["model"], job["size"], job["ratio"],
                               job["urls"], quality=job.get("quality"),
                               background=job.get("background"), mask=job.get("mask"))
        job["task_id"] = result["task_id"]
        report.touch_entry(job["entry"], task_id=result["task_id"],
                           payload=result["request"], status="submitted")
        report.flush()
        eprint(f"✓ 提交成功 [{job['label']}] task_id={result['task_id']}（{result['model']}）")
        return result

    to_submit = [j for j in jobs if not j.get("task_id")]
    resumed = [j for j in jobs if j.get("task_id")]
    for job in resumed:
        eprint(f"… 恢复模式 [{job['label']}] 跳过提交，直接轮询 task_id={job['task_id']}")
        report.touch_entry(job["entry"], status="resumed")
    for job, outcome in _run_pool(to_submit, submit_one, 10):
        if isinstance(outcome, Exception):
            _mark_failed(report, job, outcome, "submit_failed")
            exit_code = EXIT_ERROR

    # ---- 阶段 2：轮询（池 10）----
    pending = [j for j in jobs if j.get("task_id") and not _is_terminal(j["entry"])]

    def poll_one(job):
        urls = client.poll_result(job["task_id"], poll_interval, poll_timeout,
                                  callback=_make_poll_callback(job["label"]))
        job["image_urls"] = urls
        report.touch_entry(job["entry"], image_urls=urls, status="polled")
        report.flush()
        return urls

    for job, outcome in _run_pool(pending, poll_one, 10):
        if isinstance(outcome, Exception):
            status = "rejected" if getattr(outcome, "code", None) == 3 else "failed"
            _mark_failed(report, job, outcome, status)
            exit_code = EXIT_ERROR
        else:
            eprint(f"✓ 生成完成 [{job['label']}] → {len(outcome)} 张图待下载")

    # ---- 阶段 3：下载（池 5；URL 2h 失效 → 短重试后上抛）----
    def download_one(job):
        paths = []
        for idx, url in enumerate(job["image_urls"], start=1):
            dest = tmp_dir / f"task{job['no']}_{idx}.png"
            client.download_image(url, dest)
            eprint(f"✓ 下载 [{job['label']}] #{idx} → {dest}")
            paths.append(dest)
        return paths

    polled_ok = [j for j in jobs if j.get("image_urls")]
    for job, outcome in _run_pool(polled_ok, download_one, 5):
        if isinstance(outcome, Exception):
            _mark_failed(report, job, outcome, "download_failed")
            exit_code = EXIT_ERROR
        else:
            job["downloaded"] = outcome

    # ---- 阶段 4：后处理落位（主线程串行）----
    for job in jobs:
        if not job.get("downloaded"):
            continue
        try:
            finals = _deliver_images(job, job["downloaded"], out_root, raw_seq)
            report.touch_entry(job["entry"], status="done",
                               final_paths=[str(p) for p in finals])
            for final in finals:
                print(str(final))  # 结果路径走 stdout（管道友好）
                any_stdout = True
        except Exception as exc:  # noqa: BLE001
            _mark_failed(report, job, exc, "postprocess_failed")
            exit_code = EXIT_ERROR
    report.flush()

    # ---- 收尾：删本次 runid 临时目录（2h URL 失效件与垃圾不滞留）----
    shutil.rmtree(tmp_dir, ignore_errors=True)
    report.finish()
    done = sum(1 for j in jobs if j["entry"].get("status") == "done")
    eprint(f"{'✓' if exit_code == EXIT_OK else '⚠'} 完成 {done}/{len(jobs)}；记账 → {report.path}")
    return exit_code


def _is_terminal(entry: dict) -> bool:
    return entry.get("status") in ("done", "failed", "rejected", "submit_failed", "download_failed", "postprocess_failed")


def _mark_failed(report: JobReport, job: dict, exc: Exception, status: str) -> None:
    report.touch_entry(job["entry"], status=status, error=str(exc))
    report.flush()
    eprint(f"✗ 失败 [{job['label']}]（{status}）: {exc}")


def _new_job(no: int, prompt: str, resolved: dict, urls: List[str], kind: str,
             report: JobReport, task_id: Optional[str] = None,
             quality: Optional[str] = None, background: Optional[str] = None,
             mask: Optional[str] = None) -> dict:
    entry = {
        "no": no,
        "prompt": prompt,
        "kind": kind,
        "model": resolved["model"],
        "size": resolved["size"],
        "ratio": resolved["ratio"],
        "urls": urls,
        "quality": quality,
        "background": background,
        "mask": mask,
        "task_id": task_id,
        "status": "prepared",
        "image_urls": None,
        "final_paths": [],
        "error": None,
    }
    report.add_entry(entry)
    return {
        "no": no, "label": f"#{no}", "prompt": prompt, "model": resolved["model"],
        "size": resolved["size"], "ratio": resolved["ratio"], "urls": urls,
        "quality": quality, "background": background, "mask": mask,
        "kind": kind, "entry": entry, "task_id": task_id,
    }


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def _resolve_key(args) -> str:
    api_key = keychain.resolve_api_key(getattr(args, "key", None))
    eprint(f"✓ key 已解析（{keychain.mask_key(api_key)}），鉴权仅本地持有")
    return api_key


def cmd_gen(args, cfg) -> int:
    from api_client import ImgenClient  # noqa: F401（付费链需 requests）

    model_default = str(_pluginlib_config().cfg_get(cfg, "imgen.model_default", "nano"))
    confirm_enabled = bool(_pluginlib_config().cfg_get(cfg, "imgen.confirm_before_submit", True))

    if args.ref and len(args.ref) > 14:  # 前置报错（先于确认门，不浪费交互）
        eprint(f"✗ 参考图最多 14 张（生图 API 硬限制），当前 {len(args.ref)} 张")
        return EXIT_ERROR

    resolved = resolve_kind_params(args.kind, args.model, args.size, args.ratio, model_default)
    for warning in resolved["warnings"]:
        eprint(warning)
    if not (args.prompt or "").strip():
        eprint("✗ prompt 不能为空")
        return EXIT_ERROR

    # 参数预校验（build_payload 集中模型差异；URL 先用占位跳过上传，确认后再上传）
    try:
        models.build_payload(args.prompt, resolved["model"], resolved["size"], resolved["ratio"], [],
                             quality=args.quality, background=args.background, mask=args.mask)
    except ValueError as exc:
        eprint(f"✗ {exc}")
        return EXIT_ERROR
    price = models.model_price(resolved["model"])

    try:
        api_key = _resolve_key(args)
    except ValueError as exc:
        eprint(f"✗ {exc}")
        return EXIT_ERROR

    resume_mode = bool(args.task_id)
    if resume_mode:
        eprint(f"… 恢复模式：按 --task-id {args.task_id} 只重轮询+下载+后处理，不产生新提交、不再扣费")
    else:
        gate_yes = args.yes or not confirm_enabled
        if not confirm_paid_submit(1, price, gate_yes, sys.stdin.isatty()):
            return EXIT_PAID_ABORT

    try:
        urls = prepare_ref_urls(args.ref)  # 本地文件外链上传（免费，不扣费）
    except Exception as exc:  # noqa: BLE001（上传失败=未提交未扣费）
        eprint(f"✗ {exc}")
        return EXIT_ERROR

    out_root = Path(args.out).expanduser()
    out_root.mkdir(parents=True, exist_ok=True)
    runid = new_runid()
    reports_dir = out_root / "cache" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report = JobReport(reports_dir / f"imgen_jobs_{runid}.json", {
        "runid": runid, "started_utc": utc_ts(), "command": "gen",
        "api_base": resolve_api_base(cfg),
    })
    job = _new_job(1, args.prompt, resolved, urls, args.kind, report, task_id=args.task_id,
                   quality=args.quality, background=args.background, mask=args.mask)
    report.flush()
    return run_generation_pipeline([job], out_root, api_key, resolve_api_base(cfg), report)


def cmd_batch(args, cfg) -> int:
    spec_path = Path(args.prompts_json).expanduser()
    if not spec_path.is_file():
        eprint(f"✗ prompts.json 不存在: {spec_path}")
        return EXIT_ERROR
    try:
        items = json.loads(spec_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        eprint(f"✗ prompts.json 不是合法 JSON: {exc}")
        return EXIT_ERROR
    if not isinstance(items, list) or not items:
        eprint("✗ prompts.json 应为非空列表 [{prompt, kind, model, size, ratio, ref}, ...]")
        return EXIT_ERROR

    price = float(_pluginlib_config().cfg_get(cfg, "imgen.price_per_image", 0.1))  # 兼容旧 config；实际按模型单价
    model_default = str(_pluginlib_config().cfg_get(cfg, "imgen.model_default", "nano"))
    confirm_enabled = bool(_pluginlib_config().cfg_get(cfg, "imgen.confirm_before_submit", True))

    specs = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict) or not str(item.get("prompt", "")).strip():
            eprint(f"✗ 第 {idx} 条缺少非空 prompt")
            return EXIT_ERROR
        ref = item.get("ref") or []
        if isinstance(ref, str):
            ref = [ref]
        if len(ref) > 14:
            eprint(f"✗ 第 {idx} 条参考图最多 14 张，当前 {len(ref)} 张")
            return EXIT_ERROR
        kind = item.get("kind") or "raw"
        if kind not in models.KIND_CHOICES:
            eprint(f"✗ 第 {idx} 条未知 kind: {kind}（可选 {', '.join(models.KIND_CHOICES)}）")
            return EXIT_ERROR
        resolved = resolve_kind_params(kind, item.get("model"), item.get("size"),
                                       item.get("ratio"), model_default)
        for warning in resolved["warnings"]:
            eprint(f"[#{idx}] {warning}")
        quality = item.get("quality")
        background = item.get("background")
        mask = item.get("mask")
        try:
            models.build_payload(item["prompt"], resolved["model"], resolved["size"], resolved["ratio"], [],
                                 quality=quality, background=background, mask=mask)
        except ValueError as exc:
            eprint(f"✗ 第 {idx} 条参数不合法: {exc}")
            return EXIT_ERROR
        specs.append({"prompt": str(item["prompt"]).strip(), "resolved": resolved,
                      "ref": ref, "kind": kind, "quality": quality,
                      "background": background, "mask": mask})

    try:
        api_key = _resolve_key(args)
    except ValueError as exc:
        eprint(f"✗ {exc}")
        return EXIT_ERROR

    # 按模型单价逐行列价（2.5 基础版 0.1、sunburst/flare 0.3 元/张）
    breakdown: dict = {}
    for spec in specs:
        mk = spec["resolved"]["model"]
        n, unit = breakdown.get(mk, (0, models.model_price(mk)))
        breakdown[mk] = (n + 1, unit)
    gate_yes = args.yes or not confirm_enabled
    if not confirm_paid_submit(len(specs), price, gate_yes, sys.stdin.isatty(), breakdown=breakdown):
        return EXIT_PAID_ABORT

    # 确认后再做免费但可能失败的外链上传
    for idx, spec in enumerate(specs, start=1):
        try:
            spec["urls"] = prepare_ref_urls(spec["ref"])
        except Exception as exc:  # noqa: BLE001
            eprint(f"✗ 第 {idx} 条参考图处理失败: {exc}")
            return EXIT_ERROR

    out_root = Path(args.out).expanduser()
    out_root.mkdir(parents=True, exist_ok=True)
    runid = new_runid()
    reports_dir = out_root / "cache" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report = JobReport(reports_dir / f"imgen_jobs_{runid}.json", {
        "runid": runid, "started_utc": utc_ts(), "command": "batch",
        "api_base": resolve_api_base(cfg),
    })
    jobs = [_new_job(no, s["prompt"], s["resolved"], s["urls"], s["kind"], report,
                     quality=s.get("quality"), background=s.get("background"),
                     mask=s.get("mask"))
            for no, s in enumerate(specs, start=1)]
    report.flush()
    return run_generation_pipeline(jobs, out_root, api_key, resolve_api_base(cfg), report)


def cmd_postprocess(args, _cfg) -> int:
    import postprocess as pp

    src = Path(args.input).expanduser()
    if not src.is_file():
        eprint(f"✗ 输入图片不存在: {src}")
        return EXIT_ERROR
    dst = Path(args.output).expanduser()
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        eprint(f"⚠ {dst} 已存在，将覆盖")
    try:
        pp.cover_crop(src, dst, kind=args.kind)
    except RuntimeError as exc:
        eprint(f"✗ {exc}")
        return EXIT_ERROR
    print(str(dst))
    return EXIT_OK


def cmd_key_status(args, _cfg) -> int:
    levels, chosen = keychain.describe_key_chain(getattr(args, "key", None))
    eprint("key 解析链本地探测（不触网、不扣费）：")
    for name, value in levels:
        if value:
            eprint(f"  ✓ {name}：{keychain.mask_key(value)}")
        else:
            eprint(f"  ✗ {name}：未提供/未命中")
    if chosen:
        level, api_key = chosen
        eprint(f"✓ 最终将使用：{level}（尾 4 位 {keychain.mask_key(api_key)}）")
        return EXIT_OK
    eprint("✗ 未配置任何 key。请通过 --key、SC_API_KEY / NANOBANANA2_API_KEY 环境变量、")
    eprint("  keyring（service=vtx-imgen, account=api-key）或 ~/.config/vtx-imgen/secrets.json 提供。")
    return EXIT_ERROR


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

_KIND_MATRIX = """\
--kind 联动矩阵：
  bg4k     → nano / 4K / 16:9 → assets/bg/background_4k.png     (3840x2160)
  bg1080p  → nano / 2K / 16:9 → assets/bg/background_1080p.png  (1920x1080)
  vertical → nano / 4K / 9:16 → assets/bg/background_vertical.png(1080x1920)
  raw      → 不联动 → assets/imgen/<UTC>-<n>.png（原图留档，仅尺寸校验）

模型矩阵（--model）：
  nano              NanoBanana2，0.1 元/张，1K/2K/4K + --ratio
  gpt-image         GPT-Image-2，0.1 元/张，--size 传比例
  gpt-2.5           GPT-Image-2.5 基础版，0.1 元/张，1K 档比例（无 quality 等参数）
  gpt-2.5-sunburst  慢速高精、适合编辑图，0.3 元/张，--quality low~max、--background、--mask，
                    2K/4K 用 --ratio 显式像素（如 3840x2160）
  gpt-2.5-flare     快速、质量好，0.3 元/张，--quality low/medium/high、--background、--mask

示例：
  env python3 apps/imgen/imgen_cli.py gen "深色科技感演播室背景，无人物" --out <项目> --kind bg4k --yes
  env python3 apps/imgen/imgen_cli.py gen "..." --out <项目> --kind bg4k --size 2K --yes   # 警告后照做
  env python3 apps/imgen/imgen_cli.py gen "..." --out <项目> --model gpt-2.5 --ratio 16:9 --yes
  env python3 apps/imgen/imgen_cli.py gen "..." --out <项目> --model gpt-2.5-sunburst --quality high --yes
  env python3 apps/imgen/imgen_cli.py gen "..." --out <项目> --ref ref1.png https://.../r2.jpg
  python3 apps/imgen/imgen_cli.py batch prompts.json --out <项目> --yes
  python3 apps/imgen/imgen_cli.py postprocess raw.png --kind bg4k -o assets/bg/background_4k.png
  python3 apps/imgen/imgen_cli.py key status

付费纪律：按模型单价 0.1 / 0.3 元/张；审核拒绝（status=3）不退款不重试；
无 --yes 时需交互确认，非交互终端必须 --yes。"""

_EPILOG = _KIND_MATRIX + """

实现来源：由独立参考实现改写内化（编排/模型/API 客户端/key 链/参考图外链）；
运行时零外部仓依赖。"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="imgen_cli.py",
        description="生图 API 生图（0.1/0.3 元/张按模型）+ ffmpeg lanczos cover-crop 后处理（imgen 模块总入口）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("gen", help="单张生成（付费 0.1/0.3 元/张按模型；提交→轮询→下载→后处理一体）")
    g.add_argument("prompt", help="生成 prompt")
    g.add_argument("--out", required=True, help="项目目录（产物按 --kind 落位，目录自动创建）")
    g.add_argument("--model", default=None, choices=list(models.MODELS),
                   help="模型（默认按 --kind 联动或 config imgen.model_default）；"
                        "2.5 系=gpt-2.5 / gpt-2.5-sunburst / gpt-2.5-flare")
    g.add_argument("--size", default=None,
                   help="nano=1K/2K/4K；gpt-image=比例（auto/1:1/16:9/9:16 等）；2.5 系不适用（用 --ratio）")
    g.add_argument("--ratio", default=None,
                   help="nano/2.5 系的 aspectRatio（默认 auto 或 kind 联动）；2.5 系还收显式像素"
                        "（如 3840x2160，sunburst/flare 的 2K/4K 档）；gpt-image 不收（用 --size）")
    g.add_argument("--quality", default=None,
                   help="仅 gpt-2.5-sunburst（low/medium/high/xhigh/max）与 gpt-2.5-flare"
                        "（low/medium/high）；默认 medium")
    g.add_argument("--background", default=None, choices=("auto", "transparent"),
                   help="仅 sunburst/flare：transparent 透明底（默认 auto）")
    g.add_argument("--mask", default=None,
                   help="仅 sunburst/flare：遮罩图公网 URL（局部重绘）")
    g.add_argument("--ref", nargs="+", default=None,
                   help="参考图，本地路径或 http(s) URL，最多 14 张（本地图先外链上传）")
    g.add_argument("--kind", default="raw", choices=models.KIND_CHOICES,
                   help="落位/档位联动（默认 raw 仅留档）")
    g.add_argument("--yes", action="store_true", help="跳过付费确认（非 TTY 环境必需）")
    g.add_argument("--key", default=None, help="API key 值或 key 文件路径（优先级最高）")
    g.add_argument("--task-id", default=None,
                   help="断线恢复：按 job id 只重轮询+下载+后处理（不产生新提交）")

    b = sub.add_parser("batch", help="批量生成（prompts.json 列表，编排线程池 10/10/5）")
    b.add_argument("prompts_json", help="JSON 列表 [{prompt, kind, model, size, ratio, ref,"
                                        " quality, background, mask}, ...]")
    b.add_argument("--out", required=True, help="项目目录")
    b.add_argument("--yes", action="store_true", help="跳过付费确认（>50 张仍需二次确认）")
    b.add_argument("--key", default=None, help="API key 值或 key 文件路径")

    pp = sub.add_parser("postprocess", help="离线 cover-crop 后处理（免 API、不扣费）")
    pp.add_argument("input", help="输入 PNG")
    pp.add_argument("--kind", required=True,
                    choices=[k for k in models.KIND_CHOICES if k != "raw"],
                    help="目标档位（决定精确输出尺寸）")
    pp.add_argument("-o", "--output", required=True, help="输出 PNG 路径")

    k = sub.add_parser("key", help="API key 管理（只本地探测）")
    k.add_argument("--key", default=None, help="顺带探测一个 --key 值/文件")
    ks = k.add_subparsers(dest="key_command", required=True)
    ks.add_parser("status", help="打印 key 解析链与最终命中（不触网、不扣费）")

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = _pluginlib_config().load_config()
    try:
        if args.command == "gen":
            return cmd_gen(args, cfg)
        if args.command == "batch":
            return cmd_batch(args, cfg)
        if args.command == "postprocess":
            return cmd_postprocess(args, cfg)
        if args.command == "key" and args.key_command == "status":
            return cmd_key_status(args, cfg)
        eprint("✗ 未知子命令")
        return EXIT_ERROR
    except PaidAbortError as exc:
        eprint(f"✗ {exc}")
        return EXIT_PAID_ABORT
    except Exception as exc:  # noqa: BLE001（中文可行动报错，随上游 main 风格）
        eprint(f"✗ {exc}")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
