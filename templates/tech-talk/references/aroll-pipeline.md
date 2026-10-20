# aroll-pipeline · A-roll 实拍素材全链处理手册（通用化）

> **来源**：[2026-09-07 首跑实录](https://github.com/CongliangK/framivra/blob/f1efe5f688b543ca11902c9538dec02a5c265961/apps/aroll/docs/A-roll实拍素材处理流程-2026-09-07.md)+ `终版制作经验-voice-ime-D31.md` §二。提炼日期：2026-10-03；原报告以 voice-ime 为例的部分已参数化为「<口播工程>/<素材>」表述，**项目实例**行保留首期实测量作参考。关联决策：D23（实拍规范）/D25（降噪链）/D26（双系统同步）/D31（D-A 双源）。
> 拍摄端规范（怎么拍）→ `shooting-guide.md`；本文管拍完之后的全链处理。工具均在 `apps/aroll/`、`apps/sync/`、`plugins/denoise/`。

## 0. 输入与产出

- 输入：口播录音工程（Audacity `.aup3`，mono 44.1kHz float32，每段口播一条轨）+ 相机 4K 素材（内录麦轨=同步参考）。
- 产出：`assets/aroll_final/` 成品素材——头部统一构图、口型同步（构造保证）、降噪音轨、头尾无真空，可直接作 D-A 双源进 timeline。
- 硬前置（D31）：素材 fps 必须与项目精确一致——**CFR 归一化（29.97→30 用复制帧法 fps 滤波）严禁 setpts/atempo 变速**（音画累积漂移 600s 达 0.6s）。

## 1. aup3 解析与提取（`apps/aroll/aup3_extract.py`）

- `.aup3`=SQLite（dict/doc token 流/sampleblocks float32 裸 PCM）；轨道树 wavetrack→waveclip→sequence→waveblock。
- **删除的片段不会从库里清掉**（孤儿块可恢复=废片找回；工程越删越大）。
- Audacity 开着工程时须复制 `aup3+wal+shm` 三件套到缓存目录再操作。
- 掐头去尾（`trim_takes.py`）：300-3400Hz 帧级 RMS、-48dBFS 阈值、头 150ms/尾 250ms 气口；**中部气口保留**（后续按静音谷切分要用）。

## 2. ASR 配对与废片判定

- ASR 独立环境、协议与 CLI 以 `<video_tools>/apps/asr/README.md` 为准（`<video_tools>` 为本仓真实根目录）。
- 对全部 take 与视频抽轨转写 → 文本匹配配对。
- **废片判定=视频文本是某 take 的前缀且比例低**（项目实例：12 条视频中断点 12%~70%）。
- 16k mono wav 输入即可；转写很快。

## 3. 双系统同步（`apps/sync/sync_audio.py`，D26）

- GCC-PHAT 互相关（语音本身即标记，拍掌/LTC 废除）+ 稳健线性拟合（偏移+ppm）+ 话筒轨微重采样 conform（**视频永不动**）。
- 短素材（<50s）加 `--win 10 --hop 5`；残差分级：0.6~1.3ms 优 / 3.8~4.5ms 可接受（<10ms 口播感知阈值）。
- **全局拟合报超大 ppm「漂移」≠时钟漂移，=有一段对不上**（项目实例 P1040019：take 尾部多录 6.5s 废音伪装成 -19000ppm）。逐窗全范围搜索定位；**下「两遍录音/口型对不上」结论前必须做内容级验证**（ASR 段落对比+静音结构对比）——初判曾被用户听感推翻。
- `--report` 出 JSON（偏移/ppm/各窗置信度/离群点/verdict）；**录完先跑本工具再进管线**。

## 4. 音轨替换重封装

替换两个输入占位符；输出为当前目录的 `aligned.mp4`（`-y` 会覆盖同名文件，勿与输入同路径）。

```bash
ffmpeg -nostdin -y -i "<视频>" -i "<同步.wav>" -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 320k -movflags +faststart aligned.mp4
```

- **`-nostdin` 必加**：`while read` 循环里 ffmpeg 会偷吃 stdin 后续行（实测踩坑）。
- zsh 注意：`set -- $var` 不分词，批量循环用 `printf '%s\n' ... | while read a b`。

## 5. 口型感知全局偏移

- 波形级同步（亚毫秒）≠观感对齐：唇部预备动作天然领先声音，用户主观验收驱动**全部音轨后移 200ms**（前置静音实现）——作为新项目默认值，仍以用户验收为准；已验证现值写进 manifest。

## 6. 降噪（D25 标准链）

- 生产链、已验证适用域与 CLI 用法见 [denoise 插件](../../../plugins/denoise/README.md)。
- 历史项目实例（不作现行链保证）：50Hz 消 ~40dB、100Hz ~20dB、150Hz ~14dB；零时长漂移；语音 RMS 损失 ≤1.7dB。
- **处理对象用无损 PCM 源**（synced wav），不要解成品 AAC 再编（避免二代有损）。
- 算法选型对照实验（备胎 MossFormer2 等）→ `shooting-guide.md` §录音。

## 7. 头尾真空切割（最大坑位）

- **正解：对已对齐全长成品做单输入 copy 切割**：`ffmpeg -ss K -to T -i aligned.mp4 -c copy out.mp4`（A/V 由 ffmpeg 一致 seek，实测错位 0.0ms、零代损）。
- 切点=语音边界（带通 RMS -50dBFS）+头 150ms/尾 300ms pad→**吸附关键帧**（相机 GOP≈0.5s，ffprobe packet flags=K 提取）。
- **错误路径（勿走）**：双输入分别 -ss 切再重封 → A/V 错位 27~58ms 且方向不稳定，测量驱动补偿也无效。
- QC 三教训：①「相关系数 1.000」可能是静音稀释假象；②短段单独 FFT 带通有边界 Gibbs 振铃伪峰；③A/V 对齐仲裁用两种独立方法——**视频首帧哈希匹配源关键帧 + 成熟 GCC-PHAT 测音轨平移**（自写 GCC 先用已知对齐样本校准方向）。

## 8. 头部位置统一 + 放大（构图对齐）

- Haar 人脸检测（缩到 960 宽检测更稳）：每条 5 帧取脸中心中位数。
- 每条独立 crop：放大 ~20% → lanczos 回原分辨率；tx/ty 由「脸中心→统一目标点」反解+clamp（目标点选余量内全员可达的最靠上位置）。
- 公式方向：脸要靠上 → **窗口中心在脸下方**（`cy = fy + (0.5-TY)*CH`，写反即得 0.565）。
- 编码 NVENC 高码率视觉近无损，音轨 copy；按约束不做视频内稳定（残余浮动=身体自然晃动）；可选升级=逐条微调放大率使头部大小也统一。
- **几何参数必须显式标注坐标域**：1080p 域裁切值用在 4K 域源上=越界钳位头顶切（v3.7 实案）；复用裁切参数先 ffprobe 源尺寸。

## 9. 复用清单

| 环节 | 工具 | 关键参数 |
|---|---|---|
| aup3 提取 | `apps/aroll/aup3_extract.py` | float32 解码；孤儿块自动恢复 |
| 掐头去尾 | `apps/aroll/trim_takes.py` | 带通 -48dBFS；150/250ms pad |
| 同步 | `apps/sync/sync_audio.py` | 短素材 --win 10；残差 <10ms 可用 |
| 替换/切割/放大 | ffmpeg | -nostdin；单输入 copy 切割；坐标域显式 |
| 降噪 | `plugins/denoise/denoise_voice.py` | 无损 PCM 源进 |

## 10. 后续衔接

- 中部静音段切分：copy 方案触点必须在关键帧上，帧精切走 vtx 管线。
- 素材进 timeline 后即成 D-A 双源（aroll clip 与 voice 事件同生成器同映射出处号）——撤演示顶替、静默谷续延等用法见 `narrative.md` §三。
- 抠像支线（真人换背景）→ `apps/matting/`（D49 路线）；拍摄端锁参与背景互锁见 `shooting-guide.md` §六。
