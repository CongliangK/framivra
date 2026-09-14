"""blender-bridge 插件（缝 D · finish 管线收尾钩子，协议 v1.2；设计权威 docs/ARCHITECTURE.md）。

timeline **顶层键** ``"blender_bridge": true | {"out": "...", "verify": bool}``——
`vtx all` 全链（qc 通过后）自动导出 Blender 审片工程（apps/blender-bridge/
json2blend.py，经 ``blender --background --factory-startup --python`` 运行）。
「JSON 里配置了就默认直接导出」——省去每次手敲 blender 命令行。

- 产物默认 ``<项目>/preview.blend``（键参 ``out`` 相对项目根可改；拒绝越出
  项目根的 .. 路径）；``verify: true`` 时追加 verify_blend.py 回读断言。
- blender 不在 PATH → finish 返回错误行（exit 1，**正式产物 final.mp4 不受
  影响**）——键声明即用户要导出，静默跳过 = 配置了却没导出；安装期预检走
  doctor（requires.binaries blender + health 探针）。
- 与 apps/blender-bridge（应用件）的关系：app 提供 json2blend/verify_blend/
  audit_bundle 等交互工具（命令面不变），本插件只是把「渲染完自动导出」钩进
  `vtx all` 收尾缝——关停本插件不影响主链（§0.2 关停测试）。
- --draft / --dry-run / `all --until` 截断链不触发（cli 缝 D 接线口径）。
"""
import json
import shutil
import subprocess
from pathlib import Path

VERSION = "1.0.0"
KEY = "blender_bridge"
_REPO_ROOT = Path(__file__).resolve().parents[2]     # plugins/blender-bridge → 仓根
_JSON2BLEND = _REPO_ROOT / "apps" / "blender-bridge" / "json2blend.py"
_VERIFY = _REPO_ROOT / "apps" / "blender-bridge" / "verify_blend.py"


def source_resolve_map(c):
    """缝 A 读取点映射（P6）：sources[] 的插件产物解析（虚拟源如 bgm 工程 JSON、
    降噪源 denoise）→ {sid: wav 绝对路径}。经 vtx.api.resolve_source_wav（内核
    统一解析，缓存命中即时；产物缺失按插件语义产出——finish 期 prepare 已跑过，
    恒为缓存命中）。媒体源返回 None 不进映射。json2blend 在 blender 解释器内
    无法 import vtx，故由本插件算好映射经 argv 传入。"""
    from vtx.api import resolve_source_wav
    mapping = {}
    for s in c.tl.get("sources", []):
        if not isinstance(s, dict) or not isinstance(s.get("id"), str):
            continue
        try:
            p = resolve_source_wav(c, s["id"])
        except Exception:
            p = None
        if p is not None and Path(p).is_file():
            mapping[s["id"]] = str(Path(p).resolve())
    return mapping


def _params(c):
    """键值归一：true → 缺省参数；dict → 原样。无键 → None（不生效）。"""
    val = c.tl.get(KEY)
    if val is None:
        return None
    return {} if val is True else val


def validate(c):
    """validate 钩子（协议 v1.1）：键值形状早失败。"""
    val = c.tl.get(KEY)
    if val is None:
        return []
    errs = []
    if not (val is True or isinstance(val, dict)):
        return [f"timeline: 键 {KEY!r} 须为 true 或对象 {{out, verify}}，得到 {val!r}"]
    if isinstance(val, dict):
        unknown = sorted(set(val) - {"out", "verify"})
        if unknown:
            errs.append(f"timeline: 键 {KEY!r} 含未知参数 {unknown}（可用：out/verify）")
        out = val.get("out", "preview.blend")
        if not isinstance(out, str) or not out:
            errs.append(f"timeline: {KEY}.out 须为非空字符串（相对项目根，如 preview.blend）")
        else:
            p = Path(out)
            if p.is_absolute() or ".." in p.parts:
                errs.append(f"timeline: {KEY}.out 禁止绝对路径/..（须落在项目根内）: {out}")
        if "verify" in val and not isinstance(val["verify"], bool):
            errs.append(f"timeline: {KEY}.verify 须为布尔值")
    return errs


def _run_blender_script(blender, script, args, what):
    """blender --background --factory-startup --python <script> -- <args>。"""
    cmd = [blender, "--background", "--factory-startup",
           "--python", str(script), "--", *[str(a) for a in args]]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        return cmd, f"{what} 失败（exit {r.returncode}）：{' | '.join(tail[-3:])}"
    return cmd, ""


def finish(c):
    """缝 D 钩子（协议 v1.2）：`vtx all` 收尾自动导出 .blend。返回错误行列表。"""
    params = _params(c)
    if params is None:
        return []
    blender = shutil.which("blender")
    if blender is None:
        return ["blender 不在 PATH（timeline 已声明 blender_bridge 键要求自动导出 .blend）——"
                "安装 blender 后重跑 `vtx all`，或移除该键改用手动导出"]
    if not _JSON2BLEND.is_file():
        return [f"json2blend 脚本缺失：{_JSON2BLEND}（apps/blender-bridge 应用件不完整？）"]
    out = Path(params.get("out", "preview.blend"))
    out_abs = (c.root / out).resolve()
    if c.root.resolve() not in out_abs.parents:
        return [f"{KEY}.out 越出项目根: {out}"]
    out_abs.parent.mkdir(parents=True, exist_ok=True)   # 子目录产物（如 audit/ep.blend）父目录自建
    print(f"  · blender-bridge: 导出 {out}（json2blend，blender --background）")
    # 缝 A 产物映射（P6）：虚拟源（bgm 工程 JSON）/降噪源的音频走插件产物 wav，
    # json2blend 直引源路径会对 JSON 源挂掉（v1 盲区——denoise 源同样被忽略）
    resolve_map = source_resolve_map(c)
    map_file = None
    extra_args = []
    if resolve_map:
        map_file = c.cache / "tmp" / "blender_bridge_source_map.json"
        map_file.parent.mkdir(parents=True, exist_ok=True)
        map_file.write_text(json.dumps(resolve_map, ensure_ascii=False), encoding="utf-8")
        extra_args = [map_file]
        print(f"    源产物映射：{len(resolve_map)} 源经插件解析（bgm/denoise 产物 wav）")
    try:
        cmd, err = _run_blender_script(blender, _JSON2BLEND,
                                       [c.tl_path, out_abs, *extra_args],
                                       "json2blend 导出")
    finally:
        if map_file is not None:
            map_file.unlink(missing_ok=True)
    if err:
        return [err + f"（完整命令：{' '.join(cmd[:6])} …）"]
    if not out_abs.is_file():
        return [f"json2blend 声称成功但产物未落盘: {out_abs}"]
    print(f"✓ blender-bridge：已导出 {out}（{out_abs.stat().st_size} B，审片工程）")
    if params.get("verify"):
        # verify_blend 契约：blender --python verify_blend.py -- <timeline> <blend>
        cmd, err = _run_blender_script(blender, _VERIFY, [c.tl_path, out_abs],
                                       "verify_blend 校验")
        if err:
            return [err]
        print(f"✓ blender-bridge：verify_blend 回读校验通过（{out}）")
    return []
