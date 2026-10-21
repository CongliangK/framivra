# 战术信息界面

[返回风格集合](../../README.md) · [打开典型 HTML](assets/html/hud.html) · [场景与制作卡源码](assets/html/hud.js)

## 先看题材，再改制作思路

HTML 已有两种题材：**山地救援 · 信号追踪**（180 帧）；**水下巡检 · 异常识别**（240 帧）。制作思路、分镜、动作与编辑练习见页面下方制作卡，唯一 home 为相邻场景源码。

## 动画与媒体制作

能力边界：显式模拟遥测，非真实测量、识别或自由事件系统。

HUD 的 `target.points` 定义按帧覆盖全长的安全区轨迹；`stages` 组织 search/detect/confirm 三类连续状态与用户文字，`telemetry` 提供标签、值和单位。扫描、括号、环和数据层级跟随轨迹与状态，确认阶段输出精确最终数值。示例遥测是解释性示意数据，不是仪器测量；没有真三维全息、物理传感器或同步音效。

媒体语义输入（下方预演命令给出起手例子）：

- [hud-rescue.json](assets/examples/hud-rescue.json)
- [hud-water.json](assets/examples/hud-water.json)

从仓根预演（不写工程）：

```bash
python3 templates/mg-styles/build_media.py --style hud --content templates/mg-styles/styles/hud/assets/examples/hud-rescue.json --output /tmp/my-hud --frames 180 --dry
```

[运行要求与交付](../../README.md#媒体工程与交付)；实现：[render_hud.py](scripts/render_hud.py)。

早期 [Canvas prototype 源码](scripts/prototype.js) 也就近保留；其统一标题/条目接口与本款 HTML、媒体语义输入不同，状态仍为 prototype，调用见[集合入口](../../README.md#canvas-prototype-接口)。

## HTML 参考的限制（不代表媒体路径）

程序图形参考；浏览器字体与成片质量仍需独立视觉验收。无内置声音。

三条路线分别验收：[资格与历史证据](../../README.md#验收边界)；[共享捕获协议](../../html/README.md)。
