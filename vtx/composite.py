"""vtx 合成层：切点选择、层预处理、段级/单 graph 合成、ass 字幕生成。

视频合成唯一实现（不许存在第二份重复实现）；旧单 graph 路径经
RenderPlan.composite(a=0/b=None) 保留（VTX_VIDEO_SEGMENTS=1 与
--draft --frames 回落路径共用同一段代码）。
"""
import hashlib, json, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from encoders import pick_vcodec, nvenc_concurrency
from hygiene import budget_gate, gfx_dir, manifest_record, seg_key
from infra import Ctx, run_parallel, filter_filename, filter_threads

# ---- 命名常量（值不变，仅命名；2026-09-10 模块化拆分收拢）----
SEG_CUT_GUARD = 20      # 切点静默半径：±guard 帧内不得有任何层端点（多段 framesync 病灶带）
SEG_CUT_RADIUS = 150    # 切点搜索半径：找不到静默帧则放弃该切点，段并入邻段
DEFAULT_SEGMENTS = 8    # VTX_VIDEO_SEGMENTS 默认段数


# ---------------------------------------------------------------- 切点选择
def choose_cutpoints(bounds, dur: int, n: int, guard: int = SEG_CUT_GUARD, radius: int = SEG_CUT_RADIUS):
    """阶段二切点选择（纯函数，2026-09-10）：在均匀网格候选位附近搜索「静默帧」。

    静默帧定义：±guard 帧内无任何 clip 的 dst_in/dst_out（红线依据：多段 framesync
    病灶 100% 落在多层端点 ±20 帧密集交叠带，v3.6 渲染棒实测，见 composite 注释；
    切点避开该带 = 段边界不落任何层窗口开/闭沿，段首/段尾不产生 dst_in_rel==0 的
    中段入流配对风险）。搜索半径 radius 帧内找不到则放弃该切点（段并入邻段，
    宁少勿险）。返回升序切点帧位列表（不含 0 与 dur）。
    """
    bs = sorted({p for a, b in bounds for p in (a, b)})
    def silent(f):
        return all(abs(f - p) > guard for p in bs)
    cuts, prev = [], 0
    for i in range(1, n):
        cand = round(i * dur / n)
        found = None
        for r in range(0, radius + 1):
            for f in ((cand,) if r == 0 else (cand - r, cand + r)):
                if 0 < f < dur and silent(f) and f - prev > 2 * guard and dur - f > 2 * guard:
                    found = f; break
            if found is not None: break
        if found is not None:
            cuts.append(found); prev = found
        else:
            print(f"  · 切点候选 @{cand}：±{radius} 帧内无静默帧，该段并入邻段")
    return cuts


