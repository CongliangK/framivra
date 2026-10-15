// selftest_bridge_unit.mjs — OutputBridge 无 Electron 单元自检（issue #113）。
// 由 tests/test_elec_output_bridge.py 驱动（Python 侧用 vtx.frame_canary 构造
// 期望值并对拍产物），本脚本只负责桥生命周期/背压/错误契约/退出卫生的
// 机制性断言与产物落盘。
//
// 用法：node selftest_bridge_unit.mjs --helper=<path> --workdir=<dir> --scenario=<name>
//   场景 basic     ：12 帧合成 BGRA（canary 由 --spec JSON 提供）→ 全部落盘成功；
//   场景 stale     ：中段注入陈旧帧（frame 号错位）→ canary_reject、无文件；
//   场景 backpressure：12 帧连发不等回执 → maxInflight ≤ 4、背压事件 ≥ 1；
//   场景 helper_kill：在途时 SIGKILL 助手 → 在途全部拒绝、后续写抛错；
//   场景 quit      ：正常 stop → 助手退出码 0、/dev/shm 零 vtx_elec_out_* 残留。
import fs from 'node:fs';
import path from 'node:path';
import { OutputBridge, ringGeometry, VIEW_W, VIEW_H } from './output_bridge.mjs';

function parseArgs(argv) {
  const o = { helper: null, workdir: null, scenario: 'basic', spec: null };
  for (const a of argv) {
    const m = a.match(/^--([a-z-]+)=(.*)$/);
    if (!m) throw new Error(`参数：${a}`);
    if (m[1] === 'helper') o.helper = m[2];
    else if (m[1] === 'workdir') o.workdir = m[2];
    else if (m[1] === 'scenario') o.scenario = m[2];
    else if (m[1] === 'spec') o.spec = m[2];
    else throw new Error(`未知参数 --${m[1]}`);
  }
  for (const k of ['helper', 'workdir', 'spec']) {
    if (!o[k]) throw new Error(`--${k} 必填`);
  }
  return o;
}

const OPTS = parseArgs(process.argv.slice(2));
const SPEC = JSON.parse(fs.readFileSync(OPTS.spec, 'utf8'));
// spec: { frames: [{frame, bgra_hex_first8, stale_of?}], bg_hex }
const GEO = ringGeometry(VIEW_W, VIEW_H);

function buildFrameBitmap(fr) {
  const b = Buffer.alloc(GEO.frameBytes);
  const bg = Buffer.from(SPEC.bg_hex, 'hex');        // 4B BGRA 底色（顶行/行 1 等值 fixture）
  for (let o = 0; o < GEO.frameBytes; o += 4) b.set(bg, o);
  b.set(Buffer.from(fr.bgra_hex_first8, 'hex'), 0);   // (0,0)+(0,1) 签名位
  return b;
}

const out = (name) => path.join(OPTS.workdir, name);

async function run() {
  const bridge = new OutputBridge({ helperPath: OPTS.helper });
  await bridge.start();
  const result = { scenario: OPTS.scenario, ok: true, errors: [], metrics: null,
                   receipts: [], residue_shm_after_stop: null, helper_exit: null };
  const frames = SPEC.frames;

  if (OPTS.scenario === 'basic' || OPTS.scenario === 'quit') {
    for (const fr of frames) {
      const r = await bridge.writeFrameBitmap(buildFrameBitmap(fr),
        { frame: fr.frame, outPath: out(`f${String(fr.frame).padStart(4, '0')}.png`) });
      result.receipts.push({ frame: fr.frame, status: r.status,
                             rendered: r.renderedFrame, png_bytes: r.pngBytes,
                             canary_match: r.canaryMatch, restore_source: r.restoreSource });
    }
  } else if (OPTS.scenario === 'stale') {
    for (const fr of frames) {
      try {
        const r = await bridge.writeFrameBitmap(buildFrameBitmap(fr),
          { frame: fr.frame, outPath: out(`f${String(fr.frame).padStart(4, '0')}.png`) });
        result.receipts.push({ frame: fr.frame, status: r.status, rendered: r.renderedFrame });
      } catch (e) {
        result.receipts.push({ frame: fr.frame, status: e.code || 'error',
                               rendered: e.receipt ? e.receipt.renderedFrame : null,
                               message: e.message });
      }
    }
  } else if (OPTS.scenario === 'backpressure') {
    const ps = [];
    for (const fr of frames) {
      ps.push(bridge.writeFrameBitmap(buildFrameBitmap(fr),
        { frame: fr.frame, outPath: out(`f${String(fr.frame).padStart(4, '0')}.png`)
          + '' }).catch((e) => { result.errors.push(String(e.message)); }));
    }
    await Promise.all(ps);
  } else if (OPTS.scenario === 'helper_kill') {
    const ps = [];
    const inflight = [];
    for (const fr of frames.slice(0, 4)) {
      const p = bridge.writeFrameBitmap(buildFrameBitmap(fr),
        { frame: fr.frame, outPath: out(`kill_f${fr.frame}.png`) });
      p.catch(() => {});                     // 拒绝由结果断言（不吞：记录于 metrics）
      ps.push(p.then((r) => inflight.push({ frame: fr.frame, ok: true, status: r.status }),
                     (e) => inflight.push({ frame: fr.frame, ok: false, code: e.code })));
    }
    await new Promise((r) => setTimeout(r, 2));   // 让请求出队
    bridge._proc.kill('SIGKILL');
    await Promise.all(ps);
    result.inflight_results = inflight;
    // 后续写必须立即失败（错误契约）。
    try {
      await bridge.writeFrameBitmap(buildFrameBitmap(frames[4]),
        { frame: frames[4].frame, outPath: out('after_kill.png') });
      result.after_kill = 'unexpected_success';
    } catch (e) {
      result.after_kill = `rejected:${e.message.slice(0, 80)}`;
    }
    await new Promise((r) => setTimeout(r, 200));
    result.residue_shm_after_stop = fs.readdirSync('/dev/shm')
      .filter((n) => n.startsWith('vtx_elec_out_'));
    return result;
  }

  result.metrics = { ...bridge.metrics, memSamples: undefined,
                     tWriteUs: undefined, tSendUs: undefined };
  await bridge.stop();
  result.helper_exit = bridge.metrics.helperExit;
  result.residue_shm_after_stop = fs.readdirSync('/dev/shm')
    .filter((n) => n.startsWith('vtx_elec_out_'));
  result.residue_run_dirs = null;   // runDir 由 stop() 清理（失败则 throw 已显形）
  return result;
}

run().then((r) => {
  process.stdout.write(`VTX_BRIDGE_UNIT_RESULT ${JSON.stringify(r)}\n`);
  process.exit(0);
}).catch((e) => {
  process.stderr.write(`✗ ${e && e.stack ? e.stack : e}\n`);
  process.exit(1);
});
