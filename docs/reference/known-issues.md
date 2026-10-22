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

## 色彩编码

#101 留存报告尚未验收；未查询线上状态。`59811757809642534591e77ee86669aeccbcdbf7` 的 Linux/FFmpeg8.1.3/RTX4080 单帧 RGB24 八色块显示默认 YUV420P 同 BT.601 limited、不同 BT.709 limited；不覆盖任意 YUV 输入、完整图、原色/传递函数。**不能仅贴 bt709/tv 标签**：先设计实际矩阵/范围转换及原色/传递函数来源，保留既有 YUV 解释；改像素须独审、重建 Golden，不称“仅元数据零差”。新标签零差、完整 Golden/QC、响度与多帧全链帧数均 **UNRUN**。[固定原测量](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/reference/color-encoding.md) 保留样本、失败与摘要；仓外临时证据不等于持久归档。

[测量探针](../../tests/runners/probe_color_encoding.py) 默认不入套件，要求 x264 medium/veryfast、NVENC p4/p7 四档真实可用，不以 CPU 替代 NVENC。`--help` 仅查看帮助；实际执行会写合成素材/结果，必须以 `--output` 指定不存在的新目录，拒绝覆盖。

## 长流 sidechain

历史长流无口播窗口床乐反升约19dB，短流正常，深层机制**未定位**；不据短探针推断整片安全，也不宣称当前新素材必现。当前开关 A/B、无口播窗口复听与分轨审计要求归[音频工程](../skill/references/edit.md#音频工程build_audio-内置)。

## 已修且仍有消费引用的边界

- **⑨ 渲染期间输入变化**：已修，验收与限制见 [#32](https://github.com/CongliangK/framivra/issues/32) / [PR #70](https://github.com/CongliangK/framivra/pull/70)；输入身份与回执的边界见[缓存与回执](../cache-provenance.md)。
- **⑮ 回执指纹翻转的旁证**：写新指纹前，旧阶段记录尽力轮转到 `out/receipt.prev.json` 单槽；旧成功不继承，旁证不是完整版本历史。当前回归为 `tests/cache/test_cache_state.py` 的 `StaleReceiptArchive`。