# ---------------------------------------------------------------- 渲染计划（原 build_video 闭包巢的显式参数对象）
@dataclass
class RenderPlan:
    """一次 video 渲染的全部显式参数（消除原闭包捕获，nseg 等先定后用）。"""
    c: Ctx
    draft: bool
    f: float                 # 草稿缩放（0.5/1.0）
    W: int
    H: int
    nframes: object = None   # --frames N（仅 --draft --frames 组合，None = 不截短）
    nseg: int = DEFAULT_SEGMENTS
    layers: list = field(default_factory=list)

    # ---- 时间戳格式化（ass）----
    def _fc(self, fr):
        c = self.c
        cs = round(fr * 100 / c.fps); hh, rem = divmod(cs, 360000); mm, rem = divmod(rem, 6000); ss, cs2 = divmod(rem, 100)
        return f"{hh}:{mm:02d}:{ss:02d}.{cs2:02d}"

    def ass_head(self):
        f, W, H = self.f, self.W, self.H
        return ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2", "",
                "[V4+ Styles]",
                "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
                f"Style: Def,Noto Sans CJK SC,{58*f:.0f},&H00F8FBFF,&H00000000,&H80000000,1,1,{3*f:.1f},{2*f:.1f},2,{60*f:.0f},{60*f:.0f},{60*f:.0f},1",
                "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

    def write_global_ass(self, path):
        c = self.c
        lines = self.ass_head()
        for s in c.tl["subtitles"]:
            lines.append(f"Dialogue: 0,{self._fc(s['in'])},{self._fc(s['out'])},Def,,0,0,0,,{s['text']}")
        path.write_text("\n".join(lines), encoding="utf-8")

    # 段级字幕（阶段二 2026-09-10）：每段一份 ass，Dialogue 帧号换算 =「全局 fc 的
    # cs − 段起点 fc 的 cs」（整数 cs 相减，与全局量化逐位一致；直接对相对帧取整
    # 会因舍入方向差 ±1cs）；负值（段前已开始的字幕）钳 0。
    def write_seg_ass(self, path, a, b):
        c = self.c
        cs_a = round(a * 100 / c.fps)
        lines = self.ass_head()
        for s in c.tl["subtitles"]:
            if s["out"] > a and s["in"] < b:
                cs = max(0, round(s["in"] * 100 / c.fps) - cs_a)
                hh, rem = divmod(cs, 360000); mm, rem = divmod(rem, 6000); ss, cs2 = divmod(rem, 100)
                fin = f"{hh}:{mm:02d}:{ss:02d}.{cs2:02d}"
                cs = max(0, round(s["out"] * 100 / c.fps) - cs_a)
                hh, rem = divmod(cs, 360000); mm, rem = divmod(rem, 6000); ss, cs2 = divmod(rem, 100)
                fout = f"{hh}:{mm:02d}:{ss:02d}.{cs2:02d}"
                lines.append(f"Dialogue: 0,{fin},{fout},Def,,0,0,0,,{s['text']}")
        path.write_text("\n".join(lines), encoding="utf-8")

    # ---------------------------------------------------------------- 单 graph / 单段合成
    def composite(self, layers_subset, out_path, with_subs, a=0, b=None, sub_file=None):
        # 阶段二（2026-09-10）：a/b = 本段帧区间 [a,b)，段内时间从 0 起。默认
        # a=0/b=dur 与旧单 graph 逐字节等价（VTX_VIDEO_SEGMENTS=1 回落路径）。
        # ⚠ 不可把主链/enable 换到全局时间坐标：实测 -fps_mode cfr 输出级在首帧
        # pts≠0 时整段冻结（最小复现：canvas+setpts(+10s)+cfr → 300 帧全为首帧
        # 复制，2026-09-10）。故段内一律相对时间。
        c, draft, f = self.c, self.draft, self.f
        W, H, nframes = self.W, self.H, self.nframes
        b = c.dur if b is None else b
        seglen = b - a
        if sub_file is None:
            sub_file = c.cache / ("subs_draft.ass" if draft else "subs.ass")
        cmd = ["ffmpeg", "-v", "error", "-y",
               "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={c.fps}:d={seglen * c.spf:.3f}"]
        for L in layers_subset:
            rel_in = L["cl"]["dst_in"] - a   # 段内相对起点（可负 = 跨段界的层）
            # 头窗草稿（--frames）：截短每路输入。否则 -frames:v 触发的 drain 会把
            # 52 路输入全部解到 EOF（voice-ime 实测 300 帧耗 4:32，全量解码即瓶颈，
            # 2026-09-08 定位）；截短后仅解码头窗所需帧。（仅旧单 graph 路径使用）
            topts = (["-t", f"{max(2, nframes - L['cl']['dst_in']) / c.fps:.3f}"]
                     if (draft and nframes) else [])
            if L["gfx"]:
                # PNG 序列按 clip 内帧序号读入（start_number=段起点之前的已过帧数）：
                # 首帧内容 = 全局帧 max(dst_in, a)。a=0 时恒为 0，与旧路径逐字节一致。
                cmd += ["-framerate", str(c.fps), "-start_number", str(max(0, -rel_in)), *topts,
                        "-i", str(L["pngdir"] / "f%04d.png")]
            else:
                # 跨段界层（rel_in<0）：输入 -ss 对齐段起点（首帧内容=全局帧 a）。
                # 实测 -ss 提帧与全解提帧逐字节一致（prores mov / h264 mp4 各验）。
                # 红线教训（2026-09-10 最小复现定位）：不得把次输入 setpts 到负时间——
                # 负 PTS 下 overlay framesync 在输入 EOF 附近渐暗、repeat 失效透传
                # 主帧（u2_backends 跨 2791 切点复现，3033-3364 帧 gfx 层塌暗）。
                ss = ["-ss", f"{max(0, -rel_in) / c.fps:.6f}"] if rel_in < 0 else []
                cmd += [*ss, *topts, "-i", str(L["seg"])]
        parts = ["[0:v]format=yuv420p[cv]"]; cur = "cv"
        for k, L in enumerate(layers_subset):
            cl = L["cl"]; idx = k + 1
            rel_in = cl["dst_in"] - a
            rel_out = rel_in + cl["dur"]
            # enable 界值（2026-09-10 定稿，确定性取舍，不依赖浮点量化运气）：
            # 旧全片 graph 的 t1 = %.6f(dst_out·spf)，dst_out 帧是否显示取决于量化
            # 方向——dst_out·10⁶ mod fps == 0（整）或 > 1/2（进位）→ t1 ≥ 帧 pts →
            # dst_out 帧显示（层重复末帧，如 5160）；< 1/2 → 提前 1 帧切层（如
            # 4312、13003、4024，golden 实证一致）。段内用显式偏移复刻该取舍：
            #   含 dst_out → t1 = (rel_out+¼帧)·spf（含 rel_out、不含 rel_out+1）
            #   不含 dst_out → t1 = (rel_out−½帧)·spf（含 rel_out−1、不含 rel_out）
            # 两者均落在帧网格 ±¼~½ 帧，比较余量 ≥ 1/120s，无浮点边界。
            # t0 = (rel_in−pre)·spf：dst_in 帧在旧路径恒显示（pre 半帧放宽兜住
            # 量化方向，见下），rel_in−1 帧恒不显示，同为确定性。
            g_out = cl["dst_in"] + cl["dur"]
            m = (g_out * 10**6) % c.fps
            incl_end = (m == 0) or (2 * m > c.fps)
            eff = max(rel_in, 0)
            pre = 0.5 if rel_in != 0 else 0
            lo = rel_in * c.spf - pre * c.spf
            if a:
                t1 = (rel_out + (0.25 if incl_end else -0.5)) * c.spf
            else:
                t1 = rel_out * c.spf   # a=0 回落路径：与旧公式逐字节一致
            # 次输入提前半帧（rel_in=0 无余量除外）：PNG 序列输入瞬间涌入时 framesync
            # 对「次帧 PTS 恰等于主帧」的首帧偶发配对失败（该层 dst_in 首帧整帧丢失=黑帧，
            # 非确定性复现，2026-08-25 voice-ime 轮7 实测）。次帧恒早于主帧半个采样后，
            # framesync 始终能取到「最近过去帧」配对；视觉前移 1/48s 无感。enable 窗口
            # 同步放宽半帧保持覆盖不变。
            # （段内换算 2026-09-10：时移基准取 max(rel_in, 0)——跨段界层的输入已被
            # -ss/start_number 截到段起点（流首内容=全局帧 a）。pre/pad 对跨界层
            # 同样启用——实测教训：跨界层不垫帧时次输入 EOF 前约 3 帧整帧丢失
            # （u2_backends 跨 2791 切点，3350-3352 黑帧；A/B 复现：tpad 1/1 后逐帧
            # 与旧全片一致），与 v3.5 渲染棒「垫帧推开 framesync 边界异常窗口」同族。
            # rel_in==0 仅出现在 a=0（切点静默约束保证中段切点 ±20 帧内无任何
            # dst_in），与旧路径 dst_in==0 分支同构。）
            # 草稿模式：各层统一缩到 f 倍（seg/PNG 均按 iw/ih 等比，含窗口 clip）
            shrink = f"scale=trunc(iw*{f}/2)*2:trunc(ih*{f}/2)*2," if draft else ""
            # 2026-09-08 v3.5 渲染棒：tpad 首帧克隆垫片。根因（A/B 已证编码器无关）：
            # 57 层大 graph 下 framesync 对「窗口开启首帧」确定性配对失败——主链帧到达
            # overlay 时次输入首帧尚未产出，framesync 透传主帧=该层整帧丢失（voice-ime
            # v3.5 五点 100% 复现：3859/6049/6499/11779/14029，NVENC/x264 同集）。
            # 修复：次输入头部克隆 1 垫帧（pts=窗口前 1.5 帧），使 slave 流在主帧到达
            # 前已进入传输状态；真首帧 pts 与映射不变，垫帧时刻 enable=0 永不显示。
            # stop 垫：流尾克隆尾帧 1 帧（pts=原尾帧+1/30），把 EOF 边界的主帧配对
            # 异常窗口推开 1 帧；克隆尾帧即使被 enable 右端点（闭区间）拾取，画面与
            # 原尾帧完全相同，视觉零差异（voice-ime r5 实测 5 确定点全消、剩 3858
            # 尾帧侧单点，r6 验证）。
            # ⚠ v3.6 渲染棒实测教训（2026-09-08）：垫帧量扩大（start 2/stop 24）对
            # 「主链中段丢帧」型病灶无效（病灶在次输入流传输中段而非头尾边界），
            # 且 stop 过大时克隆尾帧会被输出为片尾 extra 帧（voice-ime v3.6 r3：
            # 尾部多 10 帧、时长 594.833）。保持 1/1，不要再加大。
            pad = (f"tpad=start_duration={1/c.fps:.6f}:start_mode=clone:"
                   f"stop_duration={1/c.fps:.6f}:stop_mode=clone," if pre else "")
            shift = eff - pre - (1 if pad else 0)
            parts.append(f"[{idx}:v]{shrink}{pad}setpts=PTS+{shift}/{c.fps}/TB[L{k}T]")
            # eof_action=repeat（非 pass；ffmpeg≥5 枚举名）：pass 在「窗口右端点主帧遇次输入
            # EOF」时直通丢层（黑帧）；repeat 克隆尾帧，窗口外由 enable=0 屏蔽，无残留风险。
            parts.append(f"[{cur}][L{k}T]overlay={int(L['x'] * f)}:{int(L['y'] * f)}:format=auto:"
                         f"enable='between(t,{lo:.6f},{t1:.6f})':eof_action=repeat[L{k}]")
            cur = f"L{k}"
        if with_subs:
            parts.append(f"[{cur}]subtitles=filename={filter_filename(sub_file)}[vout]")
        else:
            parts.append(f"[{cur}]null[vout]")
        vcodec, enc = pick_vcodec(c, draft)
        # -filter_complex_threads：滤镜图线程数（infra.filter_threads，默认 1，
        # VTX_FILTER_THREADS=4 可显式解锁多线程）。历史病灶记载（2026-09-08
        # v3.6 渲染棒，61 层 graph 三渲实测）：多线程 filtergraph 下多个 framesync
        # 的 enable 窗口边界在 ±20 帧内密集交叠的时间点发生主链整帧丢失（PTS 空洞：
        # draft 缺 3 / r1 缺 8 且伴 [6180,6193) 近黑崩溃带 / r2 缺 6 / r3 垫帧扩版
        # 缺 10——垫帧参数无关，病灶在次输入流传输中段），当时归因调度竞态故强制 1。
        # 该竞态归因已于 2026-09-12 证伪过时：61 层 ±20 帧密集端点复现图（exp6d
        # 形态，收编为 tests/test_filter_threads.py）threads=1 与 4 输出 MD5 逐位
        # 一致，且 4 快 1.9×（26.17s vs 13.54s）。默认暂不放开 4（bench_p2 全链
        # A/B，2026-09-12）：真实项目前 3 段存在与线程数无关的跨运行非确定性
        # （同 argv 复渲 4 次 4 个 md5，与 v3.6「病灶非确定性、与线程数无关」记载
        # 自洽；其余 5 段 t1/t4/基线逐字节全等），逐字节等价门不可复现；且 8 段
        # 并行 + x264 多线程下 threads=4 无墙钟收益（98/99s vs 99s）——单线程
        # 滤镜主链的 2× 收益只在单进程大 graph 场景，分段并行管线吃不到。
        # 输出端 CFR 强制（2026-09-08 v3.6 渲染棒，主控拍板兜底，真修复保留）：
        # 61 层大 graph 的 framesync 存在系统性「层窗口边界密集区中段主链丢帧」
        # （voice-ime v3.6 六渲实测：NVENC/x264、多/单线程、tpad 1/1 与 2/24 参数
        # 无关，病灶 100% 落在多层共享相近端点的 ±20 帧带，缺连续 2-4 帧 PTS
        # 空洞）。-fps_mode cfr -r 30 使输出端按 30fps 网格强制补帧（相邻帧复制），
        # 缺帧恰好落在层切换点（画面相对静止的 gfx 淡出带），1-4 帧复制视觉不可见。
        # 交付口径：这些帧是复制补偿帧而非原渲染帧，须 blackdetect+帧数+断点内容
        # 抽验三重确认。
        c.run(cmd + ["-filter_complex_threads", str(filter_threads()),
                     "-filter_complex", ";".join(parts), "-map", "[vout]",
                     "-frames:v", str(nframes if (draft and nframes) else seglen),
                     "-fps_mode", "cfr", "-r", str(c.fps),
                     *vcodec, str(out_path)])
        return enc

    # ---- 阶段二：段级编码 → concat demuxer -c copy 拼接（2026-09-10）----
    # 单条 61 层全片 graph 的滤镜链是单线程硬瓶颈（voice-ime 实测 CPU 7.8%、511s）。
    # 分段后每段独立 NVENC + 线程池并行（线程数 = min(段数, cpu//4)），段间以 concat
    # demuxer -c copy 无损拼接（各段统一编码参数，copy 拼接零重编码零画质损失）。
    # 段长由 -frames:v 钳到精确帧数（tpad stop 垫帧多出的尾帧被裁掉），拼接后总帧数
    # 必须 == project.duration_frames，不等即报错退出（正确性自检，不静默）。
    # 注：此处段间并行度（N=8 时 8 线程）与 infra.io_workers（上限 6，IO 混合任务）
    # 是两套各自用途的并行度，不统一（见 infra 注释）。
    def _seg_content_key(self, a, b, sl, with_subs, sfile) -> str:
        """段内容键（PR#1 合入融合，替代 PR 的 tag-n{nseg}-gpu{gpu} 目录变体）：
        sha1(段区间 + 画布/模式/gpu + 每层上游内容键与参数 + 段字幕 ass 字节)[:12]。

        段文件名携带内容键后，存在性+帧数缓存不再可能吃到旧内容——PR 回归场景
        「改字幕/改图层/换 gfx 内容后分段缓存仍命中旧段」从机制上关闭；nseg/gpu
        变体隔离（PR④）由 a|b 区间与 gpu 位天然覆盖。上游层标识复用 13bed7a 的
        内容寻址键（seg 文件名 / gfx 目录名）——底层内容变，段键随之变。
        """
        h = hashlib.sha1()
        h.update(f"{a}|{b}|{self.W}x{self.H}|{int(bool(self.draft))}"
                 f"|{int(bool(getattr(self.c, 'gpu', False)))}\n".encode())
        for L in sl:
            ident = L["pngdir"].name if L["gfx"] else L["seg"].name   # 上游内容键
            h.update((ident + "|" + json.dumps(L["cl"], sort_keys=True, ensure_ascii=False)
                      + f"|{L['x']}|{L['y']}|{L['w2']}|{L['h2']}\n").encode())
        if with_subs and sfile is not None and sfile.exists():
            h.update(sfile.read_bytes())
        return h.hexdigest()[:12]

    def render_segmented(self, out_path, layer_set=None, with_subs=True, tag="vseg"):
        c, draft = self.c, self.draft
        lset = self.layers if layer_set is None else layer_set
        vdir = c.cache / tag; vdir.mkdir(exist_ok=True)
        bounds = [(L["cl"]["dst_in"], L["cl"]["dst_in"] + L["cl"]["dur"]) for L in lset]
        cuts = choose_cutpoints(bounds, c.dur, self.nseg)
        edges = [0] + cuts + [c.dur]
        print(f"  分段渲染：{len(edges) - 1} 段，切点 {cuts}"
              f"（VTX_VIDEO_SEGMENTS={self.nseg}，guard=±{SEG_CUT_GUARD} 帧）")
        vcodec, enc = pick_vcodec(c, draft)
        def render_one(si):
            a, b = edges[si], edges[si + 1]
            sfile = None
            if with_subs:
                # dry-run 豁免（有意）：段字幕 ass/清单（list.txt）属 cache 侧再生物，且
                # 段内容键哈希其字节——不写会导致 dry 预览的段文件名与真实运行不一致。
                sfile = vdir / f"subs_{a:06d}.ass"; self.write_seg_ass(sfile, a, b)
            sl = [L for L in lset if L["cl"]["dst_in"] < b and L["cl"]["dst_in"] + L["cl"]["dur"] > a]
            segp = vdir / f"seg{si:02d}_{a:06d}_{self._seg_content_key(a, b, sl, with_subs, sfile)}.mp4"
            # 段缓存（PR#1 内容键化）：存在且流头帧数==段长即跳过——键已含全部内容
            # 决定因素（字幕/图层/上游内容/参数），命中即安全；帧数检查是便宜兜底。
            if segp.exists():
                fr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                     "-show_entries", "stream=nb_frames", "-of", "csv=p=0", str(segp)],
                                    capture_output=True, text=True,
                                    encoding="utf-8", errors="replace").stdout.strip()
                if fr.isdigit() and int(fr) == b - a:
                    print(f"  ✓ 段 {si} [{a},{b}) 已缓存"); return segp
            self.composite(sl, segp, with_subs, a=a, b=b, sub_file=sfile)
            return segp
        # NVENC 会话上限探测：并发发起 workers 路编码探测，任一失败回落串行
        workers = min(len(edges) - 1, max(1, (os.cpu_count() or 8) // 4))
        if workers > 1 and not c.dry and enc.startswith("NVENC"):
            workers = nvenc_concurrency(c, workers)
        print(f"  段间并行：{workers} 线程")
        if c.dry:
            segs = [render_one(si) for si in range(len(edges) - 1)]
        else:
            with ThreadPoolExecutor(workers) as ex:
                segs = list(ex.map(render_one, range(len(edges) - 1)))   # map 保序 → concat 顺序确定
        lst = vdir / "list.txt"
        # PR#1（Windows 实测）：相对路径 concat 项在含 '#' 等特殊字符的目录下会被
        # 按清单目录做 URL 基准解析出错；绝对 POSIX 路径 + 引号转义规避（-safe 0 已带）。
        def concat_entry(path):
            name = path.resolve().as_posix().replace("'", "'\\''")
            return f"file '{name}'\n"
        lst.write_text("".join(concat_entry(s) for s in segs), encoding="utf-8")
        c.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
               "-c", "copy", str(out_path)])
        # 内置正确性自检：容器元数据总帧数必须精确 == project.duration_frames
        # （dry-run 下产物不落地，探测必空 → 跳过自检；否则冷态 all --dry-run 在此误报退出）
        if c.dry:
            print(f"  [dry-run] 跳过拼接自检（产物未产出，帧数以段切点计算为准 = {c.dur}）")
            return enc
        fr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                             "-show_entries", "stream=nb_frames", "-of", "csv=p=0", str(out_path)],
                            capture_output=True, text=True,
                            encoding="utf-8", errors="replace").stdout.strip()
        if not (fr.isdigit() and int(fr) == c.dur):
            print(f"✗ 分段拼接自检失败：总帧数 {fr or '?'} ≠ {c.dur}（段边界丢帧/垫帧泄漏，禁止交付）")
            sys.exit(1)
        print(f"  ✓ 拼接自检：总帧数 {fr} == {c.dur}")
        return enc


