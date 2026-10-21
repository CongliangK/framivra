# MG 风格模板

按风格阅读和取用：每款目录集中制作思路、典型 HTML、两题材、媒体输入与独立脚本。制作卡在该款场景源码与页面中维护；完整技术、图像、声音与用户观看状态见[状态矩阵](../../docs/reports/mg-styles/status.md)。

| 风格包 | 制作主线 |
|---|---|
| [cel-boil](styles/cel-boil/README.md) | 角色动作与曝光表 |
| [isometric](styles/isometric/README.md) | 按依赖装配与激活 |
| [flat-vector](styles/flat-vector/README.md) | 主体触发因果与关系重排 |
| [line-art](styles/line-art/README.md) | 线条接力与具象叙事 |
| [soft-3d](styles/soft-3d/README.md) | 材质、关系与连续三维章节 |
| [morph](styles/morph/README.md) | 具名形态链 |
| [sticker](styles/sticker/README.md) | 问题、分解、证据与结论 |
| [hud](styles/hud/README.md) | 轨迹、检测与确认 |
| [collage](styles/collage/README.md) | 观点隐喻与铰接纸偶 |
| [aurora-glass](styles/aurora-glass/README.md) | 微 UI 任务焦点 |
| [bauhaus](styles/bauhaus/README.md) | 模数与机械重组 |
| [synthwave](styles/synthwave/README.md) | 路标旅程与抵达 |
| [pixel](styles/pixel/README.md) | 任务、障碍、行动与奖励 |
| [liquid](styles/liquid/README.md) | 汇聚、融合、分离与揭示 |
| [variety-captions](styles/variety-captions/README.md) | 反应事件、逐字与主体禁区 |

本包将 [mg-styles-15](https://github.com/Vincentwei1021/mg-styles-15) 的一般设计语言转化为独立编写的帧驱动模板，不是原片复刻；[研究记录](../../docs/reports/mg-styles/research.md)说明来源与许可边界。[验证记录](../../docs/reports/mg-styles/validation.md)保留历史快照及失败，不将入口数量当作完成。

## 选择入口

- **学制作、换题材**：进入上表某款 README，打开其典型 HTML；也可用 [HTML 总预览](html/index.html)。两题材、短制作卡、播放器与 JSON 编辑均在款内。共享[HTML 捕获协议](html/README.md)；HTML 尚无浏览器字体/像素/完整播放验收，也没有实际音轨。
- **生成独立媒体工程**：用该款 README 的 `build_media.py --content ... --dry`，按真实角色、任务、轨迹、章节等语义编辑 JSON；源码在款内 `scripts/`。需要的 Pillow/NumPy/Blender/FFmpeg 与本地字体按所选路径提供，缺失明确失败。
- **试验早期 Canvas prototype**：仍用根 `build.py`。十五款保留 prototype 状态，统一标题/条目接口不足以表达完整叙事，不能继承媒体路线的验证。每款实现位于自己的 `scripts/prototype.js`；共用 `runtime.js` 与 `prototype-helpers.js`，构建仅打包所选款实现及公共依赖。

## 媒体工程与交付

两个公开 CLI 均从仓根调用；`--style` 名称保持上表 slug。`--content` 为 UTF-8 JSON，媒体输入按本款语义接口解释，不与 HTML 内嵌 JSON 或 prototype schema 互换。`--frames` 控制整数帧总长，30fps；修改时须同步 `[in,out)` 窗口和阅读预算。

先用款内 `--dry` 命令预检。去掉 `--dry` 会渲染源素材、编码并创建新工程，拒绝覆盖已有目录；部分失败保留日志与中间文件。随后按[内核路由](../../docs/skill/SKILL.md)依次 validate 和完整 all，生成成功不等于技术、艺术、声音验收完成。素材、cache/out、截图和字体保存在仓外，不提交 Git。媒体无 HTML gfx 时不会启动浏览器。

输出保存捕获的 renderer/composer 源码与哈希；系统字体不复制。默认本机 Noto CJK 的来源和 SIL OFL 1.1 另记回执，指定 `font_path` 时由调用方核查许可与覆盖；系统字体与环境改变可能影响字形。三维源重生成和冻结媒体消费分别判定，已知严格重生成差异见验证记录，不改变门槛。

反应字幕须提供有权使用的本地背景视频，其长度/格式/音轨仍须经 validate，换背景必须重编 cue。三维 `full` 为逐帧 Blender，`storyboard` 为底片合成，后者不能冒称完整 Blender 帧序列。声音目前是未同步语义拍点的固定参考提示音，不是完成的配乐或声音设计。

## Canvas prototype 接口

```bash
python3 templates/mg-styles/build.py --style isometric --output /tmp/my-mg-prototype --frames 180 --dry
```

去掉 `--dry` 创建单 HTML 入口、显式脚本缓存依赖、timeline 与 cue.wav；随后仍须 validate 和完整 all。普通动态 gfx 协议不适用于拒绝 JavaScript 的 strict 资源策略，不声明静态跨度。

`--content` 可省略字段，拒绝未知字段：

```json
{"title":"从发现到交付","subtitle":"先提出问题，再让证据说话","items":[{"label":"研究","value":30},{"label":"制作","value":75}],"palette":["#faf5e8","#202730","#e66b42","#49a69d"],"seed":7,"pace":1.0,"hold_frames":12}
```

`items` 为一至六条 label/value，value 0–100仅是视觉量，不自动解释统计单位；palette 为四个 RGB。标题最多180字符、label最多80字符；Canvas测宽缩排，最小24px（原设小字保持原字号下限），单行放不下或换行报错，长度上限不保证任意构图可读。`seed` 是有符号32位整数；`pace` 为0.5–2的节拍曲线指数（小于1前段更快，端点不变）；`hold_frames` 是小于总长一半的末态停留整数帧。

`variety-captions` prototype是在横屏中组织竖屏区域，不是原生9:16输出；line-art固定正弦路径、liquid圆形重叠和玻璃局部采样均有明确能力缺口。模拟Canvas乱序重放只证明相应命令与状态，不证明浏览器像素或艺术质量。交付组织先读[内核 HTML 创作组织](../../docs/skill/references/gfx.md#html-创作组织)。
