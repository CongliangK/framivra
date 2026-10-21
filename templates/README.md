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

消费面变更公告（2026-10-21）：[分轨审计正文](../apps/blender-bridge/README.md#分轨审计包-audit_bundlepy人机协作交付门)归组件 README，[WAV 维护边界](../docs/skill/references/edit.md#既有-wav-维护)归内核音频操作节；删除重复专题、更新消费指针，操作与未验收边界不变，已在落笔前公告并经独立预审。历史公告见[冻结源记录](https://github.com/CongliangK/framivra/blob/cbfaed74761657401b04119602f2a87dd1fd0ee7/templates/README.md)；本次不宣称用户六件 live 挂载已更新或 BAD=0。

消费面补充公告（2026-10-21，`audit_document_entrypoints` 落笔前有条件预审通过）：MG 的十五款 Canvas prototype 源码就近归入各款 `scripts/prototype.js`，共享 helper 保留单份且不混同不同绘制语义；公开 CLI 与三条路线的资格边界不变。构建仅携所选款与公共脚本，缓存依赖随实际输出更新。各款导航补齐源码入口，并只在确有 readable 输入时推荐该版本。此处不宣称用户机器六件挂载已更新；云端文件校验与真实用户挂载验收分开记录。

## 第三方接入五步（复制范本 → 写 manifest → 外部注册 → enable → doctor）

1. **复制开发范本**：`cp -r templates/hello-app templates/my-app`（插件用 hello-plugin；
   工作流另参考 tech-talk / skill-demo，不走下列 app/plugin 注册）；目录名与 manifest `name` 一致
   （`^[a-z][a-z0-9-]*$`）。
2. **写 manifest**：app → `app.json`；plugin → `plugin.json`（`extends.schema_keys`
   声明扩展键，`hooks` 值域 = `prepare_source`/`audio_events`/`validate`；schema 见
   ARCHITECTURE §2.3）。`health.type=command` 的命令必须是**组件根内相对路径**。
3. **外部注册**：编辑 `config.local.json`（gitignored，不入库；纯净 clone 先 `env python3 vt.py init` 或 `cp config.example.json config.local.json` 生成；模板
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

## 安全节（§2.4）实测记录（P4，本机 2026-09-13）

- **默认 disabled**：字符串条目注册后 doctor 显示
  `ℹ [plugin:external] hello-plugin｜disabled（external 注册默认禁用，§2.4）`，
  不探测不生效；doctor 结论仍 ✅。
- **显式 enable**：`✓ [plugin:external] hello-plugin｜health ✓（exit 0｜hello-plugin
  healthy｜ffmpeg /usr/bin/ffmpeg）｜requires ✓`；`plugins list` 显示
  `✓ enabled hello-plugin（plugin:external，.../templates/hello-plugin）`。
- **坏注册 ①（路径穿越）**：`{"path": "../templates/hello-plugin", "enabled": true}`
  （目标 resolve 后合法，但含 `..`）→ registry 拒绝加载，doctor 报
  `✗ manifest 无效：external 路径禁止包含 ..（安全节 §2.4，用绝对或相对本仓的
  规范路径）`，enabled 坏条目计入 FAIL 门（doctor exit 1）。
- **坏注册 ②（绝对路径 health 命令）**：临时插件 manifest 写
  `"command": "/bin/sh -c echo pwned"` → manifest 校验拒绝
  `✗ health.command 脚本路径 必须为相对路径（插件根内），得到绝对路径: /bin/sh`，
  探针永不执行，doctor FAIL。
- 坏条目只在 enabled 时 FAIL；disabled 坏条目仅提示（发现面仍完整展示错误）。

## 卸载与缓存失效（验收门 6 对照，实测）

从 `external_plugins` 移除 hello-plugin 后（timeline 仍声明 `hello_gain`）：

- **键出白名单 → gc 可回收**：`vtx gc --dry` 将
  `cache/audio/{audio_key}::hello:1.0.0:{digest}.wav` 列入回收清单
  （`源指纹不在当前 timeline 键集`）。
- **指纹段消失**：`active_plugins_digest` 由 `plugins:hello-plugin@1.0.0:{摘要}`
  变为 `''`——录制侧（带段）与重算侧（无段）回执指纹失配，旧回执不放行陈旧
  产物，重渲走裸提取。机器断言见 `templates/hello-plugin/tests/test_hello_plugin.py`
  的 `CacheIdentity`。
- validate 侧行为：条目移除 = 键退化为未知键**警告不阻断**；`enabled:false`
  = 报错「已安装但被禁用」阻断；未装分支（mock `plugin_state`）报错+安装提示。

## 测试

`bash scripts/run_all_tests.sh`（批次四发现 `templates/*/tests`）；单目录独立跑：

```bash
cd templates/hello-app    && env python3 -m unittest discover -s tests -t .
cd templates/hello-plugin && env python3 -m unittest discover -s tests -t .
```

skill-demo 无单测（`expected_check.sh` 即其验收：真渲染 + 帧数/QC/TP 断言）。
