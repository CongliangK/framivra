"""sf2 引擎：任意 .sf2 + bank/program → wav（libfluidsynth ctypes，零额外安装）。

payload（v1）：
    sf2      名字（查 runtime.json 的 sf2 表，如 "FluidR3_GM"/"GeneralUser_GS"）或绝对路径
    bank     默认 0（128=GM 打击）
    program  默认 0
    notes    [[pitch, beat, dur, vel], ...]（BG-D7 统一记谱）
    bpm      默认 120
    gain     线性输出增益，默认 1.0
    channel  可选，默认 bank==128 ? 9 : 0

v3 R2 增（PLAN_v3 §3.2 机制 B）：
    events  控制事件数组（仅 cc/pitch_bend——描述符 events_supported 门控，
            keyswitch/program_change 被拒以护 fluid_synth_program_select 显式选音色）；
            非空时改走 midi_util.events_to_smf（notes 投影为 note 事件经
            event_sort_key 合并编码进同一 SMF）；无 events 保持 write_notes_smf
            旧路径字节不动（v1 位级锚点）。
    bend_range  弯音量程半音数（缺省 2.0；轨级声明经 track_projection 透传）。

实现：midi_util 写临时 SMF（不写 program change，音色由 fluid_synth_program_select
显式指定，可覆盖 bank 128 等非 GM-bank0 预设）→ ctypes 文件渲染器 → spec.out。

ctypes 教训（自 midi2wav.py 沿革）：所有入口必须设 argtypes——不设时指针参数按
默认 c_int 传参会截断 64 位指针，new_fluid_settings 返回值同理；restype 用 c_void_p。
"""

import ctypes
import ctypes.util
import json
import os
import shutil
import subprocess
import tempfile
import wave

import numpy as np

from .base import (BaseEngine, EngineError, default_out, measure_wav,
                   register_engine)
from . import midi_util

TAIL_BLOCKS = 40          # 播放结束后补渲块数（混响尾巴；与旧脚本一致）

# 三级解析的兜底 soname（③）：so.3→so.2→裸名（Windows DLL 名同形）
_SONAME_CANDIDATES = ["libfluidsynth.so.3", "libfluidsynth.so.2", "fluidsynth"]


def _runtime_fluidsynth_lib(runtime=None):
    """Use the same effective runtime as the engine, including explicit dicts."""
    from .base import load_runtime
    rt = load_runtime() if runtime is None else runtime
    return (rt.get("tools") or {}).get("fluidsynth_lib") or None


def _load_lib(runtime=None):
    """libfluidsynth 三级解析（M-1）：
      ① runtime.json tools.fluidsynth_lib（用户/doctor 显式指定的 so/DLL 路径）；
      ② ctypes.util.find_library("fluidsynth")（系统探测）；
      ③ 兜底 soname 列表逐个 CDLL 试载。
    全部失败抛 EngineError（带 doctor 排障指引），不裸 OSError。"""
    names = []
    cfg = _runtime_fluidsynth_lib(runtime)
    if cfg:
        names.append(cfg)
    found = ctypes.util.find_library("fluidsynth")
    if found:
        names.append(found)
    names += [n for n in _SONAME_CANDIDATES if n not in names]
    lib, tried = None, []
    for name in names:
        try:
            lib = ctypes.CDLL(name)
            break
        except OSError as e:
            tried.append(f"{name}（{e}）")
    if lib is None:
        raise EngineError(
            "libfluidsynth 加载失败（三级解析均未成功）：\n  " + "\n  ".join(tried)
            + "\n排障指引：先跑 `bgm doctor`（或 python3 bgm_gen/doctor.py）看"
              " tools.fluidsynth_lib 体检项；亦可在 MUSIC_RUNTIME 指定的用户 JSON 的 "
              "tools.fluidsynth_lib 填 so/DLL 全路径（如 "
              "/usr/lib64/libfluidsynth.so.3；Windows 为 fluidsynth.dll 全路径）"
              "后重试。")
    P = ctypes.c_void_p
    lib.new_fluid_settings.restype = P
    lib.new_fluid_synth.argtypes = [P]
    lib.new_fluid_synth.restype = P
    lib.new_fluid_player.argtypes = [P]
    lib.new_fluid_player.restype = P
    lib.new_fluid_file_renderer.argtypes = [P]
    lib.new_fluid_file_renderer.restype = P
    for fn in ["fluid_settings_setstr", "fluid_settings_setnum"]:
        getattr(lib, fn).argtypes = [P, ctypes.c_char_p,
                                     ctypes.c_char_p if "str" in fn else ctypes.c_double]
    lib.fluid_synth_sfload.argtypes = [P, ctypes.c_char_p, ctypes.c_int]
    lib.fluid_synth_sfload.restype = ctypes.c_int
    lib.fluid_synth_program_select.argtypes = [P, ctypes.c_int, ctypes.c_int,
                                               ctypes.c_int, ctypes.c_int]
    lib.fluid_synth_program_select.restype = ctypes.c_int
    lib.fluid_player_add.argtypes = [P, ctypes.c_char_p]
    lib.fluid_player_play.argtypes = [P]
    lib.fluid_player_get_status.argtypes = [P]
    lib.fluid_file_renderer_process_block.argtypes = [P]
    # 释放/停止入口同样必须设 argtypes：不设时指针按默认 c_int 传参被截断 → 段错误
    # （实测教训：gm_lofi 回归首跑 exit 139 即此因）
    for fn in ["delete_fluid_file_renderer", "fluid_player_stop", "delete_fluid_player",
               "delete_fluid_synth", "delete_fluid_settings"]:
        getattr(lib, fn).argtypes = [P]
    return lib


