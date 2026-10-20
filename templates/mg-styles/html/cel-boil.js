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

  const PAL = ["#faf2df", "#2c302b", "#ef8a66", "#95b6a3"];
  // Exposure sheet: reject budgets that would sample away preparation or flight.
  function prepare(d, N, h) {
    check(d, N, h);
    h.int(d.seed ?? 1, "seed", -2147483648, 2147483647);
    if (d.subtitle) h.label(d.subtitle, "subtitle", 68);
    if (
      !Array.isArray(d.characters) ||
      d.characters.length < 2 ||
      d.characters.length > 4
    )
      throw Error("2..4 characters");
    const ids = new Set();
    d.characters.forEach((a) => {
      h.label(a.id, "id", 24);
      h.label(a.label, "actor label", d.characters.length === 4 ? 14 : 24);
      if (
        ids.has(a.id) ||
        !["blob", "cat", "star", "sprout"].includes(a.shape) ||
        !["joy", "curious", "calm", "surprise"].includes(a.emotion ?? "joy")
      )
        throw Error("invalid character");
      h.int(a.size ?? 96, "size", 60, 130);
      ids.add(a.id);
    });
    const e = d.exposures ?? { action: 1, body: 2, hold: 3 };
    if (Object.keys(e).sort().join() !== "action,body,hold")
      throw Error("exposures");
    Object.entries(e).forEach(([k, v]) => h.int(v, k, 1, 3));
    if (!Array.isArray(d.beats) || d.beats.length < 2 || d.beats.length > 16)
      throw Error("2..16 beats");
    h.windows(d.beats, N, 9);
    const offsets = Object.fromEntries([...ids].map((id) => [id, 0]));
    const beats = d.beats.map((b) => {
      if (
        !ids.has(b.actor) ||
        !["pop", "hop", "stretch", "settle"].includes(b.action)
      )
        throw Error("cue actor/action");
      const dur = b.out - b.in,
        ant = b.anticipation ?? Math.max(2, Math.floor(dur / 6)),
        motion = b.motion ?? Math.max(4, Math.floor(dur / 3)),
        dist = b.distance ?? (b.action === "hop" ? 48 : 0);
      h.int(ant, "anticipation", e.body + 1, dur - 7);
      h.int(motion, "motion", Math.max(4, 2 * e.action + 1), dur - ant - 3);
      h.int(dist, "distance", -80, 80);
      if (b.action !== "hop" && dist) throw Error("only hop has distance");
      const offset = offsets[b.actor];
      offsets[b.actor] += dist;
      return { ...b, anticipation: ant, motion, distance: dist, offset };
    });
    d.characters.forEach((a, i) => {
      const cx = 160 + (1600 / d.characters.length) * (i + 0.5),
        r = (a.size ?? 96) * 1.65 + 35;
      [
        0,
        ...beats
          .filter((b) => b.actor === a.id)
          .map((b) => b.offset + b.distance),
      ].forEach((x) => {
        if (cx + x - r < 80 || cx + x + r > 1840)
          throw Error("motion outside stage");
      });
    });
    return { beats, e };
  }
  function validate(d, N, h) {
    prepare(d, N, h);
  }
  function silhouette(s) {
    if (s === "blob")
      return Array.from({ length: 64 }, (_, i) => {
        const a = (i / 64) * TAU;
        return [Math.cos(a) * (1 + 0.06 * Math.sin(3 * a)), 0.88 * Math.sin(a)];
      });
    if (s === "star")
      return Array.from({ length: 10 }, (_, i) => {
        const a = -Math.PI / 2 + (i / 10) * TAU,
          r = i % 2 ? 0.49 : 1;
        return [r * Math.cos(a), r * Math.sin(a)];
      });
    return s === "cat"
      ? [
          [-0.8, 0.6],
          [-1, 0],
          [-0.8, -0.5],
          [-0.75, -1],
          [-0.2, -0.7],
          [0.2, -0.7],
          [0.75, -1],
          [0.8, -0.5],
          [1, 0],
          [0.8, 0.6],
          [0.4, 0.85],
          [-0.4, 0.85],
        ]
      : [
          [-0.6, 0.7],
          [-0.75, 0.2],
          [-0.48, -0.15],
          [-0.06, -0.1],
          [-0.12, -0.55],
          [-0.65, -0.55],
          [-0.85, -0.95],
          [-0.28, -0.95],
          [0, -0.65],
          [0.28, -0.95],
          [0.85, -0.95],
          [0.65, -0.55],
          [0.12, -0.55],
          [0.06, -0.1],
          [0.48, -0.15],
          [0.75, 0.2],
          [0.6, 0.7],
        ];
  }
  // Action drawings: preparation → motion (two short smear cels) → settled pose.
  function pose(beats, id, t, size) {
    const b = beats.filter((b) => b.actor === id && b.in <= t).at(-1),
      p = { x: 0, y: 0, sx: 1, sy: 1, angle: 0, smear: false, fx: 0 };
    if (!b) return p;
    p.x = b.offset;
    const elapsed = t - b.in;
    if (elapsed < b.anticipation) {
      const q = elapsed / (b.anticipation - 1);
      return {
        ...p,
        sx: 1 - (b.action === "pop" ? 0.7 : -0.18) * q,
        sy: 1 - (b.action === "pop" ? 0.7 : 0.2) * q,
        y: size * (b.action === "pop" ? 0.45 : 0.18) * q,
        angle: -0.08 * q,
      };
    }
    const tick = elapsed - b.anticipation,
      q = Math.min(1, tick / (b.motion - 1)),
      wave = Math.sin(Math.PI * q);
    if (b.action === "hop") {
      p.x += b.distance * q;
      p.y = -110 * wave;
      p.sx = 1 - 0.12 * wave;
      p.sy = 1 + 0.18 * wave;
      p.angle = 0.08 * Math.sin(q * TAU);
    } else if (b.action === "stretch") {
      p.sx = 1 - 0.25 * wave;
      p.sy = 1 + 0.55 * wave;
      p.y = -size * 0.2 * wave;
    } else if (b.action === "pop") {
      let g =
        0.3 +
        0.7 * Math.min(1, q * 2.3) +
        0.17 * Math.sin(Math.min(1, q * 1.6) * Math.PI);
      p.sx = g * (1 + 0.08 * Math.sin(q * TAU));
      p.sy = g * (1 - 0.08 * Math.sin(q * TAU));
      p.y = size * 0.45 * (1 - Math.min(1, q * 2.3));
    } else {
      let decay = (1 - q) ** 2;
      p.sx = 1 + 0.2 * Math.cos(q * Math.PI * 3) * decay;
      p.sy = 1 - 0.18 * Math.cos(q * Math.PI * 3) * decay;
      p.angle = 0.1 * Math.sin(q * Math.PI * 3) * decay;
    }
    if (tick >= 2 && tick <= 3 && b.action !== "settle") {
      p.smear = true;
      p.sx *= b.action === "hop" ? 1.35 : 0.82;
      p.sy *= b.action === "hop" ? 0.84 : 1.3;
    }
    if (tick >= b.motion - 1 && tick < b.motion + 7)
      p.fx = 1 - (tick - b.motion + 1) / 8;
    if (tick >= b.motion)
      Object.assign(p, { sx: 1, sy: 1, y: 0, angle: 0, smear: false });
    return p;
  }
  // Sample first, then derive every actor/FX/boil from that same cel address.
  function render(c, d, f, N, h) {
    const p = prepare(d, N, h);
    h.int(f, "frame", 0, N - 1);
    const b = h.stage(p.beats, f),
      kind =
        f < b.in + b.anticipation
          ? "body"
          : f < b.in + b.anticipation + b.motion
            ? "action"
            : "hold",
      start =
        kind === "body"
          ? b.in
          : kind === "action"
            ? b.in + b.anticipation
            : b.in + b.anticipation + b.motion,
      t = start + Math.floor((f - start) / p.e[kind]) * p.e[kind],
      pal = d.palette ?? PAL;
    c.fillStyle = pal[0];
    c.fillRect(0, 0, 1920, 1080);
    txt(c, h, d.title, 140, 90, 1640, 76, pal[1], 2);
    if (d.subtitle) txt(c, h, d.subtitle, 140, 988, 1640, 32, pal[1]);
    // One held drawing address drives geometry, FX and seeded contour jitter together.
    d.characters.forEach((a, i) => {
      const size = a.size ?? 96,
        poseNow = pose(p.beats, a.id, t, size),
        cx = 160 + (1600 / d.characters.length) * (i + 0.5),
        cy = 600;
      const point = (x, y) => {
          x *= size * poseNow.sx;
          y *= size * poseNow.sy;
          return [
            cx +
              poseNow.x +
              x * Math.cos(poseNow.angle) -
              y * Math.sin(poseNow.angle),
            cy +
              poseNow.y +
              x * Math.sin(poseNow.angle) +
              y * Math.cos(poseNow.angle),
          ];
        },
        pts = silhouette(a.shape).map(([x, y], j) => {
          let boil = (noise((d.seed ?? 1) + i * 79, t, j) * 1.6) / size;
          return point(x + boil, y + boil * 0.7);
        });
      h.polygon(c, pts, pal[2 + (i % 2)]);
      c.lineWidth = 5;
      c.strokeStyle = pal[1];
      h.polygon(c, pts);
      line(
        c,
        pts
          .map(([x, y], j) => [x + noise(d.seed ?? 1, t, j + 200) * 2, y + 2])
          .concat([pts[0]]),
        pal[1],
        1,
      );
      const emo = a.emotion ?? "joy";
      [-1, 1].forEach((side) => {
        if (["joy", "calm"].includes(emo))
          line(
            c,
            [
              point(side * 0.25 - 0.09, -0.12),
              point(side * 0.25, emo === "joy" ? -0.18 : -0.08),
              point(side * 0.25 + 0.09, -0.12),
            ],
            pal[1],
            4,
          );
        else {
          const [x, y] = point(side * 0.25, -0.14);
          h.circle(
            c,
            x,
            y,
            emo === "surprise" ? 6 : side === 1 ? 7 : 4,
            pal[1],
          );
        }
      });
      if (emo === "surprise") {
        const [x, y] = point(0, 0.22);
        c.lineWidth = 4;
        c.strokeStyle = pal[1];
        h.circle(c, x, y, 10);
      } else
        line(
          c,
          [
            point(-0.13, 0.17),
            point(0, emo === "joy" ? 0.26 : 0.18),
            point(0.13, 0.17),
          ],
          pal[1],
          4,
        );
      if (poseNow.smear)
        for (let j = 0; j < 3; j++) {
          const [x, y] = point(-1.2 - j * 0.13, -0.3 + j * 0.3);
          line(
            c,
            [
              [x - 24 - j * 7, y],
              [x - 3, y - 3],
            ],
            pal[1],
            3,
          );
        }
      if (poseNow.fx)
        for (let j = 0; j < 6; j++) {
          const ang = (j / 6) * TAU,
            r = size * (1.12 + 0.22 * (1 - poseNow.fx)),
            x = cx + poseNow.x + Math.cos(ang) * r,
            y = cy + poseNow.y + Math.sin(ang) * r;
          line(
            c,
            [
              [x, y],
              [
                x + Math.cos(ang) * 18 * poseNow.fx,
                y + Math.sin(ang) * 18 * poseNow.fx,
              ],
            ],
            pal[1],
            3,
          );
        }
      txt(
        c,
        h,
        a.label,
        cx - 700 / d.characters.length,
        805,
        1400 / d.characters.length,
        36,
        pal[1],
        3,
      );
      line(
        c,
        [
          [cx - 40, 755],
          [cx + 40, 755],
        ],
        pal[1],
        2,
      );
    });
  }

  window.MG_REFERENCE = {
    ...{
      id: "cel-boil",
      title: "手绘逐格 · 让角色行动",
      limits:
        "原创建模参考，复用媒体语义 schema；浏览器字体、实际视频时序和音轨仍需单独验收。本页无声音。",
      examples: [
        {
          name: "萌芽和陪伴",
          frames: 240,
          data: {
            title: "小小勇气，慢慢长大",
            subtitle: "预备、尝试、落地；每一步都有自己的节奏。",
            seed: -2097,
            characters: [
              {
                id: "bud",
                shape: "sprout",
                label: "萌芽：先试试看",
                emotion: "curious",
                size: 108,
              },
              {
                id: "cat",
                shape: "cat",
                label: "陪伴：一起出发",
                emotion: "joy",
                size: 102,
              },
            ],
            beats: [
              {
                actor: "bud",
                action: "pop",
                in: 0,
                out: 60,
                anticipation: 10,
                motion: 20,
              },
              {
                actor: "cat",
                action: "hop",
                in: 60,
                out: 120,
                anticipation: 10,
                motion: 22,
                distance: 60,
              },
              {
                actor: "bud",
                action: "stretch",
                in: 120,
                out: 180,
                anticipation: 12,
                motion: 22,
              },
              {
                actor: "cat",
                action: "settle",
                in: 180,
                out: 240,
                anticipation: 10,
                motion: 22,
              },
            ],
            exposures: { action: 1, body: 2, hold: 3 },
          },
        },
        {
          name: "纸上工作坊",
          frames: 240,
          data: {
            title: "想法碰面，故事发生",
            subtitle: "惊喜不是整齐划一，而是彼此接住。",
            palette: ["#eef3ef", "#26363d", "#e7a466", "#8ab9bb"],
            seed: 314159,
            characters: [
              {
                id: "spark",
                shape: "star",
                label: "灵感来了",
                emotion: "surprise",
                size: 100,
              },
              {
                id: "cloud",
                shape: "blob",
                label: "留一点弹性",
                emotion: "calm",
                size: 110,
              },
              {
                id: "guide",
                shape: "cat",
                label: "轻轻接住",
                emotion: "joy",
                size: 96,
              },
            ],
            beats: [
              {
                actor: "spark",
                action: "hop",
                in: 0,
                out: 60,
                anticipation: 8,
                motion: 20,
                distance: 36,
              },
              {
                actor: "cloud",
                action: "stretch",
                in: 60,
                out: 120,
                anticipation: 12,
                motion: 24,
              },
              {
                actor: "guide",
                action: "pop",
                in: 120,
                out: 180,
                anticipation: 10,
                motion: 20,
              },
              {
                actor: "cloud",
                action: "settle",
                in: 180,
                out: 240,
                anticipation: 10,
                motion: 22,
              },
            ],
          },
        },
      ],
      recipe: {
        "适用/不适用": "适用有角色意图的短动作；不适用光滑插值替代全部绘画。",
        视觉隐喻: "先压低重心，再尝试，最后安稳落地。",
        "4–6拍": [
          "角色建立",
          "预备压缩",
          "动作一拍",
          "短位移 smear",
          "落地手绘 FX",
          "三拍留住结果",
        ],
        不可删特征:
          "真实1/2/3帧曝光、可见预备与中间姿态、短 smear、seed 轮廓 boil。",
        可替语义:
          "角色轮廓/表情/文案/动作/位移/节拍/配色，曝光预算不足写前拒绝。",
        "动作/转场/声音点":
          "动作爆发点和落地可配短声；boil 只随画地址改变，本页无音轨。",
        编辑练习: "把characters[0].shape换成star并改label，再把exposures.action设为3：body为2时anticipation至少3帧、motion至少7帧，预算不足就后移该拍out与后续拍in/out及总帧数，逐帧确认预备和腾空仍可见。",
        失败反例: "三拍采样只剩起落、smear持续一整秒、角色下一拍瞬移回原位。",
      },
    },
    validate,
    render,
  };
})();
