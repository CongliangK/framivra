#!/usr/bin/env python3
"""GitHub 仓库调研采集器：按关键词搜仓库，输出 star/活跃度/语言等结构化数据。

用法：
  github_repos.py "query1,query2" [-o out.json] [--top 10] [--sort stars]

数据字段：full_name, stars, forks, language, pushed_at, archived, description, url, topics
认证：无 token 可用（60 请求/小时）；设 GITHUB_TOKEN 环境变量可提升至 5000。
"""
import argparse, json, time, urllib.request, urllib.parse, os
from pathlib import Path

def gh(url, params=None):
    if params: url = url + "?" + urllib.parse.urlencode(params)
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "video-skill"}
    tok = os.environ.get("GITHUB_TOKEN")
    if tok: headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

def search_repos(q, top=10, sort="stars"):
    d = gh("https://api.github.com/search/repositories",
           {"q": q, "sort": sort, "order": "desc", "per_page": top})
    return [{"query": q, "full_name": it["full_name"], "stars": it["stargazers_count"],
             "forks": it["forks_count"], "language": it["language"],
             "pushed_at": it["pushed_at"][:10], "archived": it["archived"],
             "description": (it.get("description") or "")[:200],
             "url": it["html_url"], "topics": it.get("topics", [])}
            for it in d.get("items", [])]

def main():
    ap = argparse.ArgumentParser(description="GitHub 仓库调研采集器")
    ap.add_argument("arg", help="逗号分隔的搜索词")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--sort", default="stars", choices=["stars", "updated", "forks"])
    a = ap.parse_args()
    rows = []
    for q in a.arg.split(","):
        try:
            rs = search_repos(q.strip(), a.top, a.sort)
            rows += rs
            print(f"[{q}] {len(rs)} 个仓库，top stars: {rs[0]['full_name']}({rs[0]['stars']})" if rs else f"[{q}] 0 结果")
        except Exception as e:
            print(f"[{q}] 失败: {e}")
        time.sleep(2)   # 无 token 限流 10 次/分钟
    out = a.out or f"github_repos_{int(time.time())}.json"
    Path(out).write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    print(f"✓ {len(rows)} 条 → {out}")

if __name__ == "__main__":
    main()