# ---------------------------------------------------------------- 图层预处理（整片与区间渲染共用）
def prepare_layers(c: Ctx):
    """全部视频轨 clip 的图层预处理 + seg 预编码（缓存命中即跳过），返回 layers 列表
    （每项 {cl, seg|pngdir, x, y, w2, h2, gfx, ti}，顺序=图层序）。

    自 build_video 提取（方案五阶段1 区间渲染）：seg 键与渲染窗口无关（内容寻址），
    整片与区间共用同一份 cache/seg——区间渲染不得另造键或另写命名空间。本函数只做
    seg/gfx 缓存侧副作用；manifest 记账与回执由调用方负责（区间渲染不记账不写回执）。
    """
    (c.cache / "seg").mkdir(parents=True, exist_ok=True)
    tracks = c.tl["video_tracks"]
    # 统一预处理：全屏 clip（无 scale/x/y）经 transform 归一到项目分辨率；窗口 clip 按 scale 缩放。
    # 需要 alpha（opacity<1 或 fade）→ ProRes 4444；纯不透明全屏 → libx264。所有轨道等价。
    layers = []      # 每项: {cl, seg|pngdir, x, y, w2, h2|None, gfx:bool, ti}
    enc_tasks = []   # (label, cmd)：待并行编码的 seg（缓存命中的不进列表）
    for ti, tr in enumerate(tracks):
        for ci, cl in enumerate(tr["clips"]):
            kind = tr.get("kind")
            if kind == "gfx":
                # gfx 帧缓存目录 = 内容寻址键（与 gfx 阶段同键，见 hygiene.gfx_key；
                # PR#1 的路径哈希 gfx_dir 被此方案取代——内容键同样隔离同名页/换时长）
                layers.append({"cl": cl, "pngdir": gfx_dir(c, cl),
                               "x": cl.get("x", 0), "y": cl.get("y", 0), "w2": None, "h2": None,
                               "gfx": True, "ti": ti})
                continue
            windowed = any(k in cl for k in ("scale", "x", "y"))
            need_alpha = cl.get("opacity", 1) < 1 or cl.get("fade_in_frames") or cl.get("fade_out_frames")
            vf = [f"trim=start_frame={cl['src_in']}:end_frame={cl['src_out']}", "setpts=PTS-STARTPTS",
                  f"fps={c.fps}"]
            if windowed:
                sc = cl.get("scale", 1.0)
                i = c.probe(c.src_path(cl["src"]))
                w2 = int(i["w"] * sc) // 2 * 2; h2 = int(i["h"] * sc) // 2 * 2
                vf.append(f"scale={w2}:{h2}:flags=lanczos")
                x, y = cl.get("x", 0), cl.get("y", 0)
            else:
                vf += [c.crop169(cl["src"]), f"scale={c.w}:{c.h}:flags=lanczos"]
                w2 = h2 = None; x, y = 0, 0
            # seg 内容寻址键（2026-09-11 治理升级）：键 = sha1(源指纹+切片/窗口/fade/编码
            # 参数)[:12]。源或 clip 参数变了键就变，旧 seg 由 gc 回收——不再依赖
            # 「源/clip 变更后须手工 rm cache/seg」（旧 D29 注释）。
            skey = seg_key(c, cl, ti, ci, windowed, need_alpha)
            if need_alpha:
                vf.append("format=yuva420p")
                if cl.get("fade_in_frames"):
                    vf.append(f"fade=t=in:st=0:d={cl['fade_in_frames']*c.spf:.6f}:alpha=1")
                if cl.get("fade_out_frames"):
                    vf.append(f"fade=t=out:st={(cl['dur']-cl['fade_out_frames'])*c.spf:.6f}:d={cl['fade_out_frames']*c.spf:.6f}:alpha=1")
                if cl.get("opacity", 1) < 1: vf.append(f"colorchannelmixer=aa={cl['opacity']}")
                seg = c.cache / "seg" / f"{skey}.mov"
                vcodec_seg = ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le"]
            else:
                vf.append("format=yuv420p")
                seg = c.cache / "seg" / f"{skey}.mp4"
                vcodec_seg = ["-c:v", "libx264", "-crf", "14", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
            # seg 缓存命中：存在且流头帧数=clip 时长即跳过（键已含全部参数，帧数检查
            # 只是便宜的兜底自检，不读内容）。
            if seg.exists():
                fr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                     "-show_entries", "stream=nb_frames", "-of", "csv=p=0", str(seg)],
                                    capture_output=True, text=True,
                                    encoding="utf-8", errors="replace").stdout.strip()
                if fr.isdigit() and int(fr) == cl["dur"]:
                    layers.append({"cl": cl, "seg": seg, "x": x, "y": y, "w2": w2, "h2": h2,
                                   "gfx": False, "ti": ti})
                    continue
            # 阶段三（2026-09-10）：待编码任务不再就地串行执行——层对象先占位（顺序
            # 不变，分段切片按 dst_in 过滤依赖顺序），收集到 enc_tasks 由线程池并行编。
            L = {"cl": cl, "seg": seg, "x": x, "y": y, "w2": w2, "h2": h2, "gfx": False, "ti": ti}
            layers.append(L)
            enc_tasks.append((f"seg t{ti:02d}c{ci:02d}（{cl['dur']} 帧）",
                              ["ffmpeg", "-v", "error", "-y", "-i", str(c.src_path(cl["src"])),
                               "-vf", ",".join(vf), "-frames:v", str(cl["dur"]), "-an",
                               *vcodec_seg, str(seg)]))
    run_parallel(c, enc_tasks, "seg 预编码")
    return layers


