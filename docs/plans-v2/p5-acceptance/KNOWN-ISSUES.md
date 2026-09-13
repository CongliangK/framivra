# v2 交付已知问题登记（红队终审 P2 项，合并后跟进）

| # | 事项 | 评级 | 状态 |
|---|---|---|---|
| ⑨ | 渲染窗口 TOCTOU（timeline 渲染中被改：指纹录新字节/产物用旧内存）——v1 继承行为，loudness 键绑定输出 stat 无撞键窗口 | 低 | 登记，暂不改 |
| ⑩ | `_plugin_params_digest` 的 except-pass 降级路径（key_map 读失败时退化为无参数成分，代码摘要仍兜底） | 低 | 登记 |
| ⑪ | doctor 部分测试依赖 git 仓存在（剥离 .git 的部署形态下版本段测试需跳过） | 低 | 登记 |
| ④ | 外部三载体核销（music_tools SKILL.md / 04_bilibili视频 AGENTS.md / _library 六链接） | 中 | 合并后立即执行（本文档同批） |
| - | `PYTHONUTF8=1`+venv 组合的 sys.executable 劫持变体（红队发现，全局 AGENTS.md 未载） | 低 | 已知规避 `env -u PYTHONUTF8` |
