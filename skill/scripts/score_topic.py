#!/usr/bin/env python3
"""选题量化评分器：把调研的离散数据收敛为可比较的选题分数（0-100）。

用法：
  score_topic.py <research目录>            # 读目录下 supplement.json + bilibili_*.json + github_repos.json
  score_topic.py --rubric                  # 打印人工评分锚点表（评分前必读，保证跨选题一致性）

模型（v1，权重可用 supplement.json 的 weights_override 覆盖）：
  总分 = 0.30×需求强度 + 0.25×供给空白度 + 0.25×差异化契合 + 0.20×时机成熟度
  需求强度 = 0.5×B站信号 + 0.3×知乎信号 + 0.1×大众平台(抖音/小红书) + 0.1×GitHub需求代理   ← B站优先
  供给空白度 = 直接竞品数量锚点分 + 竞品新鲜度修正
  差异化契合(开源技术分享人设) = 0.45×叙事资产 + 0.35×壁垒 + 0.20×原理可讲性
  时机成熟度 = 0.5×技术底座成熟度 + 0.3×趋势方向 + 0.2×生态活跃度(GitHub stars 对数)

判读：≥80 优先做｜65-79 值得做｜50-64 谨慎/需改角度｜<50 放弃或改造
"""
import json, math, sys, time
from pathlib import Path

def rubric():
    print("""# 人工评分锚点表（0-100，只允许锚点值 ±5 内取值，每项必须附证据）

zhihu_demand 知乎/社区需求：
  0=无相关问题 25=零散提问无互动 50=多问题有回答但互动低 75=高赞问题存在且痛点原话反复出现 100=高赞问题+需求存续多年+原话与选题直接重合
mass_demand_douyin_xhs 大众平台需求（抖音/小红书）：
  0=无内容 25=少量低互动 50=工具类内容稳定出现 75=有爆款(万赞级)或大量同类内容 100=现象级话题多账号持续产出
trend 趋势方向：
  0=明确退潮 25=热度下滑 50=平稳 75=上升期 100=爆发期
tech_maturity 技术底座成熟度：
  0=无可用底座 25=实验室级 50=可用但粗糙 75=成熟底座(万星或官方维护) 100=多个成熟方案可选
narrative_assets 叙事资产（人设核心）：
  0=纯使用者视角 25=有深度使用可分享 50=能讲清原理 75=有自研/贡献+架构可讲 100=自研项目+真实数据+踩坑全链路
barrier 壁垒：
  0=纯信息搬运任何人可做 25=需调研整理 50=需要技能组合 75=需要实际开发 100=需要长期开发的项目
tech_depth_story 原理可讲性：
  0=无可讲层 25=1层(怎么用) 50=2层(怎么实现) 75=3层(为什么这样设计) 100=4层+(实现+设计+权衡+演化)""")

def load(p, default=None):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else default

def clamp(x, lo=0, hi=100): return max(lo, min(hi, x))

