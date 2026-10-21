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

- **字幕透明轨自动生成**：timeline 有字幕时，`cache/subs_alpha.mov` 只有经
  ffprobe 确认可读且有视频流、正时长，并且 `cache/subs_alpha.key.json` 的
  ASS 内容 SHA1 + project（width/height/fps/duration_frames）一致才复用；
  缺失、损坏、陈旧或无键均自动执行
  `python3 apps/blender-bridge/subs_alpha.py <timeline.json>` 重建（纯 python3
  脚本、无 bpy，经当前解释器运行），生成后校验通过才写键。项目根取 timeline
  文件自身路径的父目录，不跟随 timeline 符号链接。`cache/subs.ass` 缺失 →
  报错并指引先跑 `vtx -t <timeline.json> subs render`；生成失败含完整命令与原因。
- **失败信息滤噪**：仅滤除 `path:line: DeprecationWarning:` 警告块及缩进上下文，不删 traceback 源码或异常消息中的同名文本。
  stdout/stderr 的 json2blend `!` 错误标记优先，再附两路尾部；UserWarning 等输出保留但不抢首展错误。
  计数合并为 `已滤除 N 行 Blender DeprecationWarning`，子进程退出码语义不变。
- **退出码边界**：调用未配置 `--python-exit-code`；未捕获的 Blender Python 异常可能打印 traceback 而进程仍退出 0，不能仅凭退出 0 认定脚本成功。[原始实测与范围](https://github.com/CongliangK/framivra/blob/f1efe5f688b543ca11902c9538dec02a5c265961/plugins/blender-bridge/REPORT-F2.md#L84-L89)。

- `--draft` / `--dry-run` 或停在 qc 之前的 `all --until` 不触发；`all --until qc` 与完整 `all` 一样可执行 finish。
- qc 失败不阻断导出（2026-09-14 裁定：产物已就绪，存量假阴性项目也要能收尾）——
  `vtx all` 退出码按 qc/finish 各自失败态如实汇总（任一失败 exit 1）。
- blender 不在 PATH → `vtx all` 收尾 exit 1（final.mp4 已就绪不受影响）——配置了
  就要导出，失败必须可见，不静默跳过。
- 键在插件未装/禁用时走三分支报错（`vt plugins list` 查看），绝不静默忽略。
- **源产物映射**：导出前经内核缝 A 解析插件产物并传给 `json2blend.py`；音频优先使用映射（含普通媒体的降噪 wav），无映射的 `.json` 虚拟源报错，其余源保留原路径。视频仍用原素材，探测缓存按实际路径隔离；选中同源音频不代表预览混音与成片等价。

## 关停测试（§0.2）

卸载本插件：写了 `blender_bridge` 键的 timeline validate FAIL（三分支），删键即
恢复——主链渲染产物不受任何影响（.blend 是审片预览件，非可信度构成）。

## 测试

`env python3 -m unittest discover -s tests`（本目录，伪 blender 桩验证接线与三分支；
DeprecationWarning 滤除纯函数样例、subs_alpha 自动生成 mock 子进程路径）。
