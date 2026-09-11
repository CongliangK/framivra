#!/usr/bin/env python3
"""segline —— 字级时间戳 → 断句聚合 → 30fps subtitles[]（锚定式）

用途：把 sidecar words[]（字级 {w,s,e} 秒、含标点零宽锚，§3.4 契约）经文本规范化
（textnorm）后断句聚合成帧级字幕行，供 tl_merge（timeline.json subtitles[]）与
srt_out（独立 SRT）消费。

用法（平铺 import，vtx 同款手法）：
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import segline
    subs, summary = segline.build_subtitles(words, fps=30, duration_frames=20763)

机制（方案 §3.5，锚定式——行时间来自字级时间戳，非 make_subs 的窗口分配式）：
  1 字流锚定：规范化只做删除/替换，在 (字, s, e) 序列原位执行（textnorm.normalize_stream）
  2 断句：句末标点 [。？！…] 切句；句内超 limit 用 make_subs 同款贪心
    （cut = max(rfind(句中标点, 0, limit)) + 1）；无句中标点先试 N4 软切候选，再硬切并调
    latin_safe_cut（回退到拉丁/数字词边界，防「Qwen3-ASR」被切成孤字行）
  3 行时间窗：[start, end] = 首字 s … 末字 e（含行尾标点，零宽锚不虚增时长）
  4 秒→帧（左闭右开 [in, out)）：in = floor(start*fps)，out = ceil(end*fps)；
    最短 min_dur 帧（默认 18 = audit 过短线阈值 0.6s，stages.py d < 0.6），不足扩 out
  5 相邻去重叠（自洽下限）：out > 下一行 in 时 out = max(in + min_dur, next.in - gap)
    （gap 默认 2 帧）——去重叠不得把本行压到 in+min_dur 以下；受该下限约束仍冲突则
    保留重叠并告警（audit 对重叠只 warn，不人为截文本）
  6 速率约束（与 audit 同口径，len(text) 含标点）：len/((out-in)/fps) ≤ rate_cap（默认 9）
    ——超限在本行与下一行首字间隙内扩 out（有隙扩隙、无隙告警接受）
  7 越界夹取（保序丢弃）：duration_frames 已知时 in/out 夹入 [0, duration]；
    夹取后 in >= out（整行落在时间轴外）则丢弃该行并告警
  8 输出按 in 升序 + 审计摘要（每行速率/宽度分布、告警清单）

来源：docs/plans/2026-09-11-三大模块整合方案.md v2.1 §3.4/§3.5；
voice-ime make_subs.py split_line/speech_chars/latin_safe_cut（内化改造：
时间从「窗口分配」改为「字级时间戳锚定」）。
"""
import math
import re

import textnorm

BREAK_HARD = "。？！…"          # 句末标点（切句）
BREAK_SOFT = "，、；：——»」』）)]"   # 句中标点（贪心回退切点，make_subs 同款）
_SENT_RE = re.compile(r"[^。？！…]*[。？！…]?")
# 拉丁/数字词内字符（含连字符与点：方案 §3.5 例举「Qwen3-ASR」不得从中间切开，
# make_subs 原集 [A-Za-z0-9.] 不含 '-'，会导致 "Qwen3-"/"ASR" 分行——本内化版补上）
_LATIN_RE = re.compile(r"[A-Za-z0-9.\-]")


def speech_chars(s):
    """字幕分配口径：CJK + 拉丁字母/数字逐个计，标点/空格不计（make_subs 同款）。"""
    return sum(1 for c in s if "\u4e00" <= c <= "\u9fff" or c.isalnum())


def latin_safe_cut(s, cut):
    """硬切位置落在拉丁/数字词内部时回退到词首（make_subs 同款内化）。"""
    if cut < len(s) and _LATIN_RE.match(s[cut - 1]) and _LATIN_RE.match(s[cut]):
        j = cut - 1
        while j > 0 and _LATIN_RE.match(s[j - 1]):
            j -= 1
        return max(j, 1)
    return cut


