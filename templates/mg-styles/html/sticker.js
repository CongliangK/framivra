/* Program-drawn sticker explanation; no photographic artwork. */
(() => {
  "use strict";
  const examples = [
    {
      name: "手机：单位与证据",
      frames: 360,
      data: {
        title: "拆开问题，再看证据",
        question: { text: "一部手机，重量从哪里来？", shape: "phone" },
        parts: [
          { id: "screen", label: "屏幕组件", shape: "screen" },
          { id: "power", label: "供电组件", shape: "battery" },
          { id: "logic", label: "运算组件", shape: "chip" },
        ],
        evidence: [
          {
            id: "display_mass",
            label: "屏幕示意质量",
            value: 35,
            unit: "g",
            source: "教学示意数据，非真实测量",
            chart: "bar",
          },
          {
            id: "battery_mass",
            label: "电池示意质量",
            value: 48,
            unit: "g",
            source: "教学示意数据，非真实测量",
            chart: "bar",
          },
          {
            id: "board_mass",
            label: "主板示意质量",
            value: 18,
            unit: "g",
            source: "教学示意数据，非真实测量",
            chart: "bar",
          },
        ],
        conclusion: "比较之前，先统一单位与测量口径。",
        relations: [
          { from: "question", to: "screen", label: "拆分" },
          { from: "question", to: "power", label: "拆分" },
          { from: "question", to: "logic", label: "拆分" },
          { from: "screen", to: "display_mass", label: "称量" },
          { from: "power", to: "battery_mass", label: "称量" },
          { from: "logic", to: "board_mass", label: "称量" },
          { from: "display_mass", to: "conclusion", label: "共同解释" },
          { from: "battery_mass", to: "conclusion", label: "共同解释" },
          { from: "board_mass", to: "conclusion", label: "共同解释" },
        ],
        beats: [
          {
            kind: "question",
            in: 0,
            out: 55,
            caption: "先把一个看似简单的问题摆出来。",
            move_frames: 0,
            reveal_frames: 0,
            closeup_hold_frames: 55,
            pullback_frames: 0,
            overview_hold_frames: 0,
          },
          {
            kind: "decompose",
            in: 55,
            out: 120,
            caption: "拆成屏幕、供电、运算三个可研究部分。",
            move_frames: 15,
            reveal_frames: 0,
            closeup_hold_frames: 50,
            pullback_frames: 0,
            overview_hold_frames: 0,
          },
          {
            kind: "evidence",
            in: 120,
            out: 240,
            caption: "用同一单位比较，来源与数字一起出现。",
            move_frames: 18,
            reveal_frames: 24,
            closeup_hold_frames: 78,
            pullback_frames: 0,
            overview_hold_frames: 0,
          },
          {
            kind: "conclusion",
            in: 240,
            out: 360,
            caption: "拉远看：问题、结构、证据，才能连成结论。",
            move_frames: 15,
            reveal_frames: 0,
            closeup_hold_frames: 45,
            pullback_frames: 20,
            overview_hold_frames: 40,
          },
        ],
        seed: 371,
      },
    },
    {
      name: "节水：比较基线",
      frames: 360,
      data: {
        title: "把节水问题讲清楚",
        question: { text: "同一瓶水，哪些做法能少用一点？", shape: "bottle" },
        parts: [
          { id: "garden", label: "照料植物", shape: "leaf" },
          { id: "reuse", label: "循环使用", shape: "drop" },
        ],
        evidence: [
          {
            id: "change_a",
            label: "方案甲用量变化",
            value: -120,
            unit: "mL",
            source: "教学假设，需替换为实测记录",
            chart: "bar",
          },
          {
            id: "change_b",
            label: "方案乙用量变化",
            value: -80,
            unit: "mL",
            source: "教学假设，需替换为实测记录",
            chart: "bar",
          },
        ],
        conclusion: "负值表示减少；结论必须说明比较基线。",
        relations: [
          { from: "question", to: "garden", label: "拆分场景" },
          { from: "question", to: "reuse", label: "拆分场景" },
          { from: "garden", to: "change_a", label: "对照测量" },
          { from: "reuse", to: "change_b", label: "对照测量" },
          { from: "change_a", to: "conclusion", label: "解释变化" },
          { from: "change_b", to: "conclusion", label: "解释变化" },
        ],
        beats: [
          {
            kind: "question",
            in: 0,
            out: 50,
            caption: "换一个问题：怎样把同一瓶水用得更好？",
            move_frames: 0,
            reveal_frames: 0,
            closeup_hold_frames: 50,
            pullback_frames: 0,
            overview_hold_frames: 0,
          },
          {
            kind: "decompose",
            in: 50,
            out: 120,
            caption: "先分清使用场景，不急着说哪个更有效。",
            move_frames: 18,
            reveal_frames: 0,
            closeup_hold_frames: 52,
            pullback_frames: 0,
            overview_hold_frames: 0,
          },
          {
            kind: "evidence",
            in: 120,
            out: 240,
            caption: "这里显示相对基线的变化，向下表示少用了。",
            move_frames: 18,
            reveal_frames: 24,
            closeup_hold_frames: 78,
            pullback_frames: 0,
            overview_hold_frames: 0,
          },
          {
            kind: "conclusion",
            in: 240,
            out: 360,
            caption: "把来源和基线讲明白，才是可复用的解释。",
            move_frames: 18,
            reveal_frames: 0,
            closeup_hold_frames: 42,
            pullback_frames: 24,
            overview_hold_frames: 36,
          },
        ],
        palette: ["#eef4f0", "#263b40", "#559eaf", "#b1c9a4"],
        seed: -715,
      },
    },
  ];
  const shapes = [
    "phone",
    "screen",
    "battery",
    "chip",
    "camera",
    "leaf",
    "drop",
    "gear",
    "bottle",
    "house",
  ];
  // 契约先检查镜头与揭示预算；短窗口不能把未入场的部件算作完整态。
  function validate(d, N, h) {
    h.int(N, "frames", 40, 18000);
    h.int(d.seed ?? 0, "seed", -2147483648, 2147483647);
    h.label(d.title, "title");
    h.palette(d.palette || ["#faf6ec", "#263b40", "#e77c50", "#88b8ab"]);
    h.windows(d.beats, N, 10);
    if (d.beats.length !== 4) throw Error("需要提问/分解/证据/结论四拍");
    if (!d.question || !shapes.includes(d.question.shape))
      throw Error("选择独立问题图形");
    h.label(d.question.text, "question");
    h.label(d.conclusion, "conclusion");
    if (!Array.isArray(d.parts) || d.parts.length < 2 || d.parts.length > 4)
      throw Error("parts需2–4项");
    if (
      !Array.isArray(d.evidence) ||
      d.evidence.length < 1 ||
      d.evidence.length > 4
    )
      throw Error("evidence需1–4项");
    const ids = new Set(["question", "conclusion"]);
    for (const p of d.parts) {
      h.label(p.id, "id", 32);
      if (ids.has(p.id) || !shapes.includes(p.shape))
        throw Error("部件图形或ID错误");
      ids.add(p.id);
      h.label(p.label, "label");
    }
    const units = new Set();
    for (const e of d.evidence) {
      h.label(e.id, "evidence id", 32);
      if (ids.has(e.id)) throw Error("证据ID重复");
      ids.add(e.id);
      for (const k of ["label", "unit", "source"]) h.label(e[k], k);
      h.num(e.value, "value", -1e9, 1e9);
      if ((e.chart || "bar") === "ring") {
        if (e.unit !== "%") throw Error("环图单位必须%");
        h.num(e.value, "percentage", 0, 100);
      } else if ((e.chart || "bar") === "bar") units.add(e.unit);
      else throw Error("未知图表");
    }
    if (units.size > 1) throw Error("比较柱需要相同单位");
    if (!Array.isArray(d.relations) || !d.relations.length)
      throw Error("需要问题到结论的关系");
    for (const e of d.relations) {
      if (!ids.has(e.from) || !ids.has(e.to) || e.from === e.to)
        throw Error("关系端点无效");
      h.label(e.label, "relation");
    }
    const reached = new Set(["question"]);
    for (let i = 0; i < ids.size; i++)
      for (const e of d.relations) if (reached.has(e.from)) reached.add(e.to);
    if (reached.size !== ids.size) throw Error("全部节点须从问题可达");
    for (let i = 0; i < 4; i++) {
      const b = d.beats[i],
        kind = ["question", "decompose", "evidence", "conclusion"][i],
        duration = b.out - b.in;
      if (b.kind !== kind) throw Error("拍序错误");
      h.label(b.caption, "caption");
      const t = timing(b);
      for (const [k, v] of Object.entries(t)) h.int(v, k, 0, duration);
      if (
        t.close < 1 ||
        t.move + t.reveal + t.close + t.pull + t.overview !== duration
      )
        throw Error("镜头/揭示/读留/拉远/总览预算须完整覆盖窗口");
      if (i === 1 && t.close < 6 * (d.parts.length - 1) + 10 + 6)
        throw Error("分解拍须在全部部件入场后保留至少6帧完整态");
      if (
        (i === 0 && t.move) ||
        (i > 0 && t.move < 1) ||
        (i === 2 && t.reveal < 1) ||
        (i !== 2 && t.reveal) ||
        (i === 3 && (t.pull < 1 || t.overview < 1)) ||
        (i !== 3 && (t.pull || t.overview))
      )
        throw Error("预算不适用于此语义拍");
    }
  }
  function timing(b) {
    const n = b.out - b.in,
      move =
        b.move_frames ??
        (b.kind === "question" ? 0 : Math.min(15, Math.floor(n / 4))),
      reveal =
        b.reveal_frames ??
        (b.kind === "evidence"
          ? Math.min(15, Math.max(1, Math.floor((n - move) / 3)))
          : 0),
      pull =
        b.pullback_frames ??
        (b.kind === "conclusion"
          ? Math.min(15, Math.max(1, Math.floor((n - move) / 3)))
          : 0),
      overview =
        b.overview_hold_frames ??
        (b.kind === "conclusion"
          ? Math.max(1, Math.floor((n - move - pull) / 2))
          : 0),
      close = b.closeup_hold_frames ?? n - move - reveal - pull - overview;
    return { move, reveal, close, pull, overview };
  }
  // 同一大画布上依次近景移动，结论拉远后固定总览。
  function camera(d, f, h) {
    const i = d.beats.findIndex((b) => b.in <= f && f < b.out),
      b = d.beats[i],
      t = timing(b),
      local = f - b.in,
      q = t.move ? h.smooth(local / t.move) : 1;
    let x = 720 + Math.max(0, i - 1) * 1440 + (i ? 1440 : 0) * q,
      z = 1.16;
    if (i === 3) {
      const pull = h.smooth((local - t.move - t.close) / t.pull);
      x = h.lerp(x, 2880, pull);
      z = h.lerp(z, 1 / 3, pull);
    }
    return { x, z, i, beat: b, t };
  }
  function icon(c, type, x, y, size, color, ink, h, progress = 1) {
    const growth = Math.max(0.001, h.ease(progress));
    c.save();
    c.translate(x, y);
    c.scale(growth, growth);
    c.lineJoin = "round";
    c.lineCap = "round";
    c.lineWidth = 14;
    c.strokeStyle = "#fff";
    c.fillStyle = color;
    function path(points) {
      c.beginPath();
      points.forEach(([xx, yy], i) =>
        i ? c.lineTo(xx * size, yy * size) : c.moveTo(xx * size, yy * size),
      );
      c.closePath();
      c.stroke();
      c.fill();
      c.strokeStyle = ink;
      c.lineWidth = 2;
      c.stroke();
    }
    if (type === "leaf")
      path([
        [-1, 0.3],
        [-0.6, -0.65],
        [0.6, -1],
        [1, -0.4],
        [0.6, 0.6],
        [-0.3, 0.85],
      ]);
    else if (type === "drop") {
      c.beginPath();
      c.moveTo(0, -size);
      c.bezierCurveTo(size * 0.8, -size * 0.1, size, size * 0.65, 0, size);
      c.bezierCurveTo(-size, size * 0.65, -size * 0.8, -size * 0.1, 0, -size);
      c.closePath();
      c.stroke();
      c.fill();
    } else if (type === "bottle")
      path([
        [-0.22, -1],
        [0.22, -1],
        [0.22, -0.55],
        [0.52, -0.35],
        [0.52, 0.95],
        [-0.52, 0.95],
        [-0.52, -0.35],
        [-0.22, -0.55],
      ]);
    else if (type === "house")
      path([
        [-0.9, -0.15],
        [0, -1],
        [0.9, -0.15],
        [0.7, -0.15],
        [0.7, 0.9],
        [-0.7, 0.9],
        [-0.7, -0.15],
      ]);
    else {
      const w = type === "screen" ? 1.7 : type === "chip" ? 1.5 : 1.1,
        ht = type === "screen" ? 1.1 : 1.7;
      c.beginPath();
      c.roundRect((-size * w) / 2, (-size * ht) / 2, size * w, size * ht, 12);
      c.stroke();
      c.fill();
      c.strokeStyle = ink;
      c.lineWidth = 2;
      c.stroke();
      c.strokeRect(
        -size * w * 0.35,
        -size * ht * 0.35,
        size * w * 0.7,
        size * ht * 0.7,
      );
      if (type === "chip" || type === "gear")
        for (let i = -2; i <= 2; i++) {
          c.beginPath();
          c.moveTo(i * size * 0.22, -size * 0.9);
          c.lineTo(i * size * 0.22, -size * 0.75);
          c.stroke();
        }
      if (type === "phone") {
        h.circle(c, 0, size * 0.68, size * 0.045, ink);
      }
      if (type === "battery") {
        c.fillStyle = ink;
        c.fillRect(-size * 0.2, -size * 0.98, size * 0.4, size * 0.12);
      }
    }
    c.restore();
  }
  // 提问 → 分解 → 带来源证据 → 结论，文字阅读与镜头移动分别分配帧。
  function render(c, d, f, N, h) {
    const palette = d.palette || ["#faf6ec", "#263b40", "#e77c50", "#88b8ab"],
      [bg, ink, accent, second] = palette,
      view = camera(d, f, h);
    c.fillStyle = bg;
    c.fillRect(0, 0, 1920, 1080);
    c.save();
    c.translate(960 - view.x * view.z, 540 - 540 * view.z);
    c.scale(view.z, view.z);
    c.fillStyle = "rgba(70,60,45,.035)";
    for (let i = 0; i < 650; i++) {
      const x = (((i * 491 + (d.seed ?? 0) * 13) % 5760) + 5760) % 5760,
        y = (((i * 137 + (d.seed ?? 0) * 7) % 1080) + 1080) % 1080;
      c.fillRect(x, y, 2, 1);
    }
    const positions = { question: [720, 530], conclusion: [5040, 530] };
    d.parts.forEach(
      (p, i) =>
        (positions[p.id] = [
          2160 +
            Math.cos(-Math.PI / 2 + (i / d.parts.length) * Math.PI * 2) * 350,
          530 +
            Math.sin(-Math.PI / 2 + (i / d.parts.length) * Math.PI * 2) * 150,
        ]),
    );
    d.evidence.forEach(
      (e, i) =>
        (positions[e.id] = [
          3000 + ((i + 0.5) * 1200) / d.evidence.length,
          530,
        ]),
    );
    const end = d.beats[3],
      et = timing(end),
      relation = h.smooth((f - end.in - et.move - et.close) / et.pull);
    c.save();
    c.globalAlpha = relation;
    c.strokeStyle = second;
    c.lineWidth = 2;
    for (const e of d.relations) {
      const a = positions[e.from],
        b = positions[e.to];
      c.beginPath();
      c.moveTo(...a);
      c.bezierCurveTo(a[0] + 150, a[1] - 90, b[0] - 150, b[1] - 90, ...b);
      c.stroke();
      h.text(c, e.label, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2 - 30, 280, 25, {
        minSize: 24,
        align: "center",
        maxLines: 2,
        height: 70,
        color: ink,
      });
    }
    c.restore();
    h.text(c, d.question.text, 720, 175, 1180, 64, {
      minSize: 30,
      align: "center",
      maxLines: 2,
      height: 165,
      color: ink,
    });
    icon(
      c,
      d.question.shape,
      720,
      550,
      205,
      accent,
      ink,
      h,
      Math.max(0.1, (f + 1) / 14),
    );
    const decomp = d.beats[1],
      dt = timing(decomp);
    d.parts.forEach((p, i) => {
      const [x, y] = positions[p.id];
      c.strokeStyle = ink;
      c.lineWidth = 3;
      c.beginPath();
      c.moveTo(2160, 530);
      c.lineTo(x, y);
      c.stroke();
      icon(
        c,
        p.shape,
        x,
        y,
        105,
        i % 2 ? second : accent,
        ink,
        h,
        (f - decomp.in - dt.move - i * 6) / 10,
      );
      h.text(c, p.label, x, y + 125, 360, 28, {
        minSize: 24,
        align: "center",
        maxLines: 2,
        height: 80,
        color: ink,
      });
    });
    const proof = d.beats[2],
      pt = timing(proof),
      grow = h.smooth((f - proof.in - pt.move) / pt.reveal),
      maximum = Math.max(
        1,
        ...d.evidence
          .filter((e) => (e.chart || "bar") === "bar")
          .map((e) => Math.abs(e.value)),
      );
    d.evidence.forEach((e) => {
      const x = positions[e.id][0],
        width = 1100 / d.evidence.length;
      h.text(c, e.label, x, 190, width, 36, {
        minSize: 24,
        align: "center",
        maxLines: 2,
        height: 100,
        color: ink,
      });
      if ((e.chart || "bar") === "bar") {
        const amount = (Math.abs(e.value) / maximum) * 200 * grow;
        c.strokeStyle = ink;
        c.lineWidth = 3;
        c.beginPath();
        c.moveTo(x - 100, 590);
        c.lineTo(x + 100, 590);
        c.stroke();
        c.fillStyle = accent;
        c.fillRect(x - 55, e.value >= 0 ? 590 - amount : 590, 110, amount);
      } else {
        c.strokeStyle = second;
        c.lineWidth = 22;
        c.beginPath();
        c.arc(x, 520, 135, 0, Math.PI * 2);
        c.stroke();
        c.strokeStyle = accent;
        c.beginPath();
        c.arc(
          x,
          520,
          135,
          -Math.PI / 2,
          -Math.PI / 2 + ((Math.PI * 2 * e.value) / 100) * grow,
        );
        c.stroke();
      }
      h.text(
        c,
        (grow >= 1 ? e.value : Math.round(e.value * grow)) + e.unit,
        x,
        300,
        width,
        56,
        { minSize: 24, align: "center", height: 80, color: ink },
      );
      h.text(c, e.source, x, 800, width, 25, {
        minSize: 24,
        align: "center",
        maxLines: 3,
        height: 110,
        color: ink,
      });
    });
    icon(c, d.question.shape, 5040, 470, 180, second, ink, h);
    c.strokeStyle = ink;
    c.lineWidth = 17;
    c.beginPath();
    c.moveTo(4960, 470);
    c.lineTo(5025, 535);
    c.lineTo(5140, 385);
    c.stroke();
    h.text(c, d.conclusion, 5040, 670, 1160, 56, {
      minSize: 30,
      align: "center",
      maxLines: 3,
      height: 230,
      color: ink,
    });
    c.restore();
    c.fillStyle = bg;
    c.fillRect(0, 0, 1920, 105);
    c.fillRect(0, 915, 1920, 165);
    h.text(c, d.title, 960, 18, 1660, 66, {
      minSize: 32,
      align: "center",
      height: 85,
      color: ink,
    });
    h.text(c, view.beat.caption, 960, 934, 1640, 38, {
      minSize: 24,
      align: "center",
      maxLines: 2,
      height: 85,
      color: ink,
    });
    const sources = [...new Set(d.evidence.map((e) => e.source))].join("；");
    h.text(c, "来源：" + sources, 960, 1032, 1660, 24, {
      minSize: 24,
      align: "center",
      height: 33,
      color: ink,
    });
  }
  window.MG_REFERENCE = {
    id: "sticker",
    title: "贴纸解释：问题、结构与证据",
    limits:
      "原创程序图标/白边贴纸参考，不含照片抠图。来源字段是编辑声明，渲染器不会核实。总览用于看关系，细证据需在近景阅读；HTML尚无本环境浏览器像素验收。",
    examples,
    validate,
    render,
    recipe: {
      编辑练习:
        "将 parts 从3项改为4项，更新 relations；decompose 的 closeup_hold_frames 至少34帧（含最后部件完成后6帧保持），再按文案增加留读，并同步调整该拍 out 和后续 in/out，保持五项预算之和覆盖整拍。",
      "适用/不适用":
        "适合一问、有限部件、可核查证据和一句结论；不适合无来源数据堆砌或以图标冒充照片证据。",
      视觉隐喻:
        "把问题拆成可摆放的贴纸，镜头走过部件和证据，最后连成可解释的关系。",
      "4–6拍": [
        "问题先占主位",
        "镜头移向分解",
        "证据计数在近景完成",
        "留时间读单位与来源",
        "结论近景停留",
        "拉远后真正保持总览",
      ],
      不可删特征:
        "白边贴纸、统一大画布、句锁镜头、单位/基线/来源、因果连线和显式阅读预算。",
      可替语义:
        "question图形/文案、parts部件、evidence数值/单位/来源、relations关系、beats五种帧预算。",
      "动作/转场/声音点":
        "镜头到位、证据完成、结论出现和总览落定是可绑定声音的语义点；不要用随机点击声代替解释节奏。",
      失败反例:
        "数字还在滚就切走；只在最后两帧到总览；不同单位柱高直接比较；缺少来源却做成确定事实。",
    },
  };
  window.MG_REFERENCE.inspect = (d, f, N, h) => {
    const v = camera(d, f, h),
      p = d.beats[2],
      t = timing(p);
    return {
      kind: v.beat.kind,
      camera_x: v.x,
      zoom: v.z,
      evidence_complete: f >= p.in + t.move + t.reveal,
      overview_complete: v.i === 3 && f >= v.beat.out - v.t.overview,
      overview_hold_frames: v.t.overview,
    };
  };
})();
