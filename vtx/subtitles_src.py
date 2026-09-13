"""内核 subtitles_src 键（v2 §1.1 条目 6 / §0.3 对抗审查裁定 #4）。

timeline 根级可选键 `subtitles_src`（字符串，相对 timeline.json 的路径）：声明
字幕文件来源，内核只负责**引用与烧录语义**——谁产出该文件（asr/手写/其他工具）
内核不感知（asr 只是产出该文件的一种应用，字幕消费侧与 asr 解耦）。

语义裁定（P1-2，写入代码即冻结）：
  - `subtitles_src` 与根级 `subtitles[]` **互斥**——两者都非空 validate 报错；
  - 解析在 **timeline 加载点**完成（infra.Ctx 构造期，先于依赖校验与回执指纹）：
    load 后 c.tl["subtitles"] 即为解析结果，各阶段/preview 消费方无感（拿到的
    已是帧值列表；diff 与 preview.html 消费原始文件，v1 timeline 零影响）；
  - 解析失败/文件缺失 → validate FAIL（错误记入 ctx.subtitles_errors，不在加载
    点 crash——单阶段直跑时也能构造出可用的 Ctx）；
  - 参与回执身份：文件内容 digest 并入 cache_state.receipt_fingerprint（对齐
    gfx html/声明依赖的处理方式），文件变更 → 回执失效。

支持格式：
  - `.srt`：时间戳解析 → 按项目 fps 换算整帧（**四舍五入 half-up**，
    Python round() 是 banker's rounding，刻意用 floor(x+0.5) 对齐中文语义），
    [in, out) 半开区间（SRT 闭区间结束时刻 → 独占出帧）；多行文本以 "\\N" 连接
    （timeline 字幕行断行约定，subs 检查按 \\N 切行）；空文件 = 无字幕（合法）。
  - `.json`：v1 subtitles[] 同构列表（帧值直用），也接受 {"subtitles": [...]}
    包裹形。
"""
import json
import re
from pathlib import Path

# SRT 时间戳行：HH:MM:SS,mmm --> HH:MM:SS,mmm（毫秒分隔符 , 与 . 都接受——部分
# 工具产 .srt 用点分隔；行尾允许 SRT 属性残留（position/X1: 等随剪映导出常见）。
_TS_RE = re.compile(
    r"^\s*(\d{1,2}):(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{1,2}):(\d{1,2})[,.](\d{1,3})")


def _ts_to_seconds(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def _sec_to_frame(sec: float, fps: int) -> int:
    """秒 → 整帧，四舍五入 half-up（round() 为 banker's，0.5 帧会向偶数偏）。"""
    return int(sec * fps + 0.5)


def parse_srt(text: str, fps: int) -> list:
    """SRT 文本 → v1 subtitles[] 同构列表（纯函数）。

    容错：BOM / \\r\\n / 缺索引行（按时间戳行分块，不依赖序号）；时间戳行后直到
    空行或下一时间戳行为文本。块结构损坏（时间戳行格式非法）抛 ValueError（由
    调用方记入 validate 错误）。空文本 → []。
    """
    subs = []
    lines = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = _TS_RE.match(line)
        if not m:
            if line.strip():
                # 块外孤儿行：序号行（纯数字）忽略；其他非空行属结构损坏
                if not line.strip().isdigit():
                    raise ValueError(f"SRT 第 {i + 1} 行非空且非时间戳/序号行: {line.strip()[:40]!r}")
            i += 1
            continue
        start = _ts_to_seconds(m.group(1), m.group(2), m.group(3), m.group(4))
        end = _ts_to_seconds(m.group(5), m.group(6), m.group(7), m.group(8))
        i += 1
        text_lines = []
        while i < n and lines[i].strip() and not _TS_RE.match(lines[i]):
            text_lines.append(lines[i].strip())
            i += 1
        if not text_lines:
            raise ValueError(f"SRT 时间戳行（第 {i} 行附近）后无字幕文本")
        subs.append({"in": _sec_to_frame(start, fps),
                     "out": _sec_to_frame(end, fps),
                     "text": "\\N".join(text_lines)})
    return subs


def parse_subs_json(data, fps: int) -> list:
    """v1 subtitles[] 同构 JSON → 列表（帧值直用；fps 仅用于错误定位不参与换算）。

    接受裸列表或 {"subtitles": [...]} 包裹形。结构校验：每条须为含 int in/out 与
    str text 的 dict，损坏抛 ValueError（validate FAIL）。
    """
    if isinstance(data, dict) and "subtitles" in data:
        data = data["subtitles"]
    if not isinstance(data, list):
        raise ValueError("subtitles_src JSON 顶层须为列表（或 {\"subtitles\": [...]}）")
    out = []
    for idx, s in enumerate(data):
        if not isinstance(s, dict):
            raise ValueError(f"subtitles_src JSON 第 {idx} 条不是对象")
        for k in ("in", "out"):
            if not isinstance(s.get(k), int) or isinstance(s.get(k), bool):
                raise ValueError(f"subtitles_src JSON 第 {idx} 条缺少整数帧值 {k!r}")
        if not isinstance(s.get("text"), str):
            raise ValueError(f"subtitles_src JSON 第 {idx} 条缺少字符串 text")
        out.append({"in": s["in"], "out": s["out"], "text": s["text"]})
    return out


def resolve_subtitles_src(c) -> None:
    """timeline 加载点解析（infra.Ctx 构造期调用，先于 receipt_fingerprint）。

    结果直接反映在 c.tl 与 c.subtitles_errors（list[str]）：
      - subtitles_src 非字符串/空串 → 忽略（缺省语义；类型错误由 validate 的
        未知键扫描兜不住，此处非空非字符串记错误）；
      - subtitles_src 与 subtitles[] 都非空 → 记互斥错误（validate FAIL），不解析；
      - 文件缺失/后缀不支持/解析失败 → 记错误（validate FAIL），
        tl["subtitles"] 置 []（防下游 KeyError 先于 validate 崩）；
      - 解析成功 → tl["subtitles"] = 解析结果（后续各阶段拿到的是帧值列表）。
    """
    c.subtitles_errors = []
    src = c.tl.get("subtitles_src")
    if src is None or src == "":
        return   # 未声明（缺省）或空串（等价未声明）
    if not isinstance(src, str):
        c.subtitles_errors.append(f"subtitles_src 必须为字符串（相对 timeline.json 的路径），"
                                  f"实为 {type(src).__name__}")
        return
    if c.tl.get("subtitles"):
        c.subtitles_errors.append(
            "subtitles_src 与根级 subtitles[] 互斥——两者都非空（subtitles_src 声明了"
            "外部字幕文件，subtitles[] 又内联了字幕）；请只保留其一（外部来源用 "
            "subtitles_src，手工微调后的内联字幕直接写 subtitles[]）")
        return
    path = Path(c.root) / src
    if not path.is_file():
        c.subtitles_errors.append(f"subtitles_src 文件不存在: {src}")
        c.tl["subtitles"] = []
        return
    try:
        if path.suffix.lower() == ".srt":
            parsed = parse_srt(path.read_text(encoding="utf-8"), c.fps)
        elif path.suffix.lower() == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as e:
                raise ValueError(f"JSON 解析失败: {e}") from None
            parsed = parse_subs_json(data, c.fps)
        else:
            raise ValueError(f"不支持的字幕格式 {path.suffix!r}（支持 .srt / .json）")
    except (OSError, ValueError) as e:
        c.subtitles_errors.append(f"subtitles_src 解析失败（{src}）: {e}")
        c.tl["subtitles"] = []
        return
    c.tl["subtitles"] = parsed
