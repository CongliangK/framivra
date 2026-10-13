"""vtx 合成层：切点选择、层预处理、段级/单 graph 合成、ass 字幕生成。

视频合成唯一实现（不许存在第二份重复实现）；旧单 graph 路径经
RenderPlan.composite(a=0/b=None) 保留（VTX_VIDEO_SEGMENTS=1 与
--draft --frames 回落路径共用同一段代码）。
"""
import ast, contextlib, functools, hashlib, inspect, json, os, subprocess, sys, textwrap
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .encoders import pick_vcodec, nvenc_budget, nvenc_budget_value, nvenc_concurrency, seg_encoder_kind
from .hygiene import budget_gate, gfx_dir, manifest_record, seg_key
from .infra import Ctx, run_parallel, filter_filename, filter_threads
from . import probe_cache

# ---- 命名常量（值不变，仅命名；2026-09-10 模块化拆分收拢）----
SEG_CUT_GUARD = 20      # 切点静默半径：±guard 帧内不得有任何层端点（多段 framesync 病灶带）
SEG_CUT_RADIUS = 150    # 切点搜索半径：找不到静默帧则放弃该切点，段并入邻段
DEFAULT_SEGMENTS = 8    # VTX_VIDEO_SEGMENTS 默认段数


# ---------------------------------------------------------------- 合成代码摘要（段缓存防洗白）
_SYNTH_FUNCS = ("composite", "_composite_graph", "_composite_passes",
                "_layer_chain", "_layer_input_args")


def _normalized_ast_src(source: str) -> str:
    """源码 → 去注释/去 docstring/去格式的 AST 归一化文本。

    2026-09-15 口径修订（R7）：原实现哈希 inspect.getsource 原始字节，纯注释/
    docstring 提交（如 R5 收口 4e4b756「行为零变化」）也翻转段键 → 全片 ~900s
    冷重建（实测事故）。AST 归一化后只有语法树变才翻键——表达式结构、常量、
    控制流全部保留（ast.dump 含位置无关的完整语义树）；注释天生不在树中，
    docstring 是函数体首条 Expr(Constant str)，显式剔除（docstring 运行期仅
    可经 __doc__ 访问，合成路径不读它）。inspect.getsource 对类方法返回**带类体
    缩进**的源码，直接 parse 抛 SyntaxError（unexpected indent）——实测教训
    （2026-09-15 实测轮）：漏 dedent 时回退分支生效，摘要退化为原始字节哈希，
    R7 归一化在真实方法上整体空转（单测用无缩进片段未覆盖该形态）。故先
    textwrap.dedent 再 parse；dedent 后仍解析失败（真语法错误）退回原始字节
    哈希：保守失效优于崩溃。
    """
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return source

    def strip_docstrings(node):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                node.body = body[1:]
        for child in ast.iter_child_nodes(node):
            strip_docstrings(child)

    strip_docstrings(tree)
    return ast.dump(tree, include_attributes=False)


