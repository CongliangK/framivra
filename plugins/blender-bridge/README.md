# blender-bridge（插件）——渲染收尾自动导出 Blender 审片工程

> 缝 D · finish 管线收尾钩子（协议 v1.2，2026-09-14 P6）。交互工具（json2blend/
> verify_blend/audit_bundle/blend2json 手动命令面）在应用件 `apps/blender-bridge/`，
> 本插件只做一件事：**timeline 里配置了，`vtx all` 结束就自动导出 .blend**。

## 用法

timeline.json 顶层键（配置即生效，不配置零行为）：

```json
{
  "blender_bridge": true
}
```

或带参数：

```json
{
  "blender_bridge": {"out": "preview.blend", "verify": true}
}
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `out` | `preview.blend` | 产物路径，相对项目根（禁绝对路径/`..`） |
| `verify` | `false` | 导出后追加 verify_blend.py 逐条回读断言（blender 再起一次，多花几秒） |

`vtx all`（完整链，mux/qc 完成后）自动执行等价命令：

```bash
blender --background --factory-startup --python apps/blender-bridge/json2blend.py \
  -- <项目>/timeline.json <项目>/preview.blend
```

- `--draft` / `--dry-run` / `all --until <截断>` 不触发（无正式产物语义）。
- qc 失败不阻断导出（2026-09-14 裁定：产物已就绪，存量假阴性项目也要能收尾）——
  `vtx all` 退出码按 qc/finish 各自失败态如实汇总（任一失败 exit 1）。
- blender 不在 PATH → `vtx all` 收尾 exit 1（final.mp4 已就绪不受影响）——配置了
  就要导出，失败必须可见，不静默跳过。
- 键在插件未装/禁用时走三分支报错（`vt plugins list` 查看），绝不静默忽略。
- **源产物映射（P6）**：导出前经内核缝 A 解析插件产物（bgm 工程 JSON 源的渲染 wav、denoise 源的降噪 wav），映射传给 json2blend——.blend 里音轨听到的与成片同源（v1 直引源路径，虚拟源会挂、降噪源听原始音）。

## 关停测试（§0.2）

卸载本插件：写了 `blender_bridge` 键的 timeline validate FAIL（三分支），删键即
恢复——主链渲染产物不受任何影响（.blend 是审片预览件，非可信度构成）。

## 测试

`env python3 -m unittest discover -s tests`（本目录，伪 blender 桩验证接线与三分支）。
