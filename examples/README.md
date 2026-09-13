# examples/ —— v2 纪律范本与第三方接入指南

三件范本（ARCHITECTURE §4.2）：

| 目录 | 是什么 | 起手模板 |
|---|---|---|
| `hello-app/` | 最小应用（app）范本：产出独立素材、不进 timeline 管线 | 第三方 app |
| `hello-plugin/` | 音频插件完整纪律范本：缝 A prepare_source + 三不变量 + 三分支 | 第三方音频插件 |
| `skill-demo/` | 虚拟歌手搞笑视频工作流包骨架：零依赖纯内核出片 + 验收门 4 道具 | 第三方工作流包 |

examples/ **不在 registry 内置扫描范围**（内置只扫 `apps/` 与 `plugins/`）——
三件都走 external 注册路径，范本即安全节（§2.4）的活体测试。

## 第三方接入五步（复制范本 → 写 manifest → 外部注册 → enable → doctor）

1. **复制范本**：`cp -r examples/hello-app examples/my-app`（插件用 hello-plugin，
   工作流包参考 skill-demo 结构）；目录名与 manifest `name` 一致
   （`^[a-z][a-z0-9-]*$`）。
2. **写 manifest**：app → `app.json`；plugin → `plugin.json`（`extends.schema_keys`
   声明扩展键，`hooks` 值域 = `prepare_source`/`audio_events`/`validate`；schema 见
   ARCHITECTURE §2.3）。`health.type=command` 的命令必须是**组件根内相对路径**。
3. **外部注册**：编辑 `config.local.json`（gitignored，不入库；模板
   `config.example.json` 的 `external_apps` / `external_plugins`）：
   ```json
   "external_apps":    [{"path": "examples/my-app", "enabled": true}],
   "external_plugins": [{"path": "examples/my-plugin", "enabled": true}]
   ```
   只写字符串路径 = 注册但**默认 disabled**（§2.4 安全节——external 注册显式
   enable 才生效）。路径可相对本仓或绝对/`~/` 路径；**含 `..` 一律拒绝**。
4. **enable**：如上把条目写成 `{"path": ..., "enabled": true}`。
5. **doctor 验证**：`env python3 vt.py doctor`——②组件段应出现
   `[app:external]`/`[plugin:external]` 条目且 health ✓ / requires ✓；
   `env python3 vt.py apps list` / `plugins list` 出现 external 条目。

## 安全节（§2.4）实测记录（P4，本机 2026-09-13）

- **默认 disabled**：字符串条目注册后 doctor 显示
  `ℹ [plugin:external] hello-plugin｜disabled（external 注册默认禁用，§2.4）`，
  不探测不生效；doctor 结论仍 ✅。
- **显式 enable**：`✓ [plugin:external] hello-plugin｜health ✓（exit 0｜hello-plugin
  healthy｜ffmpeg /usr/bin/ffmpeg）｜requires ✓`；`plugins list` 显示
  `✓ enabled hello-plugin（plugin:external，.../examples/hello-plugin）`。
- **坏注册 ①（路径穿越）**：`{"path": "../examples/hello-plugin", "enabled": true}`
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
  产物，重渲走裸提取。机器断言见 `examples/hello-plugin/tests/test_hello_plugin.py`
  的 `CacheIdentity`。
- validate 侧行为：条目移除 = 键退化为未知键**警告不阻断**；`enabled:false`
  = 报错「已安装但被禁用」阻断；未装分支（mock `plugin_state`）报错+安装提示。

## 测试

`bash scripts/run_all_tests.sh`（批次四发现 `examples/*/tests`）；单目录独立跑：

```bash
cd examples/hello-app    && env python3 -m unittest discover -s tests -t .
cd examples/hello-plugin && env python3 -m unittest discover -s tests -t .
```

skill-demo 无单测（`expected_check.sh` 即其验收：真渲染 + 帧数/QC/TP 断言）。
