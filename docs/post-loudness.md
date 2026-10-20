# 响度终链与旧兼容入口

正常渲染由 `vtx audio` 内置归一、限幅与终态检查；正式交付使用完整 `vtx all`，再核对成片 QC 与项目内容门。阶段依赖和操作顺序见 [runbook](skill/references/runbook.md)，响度分级见 [音频工程](skill/references/edit.md)。无需再追加 post_loudness 步骤。

`scripts/post_loudness.py`、`scripts/post_loudness.sh` 与 `vtx.stages.post_loudness_compat` 当前仍存在，但不作为正式交付、恢复或补签回执流程：

- 兼容重跑可能再次施加增益，改变已限幅产物的响度；它不保证重复执行保持字节或响度不变。
- 它可替换 `out/audio.wav`，却不更新或撤销 audio/mux/qc 阶段回执。因此既有成功回执不能证明替换后产物已经验证。
- 需要调整声音时修改 timeline，并重跑正式链；不要直接修改成片音频后沿用旧成功状态。

上述兼容入口的存在不代表已修复这些边界。本说明不提供调用配方；旧实现与历史测试可从版本历史追溯。源码依据：`vtx/stages.py` 的 `post_loudness_compat`，终链合同见 [ARCHITECTURE §1.2](ARCHITECTURE.md#12-响度终链合并limiter-入-build_audio-尾部)。