def _pick_soft_cut(sent, limit, soft_cands, base):
    """N4 软切候选优先：候选（整条转写绝对索引）换算句内后落在 [limit-8, limit]
    窗口内取最近者（候选同样受行宽 limit 硬约束，不得为软切超宽）；无合适候选
    返回 0（交由 latin_safe_cut 硬切兜底）。"""
    if not soft_cands:
        return 0
    best, best_d = 0, None
    for c in soft_cands:
        local = c - base
        if not (1 <= local <= limit):
            continue
        d = abs(local - limit)
        if d > textnorm._SOFT_SPLIT_SEARCH:
            continue
        if best_d is None or d < best_d:
            best, best_d = local, d
    return best


def split_line(sent, limit=18, soft_cands=None, base=0):
    """把一句切成 ≤limit 字（含标点）的行（make_subs 同款贪心 + N4 软切 + 拉丁安全）。

    soft_cands: N4 候选切点（整条转写上的绝对字符索引列表或 None）；base: 本句在
    整条转写中的起始索引。纯切分、不改字符 → 行文本拼接恒等于原句。
    """
    out = []
    offset = 0                       # 句内已切掉的前缀长度（候选索引同步平移）
    head_punct = textnorm._LINE_HEAD_PUNCT
    while len(sent) > limit:
        cut = max(sent.rfind(p, 0, limit) for p in BREAK_SOFT)
        cut = cut + 1 if cut > 0 else 0
        if cut == 0:
            cand = _pick_soft_cut(sent, limit, soft_cands, base + offset)
            cut = cand if cand > 0 else latin_safe_cut(sent, limit)
            # 防孤标点行：硬切点若落在标点前（剩余以标点开头），回退一位让标点随
            # 其实字成行（N2 已折叠重复标点 → 标点前必是实字，行首无标点硬断言不破）
            if 0 < cut < len(sent) and sent[cut] in head_punct:
                cut = max(1, cut - 1)
        out.append(sent[:cut])
        sent = sent[cut:]
        offset += cut
    if sent:
        out.append(sent)
    return [x for x in out if x.strip()]


