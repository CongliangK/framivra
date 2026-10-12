#!/usr/bin/env python3
"""GitHub 仓库调研采集器：按关键词搜仓库，输出 star/活跃度/语言等结构化数据。

用法：
  github_repos.py "query1,query2" [-o out.json] [--top 10] [--sort stars]

数据字段：full_name, stars, forks, language, pushed_at, archived, description, url, topics
认证：无 token 可用（60 请求/小时）；设 GITHUB_TOKEN 环境变量可提升至 5000。
"""
import argparse, json, time, urllib.request, urllib.parse, urllib.error, os, sys, socket
from pathlib import Path

def gh(url, params=None):
    if params: url = url + "?" + urllib.parse.urlencode(params)
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "video-skill"}
    tok = os.environ.get("GITHUB_TOKEN")
    if tok: headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            status, body = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            d = json.loads(body)
            reason = d.get("message", "HTTP 错误") if isinstance(d, dict) else "HTTP 错误"
        except ValueError:
            reason = "非 JSON 响应"
        raise RuntimeError(f"GitHub 请求 [{req.full_url.split('?')[0]}] HTTP {e.code}：{reason}；"
                           "可能限流/网络拦截；请检查 GITHUB_TOKEN、换网络或稍后重试。") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        timeout = isinstance(e, (TimeoutError, socket.timeout)) or isinstance(
            getattr(e, "reason", None), (TimeoutError, socket.timeout)) or "timed out" in str(e).lower()
        reason = "请求超时（上限 15 秒）" if timeout else f"网络不可达：{e}"
        raise RuntimeError(f"GitHub 请求 [{req.full_url.split('?')[0]}] HTTP 未收到：{reason}；"
                           "请检查网络/HTTPS_PROXY 代理（如 http://127.0.0.1:7890）或稍后重试。") from e
    try:
        d = json.loads(body)
    except ValueError as e:
        raise RuntimeError(f"GitHub 请求 HTTP {status}：非 JSON 响应，可能被拦截或接口变更。") from e
    if not isinstance(d, dict) or not isinstance(d.get("items"), list):
        raise RuntimeError(f"GitHub 搜索 HTTP {status}：缺少 items 列表，响应异常，并非真无结果。")
    return d

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
    rows, failed = [], False
    for q in a.arg.split(","):
        try:
            rs = search_repos(q.strip(), a.top, a.sort)
            rows += rs
            print(f"[{q}] {len(rs)} 个仓库，top stars: {rs[0]['full_name']}({rs[0]['stars']})" if rs else
                  f"[{q}] 真无结果（接口正常返回 items=[]），可调整关键词")
        except Exception as e:
            failed = True
            print(f"[{q}] 失败: {e}", file=sys.stderr)
        time.sleep(2)   # 无 token 限流 10 次/分钟
    if failed and not rows: return 1
    out = a.out or f"github_repos_{int(time.time())}.json"
    Path(out).write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    print(f"✓ {len(rows)} 条 → {out}")
    if failed:
        print("部分关键词请求失败，输出仅含成功部分。", file=sys.stderr)
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
