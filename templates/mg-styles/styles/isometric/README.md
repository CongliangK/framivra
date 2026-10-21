# 等距微世界：从依赖到激活

[返回风格集合](../../README.md) · [打开典型 HTML](assets/html/isometric.html) · [场景与制作卡源码](assets/html/isometric.scene.js)

## 先看题材，再改制作思路

HTML 已有两种题材：**社区能源与生活**（240 帧）；**校园知识网络**（210 帧）。制作思路、分镜、动作与编辑练习见页面下方制作卡，唯一 home 为相邻场景源码。

## 动画与媒体制作

能力边界：等角2.5D，不是自由透视摄影机、复杂交通或真实AO。

等角的 `grid/nodes` 组织 home/lab/hub/garden，每个节点显式给出 base、wall、roof、activate 帧，`depends_on` 必须先点亮；`flows` 只在两端已落定后开始。真 30° 等角投影、按空间深度排序、延迟屋盖和整体平行滑轨保持空间关系；不是透视三维模型。

媒体语义输入（下方预演命令给出起手例子）：

- [isometric-neighborhood.json](assets/examples/isometric-neighborhood.json)
- [isometric-campus.json](assets/examples/isometric-campus.json)

从仓根预演（不写工程）：

```bash
python3 templates/mg-styles/build_media.py --style isometric --content templates/mg-styles/styles/isometric/assets/examples/isometric-neighborhood.json --output /tmp/my-isometric --frames 240 --dry
```

[运行要求与交付](../../README.md#媒体工程与交付)；实现：[render_isometric.py](scripts/render_isometric.py)。

早期 [Canvas prototype 源码](scripts/prototype.js) 也就近保留；其统一标题/条目接口与本款 HTML、媒体语义输入不同，状态仍为 prototype，调用见[集合入口](../../README.md#canvas-prototype-接口)。

## HTML 参考的限制（不代表媒体路径）

真实等距投影的Canvas构图参考；不是工程模拟或媒体路径像素等价副本。无音轨。

三条路线分别验收：[资格与历史证据](../../README.md#验收边界)；[共享捕获协议](../../html/README.md)。
