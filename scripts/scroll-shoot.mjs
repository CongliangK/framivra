// CDP 滚动截图：chrome --remote-debugging-port → 逐步滚动触发懒加载 → 逐段截图
// 用法：node scroll-shoot.mjs <url> <输出前缀>
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";

const url = process.argv[2] ?? "https://en.manayerbamate.com/";
const prefix = process.argv[3] ?? "sec";
const PORT = 9333;

const chrome = spawn("google-chrome", [
  "--headless=new", "--no-sandbox", "--hide-scrollbars",
  `--remote-debugging-port=${PORT}`, "--user-data-dir=/tmp/chrome-cdp",
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

function send(method, params = {}) {
  return new Promise(resolve => {
    const mid = ++id;
    pending.set(mid, resolve);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
}

async function evalJs(expr) {
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
  return r?.result?.result?.value;
}

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

async function screenshot(name) {
  const r = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(`${name}.png`, Buffer.from(r.result.data, "base64"));
}

// 等待 WebSocket 开通
if (ws.readyState !== 1) await new Promise(r => ws.onopen = r);
await send("Page.enable");
await send("Runtime.enable");

const loaded = waitEvent("Page.loadEventFired", 30000);
await send("Page.navigate", { url });
await loaded;
await sleep(6000); // 入场动画

const height = await evalJs("document.body.scrollHeight");
console.log("页面真实高度:", height);

// 第一遍：逐步滚动到底，触发全部懒加载/入场动画
for (let y = 0; y <= height; y += 600) {
  await evalJs(`window.scrollTo(0, ${y})`);
  await sleep(400);
}
await evalJs("window.scrollTo(0, 0)");
await sleep(1500);

// 第二遍：每 900px 截一段（段与段有重叠感，覆盖整页）
for (let y = 0, i = 0; y < height; y += 900, i++) {
  await evalJs(`window.scrollTo(0, ${y})`);
  await sleep(900); // 等滚动动画/视差稳定
  await screenshot(`${prefix}-${String(i).padStart(2, "0")}`);
  process.stdout.write(`  ✓ ${prefix}-${String(i).padStart(2, "0")} @y=${y}\n`);
}

chrome.kill();
process.exit(0);
