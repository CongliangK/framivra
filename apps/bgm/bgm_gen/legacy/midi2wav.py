#!/usr/bin/env python3
"""midi2wav — MIDI → FluidR3_GM 音色 → wav（libfluidsynth ctypes，零额外安装）。
用法: python3 midi2wav.py in.mid out.wav

薄包装版（PLAN.md BG-D4，2026-08-22 R2 重构）：ctypes 渲染上移 engines/sf2.py；
argv/cwd 契约不变（2026-08-22 迁移后位于 bgm_gen/legacy/，in/out 路径相对 cwd）。
"""
import sys

SF2 = "/usr/share/soundfonts/FluidR3_GM.sf2"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print("用法: python3 midi2wav.py in.mid out.wav", file=sys.stderr)
        return 2
    mid, wav = argv
    from bgm_gen.engines.base import load_runtime
    from bgm_gen.engines.sf2 import fluidsynth_render, resolve_sf2
    runtime = load_runtime()
    sf2 = resolve_sf2("FluidR3_GM" if "FluidR3_GM" in runtime.get("sf2", {})
                      else SF2, runtime)
    # bank/program=None：音色沿用 MIDI 文件内的 program change（与旧版渲染路径一致）
    fluidsynth_render(mid, wav, sf2, sr=44100, bank=None, program=None,
                      channel=0, runtime=runtime)
    print("ok:", wav)
    return 0


if __name__ == "__main__":
    sys.exit(main())
