"""Execute the production bridge in a small deterministic Node DOM model.

These checks exercise readiness, request ownership and witness placement, not
Chromium rasterization, native JS observation, or measured CEF performance.
The native model class additionally requires a C++20 compiler and links actual
production FSM/pump/orchestration with fake CEF events; missing tools are SKIP.
Run with Node installed: python3 -m unittest
tests.gfx.test_cef_exact_bridge_contract -v.  No CEF worker is launched.
"""
import json
import platform
import tempfile
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
CPP = ROOT / "experiments/gfx_cef_worker/src"
SOURCE = CPP / "serve.cpp"
FIXTURES = Path(__file__).resolve().parent
COMPILER = shutil.which("g++") or shutil.which("clang++")
NODE = shutil.which("node")


def bridge_body():
    source = SOURCE.read_text(encoding="utf-8")
    begin = source.index('R"VTXSYNC(') + len('R"VTXSYNC(')
    return source[begin:source.index(')VTXSYNC"', begin)]


HARNESS = r"""
const vm = require('vm');
let input = '';
process.stdin.on('data', d => input += d);
process.stdin.on('end', async () => {
  const {body, scenario, mg_runtime} = JSON.parse(input);
  function deferred() {
    let resolve;
    const promise = new Promise(r => resolve = r);
    return {promise, resolve};
  }
  const fonts = deferred(), img = deferred(), changedFont = deferred(), changedImg = deferred();
  const events = [], nodes = [], raf = [], tasks = [], created = [], calls = [], rejections = [];
  let rafCalls = 0;
  process.on('unhandledRejection', e => rejections.push(String(e)));
  const root = {getBoundingClientRect() { return {}; }, appendChild(n) { nodes.push(n); n.parentNode = root; },
                removeChild(n) { const i = nodes.indexOf(n); if (i >= 0) nodes.splice(i, 1); }};
  const document = {
    readyState: 'complete', documentElement: root,
    fonts: {ready: fonts.promise}, images: [{decode: () => img.promise}],
    querySelectorAll: () => [...nodes],
    createElement(tag) {
      created.push(tag);
      if (tag === 'canvas') throw Error('canvas is forbidden in exact bridge');
      const values = {}, priorities = {};
      return {tag, values, priorities, attrs: {},
              setAttribute(k, v) { this.attrs[k] = v; },
              style: {setProperty(k, v, p) { values[k] = v; priorities[k] = p; }}};
    }
  };
  const ctx = {document, Promise, URLSearchParams, location: {search: '?f=257'},
    console: {debug(s) { events.push(JSON.parse(s.slice('__VTX_READY__:'.length))); }},
    requestAnimationFrame(cb) { rafCalls++; raf.push(cb); },
    setTimeout(cb) { tasks.push(cb); },
    MessageChannel: class {
      constructor() { this.port1 = {}; this.port2 = {postMessage: () => tasks.push(() => this.port1.onmessage())}; }
    },
    addEventListener() {},
  };
  ctx.window = ctx;
  const context = vm.createContext(ctx);
  function run(overrides = {}) {
    const p = {GEN: 1, REQ: 0xfedcba98, F: 257, MODE_N: true,
               CLEAN: false, EXACT: true, BASELINE: false, FAULT: 'none', ...overrides};
    const vars = Object.entries(p).map(([k,v]) => `var ${k}=${JSON.stringify(v)};`).join('');
    vm.runInContext('(function(){"use strict";' + vars + body, context);
  }
  async function microtasks() { for (let i = 0; i < 12; i++) await Promise.resolve(); }
  async function tick() {
    await microtasks();
    const r = raf.splice(0); for (const f of r) f();
    await microtasks();
    const t = tasks.splice(0); for (const f of t) f();
    await microtasks();
  }
  async function flush() { for (let i = 0; i < 6; i++) await tick(); await new Promise(setImmediate); }
  const snapshots = [];
  const snap = () => snapshots.push({events: structuredClone(events), nodes: nodes.length, calls: [...calls]});
  if (scenario === 'normal' || scenario === 'baseline') {
    ctx.__vtx_originals = () => { calls.push('originals'); return [[1,2,3,4],[5,6,7,8]]; };
    run({BASELINE: scenario === 'baseline'});
    await flush(); snap();
    fonts.resolve(); await flush(); snap();
    img.resolve(); await flush(); snap();
    if (scenario === 'normal') { ctx.__VTX_RELOCATE(); await flush(); snap(); }
  } else if (scenario === 'state_assets') {
    ctx.__vtx_render = f => {
      calls.push(f);
      document.fonts.ready = changedFont.promise;
      document.images = [{decode: () => changedImg.promise}];
    };
    run({MODE_N: false});
    fonts.resolve(); img.resolve(); await flush(); snap();
    changedFont.resolve(); await flush(); snap();
    changedImg.resolve(); await flush(); snap();
  } else if (scenario === 'async_state_assets' || scenario === 'async_baseline_assets') {
    const rendered = deferred();
    ctx.__vtx_render = f => {
      calls.push(f);
      return rendered.promise.then(() => {
        document.fonts.ready = changedFont.promise;
        document.images = [{decode: () => changedImg.promise}];
      });
    };
    run({MODE_N: false, BASELINE: scenario === 'async_baseline_assets'});
    fonts.resolve(); img.resolve(); await flush(); snap();
    rendered.resolve(); await flush(); snap();
    changedFont.resolve(); await flush(); snap();
    changedImg.resolve(); await flush(); snap();
  } else if (scenario === 'mg_reference_runtime') {
    // Actual MG runtime queues asynchronous complete-frame drawing. This mock
    // substitutes Canvas operations, not that runtime's Promise/hook contract.
    const rendered = deferred(), elements = {};
    const canvasContext = {save() {}, restore() {}, fillRect() {}};
    function element(id) {
      if (!elements[id]) elements[id] = {
        textContent: '', value: 0, append() {}, appendChild() {}, addEventListener() {},
        getContext: () => canvasContext, toDataURL: () => ''
      };
      return elements[id];
    }
    document.getElementById = element;
    root.classList = {add() {}};
    ctx.MG_REFERENCE = {id: 'bridge-contract', title: 'Bridge',
      examples: [{name: 'frame', frames: 300, data: {}}], validate() {},
      render(c, d, f) { calls.push(f); return f === 257 ? rendered.promise : undefined; }};
    ctx.location.search = '?f=0';
    vm.runInContext(mg_runtime, context);
    run({MODE_N: false});
    img.resolve(); await flush(); snap();
    fonts.resolve(); await flush(); snap();
    rendered.resolve(); await flush(); snap();
  } else if (scenario === 'async_rejected') {
    ctx.__vtx_render = () => Promise.reject(Error('ASYNC_FRAME_FAILURE'));
    run({MODE_N: false}); fonts.resolve(); img.resolve(); await flush(); snap();
  } else if (scenario === 'stale_async') {
    const first = deferred(), latest = deferred();
    ctx.__vtx_render = f => { calls.push(f); return f === 10 ? first.promise : latest.promise; };
    run({MODE_N: false, REQ: 41, F: 10});
    await flush();
    run({MODE_N: false, REQ: 42, F: 20});
    fonts.resolve(); img.resolve(); latest.resolve(); await flush(); snap();
    first.resolve(); await flush(); snap();
  } else if (scenario === 'stale') {
    ctx.__vtx_render = f => calls.push(f);
    run({MODE_N: false, REQ: 41, F: 10});
    run({MODE_N: false, REQ: 42, F: 20});
    fonts.resolve(); img.resolve(); await flush(); snap();
  } else if (scenario === 'duplicate') {
    ctx.__vtx_render = f => calls.push(f);
    run({MODE_N: false}); run({MODE_N: false});
    fonts.resolve(); img.resolve(); await flush(); snap();
  } else if (scenario === 'thenable' || scenario === 'missing_hook') {
    if (scenario === 'thenable') ctx.__vtx_render = () => ({then() {}});
    run({MODE_N: false}); fonts.resolve(); img.resolve(); await flush(); snap();
  } else if (scenario === 'missing_frame_parameter') {
    ctx.location.search = '';
    run({F: 0}); fonts.resolve(); img.resolve(); await flush(); snap();
  } else { throw Error('unknown scenario'); }
  process.stdout.write(JSON.stringify({snapshots, events, created, rejections, rafCalls,
    nodes: nodes.map(n => ({tag:n.tag, attrs:n.attrs, values:n.values, priorities:n.priorities}))}));
});
"""


