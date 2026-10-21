# 连续轮廓 · 让概念变形

[返回风格集合](../../README.md) · [打开典型 HTML](assets/html/morph.html) · [场景与制作卡源码](assets/html/morph.js)

## 先看题材，再改制作思路

HTML 已有两种题材：**想法生长**（240 帧）；**汇聚到出发**（180 帧）。制作思路、分镜、动作与编辑练习见页面下方制作卡，唯一 home 为相邻场景源码。

## 动画与媒体制作

能力边界：有限单闭合轮廓，非孔洞/任意多部件拓扑。

Morph 的 `shapes` 为连续窗口的 circle/leaf/star/arrow/drop、标签、位置、尺寸、颜色与转场长度。等弧长轮廓保持统一拓扑，经圆形中介与轻挤压变成下一具象形；几何、位置和色场共同转场，非两形淡化。每形保留落定段；它不支持任意字体/图标拓扑或子部件绑定。

媒体语义输入（下方预演命令给出起手例子）：

- [morph-growth.json](assets/examples/morph-growth.json)
- [morph-navigation.json](assets/examples/morph-navigation.json)

从仓根预演（不写工程）：

```bash
python3 templates/mg-styles/build_media.py --style morph --content templates/mg-styles/styles/morph/assets/examples/morph-growth.json --output /tmp/my-morph --frames 240 --dry
```

[运行要求与交付](../../README.md#媒体工程与交付)；实现：[render_morph.py](scripts/render_morph.py)。

早期 [Canvas prototype 源码](scripts/prototype.js) 也就近保留；其统一标题/条目接口与本款 HTML、媒体语义输入不同，状态仍为 prototype，调用见[集合入口](../../README.md#canvas-prototype-接口)。

## HTML 参考的限制（不代表媒体路径）

原创建模参考，复用媒体语义 schema；浏览器字体、实际视频时序和音轨仍需单独验收。本页无声音。

三条路线分别验收：[资格与历史证据](../../README.md#验收边界)；[共享捕获协议](../../html/README.md)。
