# v2 交付已知问题登记（红队终审 P2 项，合并后跟进）

| # | 事项 | 评级 | 状态 |
|---|---|---|---|
| ⑨ | 渲染窗口 TOCTOU（timeline 渲染中被改：指纹录新字节/产物用旧内存）——v1 继承行为，loudness 键绑定输出 stat 无撞键窗口 | 低 | 登记，暂不改 |
| ⑩ | `_plugin_params_digest` 的 except-pass 降级路径（key_map 读失败时退化为无参数成分，代码摘要仍兜底） | 低 | 登记 |
| ⑪ | doctor 部分测试依赖 git 仓存在（剥离 .git 的部署形态下版本段测试需跳过） | 低 | 登记 |
| ④ | 外部三载体核销（music_tools SKILL.md / 04_bilibili视频 AGENTS.md / _library 六链接） | 中 | **P6 已核销**（2026-09-14 实测：①music_tools SKILL.md 已指 apps/blender-bridge 新路径；③_library 六链接已直指新路径；②AGENTS.md 大体 v2 化，残留 3 处旧路径写法 + 1 句 v1 链语义——经仓根兼容 symlink 可解析，待下次工作区文档批次清理） |
| ⑫ | external 插件卸载语义与官方不对称（条目移除→键退化「未知键警告」不阻断，渲染照走——靠指纹段消失强制重渲 + gc 兜底；官方件卸载=validate FAIL） | 低 | 设计选择，登记（P6 验收期确认） |
| ⑬ | external 导入锚 sys.path append/remove 非线程安全（当前全部加载点在主线程，无现实竞态；并行化前需加锁） | 低 | 登记（P6 验收期确认） |
| ⑭ | `vtx video` 分段合成**跨运行非确定性**（同 argv 隔离复渲 4 次得 4 个 md5，voice-ime 前 3 段 100% 复现；后 5 段本稳）。**真实机制（R4-E1-v2 2026-09-14 定谳，取代 R3 时代三族假说——三族均证伪）**：链式多输入 graph 下 overlay/framesync 的**输出节律**跨运行随机（丢帧 2~4 帧/主帧×次帧配对滞后，showinfo 直证；错帧全程随机发生，静止区 checksum 相同不可见、只在层淡入/淡出被观测——「差异带在层窗口边界 ±20 帧」与 96.9% 一致率是观测偏差而非病灶位置）。已证伪的参数化路线：tpad 尾垫至段尾（EOF 裁决假说）、全输入 `-threads 1`（解码线程假说）、`filter_complex_threads`（v3.6 已排除）；framesync.c 状态机闭合，非确定源在滤镜图框架「帧何时可得」的调度依赖，参数层不可根治。**修复（结构化）**：composite() 段级多层路径改**逐层 pass 合成**——每 pass 恰 2 输入（已证单 overlay+canvas 对 PNG/prores/h264 全确定），中间物 FFV1 intra 无损落 cache/vseg_pass，pass 键/段键掺合成代码五函数联合摘要（防改码洗白）；pass 帧数硬校验 fail-fast + 帧数旁车温态快路径 + .part 原子落盘。**结果**：确定性根治——seg00/01/02 各 4×隔离复渲 md5 全等（修复前各 4/4 发散）；**已知残差（不闭合）**：pass 链与旧链式的域协商路径不同（链式被 subtitles 的 RGB 约束反向钉 rgba 全程；pass 链钉 rgba+旗标抹除后内容帧仍有全域 ±1 级域残差，seg 级 vs 链式 255/2093、vs v4.6 基线一致率显著低于链式口径 96.3%）——**视觉不可见**（差异帧 PSNR avg 47dB），像素等价、字节不等价；涉及字节级 golden 对比的流程门需按新产物重锚。冷态成本 ≈4~5×（每段 N−1 个 FFV1 pass 串行），温态 pass 全命中仅最终段编码 | 中 | **确定性已修**（2026-09-14 R4-E1-v2）；字节级域残差作为已知限制记录，闭合需 libavfilter 协商/swscale 层深挖（诊断档案 /tmp/vtx_r4/FINDINGS.md E-A1~E-C6） |
| ⑮ | validate-only 等单阶段运行在回执输入指纹翻转后落笔，`load_receipt` 归零 stages → **out/receipt.json 全量阶段记录被覆盖丢失**（实案：voice-ime v4.6 回执被 9/14 13:28 validate 覆盖，幸有 qc_report_prev/FREEZE 旁证）。安全不变量（旧成功不继承）本身正确，丢的是溯源证据 | 低 | **已修**（2026-09-14 R3-E1 最小修复：翻转前把旧回执轮转到 out/receipt.prev.json 单槽旁证，receipt.json 语义/gc/VERSIONS 消费者零变化；tests/test_cache_state.py StaleReceiptArchive 两例） |
| - | `PYTHONUTF8=1`+venv 组合的 sys.executable 劫持变体（红队发现，全局 AGENTS.md 未载） | 低 | 已知规避 `env -u PYTHONUTF8` |