@unittest.skipUnless(NODE, "Bridge contract model requires Node; it does not run CEF")
class ExactBridgeContract(unittest.TestCase):
    def run_scenario(self, name):
        result = subprocess.run(
            [NODE, "-e", HARNESS],
            input=json.dumps({"body": bridge_body(), "scenario": name,
                              "mg_runtime": (ROOT / "templates/mg-styles/html/runtime.js")
                              .read_text(encoding="utf-8")
                              if name == "mg_reference_runtime" else None}),
            text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_ready_waits_for_fonts_and_images(self):
        out = self.run_scenario("normal")
        for snapshot in out["snapshots"][:2]:
            self.assertEqual(snapshot["events"], [])
            self.assertEqual(snapshot["nodes"], 0)
        self.assertEqual([e["phase"] for e in out["events"]],
                         ["assets_ready", "canary_set"])
        self.assertTrue(all(e["f"] == 257 and e["req"] == 0xfedcba98
                            for e in out["events"]))
        self.assertEqual(out["snapshots"][2]["nodes"], 2)
        self.assertEqual(out["snapshots"][3]["nodes"], 2)
        self.assertNotIn("canvas", out["created"])
        self.assertEqual(out["nodes"][0]["values"]["right"], "0")
        self.assertEqual(out["nodes"][1]["values"]["left"], "0")

    def test_unsigned_reference_never_inserts_or_reads_witness_pixels(self):
        out = self.run_scenario("baseline")
        self.assertEqual(out["created"], [])
        self.assertEqual(out["nodes"], [])
        self.assertEqual([e["phase"] for e in out["events"]], ["baseline_ready"])
        self.assertEqual(out["snapshots"][-1]["calls"], [])

    def test_mode_p_waits_for_frame_dependent_assets(self):
        out = self.run_scenario("state_assets")
        for snapshot in out["snapshots"][:2]:
            self.assertEqual(snapshot["events"], [])
            self.assertEqual(snapshot["nodes"], 0)
        self.assertEqual(out["snapshots"][-1]["calls"], [257])
        self.assertEqual([e["phase"] for e in out["events"]],
                         ["assets_ready", "canary_set"])

    def test_superseded_async_chain_cannot_publish_old_request(self):
        out = self.run_scenario("stale")
        self.assertEqual(len(out["events"]), 2)
        self.assertTrue(all(e["req"] == 42 and e["f"] == 20 for e in out["events"]))
        self.assertEqual(len(out["nodes"]), 2)

    def test_async_mode_p_hook_and_its_assets_are_both_awaited(self):
        for scenario in ("async_state_assets", "async_baseline_assets"):
            with self.subTest(scenario=scenario):
                out = self.run_scenario(scenario)
                for snapshot in out["snapshots"][:3]:
                    self.assertEqual(snapshot["events"], [])
                    self.assertEqual(snapshot["nodes"], 0)
                expected = (["baseline_ready"] if scenario == "async_baseline_assets"
                            else ["assets_ready", "canary_set"])
                self.assertEqual([e["phase"] for e in out["events"]], expected)
                self.assertEqual(out["snapshots"][-1]["calls"], [257])
                self.assertEqual(out["rejections"], [])

    def test_mode_p_avoids_animation_frame_delay_without_changing_mode_n(self):
        for scenario in ("state_assets", "async_state_assets", "async_baseline_assets"):
            with self.subTest(scenario=scenario):
                out = self.run_scenario(scenario)
                self.assertEqual(out["rafCalls"], 0)
                self.assertTrue(out["events"])
        self.assertEqual(self.run_scenario("normal")["rafCalls"], 2)
        self.assertEqual(self.run_scenario("baseline")["rafCalls"], 2)

    def test_actual_mg_reference_runtime_finishes_queued_frame_before_ready(self):
        out = self.run_scenario("mg_reference_runtime")
        for snapshot in out["snapshots"][:2]:
            self.assertEqual(snapshot["events"], [])
            self.assertEqual(snapshot["nodes"], 0)
        self.assertIn(257, out["snapshots"][1]["calls"])
        self.assertEqual([e["phase"] for e in out["events"]],
                         ["assets_ready", "canary_set"])
        self.assertEqual(out["rafCalls"], 0)
        self.assertEqual(out["rejections"], [])

    def test_rejected_async_hook_is_not_swallowed_as_readiness(self):
        out = self.run_scenario("async_rejected")
        self.assertEqual(out["events"], [])
        self.assertEqual(out["nodes"], [])
        self.assertEqual(out["rejections"], ["Error: ASYNC_FRAME_FAILURE"])

    def test_superseded_hook_promise_cannot_publish_late(self):
        out = self.run_scenario("stale_async")
        self.assertEqual(out["snapshots"][0]["events"], out["snapshots"][1]["events"])
        self.assertEqual(len(out["events"]), 2)
        self.assertTrue(all(e["req"] == 42 and e["f"] == 20 for e in out["events"]))

    def test_duplicate_bridge_does_not_reapply_hook(self):
        out = self.run_scenario("duplicate")
        self.assertEqual(out["snapshots"][-1]["calls"], [257])
        self.assertEqual(len(out["events"]), 2)

    def test_invalid_mode_p_hook_cannot_publish_readiness(self):
        for scenario in ("missing_hook", "thenable"):
            with self.subTest(scenario=scenario):
                out = self.run_scenario(scenario)
                self.assertEqual(out["events"], [])
                self.assertEqual(out["nodes"], [])

    def test_absent_frame_parameter_is_not_frame_zero(self):
        out = self.run_scenario("missing_frame_parameter")
        self.assertEqual(out["events"], [])
        self.assertEqual(out["nodes"], [])


# These models use actual native production code with fake CEF callbacks.
# They require a C++20 compiler, not a browser, and are not performance tests.
def region(source, start, end):
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


@unittest.skipUnless(COMPILER, "CEF-independent models require a C++20 compiler")
class NativeOrchestrationModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="cef_native_model_")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.build = Path(cls.temp.name)
        arch = (["-march=x86-64-v3"]
                if platform.machine().lower() in ("x86_64", "amd64") else [])
        cls.flags = ["-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror",
                     "-pthread", *arch, "-I", str(CPP)]
        cls.objects = []
        for name in ("frame_sync_machine", "frame_pipeline", "pixel_ops"):
            target = cls.build / (name + ".o")
            cls.command([COMPILER, *cls.flags, "-c", str(CPP / (name + ".cpp")),
                         "-o", str(target)])
            cls.objects.append(str(target))

    @staticmethod
    def command(argv):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise AssertionError("Native model command failed:\n" + " ".join(argv)
                                 + "\n" + result.stdout + result.stderr)
        return result.stdout

    def compile_run(self, name, source, *, link_machine=True):
        path, executable = self.build / (name + ".cpp"), self.build / name
        path.write_text(source, encoding="utf-8")
        model = {"paired": "ORCHESTRATION", "exact_bytes": "EXACT_BYTES",
                 "pump": "PUMP", "lifecycle": "LIFECYCLE", "passive": "PASSIVE"}[name]
        self.command([COMPILER, *self.flags, "-DVTX_MODEL_" + model, str(path),
                      *(self.objects if link_machine else []), "-o", str(executable)])
        return self.command([str(executable)])

    def test_actual_pair_orchestration_and_cross_browser_error_fences(self):
        serve = (CPP / "serve.cpp").read_text(encoding="utf-8")
        app = (CPP / "app_handler.cpp").read_text(encoding="utf-8")
        observer = (CPP / "js_error_observer.cpp").read_text(encoding="utf-8")
        fixture = (FIXTURES / "_cef_native_models.cpp").read_text(encoding="utf-8")
        # Real ready-message parser and real negative-only OnPaint path: neither
        # the model nor a second verifier may manufacture an accepted frame.
        parser = region(serve, "namespace {\n\nbool ParseJsonInt", "// —— #114 注入桥 JS")
        expand = region(serve, "std::string ExpandUrlTemplate", "// 导航 →")
        result = region(serve, "struct SyncProduceResult", "// —— __VTX_READY__")
        core = region(serve, "class ExactBaselineCapture", "void FillReceipt")
        paint = region(app,
                       "  if (state_->sync_machine != nullptr && state_->sync_capture_armed)",
                       "  if (state_->capture_armed)")
        fallback = region(app, "  // One-shot capture still snapshots every paint.",
                          "}\n\nbool WorkerClient::OnConsoleMessage")
        paint = ("void SimPaint(RunState* state_, const std::vector<uint8_t>& bytes) {"
                 "const auto* src = bytes.data(); int width = 32, height = 24;\n"
                 + paint + fallback + "}\n")
        checkpoint = region(observer, "bool CheckJsCheckpoint(", "}  // namespace vtx_cef")
        source = (fixture.replace("// PRODUCTION_NATIVE_ORCHESTRATION",
                                  expand + result + parser + paint + core)
                  .replace("// PRODUCTION_JS_CHECKPOINT", checkpoint))
        output = self.compile_run("paired", source)
        self.assertIn("EXTRACTED_NATIVE_MODEL_PASS", output)
        self.assertIn("scenarios=49", output)

    def test_every_veto_and_probe_byte_with_owned_capture_storage(self):
        source = (FIXTURES / "_cef_native_models.cpp").read_text(encoding="utf-8")
        output = self.compile_run("exact_bytes", source)
        self.assertIn("INDEPENDENT_EXACT_PASS", output)
        self.assertIn("veto_bytes=12288 signature_bytes=1024 geometries=8", output)

    @unittest.skipUnless(platform.system() in ("Linux", "Darwin"),
                         "Native serve transport requires POSIX Unix sockets and SCM_RIGHTS")
    def test_actual_full_serve_failure_paths_over_real_socketpair(self):
        serve = SOURCE.read_text(encoding="utf-8")
        app = (CPP / "app_handler.cpp").read_text(encoding="utf-8")
        fixture = (FIXTURES / "_cef_native_models.cpp").read_text(encoding="utf-8")
        structs = (region(serve, "struct ProduceResult", "std::string ExpandUrlTemplate")
                   + region(serve, "struct SyncProduceResult", "// —— __VTX_READY__"))
        transport = region(serve, "int RecvAllOrShort(", "// ---------------------------------------------------------------- 帧生产")
        control = region(serve, "void FillReceipt(", "}  // namespace vtx_cef")
        creator = region(app, "bool CreateOsrBrowser(", "void PumpDriver::Once(")
        source = (fixture.replace("// PRODUCTION_SERVE_RESULT_STRUCTS", structs)
                  .replace("// PRODUCTION_SERVE_BROWSER_CREATOR", creator)
                  .replace("// PRODUCTION_SERVE_TRANSPORT_POOL", transport)
                  .replace("// PRODUCTION_SERVE_FAILURE_CONTROL_FLOW", control))
        path = self.build / "serve_failures.cpp"
        executable = self.build / "serve_failures"
        path.write_text(source, encoding="utf-8")
        self.command([COMPILER, *self.flags, "-DVTX_MODEL_SERVE_FAILURES", str(path),
                      *self.objects, "-o", str(executable)])
        scenarios = ("initialize_false", "create_false", "create_late", "create_closed", "sync_log_failure",
                     "resident_timeout", "resident_closed", "ring_failure", "hello_failure", "eof",
                     "partial_request", "invalid_request", "idle_timeout", "success",
                     "capture_failure", "encode_throw", "write_false", "baseline_closes_signed")
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                result = subprocess.run([str(executable), scenario, directory],
                                        capture_output=True, text=True, timeout=10,
                                        start_new_session=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("EXTRACTED_SERVE_FAILURE_PASS scenario=" + scenario, result.stdout)
        for fps in (1, 30, 60, 120, 240):
            with self.subTest(signed_capture_fps=fps), tempfile.TemporaryDirectory() as directory:
                result = subprocess.run([str(executable), "success", directory, str(fps)],
                                        capture_output=True, text=True, timeout=10,
                                        start_new_session=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("EXTRACTED_SERVE_FAILURE_PASS scenario=success", result.stdout)
        for scenario in ("create_missing", "close_timeout"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                result = subprocess.run([str(executable), scenario, directory],
                                        capture_output=True, text=True, timeout=10,
                                        start_new_session=True)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertNotIn("EXTRACTED_SERVE_FAILURE_PASS", result.stdout)

    @unittest.skipUnless(platform.system() in ("Linux", "Darwin"),
                         "One-shot lifecycle model requires POSIX worker interfaces")
    def test_actual_one_shot_control_flow_closes_every_exit(self):
        main = (CPP / "main.cpp").read_text(encoding="utf-8")
        serve = SOURCE.read_text(encoding="utf-8")
        app = (CPP / "app_handler.cpp").read_text(encoding="utf-8")
        fixture = (FIXTURES / "_cef_native_models.cpp").read_text(encoding="utf-8")
        # Keep the complete one-shot CEF initialization, screenshot, probe,
        # hold, JSON and cleanup control flow; only CEF/PNG operations are fake.
        control = main[main.index("  const Clock::time_point t0 = Clock::now();"):]
        helper = region(serve, "bool CloseServeBrowser(",
                        "// ---------------------------------------------------------------- RunServe")
        creator = region(app, "bool CreateOsrBrowser(", "void PumpDriver::Once(")
        json_helpers = region(main, "std::string JsonEscape(", "// 预乘 BGRA")
        source = (fixture.replace("// PRODUCTION_SERVE_BROWSER_CREATOR", creator)
                  .replace("// PRODUCTION_ONESHOT_JSON_HELPERS", json_helpers)
                  .replace("// PRODUCTION_ONESHOT_CLOSE_HELPER", helper)
                  .replace("// PRODUCTION_ONESHOT_CONTROL_FLOW", control))
        path, executable = self.build / "oneshot.cpp", self.build / "oneshot"
        path.write_text(source, encoding="utf-8")
        self.command([COMPILER, *self.flags, "-DVTX_MODEL_ONESHOT", str(path),
                      "-o", str(executable)])
        scenarios = ("initialize_false", "create_false", "create_late", "create_closed",
                     "load_timeout", "load_closed", "settle_closed", "probe_closed",
                     "frame_closed", "gpu_load_closed", "gpu_settle_closed",
                     "gpu_probe_closed", "hold_closed", "success", "probe_timeout",
                     "gpu_load_timeout", "write_false", "no_paint")
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                result = subprocess.run([str(executable), scenario, directory],
                                        capture_output=True, text=True, timeout=10,
                                        start_new_session=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("EXTRACTED_ONESHOT_PASS scenario=" + scenario, result.stdout)
        for scenario, status in (("create_missing", 124), ("close_timeout", 1)):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                result = subprocess.run([str(executable), scenario, directory],
                                        capture_output=True, text=True, timeout=10,
                                        start_new_session=True)
                self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                self.assertNotIn("EXTRACTED_ONESHOT_PASS", result.stdout)

    def test_actual_early_close_handles_pending_and_timed_out_browser(self):
        serve = SOURCE.read_text(encoding="utf-8")
        helper = region(serve, "bool CloseServeBrowser(",
                        "// ---------------------------------------------------------------- RunServe")
        fixture = (FIXTURES / "_cef_native_models.cpp").read_text(encoding="utf-8")
        source = fixture.replace("// PRODUCTION_LIFECYCLE_IMPLEMENTATION", helper)
        output = self.compile_run("lifecycle", source, link_machine=False)
        self.assertIn("EXTRACTED_LIFECYCLE_PASS", output)
        self.assertIn("scenarios=8", output)
        stopped = subprocess.run([str(self.build / "lifecycle"), "fail-stop"],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(stopped.returncode, 17)
        self.assertNotIn("UNSAFE_UNWIND", stopped.stderr)
        run_serve = serve[serve.index("int RunServe("):]
        self.assertIn("creation_requested = CreateOsrBrowser", run_serve)
        self.assertIn("if (!creation_requested || !pump.Until(", run_serve)
        # The only calls are inside the checked early-close lambda and after
        # the paired final close gate; no unchecked setup branch calls CEF.
        self.assertEqual(run_serve.count("    CefShutdown();"), 1)
        self.assertEqual(run_serve.count("  CefShutdown();"), 2)
        self.assertNotIn("pump.Until([&] { return state.closed.load(); }, 5000)",
                         run_serve)

    def test_passive_copy_policy_and_post_readiness_sequence_floor(self):
        source = (FIXTURES / "_cef_native_models.cpp").read_text(encoding="utf-8")
        self.compile_run("passive", source, link_machine=False)

    def test_actual_external_pump_completion_and_wakeup_races(self):
        app = (CPP / "app_handler.cpp").read_text(encoding="utf-8")
        implementation = region(app, "void PumpDriver::Once()", "}  // namespace vtx_cef")
        fixture = (FIXTURES / "_cef_native_models.cpp").read_text(encoding="utf-8")
        source = fixture.replace("// PRODUCTION_PUMP_IMPLEMENTATION", implementation)
        output = self.compile_run("pump", source, link_machine=False)
        self.assertIn("EXTRACTED_PUMP_PASS", output)
        self.assertIn("races=200", output)


if __name__ == "__main__":
    unittest.main()
