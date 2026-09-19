"""cer.py —— CER 计算与讲稿对齐(R-20 修订二十第五件;修订八口径)。

真实轨可懂度轨(CER):参考文本 = 讲稿 06a-teleprompter.md(哈希在档,地板对
两链同参考消去,相对比较口径);假设文本 = apps/asr sidecar 转写产物。

解码确定性(修订八,钉死参数,runner 必遵):
  DECODE_PARAMS = {"temperature": 0, "batch_size": 1}(贪婪解码);
  首轮双跑一致性:DUAL_RUN_TOL_PP=0.2,同链同段双跑 CER 差 >0.2pp =
  解码非确定性 → 换参重钉并公告,禁静默续跑。
  容差来源句:#6 CER 非劣带宽 0.5pp 系预注册值,P1 冻结表按双跑离差实测复核,
  只许调严不许松。

文本归一(成文):全角→半角、casefold、删除全部空白与标点(中英混排口播;
标点不入字符错计)、阿拉伯数字保留。CER = (S+D+I)/N(Levenshtein 字符级);
插入率 INS = I/N(修订七.4 幻觉敏感联动:ins_median ≤ 基线+0.5pp)。
"""
import hashlib
import re
import unicodedata
from pathlib import Path

DECODE_PARAMS = {"temperature": 0, "batch_size": 1}  # 修订八钉参(在档)
DUAL_RUN_TOL_PP = 0.2   # 双跑一致性容差(pp)
CER_TOL_PP = 0.5        # #6 非劣带宽预注册值(pp;P1 冻结表复核)

_STRIP_RE = re.compile(r"[\s\W_]+", re.UNICODE)  # 空白+标点(含全角)全删


def normalize_text(text: str) -> str:
    """归一(成文):NFKC 全半角折叠 → casefold → 删空白与标点。"""
    t = unicodedata.normalize("NFKC", text)
    t = t.casefold()
    return _STRIP_RE.sub("", t)


def levenshtein_ops(ref: str, hyp: str):
    """字符级 Levenshtein 动态规划,回溯 (S, D, I)。"""
    n, m = len(ref), len(hyp)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1,        # D
                           dp[i][j - 1] + 1,        # I
                           dp[i - 1][j - 1] + cost)  # S/=
    # 回溯
    i, j = n, m
    S = D = I = 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + (0 if ref[i - 1] == hyp[j - 1] else 1):
            if ref[i - 1] != hyp[j - 1]:
                S += 1
            i, j = i - 1, j - 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            D += 1
            i -= 1
        else:
            I += 1
            j -= 1
    return S, D, I


def cer(ref_text: str, hyp_text: str) -> dict:
    """CER 计算返回 {cer, substitutions, deletions, insertions, n_ref,
    insertion_rate}。参考空串 → cer=None(临时未测 null 位,禁 0/1 凑数)。"""
    r = normalize_text(ref_text)
    h = normalize_text(hyp_text)
    if not r:
        return {"cer": None, "substitutions": None, "deletions": None,
                "insertions": None, "n_ref": 0, "insertion_rate": None}
    S, D, I = levenshtein_ops(r, h)
    n = len(r)
    return {"cer": round((S + D + I) / n, 6),
            "substitutions": S, "deletions": D, "insertions": I, "n_ref": n,
            "insertion_rate": round(I / n, 6)}


def load_reference(md_path) -> str:
    """讲稿参考文本提取(成文):06a-teleprompter.md 去行首 Markdown 装饰
    (#/＞/-/*/数字表符)与括注,逐行保留正文,拼接为单一参考流。"""
    lines = []
    for raw in Path(md_path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "---", "```", "|", ">")):
            continue
        line = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line)  # 列表符
        line = re.sub(r"\*+", "", line)                        # 强调符
        line = re.sub(r"[([【]([^])】]*)[])】]", "", line)     # 括注(舞台指示)
        lines.append(line)
    return "\n".join(lines)


def ref_sha256(md_path) -> str:
    """参考文本哈希(修订八:06a-teleprompter.md 哈希在档;文件字节级)。"""
    return hashlib.sha256(Path(md_path).read_bytes()).hexdigest()


def dual_run_consistent(cer_a: float, cer_b: float) -> dict:
    """双跑一致性检查(修订八):|ΔCER| ≤ 0.2pp。返回 {consistent, delta_pp}。"""
    if cer_a is None or cer_b is None:
        return {"consistent": False, "delta_pp": None}
    delta_pp = round(abs(cer_a - cer_b) * 100.0, 4)
    return {"consistent": bool(delta_pp <= DUAL_RUN_TOL_PP), "delta_pp": delta_pp}
