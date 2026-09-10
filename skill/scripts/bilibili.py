#!/usr/bin/env python3
"""B 站调研采集器（通用可复用）：搜索 / 视频统计 / 热评，仅公开接口，无需登录。

用法：
  bilibili.py search "关键词1,关键词2" [-o out.json] [--pages 1] [--retry 3]
  bilibili.py stats "BV1xxx,BV1yyy" [-o out.json]
  bilibili.py comments "BV1xxx" [-o out.json] [-n 30]

特性：
  - WBI 签名自动处理；buvid 指纹持久化到 ~/.cache/bili-research/（复用热指纹，规避冷启动空结果风控）
  - 搜索空结果自动重试；search 输出自动与已存在文件合并去重
  - 输出为 UTF-8 JSON 数组，供后续分析直接消费

派生指标建议（分析时自行计算）：
  藏率 = favorite/view；投币赞比 = coin/like（>1 为极强"值得"信号）；
  弹幕密度 = danmaku/view（>0.02 娱乐向，<0.005 工具向）
"""
import argparse, json, time, hashlib, urllib.request, urllib.parse, re, uuid, sys
from pathlib import Path

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
STATE = Path.home() / ".cache" / "bili-research"
MIXIN = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,
         38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]
COOKIE = ""

def http(url, params=None):
    if params: url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.bilibili.com",
                                               "Cookie": COOKIE})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode())
        except Exception:
            if attempt == 2: raise
            time.sleep(3)

def load_or_init_cookie():
    global COOKIE
    STATE.mkdir(parents=True, exist_ok=True)
    f = STATE / "cookie"
    if f.exists():
        COOKIE = f.read_text().strip()
        if COOKIE: return
    b3 = str(uuid.uuid4()).upper() + "infoc"
    b4 = str(uuid.uuid4()).upper() + "%infoc"
    COOKIE = f"buvid3={b3}; buvid4={b4}"
    f.write_text(COOKIE)

def mixin_key():
    d = http("https://api.bilibili.com/x/web-interface/nav")
    raw = (d["data"]["wbi_img"]["img_url"].split("/")[-1].split(".")[0] +
           d["data"]["wbi_img"]["sub_url"].split("/")[-1].split(".")[0])
    return "".join(raw[i] for i in MIXIN)[:32]

def wbi_sign(params, key):
    params = dict(params); params["wts"] = int(time.time())
    params = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in params.items()}
    qs = urllib.parse.urlencode(sorted(params.items()))
    params["w_rid"] = hashlib.md5((qs + key).encode()).hexdigest()
    return params

def strip_em(s): return re.sub(r"<[^>]+>", "", s)

def search(keyword, key, page=1):
    p = wbi_sign({"search_type": "video", "keyword": keyword, "page": page,
                  "page_size": 30, "order": "", "tids": 0}, key)
    d = http("https://api.bilibili.com/x/web-interface/wbi/search/type", p)
    if d.get("code") != 0: return []
    return [{"keyword": keyword, "bvid": r.get("bvid", ""), "aid": r["aid"],
             "title": strip_em(r["title"]), "author": r["author"], "mid": r["mid"],
             "play": r.get("play", -1), "danmaku": r.get("video_review", -1),
             "favorites": r.get("favorites", -1), "review": r.get("review", -1),
             "pubdate": time.strftime("%Y-%m-%d", time.localtime(r["pubdate"])),
             "duration": r.get("duration"), "desc": (r.get("description") or "")[:150]}
            for r in (d.get("data", {}).get("result") or [])]

def view_stats(bvid):
    d = http("https://api.bilibili.com/x/web-interface/view", {"bvid": bvid})
    if d.get("code") != 0: return None
    v, s = d["data"], d["data"]["stat"]
    return {"bvid": bvid, "title": v["title"], "owner": v["owner"]["name"],
            "view": s["view"], "danmaku": s["danmaku"], "like": s["like"], "coin": s["coin"],
            "favorite": s["favorite"], "reply": s["reply"], "share": s["share"],
            "duration": v["duration"],
            "pubdate": time.strftime("%Y-%m-%d", time.localtime(v["pubdate"])),
            "desc": (v.get("desc") or "")[:300]}

def comments(bvid, n=30):
    d = http("https://api.bilibili.com/x/web-interface/view", {"bvid": bvid})
    if d.get("code") != 0: return []
    aid = d["data"]["aid"]
    r = http("https://api.bilibili.com/x/v2/reply/main", {"type": 1, "oid": aid, "mode": 3, "next": 0})
    reps = (r.get("data") or {}).get("replies") or []
    return [{"text": c["content"]["message"][:250], "like": c["like"],
             "uname": c["member"]["uname"]} for c in reps[:n]]

def save(out, rows):
    p = Path(out)
    if p.suffix == "" : p = p.with_suffix(".json")
    p.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    print(f"✓ {len(rows)} 条 → {p}")

def main():
    ap = argparse.ArgumentParser(description="B 站调研采集器")
    ap.add_argument("cmd", choices=["search", "stats", "comments"])
    ap.add_argument("arg", help="search: 逗号分隔关键词；stats/comments: 逗号分隔 BV 号")
    ap.add_argument("-o", "--out", default=None, help="输出 JSON 路径（默认打印）")
    ap.add_argument("--pages", type=int, default=1, help="搜索页数（默认 1）")
    ap.add_argument("--retry", type=int, default=3, help="空结果重试次数")
    ap.add_argument("-n", type=int, default=30, help="评论条数上限")
    a = ap.parse_args()
    load_or_init_cookie()
    if a.cmd == "search":
        key = mixin_key()
        seen, rows = {}, []
        if a.out and Path(a.out).exists():          # 与已有文件合并去重
            for r in json.loads(Path(a.out).read_text()):
                if r.get("bvid"): seen[r["bvid"]] = r; rows.append(r)
        for kw in a.arg.split(","):
            for pg in range(1, a.pages + 1):
                for attempt in range(a.retry):
                    res = search(kw, key, pg)
                    if res: break
                    time.sleep(4 + attempt * 3)
                for r in res:
                    if r["bvid"] and r["bvid"] not in seen:
                        seen[r["bvid"]] = r; rows.append(r)
                print(f"[{kw}] 第{pg}页 {len(res)} 条")
                time.sleep(3)
        out = a.out or f"bili_search_{int(time.time())}.json"
        save(out, rows)
    elif a.cmd == "stats":
        rows = []
        for bv in a.arg.split(","):
            st = view_stats(bv.strip())
            if st: rows.append(st); print(f"[{bv}] {st['title'][:30]} view={st['view']}")
            time.sleep(2)
        out = a.out or f"bili_stats_{int(time.time())}.json"
        save(out, rows)
    elif a.cmd == "comments":
        all_c = {}
        for bv in a.arg.split(","):
            cs = comments(bv.strip(), a.n)
            all_c[bv.strip()] = cs
            print(f"[{bv}] {len(cs)} 条热评"); time.sleep(2.5)
        out = a.out or f"bili_comments_{int(time.time())}.json"
        Path(out).write_text(json.dumps(all_c, ensure_ascii=False, indent=1))
        print(f"✓ {len(all_c)} 视频 → {out}")

if __name__ == "__main__":
    main()
