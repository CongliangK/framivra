#!/usr/bin/env python3
"""hello-app CLI —— 最小应用范本的探针子命令（v2 ARCHITECTURE §4.2 / §3.1）。

app 的定义（§0.2）：产出**独立素材**、不进 timeline 管线、报错不影响渲染主线。
本 CLI 用 `hello --name X` 演示最小形态：在 <app>/out/ 落一个素材文件并打印
产物路径——模拟「app 产出独立素材」的语义（素材由人/上层编排决定是否引用）。

自包含纪律（§3.1）：纯标准库、零 sys.path hack、零对 vtx 内部的 import——
app 与内核之间只有「产物文件」这一交付面。
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def cmd_hello(args) -> int:
    out_dir = HERE / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in args.name)[:60]
    product = out_dir / f"hello-{safe}.txt"
    product.write_text(f"hello, {args.name}!\n", encoding="utf-8")
    print(f"hello, {args.name}!")
    print(f"产物（独立素材，不进 timeline 管线）：{product}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="hello-app", description="hello-app —— video_tools v2 最小应用范本（产出独立素材的探针子命令）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_hello = sub.add_parser("hello", help="产出一份独立素材文件并打印路径（app 语义的最小演示）")
    p_hello.add_argument("--name", default="world", help="素材署名（写入产物内容与文件名，默认 world）")
    p_hello.set_defaults(fn=cmd_hello)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
