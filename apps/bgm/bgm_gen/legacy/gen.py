#!/usr/bin/env python3
"""提交 BGM 工作流到 ComfyUI 并等待完成。用法：python3 gen.py wf_ace15.json"""
import json, sys, time, urllib.request, pathlib

HOST = "http://127.0.0.1:8188"
wf = json.load(open(sys.argv[1]))
data = json.dumps({"prompt": wf, "client_id": "bgm_gen"}).encode()
req = urllib.request.Request(f"{HOST}/prompt", data=data, headers={"Content-Type": "application/json"})
pid = json.load(urllib.request.urlopen(req, timeout=30))["prompt_id"]
print("prompt_id:", pid, flush=True)

t0 = time.time()
while True:
    try:
        h = json.load(urllib.request.urlopen(f"{HOST}/history/{pid}", timeout=30)).get(pid)
    except Exception:
        h = None
    if h and h.get("status", {}).get("completed"):
        outs = []
        for node_out in h["outputs"].values():
            for a in node_out.get("audio", []):
                outs.append((a["filename"], a.get("subfolder", ""), a.get("type", "output")))
        print(f"DONE in {time.time()-t0:.0f}s -> {outs}")
        for fn, sub, typ in outs:
            src = f"{HOST}/view?filename={urllib.request.quote(fn)}&subfolder={urllib.request.quote(sub)}&type={urllib.request.quote(typ)}"
            dst = pathlib.Path("out") / fn
            dst.parent.mkdir(exist_ok=True)
            urllib.request.urlretrieve(src, dst)
            print("saved:", dst)
        break
    if h and h.get("status", {}).get("status_str") == "error":
        print("ERROR:", json.dumps(h["status"], ensure_ascii=False)[:2000]); sys.exit(1)
    time.sleep(3)
    if time.time() - t0 > 1800:
        print("TIMEOUT"); sys.exit(1)