def build_subtitles(words, fps=30, limit=18, rate_cap=9.0, min_dur=18, gap=2,
                    duration_frames=None, offset_frames=0, remove_fillers=True):
    """words[]（§3.4 契约）→ (subtitles[], summary)。

    words: [{"w": 字, "s": 秒, "e": 秒}, ...]（含标点零宽锚；拉丁连续串已拆单字符）
    返回 subs: [{"in": 帧整数, "out": 帧整数, "text": 行文本}, ...] 按 in 升序；
    summary: {"n_lines", "n_chars", "max_rate", "max_width", "rates", "warnings"}。
    """
    warnings = []
    tokens = [(w["w"], float(w["s"]), float(w["e"])) for w in words]
    tokens = textnorm.normalize_stream(tokens, remove_fillers=remove_fillers)
    text = "".join(t[0] for t in tokens)
    if not text.strip():
        return [], {"n_lines": 0, "n_chars": 0, "max_rate": 0.0, "max_width": 0,
                    "rates": [], "warnings": warnings}

    # 步骤 2：切句 → 切行（N4 候选在整条 text 上计算，与 textnorm 口径一致）
    soft_cands = textnorm.soft_split_candidates(text)
    lines = []          # (行文本, 行首 token 索引, 行尾 token 索引)
    pos = 0             # text/tokens 同步游标（split_line 纯切分，长度恒等）
    for sent in _SENT_RE.findall(text):
        if not sent:
            continue
        for piece in split_line(sent, limit=limit, soft_cands=soft_cands, base=pos):
            if piece.strip():
                lines.append((piece, pos, pos + len(piece)))
            pos += len(piece)
    if not lines:
        return [], {"n_lines": 0, "n_chars": 0, "max_rate": 0.0, "max_width": 0,
                    "rates": [], "warnings": warnings}

    subs = []
    for piece, i0, i1 in lines:
        # 步骤 3 + N7：行时间窗 = 首字 s … 末字 e；行尾句中标点截去（零宽锚，不影响时间）
        disp = textnorm.rstrip_mid_punct(piece)
        start = tokens[i0][1]
        end = tokens[i1 - 1][2]
        # 步骤 4：秒→帧 + 最短时长下限 + 整体偏移
        fin = math.floor(start * fps) + offset_frames
        fout = math.ceil(end * fps) + offset_frames
        if fout < fin + min_dur:
            fout = fin + min_dur
        subs.append({"in": fin, "out": fout, "text": disp})

    # 步骤 5：相邻去重叠（自洽下限：不破 in+min_dur；极冲突保留重叠并告警）
    for i in range(len(subs) - 1):
        cur, nxt = subs[i], subs[i + 1]
        if cur["out"] > nxt["in"]:
            new_out = max(cur["in"] + min_dur, nxt["in"] - gap)
            if new_out > nxt["in"]:            # 受 min_dur 下限约束仍冲突 → 保留重叠
                warnings.append("第 %d 行与下一行极冲突，保留重叠（in=%d, 下一行 in=%d）"
                                % (i + 1, cur["in"], nxt["in"]))
            else:
                cur["out"] = new_out

    # 步骤 6：速率约束（len(text) 含标点，与 audit 同口径）
    rates = []
    for i, s in enumerate(subs):
        dur_s = (s["out"] - s["in"]) / fps
        rate = len(s["text"]) / dur_s if dur_s > 0 else float("inf")
        rates.append(round(rate, 3))
        if rate > rate_cap:
            need = s["in"] + math.ceil(len(s["text"]) * fps / rate_cap)
            if need > s["out"]:
                limit_out = None
                if i + 1 < len(subs):
                    limit_out = subs[i + 1]["in"] - gap        # 扩隙上限：不制造新重叠
                elif duration_frames is not None:
                    limit_out = duration_frames
                target = need if limit_out is None else min(need, limit_out)
                if target > s["out"]:
                    s["out"] = target
                    dur_s = (s["out"] - s["in"]) / fps
                    rate = len(s["text"]) / dur_s if dur_s > 0 else float("inf")
                    rates[-1] = round(rate, 3)
                if rate > rate_cap:
                    warnings.append("第 %d 行阅读速度 %.1f 字/s 超 %s 上限（间隙不足，告警接受）"
                                    % (i + 1, rate, rate_cap))

    # 步骤 7：越界夹取（保序丢弃）
    if duration_frames is not None:
        kept = []
        for i, s in enumerate(subs):
            s["in"] = max(0, s["in"])
            s["out"] = min(s["out"], duration_frames)
            if s["in"] >= s["out"]:
                warnings.append("第 %d 行整行落在时间轴外（in=%d, out=%d, duration=%d），丢弃"
                                % (i + 1, s["in"], s["out"], duration_frames))
                continue
            kept.append(s)
        subs = kept

    # 步骤 8：按 in 升序 + 摘要
    subs.sort(key=lambda s: s["in"])
    summary = {
        "n_lines": len(subs),
        "n_chars": sum(len(s["text"]) for s in subs),
        "max_rate": max(rates) if rates else 0.0,
        "max_width": max((len(s["text"]) for s in subs), default=0),
        "rates": rates,
        "warnings": warnings,
    }
    return subs, summary


if __name__ == "__main__":
    import argparse
    import json
    import sys
    ap = argparse.ArgumentParser(description="words[] → subtitles[]（stdin json 入，stdout json 出）")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--limit", type=int, default=18)
    ap.add_argument("--rate-cap", type=float, default=9.0)
    ap.add_argument("--min-dur", type=int, default=18, help="最短时长（帧）")
    ap.add_argument("--gap", type=int, default=2)
    ap.add_argument("--duration", type=int, default=None, help="timeline duration_frames")
    args = ap.parse_args()
    words = json.load(sys.stdin)
    subs, summary = build_subtitles(words, fps=args.fps, limit=args.limit,
                                    rate_cap=args.rate_cap, min_dur=args.min_dur,
                                    gap=args.gap, duration_frames=args.duration)
    json.dump({"subtitles": subs, "summary": summary}, sys.stdout, ensure_ascii=False, indent=1)
    print()
