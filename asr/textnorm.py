#!/usr/bin/env python3
"""textnorm —— ASR 转写文本规范化（字幕产线第一步）

用途：对 sidecar 转写文本（纯文本或带时间锚的字级流）执行确定性规范化。
只做「删除/替换」两类操作、绝不增删发音内容；在 (字, s, e) 字级流上原位执行
（口水词删除即删其字元与时间；标点替换保留零宽时间锚 = 前实字 {s, e}），
保证断句（segline.py）输出的任意行可回溯首末字时间。

用法（平铺 import，vtx 同款手法）：
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import textnorm
    textnorm.normalize_text("嗯，今天天气，。")          # → "今天天气。"
    tokens = [("嗯", 0.0, 0.2), ("，", 0.2, 0.2), ...]  # (字, s, e) 秒
    textnorm.normalize_stream(tokens)                    # 同规则作用于字级流

机制（方案 §3.6 规则表；内化自 ibus-voice-ime text_postprocess.py 确定性子集，
非 import）：
  N1 行首标点删除：(^|\\n)[，,、。．.!?！？；;：:…]+ → 删除（IME 仅删 [，,、]，
     本规则按方案增强为全标点集——断句输出的每一行首绝不出标点）
  N2 重复标点折叠：[，,、。.!?！？；;：:]{2,} → 取最强（强度序 ？>！>。>；>：>，
     「！！！」→「！」、「，。」→「。」）
  N3 空格清理：CJK 标点前后空白删除；CJK-CJK 间空白删除；CJK↔拉丁边界空格保留
  N4 长无标点软切标记：>24 字无标点段记录切点候选（只记录，不改文本，供
     segline 硬切前参考——soft_split_candidates()）
  N5 口水词清除（默认开，remove_fillers=False 关）：
     语气词组 嗯/呃/啊/额/哦/唔+（词内连续 + 边界感知句间，两侧非 ASCII 字词）；
     边界感知 discourse 组 那个/这个/就是、对吧/怎么说呢/然后呢 等（仅当两侧为
     标点/空白/边界时删——「就是这个方案」中「就是」因两侧无界不删）
  N6 繁简统一（默认关，--t2s 由 asr_cli 对聚合后显示文本调用）：ctypes 直调
     系统 libopencc 软依赖（chinese_script.py 同手法），缺库返回原文本并告警
  N7 行尾句中标点截去（rstrip_mid_punct，由 segline 逐行调用；句末类 。？！… 保留）

来源：docs/plans/2026-09-11-三大模块整合方案.md v2.1 §3.5/§3.6；
ibus-voice-ime text_postprocess.py（cleanup_punctuation / _FILLER_RE 族 /
_split_long_unpunctuated_runs）、chinese_script.py（ctypes opencc）。
"""
import ctypes
import ctypes.util
import os
import re
from pathlib import Path

# ---------------------------------------------------------------- 常量（规则字符集）

_SENT_END = "。？！…"            # 句末标点（断句切句用，segline 消费）
_MID_PUNCT = "，、；：,;:"        # N7：句中类标点，行尾截去
_CJK_PUNCT = "，。！？；：、"     # N3：CJK 标点（周边空白删除）

# N1：行首可删标点全集（方案 §3.6 原文照录，含全角句点 ． 与省略号 …）
_LINE_HEAD_PUNCT = "，,、。．.!?！？；;：:…"

# N2：重复标点折叠字符集与强度序（？>！>。>；>：>，）
_DUP_PUNCT_RUN_RE = re.compile("[" + re.escape("，,、。.!?！？；;：:") + "]{2,}")
_PUNCT_STRENGTH = ("？", "！", "。", "；", "：", "，")

# N3：空格清理
_SPACE_AROUND_CJK_PUNCT_RE = re.compile(r"[ \t\r\f\v]*([，。！？；：、])[ \t\r\f\v]*")
_CJK_SPACE_CJK_RE = re.compile(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])")
# 注：CJK↔拉丁边界空格按方案 N3 保留（与 IME 原实现不同，方案为权威规格），
#     「配合 opencode」「opencode 编辑」中的空格不删。

# N5：口水词（方案 §3.6 N5 分组）
#   语气词组：词内连续（嗯嗯→删）+ 句间（两侧非 ASCII 字母数字即删）
_INLINE_FILLER_RE = re.compile(r"(?<![A-Za-z0-9_])(?:嗯+|呃+|额+|啊+|哦+|唔+)(?![A-Za-z0-9_])")
#   边界感知 discourse 组：仅两侧为标点/空白/边界时删（正文同形词不伤）
_FILLER_BOUNDARY = r"^|[\s，。！？；：、,.!?;:]"
_FILLER_TAIL = r"$|[\s，。！？；：、,.!?;:]"
_DISCOURSE_WORDS = ("那个", "这个", "就是", "对吧", "怎么说呢", "然后呢",
                    "首先呢", "其次呢", "最后呢", "除此以外呢", "那么呢")
_DISCOURSE_FILLER_RE = re.compile(
    "(" + _FILLER_BOUNDARY + ")(?:" + "|".join(_DISCOURSE_WORDS) + ")(?=" + _FILLER_TAIL + ")")

