/* Analytic Canvas optical reference; original implementation. */
(() => {
  "use strict";
  const examples = [
    {
      name: "澄光：从声音到专注",
      frames: 300,
      data: {
        title: "澄光 · 为灵感留一点空间",
        subtitle: "说出想法，让思绪找到清晰的下一步",
        seed: 31,
        chapters: [
          {
            in: 0,
            out: 90,
            mode: "voice",
            label: "先把想法说出来",
            detail: "捕捉还没成形的念头",
            value: 64,
            unit: "秒灵感",
            reveal_frames: 18,
            hold_frames: 60,
          },
          {
            in: 90,
            out: 180,
            mode: "chat",
            label: "一起理清线索",
            detail: "将零散片段汇成建议",
            value: 12,
            unit: "条建议",
            reveal_frames: 24,
            hold_frames: 60,
          },
          {
            in: 180,
            out: 300,
            mode: "focus",
            label: "把注意力留给创造",
            detail: "少一点打扰，多一点专注",
            value: 88,
            unit: "专注值",
            reveal_frames: 30,
            hold_frames: 75,
          },
        ],
      },
    },
    {
      name: "晚霞：回顾与留言",
      frames: 300,
      data: {
        title: "晚霞 · 整理今天的收获",
        subtitle: "留住声音，理解片段，安排下一次出发",
        palette: ["#28131e", "#fff1e9", "#d97976", "#cc9955"],
        seed: -11,
        lens: { strength: 0.3, dispersion: 1.1, radius: 180 },
        chapters: [
          {
            in: 0,
            out: 150,
            mode: "chat",
            label: "回看今天",
            detail: "三条值得记住的小事",
            value: 3,
            unit: "段记录",
            reveal_frames: 30,
            hold_frames: 100,
          },
          {
            in: 150,
            out: 300,
            mode: "voice",
            label: "给明天留一句话",
            detail: "让期待拥有自己的声音",
            value: 76,
            unit: "秒留言",
            reveal_frames: 30,
            hold_frames: 100,
          },
        ],
      },
    },
  ];
  const rgb = (s) => [1, 3, 5].map((i) => parseInt(s.slice(i, i + 2), 16));
  // 章节窗口与揭示/保持预算先验证，再开始任何绘制。
  function validate(d, N, h) {
    h.int(N, "frames", 90, 18000);
    h.label(d.title, "title", 120);
    h.label(d.subtitle, "subtitle", 120);
    h.palette(d.palette || ["#111226", "#f3f0ff", "#7161d9", "#46aabf"]);
    const palette = d.palette || ["#111226", "#f3f0ff", "#7161d9", "#46aabf"];
    function hue(hex) {
      const [r, g, b] = rgb(hex).map((v) => v / 255),
        max = Math.max(r, g, b),
        min = Math.min(r, g, b),
        delta = max - min;
      if (!delta) return 0;
      let q =
        max === r
          ? (g - b) / delta
          : max === g
            ? (b - r) / delta + 2
            : (r - g) / delta + 4;
      return (((q / 6) % 1) + 1) % 1;
    }
    const gap = Math.abs(hue(palette[2]) - hue(palette[3]));
    if (Math.min(gap, 1 - gap) > 1 / 3) throw Error("极光需邻近色相");
    h.int(d.seed ?? 0, "seed", -2147483648, 2147483647);
    h.windows(d.chapters, N, 30);
    if (d.chapters.length < 2 || d.chapters.length > 4)
      throw Error("需要2–4个语义章节");
    for (const s of d.chapters) {
      if (!["chat", "voice", "focus"].includes(s.mode))
        throw Error("未知微UI语义");
      for (const k of ["label", "detail", "unit"])
        h.label(s[k], k, k === "detail" ? 70 : k === "unit" ? 12 : 40);
      h.num(s.value, "value", 0, 100);
      const reveal = s.reveal_frames ?? Math.min(30, s.out - s.in - 10),
        hold = s.hold_frames ?? Math.min(30, s.out - s.in - reveal);
      h.int(reveal, "reveal_frames", 8, s.out - s.in - 6);
      h.int(hold, "hold_frames", 6, s.out - s.in - reveal);
    }
    if (d.lens) {
      h.num(d.lens.strength ?? 0.24, "lens strength", 0.05, 0.4);
      h.num(d.lens.dispersion ?? 0.7, "dispersion", 0.1, 1.5);
      h.num(d.lens.radius ?? 165, "radius", 100, 210);
    }
  }
  function rounded(c, x, y, w, ht, r, fill, stroke) {
    c.beginPath();
    c.roundRect(x, y, w, ht, r);
    if (fill) {
      c.fillStyle = fill;
      c.fill();
    }
    if (stroke) {
      c.strokeStyle = stroke;
      c.lineWidth = 1.5;
      c.stroke();
    }
  }
  function corners(cx, cy, w, ht, yaw, pitch, depth) {
    return [
      [-w / 2, -ht / 2],
      [w / 2, -ht / 2],
      [w / 2, ht / 2],
      [-w / 2, ht / 2],
    ].map(([x, y]) => {
      const xx = x * Math.cos(yaw),
        z = -x * Math.sin(yaw),
        yy = y * Math.cos(pitch) - z * Math.sin(pitch),
        zz = y * Math.sin(pitch) + z * Math.cos(pitch) + depth,
        k = 1200 / (1200 + zz);
      return [cx + xx * k, cy + yy * k];
    });
  }
  function projectMesh(c, image, quad) {
    const [a, b, d, e] = quad;
    function p(u, v) {
      const top = [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u],
        bottom = [e[0] + (d[0] - e[0]) * u, e[1] + (d[1] - e[1]) * u];
      return [
        top[0] + (bottom[0] - top[0]) * v,
        top[1] + (bottom[1] - top[1]) * v,
      ];
    }
    function tri(src, dst) {
      const [s0, s1, s2] = src,
        [p0, p1, p2] = dst;
      const det =
        (s1[0] - s0[0]) * (s2[1] - s0[1]) - (s2[0] - s0[0]) * (s1[1] - s0[1]);
      if (Math.abs(det) < 1e-8) return;
      const A =
          ((p1[0] - p0[0]) * (s2[1] - s0[1]) -
            (p2[0] - p0[0]) * (s1[1] - s0[1])) /
          det,
        B =
          ((p1[1] - p0[1]) * (s2[1] - s0[1]) -
            (p2[1] - p0[1]) * (s1[1] - s0[1])) /
          det,
        C =
          ((p2[0] - p0[0]) * (s1[0] - s0[0]) -
            (p1[0] - p0[0]) * (s2[0] - s0[0])) /
          det,
        D =
          ((p2[1] - p0[1]) * (s1[0] - s0[0]) -
            (p1[1] - p0[1]) * (s2[0] - s0[0])) /
          det;
      c.save();
      c.beginPath();
      dst.forEach(([x, y], i) => (i ? c.lineTo(x, y) : c.moveTo(x, y)));
      c.closePath();
      c.clip();
      c.transform(
        A,
        B,
        C,
        D,
        p0[0] - A * s0[0] - C * s0[1],
        p0[1] - B * s0[0] - D * s0[1],
      );
      c.drawImage(image, 0, 0);
      c.restore();
    }
    for (let j = 0; j < 8; j++)
      for (let i = 0; i < 12; i++) {
        const u = i / 12,
          v = j / 8,
          u1 = (i + 1) / 12,
          v1 = (j + 1) / 8,
          s = [
            [u * image.width, v * image.height],
            [u1 * image.width, v * image.height],
            [u1 * image.width, v1 * image.height],
            [u * image.width, v1 * image.height],
          ],
          q = [p(u, v), p(u1, v), p(u1, v1), p(u, v1)];
        tri([s[0], s[1], s[2]], [q[0], q[1], q[2]]);
        tri([s[0], s[2], s[3]], [q[0], q[2], q[3]]);
      }
  }
  function card(c, s, f, h, options) {
    const { cx, cy, w, ht, yaw, pitch, depth, small } = options;
    const off = new OffscreenCanvas(w, ht),
      q = off.getContext("2d");
    q.save();
    q.beginPath();
    q.roundRect(1, 1, w - 2, ht - 2, 28);
    q.clip();
    q.filter = "blur(22px)";
    q.drawImage(c.canvas, cx - w / 2, cy - ht / 2, w, ht, 0, 0, w, ht);
    q.filter = "none";
    q.restore();
    rounded(
      q,
      1,
      1,
      w - 2,
      ht - 2,
      28,
      "rgba(235,242,255,.14)",
      "rgba(255,255,255,.4)",
    );
    const pad = small ? 28 : 40;
    h.text(q, s.label, pad, 28, w - pad * 2, small ? 28 : 38, {
      minSize: 24,
      maxLines: 2,
      height: 82,
      color: "#fff8ff",
    });
    const local = f - s.in,
      reveal = s.reveal_frames ?? Math.min(30, s.out - s.in - 10),
      u = h.clamp(local / Math.max(1, reveal - 1)),
      entry = h.clamp(
        local / Math.min(14, Math.max(1, Math.floor(reveal / 2))),
      );
    q.save();
    q.globalAlpha = small ? 1 : entry;
    h.text(
      q,
      s.detail,
      pad,
      small ? 104 : 104 + 8 * (1 - entry),
      w - pad * 2,
      small ? 24 : 27,
      { minSize: 24, maxLines: 2, height: 72, color: "#fff8ff" },
    );
    if (!small) {
      const n =
        (u >= 1 ? s.value : Math.round(s.value * u)).toString() + " " + s.unit;
      if (s.mode === "chat") {
        rounded(
          q,
          pad,
          178,
          w - pad * 2,
          100,
          22,
          "rgba(255,255,255,.12)",
          "rgba(255,255,255,.18)",
        );
        h.text(q, n, pad + 20, 200, w - pad * 2 - 40, 44, {
          minSize: 24,
          height: 65,
          color: "#fff8ff",
        });
      } else if (s.mode === "voice") {
        for (let i = 0; i < 43; i++) {
          const x = pad + (i * (w - pad * 2)) / 43,
            amp =
              10 +
              (50 *
                (0.25 + 0.75 * Math.abs(Math.sin(i * 0.63 + f * 0.07))) *
                s.value) /
                100;
          rounded(q, x, 240 - amp, 5, amp * 2, 2, "rgba(240,246,255,.85)");
        }
        h.text(q, n, pad, 340, w - pad * 2, 32, {
          minSize: 24,
          height: 48,
          color: "#fff8ff",
        });
      } else {
        for (let i = 0; i < 3; i++) {
          const y = 185 + i * 58,
            on = local >= Math.round(((reveal - 1) * (i + 1)) / 3);
          rounded(q, pad, y, w - pad * 2, 42, 13, "rgba(255,255,255,.10)");
          rounded(
            q,
            w - pad - 78,
            y + 8,
            60,
            26,
            13,
            on ? "#9edecf" : "#7d86a3",
          );
          h.circle(q, w - pad - (on ? 29 : 65), y + 21, 9, "#fff8ff");
        }
        h.text(q, n, pad, 378, w - pad * 2, 28, {
          minSize: 24,
          height: 40,
          color: "#fff8ff",
        });
      }
    }
    q.restore();
    projectMesh(c, off, corners(cx, cy, w, ht, yaw, pitch, depth));
  }
  function lens(c, cx, cy, r, strength, dispersion, phase) {
    const x0 = Math.max(0, Math.floor(cx - r - 2)),
      y0 = Math.max(0, Math.floor(cy - r - 2)),
      w = Math.min(1920 - x0, Math.ceil(r * 2 + 4)),
      ht = Math.min(1080 - y0, Math.ceil(r * 2 + 4)),
      source = c.getImageData(x0, y0, w, ht),
      output = c.createImageData(w, ht);
    output.data.set(source.data);
    function sample(x, y, ch) {
      x = Math.max(0, Math.min(w - 1, x));
      y = Math.max(0, Math.min(ht - 1, y));
      const ix = Math.floor(x),
        iy = Math.floor(y),
        jx = Math.min(w - 1, ix + 1),
        jy = Math.min(ht - 1, iy + 1),
        fx = x - ix,
        fy = y - iy;
      return (
        source.data[(iy * w + ix) * 4 + ch] * (1 - fx) * (1 - fy) +
        source.data[(iy * w + jx) * 4 + ch] * fx * (1 - fy) +
        source.data[(jy * w + ix) * 4 + ch] * (1 - fx) * fy +
        source.data[(jy * w + jx) * 4 + ch] * fx * fy
      );
    }
    for (let y = 0; y < ht; y++)
      for (let x = 0; x < w; x++) {
        const u = (x + x0 - cx) / r,
          v = (y + y0 - cy) / r,
          rr = Math.hypot(u, v);
        if (rr >= 1) continue;
        const radial = Math.sqrt(1 - rr * rr),
          nx = u * radial + 0.055 * Math.sin(v * 4 + phase) * radial,
          ny = v * radial + 0.04 * Math.sin(u * 5 - phase) * radial,
          dx = -strength * r * nx,
          dy = -strength * r * ny,
          rim =
            Math.exp(-Math.pow((rr - 0.88) / 0.12, 2)) * dispersion * r * 0.026,
          spec =
            Math.exp(-Math.pow((rr - 0.965) / 0.022, 2)) *
            Math.max(0, Math.min(1, 0.7 - u * 0.4 - v * 0.6)) *
            80,
          cover = Math.min(1, ((1 - rr) * r) / 1.5),
          idx = (y * w + x) * 4;
        for (let k = 0; k < 3; k++) {
          const lambda = 1 - k;
          output.data[idx + k] = Math.max(
            0,
            Math.min(
              255,
              source.data[idx + k] * (1 - cover) +
                (sample(
                  x + dx + lambda * rim * u,
                  y + dy + lambda * rim * v,
                  k,
                ) +
                  spec) *
                  cover,
            ),
          );
        }
        output.data[idx + 3] = 255;
      }
    c.putImageData(output, x0, y0);
  }
  // 光场、透视卡片与语义微界面共享纯整数帧；解析折射不是物理路径追踪。
  function render(c, d, f, N, h) {
    const colors = d.palette || ["#111226", "#f3f0ff", "#7161d9", "#46aabf"],
      p = f / (N - 1),
      bias = (((d.seed ?? 0) % 97) / 97) * Math.PI * 2;
    c.fillStyle = colors[0];
    c.fillRect(0, 0, 1920, 1080);
    c.save();
    c.globalCompositeOperation = "screen";
    for (let i = 0; i < 4; i++) {
      const x =
          1920 * (0.2 + 0.6 * (0.5 + 0.5 * Math.sin(p * 1.4 + bias + i * 1.9))),
        y =
          1080 *
          (0.25 + 0.5 * (0.5 + 0.5 * Math.cos(p * 1.1 + bias * 0.6 + i * 2.1))),
        g = c.createRadialGradient(x, y, 0, x, y, 590 + i * 42),
        col = rgb(colors[2 + (i % 2)]);
      g.addColorStop(0, `rgba(${col.join(",")},.50)`);
      g.addColorStop(1, `rgba(${col.join(",")},0)`);
      c.fillStyle = g;
      c.fillRect(0, 0, 1920, 1080);
    }
    c.restore();
    c.fillStyle = "rgba(255,255,255,.025)";
    for (let i = 0; i < 2500; i++) {
      const x = (((i * 491 + (d.seed ?? 0) * 13) % 1920) + 1920) % 1920,
        y = (((i * 137 + (d.seed ?? 0) * 7) % 1080) + 1080) % 1080;
      c.fillRect(x, y, 1, 1);
    }
    const index = d.chapters.findIndex((s) => s.in <= f && f < s.out),
      s = d.chapters[index],
      drift = Math.sin(p * Math.PI) * 28;
    card(c, d.chapters[Math.max(0, index - 1)], f, h, {
      cx: 470 + drift * 0.55,
      cy: 610,
      w: 360,
      ht: 235,
      yaw: -0.18,
      pitch: 0.1,
      depth: 140,
      small: true,
    });
    card(c, s, f, h, {
      cx: 960 + drift * 0.8,
      cy: 575,
      w: 700,
      ht: 440,
      yaw: 0.1 * Math.sin(p * 2.2 + 0.3),
      pitch: -0.055,
      depth: 0,
      small: false,
    });
    card(c, d.chapters[Math.min(d.chapters.length - 1, index + 1)], f, h, {
      cx: 1490 + drift,
      cy: 660,
      w: 350,
      ht: 230,
      yaw: 0.2,
      pitch: -0.08,
      depth: -45,
      small: true,
    });
    const optics = d.lens || {};
    lens(
      c,
      530 + 910 * (0.5 - 0.5 * Math.cos(Math.PI * p)),
      590 + 85 * Math.sin(p * Math.PI * 1.4),
      optics.radius ?? 165,
      optics.strength ?? 0.24,
      optics.dispersion ?? 0.7,
      p * 2,
    );
    h.text(c, d.title, 960, 72, 1640, 80, {
      minSize: 32,
      align: "center",
      height: 110,
      color: colors[1],
    });
    h.text(c, d.subtitle, 960, 190, 1600, 31, {
      minSize: 24,
      align: "center",
      height: 46,
      color: colors[1],
    });
    h.text(c, s.label, 960, 930, 1500, 42, {
      minSize: 24,
      align: "center",
      height: 65,
      color: colors[1],
    });
  }
  window.MG_REFERENCE = {
    id: "aurora-glass",
    title: "极光玻璃：让光场聚焦语义",
    limits:
      "解析图像空间折射/色散与透视平面参考；不是物理path tracing，也不是可交互产品。需支持OffscreenCanvas的现代浏览器；本环境没有重启浏览器验收。",
    examples,
    validate,
    render,
    recipe: {
      编辑练习:
        "将 chapter.mode 从 chat 计数改成 focus 开关；重新设置 reveal_frames 与 hold_frames，并延长该章 out（随后章节 in/out 一并平移），确保 reveal + hold 不超过章节时长，最后一个开关打开后仍有完整保持。",
      "适用/不适用":
        "适合短产品概念、思考工具、缓慢信息聚焦；不适合密集表格、真实光学精度或完整交互系统。",
      视觉隐喻: "思绪先成为光场，玻璃层承载短语义，镜片让注意力经过信息。",
      "4–6拍": [
        "邻色光场先建立空间",
        "前后卡片有不同深度",
        "声音/对话/专注逐章出现",
        "镜片重采样折射与色散",
        "控件完成后留出明确阅读hold",
      ],
      不可删特征:
        "慢漂移、透明边界、透视/视差、非均匀采样折射、短微UI语义与完成态停留。",
      可替语义:
        "title/subtitle、chapters的mode/label/detail/value/unit、显式reveal_frames/hold_frames、邻色palette和镜片参数。",
      "动作/转场/声音点":
        "光场与镜片承接章节；文字仅轻升/渐显；数值/开关完成点可作为细微提示音cue，听审另做。",
      失败反例:
        "只做blur冒充折射；换色不换微UI关系；最后一帧才开最后按钮；长段正文挤进玻璃卡。",
    },
  };
  window.MG_REFERENCE.inspect = (d, f, N, h) => {
    const s = h.stage(d.chapters, f),
      r = s.reveal_frames ?? Math.min(30, s.out - s.in - 10),
      local = f - s.in,
      q = h.clamp(local / Math.max(1, r - 1));
    return {
      mode: s.mode,
      displayed: q >= 1 ? s.value : Math.round(s.value * q),
      enabled: [0, 1, 2].map(
        (i) => local >= Math.round(((r - 1) * (i + 1)) / 3),
      ),
      completed: f >= s.in + r - 1,
      held_frames: s.out - (s.in + r - 1),
    };
  };
})();
