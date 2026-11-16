# 在 DSH 中制作视频

查看时间线、预览指定帧，把画面与引用加入对话草稿；剪辑先预检，再确认保存。

## 可视化安装（推荐）

适配 **DSH 0.2.2-rc.1 / Node ≥24.9.0**。通过 npm 安装的 DSH 还需 `pnpm` 在宿主 PATH 中可用，准备命令见下方「命令行安装」。已安装 DSH 的用户优先在界面中从 npm 安装：

1. 打开 DSH 的 **插件** 页，点击 **添加插件**。
2. 在包名输入框中填写 `framivra-dsh-sidebar`，无需下载 ZIP、手动解压或克隆仓库。
3. **安装源** 可保留界面默认值（如中国大陆镜像源）；若找不到包或版本未同步，切换到 npm 官方源（`https://registry.npmjs.org/`）。
4. 确认来源可信后点击 **安装**（**下载插件及依赖，并写入当前 profile**）；插件会以你的本机权限运行，仅安装信任的来源。
5. 安装完成后刷新页面；若界面提示需要重启或出现宿主构建不匹配，重启当前 DSH 后再刷新。进入 **插件 → Framivra**，按下方「首次配置与使用」完成内核连接。

npm 包仅提供侧栏，不包含 Python `vtx` 内核、FFmpeg、浏览器或模型；内核与媒体依赖需另行准备。

## 命令行安装（可选）

供智能体或终端用户使用；普通用户优先使用上方界面。尚未安装 DSH/pnpm 时执行（**全局安装写操作**；已有兼容版本可跳过）：

```bash
npm install -g @deepseek-ai/dsh@0.2.2-rc.1 pnpm
```

再从 npm 官方仓库安装插件（**下载插件及依赖，并写入所选 profile**）：

```bash
dsh plugin --profile web add framivra-dsh-sidebar --registry=https://registry.npmjs.org/
```

`web` 对应 `dsh web`；使用其他 Web profile 时，将 `web` 替换为实际启动的 profile 名。插件须通过 DSH 界面或上述命令装入目标 profile；仅执行 `npm install -g framivra-dsh-sidebar` 不会让 DSH 加载它。

安装后启动或重启使用该 profile 的 DSH（Web profile 用 `dsh web`），刷新页面，再按下方说明配置内核。

## 首次配置与使用

在插件配置页扫描或安装内核 → 保存配置 → 检查连接。然后回到会话，打开右侧 **Framivra** 并选择时间线。

- **内核**：扫描已有安装，或从可信的本地 Framivra wheel 安装到独立环境，不覆盖系统 Python。
- **配置文件**：常用字段表单与完整 JSON 编辑；全局缓存三选一：`off` 不读取或保留可复用缓存（临时件用后清理）、`core` 压缩保留高价值 gfx、合成缓存短暂（缺省/推荐）、`aggressive` 保留更多缓存。缺失字段只显示推荐值，不自动写回；确认保存会影响使用同一 `VTX_CONFIG` 的所有工程，下次操作读取生效，不改变运行中任务、不立即清理旧目录。保存前比较内容，保留备份并检查冲突。
- **工程配置**：时间线的「属性与编辑」中查看固定帧率/画布，调整时长、图形依赖及素材/音轨插件参数；授权后加入批次、预检，再确认保存。根级插件只读；插件安装与真实效果不由结构计划证明，预览仍限已保存、已准备的纯内核工程。
- **检查**：核对连接与预览、编辑能力。结构检查不等于完整渲染测试，FFmpeg、浏览器与模型需另行准备。

扫描不执行程序；安装与保存需明确确认。生成预览会写缓存，剪辑另行授权，加入草稿不会自动发送消息。

[开发与验证记录](https://github.com/CongliangK/framivra/blob/main/dsh-sidebar/DEVELOPMENT.md) · [反馈问题](https://github.com/CongliangK/framivra/issues)
