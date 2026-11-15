# plugins · timeline 管线扩展

插件扩展 timeline 键并接入内核管线，不是独立素材应用。[ARCHITECTURE 扩展协议](<../docs/ARCHITECTURE.md#2-管线扩展协议>)是稳定性、安装态与失败语义的权威；组件版本、键、hooks 与安装声明见 [catalog](<../docs/catalog.md>)，不在此重复维护。

## 找到对应能力

- [denoise](<denoise/README.md>)：源音频降噪；先核对专用解释器、权重及许可。
- [duck-static](<duck-static/README.md>)：床乐静态避让；区分推荐的管线内存变换与会改 timeline 的独立烘焙命令。
- [bgm-render](<bgm-render/README.md>)：把[音乐应用](<../apps/bgm/README.md>)工程作为音源；按所选引擎准备运行环境与资产。
- [blender-bridge](<blender-bridge/README.md>)：全链收尾导出审片工程；依赖[应用侧 Blender 版本/API 与工具](<../apps/blender-bridge/README.md#依赖>)。

## 使用与安全

先按[根安装说明](<../README.md>)准备当前源码 checkout，再从仓根检查状态、阅读目标插件 README：

```bash
python3 -B vt.py plugins list
```

该入口会做组件健康探测；通过不等于已执行真实渲染。**安装不等于启用，启用也不等于依赖已就绪**；官方注册表为 [registry](<registry.json>)，配置和 external 注册/信任边界分别见[统一配置](<../docs/ARCHITECTURE.md#53-配置>)与[安全边界](<../docs/ARCHITECTURE.md#24-安全边界>)。不要为查看状态运行修复或安装命令。

只在确认环境、[第三方许可](<../docs/third-party-licenses.md>)与写入授权后，按子 README 配置 timeline 并运行[正式交付链](<../docs/skill/references/edit.md#正式交付链r1-r4-共用>)。**实际管线会写项目缓存/产物，部分插件会调用应用或导出工程；独立 CLI 另有写入与备份要求。** 插件已声明的键不能因缺件、禁用或环境失败而静默忽略；关停前必须处理仍使用该扩展的时间线，不能靠删除目录绕过校验。

开发从 [hello-plugin 范本](<../templates/hello-plugin/README.md>)进入，按权威协议实现与验收；manifest/registry 改动须按[文档规则](<../docs/DOC-CHARTER.md#单一事实来源>)重注 catalog。不要手改生成区或另写平行协议。
