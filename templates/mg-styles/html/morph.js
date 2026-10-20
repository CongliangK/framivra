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
  // Semantic windows separate the deformation budget from the readable hold.
  function normalize(d, N, h) {
    check(d, N, h);
    if (!Array.isArray(d.shapes) || d.shapes.length < 2 || d.shapes.length > 5)
      throw Error("2..5 semantic shapes");
    h.windows(d.shapes, N, 10);
    return d.shapes.map((s, i) => {
      if (!["circle", "leaf", "star", "drop", "arrow"].includes(s.shape))
        throw Error("unknown shape");
      h.label(s.label, "label", 30);
      const t = s.transition ?? (i ? 18 : 0);
      h.int(t, "transition", i ? 8 : 0, i ? s.out - s.in - 10 : 0);
      const x = s.x ?? 960,
        y = s.y ?? 550,
        scale = s.scale ?? 1;
      h.num(x, "x", 400, 1520);
      h.num(y, "y", 400, 680);
      h.num(scale, "scale", 0.5, 1.1);
      if (y - 240 * scale < 270 || y + 240 * scale > 850)
        throw Error("shape overlaps reading zones");
      if (s.color !== undefined && !/^#[a-f0-9]{6}$/i.test(s.color))
        throw Error("shape color");
      return {
        ...s,
        x,
        y,
        scale,
        transition: t,
        color: s.color ?? (d.palette ?? PAL)[2 + (i % 2)],
      };
    });
  }
  function validate(d, N, h) {
    normalize(d, N, h);
  }
  // Build named silhouettes, then resample their perimeters to matching arc lengths.
  function contour(name) {
    let p = [];
    if (name === "arrow")
      p = [
        [0, -0.38],
        [0.35, -0.38],
        [0.35, -0.85],
        [1, 0],
        [0.35, 0.85],
        [0.35, 0.38],
        [-1, 0.38],
        [-1, -0.38],
      ];
    else
      for (let i = 0; i < 256; i++) {
        const a = -Math.PI / 2 + (TAU * i) / 256;
        let x, y;
        if (name === "circle") {
          x = Math.cos(a);
          y = Math.sin(a);
        } else if (name === "star") {
          let r = 0.68 + 0.32 * Math.cos(5 * (a + Math.PI / 2));
          x = r * Math.cos(a);
          y = r * Math.sin(a);
        } else if (name === "leaf") {
          x = Math.cos(a) * Math.abs(Math.cos(a)) ** 0.5 * 0.73;
          y = Math.sin(a);
        } else {
          let r = 0.72 + 0.28 * Math.sin(a);
          x = r * Math.cos(a);
          y = r * Math.sin(a) - 0.28 * Math.max(0, -Math.sin(a)) ** 8;
        }
        p.push([x, y]);
      }
    const loop = [...p, p[0]],
      lens = p.map((a, i) =>
        Math.hypot(loop[i + 1][0] - a[0], loop[i + 1][1] - a[1]),
      ),
      total = lens.reduce((a, b) => a + b, 0),
      pts = [];
    let edge = 0,
      passed = 0;
    for (let i = 0; i < 128; i++) {
      const dist = (i / 128) * total;
      while (edge < lens.length - 1 && passed + lens[edge] < dist) {
        passed += lens[edge];
        edge++;
      }
      const q = (dist - passed) / Math.max(0.0001, lens[edge]);
      pts.push(loop[edge].map((a, k) => a + (loop[edge + 1][k] - a) * q));
    }
    return pts;
  }
  // Old outline → compressed round mediator → next outline; hold the named result.
  function render(c, d, f, N, h) {
    h.int(f, "frame", 0, N - 1);
    const ss = normalize(d, N, h),
      p = d.palette ?? PAL,
      i = ss.findIndex((s) => s.in <= f && f < s.out),
      s = ss[i],
      old = ss[Math.max(0, i - 1)],
      t = s.transition ? h.clamp((f - s.in) / s.transition) : 1,
      q = h.smooth(t),
      u = h.smooth(t < 0.5 ? 2 * t : 2 * t - 1),
      a = contour(t < 0.5 ? old.shape : "circle"),
      b = contour(t < 0.5 ? "circle" : s.shape),
      sq = 1 - 0.1 * Math.sin(Math.PI * t),
      x = h.lerp(old.x, s.x, q),
      y = h.lerp(old.y, s.y, q),
      sc = h.lerp(old.scale, s.scale, q);
    c.fillStyle = p[0];
    c.fillRect(0, 0, 1920, 1080);
    txt(c, h, d.title, 140, 90, 1640, 78, p[1]);
    const rgb = (color) =>
        [1, 3, 5].map((k) => parseInt(color.slice(k, k + 2), 16)),
      ca = rgb(old.color),
      cb = rgb(s.color),
      color = `rgb(${ca.map((v, k) => Math.round(h.lerp(v, cb[k], q))).join(",")})`;
    // Matching arc-length samples remain one closed contour; no crossfade of icons.
    const pts = a.map((v, j) => [
      x + (h.lerp(v[0], b[j][0], u) * 240 * sc) / sq,
      y + h.lerp(v[1], b[j][1], u) * 240 * sc * sq,
    ]);
    h.polygon(c, pts, color);
    c.lineWidth = 5;
    c.strokeStyle = p[1];
    h.polygon(c, pts);
    const label = t < 1 && i ? old.label + " → " + s.label : s.label;
    txt(c, h, label, 160, 900, 1600, 48, p[1]);
    ss.forEach((s, j) => {
      c.fillStyle = j <= i ? p[2] : p[3];
      c.fillRect(160 + j * 160, 1000, 120, 6);
    });
  }

  window.MG_REFERENCE = {
    ...{
      id: "morph",
      title: "连续轮廓 · 让概念变形",
      limits:
        "原创建模参考，复用媒体语义 schema；浏览器字体、实际视频时序和音轨仍需单独验收。本页无声音。",
      examples: [
        {
          name: "想法生长",
          frames: 240,
          data: {
            title: "让一个想法生长",
            palette: ["#faf5e8", "#202730", "#e66b42", "#49a69d"],
            shapes: [
              {
                shape: "circle",
                label: "一个起点",
                in: 0,
                out: 60,
                color: "#e66b42",
              },
              {
                shape: "leaf",
                label: "向外生长",
                in: 60,
                out: 144,
                transition: 24,
                color: "#49a69d",
              },
              {
                shape: "star",
                label: "让可能发光",
                in: 144,
                out: 240,
                transition: 30,
                color: "#e7b347",
              },
            ],
          },
        },
        {
          name: "汇聚到出发",
          frames: 180,
          data: {
            title: "从汇聚到出发",
            palette: ["#edf8fa", "#192c37", "#277e9b", "#92dbcf"],
            shapes: [
              {
                shape: "drop",
                label: "汇聚一点力量",
                in: 0,
                out: 48,
                x: 810,
                color: "#277e9b",
              },
              {
                shape: "circle",
                label: "形成共同方向",
                in: 48,
                out: 108,
                transition: 20,
                x: 960,
                color: "#43a9a0",
              },
              {
                shape: "arrow",
                label: "现在向前出发",
                in: 108,
                out: 180,
                transition: 24,
                x: 1110,
                color: "#d48c47",
              },
            ],
          },
        },
      ],
      recipe: {
        "适用/不适用": "适用概念之间有关系的演变；不适用无关联图标轮播。",
        视觉隐喻: "同一个物体通过圆形中介改变身份。",
        "4–6拍": [
          "建立第一轮廓",
          "压缩蓄势",
          "经过圆形中介",
          "展开下一语义",
          "色彩与位置跟随",
          "终态留读",
        ],
        不可删特征: "等弧长对应、完整单闭环、轮廓变形承担转场、终态持留。",
        可替语义:
          "shape/label/color/x/y/scale 与整数窗口、transition；不是统一卡片。",
        "动作/转场/声音点":
          "圆形中介压缩处可配弹性声，终态落点留白；本页无音轨。",
        编辑练习: "把shapes的具名链改为leaf→star→arrow并替换各label，将最后一拍out与总帧数一起后移30帧，检查每次transition确实变形且最终箭头仍有至少10帧完整hold。",
        失败反例: "两张图标交叉淡化、尖角穿结、标题挤入图形。",
      },
    },
    validate,
    render,
  };
})();
