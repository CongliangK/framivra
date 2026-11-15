# apps · 独立素材与产线辅助应用

这里的应用产出素材、审阅工程或发布侧结果，不扩展 timeline 管线；与插件的边界以 [ARCHITECTURE](<../docs/ARCHITECTURE.md#02-应用与插件>) 为准。组件清单、版本与声明状态只维护在 [catalog](<../docs/catalog.md>)；本页按任务导航，不另建清单契约。

## 按任务进入

- 前期素材：[research 调研](<research/README.md>)、[aroll 实拍整理](<aroll/README.md>)、[sync 双系统同步与实录替换](<sync/README.md>)。
- 生成与加工：[ASR 字幕](<asr/README.md>)、[TTS 占位语音](<tts/README.md>)、[bgm 音乐](<bgm/README.md>)、[imgen 生图](<imgen/README.md>)、[matting 抠像](<matting/README.md>)。
- 审阅与发布：[Blender 预览/分轨审计](<blender-bridge/README.md>)、[gates 内容复检](<gates/README.md>)、[cover 投稿封面](<cover/README.md>)、[publish 4K 导出](<publish/README.md>)。这些增强不替代内核 QC。

## 使用顺序与写入边界

1. 先按[根安装与依赖说明](<../README.md#依赖矩阵>)准备仓库，再读目标应用的依赖、开启步骤和用法。应用执行代码不属于 Python wheel 的分发承诺；使用当前 checkout 的真实入口，见[包与命令归属](<../docs/ARCHITECTURE.md#52-包与命令归属>)。
2. 从仓根查看入口与健康状态；健康检查可能启动探针子进程，帮助/语法检查成功不证明模型、服务或业务可用：

   ```bash
   python3 -B vt.py apps list
   python3 -B apps/bgm/bgm.py --help
   ```

3. 各应用解释器、FFmpeg、GPU/GUI、服务与资产要求不同，不要把基础安装视为全部就绪。配置入口见[统一配置](<../docs/ARCHITECTURE.md#53-配置>)，模型、权重、服务及输出权利先读[第三方许可](<../docs/third-party-licenses.md>)；不得借默认关闭或已有资产绕过启用/许可边界。
4. **实际生成、转写、同步、替换、导出会写文件，部分还联网、付费或启动服务。** 尤其字幕合并、实录替换和音频归一可能改现有项目；按子 README 核对目标、备份与预演支持，不把 `--dry-run` 推广到未声明支持的命令。生图参考图上传还涉及数据外发。

视频项目及运行产物放独立工作区，不放应用源码目录。素材接入后按[正式交付链](<../docs/skill/references/edit.md#正式交付链r1-r4-共用>)重新验证；产物存在、局部通过或应用健康不等于成片验收。

开发应用从 [hello-app 范本](<../templates/hello-app/README.md>)进入；manifest 修改后的 catalog 重注与验证遵循[文档规则](<../docs/DOC-CHARTER.md#单一事实来源>)。
