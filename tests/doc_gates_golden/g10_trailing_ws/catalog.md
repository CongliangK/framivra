# 组件目录(manifest catalog)
> 本页由 scripts/gen_manifest_catalog.py 生成区内机器区(R-22 M2;一事实一家:安装态与键面事实以本区为唯一 home,他处只许链接)。
> 读者=clone 新接手者与执行席;区内禁手改,修订走 gen 重注。

<!-- BEGIN GENERATED manifest-catalog (gen: scripts/gen_manifest_catalog.py; 区内禁手改) -->  
| 组件名 | 类型 | 版本 | 键 | hooks | installed | tests 态 | README 态 |  
| --- | --- | --- | --- | --- | --- | --- | --- |  
- source: apps/aroll/app.json:3  
| aroll | app | 1.0.0 | — | — | — | 有 | 有 |  
- source: apps/asr/app.json:3  
| asr | app | 1.0.0 | asr.enabled, asr.base_url, asr.python, asr.model_dir, asr.aligner_dir, asr.device_map, asr.vram_budget_mib | — | — | 有 | 有 |  
- source: apps/bgm/app.json:3  
| bgm | app | 1.0.0 | bgm.enabled | — | — | 无 | 有 |  
- source: apps/blender-bridge/app.json:3  
| blender-bridge | app | 1.0.0 | — | — | — | 有 | 有 |  
- source: apps/cover/app.json:3  
| cover | app | 1.0.0 | — | — | — | 无 | 有 |  
- source: apps/gates/app.json:3  
| gates | app | 1.0.0 | — | — | — | 有 | 有 |  
- source: apps/imgen/app.json:3  
| imgen | app | 1.0.0 | imgen.api_base, imgen.model_default, imgen.price_per_image, imgen.confirm_before_submit | — | — | 有 | 有 |  
- source: apps/matting/app.json:3  
| matting | app | 1.0.0 | comfyui.enabled | — | — | 无 | 有 |  
- source: apps/publish/app.json:3  
| publish | app | 1.0.0 | — | — | — | 有 | 有 |  
- source: apps/research/app.json:3  
| research | app | 1.0.0 | — | — | — | 有 | 有 |  
- source: apps/sync/app.json:3  
| sync | app | 1.0.0 | — | — | — | 有 | 有 |  
- source: apps/tts/app.json:3  
| tts | app | 1.0.0 | tts.engine, tts.repo, tts.python, tts.voice_ref, tts.edge_bin | — | — | 有 | 有 |  
- source: plugins/bgm-render/plugin.json:3  
| bgm-render | plugin | 1.2.0 | sources[]:bgm | validate, probe_source, prepare_source | true | 有 | 有 |  
- source: plugins/blender-bridge/plugin.json:3  
| blender-bridge | plugin | 1.0.0 | root:blender_bridge | validate, finish | true | 有 | 有 |  
- source: plugins/denoise/plugin.json:3  
| denoise | plugin | 2.0.0 | sources[]:denoise | prepare_source | true | 有 | 有 |  
- source: plugins/duck-static/plugin.json:3  
| duck-static | plugin | 1.0.0 | audio_tracks[]:duck_strategy | validate, audio_events | true | 有 | 有 |  
| registry | registry | — | 4 键声明 | audio_events×1, finish×1, prepare_source×2, probe_source×1, validate×3 | 4/4 | — | — |  
- source: plugins/registry.json:2  
<!-- END GENERATED manifest-catalog -->
