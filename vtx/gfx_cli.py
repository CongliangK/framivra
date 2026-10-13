#!/usr/bin/env python3
"""gfx_cli.py — gfx Chrome CLI 截图回退路径的命令执行与产物校验（B04 / issue #28 / F-06 / #6）

`vtx gfx` 的 CLI 回退（VTX_GFX=cli、CDP 不可用整页回落、CDP 帧级失败补渲）原实现
（cli.py gfx() 内 shot_cli/render_cli 闭包）不检查每个 chrome --headless
--screenshot 子进程的退出码，完成度主要靠 PNG 数量推断；已存在但损坏（零字节/
魔数头不符）的 PNG 会被当作缓存命中。本模块把该路径收敛为单一权威实现：

  1. run_cli_shot()/render_cli()：逐帧执行 Chrome 截图命令——非零退出码显式
     失败并报出是哪条命令（含退出码与 stderr 尾部）；退出码 0 仍逐帧校验
     产物，缺帧/零字节/损坏帧均显式失败，绝不静默计入完成数。
  2. png_invalid_reason()/verify_frames()/require_frames()：目标 PNG 有效性
     检查——存在、非零字节、PNG 签名、完整块/CRC，以及 IDAT 的 zlib 可解码性
     （流式增量解压 + 解出总量与 IHDR 尺寸比对，issue #28 R3；隔行件在 IHDR
     处直接拒绝、zlib 流后尾随数据判损坏——R3 红队复审 D1/D2）。供调用方把
     「数量门/缓存命中门」升级为「有效性门」：损坏帧按缺失处理，绝不当作缓存命中。

命令构造与 cli.py 原 shot_cli 逐字节一致（参数序/拼写不变，视口取项目分辨率
由调用方传 width/height），位置参数序也与原闭包一致（i, d, html, chrome），
调用方可直接替换。纯标准库，无 numpy/websocket 依赖（CLI 回落态不因加速后端
缺依赖而失效）。

独立测试：python3 -m unittest tests.test_gfx_cli_fallback（假 chrome stub，
无需真 Chrome）。
"""
import struct
import subprocess
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# PNG 魔数头（RFC 2083 §3 及 spec：8 字节签名）。任务口径「\x89PNG」为其前 4
# 字节；校验取完整 8 字节签名（严格更强：前缀对而后缀错同样判损坏）。
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# stderr 尾部截取长度：报错要含 chrome 输出线索，但不让异常消息被刷屏淹没。
_STDERR_TAIL = 400

# IHDR 颜色类型 → 每像素通道数 / 合法位深组合（PNG spec §11.2.2 表 11.1）。
# 非法组合真编码器不可能产出，直接判损坏；通道数同时供 IDAT 解压总量粗校。
_IHDR_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_IHDR_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8),
                4: (8, 16), 6: (8, 16)}

# 单次 decompress 的输出上限（1 MiB）：IDAT 校验只累计解出长度、流式丢弃产物，
# 恶意高压缩流（64 KiB 压缩解出数 GiB）也不会整段驻内存（issue #28 R3）。
_MAX_ZLIB_OUT = 1 << 20


class GfxCliError(RuntimeError):
    """Chrome CLI 截图回退路径的显式失败（非零退出码 / 产物校验不过）。"""