def score(research_dir: Path):
    sup = load(research_dir / "supplement.json")
    if not sup:
        sys.exit(f"缺 {research_dir}/supplement.json（模板见 skill assets/supplement.template.json，评分锚点运行 --rubric 查看）")
    m = sup["manual"]
    w = {"demand": .30, "blank": .25, "diff": .25, "timing": .20,
         "d_bili": .5, "d_zhihu": .3, "d_mass": .1, "d_gh": .1,
         "f_narr": .45, "f_bar": .35, "f_depth": .2,
         "t_mat": .5, "t_trend": .3, "t_act": .2}
    w.update(sup.get("weights_override", {}))

    # ---- 需求强度：B站信号来自 stats 实测 ----
    stats = load(research_dir / "bilibili_stats.json", [])
    comps = [s for s in stats if s.get("bvid") in sup.get("direct_competitor_bvids", [])]
    pool = comps or stats
    if pool:
        max_view = max(s["view"] for s in pool)
        best_fav = max(s["favorite"] / max(s["view"], 1) for s in pool) * 100
        best_cl = max(s["coin"] / max(s["like"], 1) for s in pool)
        bili = clamp(0.40 * min(100, math.log10(max(max_view, 1)) * 20) +
                     0.30 * min(100, best_fav / 7 * 100) +
                     0.30 * min(100, best_cl / 1.0 * 100))
        bili_detail = f"对标最高播放 {max_view}，最佳藏率 {best_fav:.1f}%，最佳投币赞比 {best_cl:.2f}"
    else:
        bili, bili_detail = 50.0, "无 stats 数据，取中性值 50"
    demand = (w["d_bili"] * bili + w["d_zhihu"] * m["zhihu_demand"]["score"] +
              w["d_mass"] * m["mass_demand_douyin_xhs"]["score"] + w["d_gh"] * gh_demand(research_dir))

    # ---- 供给空白度 ----
    n = len(sup.get("direct_competitor_bvids", []))
    blank = 95 if n == 0 else 75 if n == 1 else 60 if n <= 3 else 35 if n <= 8 else 15
    search = load(research_dir / "bilibili_search.json", [])
    newest = ""
    comp_ids = set(sup.get("direct_competitor_bvids", []))
    for r in search:
        if r.get("bvid") in comp_ids and r.get("pubdate", "") > newest:
            newest = r["pubdate"]
    if not newest:  # 竞品可能是外部发现的（不在搜索结果里），从 stats 兜底
        for s in stats:
            if s.get("bvid") in comp_ids and s.get("pubdate", "") > newest:
                newest = s["pubdate"]
    adj = 0
    if newest:
        age_mo = (time.time() - time.mktime(time.strptime(newest, "%Y-%m-%d"))) / 86400 / 30
        adj = 10 if age_mo >= 18 else (-15 if age_mo < 6 else 0)
        blank_adj_detail = f"最新直接竞品 {newest}（{age_mo:.0f} 个月前）修正 {adj:+d}"
    else:
        blank_adj_detail = "竞品时间未知，无修正"
    blank = clamp(blank + adj)

    # ---- 差异化契合（开源技术分享人设）----
    diff = (w["f_narr"] * m["narrative_assets"]["score"] +
            w["f_bar"] * m["barrier"]["score"] + w["f_depth"] * m["tech_depth_story"]["score"])

    # ---- 时机成熟度 ----
    repos = load(research_dir / "github_repos.json", [])
    max_stars = max((r["stars"] for r in repos), default=0)
    activity = clamp(math.log10(max_stars + 1) * 20)
    timing = w["t_mat"] * m["tech_maturity"]["score"] + w["t_trend"] * m["trend"]["score"] + w["t_act"] * activity

    total = w["demand"] * demand + w["blank"] * blank + w["diff"] * diff + w["timing"] * timing
    verdict = "优先做" if total >= 80 else "值得做" if total >= 65 else "谨慎/需改角度" if total >= 50 else "放弃或改造"
    out = {
        "topic": sup.get("topic", ""), "total": round(total, 1), "verdict": verdict,
        "dimensions": {"需求强度": round(demand, 1), "供给空白度": round(blank, 1),
                       "差异化契合": round(diff, 1), "时机成熟度": round(timing, 1)},
        "dimension_weights": {k: w[k] for k in ("demand", "blank", "diff", "timing")},
        "details": {"bili_signal": {"score": round(bili, 1), "detail": bili_detail},
                    "direct_competitors": n, "blank_adjust": blank_adj_detail,
                    "github_max_stars": max_stars,
                    "evidence": {k: v.get("evidence", []) for k, v in m.items()}},
        "generated": time.strftime("%F %T")}
    (research_dir / "topic_score.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n选题：「{out['topic']}」  总分 {out['total']} / 100  → {verdict}\n" +
          "\n".join(f"  {k:<20} {v:>5}  (权重 {out['dimension_weights'][kk]})" for kk, (k, v) in
                    zip(("demand", "blank", "diff", "timing"), out["dimensions"].items())) +
          f"\n  B站信号 {out['details']['bili_signal']['score']}｜{out['details']['bili_signal']['detail']}" +
          f"\n  直接竞品 {n} 个｜{blank_adj_detail}｜GitHub 最高 {max_stars}★" +
          f"\n明细 → {research_dir}/topic_score.json")

def gh_demand(research_dir: Path):
    """GitHub 需求代理：近似方案的存在与热度证明需求（竞品 stars 对数，上限封顶防大厂项目扭曲）"""
    repos = load(research_dir / "github_repos.json", [])
    if not repos: return 50.0
    near = [r for r in repos if r["stars"] > 100]
    if not near: return 30.0
    return clamp(min(100, math.log10(max(r["stars"] for r in near) + 1) * 22))

if __name__ == "__main__":
    if len(sys.argv) < 2: print(__doc__); sys.exit(1)
    if sys.argv[1] == "--rubric": rubric()
    else: score(Path(sys.argv[1]))
