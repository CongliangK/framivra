"""doctor：环境探测/校验（BG-D3）。

runtime.json 的唯一写入者是本模块（探测逻辑）；引擎只读。
R1 收尾由主线生成 runtime.json；本模块负责校验与报告。

呈现层（R4，2026-08-22）：
    doctor_report()      探测/校验逻辑——R1 起冻结，不动。
    python3 bgm_gen/doctor.py          默认人读绿红表（逐项 ✓/✗ + 明细 + 可选档标注）。
    python3 bgm_gen/doctor.py --json   原 JSON 输出（与 doctor_report() 逐字段一致）。
    python3 -m bgm_gen doctor          cli.py 冻结路径，仍输出 JSON（机器可读不回退）。
    exit code：0 = 全部必检项通过；1 = 存在 ✗（与 JSON 报告的 ok 字段一致）。

v3 R1-W1 增（PLAN_v3 §3.1 防漂移四件套③）：
    item 结构扩展 severity: "error"|"warn"——error 失败才拉红 report.ok/exit code；
    warn（如 binary_pin sha/版本漂移 advisory 黄灯）不影响 exit code。
    每引擎新增 2 项：descriptor.<eng>（存在+可解析+必填字段齐，error）+
    descriptor.<eng>.binary_pin（version/sha 与 runtime 实测交叉核验，warn）。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # 仓库根（脚本直跑模式的 import 基准）
DESCRIPTORS_DIR = os.path.join(HERE, "engines", "descriptors")

if ROOT not in sys.path:              # 直跑 python3 bgm_gen/doctor.py 亦可 import 包
    sys.path.insert(0, ROOT)

# ---- v3 R1-W1 描述符一致性 ----
ENGINE_NAMES = ("synth", "sf2", "sfz", "sample", "master", "demucs", "ai")
# 主二进制 pin → 实测探针映射（None=无独立二进制；additional_pins 按 name 查同表）
_PRIMARY_PIN_FACT = {"synth": None, "sample": None, "sf2": "fluidsynth",
                     "sfz": "sfizz_render", "master": "ffmpeg",
                     "demucs": "demucs", "ai": "comfyui_version"}


def load_descriptors(dir=DESCRIPTORS_DIR):
    """descriptors/*.json → ({engine: desc}, {engine: 错误文本})。

    结构检查 = engines/descriptor_check.py 唯一共享实现（与 engines.base
    注册表同源——R1 审查官 B·D1 修复：原 doctor 自检为弱化子集，假绿风险）。
    """
    from bgm_gen.engines.descriptor_check import check_descriptor_structure

    descs, errs = {}, {}
    if not os.path.isdir(dir):
        return descs, errs
    for fn in sorted(os.listdir(dir)):
        if not fn.endswith(".json"):
            continue
        eng = fn[:-5]
        path = os.path.join(dir, fn)
        try:
            with open(path, encoding="utf-8") as f:
                desc = json.load(f)
        except Exception as e:                                   # noqa: BLE001
            errs[eng] = f"解析失败：{e}"
            continue
        err = check_descriptor_structure(desc, fn)
        if err:
            errs[eng] = err
        else:
            descs[eng] = desc
    return descs, errs


def _probe_binary_versions(rt):
    """binary_pin 交叉核验的实测值：{探针名: 版本串 或 (sha256, size) 或 错误串}。

    探针（秒级，无缓存）：
      fluidsynth  `fluidsynth --version`（系统包）
      ffmpeg      `ffmpeg -version`（系统包）
      sfizz       sha/size——sfizz_render 无 --version（实测 'Option version does
                  not exist'），版本为 rpm 登记事实，核验退化（C2）
      comfyui     GET /system_stats → system.comfyui_version
      demucs/matchering  comfyui env python 单次子进程 import（共用一次 torch 加载）
    """
    facts = {}
    try:
        r = subprocess.run(["fluidsynth", "--version"], capture_output=True,
                           text=True, timeout=30)
        m = re.search(r"runtime version ([\d.]+)", (r.stdout or "") + (r.stderr or ""))
        facts["fluidsynth"] = m.group(1) if m else f"无法解析（exit={r.returncode}）"
    except Exception as e:                                       # noqa: BLE001
        facts["fluidsynth"] = f"探测失败：{e}"
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True,
                           text=True, timeout=30)
        m = re.search(r"ffmpeg version ([\d.]+)", r.stdout or "")
        facts["ffmpeg"] = m.group(1) if m else f"无法解析（exit={r.returncode}）"
    except Exception as e:                                       # noqa: BLE001
        facts["ffmpeg"] = f"探测失败：{e}"

    tool = (rt.get("tools") or {}).get("sfizz_render") or ""
    bin_path = tool
    if os.path.basename(tool) == "sfizz_render.sh":
        alt = os.path.join(os.path.dirname(tool), "usr", "bin", "sfizz_render")
        if os.path.isfile(alt):
            bin_path = alt
    if os.path.isfile(bin_path):
        try:
            with open(bin_path, "rb") as f:
                facts["sfizz_render"] = (hashlib.sha256(f.read()).hexdigest(),
                                         os.path.getsize(bin_path))
        except Exception as e:                                   # noqa: BLE001
            facts["sfizz_render"] = f"sha/size 读取失败：{e}"
    else:
        facts["sfizz_render"] = f"二进制不在：{bin_path}"

    base = (rt.get("comfyui") or {}).get("base_url") or "http://127.0.0.1:8188"
    try:
        with urllib.request.urlopen(f"{base}/system_stats", timeout=5) as r:
            st = json.loads(r.read().decode())
            facts["comfyui_version"] = ((st.get("system") or {}).get("comfyui_version")
                                        or "缺失")
    except Exception as e:                                       # noqa: BLE001
        facts["comfyui_version"] = f"不可达/解析失败：{e}"

    py = ((rt.get("demucs") or {}).get("python")
          or (rt.get("matchering") or {}).get("python")
          or rt.get("python_torch"))
    if py:
        code = "import demucs,matchering;print(demucs.__version__);print(matchering.__version__)"
        try:
            r = subprocess.run([py, "-c", code], capture_output=True,
                               text=True, timeout=120)
            lines = (r.stdout or "").strip().splitlines()
            if r.returncode == 0 and len(lines) >= 2:
                facts["demucs"], facts["matchering"] = lines[0], lines[1]
            else:
                facts["demucs"] = facts["matchering"] = f"无法解析（exit={r.returncode}）"
        except Exception as e:                                   # noqa: BLE001
            facts["demucs"] = facts["matchering"] = f"探测失败：{e}"
    else:
        facts["demucs"] = facts["matchering"] = "无 comfyui env python（runtime.json）"
    return facts


def _check_pin(desc, pin_name, pin, facts):
    """单个 pin 与实测交叉核验 → (ok, detail)。sfizz 特殊：sha/size 核验。"""
    if pin_name is None:
        return True, "无独立二进制（纯 numpy 实现，version=null）"
    if pin_name == "sfizz_render":
        got = facts.get("sfizz_render")
        if not isinstance(got, tuple):
            return False, f"sfizz_render 无法 sha/size 核验：{got}"
        sha, size = got
        if pin.get("sha256") and sha != pin["sha256"]:
            return False, (f"sfizz_render sha 漂移：实测 {sha[:16]}… ≠ "
                           f"描述符 {pin['sha256'][:16]}…")
        if pin.get("size") and size != pin["size"]:
            return False, f"sfizz_render size 漂移：实测 {size} ≠ 描述符 {pin['size']}"
        return True, f"sfizz_render {pin.get('version')} sha/size 一致（版本= rpm 登记事实）"
    want = pin.get("version")
    if not want:
        return True, f"{pin_name} 版本未 pin（null）"
    got = facts.get(pin_name)
    if got is None:
        return True, f"{pin_name} 无探针（跳过交叉核验）"
    if not isinstance(got, str) or got != want:
        return False, f"{pin_name} 版本漂移：实测 {got!r} ≠ 描述符 {want!r}"
    return True, f"{pin_name} {want} 一致"


def check_descriptors(item, descs, errs, facts):
    """v3 R1-W1 描述符一致性组：每引擎 2 项。

    descriptor.<eng>           完整性（error：失败拉红 ok/exit code）
    descriptor.<eng>.binary_pin   binary_pin 与实测交叉核验（warn：advisory 黄灯，
                              失败不拉红——C2：sha=advisory；版本漂移提示走
                              '先 diff 描述符再重收割' 流程）
    纯函数（不碰磁盘/网络），item 为收口回调 → 可单元测试。
    """
    for eng in ENGINE_NAMES:
        desc = descs.get(eng)
        if desc is None:
            item(f"descriptor.{eng}", False, errs.get(eng, "描述符缺失"),
                 severity="error")
            item(f"descriptor.{eng}.binary_pin", True, "描述符不可用，跳过",
                 severity="warn")
            continue
        cov = desc.get("coverage") or {}
        evs = sorted(e.get("type") for e in desc.get("events_supported") or [])
        item(f"descriptor.{eng}", True,
             f"tier {cov.get('tier', '?')} · v{desc.get('version')} · "
             f"{len(desc.get('parameters') or [])} params"
             + (f" · events: {','.join(evs)}" if evs else " · 无事件"),
             severity="error")
        pin = desc.get("binary_pin") or {}
        pins = [(_PRIMARY_PIN_FACT.get(eng), pin)]
        pins += [(a.get("name"), a) for a in pin.get("additional_pins") or []]
        bad = []
        for fact_name, p in pins:
            ok, detail = _check_pin(desc, fact_name, p, facts)
            if not ok:
                bad.append(detail)
        if bad:
            item(f"descriptor.{eng}.binary_pin", False, "；".join(bad),
                 severity="warn")
        else:
            item(f"descriptor.{eng}.binary_pin", True, "binary_pin 与实测一致",
                 severity="warn")


def _path_ok(p):
    if not p:
        return False
    p = os.path.expanduser(p)
    return os.path.exists(p) if os.path.isabs(p) else bool(shutil.which(p))


def _http_ok(url, timeout=3):
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except Exception:
        return False


def _torch_ok(py, timeout=90, code="import torch;print(torch.__version__)"):
    try:
        r = subprocess.run([py, "-c", code],
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout.strip()
    except Exception:
        return False, ""


def _check_fluidsynth_lib(val):
    """tools.fluidsynth_lib 校验（M-1，与 sf2._load_lib 三级解析同口径）：
    记录了值 → ctypes.CDLL 试载该值成功才算绿（find_library 真值不算——
    系统里探测得到库不代表 runtime 记录的路径/soname 可载）；
    未记录 → find_library 探测后提示可写入 runtime.json 固定解析。"""
    import ctypes
    import ctypes.util
    if val:
        try:
            ctypes.CDLL(val)
            return True, val
        except OSError as e:
            return False, f"{val}（ctypes.CDLL 试载失败：{e}）"
    found = ctypes.util.find_library("fluidsynth")
    if found:
        return True, (f"未记录（find_library 探测到 {found}；"
                      "可写入 runtime.json tools.fluidsynth_lib 固定解析）")
    return False, ("未记录且 find_library 未探测到 libfluidsynth；可在 runtime.json "
                   "tools.fluidsynth_lib 填 so/DLL 全路径")


def doctor_report():
    from bgm_gen.paths import effective_runtime, RuntimeConfigError
    try:
        rt = effective_runtime()
    except RuntimeConfigError as e:
        return {"ok": False, "items": [{"name": "MUSIC_RUNTIME", "ok": False,
                "detail": str(e), "optional": False, "severity": "error"}]}
    report = {"ok": True, "items": []}

    def item(name, ok, detail="", optional=False, severity="error"):
        """收口：severity="error" 且失败且非 optional → 拉红 report.ok；
        severity="warn"（advisory 黄灯）失败不影响 ok/exit code（C2）。"""
        report["items"].append({"name": name, "ok": bool(ok), "detail": detail,
                                "optional": optional, "severity": severity})
        if not ok and not optional and severity == "error":
            report["ok"] = False

    for key, val in rt.get("tools", {}).items():
        if key == "fluidsynth_lib":
            item(f"tools.{key}", *_check_fluidsynth_lib(val))
        else:
            item(f"tools.{key}", _path_ok(val), val)
    if "fluidsynth_lib" not in (rt.get("tools") or {}):
        # 未记录也出体检行（M-1）：find_library 探测 + 提示可写入 runtime.json
        item("tools.fluidsynth_lib", *_check_fluidsynth_lib(None))
    for key, val in rt.get("sf2", {}).items():
        item(f"sf2.{key}", _path_ok(val), val)
    item("assets_root", _path_ok(rt.get("assets_root")), rt.get("assets_root", ""))
    item("samples_root", _path_ok(rt.get("samples_root")), rt.get("samples_root", ""))

    py_sys = rt.get("python_system")
    ok_sys, ver_sys = _torch_ok(py_sys, code="import numpy;print(numpy.__version__)") if py_sys else (False, "")
    item("python_system.numpy", ok_sys, ver_sys or py_sys or "")

    py_torch = rt.get("python_torch")
    ok, ver = _torch_ok(py_torch) if py_torch else (False, "")
    item("python_torch.torch", ok, ver or py_torch or "")

    cu = rt.get("comfyui", {}).get("base_url")
    item("comfyui", _http_ok(cu) if cu else False, cu or "")

    dr = rt.get("diffrhythm", {})
    item("diffrhythm", dr.get("installed", False), dr.get("note", "可选档"), optional=True)

    # ---- v3 R1-W1：描述符一致性组（error=完整性；warn=binary_pin 交叉核验黄灯）----
    descs, desc_errs = load_descriptors()
    check_descriptors(item, descs, desc_errs, _probe_binary_versions(rt))

    return report


# ------------------------------------------------------------------ 呈现层（R4）

def pretty_report(report):
    """人读绿红表：逐项 ✓/✗ + 明细 + optional 标注"可选档" + warn 失败标注黄灯。
    每行必含 ✓ 或 ✗（test_cli 计数口径：绿红行数 == 体检项数）。"""
    items = report["items"]
    n_ok = sum(1 for it in items if it["ok"])
    n_bad = len(items) - n_ok
    width = max((len(it["name"]) for it in items), default=0)
    lines = ["bgm doctor — 环境体检", "─" * max(46, width + 34)]
    for it in items:
        mark = "✓" if it["ok"] else "✗"
        opt = "（可选档）" if it.get("optional") else ""
        warn = "" if it["ok"] or it.get("severity") != "warn" else "（黄灯·不阻断）"
        lines.append(f" {mark} {it['name']:<{width}}  {it.get('detail', '')}{opt}{warn}")
    lines.append("─" * max(46, width + 34))
    if report["ok"]:
        lines.append(f" 总判定：OK（{n_ok}/{len(items)} 项通过）")
    else:
        bad = [it["name"] for it in items
               if not it["ok"] and not it.get("optional")
               and it.get("severity") != "warn"]
        lines.append(f" 总判定：有问题（{n_bad} ✗ / {len(items)} 项）——未过项：{', '.join(bad)}")
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="doctor", description="bgm 环境体检（默认人读绿红表；--json 输出机器可读 JSON）")
    p.add_argument("--json", action="store_true", help="输出原 JSON（与 doctor_report() 一致）")
    a = p.parse_args(argv)
    report = doctor_report()
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(pretty_report(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