def png_invalid_reason(path) -> "str | None":
    """校验单个目标 PNG：存在、非零字节、PNG 签名、完整块与 CRC、IDAT 可解码。

    有效返回 None；无效返回中文原因（缺失/零字节/非 PNG/截断/CRC 损坏/不支持
    隔行/IDAT 解码失败或尺寸不符/流后尾随数据），供逐帧报错与缓存命中判定
    共用。IDAT 校验为流式增量 zlib 解压（逐块读逐块喂、输出限流丢弃），不整读
    超大文件。
    """
    p = Path(path)
    try:
        if not p.is_file():
            return "缺失"
        size = p.stat().st_size
    except OSError as e:            # 竞态删除/权限等：按无效处理，不放行
        return f"不可读（{e}）"
    if size == 0:
        return "零字节"
    try:
        with open(p, "rb") as fh:
            head = fh.read(len(PNG_SIGNATURE))
            if head != PNG_SIGNATURE:
                return f"魔数头不符（前 {len(head)} 字节 = {head[:8]!r}，期望 {PNG_SIGNATURE!r}）"
            # issue #28：签名不等于完整 PNG；流式检查块边界与 CRC，拒绝截断件。
            if size < 33:
                return "截断 PNG（不足完整 IHDR 的 33 字节）"
            first, has_data = True, False
            # issue #28 R3：CRC 全对 ≠ 像素可解码——红队用「块结构完整、CRC 正确、
            # IDAT 为垃圾字节」的件骗过纯结构校验：真 CLI rc0、gfx ok:true、二次
            # 运行还缓存命中。故对全部 IDAT 块的联合 zlib 流做增量解压：解码报错、
            # 流未完整结束（eof）、解出总量与 IHDR 尺寸不符、流结束后仍有尾随
            # 数据均判损坏，绝不放行为缓存命中或已完成帧。隔行件同样不放行——
            # R3 红队复审 D1：interlace=1 跳过总量比对即可蒙混，IHDR 处直接拒绝。
            dec = zlib.decompressobj()
            raw_len = 0              # 已解出的滤波扫描线总字节数
            tail_after_eof = False   # zlib 流 eof 后仍喂入的 IDAT 尾随数据（D2）
            expect_raw = None        # 按 IHDR 算出的应解出总量（隔行已在 IHDR 处拒绝）
            while True:
                header = fh.read(8)
                if len(header) != 8:
                    return "截断 PNG（缺少 IEND 或块头）"
                length, tag = struct.unpack(">I4s", header)
                if length > size - fh.tell() - 4:
                    return "截断 PNG（块长度超出文件）"
                if first:
                    if tag != b"IHDR" or length != 13:
                        return "损坏 PNG（首块必须是完整 IHDR）"
                    data = fh.read(13)
                    w, h, depth, color, comp, filt, interlace = struct.unpack(">IIBBBBB", data)
                    if not w or not h:
                        return "损坏 PNG（IHDR 尺寸为零）"
                    channels = _IHDR_CHANNELS.get(color)
                    if channels is None or depth not in _IHDR_DEPTHS.get(color, ()) \
                            or comp or filt or interlace not in (0, 1):
                        return (f"损坏 PNG（IHDR 字段非法：位深 {depth}/颜色类型 "
                                f"{color}/压缩 {comp}/滤波 {filt}/隔行 {interlace}）")
                    if interlace:
                        # issue #28 D1：直接拒绝隔行 PNG。本管线真 Chrome
                        # （--headless --screenshot）产物从不含隔行件；原实现放行
                        # interlace=1 后跳过「解出总量与 IHDR 比对」，结构/CRC 全对、
                        # IDAT=zlib.compress(垃圾) 的隔行攻击件即蒙混为缓存命中。
                        return ("损坏 PNG（不支持隔行 PNG（interlace=1，issue #28）："
                                "本管线产物均为非隔行；隔行且尺寸不符的垃圾件无法经"
                                "总量比对拦截）")
                    expect_raw = h * (1 + (w * channels * depth + 7) // 8)
                    crc = zlib.crc32(data, zlib.crc32(tag))
                    first = False
                else:
                    crc = zlib.crc32(tag)
                    remaining = length
                    while remaining:
                        data = fh.read(min(remaining, 65536))
                        if not data:
                            return "截断 PNG（块数据不足）"
                        crc = zlib.crc32(data, crc)
                        if tag == b"IDAT":
                            # 逐块喂入联合 zlib 流；单次解压输出限流，产物即弃
                            # 只记长度——流式推进，不整读超大文件也不驻留解压产物。
                            try:
                                tail = data
                                while tail:
                                    out = dec.decompress(tail, _MAX_ZLIB_OUT)
                                    raw_len += len(out)
                                    nxt = dec.unconsumed_tail
                                    if not out and nxt == tail:
                                        # 无进展的病态件：流已 eof 则滞留的 tail 即
                                        # 流后尾随数据（D2）；未 eof 由 IEND 的 eof 门拒绝。
                                        if dec.eof:
                                            tail_after_eof = True
                                        break
                                    tail = nxt
                            except zlib.error as e:
                                return f"损坏 PNG（IDAT zlib 解码失败：{e}）"
                        remaining -= len(data)
                stored_crc = fh.read(4)
                if len(stored_crc) != 4 or struct.unpack(">I", stored_crc)[0] != crc & 0xFFFFFFFF:
                    return "损坏 PNG（块 CRC 不符）"
                if tag == b"IDAT" and length:
                    has_data = True
                if tag == b"IEND":
                    if length or not has_data:
                        return "损坏 PNG（IEND 或 IDAT 无效）"
                    if not dec.eof:
                        return "损坏 PNG（IDAT zlib 流未完整结束：垃圾或截断的压缩数据）"
                    # issue #28 D2：PNG spec 要求全部 IDAT 恰好含一个 zlib 流；
                    # 流结束（eof）后仍喂入的尾随数据（落入 unused_data 或滞留
                    # unconsumed_tail）判损坏，不给「流后再拼数据」的病态件留门。
                    if dec.unused_data or tail_after_eof:
                        return ("损坏 PNG（IDAT zlib 流结束后仍有尾随数据："
                                "须恰好一个 zlib 流）")
                    if expect_raw is not None and raw_len != expect_raw:
                        return (f"损坏 PNG（IDAT 解压得 {raw_len} 字节，与 IHDR "
                                f"{w}×{h} 应解出 {expect_raw} 字节不符）")
                    return None
    except (OSError, struct.error) as e:
        return f"不可读或截断（{e}）"


def verify_frames(outdir, frames):
    """批量校验目标帧 PNG → [(帧号, 原因)]（空列表 = 全部有效）。

    缓存命中/完成门的权威检查：缺失帧报「缺失」，零字节与魔数头不符的
    损坏帧各报其因——三者均不得当作缓存命中或已完成帧。
    """
    outdir = Path(outdir)
    bad = []
    for i in frames:
        reason = png_invalid_reason(outdir / f"f{i:04d}.png")
        if reason is not None:
            bad.append((i, reason))
    return bad


def require_frames(outdir, frames):
    """校验目标帧 PNG，任一无效 → GfxCliError 逐帧列原因。

    渲染收尾门的替换实现：glob 计数只看得见文件数，本函数同时保证
    「一帧不缺、帧帧有效」。frames 为空时恒通过。
    """
    frames = list(frames)
    bad = verify_frames(outdir, frames)
    if bad:
        lines = "\n".join(f"  f{i:04d}.png: {reason}" for i, reason in sorted(bad))
        raise GfxCliError(f"{len(bad)} 帧缺失或损坏：\n{lines}")
    return len(list(frames))


def _cli_shot_cmd(chrome, i, d, html, width, height):
    """构造第 i 帧的 Chrome 截图命令（与 cli.py 原 shot_cli 逐字节一致）。"""
    png = Path(d) / f"f{i:04d}.png"
    return [chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
            f"--window-size={width},{height}", "--default-background-color=00000000",
            f"--screenshot={png}", f"{Path(html).resolve().as_uri()}?f={i}"]


def _rc_error(cmd, proc) -> str:
    """非零退出码的报错文案：完整命令 + 退出码 + stderr 尾部。"""
    tail = (proc.stderr or b"").decode("utf-8", "replace").strip()[-_STDERR_TAIL:]
    msg = f"Chrome CLI 截图命令失败（退出码 {proc.returncode}）：{' '.join(map(str, cmd))}"
    if tail:
        msg += f"\n    stderr 尾部：{tail}"
    return msg


def run_cli_shot(i, d, html, chrome, width=1920, height=1080, timeout=None):
    """渲一帧：执行 Chrome 截图命令并校验产物。

    非零退出码 → GfxCliError（完整命令 + 退出码 + stderr 尾部）；
    退出码 0 但产物无效（缺失/零字节/魔数头不符）→ GfxCliError（命令 + 原因）。
    成功返回目标 PNG 的 Path。
    """
    cmd = _cli_shot_cmd(chrome, i, d, html, width, height)
    png = Path(d) / f"f{i:04d}.png"
    try:
        # issue #28：旧帧不能替失败命令背书，非零命令产物也不得成为下次缓存。
        png.unlink(missing_ok=True)
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:            # 可执行不存在/无执行位等：显式失败而非崩栈
        raise GfxCliError(f"Chrome CLI 截图命令无法执行：{' '.join(map(str, cmd))}\n    {e}")
    if proc.returncode != 0:
        png.unlink(missing_ok=True)
        raise GfxCliError(_rc_error(cmd, proc))
    png = Path(d) / f"f{i:04d}.png"
    reason = png_invalid_reason(png)
    if reason is not None:
        raise GfxCliError(
            f"Chrome CLI 截图产物无效（f{i:04d}.png：{reason}）：{' '.join(map(str, cmd))}")
    return png


def render_cli(i_range, d, html, chrome, width=1920, height=1080,
               workers=6, timeout=None):
    """并行渲一批帧（默认 6 并发，与原 cli.py 回落路径一致）。

    逐帧走 run_cli_shot（退出码 + 产物双重检查）；失败聚合成一条
    GfxCliError（逐帧一行、含各自命令线索），绝不静默跳过、绝不以
    PNG 数量推断完成。全部成功返回渲染帧数。
    """
    frames = list(i_range)
    if not frames:
        return 0
    errs = []

    def one(i):
        try:
            run_cli_shot(i, d, html, chrome, width, height, timeout)
        except GfxCliError as e:
            errs.append((i, str(e)))

    with ThreadPoolExecutor(max(1, workers)) as ex:
        list(ex.map(one, frames))
    if errs:
        errs.sort()
        lines = "\n".join(f"  [{i:04d}] {msg}" for i, msg in errs)
        raise GfxCliError(
            f"Chrome CLI 回退渲染 {len(errs)}/{len(frames)} 帧失败：\n{lines}")
    return len(frames)
