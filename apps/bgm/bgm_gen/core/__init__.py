"""编排层包（PLAN.md §2）：project 装载/校验、混音总线、QC。

分层边界：core 只编排 engines（经 run_engine envelope 契约）与 ffmpeg 滤镜链，
不直接实现任何音色合成；engines/*.py 冻结不动（R3 红线）。
"""