# N1
_LINE_HEAD_PUNCT_RE = re.compile(r"(^|\n)[" + re.escape(_LINE_HEAD_PUNCT) + "]+")

# N4：长无标点段（>24 字）软切参数（IME _split_long_unpunctuated_runs 同源，24 为默认）
_CLAUSE_SPLIT_RE = re.compile(r"([，。！？；：、\n])")
_SOFT_SPLIT_AFTER_CHARS = "了呢吧吗么呀嘛啊哦"   # 助词后优先断（IME 同款）
_SOFT_SPLIT_SEARCH = 8                            # 候选切点搜索半窗


def _fold_dupe(match):
    """N2 折叠：重复标点串取最强（？>！>。>；>：>，）。"""
    s = match.group(0)
    folded = (s.replace("?", "？").replace("!", "！").replace(".", "。")
               .replace(";", "；").replace(":", "：").replace(",", "，"))
    for strong in _PUNCT_STRENGTH:
        if strong in folded:
            return strong
    return "，"


def _collect_edits(text, remove_fillers):
    """在 text 上收集一轮规范化编辑，返回 (edits, changed)。

    edits: [(start, end, repl_str), ...] 字符区间替换（repl="" 即删除）；
    区间互不重叠（口水词删除区与标点折叠区天然不重叠；同规则内 finditer 不重叠）。
    """
    edits = []

    def collect(pattern, repl_of):
        for m in pattern.finditer(text):
            r = repl_of(m)
            if m.group(0) != r:
                edits.append((m.start(), m.end(), r))

    if remove_fillers:
        collect(_INLINE_FILLER_RE, lambda m: "")
        collect(_DISCOURSE_FILLER_RE, lambda m: m.group(1))   # 保留边界字符
    collect(_SPACE_AROUND_CJK_PUNCT_RE, lambda m: m.group(1))
    collect(_CJK_SPACE_CJK_RE, lambda m: "")
    collect(_DUP_PUNCT_RUN_RE, _fold_dupe)
    collect(_LINE_HEAD_PUNCT_RE, lambda m: m.group(1))
    # 丢弃互相重叠的编辑（保守：保留先出现者）
    edits.sort(key=lambda e: e[0])
    kept, last_end = [], -1
    for st, en, r in edits:
        if st >= last_end:
            kept.append((st, en, r))
            last_end = en
    return kept, bool(kept)


def _apply_edits(s, edits):
    """把字符区间编辑应用到纯文本（从后往前，索引不失效）。"""
    for st, en, r in sorted(edits, reverse=True):
        s = s[:st] + r + s[en:]
    return s


def _apply_edits_stream(tokens, edits):
    """把编辑应用到 (字, s, e) 字级流：1 字符 = 1 token。

    删除：删字元与时间；替换（N2 折叠标点）：新区间 token 的锚 = 原区间首
    token 的锚（原标点本为零宽锚 = 前实字 {s, e}，折叠结果继承同一锚）。
    """
    for st, en, r in sorted(edits, reverse=True):
        seg = tokens[st:en]
        if seg:
            s0, e0 = seg[0][1], seg[0][2]
        else:
            s0 = e0 = 0.0
        tokens[st:en] = [(ch, s0, e0) for ch in r]
    return tokens


def normalize_text(text, remove_fillers=True):
    """纯文本规范化（N1/N2/N3/N5；N4 不改文本、N6/N7 由调用方按需后置）。"""
    if not text:
        return text
    text = text.replace("\u3000", " ")
    while True:
        edits, _ = _collect_edits(text, remove_fillers)
        if not edits:
            return text
        text = _apply_edits(text, edits)


def normalize_stream(tokens, remove_fillers=True):
    """字级流规范化（§3.5 步骤 1：删除/替换原位执行，保时间锚可回溯）。

    tokens: [(字, s, e), ...]（1 字符 1 元组，秒）；返回同型新列表。
    """
    tokens = list(tokens)
    if not tokens:
        return tokens
    if tokens[0][0] == "\u3000":   # 全角空格与纯文本版同口径（仅首字符防御，正则内空格规则同理）
        tokens[0] = (" ", tokens[0][1], tokens[0][2])
    while True:
        text = "".join(t[0] for t in tokens)
        edits, _ = _collect_edits(text, remove_fillers)
        if not edits:
            return tokens
        tokens = _apply_edits_stream(tokens, edits)


def rstrip_mid_punct(line):
    """N7：截去行尾句中类标点（，、；：,;:）；句末类 。？！… 原样保留。"""
    return line.rstrip(_MID_PUNCT)


def _find_soft_split(seg, target):
    """在 seg（无标点长段）target 附近找软切点（IME _find_soft_split 内化简化）。

    优先助词/语气词后（了呢吧吗么呀嘛啊哦），无则返回 0（不强行从词中间切）。
    """
    if len(seg) <= 2:
        return len(seg)
    start = max(1, target - _SOFT_SPLIT_SEARCH)
    end = min(len(seg) - 1, target + _SOFT_SPLIT_SEARCH)
    for i in range(end, start - 1, -1):
        if seg[i - 1] in _SOFT_SPLIT_AFTER_CHARS and not re.match(r"[A-Za-z0-9_]", seg[i]):
            return i
    return 0


