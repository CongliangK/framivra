# 合成波旅程

[返回风格集合](../../README.md) · [打开典型 HTML](assets/html/synthwave.html) · [场景与制作卡源码](assets/html/synthwave.js)

## 先看题材，再改制作思路

HTML 已有两种题材：**穿越夜色**（180 帧）；**向海边出发**（240 帧）。制作思路、分镜、动作与编辑练习见页面下方制作卡，唯一 home 为相邻场景源码。

## 动画与媒体制作

能力边界：图像镀铬、VHS事件近似，非物理材质/完整磁带信号链。

合成波的 `waypoints` 给出路标到达帧、标签与左右位置，approach/cross/arrive 三段驱动前进到停车；`tracking` 只在显式窗口局部错位和 RGB 分离，落定阅读期不允许 tracking。透视网格、连续世界坐标山层、光晕与金属色场文字构成旅程；这些是二维投影与程序媒介效果，不是物理镜头、真实镀铬材质或完整模拟磁带链。各路径实际成片、字体与边界问题以[验收边界](../../README.md#验收边界)为准。

媒体语义输入（下方预演命令给出起手例子）：

- [synthwave-night.json](assets/examples/synthwave-night.json)
- [synthwave-coast.json](assets/examples/synthwave-coast.json)

从仓根预演（不写工程）：

```bash
python3 templates/mg-styles/build_media.py --style synthwave --content templates/mg-styles/styles/synthwave/assets/examples/synthwave-night.json --output /tmp/my-synthwave --frames 180 --dry
```

[运行要求与交付](../../README.md#媒体工程与交付)；实现：[render_synthwave.py](scripts/render_synthwave.py)。

早期 [Canvas prototype 源码](scripts/prototype.js) 也就近保留；其统一标题/条目接口与本款 HTML、媒体语义输入不同，状态仍为 prototype，调用见[集合入口](../../README.md#canvas-prototype-接口)。

## HTML 参考的限制（不代表媒体路径）

程序图形参考；浏览器字体与成片质量仍需独立视觉验收。无内置声音。

三条路线分别验收：[资格与历史证据](../../README.md#验收边界)；[共享捕获协议](../../html/README.md)。
