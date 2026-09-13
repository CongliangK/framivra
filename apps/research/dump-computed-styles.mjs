// CDP 计算样式采集：滚动触发懒加载后，遍历全部可见元素，聚合计算样式统计
// 用法：node dump-computed-styles.mjs <url> <输出.json>
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";

// 无参/--help 守卫（P3：防误触直连浏览器——调研工具须显式给目标 URL）
if (process.argv.length < 3 || process.argv[2] === "--help" || process.argv[2] === "-h") {
  console.error("用法: node dump-computed-styles.mjs <url> <输出.json>");
  console.error("  CDP 计算样式采集：滚动触发懒加载后，遍历全部可见元素，聚合计算样式统计。");
  console.error("  依赖 PATH 上的 google-chrome（CDP 端口 9334）。");
  process.exit(process.argv.length < 3 ? 2 : 0);
}

const url = process.argv[2];
const out = process.argv[3] ?? "computed-styles.json";
const PORT = 9334;

const chrome = spawn("google-chrome", [
  "--headless=new", "--no-sandbox", "--hide-scrollbars",
  `--remote-debugging-port=${PORT}`, "--user-data-dir=/tmp/chrome-cdp2",
  "--window-size=1440,900", "about:blank",
], { stdio: "ignore" });

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function getTargetWs() {
  for (let i = 0; i < 30; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
      const page = list.find(t => t.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch {}
    await sleep(500);
  }
  throw new Error("CDP 端点未就绪");
}

const ws = new WebSocket(await getTargetWs());
let id = 0;
const pending = new Map();
const events = [];
ws.onmessage = ev => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  else events.push(msg);
};
const send = (method, params = {}) => new Promise(resolve => {
  const mid = ++id;
  pending.set(mid, resolve);
  ws.send(JSON.stringify({ id: mid, method, params }));
});
const evalJs = async expr => (await send("Runtime.evaluate", { expression: expr, returnByValue: true }))?.result?.result?.value;
function waitEvent(method, timeoutMs) {
  return new Promise(resolve => {
    const start = events.length;
    const timer = setInterval(() => {
      const hit = events.slice(start).find(e => e.method === method);
      if (hit) { clearInterval(timer); resolve(hit); }
    }, 100);
    setTimeout(() => { clearInterval(timer); resolve(null); }, timeoutMs);
  });
}

if (ws.readyState !== 1) await new Promise(r => ws.onopen = r);
await send("Page.enable");
await send("Runtime.enable");
const loaded = waitEvent("Page.loadEventFired", 30000);
await send("Page.navigate", { url });
await loaded;
await sleep(6000);

const height = await evalJs("document.body.scrollHeight");
console.log("页面高度:", height);
for (let y = 0; y <= height; y += 600) {
  await evalJs(`window.scrollTo(0, ${y})`);
  await sleep(300);
}
await evalJs("window.scrollTo(0, 0)");
await sleep(1200);

