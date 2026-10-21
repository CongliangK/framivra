# templates/ —— 工作流模板与开发范本

平级模板（ARCHITECTURE §4 + #177 切片）；工作流可组合 SKILL、参考资料、资产和脚本，包内布局保持自包含：

| 目录 | 是什么 | 起手模板 |
|---|---|---|
| `mg-styles/` | 可替换内容与整数帧节奏的原创 MG 风格工程模板 | [用法](mg-styles/README.md) |
| `tech-talk/` | 完整 B 站科技口播工作流：六阶段 SOP、参考资料、媒体与工程模板、脚本 | 视频制作全流程 |
| `hello-app/` | 最小应用（app）范本：产出独立素材、不进 timeline 管线 | 第三方 app |
| `hello-plugin/` | 音频插件完整纪律范本：缝 A prepare_source + 三不变量 + 三分支 | 第三方音频插件 |
| `skill-demo/` | 虚拟歌手搞笑视频工作流包骨架：零依赖纯内核出片 + 验收门 4 道具 | 第三方工作流包 |
| `standard-demo/` | #177/#91 冻结公开版本化 demo 工程输入包（quick/standard/gfx-stress + benchmark 冻结锚；唯一入口 `scripts/demo.py`；无 SKILL.md，不入 workflow 扫描） | demo 工程输入 |
| `demo-timeline-svg/` | Linux / macOS arm64 身份实现的 Inkscape 静态 SVG 生产输入；无 SKILL.md，不入 workflow 扫描；原生全链与视听未验收 | [用法与依赖边界](demo-timeline-svg/README.md#实际依赖与字体) |

`vt templates list|check` 从 `skill.json` / `SKILL.md` 识别并检查 tech-talk 与 skill-demo；doctor JSON 的工作流检查字段为 `skills`。发现不会执行工作流。hello-app / hello-plugin 由各自组件 manifest 区分，不作为 workflow 检查。

`templates/` **不在 registry 内置扫描范围**（内置只扫 `apps/` 与 `plugins/`）。hello-app / hello-plugin 只有经过下述 external 显式注册与 enable 才能使用，不自动安装、启用或探测。工作流模板不走 app/plugin external 注册。

tech-talk 的内部 `templates/` 保存四份工程文档，不展开到集合根。此布局不构成 #91 的干净安装、交互与成片质量验收证据。

消费面公告（2026-10-21，`refactor_docs_structure` 落笔前预审）：本次以本页为 external 注册流程唯一 home，范本只保专有接口；更新 MG 测试与内核预览指针，当前能力和未验收边界不变。[历史公告](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/templates/README.md)保留先前归组记录。本次仅核仓内链接与命令，不宣称用户机器六件 live 挂载已更新。

## 第三方接入

1. **复制开发范本**：`cp -r templates/hello-app templates/my-app`（插件用 hello-plugin；
   工作流另参考 tech-talk / skill-demo，不走下列 app/plugin 注册）；目录名与 manifest `name` 一致
   （`^[a-z][a-z0-9-]*$`）。复制会创建目录，须先确认目标不存在。
2. **写 manifest**：app → `app.json`；plugin → `plugin.json`（`extends.schema_keys`
   声明扩展键；`extends.hooks` 的当前值域与协议见
   [ARCHITECTURE §2.3](../docs/ARCHITECTURE.md#23-manifest-schema-v1)）。`health.type=command` 的命令必须是**组件根内相对路径**。
3. **外部注册**：编辑 `config.local.json`（gitignored，不入库；纯净 clone 通过 `env python3 vt.py init` 生成，会写本地配置；配置模板见
   `config.example.json` 的 `external_apps` / `external_plugins`）：
   ```json
   "external_apps":    [{"path": "templates/my-app", "enabled": true}],
   "external_plugins": [{"path": "templates/my-plugin", "enabled": true}]
   ```
   只写字符串路径 = 注册但**默认 disabled**（§2.4 安全节——external 注册显式
   enable 才生效）。路径可相对本仓或绝对/`~/` 路径；**含 `..` 一律拒绝**。
4. **enable**：如上把条目写成 `{"path": ..., "enabled": true}`。
5. **doctor 验证**：`env VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor`——②组件段应出现
   `[app:external]`/`[plugin:external]` 条目且 health ✓ / requires ✓；
   `env python3 vt.py apps list` / `plugins list` 出现 external 条目。

## external 注册的安全边界

| 配置 | 当前行为 |
|---|---|
| 字符串路径或未显式 enable | 默认 disabled，不执行健康探针或插件；发现面仍显示配置错误 |
| `enabled: true` | 检查 manifest、requires 与 health；坏配置计入 doctor FAIL |
| 注册路径含 `..` | 拒绝加载，即使 resolve 后仍落在合法目录 |
| manifest 逃出注册目录，或 health 命令使用绝对路径/逃出组件根 | 校验拒绝，不执行探针 |

插件移除、禁用、未安装三种状态并不等价；[hello-plugin 三分支及卸载缓存规则](hello-plugin/README.md)是范本行为的唯一说明，断言在其 `tests/test_hello_plugin.py`。

## 测试

`bash scripts/run_all_tests.sh`（批次四发现 `templates/*/tests`）；单目录独立跑：

```bash
(cd templates/hello-app && env python3 -m unittest discover -s tests -t .)
(cd templates/hello-plugin && env python3 -m unittest discover -s tests -t .)
```

skill-demo 无单测（`expected_check.sh` 即其验收：真渲染 + 帧数/QC/TP 断言）。
