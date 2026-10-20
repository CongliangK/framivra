(() => {
  "use strict";
  const TAU = Math.PI * 2;
  const txt = (c, h, s, x, y, w, size, color, lines = 1) =>
    h.text(c, s, x, y, w, size, {
      minSize: 24,
      maxLines: lines,
      height: size * 1.3 * lines,
      color,
    });
  const line = (c, pts, color, width = 4) => {
    c.strokeStyle = color;
    c.lineWidth = width;
    c.beginPath();
    pts.forEach(([x, y], i) => (i ? c.lineTo(x, y) : c.moveTo(x, y)));
    c.stroke();
  };
  const finite = (v, name, lo, hi, h) => h.num(v, name, lo, hi);
  const noise = (seed, f, i) => {
    let v = (seed ^ Math.imul(f, 374761393) ^ Math.imul(i, 668265263)) >>> 0;
    v = Math.imul(v ^ (v >>> 13), 1274126177) >>> 0;
    return ((v ^ (v >>> 16)) & 65535) / 32767.5 - 1;
  };
  const check = (d, N, h, pn = 4) => {
    h.int(N, "frames", 30, 18000);
    h.label(d.title, "title", 60);
    if (d.palette !== undefined) h.palette(d.palette, pn);
  };

  const PAL = ["#faf5e8", "#202730", "#e66b42", "#49a69d"];
  // Shared endpoint positions make gather/merge/split boundaries continuous.
  function balls(s, u, seed) {
    let r = 120 * (s.amount ?? 1),
      dist =
        s.mode === "gather"
          ? 350 - 130 * u
          : s.mode === "merge"
            ? 220 - 150 * u
            : s.mode === "split"
              ? 70 + 280 * u
              : 350 * (1 - Math.min(1, u / 0.55));
    if (s.mode === "resolve") r += 55 * Math.min(1, u / 0.55);
    const cy = s.mode === "resolve" ? 540 + 30 * Math.min(1, u / 0.55) : 540,
      tilt = 12 * Math.sin(seed),
      b = [
        [960 - dist, cy + tilt, r],
        [960 + dist, cy - tilt, r],
      ];
    if (s.mode === "split" && u > 0.57) {
      const lag = (u - 0.57) / 0.43,
        tiny =
          r * 0.24 * Math.sin(Math.PI * lag) * (0.95 + 0.05 * Math.cos(seed)),
        drift = 14 * Math.sin(seed);
      b.push(
        [960 - 75 * lag + drift, 540 + 180 * lag, tiny],
        [960 + 80 * lag - drift, 540 - 170 * lag, tiny * 0.8],
      );
    }
    if (s.mode === "resolve" && u > 0.55) {
      const a = (u - 0.55) / 0.45;
      b.push([960 - 220 * a, cy, 90 * a], [960 + 220 * a, cy, 90 * a]);
    }
    return b;
  }
  const field = (b, x, y) =>
    b.reduce(
      (a, [cx, cy, r]) =>
        a + (r * r) / Math.max(1, (x - cx) ** 2 + (y - cy) ** 2),
      0,
    );
  // Fit the full text rectangle inside the final implicit pool, including padding.
  function resolveSize(d) {
    const b = balls(d.stages[3], 1, d.seed ?? 0),
      len = Array.from(d.stages[3].label).length;
    for (let px = 40; px >= 24; px--) {
      const half = (len * px) / 2 + 10;
      if (
        [960 - half, 960 + half].every((x) =>
          [542, 558 + px * 1.25].every((y) => field(b, x, y) >= 1.04),
        )
      )
        return px;
    }
    throw Error(
      "final label cannot fit liquid mask at minimum24px; shorten it",
    );
  }
  function validate(d, N, h) {
    check(d, N, h);
    if (d.subtitle) h.label(d.subtitle, "subtitle", 68);
    h.int(d.seed ?? 0, "seed", -2147483648, 2147483647);
    h.windows(d.stages, N, 10);
    if (d.stages.map((s) => s.mode).join() !== "gather,merge,split,resolve")
      throw Error("ordered stages required");
    let amount;
    d.stages.forEach((s) => {
      h.label(s.label, "stage label", 32);
      h.num(s.amount ?? 1, "amount", 0.65, 1.25);
      if (amount !== undefined && amount !== (s.amount ?? 1))
        throw Error("amount must be common across stages");
      amount = s.amount ?? 1;
    });
    // Completion starts at this integer address; count complete drawings rather
    // than rounding a percentage of the duration (12 frames would leave only5).
    const resolveFrames = d.stages[3].out - d.stages[3].in;
    const completeAt = Math.ceil(0.55 * (resolveFrames - 1));
    if (resolveFrames - completeAt < 6)
      throw Error("resolve needs at least six complete held frames (minimum13)");
    h.label(d.stages[3].label, "final masked word", 14);
    resolveSize(d);
  }
  // Field surface → neck/satellites → masked word; final45% of resolve holds still.
  function render(c, d, f, N, h) {
    h.int(f, "frame", 0, N - 1);
    const s = h.stage(d.stages, f),
      raw = (f - s.in) / Math.max(1, s.out - s.in - 1),
      u = s.mode === "resolve" ? Math.min(1, raw / 0.55) : raw,
      b = balls(s, u, d.seed ?? 0),
      p = d.palette ?? PAL,
      rgb = (hex) => [1, 3, 5].map((k) => parseInt(hex.slice(k, k + 2), 16)),
      bg = rgb(p[0]),
      ink = rgb(p[2]),
      im = c.createImageData(1920, 1080),
      buf = im.data,
      words = new Uint32Array(buf.buffer),
      little = new Uint8Array(new Uint32Array([1]).buffer)[0] === 1;
    // Evaluate the sum-of-inverse-distance field, not a union of painted circles.
    // Four-pixel field cells share one threshold with a narrow antialiased boundary.
    const rows = [];
    for (let y = 0; y < 270; y++) {
      let spans = [],
        run = -1;
      for (let x = 0; x < 480; x++) {
        const v = field(b, x * 4 + 2, y * 4 + 2),
          a = h.clamp((v - 1) * 28 + 0.5);
        if (v >= 1 && run < 0) run = x;
        if ((v < 1 || x === 479) && run >= 0) {
          spans.push([run, x === 479 && v >= 1 ? 480 : x]);
          run = -1;
        }
        const red = Math.round(bg[0] + (ink[0] - bg[0]) * a),
          green = Math.round(bg[1] + (ink[1] - bg[1]) * a),
          blue = Math.round(bg[2] + (ink[2] - bg[2]) * a),
          word = little
            ? (255 << 24) | (blue << 16) | (green << 8) | red
            : (red << 24) | (green << 16) | (blue << 8) | 255,
          offset = y * 4 * 1920 + x * 4;
        words.fill(word >>> 0, offset, offset + 4);
      }
      const row = y * 4 * 1920;
      for (let dy = 1; dy < 4; dy++)
        words.copyWithin(row + dy * 1920, row, row + 1920);
      rows.push(spans);
    }
    c.putImageData(im, 0, 0);
    txt(c, h, d.title, 140, 90, 1640, 78, p[1]);
    if (d.subtitle) txt(c, h, d.subtitle, 140, 988, 1640, 32, p[1]);
    txt(c, h, s.label, 140, 865, 1640, 42, p[1]);
    d.stages.forEach((a, i) => {
      c.fillStyle = a.in <= f ? p[2] : p[3];
      c.fillRect(140 + i * 240, 940, 200, 7);
    });
    // Clip reveal type to the actual instantaneous surface, including early gaps.
    if (s.mode === "resolve" && u > 0.55) {
      c.save();
      c.beginPath();
      rows.forEach((spans, y) =>
        spans.forEach(([x, z]) => c.rect(x * 4, y * 4, (z - x) * 4, 4)),
      );
      c.clip();
      const label = d.stages[3].label,
        px = resolveSize(d);
      c.globalAlpha = h.clamp((u - 0.55) / 0.25);
      h.text(c, label, 960, 550, 650, Math.max(24, px), {
        minSize: 24,
        maxLines: 1,
        height: 65,
        align: "center",
        color: p[0],
      });
      c.restore();
    }
  }

  window.MG_REFERENCE = {
    ...{
      id: "liquid",
      title: "液面连接 · 分流与成形",
      limits:
        "CPU隐式液面参考，不是物理流体或镜面仿真；4px场采样。当前浏览器未获启动验证，字体与视频需另验；无音轨。",
      examples: [
        {
          name: "合力 · 完整末态留读",
          frames: 270,
          data: {
            title: "从想法到合力",
            subtitle: "相遇不是终点，连接也保留个性",
            seed: 3,
            palette: ["#faf5e8", "#202730", "#e66b42", "#49a69d"],
            stages: [
              { in: 0, out: 45, mode: "gather", label: "各自出发", amount: 1 },
              { in: 45, out: 108, mode: "merge", label: "找到连接", amount: 1 },
              {
                in: 108,
                out: 180,
                mode: "split",
                label: "保留个性",
                amount: 1,
              },
              {
                in: 180,
                out: 270,
                mode: "resolve",
                label: "一起向前",
                amount: 1,
              },
            ],
          },
        },
        {
          name: "水旅程 · 完整末态留读",
          frames: 300,
          data: {
            title: "一滴水的旅程",
            subtitle: "汇集、分流、滋养，然后循环新生",
            seed: 7,
            palette: ["#edf8fa", "#192c37", "#277e9b", "#92dbcf"],
            stages: [
              {
                in: 0,
                out: 60,
                mode: "gather",
                label: "水滴汇集",
                amount: 1.1,
              },
              {
                in: 60,
                out: 126,
                mode: "merge",
                label: "聚成水流",
                amount: 1.1,
              },
              {
                in: 126,
                out: 198,
                mode: "split",
                label: "分流滋养",
                amount: 1.1,
              },
              {
                in: 198,
                out: 300,
                mode: "resolve",
                label: "循环新生",
                amount: 1.1,
              },
            ],
          },
        },
      ],
      recipe: {
        "适用/不适用":
          "适用汇聚、连接、分流的抽象过程；不适用声称物理流体仿真。",
        视觉隐喻: "连接来自连续液颈；分开后仍保有各自的动势。",
        "4–6拍": [
          "独立液滴靠近",
          "液颈连续生成",
          "拉伸颈部",
          "延迟次滴分离",
          "液池重聚",
          "文字随液面成形",
        ],
        不可删特征:
          "隐式标量场真实连通颈、边界连续、滞后小滴、结尾文字受液面遮罩、resolve末45%保持完整结果。",
        可替语义:
          "四阶段文案/窗口、共同amount、seed、配色；共同amount保持边界连续。",
        "动作/转场/声音点":
          "融合与断颈点可配湿润短声，结尾持留为阅读；本页无音轨。",
        失败反例: "重叠圆没有液颈、阶段切换半径跳变、文字飘在液体外。",
      },
    },
    validate,
    render,
  };
})();
