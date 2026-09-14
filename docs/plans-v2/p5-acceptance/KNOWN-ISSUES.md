# v2 交付已知问题登记（红队终审 P2 项，合并后跟进）

| # | 事项 | 评级 | 状态 |
|---|---|---|---|
| ⑨ | 渲染窗口 TOCTOU（timeline 渲染中被改：指纹录新字节/产物用旧内存）——v1 继承行为，loudness 键绑定输出 stat 无撞键窗口 | 低 | 登记，暂不改 |
| ⑩ | `_plugin_params_digest` 的 except-pass 降级路径（key_map 读失败时退化为无参数成分，代码摘要仍兜底） | 低 | 登记 |
| ⑪ | doctor 部分测试依赖 git 仓存在（剥离 .git 的部署形态下版本段测试需跳过） | 低 | 登记 |
| ④ | 外部三载体核销（music_tools SKILL.md / 04_bilibili视频 AGENTS.md / _library 六链接） | 中 | **P6 已核销**（2026-09-14 实测：①music_tools SKILL.md 已指 apps/blender-bridge 新路径；③_library 六链接已直指新路径；②AGENTS.md 大体 v2 化，残留 3 处旧路径写法 + 1 句 v1 链语义——经仓根兼容 symlink 可解析，待下次工作区文档批次清理） |
| ⑫ | external 插件卸载语义与官方不对称（条目移除→键退化「未知键警告」不阻断，渲染照走——靠指纹段消失强制重渲 + gc 兜底；官方件卸载=validate FAIL） | 低 | 设计选择，登记（P6 验收期确认） |
| ⑬ | external 导入锚 sys.path append/remove 非线程安全（当前全部加载点在主线程，无现实竞态；并行化前需加锁） | 低 | 登记（P6 验收期确认） |
| - | `PYTHONUTF8=1`+venv 组合的 sys.executable 劫持变体（红队发现，全局 AGENTS.md 未载） | 低 | 已知规避 `env -u PYTHONUTF8` |