def fluidsynth_render(midi_path, wav_path, sf2_path, sr=44100,
                      bank=0, program=0, channel=0, tail_blocks=TAIL_BLOCKS, runtime=None):
    """MIDI → wav 底层渲染（gm_lofi.py / midi2wav.py 薄包装复用）。

    bank/program 为 None 时跳过 program_select（沿用 MIDI 文件内的 program change）。
    """
    lib = _load_lib(runtime)
    ren = player = synth = st = None

    def _drop(fn, *args):
        f = getattr(lib, fn, None)
        if f is None or args[0] is None:
            return
        try:
            f(*args)
        except Exception:
            pass

    try:
        st = lib.new_fluid_settings()
        lib.fluid_settings_setstr(st, b"audio.file.name", os.path.abspath(wav_path).encode())
        lib.fluid_settings_setstr(st, b"player.timing-source", b"sample")
        lib.fluid_settings_setnum(st, b"synth.sample-rate", float(sr))
        synth = lib.new_fluid_synth(st)
        sfid = lib.fluid_synth_sfload(synth, os.path.abspath(sf2_path).encode(), 1)
        if sfid == -1:
            raise EngineError(f"fluid_synth_sfload 失败：{sf2_path}")
        if bank is not None and program is not None:
            if lib.fluid_synth_program_select(synth, channel, sfid, bank, program) == -1:
                raise EngineError(f"program_select 失败：bank {bank} program {program}（{sf2_path}）")
        player = lib.new_fluid_player(synth)
        lib.fluid_player_add(player, os.path.abspath(midi_path).encode())
        lib.fluid_player_play(player)
        ren = lib.new_fluid_file_renderer(synth)
        while lib.fluid_player_get_status(player) == 1:   # FLUID_PLAYER_PLAYING
            lib.fluid_file_renderer_process_block(ren)
        for _ in range(tail_blocks):
            lib.fluid_file_renderer_process_block(ren)
    finally:
        # 引擎进程内可能多次渲染；按依赖逆序显式释放，避免句柄/内存累积
        _drop("delete_fluid_file_renderer", ren)
        _drop("fluid_player_stop", player)
        _drop("delete_fluid_player", player)
        _drop("delete_fluid_synth", synth)
        _drop("delete_fluid_settings", st)
    if not os.path.exists(wav_path):
        raise EngineError(f"fluidsynth 未产出输出文件：{wav_path}")


