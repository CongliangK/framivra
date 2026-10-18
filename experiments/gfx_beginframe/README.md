# BeginFrame isolated research

This runner is an experiment, with no production backend registration. Findings and remaining acceptance gates live in [the research report](../../docs/gfx-beginframe.md). The fixed distribution and live protocol identity live in [binary-lock.json](binary-lock.json). An absent or different binary fails explicitly; there is no silent skip or browser substitution.

Inspect the entry points without starting Chrome or writing frames:

```bash
python3 experiments/gfx_beginframe/run.py --help
python3 experiments/gfx_beginframe/functional.py --help
python3 experiments/gfx_beginframe/strict.py --help
```

Actual rendering writes only the caller's isolated output directory and temporary browser profile. Use a fresh output for each independent run. Both persistent entry points require an explicit declaration that the page's `__vtx_render(f)` is a pure function; this declaration is a caller responsibility, not a capability inferred from the mere presence of a function. `functional.py` owns its generated pure fixture.

`run.py` exposes small frame-order probes. `functional.py` exercises resources, frame watermarks, repeats/seeks, idle submission and an intentional exception followed by a fresh target/run. `strict.py` freezes a comparison plan, renders independent arms, emits real completion receipts only after all instances finish, and invokes the existing strict Golden verifier. Input timelines must be self-contained or declare every resource dependency. Production caches and real source projects must not be used as output roots.

The navigation arm uses the same binary with the two required compositor control flags removed and `enableBeginFrameControl=false`. A controlled target cannot serve as the ordinary screenshot reference: its `Page.captureScreenshot` waits for a compositor step. This necessary axis is declared in each strict plan.

A failed request aborts its run and writes no completed receipt. Recovery is a fresh target/run with newly initialized state; it does not silently turn a partial run into a successful one. Repeated/no-damage requests require their own returned screenshot. CDP request IDs, target-specific sockets, and a page acknowledgement bind run/target/generation/request/frame. Diagnostic runs additionally decode both framebuffer watermark corners. Clean Golden runs compare every RGBA pixel independently, without diagnostic overlays.

CPU protocol checks do not require a browser:

```bash
python3 experiments/gfx_beginframe/test_run.py
```
