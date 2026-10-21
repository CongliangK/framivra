(() => {
  "use strict";
  const TAU = Math.PI * 2;
  const {txt, line, finite, noise, check} = window.MGSceneHelpers;

  const PAL = ["#f2e9d6", "#22323b", "#dc7965", "#87aaa1", "#d4bc66"];
  function shape(s) {
    if (["head", "planet"].includes(s))
      return Array.from({ length: 48 }, (_, i) => [
        Math.cos((i / 48) * TAU),
        Math.sin((i / 48) * TAU) * (s === "head" ? 0.85 : 1),
      ]);
    return {
      jaw: [
        [-0.95, -0.12],
        [0.8, -0.12],
        [0.95, 0.26],
        [0.6, 0.65],
        [-0.8, 0.55],
      ],
      body: [
        [-0.45, -1],
        [0.4, -1],
        [0.7, 1],
        [-0.7, 1],
      ],
      arm: [
        [-0.18, -0.15],
        [0.18, -0.15],
        [0.25, 0.9],
        [-0.15, 1],
      ],
      hand: [
        [-0.5, 0.6],
        [-0.7, -0.2],
        [-0.5, -0.3],
        [-0.2, 0.1],
        [-0.2, -0.8],
        [0, -0.85],
        [0.1, -0.2],
        [0.25, -0.7],
        [0.42, -0.65],
        [0.35, 0.15],
        [0.65, -0.3],
        [0.8, -0.1],
        [0.55, 0.7],
      ],
      leaf: [
        [-1, 0.4],
        [-0.5, -0.5],
        [0.6, -0.8],
        [1, -0.4],
        [0.7, 0.5],
        [-0.3, 0.85],
      ],
      rocket: [
        [-0.35, 0.6],
        [-0.35, -0.4],
        [0, -1],
        [0.35, -0.4],
        [0.35, 0.6],
        [0.7, 0.9],
        [0.2, 0.75],
        [0, 1],
        [-0.2, 0.75],
        [-0.7, 0.9],
      ],
    }[s];
  }
  const rot = (x, y, a) => {
    a *= Math.PI / 180;
    return [
      x * Math.cos(a) - y * Math.sin(a),
      x * Math.sin(a) + y * Math.cos(a),
    ];
  };
  // Step exposure → parent transform → local hinge compensation → independent cutout.
  function state(d, f, h) {
    const exposure = d.exposure ?? 3,
      t = Math.floor(f / exposure) * exposure,
      b = h.stage(d.beats, t),
      local = t - b.in,
      u = local / Math.max(1, b.out - b.in - exposure),
      tr = {};
    d.parts.forEach((p) => {
      const par = tr[p.parent];
      let x, y, a;
      if (par) {
        const [rx, ry] = rot(p.x * par.s, p.y * par.s, par.a);
        x = par.x + rx;
        y = par.y + ry;
        a = par.a + p.angle;
      } else {
        x = p.x * 1920;
        y = p.y * 1080;
        a = p.angle;
      }
      if (b.active.includes(p.id)) {
        let q = 0;
        if (b.mode === "awaken")
          a += Math.sin((local / exposure) * 1.3) * p.motion.hinge;
        else {
          q =
            b.mode === "release"
              ? h.ease(u)
              : b.mode === "reframe"
                ? 1 - h.ease(u)
                : 0;
          x += p.motion.drift[0] * q;
          y += p.motion.drift[1] * q;
          a += p.motion.hinge * q;
        }
      }
      const [bx, by] = rot(
          p.pivot[0] * p.scale,
          p.pivot[1] * p.scale,
          par ? par.a : 0,
        ),
        [rx, ry] = rot(p.pivot[0] * p.scale, p.pivot[1] * p.scale, a);
      tr[p.id] = { x: x + bx - rx, y: y + by - ry, a, s: p.scale };
    });
    return { t, b, u, tr };
  }
  // Check all held drawings, including inherited rotation, before accepting edits.
  function validate(d, N, h) {
    check(d, N, h, 5);
    if (d.subtitle) h.label(d.subtitle, "subtitle", 68);
    h.int(d.seed ?? 7, "seed", -2147483648, 2147483647);
    h.int(d.exposure ?? 3, "exposure", 1, 6);
    const ids = [];
    if (!Array.isArray(d.parts) || d.parts.length < 3 || d.parts.length > 10)
      throw Error("3..10 cutouts");
    d.parts.forEach((p) => {
      if (
        !/^[a-z][a-z0-9_-]{0,24}$/.test(p.id) ||
        ids.includes(p.id) ||
        !shape(p.shape) ||
        (p.parent !== null && !ids.includes(p.parent))
      )
        throw Error("part id/shape/parent");
      h.num(p.x, "x", p.parent ? -2 : 0.1, p.parent ? 2 : 0.9);
      h.num(p.y, "y", p.parent ? -2 : 0.3, p.parent ? 2 : 0.8);
      h.num(p.scale, "scale", 24, 220);
      h.num(p.angle, "angle", -180, 180);
      if (
        !Array.isArray(p.pivot) ||
        p.pivot.length !== 2 ||
        !p.motion ||
        !Array.isArray(p.motion.drift) ||
        p.motion.drift.length !== 2
      )
        throw Error("pivot/drift");
      p.pivot.forEach((v) => h.num(v, "pivot", -1.5, 1.5));
      p.motion.drift.forEach((v) => h.num(v, "drift", -250, 250));
      h.num(p.motion.hinge, "hinge", -70, 70);
      ids.push(p.id);
    });
    h.windows(d.beats, N, 18);
    if (d.beats.map((b) => b.mode).join() !== "awaken,release,reframe,resolve")
      throw Error("ordered beats required");
    d.beats.forEach((b) => {
      h.label(b.text, "beat text", 65);
      if (
        b.in % (d.exposure ?? 3) ||
        (b.out !== N && b.out % (d.exposure ?? 3))
      )
        throw Error("windows must align to exposure");
      if (
        !Array.isArray(b.active) ||
        new Set(b.active).size !== b.active.length ||
        b.active.some((id) => !ids.includes(id))
      )
        throw Error("active parts");
    });
    if ((d.evidence ?? []).length > 2) throw Error("max2 evidence");
    (d.evidence ?? []).forEach((e) => {
      ["label", "value", "source"].forEach((k) =>
        h.label(e[k], k, k === "source" ? 45 : 15),
      );
      h.label(`${e.label} ${e.value} / ${e.source}`, "evidence line", 70);
    });
    for (let f = 0; f < N; f += d.exposure ?? 3) {
      const st = state(d, f, h);
      d.parts.forEach((p) => {
        const tr = st.tr[p.id];
        shape(p.shape).forEach(([x, y]) => {
          const [rx, ry] = rot(x * tr.s, y * tr.s, tr.a);
          if (
            tr.x + rx < 30 ||
            tr.x + rx > 1890 ||
            tr.y + ry < 310 ||
            tr.y + ry > 940
          )
            throw Error("cutout outside safe stage");
        });
      });
    }
  }
  // Establish face → release symbols → torn front reframes → readable resolved page.
  function render(c, d, f, N, h) {
    h.int(f, "frame", 0, N - 1);
    const { t, b, u, tr } = state(d, f, h),
      p = d.palette ?? PAL,
      seed = d.seed ?? 7;
    c.fillStyle = p[0];
    c.fillRect(0, 0, 1920, 1080);
    c.fillStyle = "#d4c9af";
    for (let i = 0; i < 1700; i++) {
      const x = (((i * 491 + seed * 13) % 1920) + 1920) % 1920,
        y = (((i * 137 + seed * 7) % 1080) + 1080) % 1080;
      c.fillRect(x, y, 1 + (i % 4), 1);
    }
    // Compose every hinge from its parent in local space, then sample the entire page.
    d.parts.forEach((part, i) => {
      const a = tr[part.id],
        point = (x, y) => {
          const [rx, ry] = rot(x * a.s, y * a.s, a.a);
          return [a.x + rx, a.y + ry];
        },
        pts = shape(part.shape).map(([x, y]) => point(x, y)),
        color = [p[2], p[3], p[4], p[1]][(((i + seed) % 4) + 4) % 4];
      h.polygon(
        c,
        pts.map(([x, y]) => [x + 6, y + 8]),
        "#bfb49d",
      );
      h.polygon(c, pts, color);
      c.lineWidth = 8;
      c.strokeStyle = "#fffcef";
      h.polygon(c, pts);
      if (part.shape === "head") {
        c.save();
        c.beginPath();
        pts.forEach(([x, y], j) => (j ? c.lineTo(x, y) : c.moveTo(x, y)));
        c.closePath();
        c.clip();
        for (let yy = 1; yy < 5; yy++)
          for (let xx = -5; xx <= 5; xx++) {
            const [x, y] = point(xx * 0.16, yy * 0.14 + 0.2);
            h.circle(c, x, y, 2, p[1]);
          }
        c.restore();
        [-0.32, 0.32].forEach((xx) => {
          const [x, y] = point(xx, -0.12);
          h.circle(c, x, y, Math.max(3, a.s * 0.055), p[0]);
        });
      } else if (["leaf", "planet"].includes(part.shape))
        line(c, [point(-0.8, 0.3), point(0.8, -0.3)], p[1], 4);
      else if (part.shape === "rocket") {
        const [x, y] = point(0, -0.25);
        h.circle(c, x, y, a.s * 0.2, p[0]);
      }
      if (["body", "arm"].includes(part.shape))
        h.polygon(
          c,
          [
            [-0.25, -0.18],
            [0.25, -0.18],
            [0.25, -0.02],
            [-0.25, -0.02],
          ].map(([x, y]) => point(x, y)),
          p[4],
        );
    });
    // A held jagged front passes over the old composition; its edge is the transition.
    if (b.mode === "reframe" && u < 0.55) {
      const edge = 1920 - 3840 * Math.min(1, u / 0.55),
        pts = Array.from({ length: 23 }, (_, j) => [
          Math.max(-20, edge) + (j % 2 ? 12 : 0),
          300 + j * 30,
        ]);
      h.polygon(c, [[edge + 1920, 300], ...pts, [edge + 1920, 960]], "#fffcef");
      line(c, pts, p[1], 3);
    }
    c.fillStyle = p[1];
    c.fillRect(90, 55, 1735, 102);
    txt(c, h, d.title, 115, 57, 1660, 80, p[0]);
    if (d.subtitle) txt(c, h, d.subtitle, 110, 182, 1660, 30, p[1]);
    txt(c, h, b.text, 120, 246, 1660, 46, p[1]);
    (d.evidence ?? []).forEach((e, i) =>
      txt(
        c,
        h,
        `${e.label} ${e.value} / ${e.source}`,
        100,
        982 + i * 34,
        1720,
        26,
        p[1],
      ),
    );
  }

  window.MG_REFERENCE = {
    ...{
      id: "collage",
      title: "剪纸拼贴 · 重排旧关系",
      limits:
        "原创建模参考，复用媒体语义 schema；浏览器字体、实际视频时序和音轨仍需单独验收。本页无声音。",
      examples: [
        {
          name: "观察拼出想象",
          frames: 240,
          data: {
            title: "脑洞不是凭空出现的",
            subtitle: "一张程序剪纸脸，把观察拼成新的想象",
            exposure: 3,
            seed: -9,
            parts: [
              {
                id: "body",
                shape: "body",
                parent: null,
                x: 0.4,
                y: 0.7,
                scale: 75,
                angle: 8,
                pivot: [0, 0],
                motion: { hinge: 0, drift: [0, 0] },
              },
              {
                id: "left",
                shape: "arm",
                parent: "body",
                x: -0.5,
                y: -0.6,
                scale: 100,
                angle: -20,
                pivot: [0, -0.1],
                motion: { hinge: 28, drift: [0, 0] },
              },
              {
                id: "head",
                shape: "head",
                parent: null,
                x: 0.4,
                y: 0.5,
                scale: 180,
                angle: -5,
                pivot: [0, 0],
                motion: { hinge: 0, drift: [0, 0] },
              },
              {
                id: "jaw",
                shape: "jaw",
                parent: "head",
                x: 0,
                y: 0.6,
                scale: 90,
                angle: 0,
                pivot: [-0.85, 0],
                motion: { hinge: 24, drift: [0, 0] },
              },
              {
                id: "idea",
                shape: "leaf",
                parent: null,
                x: 0.66,
                y: 0.52,
                scale: 80,
                angle: 20,
                pivot: [0, 0],
                motion: { hinge: -35, drift: [150, -40] },
              },
              {
                id: "journey",
                shape: "rocket",
                parent: null,
                x: 0.76,
                y: 0.65,
                scale: 90,
                angle: -20,
                pivot: [0, 0],
                motion: { hinge: 25, drift: [-30, -130] },
              },
              {
                id: "world",
                shape: "planet",
                parent: null,
                x: 0.67,
                y: 0.7,
                scale: 65,
                angle: 0,
                pivot: [0, 0],
                motion: { hinge: 35, drift: [120, 0] },
              },
            ],
            beats: [
              {
                mode: "awaken",
                in: 0,
                out: 45,
                text: "先让好奇心开口",
                active: ["jaw", "left"],
              },
              {
                mode: "release",
                in: 45,
                out: 105,
                text: "观察跳出来，和想象碰个面",
                active: ["idea", "journey", "world"],
              },
              {
                mode: "reframe",
                in: 105,
                out: 165,
                text: "撕开旧框架，重新拼一次",
                active: ["idea", "journey", "world"],
              },
              {
                mode: "resolve",
                in: 165,
                out: 240,
                text: "把看见的东西，拼成没见过的答案",
                active: [],
              },
            ],
            evidence: [
              {
                label: "观察样例",
                value: "3 条",
                source: "本示例人工设置，非现实统计",
              },
            ],
          },
        },
        {
          name: "换个角度 · 延长末态",
          frames: 240,
          data: {
            title: "把习惯翻个面",
            subtitle: "巨大脑袋、小身体，一场不按比例的纸上旅行",
            exposure: 2,
            seed: 23,
            parts: [
              {
                id: "body",
                shape: "body",
                parent: null,
                x: 0.62,
                y: 0.71,
                scale: 70,
                angle: -8,
                pivot: [0, 0],
                motion: { hinge: 0, drift: [0, 0] },
              },
              {
                id: "arm",
                shape: "arm",
                parent: "body",
                x: 0.5,
                y: -0.6,
                scale: 90,
                angle: 20,
                pivot: [0, -0.1],
                motion: { hinge: -35, drift: [0, 0] },
              },
              {
                id: "hand",
                shape: "hand",
                parent: "arm",
                x: 0,
                y: 0.95,
                scale: 42,
                angle: 0,
                pivot: [0, 0.6],
                motion: { hinge: 20, drift: [0, 0] },
              },
              {
                id: "head",
                shape: "head",
                parent: null,
                x: 0.62,
                y: 0.48,
                scale: 160,
                angle: 8,
                pivot: [0, 0],
                motion: { hinge: 0, drift: [0, 0] },
              },
              {
                id: "jaw",
                shape: "jaw",
                parent: "head",
                x: 0,
                y: 0.6,
                scale: 82,
                angle: 0,
                pivot: [0.85, 0],
                motion: { hinge: -28, drift: [0, 0] },
              },
              {
                id: "world",
                shape: "planet",
                parent: null,
                x: 0.28,
                y: 0.65,
                scale: 78,
                angle: -10,
                pivot: [0, 0],
                motion: { hinge: 40, drift: [100, -100] },
              },
              {
                id: "idea",
                shape: "leaf",
                parent: null,
                x: 0.34,
                y: 0.43,
                scale: 75,
                angle: -25,
                pivot: [0, 0],
                motion: { hinge: 25, drift: [-70, 80] },
              },
            ],
            beats: [
              {
                mode: "awaken",
                in: 0,
                out: 40,
                text: "同一件事，能换一种看法吗？",
                active: ["arm", "hand", "jaw"],
              },
              {
                mode: "release",
                in: 40,
                out: 90,
                text: "让熟悉的比例，暂时失去规矩",
                active: ["world", "idea"],
              },
              {
                mode: "reframe",
                in: 90,
                out: 140,
                text: "把问题翻过来，寻找新关系",
                active: ["world", "idea"],
              },
              {
                mode: "resolve",
                in: 140,
                out: 240,
                text: "新的角度，让旧日常有了新故事",
                active: [],
              },
            ],
            palette: ["#f2e9d6", "#22323b", "#dc7965", "#87aaa1", "#d4bc66"],
          },
        },
      ],
      recipe: {
        "适用/不适用": "适用观察到重组的观点故事；不适用把程序脸假称真实照片。",
        视觉隐喻: "夸大头脑、拆开关节，把原来的世界撕开再拼。",
        "4–6拍": [
          "巨头小身建立",
          "下颌和手臂开口",
          "意象分离",
          "纸边扫过旧框",
          "重组为关系",
          "结果留读至少75帧",
        ],
        不可删特征: "父子局部关节、步进曝光、剪影白边/半调、撕纸承担重排。",
        可替语义: "parts层级/形状/铰链/漂移，beats活动对象与证据来源。",
        "动作/转场/声音点": "纸张扫入和剪件落位可作声音点；本页无声音。",
        编辑练习: "把jaw.pivot改为[0.85,0]并旋转父head.angle，替换idea.shape与beats文字，在awaken拍逐帧检查下颌铰链仍附着于父头部，再把resolve.out与总帧数一起后移以留读新结论。",
        失败反例: "所有剪件同频上下浮动、关节脱落、纸遮挡结果正文。",
      },
    },
    validate,
    render,
  };
})();