def apply_gain_wav(path, gain):
    """PCM16 wav 线性增益后处理（gain==1 时 no-op，保持字节不动）。"""
    if abs(gain - 1.0) < 1e-12:
        return
    with wave.open(path, "rb") as w:
        sr, ch, n, sw = w.getframerate(), w.getnchannels(), w.getnframes(), w.getsampwidth()
        raw = w.readframes(n)
    if sw != 2:
        raise EngineError(f"apply_gain_wav: 仅支持 PCM16（sampwidth={sw}）：{path}")
    a = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    a = np.clip(a * gain, -1.0, 1.0)
    pcm = (a * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def ffmpeg_match_wav(path, sr=None, channels=None):
    """声道/采样率与 spec 不一致时用 ffmpeg 兜底转换（原地）。一致时 no-op。"""
    if sr is None and channels is None:
        return
    m = measure_wav(path)
    if (sr is None or m["sr"] == sr) and (channels is None or m["channels"] == channels):
        return
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", path]
    if sr is not None:
        cmd += ["-ar", str(sr)]
    if channels is not None:
        cmd += ["-ac", str(channels)]
    cmd += ["-c:a", "pcm_s16le", tmp.name]
    try:
        subprocess.run(cmd, check=True)
        shutil.move(tmp.name, path)
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)


def resolve_sf2(name_or_path, runtime):
    if os.path.isabs(os.path.expanduser(name_or_path)):
        p = os.path.expanduser(name_or_path)
        if not os.path.exists(p):
            raise EngineError(f"sf2 文件不存在：{p}")
        return p
    table = runtime.get("sf2", {})
    if name_or_path in table:
        p = table[name_or_path]
        if not os.path.exists(p):
            raise EngineError(f"runtime.json sf2 表指向不存在的文件：{name_or_path} → {p}")
        return p
    raise EngineError(f"未知 sf2 名字（runtime.json sf2 表无此项）：{name_or_path}；"
                      f"可用：{sorted(table)}，或给绝对路径")


@register_engine("sf2")
class SF2Engine(BaseEngine):
    determinism = "deterministic"
    summary = "任意 .sf2 + bank/program（libfluidsynth ctypes 文件渲染）"

    def render(self, spec):
        payload = spec.get("payload", {})
        bank = int(payload.get("bank", 0))
        program = int(payload.get("program", 0))
        bpm = float(payload.get("bpm", 120.0))
        gain = float(payload.get("gain", 1.0))
        channel = int(payload.get("channel", 9 if bank == 128 else 0))
        sr = int(spec.get("sr", 44100))
        channels = int(spec.get("channels", 2))
        out = spec.get("out") or default_out("smoke", self.name, "render.wav")

        sf2_path = resolve_sf2(payload["sf2"], self.rt)
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)

        mid_tmp = wav_tmp = None
        try:
            fd, mid_tmp = tempfile.mkstemp(suffix=".mid")
            os.close(fd)
            events = payload.get("events") or []
            if events:                                  # v3 R2（PLAN_v3 §3.2）：
                combined = midi_util.notes_to_events(payload["notes"]) + list(events)
                midi_util.events_to_smf(
                    mid_tmp, combined, bpm=bpm, ch=channel, prog=None,
                    bend_range=float(payload.get("bend_range", 2.0)))
            else:                                       # 无 events：旧路径字节不动（v1 锚点）
                midi_util.write_notes_smf(mid_tmp, payload["notes"], bpm=bpm,
                                          ch=channel, prog=None)
            fd, wav_tmp = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            os.unlink(wav_tmp)                      # fluidsynth 按扩展名自动选格式，需先移走空文件
            fluidsynth_render(mid_tmp, wav_tmp, sf2_path, sr=sr,
                              bank=bank, program=program, channel=channel, runtime=self.rt)
            apply_gain_wav(wav_tmp, gain)
            ffmpeg_match_wav(wav_tmp, sr=sr, channels=channels)
            shutil.move(wav_tmp, out)
            wav_tmp = None
        finally:
            for p in (mid_tmp, wav_tmp):
                if p and os.path.exists(p):
                    os.unlink(p)

        m = measure_wav(out)
        return {"path": out, "sr": m["sr"], "channels": m["channels"],
                "duration_s": m["duration_s"], "rms_db": m["rms_db"]}
