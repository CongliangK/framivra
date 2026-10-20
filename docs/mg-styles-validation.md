# MG 模板验证记录

验证环境：2026-10-20，Linux 云工作区；实现范围见 [模板操作入口](../templates/mg-styles/README.md)，来源与许可见 [研究记录](mg-styles-research.md)。本记录由独立验证席产出，技术结论不构成艺术审查或用户视觉确认。

## 已执行

- `tests/test_mg_styles.py` 六项测试通过：全部款式各用英语单条与中文特殊字符三条内容生成；检查 v2 时间线、整数帧、单页 clip、依赖文件存在；非法帧/内容拒绝且不创建输出；已有目录不覆盖；dry 不落盘。
- Node 模拟 Canvas 对全部款式检查帧 0、1、9、45、80、89 的绘制操作、非有限数值、save/restore 平衡及 45→89→0→45 乱序重放。操作序列复现通过。**模拟 Canvas 不实际绘制，不能证明像素确定性、字体适配或视觉质量。**
- 全部款式生成 90 帧、1920×1080、30fps 项目，真实 `vtx validate` 全部退出 0。

集成席另报告：模板与存量布局等联合测试共 16 项通过，文档门 7 项通过；此两项由集成席执行，验证席未重复运行。挂载复核 `/home/agent/.agents/skills/video/` 下 SKILL.md、assets、references、scripts、templates、vtx 全部 MISSING，不能报告 BAD=0；未修改挂载或权限。

本地原始日志在 `/workspace/mg-style-evidence/`：`unit.log`、`validate-summary.json`、各款 `*-validate.log`；带提示音最终输入见 `audio-validate-summary.json` 与各款 `*-audio-validate.log`。媒体及缓存未纳入 Git。

## 实际成片尝试与阻塞

对 `flat-vector` 执行完整 `vtx all`，**退出 1，未产出成片**。`all-flat-vector.log` 记录两项独立失败：

1. gfx 环境身份拒绝：`native ELF renderer required: /usr/bin/chromium`。本机命令是包装入口，未获得合格渲染环境身份。
2. 当次模板无音轨，音频阶段生成 `amix=inputs=0`，FFmpeg 拒绝输入数量。该问题已在模板生成器修正：加入原创确定性参考提示音，不修改内核或门禁。最终输入全部款式重新 validate 均退出 0；代表性 `flat-vector` 的 prepare/audio 分阶段退出 0（`prepare-audio.log`、`audio.log`），音频终态 I=-14.8 LUFS、TP=-4.4 dBFS。此分阶段结果不代表 mux/QC 或完整 all 通过。

另以仓库 Browser 启动浏览器报 `chrome exited during startup`；使用可写项目外 profile 获取 Chromium stderr，指出 SUID sandbox helper 未正确配置、要求 root 所有者与 mode 4755。验证席未修改权限，未加入禁用 sandbox 参数，未请求提权；环境阻塞后停止浏览器启动。

因此未完成真实 PNG 渲染、同帧像素复现、抽帧艺术审查、黑场检测或成片 QC。不得将单元测试、操作序列或 validate 通过记作成片验收。需在具备合格原生浏览器与沙箱的环境补验；参考提示音应按创作要求替换为有许可的正式配乐或旁白。
