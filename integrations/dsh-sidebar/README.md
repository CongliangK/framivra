# 在 DSH 中制作视频

查看时间线、预览指定帧，把画面与引用加入对话草稿；剪辑先预检，再确认保存。

**首次使用：** 在下方扫描或安装内核 → 保存配置 → 检查连接。然后回到会话，打开右侧 **Framivra** 并选择时间线。

- **内核**：扫描已有安装，或从可信的本地 Framivra wheel 安装到独立环境，不覆盖系统 Python。
- **配置文件**：常用字段表单与完整 JSON 编辑；保存前比较内容，保留备份并检查冲突。
- **检查**：核对连接与预览、编辑能力。结构检查不等于完整渲染测试，FFmpeg、浏览器与模型需另行准备。

扫描不执行程序；安装与保存需明确确认。生成预览会写缓存，剪辑另行授权，加入草稿不会自动发送消息。

适配 **DSH 0.2.2-rc.1 / Node ≥24.9.0**。[开发与验证记录](https://github.com/CongliangK/framivra/blob/main/integrations/dsh-sidebar/DEVELOPMENT.md) · [反馈问题](https://github.com/CongliangK/framivra/issues)