def build_video(c: Ctx):
    draft = getattr(c, "draft", False); nframes = getattr(c, "frames", None)
    budget_gate(c, "video")
    f = 0.5 if draft else 1.0
    W, H = ((c.w // 2) // 2 * 2, (c.h // 2) // 2 * 2) if draft else (c.w, c.h)
    layers = prepare_layers(c)
    tracks = c.tl["video_tracks"]
    for L in layers:                      # 记账：本时间轴的全部 seg（含缓存命中）
        if not L["gfx"] and L["seg"].exists() and not c.dry:
            manifest_record(c, L["seg"], "video", L["seg"].stem)
    plan = RenderPlan(c=c, draft=draft, f=f, W=W, H=H, nframes=nframes,
                      nseg=int(os.environ.get("VTX_VIDEO_SEGMENTS", str(DEFAULT_SEGMENTS))),
                      layers=layers)
    plan.write_global_ass(c.cache / ("subs_draft.ass" if draft else "subs.ass"))
    # 默认 8（阶段三 2026-09-10 调优：voice-ime 实测 N=8 89.8s vs N=6 112.9s，-20%；
    # 20 帧含段边界灰度差均值 0.135/最大帧均值 1.195，与 N=6 相对单 graph golden 同级——
    # 偏差为段边界 ±1 帧固有，非 N=8 特有。NVENC 8 并发会话实测可用，探测失败自动回落串行）
    nseg = plan.nseg
    out_main = c.out / ("draft.mp4" if draft else "video_noaudio.mp4")
    # --frames 与分段冲突（头窗截短逻辑基于全片坐标），回落旧单 graph；
    # VTX_VIDEO_SEGMENTS=1 回落旧单 graph（行为逐字节等价）。
    if nseg <= 1 or (draft and nframes):
        enc = plan.composite(layers, out_main, True)
    else:
        enc = plan.render_segmented(out_main, tag="vseg_draft" if draft else "vseg")
    # qc_clean = 仅底轨（tracks[0]）的基准画面，供 QC 做上层窗口生效比对（草稿跳过）。
    # 性能升级（2026-09-10）：仅当底轨是 gfx（无源视频可提帧）时才渲全片基准轨——
    # 那是第二遍全片合成（voice-ime 实测与主合成同量级）；底轨为普通视频轨时，
    # qc 改为对每个探测帧现场从源视频提帧合成基准（像素语义等价，见 qc 内注释），
    # video 阶段不再为 qc 付这笔全片成本。
    if not draft and tracks and tracks[0].get("kind") == "gfx":
        bottom = [L for L in layers if L["ti"] == 0]
        if nseg > 1:
            plan.render_segmented(c.cache / "qc_clean.mp4", bottom, with_subs=False, tag="vseg_qc")
        else:
            plan.composite(bottom, c.cache / "qc_clean.mp4", False)
    msg = f"黑场画布 + {len(tracks)} 轨 {len(layers)} 层等价叠加（时移）+ 字幕｜{enc}"
    if nseg > 1 and not (draft and nframes):
        msg += f"｜分段并行 N={nseg}"
    if draft:
        msg += f"｜草稿 {W}x{H}" + (f" 前 {nframes} 帧" if nframes else "")
    print("✓ 视频合成：" + msg)
    c.receipt("video_draft" if draft else "video", True, msg)
