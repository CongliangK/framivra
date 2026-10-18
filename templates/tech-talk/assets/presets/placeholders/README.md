# placeholders

timeline.skeleton.json 的占位素材（1s 320x180 黑色视频＋静音 AV / 0.5s 静音 wav / 帧驱动占位 html），保证骨架在原位可通过 `vtx validate`（vtx 要求每条视频轨至少 1 个 clip）。实例化项目时用真实素材替换。注意：在 presets/ 原位跑 validate 会生成 out/receipt.json 与 cache/ 副产物，属临时物可删，勿提交。

媒体由 [generate_placeholder_media.py](../../../../../scripts/generate_placeholder_media.py) 使用 FFmpeg 的 `color` / `anullsrc` 内建源生成，无外部素材、字体或声音输入，按根 [LICENSE](../../../../../LICENSE) 分发。生成参数及本批字节证据归 [许可清单](../../../../../docs/model-license-inventory.json)；默认执行脚本仅显示命令，`--write` 会覆盖媒体，重新生成后须复核清单哈希（不同 FFmpeg 构建可能生成不同字节）。

消费面变更公告（2026-10-18，FR43）：维护席预审通过公开生成参数替换来源未证占位媒体；保持骨架路径、尺寸与时长契约。
