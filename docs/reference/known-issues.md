# 当前已知问题与边界

本页保留未结项与仍被引用的修复边界；历次实验数值、提交状态修订和旧方案见[固定历史登记](https://github.com/CongliangK/framivra/blob/f596efc5e0712d6c47c1e906f31fdd6f6c4e51f4/docs/reference/known-issues.md)。历史观察不等于当前宿主或任意新素材的验收。

| # | 事项 | 评级 | 当前状态与边界 |
|---|---|---|---|
| ⑩ | 插件参数摘要降级 | 低 | `_plugin_params_digest` 获取键声明失败时仍有 except-pass 降级；不是 #86 已修的代码路径排序/身份问题。代码身份与升级边界见[缓存与回执](../cache-provenance.md#插件代码身份与升级说明86)。 |
| ⑪ | 无 `.git` 部署的 doctor 测试覆盖 | 低 | 无 `.git` 时版本探针不提供 `head/state`，而部分 doctor 版本段测试要求这些字段；仓内测试通过不等于该部署形态已验收。 |
| ④ | 外部三载体核销 | 中 | <a id="external-installation-evidence"></a>**未结（#98）；当前宿主核验 UNRUN**。music 技能源件缺失/挂载悬空、外部 AGENTS 旧兼容说明须维护方核查；Blender 六链接只属当时 checkout 观察。[固定原始核销](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/ARCHITECTURE.md#63-外部三载体核销清单文件级)不是当前迁移成功证明；仓内通过不能核销外部待办，只读核验与写态修复分开。 |
| ⑫ | external 与官方插件的卸载语义不同 | 低 | external 条目移除后，其键可退化为未知键警告；官方件卸载则 validate 失败。既有设计选择仍登记，不等于旧产物可直接认领。 |
| ⑬ | external 导入的线程安全 | 低 | 导入锚修改进程级 `sys.path`，append/remove 未加锁；并行加载前须复核，不沿用历史“当前全在主线程”的保证。 |
| ⑭ | 合成冷建与字节级确定性 | 中 | 逐层 pass 已采用，普通 GC 保留 `vseg_pass`，键种子包含 `with_subs`。历史实测的温态复用一致不能推出清缓存冷建逐位一致：仍登记 overlay 冷建窄带像素差异与 FFV1 容器字节非确定性。清缓存重渲不等于原交付件；既有 R-03 验收口径为视觉等价与温态 md5 全等，字节级闭合未立项，不据此宣称新素材已验收。 |
| - | `PYTHONUTF8=1` 与 venv 下的解释器身份变体 | 低 | 保留历史风险登记及当时的 `env -u PYTHONUTF8` 规避记录；未据此宣称当前宿主或任意 venv 已复验。 |

## 已修且仍有消费引用的边界

- **⑨ 渲染期间输入变化**：已修，验收与限制见 [#32](https://github.com/CongliangK/framivra/issues/32) / [PR #70](https://github.com/CongliangK/framivra/pull/70)；输入身份与回执的边界见[缓存与回执](../cache-provenance.md)。
- **⑮ 回执指纹翻转的旁证**：写新指纹前，旧阶段记录尽力轮转到 `out/receipt.prev.json` 单槽；旧成功不继承，旁证不是完整版本历史。当前回归为 `tests/cache/test_cache_state.py` 的 `StaleReceiptArchive`。