def soft_split_candidates(text, max_run=24):
    """N4：>max_run 字无标点段的软切点候选（绝对字符索引，只记录不改文本）。

    供 segline 硬切前参考；段内无自然边界（助词/话语词）则无候选，硬切兜底。
    """
    cands = []
    pos = 0
    for part in _CLAUSE_SPLIT_RE.split(text):
        if not part or _CLAUSE_SPLIT_RE.fullmatch(part):
            pos += len(part)
            continue
        if len(part) > max_run:
            cut = _find_soft_split(part, max_run)
            if 0 < cut < len(part):
                cands.append(pos + cut)
        pos += len(part)
    return cands


# ---------------------------------------------------------------- N6 繁简（ctypes libopencc 软依赖）

_OPENCC_LIB = None
_OPENCC_HANDLE = None
_OPENCC_ERROR = ctypes.c_void_p(-1).value
_OPENCC_WARNED = False


def _opencc_lib():
    global _OPENCC_LIB
    if _OPENCC_LIB is not None:
        return _OPENCC_LIB
    name = ctypes.util.find_library("opencc") or "libopencc.so.1.1"
    lib = ctypes.CDLL(name)
    lib.opencc_open.argtypes = [ctypes.c_char_p]
    lib.opencc_open.restype = ctypes.c_void_p
    lib.opencc_close.argtypes = [ctypes.c_void_p]
    lib.opencc_close.restype = ctypes.c_int
    lib.opencc_convert_utf8.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    lib.opencc_convert_utf8.restype = ctypes.c_void_p
    lib.opencc_convert_utf8_free.argtypes = [ctypes.c_void_p]
    lib.opencc_convert_utf8_free.restype = None
    _OPENCC_LIB = lib
    return lib


def t2s_available():
    """系统 libopencc + t2s 配置是否可用（单测/探测用，不做转换）。"""
    cfg = os.environ.get("VT_ASR_OPENCC_T2S_CONFIG", "/usr/share/opencc/t2s.json")
    if not Path(cfg).expanduser().exists():
        return False
    try:
        _opencc_lib()
        return True
    except Exception:
        return False


def t2s_convert(text):
    """N6 繁→简（仅作用于聚合完成后的显示文本，绝不动锚定字流）。

    返回 (转换后文本, 是否成功)。libopencc 缺失 → (原文, False) 并打印一次性告警
    （Fedora 可 sudo dnf install opencc）；不抛异常、不断产线。
    """
    global _OPENCC_HANDLE, _OPENCC_WARNED
    if not text or not re.search(r"[\u3400-\u9fff\uf900-\ufaff]", text):
        return text, True
    cfg = os.environ.get("VT_ASR_OPENCC_T2S_CONFIG", "/usr/share/opencc/t2s.json")
    cfg = str(Path(cfg).expanduser())
    if not Path(cfg).exists():
        if not _OPENCC_WARNED:
            _OPENCC_WARNED = True
            print("⚠ OpenCC 配置缺失（%s），繁简转换跳过，原文直通；可 sudo dnf install opencc" % cfg)
        return text, False
    try:
        if _OPENCC_HANDLE is None:
            lib = _opencc_lib()
            h = lib.opencc_open(cfg.encode("utf-8"))
            if not h or h == _OPENCC_ERROR:
                return text, False
            _OPENCC_HANDLE = int(h)
        lib = _opencc_lib()
        raw = text.encode("utf-8")
        out = lib.opencc_convert_utf8(ctypes.c_void_p(_OPENCC_HANDLE), raw, len(raw))
        if not out or out == _OPENCC_ERROR:
            return text, False
        try:
            return ctypes.string_at(out).decode("utf-8"), True
        finally:
            lib.opencc_convert_utf8_free(out)
    except Exception as exc:
        if not _OPENCC_WARNED:
            _OPENCC_WARNED = True
            print("⚠ libopencc 不可用（%s），繁简转换跳过，原文直通；可 sudo dnf install opencc" % exc)
        return text, False


if __name__ == "__main__":
    import argparse
    import sys
    ap = argparse.ArgumentParser(description="ASR 文本规范化（N1-N7，纯文本模式；字级流由 segline 内部调用）")
    ap.add_argument("text", nargs="?", help="待规范化文本（缺省读 stdin）")
    ap.add_argument("--no-filler", action="store_true", help="关闭 N5 口水词清除")
    ap.add_argument("--t2s", action="store_true", help="附加 N6 繁→简（仅显示文本）")
    args = ap.parse_args()
    src = args.text if args.text is not None else sys.stdin.read()
    out = normalize_text(src, remove_fillers=not args.no_filler)
    if args.t2s:
        out, ok = t2s_convert(out)
        if not ok:
            print("⚠ 繁简转换未生效（软依赖）", file=sys.stderr)
    print(out)
