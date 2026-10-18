# CDP fast PNG 实验（issue #64）

## 边界与复现合同

本专题归 docs 层。实验只给 `Tab` 与 `Browser` 构造器增加显式 `fast_png=True`；生产调用默认值保持关闭，关闭时不增加探针、不发送 `optimizeForSpeed`。开启时初始化截图探测参数，只有错误明确点名该参数且说明未知/不支持才降级，每进程一次告警；超时、连接、渲染及其他参数错误继续失败。支持状态与实际启用状态分别可查。所有截图共享 `Tab.shot()`，包含透明 override 探针及白黑双截；Canvas export 与 Python PNG_LEVEL 不受此参数影响。

实验入口为 `experiments/gfx_fast_png/bench.py`、`full.py` 与 `cli_fallback.py`。运行会创建新证据目录和合成项目，输出目录必须不存在；完整流程使用 `cache/gfx_exp/issue64_fastpng`，真实 CLI 回退使用另一个实验命名空间。各臂独立项目、独立缓存、独立 Chrome profile，不复用正式素材或缓存。完整流程只通过实验工厂注入选项，调用标准 `vtx all`，没有另写生产计时器或编码管线。

以下仅预览合同，零写态；实际运行去掉 dry 会生成文件：

```bash
python3 experiments/gfx_fast_png/bench.py --output /tmp/issue64-new-evidence --ffmpeg /usr/bin/ffmpeg --dry
python3 experiments/gfx_fast_png/full.py --dry
```

两套合同在执行前冻结于证据根。独立 gfx 使用低熵、高熵 blur、渐变、透明、flatten、强制 override 不可用的 twoshot、Canvas 七类页，各页每臂完整解码 RGBA 比较。完整流程使用合成有声底片与相同页面。RSS 是运行进程及后代 `/proc` 采样之和，采样可能漏掉短峰，不代表操作系统严格上限。PNG 压缩文件字节允许不同；质量门为逐页逐帧解码 RGBA 摘要相同，不用最终有损视频摘要代替。

当前基线只有通用任务事件与 `render_page` 已有 export/decode/write 统计，尚无 #63 的截图 CDP/Base64 细项事件接口；这些字段显式 null。export 包含 CDP、传输与 Base64 解码，不能称为纯 PNG 编码时间。下游 FFmpeg 的独立解码墙钟含启动与读取；不是 CPU 解码内核耗时。单独 gfx 与完整 all 的墙钟分开统计，不把两者相加冒充全链。

## 验收状态

证据根：`/tmp/framivra-orchestration-20261018/issue64/`。Chrome 为 155.0.8059.39，FFmpeg 为正式提权环境可用的系统工具。独立矩阵三轮完成，252 对完整帧 RGBA 零差异（504 张 PNG），全部 fast 臂初始化探测成功。用户确认同期存在大量并行开发，因此这些运行的墙钟与内存仅为受背景负载干扰的观察值，不构成隔离性能验收。原始 JSON 保留以供审计，不据此判定提速、退化或性能 NoGo。正式三轮全链性能为 UNRUN；后续按用户指示取消跑分，只补低成本功能与像素验证。

| 合成页 | 实际导出 | PNG 体积倍率 |
|---|---|---:|
| 低熵 | twoshot | 1.00 |
| 高熵 blur | twoshot | 1.00 |
| 渐变 | override | 3.89 |
| 透明 | override | 4.13 |
| flatten | twoshot | 1.00 |
| 强制双截 | twoshot | 1.00 |
| Canvas | canvas | 1.00 |

名称不能替代实际路径：本机低熵与 blur 页触发真实 flatten 守卫，故输出仍经 Python 重编码且最终文件大小相同；参数确实作用于其白黑截图。Canvas 输出相同；其初始化探针会增加一次截图，不宣称该通道获益。PNG 字节增幅是本次文件事实，下游读取/解码性能尚不可判。

完整流程第一次 fixture 无音频事件，标准 audio 失败；第二次添加事件后生成成片，标准 QC 13/15，灰蓝底与 blur 页差异太小、空窗底片编码色差触发探针失败。两次失败分别保留于 `full/`、`full-v2/`，不计入正式成功样本。后续 fixture 只调整合成底片颜色来满足原有 QC 探针，不修改生产色彩标签或 QC 门。正式完整链三轮按用户指示取消，#63 细项事件依赖仍缺，当前不宣称完整 DoD 通过，issue 应保持 OPEN。白底标准 all 功能检查 normal/fast 各一轮（七页各一帧，90 帧合成有声时间线）均 exit 0、QC 15/15，七张 gfx 输出逐页 RGBA 相同；不是正式全链性能矩阵。补充 opaque 路径 normal/fast 各 12 帧，全部 RGBA 相同。

CLI 兼容修复独立于 fast PNG：基线 `gfx_cli` 的原子截图临时文件以 `.tmp` 结尾，Chrome 155 明确拒绝该图片类型。直接 `.png` 目标成功、`.tmp` 失败，独立 profile 不改变失败；原始证据保留于 `real-cli.log`、`real-cli-isolated.log`、`manual-cli.log` 与 `manual-cli-tmp.log`。修复为 `locked_staging` 增加只接受 `.tmp`/`.png` 的可选后缀，默认仍为 `.tmp`；仅截图调用显式选择 `.png`。同目录随机 O_EXCL 临时文件、原 inode 对账、两层锁、提交与失败清理均保留。真实 Chrome 回退透明/不透明各两帧通过，透明 alpha 与不透明 alpha 断言及 complete 记录通过，见 `fixed-real-cli-alpha.log` 与 `fixed-real-cli-opaque.log`。实验不改变默认配置。


验证：fast PNG 参数合同 6 项、graphics portability 43 项（真实 Chrome/FFmpeg，零跳过）、CLI fallback mock 23 项、golden 38 项通过；文件事务 14 项、写安全对抗/平台 15 项、后缀专属 4 项（native/legacy/中止/逃逸/inode 交换）通过；文档聚合门 7/7。完整性能、内存正式预算验收、#63 CDP/Base64细项为未验收；因此候选仅供独立代码审查，不建议关闭 #64 或开启生产默认。
