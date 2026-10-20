/* Caption layout reference only: no video or human performance is supplied. */
(() => {
  "use strict";
  const examples = [
    {
      name: "知识闯关：四个动作",
      frames: 300,
      data: {
        title: "像素闯关反应字幕",
        caption_band: "upper",
        subject_rect: [0.2, 0.6, 0.8, 0.95],
        reactions: [
          {
            in: 8,
            out: 55,
            text: "跳过去！",
            emotion: "emphasis",
            punchFrame: 30,
            stagger: 2,
          },
          {
            in: 68,
            out: 115,
            text: "居然跨过去了！",
            emotion: "surprise",
            punchFrame: 88,
            stagger: 2,
          },
          {
            in: 128,
            out: 175,
            text: "蹲下，稳住！",
            emotion: "question",
            punchFrame: 148,
            stagger: 2,
          },
          {
            in: 190,
            out: 235,
            text: "最后一块证据！",
            emotion: "joy",
            punchFrame: 215,
            stagger: 2,
          },
        ],
      },
    },
    {
      name: "夜间送件：短反应",
      frames: 180,
      data: {
        title: "夜间送件实况反应",
        caption_band: "upper",
        subject_rect: [0.2, 0.6, 0.8, 0.95],
        reactions: [
          {
            in: 3,
            out: 48,
            text: "低头过门！",
            emotion: "emphasis",
            punchFrame: 25,
            stagger: 1,
          },
          {
            in: 53,
            out: 98,
            text: "包裹在前面！",
            emotion: "joy",
            punchFrame: 80,
            stagger: 1,
          },
          {
            in: 103,
            out: 148,
            text: "最后一跃！",
            emotion: "surprise",
            punchFrame: 128,
            stagger: 1,
          },
        ],
      },
    },
  ];
  const symbols = { emphasis: "!", surprise: "!?", joy: "♪", question: "?" },
    colors = {
      emphasis: "#fff",
      surprise: "#fff34a",
      joy: "#ffafcf",
      question: "#a8ecff",
    };
  // 以最后一个字、整体入场及强调动作三者最晚完成点计算完整留读。
  function validate(d, N, h) {
    h.int(N, "frames", 30, 18000);
    h.label(d.title, "title", 120);
    if (!["upper", "lower"].includes(d.caption_band))
      throw Error("caption_band须upper或lower");
    const r = d.subject_rect;
    if (!Array.isArray(r) || r.length !== 4)
      throw Error("需要主体禁区[left,top,right,bottom]");
    r.forEach((v) => h.num(v, "subject_rect", 0, 1));
    if (r[0] >= r[2] || r[1] >= r[3]) throw Error("主体禁区无面积");
    // 最坏两行高度、1.04强调缩放、描边/阴影及逐字上移的保守包围框。
    const top = d.caption_band === "upper" ? 0.24 : 0.72,
      bottom = d.caption_band === "upper" ? 0.52 : 1;
    if (r[0] < 0.99 && r[2] > 0.01 && r[1] < bottom && r[3] > top)
      throw Error("字幕带与主体禁区相交");
    if (!Array.isArray(d.reactions) || !d.reactions.length)
      throw Error("需要人工语义反应cue");
    let end = 0;
    for (const s of d.reactions) {
      h.int(s.in, "cue.in", end, N - 1);
      h.int(s.out, "cue.out", s.in + 1, N);
      h.label(s.text, "reaction", 120);
      if (/[\n\r]/.test(s.text) || !Object.hasOwn(symbols, s.emotion))
        throw Error("需要单行语义与已知情绪");
      h.int(s.stagger ?? 0, "stagger", 0, 5);
      h.int(s.punchFrame ?? s.in, "punch", s.in, s.out - 1);
      const n = Array.from(symbols[s.emotion] + "  " + s.text).length,
        reveal = (n - 1) * (s.stagger ?? 0) + 3;
      const stable = Math.max(
        s.in + reveal,
        s.in + 8,
        (s.punchFrame ?? s.in) + 5,
      );
      if (s.out - stable < 6)
        throw Error("窗口不足以逐字完成并留读；缩短反应或延长素材");
      end = s.out;
    }
  }
  // 先排完整句子再逐字揭示，避免揭示期间换行漂移。
  function layout(c, text, width, boxHeight) {
    for (let size = 80; size >= 32; size--) {
      c.font = `900 ${size}px "Noto Sans CJK SC", sans-serif`;
      const lines = [[]];
      let cursor = 0;
      for (const ch of Array.from(text)) {
        const w = c.measureText(ch).width;
        if (cursor + w > width && lines[lines.length - 1].length) {
          lines.push([]);
          cursor = 0;
        }
        lines[lines.length - 1].push({ ch, x: cursor, w });
        cursor += w;
      }
      if (lines.length <= 2 && lines.length * size * 1.5 <= boxHeight) {
        const glyphs = [];
        let i = 0;
        lines.forEach((line, row) => {
          const len = line.reduce((sum, g) => sum + g.w, 0),
            start = (width - len) / 2;
          line.forEach((g) =>
            glyphs.push({
              ...g,
              x: g.x + start,
              y: row * size * 1.5,
              index: i++,
            }),
          );
        });
        return { size, glyphs, height: lines.length * size * 1.5 };
      }
    }
    throw Error("字幕在安全带最小字级仍不能完整容纳，请缩句");
  }
  // 网格只是素材占位；主体禁区与真实视频证据不能混淆。
  function render(c, d, f, N, h) {
    c.fillStyle = "#1c2430";
    c.fillRect(0, 0, 1920, 1080);
    c.strokeStyle = "#364252";
    c.lineWidth = 1;
    for (let x = 100; x < 1920; x += 100) {
      c.beginPath();
      c.moveTo(x, 250);
      c.lineTo(x, 940);
      c.stroke();
    }
    for (let y = 250; y <= 940; y += 100) {
      c.beginPath();
      c.moveTo(100, y);
      c.lineTo(1820, y);
      c.stroke();
    }
    h.text(c, d.title, 960, 48, 1640, 64, {
      minSize: 32,
      align: "center",
      height: 90,
      color: "#f4f0e8",
    });
    h.text(c, "排版/禁区示意 · 非真实视频或真人节目", 960, 157, 1640, 30, {
      minSize: 24,
      align: "center",
      height: 48,
      color: "#ffd47b",
    });
    const [left, top, right, bottom] = d.subject_rect;
    c.save();
    c.fillStyle = "rgba(96,190,172,.08)";
    c.fillRect(
      left * 1920,
      top * 1080,
      (right - left) * 1920,
      (bottom - top) * 1080,
    );
    c.setLineDash([15, 12]);
    c.strokeStyle = "#83bfae";
    c.lineWidth = 3;
    c.strokeRect(
      left * 1920,
      top * 1080,
      (right - left) * 1920,
      (bottom - top) * 1080,
    );
    c.restore();
    h.text(
      c,
      "保留主体动作与视线",
      960,
      top * 1080 + 36,
      Math.max(500, (right - left) * 1920 - 80),
      32,
      { minSize: 24, align: "center", height: 55, color: "#b8d6c9" },
    );
    const cue = d.reactions.find((s) => s.in <= f && f < s.out);
    if (cue) {
      const y0 = d.caption_band === "upper" ? 292 : 810,
        areaWidth = 1728,
        areaHeight = 226,
        cueText = symbols[cue.emotion] + "  " + cue.text,
        L = layout(c, cueText, areaWidth, areaHeight),
        local = f - cue.in,
        attack = h.clamp(local / 8),
        punch = cue.punchFrame ?? cue.in,
        scale = Math.min(
          1.04,
          0.88 +
            0.12 * attack +
            0.08 * Math.sin(attack * Math.PI) +
            0.08 * Math.max(0, 1 - Math.abs(f - punch) / 5),
        );
      c.save();
      c.translate(960, y0 + areaHeight / 2);
      c.scale(scale, scale);
      c.translate(-areaWidth / 2, -L.height / 2);
      c.font = `900 ${L.size}px "Noto Sans CJK SC", sans-serif`;
      c.textBaseline = "top";
      c.textAlign = "left";
      c.lineJoin = "round";
      for (const g of L.glyphs) {
        const q = h.clamp((local - g.index * (cue.stagger ?? 0) + 1) / 4);
        if (!q) continue;
        c.save();
        c.globalAlpha = q;
        const yy = g.y + 12 * (1 - q);
        c.strokeStyle = "#10131b";
        c.lineWidth = Math.max(6, L.size / 7);
        c.strokeText(g.ch, g.x + 4, yy + 6);
        c.strokeText(g.ch, g.x, yy);
        c.strokeStyle = "#fff";
        c.lineWidth = Math.max(2, L.size / 32);
        c.strokeText(g.ch, g.x, yy);
        c.fillStyle = colors[cue.emotion];
        c.fillText(g.ch, g.x, yy);
        c.restore();
      }
      c.restore();
      h.text(
        c,
        `人工反应窗口 [${cue.in}, ${cue.out}) · 强调帧 ${punch}`,
        960,
        1010,
        1650,
        26,
        { minSize: 24, align: "center", height: 40, color: "#d5dce4" },
      );
    } else
      h.text(c, "此帧没有字幕；背景内容应继续承担叙事", 960, 1010, 1650, 26, {
        minSize: 24,
        align: "center",
        height: 40,
        color: "#d5dce4",
      });
  }
  window.MG_REFERENCE = {
    id: "variety-captions",
    title: "综艺反应字幕：动作、情绪与留读",
    limits:
      "仅字幕带/主体禁区/节奏示意，不含真实背景视频，不宣称真人节目或自动识别。实际交付需将RGBA字幕接入真实素材、核对动作语义和听感；浏览器像素尚待外部验证。",
    examples,
    validate,
    render,
    recipe: {
      "适用/不适用":
        "适合真实素材上的短反应、情绪强调与人工已确认的事件；不适合将长解说压成反应字幕或自动宣布人物已经成功。",
      视觉隐喻:
        "字幕是对一个已发生或正在发生动作的即时回应，空间避让先于装饰。",
      "4–6拍": [
        "读素材动作和人物禁区",
        "反应词先缩短",
        "逐字错峰进入",
        "强调落在语义帧",
        "完整文字保持可读",
        "退场后把注意力还给素材",
      ],
      不可删特征:
        "真实语义cue、逐字而非整句伪错峰、多层描边、情绪符号、主体禁区和完整揭示后留读。",
      可替语义:
        "reactions的in/out/text/emotion/punchFrame/stagger、字幕带与subject_rect；需要更长文字就增加真实素材阅读时间。",
      "动作/转场/声音点":
        "强调点与动作/停顿同步；符号和声音服务惊讶、疑问或鼓励，不提前宣告收集/通过成功。",
      失败反例:
        "把文字放在脸/手关键动作上；13字只剩半秒稳定态；全部词同一帧弹出；用占位示意冒充真人节目验收。",
    },
  };
  window.MG_REFERENCE.inspect = (d, f, N, h) => {
    const s = d.reactions.find((s) => s.in <= f && f < s.out);
    if (!s) return { active: false };
    const count = Array.from(symbols[s.emotion] + "  " + s.text).length,
      full = s.in + (count - 1) * (s.stagger ?? 0) + 3,
      stable = Math.max(full, s.in + 8, (s.punchFrame ?? s.in) + 5);
    return {
      active: true,
      text: s.text,
      glyph_count: count,
      all_glyphs_visible: f >= full,
      fully_stable: f >= stable,
      stable_hold: s.out - stable,
    };
  };
})();
