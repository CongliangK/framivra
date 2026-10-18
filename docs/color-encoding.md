# 编码色彩测量与 BT.709 标记前置条件

[#101](https://github.com/CongliangK/framivra/issues/101) **保持开放**。基线 `59811757809642534591e77ee86669aeccbcdbf7` 的四档编码不能安全地直接补 BT.709 标签。本次不改编码参数、转换或 mux 行为。

## 测量

2026-10-18，Linux `/usr/bin/ffmpeg` 8.1.3、RTX 4080；正式权限环境中 libx264、H.264 解码和 NVENC 均实际成功。受限沙盒中的能力清单不同，不能据此判定宿主工具缺编解码器。使用合成 1920×1080 RGB24 八色块（黑、白、红、绿、蓝、灰、黄、青），单帧 30fps，不接触项目素材。

隐式 `format=yuv420p` 与显式 `scale=out_color_matrix=bt601:out_range=tv,format=yuv420p` 的完整 YUV420P 字节相同；与显式 BT.709 limited 不同：

| 转换 | 八色块中央 Y 样本 | 完整帧 SHA-256 |
|---|---|---|
| 默认 / BT.601 limited | 16,235,81,145,41,126,210,170 | `55747ae561d405af76e8b8b06d748d38302510b0f4a610409c381789be8957bd` |
| BT.709 limited | 16,235,63,173,32,126,219,188 | `3304aa9740f884ea9239ecb80e848481f74207eb02c854a274663c6f8269feb8` |

通过 `pick_vcodec` 取得真实参数后，x264 medium CRF17、x264 veryfast CRF26、NVENC p4 CQ23、NVENC p7 HQ CQ17 均完成编码与解码；中央 Y 样本均为默认转换一行。四份 H.264 SPS 的 `video_signal_type_present_flag=0`，ffprobe 视频流未返回 color_* 字段，不能称为已标明 BT.709/tv。合成 AAC + 视频 copy + faststart 的 mux 后，四档解码 YUV 各自与 mux 前逐字节相同，均为一帧；color_* 仍未返回。容器格式层不返回 color_* 不单独判失败。

本测量证明这一 RGB 输出入口的矩阵/范围；不证明素材原色/传递函数、不覆盖任意 YUV 透传输入或完整合成图。不能从灰阶范围或 VUI 缺省推导原色/传递函数。

## 复现与证据

诊断探针 [tests/probe_color_encoding.py](../tests/probe_color_encoding.py) 默认不参加测试套件，要求四档真实可用；失败即报错，不用 CPU 代替 NVENC。它记录逐条命令、返回码、stderr、ffprobe 流/格式信息、SPS trace、解码摘要及 mux 对比。只运行帮助为零写态：

```bash
python3 tests/probe_color_encoding.py --help
```

实际运行 **有写入**：使用帮助中的 `--output` 参数指定一个不存在的临时目录；只写该目录的合成素材和测量结果。

ffmpeg/ffprobe 从 PATH 取得，`results.json` 记录版本。原始本机证据目录为 `/tmp/framivra-orchestration-20261018/issue101/`，其中 `commands.json`、`results.json`、各档 `*-vui.txt` 与 `measure.py` 保留原始首次测量；临时目录不是仓库持久归档。

## 最小设计与剩余验收

直接加 bt709/tv 会给 BT.601 矩阵样本错误解释。下一步须先确定输出色彩合同：对最终 RGB→YUV 转换显式选择矩阵/范围，同时明确原色/传递函数来源；保留既有 YUV 输入的解释，避免无条件重标。若选择实际转换为 BT.709，decoded 像素必然改变，须作为独立色彩转换设计评审，重新建立 golden，而不能声称“仅元数据、像素零变化”。只给经证明已经符合合同的流添加相符标签，并核实编码器 SPS 与 copy mux 后标签一致。

UNRUN：新标签前后 decoded 零差（未实施标签）、完整 golden/QC、音频响度回归和多帧全链帧数回归。本次一帧 mux 与 AAC 存在性检查不代替这些门。原整单全部验收仍有效，现有测量不足以关闭 #101。
