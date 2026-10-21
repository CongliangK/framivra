# 一笔叙事：从轮廓到共同结果

[返回风格集合](../../README.md) · [打开典型 HTML](assets/html/line-art.html) · [场景与制作卡源码](assets/html/line-art.scene.js)

## 先看题材，再改制作思路

HTML 已有两种题材：**种子到共同的家**（240 帧）；**知识跨过围墙**（240 帧）。制作思路、分镜、动作与编辑练习见页面下方制作卡，唯一 home 为相邻场景源码。

## 动画与媒体制作

线条路径需要 Pillow 与本地字体；液体路径还需要 NumPy。线条内容使用 `story` 中的对象语义、标签和连续整数帧窗口。可选 seed/tree/book/house/network 轮廓按实际几何弧长绘制，桥接上一对象的笔尾与下一对象的笔头，旧线渐弱；末对象由原轮廓转面，保持单一连续画布。`hold` 保留语义阅读段，`drawing_speed` 改变绘制/停留分配。两份例子分别为生长建造与知识连接，不是固定正弦线改标签。

媒体语义输入（优先 `readable` 版）：

- [line-art-a-readable.json](assets/examples/line-art-a-readable.json)
- [line-art-b-readable.json](assets/examples/line-art-b-readable.json)
- [line-art-a.json](assets/examples/line-art-a.json)
- [line-art-b.json](assets/examples/line-art-b.json)

从仓根预演（不写工程）：

```bash
python3 templates/mg-styles/build_media.py --style line-art --content templates/mg-styles/styles/line-art/assets/examples/line-art-a-readable.json --output /tmp/my-line-art --frames 240 --dry
```

[运行要求与交付](../../README.md#媒体工程与交付)；实现：[render_line_art.py](scripts/render_line_art.py)。

早期 [Canvas prototype 源码](scripts/prototype.js) 也就近保留；其统一标题/条目接口与本款 HTML、媒体语义输入不同，状态仍为 prototype，调用见[集合入口](../../README.md#canvas-prototype-接口)。

## HTML 参考的限制（不代表媒体路径）

按语义路径重建的Canvas参考；非媒体路径像素等价副本。最后实色与阅读预算可换。无音轨。

三条路线分别验收：[当前状态](../../../../docs/reports/mg-styles/status.md)、[历史记录](../../../../docs/reports/mg-styles/validation.md)；[共享捕获协议](../../html/README.md)。
