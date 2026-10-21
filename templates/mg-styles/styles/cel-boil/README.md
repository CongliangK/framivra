# 手绘逐格 · 让角色行动

[返回风格集合](../../README.md) · [打开典型 HTML](assets/html/cel-boil.html) · [场景与制作卡源码](assets/html/cel-boil.js)

## 先看题材，再改制作思路

HTML 已有两种题材：**萌芽和陪伴**（240 帧）；**纸上工作坊**（240 帧）。制作思路、分镜、动作与编辑练习见页面下方制作卡，唯一 home 为相邻场景源码。

## 动画与媒体制作

能力边界：有限程序角色/曝光模拟，非艺术家逐张绘制或任意角色输入。

CEL 的 `characters` 定义 blob/cat/star/sprout 与情绪，`beats` 定义角色、动作及预备/运动帧预算；`exposures` 使用 ones/twos/threes 的整数帧保持，分别对应一、二、三帧一张画。角色压缩预备、跳跃与伸展、短 smear 和手绘效果由动作驱动；整张图按曝光保持，boil 也不在保持帧偷动。程序轮廓与系统字体不能冒称手绘原画或专用手写字体。

媒体语义输入（下方预演命令给出起手例子）：

- [garden.json](assets/examples/garden.json)
- [workshop.json](assets/examples/workshop.json)

从仓根预演（不写工程）：

```bash
python3 templates/mg-styles/build_media.py --style cel-boil --content templates/mg-styles/styles/cel-boil/assets/examples/garden.json --output /tmp/my-cel-boil --frames 240 --dry
```

[运行要求与交付](../../README.md#媒体工程与交付)；实现：[render_cel.py](scripts/render_cel.py)。

早期 [Canvas prototype 源码](scripts/prototype.js) 也就近保留；其统一标题/条目接口与本款 HTML、媒体语义输入不同，状态仍为 prototype，调用见[集合入口](../../README.md#canvas-prototype-接口)。

## HTML 参考的限制（不代表媒体路径）

原创建模参考，复用媒体语义 schema；浏览器字体、实际视频时序和音轨仍需单独验收。本页无声音。

三条路线分别验收：[资格与历史证据](../../README.md#验收边界)；[共享捕获协议](../../html/README.md)。
