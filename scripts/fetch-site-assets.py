#!/usr/bin/env python3
"""抓取网站完整样式资产到本地：外链 CSS（递归 @import）、内联 <style> 块、字体文件。

用法：fetch-site-assets.py <页面URL> <输出目录>
产出：manifest.json + index.html + css-*.css + inline-styles.css + font-*.woff2
"""
import json
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def save(url: str, outdir: Path, kind: str) -> Path:
    data = fetch(url)
    raw = urlparse(url).path.split("/")[-1] or "root"
    name = re.sub(r"[^A-Za-z0-9._-]", "_", raw)[:80]
    path = outdir / f"{kind}-{name}"
    path.write_bytes(data)
    return path


def main() -> None:
    url, outdir = sys.argv[1], Path(sys.argv[2])
    outdir.mkdir(parents=True, exist_ok=True)

    html = fetch(url).decode("utf-8", "replace")
    (outdir / "index.html").write_text(html)

    def links(attr_first: bool) -> list[str]:
        pat = (
            r'<link[^>]+href=["\']([^"\']+)["\'][^>]*rel=["\']stylesheet["\']'
            if attr_first
            else r'<link[^>]+rel=["\']stylesheet["\'][^>]*href=["\']([^"\']+)["\']'
        )
        return re.findall(pat, html)

    css_urls = links(True) + links(False)
    font_urls = re.findall(r'<link[^>]+as=["\']font["\'][^>]*href=["\']([^"\']+)["\']', html)
    font_urls += re.findall(r'<link[^>]+href=["\']([^"\']+)["\'][^>]+as=["\']font["\']', html)

    inline_blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, re.S)
    (outdir / "inline-styles.css").write_text(
        "\n/* ===== 另一个 <style> 块 ===== */\n".join(inline_blocks)
    )
    for block in inline_blocks:
        font_urls += re.findall(r"url\([\"']?([^\"')]+\.(?:woff2?|ttf|otf))", block)

    css_files, seen_css = [], set()
    queue = [urljoin(url, u) for u in css_urls]
    while queue:
        u = queue.pop(0)
        if u in seen_css:
            continue
        seen_css.add(u)
        try:
            path = save(u, outdir, "css")
            css_files.append(path.name)
            text = path.read_text("utf-8", "replace")
            font_urls += re.findall(r"url\([\"']?([^\"')]+\.(?:woff2?|ttf|otf))", text)
            imports = re.findall(r"@import\s+(?:url\()?[\"]?([^\"\')\s;]+)", text)
            queue += [urljoin(u, i) for i in imports]
            print(f"  ✓ CSS {path.name} ({path.stat().st_size//1024}KB)")
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ CSS 失败 {u}: {e}")

    font_files, seen_font = [], set()
    for u in font_urls:
        u = urljoin(url, u)
        if u in seen_font or not u.startswith("http"):
            continue
        seen_font.add(u)
        try:
            path = save(u, outdir, "font")
            font_files.append(path.name)
            print(f"  ✓ 字体 {path.name} ({path.stat().st_size//1024}KB)")
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ 字体失败 {u}: {e}")

    manifest = {
        "source": url,
        "css": css_files,
        "inline_style_blocks": len(inline_blocks),
        "fonts": font_files,
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"完成：{len(css_files)} CSS + {len(inline_blocks)} 内联块 + {len(font_files)} 字体 → {outdir}")


if __name__ == "__main__":
    main()