@functools.lru_cache(maxsize=1)
def _graph_code_digest() -> str:
    """段内容键掺入的合成代码摘要（R4-E1，2026-09-14，先例：插件产物键掺 12 位
    代码摘要 vtx.api.plugin_code_digest）：合成路径全部函数 **AST 归一化** 源码
    联合 sha1[:12]（归一化口径见 _normalized_ast_src：注释/docstring/格式不翻键，
    任何语法级改动必翻键）。

    为什么必须掺：段文件是字节级缓存产物，合成代码改一行（如 R4-E1 改 tpad
    尾垫、R4-E1-v2 改中间物编码）输出即变；若键不含代码内容，改码后旧段文件
    仍被 _seg_content_key 命中——脏缓存「洗白」（与插件「改代码不 bump version
    时回执翻转但产物命中旧缓存」同一盲区，A-P1-1）。

    函数集口径（R4-E1-v2 2026-09-14 修订）：曾只摘 RenderPlan.composite 单函数，
    其论证前提「composite() 是段文件内容的唯一代码决定因素」在 composite() 路由化
    （单 graph / 逐层 pass 分派）后不再成立——决定输出的 _composite_passes/
    _composite_graph/_layer_chain/_layer_input_args 改动将不翻转键（FFV1 迭代
    E-B2 恰属此类，当时仅凭中间物扩展名 .mp4→.mkv 巧合失效，非机制保证）。
    现扩为合成路径五函数联合摘要：任一行为性改动必翻转段键与 pass 键；切点逻辑
    经 a|b 入键、prepare_layers 滤镜链经上游内容键入键，不在此列。lru_cache：进程内取一次。
    """
    return hashlib.sha1("".join(
        _normalized_ast_src(inspect.getsource(getattr(RenderPlan, n))) for n in _SYNTH_FUNCS
    ).encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- 切点选择
def choose_cutpoints(bounds, dur: int, n: int, guard: int = SEG_CUT_GUARD, radius: int = SEG_CUT_RADIUS):
    """阶段二切点选择（纯函数，2026-09-10）：在均匀网格候选位附近搜索「静默帧」。

    静默帧定义：±guard 帧内无任何 clip 的 dst_in/dst_out（红线依据：多段 framesync
    病灶 100% 落在多层端点 ±20 帧密集交叠带，v3.6 渲染棒实测，见 composite 注释；
    切点避开该带 = 段边界不落任何层窗口开/闭沿，段首/段尾不产生 dst_in_rel==0 的
    中段入流配对风险）。搜索半径 radius 帧内找不到则放弃该切点（段并入邻段，
    宁少勿险）。返回升序切点帧位列表（不含 0 与 dur）。
    R4-E1 注（2026-09-14，R4-E1-v2 依实修订）：曾记载「⑭ 非确定性已由次输入
    尾垫至段尾消灭」，该断言被复渲数据证伪——seg00/01/02 尾垫修复版隔离复渲
    4 次仍各得 4 个 md5（/tmp/vtx_r4/repro_seg0{0,1,2}_new.log），非确定性主因
    不在 EOF 裁决层。guard 回归本义：作为纵深防御保留（段边界远离层端点仍
    降低 framesync 行为变化的暴露面），不是确定性前提。
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

    # ---------------------------------------------------------------- 单 graph / 逐层 pass 合成
    def composite(self, layers_subset, out_path, with_subs, a=0, b=None, sub_file=None):
        """入口（按形态路由，R4-E1-v2 2026-09-14）：
        - b=None（整片回落：VTX_VIDEO_SEGMENTS=1 / --draft --frames）或单层 → 单 graph
          （_composite_graph，旧行为；整片回落路径不承诺跨运行确定性——⑭ 病灶在
          多输入 graph，单层图已证确定，回落路径为兼容保留）；
        - 段级多层（render_segmented 常规路径）→ 逐层 pass（_composite_passes）：
          每 pass ≤2 输入（主=canvas 或上游中间物 + 次=1 层），中间物无损落 cache
          复用段缓存体系。确定性依据见 _composite_passes docstring。
        """
        if b is None or len(layers_subset) <= 1:
            return self._composite_graph(layers_subset, out_path, with_subs, a, b, sub_file)
        return self._composite_passes(layers_subset, out_path, with_subs, a, b, sub_file)

    def _composite_graph(self, layers_subset, out_path, with_subs, a=0, b=None, sub_file=None):
        # 阶段二（2026-09-10）：a/b = 本段帧区间 [a,b)，段内时间从 0 起。默认
        # a=0/b=dur 供 VTX_VIDEO_SEGMENTS=1 与 --draft --frames 回落路径共用。
        # ⚠ 不可把主链/enable 换到全局时间坐标：实测 -fps_mode cfr 输出级在首帧
        # pts≠0 时整段冻结（最小复现：canvas+setpts(+10s)+cfr → 300 帧全为首帧
        # 复制，2026-09-10）。故段内一律相对时间。
        # R4-E1（2026-09-14，R4-E1-v2 依实修订）：次输入尾垫从固定 1 帧改为垫满
        # 至段尾+2（见下方 pad 注释）。⚠ 该改动曾宣称带来「同 argv 跨运行确定性」，
        # 已被复渲数据证伪：seg00/01/02 修复后隔离复渲 4 次仍各得 4 个 md5
        # （/tmp/vtx_r4/repro_seg0{0,1,2}_new.log）——尾垫只消除段内 EOF 裁决这一族，
        # 非确定性主因未除，最终机制与修复口径以本函数 pad 注释的最新修订为准。
        c, draft, f = self.c, self.draft, self.f
        W, H, nframes = self.W, self.H, self.nframes
        b = c.dur if b is None else b
        seglen = b - a
        if sub_file is None:
            sub_file = c.cache / ("subs_draft.ass" if draft else "subs.ass")
        cmd = ["ffmpeg", "-v", "error", "-y",
               "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={c.fps}:d={seglen * c.spf:.3f}"]
        for L in layers_subset:
            rel_in = L["cl"]["dst_in"] - a
            # 头窗草稿（--frames）：截短每路输入（仅回落路径；否则 -frames:v 触发
            # 的 drain 会把全部输入解到 EOF——voice-ime 实测 300 帧耗 4:32，2026-09-08）
            topts = (["-t", f"{max(2, nframes - L['cl']['dst_in']) / c.fps:.3f}"]
                     if (draft and nframes) else [])
            cmd += self._layer_input_args(L, a, topts)
        parts = ["[0:v]format=yuv420p[cv]"]; cur = "cv"
        for k, L in enumerate(layers_subset):
            slave, ov, cur = self._layer_chain(L, k, cur, a, seglen, idx=k + 1)
            parts += [slave, ov]
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
        # 交付口径（2026-09-14，R4-E1-v2 依实修订）：曾记载「尾垫至段尾后主链不再
        # 丢帧、cfr 成零操作」，其前提（EOF 裁决=非确定性主因）已被复渲证伪（见
        # 上方 pad 注释），cfr 对本 graph 仍是兜底而非可宣告零操作。帧数/时基
        # 安全网与拼接自检（总帧数 ffprobe 硬门）保留。
        c.run(cmd + ["-filter_complex_threads", str(filter_threads()),
                     "-filter_complex", ";".join(parts), "-map", "[vout]",
                     "-frames:v", str(nframes if (draft and nframes) else seglen),
                     "-fps_mode", "cfr", "-r", str(c.fps),
                     *vcodec, str(out_path)])
        return enc

    # ---- 层级滤镜构件（单 graph 与逐层 pass 两路共用，防双实现漂移）----
    def _layer_input_args(self, L, a, topts=()):
        """单层次输入的 ffmpeg 输入选项（-i 前）：gfx PNG 序列 / seg 文件（含跨段 -ss）。
        PNG 序列按 clip 内帧序号读入（start_number=段起点之前的已过帧数）：首帧内容
        = 全局帧 max(dst_in, a)。跨段界层（rel_in<0）输入 -ss 对齐段起点——实测 -ss
        提帧与全解提帧逐字节一致（prores mov / h264 mp4 各验）。红线教训（2026-09-10
        最小复现定位）：不得把次输入 setpts 到负时间——负 PTS 下 overlay framesync
        在输入 EOF 附近渐暗、repeat 失效透传主帧（u2_backends 跨 2791 切点复现，
        3033-3364 帧 gfx 层塌暗）。topts：头窗草稿的 -t 截短（仅旧单 graph 回落路径）。
        """
        c = self.c
        rel_in = L["cl"]["dst_in"] - a   # 段内相对起点（可负 = 跨段界的层）
        if L["gfx"]:
            return ["-framerate", str(c.fps), "-start_number", str(max(0, -rel_in)),
                    *topts, "-i", str(L["pngdir"] / "f%04d.png")]
        ss = ["-ss", f"{max(0, -rel_in) / c.fps:.6f}"] if rel_in < 0 else []
        return [*ss, *topts, "-i", str(L["seg"])]

    def _layer_chain(self, L, k, cur, a, seglen, idx):
        """单层次输入滤镜段与 overlay 段。返回 (slave_part, overlay_part, out_label)。

        enable 界值（2026-09-10 定稿，确定性取舍，不依赖浮点量化运气）：旧全片
        graph 的 t1 = %.6f(dst_out·spf)，dst_out 帧是否显示取决于量化方向——
        dst_out·10⁶ mod fps == 0（整）或 > 1/2（进位）→ t1 ≥ 帧 pts → dst_out 帧
        显示（层重复末帧）；< 1/2 → 提前 1 帧切层。段内用显式偏移复刻该取舍：
          含 dst_out → t1 = (rel_out+¼帧)·spf；不含 → (rel_out−½帧)·spf。
        两者均落在帧网格 ±¼~½ 帧，比较余量 ≥ 1/120s，无浮点边界。t0 同理：
        dst_in 帧恒显示（pre 半帧放宽兜住量化方向），rel_in−1 帧恒不显示。

        次输入提前半帧 + 头垫 1 帧（rel_in=0 无余量除外）：PNG 序列输入瞬间涌入时
        framesync 对「次帧 PTS 恰等于主帧」的首帧偶发配对失败（该层 dst_in 首帧整
        帧丢失=黑帧，非确定性复现，2026-08-25 voice-ime 轮7；v3.5 渲染棒五点 100%
        复现 3859/6049/6499/11779/14029，A/B 已证编码器无关）。次帧恒早于主帧半个
        采样后 framesync 始终取「最近过去帧」配对；enable 窗口同步放宽半帧。
        段内换算（2026-09-10）：时移基准取 max(rel_in, 0)——跨段界层的输入已被
        -ss/start_number 截到段起点（流首内容=全局帧 a）；rel_in==0 仅出现在 a=0
        （切点静默约束保证中段切点 ±20 帧内无任何 dst_in）。

        尾垫（R4-E1 改版 2026-09-14 / R4-E1-v2 依实修订）：克隆垫满至段尾+2 帧。
        原假说「EOF 裁决=非确定性主因，尾垫后段内不再 EOF 裁决 → 应收敛」❌ 已被
        复渲证伪（seg00/01/02 修复后隔离复渲各 4 次仍各 4 个 md5，
        /tmp/vtx_r4/repro_seg0{0,1,2}_new.log）。尾垫的保留价值收窄为：消除段内
        EOF 裁决触发面（次输入 EOF 前丢帧/近-EOF 渐暗族的边界条件）——最终段文件
        的确定性已由逐层 pass 结构保证（_composite_passes），垫帧本身不承担确定性
        语义。语义不变论证（仍成立）：垫出的克隆尾帧 pts ≥ rel_out，enable 右端
        t1 ≤ (rel_out+¼)·spf，窗口外帧 enable=0 直通主帧；incl_end 的 ¼ 帧规则
        拾取的首个克隆帧内容==原尾帧（v3.5 r5/r6 已证）。v3.6「stop 过大→片尾
        extra 帧」教训不适用：输出端恒有 -frames:v 硬钳 + 拼接后总帧数 ffprobe
        硬门（render_segmented），extra 帧无法逸出。
        eof_action=repeat（非 pass；ffmpeg≥5 枚举名）：pass 在「窗口右端点主帧遇
        次输入 EOF」时直通丢层（黑帧）；repeat 克隆尾帧，窗口外由 enable=0 屏蔽。
        """
        c, draft, f = self.c, self.draft, self.f
        cl = L["cl"]
        rel_in = cl["dst_in"] - a
        rel_out = rel_in + cl["dur"]
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
        head = 1 if pre else 0                       # 头垫 1 帧（v3.5 修复，保持）
        stop = max(1, seglen - rel_out + 2)          # 尾垫至段尾+2；跨右界层自然退化为 1
        pad = ("tpad="
               + (f"start_duration={head / c.fps:.6f}:start_mode=clone:" if head else "")
               + f"stop_duration={stop / c.fps:.6f}:stop_mode=clone,")
        shift = eff - pre - head
        shrink = f"scale=trunc(iw*{f}/2)*2:trunc(ih*{f}/2)*2," if draft else ""
        slave = f"[{idx}:v]{shrink}{pad}setpts=PTS+{shift}/{c.fps}/TB[L{k}T]"
        ov = (f"[{cur}][L{k}T]overlay={int(L['x'] * f)}:{int(L['y'] * f)}:format=auto:"
              f"enable='between(t,{lo:.6f},{t1:.6f})':eof_action=repeat[L{k}]")
        return slave, ov, f"L{k}"

    def _pass_fresh(self, p, seglen, sidecar=False):
        """pass 中间物缓存命中：存在且精确帧数==段长。

        sidecar 快路径（仅中间 pass 用，R4-E1-v2 2026-09-14）：mkv 容器不写
        nb_frames 流元数据，-count_frames 需全文件逐包读——FFV1 无损中间物体量
        巨大（1080p×2093 帧实测 44~610MB/个，alpha 10bit 域尤甚），单次解包读帧
        数十秒（docstring 曾记「~0.5s 级」系 libx264 qp0 小文件口径，对 FFV1 失效，
        E-B3 实测温态 13 pass 校验拖成分钟级）。故创建路径帧数硬校验通过后把
        段长写入旁车 <target>.n；命中判定=旁车存在且内容==seglen 且 target 非空。
        防伪命中：旁车只在「真实 count_frames 校验通过后」落盘，ffmpeg 中断遗留
        的截断中间物无旁车；外部损坏 target 而旁车尚存的极端场景由段级总帧数
        ffprobe 硬门（render_segmented）兜底。
        非 sidecar 路径（段文件/无旁车旧产物）维持 -count_frames 实测。"""
        if not p.exists() or p.stat().st_size == 0:
            return False
        if sidecar:
            try:
                return int((p.with_suffix(p.suffix + ".n")).read_text()) == seglen
            except (OSError, ValueError):
                return False
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-count_frames", "-show_entries", "stream=nb_read_frames",
                            "-of", "csv=p=0", str(p)],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        return r.stdout.strip().isdigit() and int(r.stdout.strip()) == seglen

    def _composite_passes(self, layers_subset, out_path, with_subs, a, b, sub_file=None):
        """段内逐层 pass 合成（R4-E1-v2 核心修复，2026-09-14）：每 pass 恰 2 输入
        （主=canvas 或上游 pass 中间物，次=1 层），pass k 的输出作为 pass k+1 的
        主输入，层序与旧链式 graph 逐位同序（视觉合成语义不变）。

        为什么必须拆 pass（证据链全部在案 /tmp/vtx_r4/FINDINGS.md、
        docs/plans-v2/p5-acceptance/KNOWN-ISSUES.md ⑭）：链式 N 输入 graph 下
        overlay/framesync 的输出节律存在跨运行随机的丢帧/配对滞后——E-A5 showinfo
        直证：canvas+L9+L11 双 overlay 最小图 2 次复渲，一版 L11 输出缺 4 帧
        （pts 2054~2057）且其后 1 帧内容迟到，另一版 L9 输出旧帧内容；错帧全程
        随机发生，静止区 checksum 相同不可见，只在层淡入/淡出（enable 窗口边界
        带）被观测——这同时解释 E2 的「差异带在层窗口边界 ±20 帧」与 96.9% 帧
        一致（观测偏差而非病灶位置）。单 overlay+canvas 三种输入（PNG/prores/
        h264）各 2 次复渲全确定（E-A3）；tpad 尾垫满（EOF 裁决假说）与全输入
        -threads 1（解码线程假说）两族参数化方案均被 4×复渲证伪。framesync.c
        状态机本身闭合（miss→request 等待、同 pts 同轮推进），非确定源在滤镜图
        框架对「帧何时可得」的调度依赖 → 参数层无法钉死，结构化：把 N 输入问题
        分解为 N−1 个「已证确定的两输入 pass」。

        中间物：cache/vseg_pass/p{k}_{键}.mkv，FFV1 intra（-level 3 -g 1）无损编码，
        输出端不强制 format——保留 overlay(format=auto) 协商出的 alpha/位深，使 pass
        链的格式域演化与链式 graph 逐位同构（关键教训：曾用 libx264 qp0 + 强制
        format=yuv420p，prores alpha 10bit 层使链式自该层升 10bit/alpha 域而中间物
        落回 8bit，量化点错位造成全帧 ±1 级系统性微差——与链式成品 0/2093 帧一致、
        PSNR 46.7dB，见 E-B2/FINDINGS）。pass 键 = sha1(上游 pass 键|pass 序|合成代码
        摘要|层内容键/参数)[a|b|WxH|draft 打头][:12]——上游内容/层参数/代码任一变更
        整条 pass 链自动失效（与段键 _seg_content_key 同防洗白口径）。中间物帧数硬
        校验 == 段长（不 cfr 补帧：丢帧 fail-fast 而非静默复制；这也是对 framesync
        节律异常的在案探针）。
        成本（实测口径 R5 2026-09-15）：温态 pass 全命中=仅最终段编码一次 ffmpeg；
        冷态每段 N−1 个 pass 串行（2093 帧段 14 层 ≈ 13 pass，FFV1 每 pass ~15s 级）。
        全片冷建（8 段并行 ~104 pass）墙钟实测 900s、单代 pass 占盘 ~44G，对照 v4.0
        旧链式分段 277s ≈ 3.2×（「≈4~5×」为无实测支撑的推算值已废弃；区间多层窗口
        回落路径更慢且未被该账覆盖——R4-E2' A5）。
        gc：hygiene.gc_plan 的 known 白名单不含 vseg_pass，本目录整体落 legacy 类别
        （判定理由=「键不含内容指纹」，对内容寻址 pass 键是误描述）随 gc 回收——
        行为可辩护（可再生中间物，删后下次段重渲自动重建），后果=温态冻结语义失效、
        下次渲染全冷建（R4-E2' A3 定谳；白名单补录需动 hygiene.py=P6 遗留文件，暂缓）。
        -fps_mode cfr 只在最终 pass（正式段文件）：帧数/时基安全网 + 拼接自检
        （render_segmented 总帧数硬门），语义见 _composite_graph 同名注释。
        """
        c = self.c
        seglen = b - a
        pdir = c.cache / "vseg_pass"; pdir.mkdir(parents=True, exist_ok=True)
        prev_key = f"{a}|{b}|{self.W}x{self.H}|{int(bool(self.draft))}|{int(bool(with_subs))}"
        prev = None            # None = 主输入用 canvas（pass0）
        n = len(layers_subset)
        vcodec, enc = pick_vcodec(c, self.draft)
        for k, L in enumerate(layers_subset):
            last = k == n - 1
            ident = L["pngdir"].name if L["gfx"] else L["seg"].name
            h = hashlib.sha1()
            h.update(f"{prev_key}|pass{k}\n".encode())
            h.update(f"passgraph={_graph_code_digest()}\n".encode())
            h.update((ident + "|" + json.dumps(L["cl"], sort_keys=True, ensure_ascii=False)
                      + f"|{L['x']}|{L['y']}|{L['w2']}|{L['h2']}\n").encode())
            pkey = h.hexdigest()[:12]
            prev_key = pkey
            target = out_path if last else pdir / f"p{k}_{pkey}.mkv"
            if not last:
                if self._pass_fresh(target, seglen, sidecar=True):
                    print(f"    · pass {k + 1}/{n} 已缓存（{target.name}）")
                    prev = target
                    continue
            elif out_path.exists() and self._pass_fresh(out_path, seglen):
                # 最终 pass 也走帧数兜底（段缓存已在 render_segmented 判过，防御
                # composite 被直接调用无段缓存的场景——如 --draft --frames 回落不
                # 进此路径，b=None 时根本不到这里）
                break
            cmd = ["ffmpeg", "-v", "error", "-y"]
            if prev is None:
                cmd += ["-f", "lavfi", "-i",
                        f"color=c=black:s={self.W}x{self.H}:r={c.fps}:d={seglen * c.spf:.3f}"]
            else:
                cmd += ["-i", str(prev)]
            cmd += self._layer_input_args(L, a)
            slave, ov, cur = self._layer_chain(L, k, "base", a, seglen, idx=1)
            if k == 0:
                parts = ["[0:v]format=yuv420p[base]", slave, ov]
            else:
                # pass k>0 主输入：FFV1 的 RGB 打包往返会把 rgba 落成 bgra 且旗标
                # 标 pc/gbr（实测），format=rgba 重排回链式布局（无损），setparams
                # 把旗标抹回链式内存帧口径（E-C3 实测链式 rgba 帧 cl:unspecified）。
                parts = ["[0:v]format=rgba,setparams=range=unspecified:colorspace=unknown[base]",
                         slave, ov]
            if last and with_subs:
                parts.append(f"[{cur}]subtitles=filename={filter_filename(sub_file)}[vout]")
            elif last:
                parts.append(f"[{cur}]format=yuv420p[vout]")
            elif with_subs:
                # 域钉定 rgba（域同构核心步骤，R4-E1-v2 2026-09-14）：链式
                # graph 里 subtitles(libass) 只接受 RGB 输入，该约束经 filtergraph
                # 协商反向传播，把主链自 L0 overlay 起全程钉在 rgba（E-C3 实测：
                # seg00 链式 14 层 showinfo fmt 全=rgba、cl=unspecified，
                # /tmp/vtx_r4/FINDINGS.md）。pass 链主输入格式列表钉成 [rgba] 后，
                # overlay 的 auto 协商输入与链式相同 → 域演化逐层同构。
                # 实测史（为何必须钉）：不钉则 pass 开头的 format=yuv420p 把主链
                # 拉回 YUV 域且下游无 RGB 约束，协商自由漂移（yuva420p/bgra 混杂）
                # → 全帧 ±1 级系统性微差：vs 链式 0/2093、vs v4.6 基线 8.15%
                # （链式同测 96.3%，E-B4/E-C1/E-C2）；首版钉 rgba 未抹旗标仍
                # 255/2093（FFV1 落盘 bgra/pc/gbr，旗标路径与链式 unspec 不对称）
                # → 本版输入/输出端 setparams 抹旗标（修复迭代 4）。
                parts.append(f"[{cur}]format=rgba,setparams=range=unspecified:colorspace=unknown[vout]")
            else:
                # 无字幕路径（复渲/内部形态）：单 graph 回落路径同样无 subtitles
                # 的 RGB 反向约束，链式域本身协商自由——pass 链保持 auto 不钉
                # （确定性由单 overlay pass 结构保证，与域无关；域不承诺与链式
                # 逐位同构——该承诺只在正式段路径 with_subs=True 下成立）。
                parts.append(f"[{cur}]null[vout]")
            if last:
                tail = ["-frames:v", str(seglen), "-fps_mode", "cfr", "-r", str(c.fps),
                        *vcodec, str(out_path)]
            else:
                # FFV1 intra（-g 1）无损：像素/alpha/位深零漂移，解码域确定
                # （实测口径 R4-E2' A1d：FFV1 同输入同 argv 重复编码 4 次得 4 个
                # 不同容器 md5——容器字节层非确定；但 4 产物解码域 framemd5 逐帧
                # 全同，无损语义成立，只影响 pass 文件字节、不影响像素与缓存命中
                # 正确性，「与线程数无关的确定性编码器」旧表述作废）。落盘走 *.part.mkv
                # 临时名 + os.replace 原子替换：并发/重入读者（帧数校验、下游
                # pass）永远看到完整文件而非半截 mkv（FFV1 大文件写入窗口数十秒，
                # 直接写 target 存在互踩窗口——tests 并发复渲实测踩中，R4-E1-v2）。
                # 临时名保留 .mkv 结尾：ffmpeg 按扩展名选 muxer，.part 结尾无法
                # 初始化（实测 Invalid argument）。
                tail = ["-frames:v", str(seglen), "-c:v", "ffv1", "-level", "3",
                        "-g", "1",
                        str(target.with_name(target.stem + ".part" + target.suffix))]
            c.run(cmd + ["-filter_complex_threads", "1",
                         "-filter_complex", ";".join(parts), "-map", "[vout]", *tail])
            if c.dry:
                # dry-run：命令已由 Ctx.run 打印（[dry-run] 前缀），不执行不落盘
                # ——跳过原子替换/帧数校验/旁车，继续打印余下 pass 命令。dry 口径
                # 与单 graph 一致（编排可见、零副作用）；否则 dry 下帧数校验
                # sys.exit(1) 误杀 --dry-run（TestUntilFlag 实测回归，R4-E1-v2）
                prev = target
                continue
            if not last:
                os.replace(target.with_name(target.stem + ".part" + target.suffix),
                           target)
            if not self._pass_fresh(target, seglen):
                print(f"✗ pass {k + 1}/{n} 帧数自检失败：{target.name} ≠ {seglen} 帧"
                      f"（framesync 节律异常/中间物损坏，禁止向下游传递）")
                sys.exit(1)
            if not last:
                # 帧数硬校验通过后落旁车（_pass_fresh sidecar 快路径的凭据，
                # 见其 docstring；截断/中断产物到不了这里，不会误发旁车）
                target.with_suffix(target.suffix + ".n").write_text(str(seglen))
            prev = target
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
        sha1(段区间 + 画布/模式/gpu + 每层上游内容键与参数 + 段字幕 ass 字节
        + 合成代码摘要)[:12]。

        段文件名携带内容键后，存在性+帧数缓存不再可能吃到旧内容——PR 回归场景
        「改字幕/改图层/换 gfx 内容后分段缓存仍命中旧段」从机制上关闭；nseg/gpu
        变体隔离（PR④）由 a|b 区间与 gpu 位天然覆盖。上游层标识复用 13bed7a 的
        内容寻址键（seg 文件名 / gfx 目录名）——底层内容变，段键随之变。
        R4-E1（2026-09-14）补合成代码摘要 _graph_code_digest：composite() 改码
        （如 tpad 尾垫策略）不再依赖手工清段缓存，旧段自动键漂移失效（防洗白，
        见该函数 docstring）。
        """
        h = hashlib.sha1()
        h.update(f"{a}|{b}|{self.W}x{self.H}|{int(bool(self.draft))}"
                 f"|{int(bool(getattr(self.c, 'gpu', False)))}\n".encode())
        h.update(f"graph={_graph_code_digest()}\n".encode())
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
        # B03（issue #27 / F-05）：布局延迟创建——cli 不再无条件 mkdir cache/，
        # 段目录自建（parents 兜底 fresh 项目直渲本目录的首个落笔点）。
        vdir = c.cache / tag; vdir.mkdir(parents=True, exist_ok=True)
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
            # NVENC 会话预算（方案二阶段3）：批次开始一次性占 min(workers, 预算) 个槽、
            # 结束释放。与探测的分工（见 encoders.nvenc_budget 注释）：nvenc_concurrency
            # 管「会话真可用」（workers 已被它钳到驱动可用会话数），nvenc_budget 管
            # 「本进程内不超发」（防段渲染与 seg 预编码等进程内其他 NVENC 点叠加）——
            # 批次级整段持有，不与逐任务的探测钳制重复。x264 段不占预算。
            batch = (nvenc_budget(c, min(workers, nvenc_budget_value()))
                     if enc.startswith("NVENC") else contextlib.nullcontext())
            with batch, ThreadPoolExecutor(workers) as ex:
                segs = list(ex.map(render_one, range(len(edges) - 1)))   # map 保序 → concat 顺序确定
        lst = vdir / "list.txt"
        # PR#1（Windows 实测）：相对路径 concat 项在含 '#' 等特殊字符的目录下会被
        # 按清单目录做 URL 基准解析出错；绝对 POSIX 路径 + 引号转义规避（-safe 0 已带）。
        def concat_entry(path):
            name = path.resolve().as_posix().replace("'", "'\\''")
            return f"file '{name}'\n"
        lst.write_text("".join(concat_entry(s) for s in segs), encoding="utf-8")
        # 记录本代段缓存对应的合成代码摘要（R7）：vtx diff 据此在 timeline 侧 0 失效
        # 时仍能提示「代码摘要已漂移 → 磁盘段缓存全冷重建」，避免影响面误报温态。
        if not c.dry:
            try:
                (vdir / ".graph_digest").write_text(_graph_code_digest(), encoding="utf-8")
            except OSError:
                pass
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
    enc_tasks = []   # (label, cmd, nvenc)：待并行编码的 seg（缓存命中的不进列表）；
                     # nvenc=True 的任务在 run_ffmpeg_task 里占 NVENC 会话预算槽
    # seg 编码器判定（方案二阶段3）循环外算一次：无 alpha seg 默认 NVENC 化
    # （卸给 GPU，缓解波内与 gfx 的 CPU 竞争），VTX_SEG_NVENC=0 或探测失败回落
    # x264。判定单一来源 encoders.seg_encoder_kind——hygiene.gc 的键集重算共用
    # 同一函数，防键集漂移误回收。alpha 段 ProRes 4444 完全不动。
    seg_kind = seg_encoder_kind(c)
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
            skey = seg_key(c, cl, ti, ci, windowed, need_alpha,
                           "prores" if need_alpha else seg_kind)
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
                if seg_kind == "nvenc":
                    # NVENC p4 + vbr cq14（方案二阶段3）：cq 14 对齐 x264 crf14 的
                    # 中间物质量档；编码卸给 GPU，缓解流水线波内与 gfx 的 CPU 竞争
                    vcodec_seg = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr",
                                  "-cq", "14", "-b:v", "0", "-pix_fmt", "yuv420p"]
                else:
                    # x264 回退串保持原样（VTX_SEG_NVENC=0 显式回退或探测失败回落）
                    vcodec_seg = ["-c:v", "libx264", "-crf", "14", "-preset", "veryfast",
                                  "-pix_fmt", "yuv420p"]
            # seg 缓存命中：存在且流头帧数=clip 时长即跳过（键已含全部参数，帧数检查
            # 只是便宜的兜底自检，不读内容）。帧数检查经 probe_cache（(路径,mtime_ns,
            # size) 键缓存 ffprobe 结论）：69 层逐层 ffprobe 温态实测 8.7s（区间渲染
            # --range 0 6 总 10.5s 的 83%），缓存后温态零 ffprobe；文件被删/被换
            # （mtime/size 变或不存在）自动 miss 回落真实 ffprobe，不漏检缓存失效。
            if seg.exists():
                fr = probe_cache.ffprobe_nb_frames(c, seg)
                fr = str(fr) if fr is not None else ""
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
                               *vcodec_seg, str(seg)],
                              not need_alpha and seg_kind == "nvenc"))
    run_parallel(c, enc_tasks, "seg 预编码")
    # This task also runs independently in the all pipeline wave.
    stage = "video_draft" if getattr(c, "draft", False) else "video"
    c.verify_inputs(None if getattr(c, "range_window", None) else stage)
    return layers


def build_video(c: Ctx, layers=None):
    """layers=None（单跑 `vtx video` / 线性 all）：内部现做 prepare_layers（行为逐字
    不变）；流水线模式（方案二阶段2）由波内「video 预编码」job 预计算传入，跳过
    重复扫描——layers 是纯数据（clip→缓存路径映射 + 参数），先算后用无时序风险
    （seg 内容寻址键与渲染时机无关，波内先行写入的段文件 video 阶段直接命中）。
    """
    draft = getattr(c, "draft", False); nframes = getattr(c, "frames", None)
    # budget_gate 仍在 video 阶段开头执行（流水线下为提示性门）：seg 预编码已在波内
    # 先行落盘，但 gc 只回收「键集外」条目——先行写入的当前键 seg 不在回收集，
    # 无冲突；超预算提示/自动回收语义与线性路径一致。
    budget_gate(c, "video")
    f = 0.5 if draft else 1.0
    W, H = ((c.w // 2) // 2 * 2, (c.h // 2) // 2 * 2) if draft else (c.w, c.h)
    if layers is None:
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
