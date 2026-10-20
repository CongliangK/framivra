# MG 风格模板

本包将 [mg-styles-15](https://github.com/Vincentwei1021/mg-styles-15) 的一般设计语言转化为独立编写的帧驱动程序模板。不是原片复刻。逐款设计研究与许可边界见 [研究记录](../../docs/mg-styles-research.md)。

本包有两条入口：`build.py` 的十五款 Canvas **prototype**，以及 `build_media.py` 的三款深入样板。前者仍是标题/标签/数值接口，尚不足以表达各风格完整叙事；后者分别接受三维场景与节拍、像素任务与障碍、实际视频与反应 cue。实现/缺失/验证状态以[研究矩阵](../../docs/mg-styles-research.md)和[验证记录](../../docs/mg-styles-validation.md)为准，不以入口数量代表完成。

从仓根预演：

```bash
python3 templates/mg-styles/build.py --style isometric --output /workspace/my-mg --frames 180 --dry
```

去掉 `--dry` 会创建新工程目录；已有目录拒绝覆盖。实际写入后，按 [内核操作路由](../../docs/skill/SKILL.md) 对新工程依次 validate 和完整 all。此入口不自行调用渲染器；媒体入口会显式调用所选素材渲染器。两者均不安装工具或更改权限。成片、缓存与截图应保存在仓外，不提交 Git。

`--content` 接受 UTF-8 JSON 文件路径，字段如下；省略字段沿用默认值，不接受未知字段。`items` 是一至六条 label/value，value 为 0–100 的视觉量；它不是自动解释的统计单位。颜色为四个十六进制 RGB（背景、文字、强调、辅助）。标题最长 180 字符、条目标签最长 80 字符，Canvas 按测量宽度缩字，过密长文仍需编辑与实际视觉验收。

```json
{
  "title": "从发现到交付",
  "subtitle": "先提出问题，再让证据说话",
  "items": [{"label": "研究", "value": 30}, {"label": "制作", "value": 75}],
  "palette": ["#faf5e8", "#202730", "#e66b42", "#49a69d"],
  "seed": 7,
  "pace": 1.0,
  "hold_frames": 12
}
```

`seed` 必须为有符号 32 位整数（−2147483648 至 2147483647），避免 JavaScript 数值溢出。`hold_frames` 为末态构图停留的整数帧，须小于总长的一半。

`pace` 为 0.5–2 的节拍曲线指数：小于 1 加快前段揭示，大于 1 延迟前段揭示，端点不变。

节奏以 `--frames` 的整数帧总长控制，各风格使用归一化阶段窗口随长度伸缩。范围由构建器校验；帧率固定 30。修改时间结构应调整对应模块的阶段窗口，再用不同总帧数检查阅读停留。构建器生成原创确定性参考提示音轨 `cue.wav`，以保证现有音频链可运行；它不是完成的配乐设计。实际创作应按现有音轨协议替换为配音或配乐，再检查声画节拍。

可选风格以构建器 `--help` 为准。源码按三组维护，`runtime.js` 只管理同步、测量文字与画布恢复；不统一各款场景布局。`variety-captions` 在横屏画布中央组织竖屏节目区域，并非原生 9:16 输出。系统字体会随平台变化，交付前确认目标环境字形与排版；不承诺跨机器像素相同。

生成工程显式声明内容与所有脚本的缓存依赖。动画由请求帧纯函数计算，无 CSS 动画或墙钟，不声明静态跨度。采用普通动态 gfx 协议，不能用于拒绝 JavaScript 的 strict 资源策略。Canvas 操作重放的重复帧、乱序帧（非浏览器像素），以及不同内容和时间线验证由 [测试](../../tests/test_mg_styles.py) 支持；真实渲染、艺术复审与用户观看确认须分别记录，静态测试不能代替成片验收。

能力状态：十五款 Canvas 均保持 prototype。`line-art` 固定正弦路径缺连续具象叙事，`liquid` 的圆形重叠缺融合颈/液体遮罩；不把这些缺口称为已实现。玻璃局部采样不构成物理折射。未宣称十五款全部合格。


## 三款独立媒体样板

选择的工具依赖缺失会明确失败。源帧编码只制作素材；剪辑、混音和正式交付仍走现有 vtx 时间线。无 HTML gfx 的工程不会启动浏览器，这与浏览器环境恢复是不同结论。所有写入在新工程目录；部分生成失败会保留日志与中间文件，排查后换新目录重建。

```bash
python3 templates/mg-styles/build_media.py --style soft-3d --content templates/mg-styles/examples/three-d.json --output /workspace/my-3d --frames 30 --samples 32 --dry
python3 templates/mg-styles/build_media.py --style pixel --content templates/mg-styles/examples/pixel.json --output /workspace/my-pixel --frames 300 --dry
python3 templates/mg-styles/build_media.py --style variety-captions --content templates/mg-styles/examples/captions.json --background /workspace/my-pixel/media/pixel.mp4 --output /workspace/my-captions --frames 300 --dry
```

去掉 `--dry` 会渲染素材、编码源媒体、创建 timeline 与日志；随后必须按内核路由执行 validate 和完整 all。背景视频须自行提供且有使用权，满足内核时基、长度与音轨约束；构建成功不等于背景已通过 validate。示例字幕的语义与示例像素任务各障碍窗口对应；换背景必须重编 cue，不能只替换文件名。字幕源按整段实际 alpha 联合边界裁成独立资产，再由时间线 x/y 放回原位；这保留原始画面，不拉大字幕。源开头/结尾各一帧淡入/淡出，以现有透明视频合成契约保留 alpha。

- 三维：需要 Blender 与 FFmpeg；`objects` 定义形状、材质、粗糙度和大小量，`scene.layout` 决定舞台/弧形空间布局；整数帧 `beats` 定义 reveal、settle、resolve，`transition` 选择停留或相机拉远。PNG 与 scene.blend 记录真实体积和光照，执行脚本快照随工程保存。样本数是画质参数；低样本预览有粒噪，不能当高质量终片。去噪依赖 Blender 编译能力，本云环境缺 OpenImageDenoise；生成器默认关闭，不静默忽略失败。指定本地字体可用 `font_path`。
- 像素：需要 Pillow、FFmpeg 与本地 CJK 字体；`mission`、带帧窗口和障碍类型的 `steps`、`reward` 驱动场景。原生 320×180 整数坐标，字体阈值点阵化后最近邻放大六倍；这是栅格化系统字，不冒称专门设计的像素字体。跳跃、跨沟、下蹲、收集与奖励是不同动作。示例为 300 帧，奖励窗口需要保留阅读时间。
- 反应字幕：需要 Pillow、FFmpeg、背景视频和本地字体；每条 `reactions` 有 `[in,out)`、文字、情绪，可附 `punchFrame` 与逐字 `stagger`。不足以完整揭示和阅读的窗口会拒绝。`caption_band` 选择上下区域，`subject_rect` 保护主体；发生冲突则拒绝。上区示例保护游戏底部角色，下区适用于主体在上方的视频。字幕 PNG 经 alpha ProRes 编码后与原背景分别作为普通视频源进入时间线。

默认使用本机 Noto CJK，来源及 SIL OFL 1.1 由各渲染证据记录；不分发字体文件。显式指定字体时，调用方负责核查其许可与覆盖。参考提示音仍是 placeholder，不与视觉 beat/pace 同步，不能声称声音设计完成。