// 采集器：统计聚合 + 重点元素明细（在页面上下文执行，returnByValue 拿回 JSON）
const collector = `(() => {
  const toHex = c => {
    const m = c.match(/rgba?\\(([^)]+)\\)/); if (!m) return c;
    const p = m[1].split(',').map(s => parseFloat(s));
    const h = x => Math.round(x).toString(16).padStart(2, '0');
    const a = p.length > 3 && p[3] < 1 ? '(' + p[3] + ')' : '';
    return '#' + h(p[0]) + h(p[1]) + h(p[2]) + a;
  };
  const bump = (o, k, w) => { if (k) o[k] = (o[k] || 0) + w; };
  const S = { textColors:{}, bgColors:{}, borderColors:{}, fontFamilies:{}, fontSizes:{},
              fontWeights:{}, lineHeights:{}, letterSpacings:{}, textTransforms:{},
              radii:{}, shadows:{}, borderWidths:{}, paddings:{}, gaps:{}, transitions:{}, opacities:{} };
  const I = { headings:[], buttons:[], navLinks:[] };
  let visible = 0, textEls = 0, textChars = 0;
  for (const el of document.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    visible++;
    const isTextLeaf = el.childElementCount === 0;
    const tlen = isTextLeaf ? (el.textContent || '').trim().length : 0;
    if (tlen) { textEls++; textChars += tlen; }

    bump(S.textColors, toHex(cs.color), 1 + tlen);
    if (cs.backgroundColor !== 'rgba(0, 0, 0, 0)')
      bump(S.bgColors, toHex(cs.backgroundColor), Math.max(1, Math.round(r.width * r.height / 5000)));
    for (const side of ['Top','Right','Bottom','Left']) {
      const w = parseFloat(cs['border' + side + 'Width']);
      if (w > 0) { bump(S.borderColors, toHex(cs['border' + side + 'Color']), 1); bump(S.borderWidths, w + 'px', 1); }
    }
    const fam = cs.fontFamily.split(',')[0].replace(/["']/g, '');
    bump(S.fontFamilies, fam, 1 + tlen);
    bump(S.fontSizes, cs.fontSize, 1 + tlen);
    bump(S.fontWeights, cs.fontWeight, 1 + tlen);
    bump(S.lineHeights, cs.lineHeight, 1 + tlen);
    if (cs.letterSpacing !== 'normal') bump(S.letterSpacings, cs.letterSpacing, 1 + tlen);
    if (cs.textTransform !== 'none') bump(S.textTransforms, cs.textTransform, 1 + tlen);
    const rad = cs.borderTopLeftRadius;
    if (rad !== '0px') bump(S.radii, rad, 1);
    if (cs.boxShadow !== 'none') bump(S.shadows, cs.boxShadow.slice(0, 80), 1);
    bump(S.paddings, cs.paddingTop, 1);
    if (cs.columnGap !== 'normal' && parseFloat(cs.columnGap) > 0) bump(S.gaps, cs.columnGap, 1);
    if (cs.transitionDuration !== 'all 0s ease 0s' && cs.transitionProperty !== 'none' && cs.transitionDuration.replace(/0s|,/g,'').trim() !== '')
      bump(S.transitions, cs.transitionProperty + ' ' + cs.transitionDuration, 1);

    const tag = el.tagName.toLowerCase();
    if (/^h[1-6]$/.test(tag) && I.headings.length < 40)
      I.headings.push({ tag, text: (el.textContent||'').trim().slice(0,40), size: cs.fontSize, weight: cs.fontWeight,
                        lh: cs.lineHeight, transform: cs.textTransform, color: toHex(cs.color), family: fam, spacing: cs.letterSpacing });
    if ((tag === 'button' || /btn/i.test(el.className)) && I.buttons.length < 40)
      I.buttons.push({ text: (el.textContent||'').trim().slice(0,25), size: cs.fontSize, weight: cs.fontWeight,
                       transform: cs.textTransform, radius: cs.borderTopLeftRadius,
                       bg: toHex(cs.backgroundColor), color: toHex(cs.color),
                       pad: cs.paddingTop + ' ' + cs.paddingLeft,
                       border: parseFloat(cs.borderTopWidth) + 'px ' + cs.borderTopStyle + ' ' + toHex(cs.borderTopColor) });
    if (el.closest('header,nav') && tag === 'a' && I.navLinks.length < 15)
      I.navLinks.push({ text: (el.textContent||'').trim().slice(0,20), size: cs.fontSize, weight: cs.fontWeight,
                        transform: cs.textTransform, color: toHex(cs.color) });
  }
  return { meta: { url: location.href, viewport: innerWidth + 'x' + innerHeight,
                   scrollHeight: document.body.scrollHeight, visibleElements: visible,
                   textElements: textEls, textChars },
           stats: S, interesting: I };
})()`;

const dump = await evalJs(collector);
writeFileSync(out, JSON.stringify(dump, null, 2));
console.log(`已采集：${dump.meta.visibleElements} 个可见元素 / ${dump.meta.textChars} 字符 → ${out}`);
chrome.kill();
process.exit(0);
