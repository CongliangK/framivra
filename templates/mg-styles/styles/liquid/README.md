# 液面连接 · 分流与成形

[返回风格集合](../../README.md) · [打开典型 HTML](assets/html/liquid.html) · [场景与制作卡源码](assets/html/liquid.js)

## 先看题材，再改制作思路

HTML 已有两种题材：**合力 · 完整末态留读**（270 帧）；**水旅程 · 完整末态留读**（300 帧）。制作思路、分镜、动作与编辑练习见页面下方制作卡，唯一 home 为相邻场景源码。

## 动画与媒体制作

液体内容使用 gather→merge→split→resolve 四段，标签、整数窗口、统一 amount、seed 和配色可换。标量场等势轮廓产生真实融合颈、断裂与延迟小滴，最终液体遮罩揭示结论；它不是 Navier–Stokes 物理求解器。各段 amount 需一致以保持边界连续。解析连通性在低分辨率检查，实际画面以较高采样遮罩抗锯齿；文字预检覆盖中央窄区，最小字号仍超界就拒绝。

媒体语义输入（下方预演命令给出起手例子）：

- [liquid-a.json](assets/examples/liquid-a.json)
- [liquid-b.json](assets/examples/liquid-b.json)

从仓根预演（不写工程）：

```bash
python3 templates/mg-styles/build_media.py --style liquid --content templates/mg-styles/styles/liquid/assets/examples/liquid-a.json --output /tmp/my-liquid --frames 240 --dry
```

[运行要求与交付](../../README.md#媒体工程与交付)；实现：[render_liquid.py](scripts/render_liquid.py)。

早期 [Canvas prototype 源码](scripts/prototype.js) 也就近保留；其统一标题/条目接口与本款 HTML、媒体语义输入不同，状态仍为 prototype，调用见[集合入口](../../README.md#canvas-prototype-接口)。

## HTML 参考的限制（不代表媒体路径）

CPU隐式液面参考，不是物理流体或镜面仿真；4px场采样。当前浏览器未获启动验证，字体与视频需另验；无音轨。

三条路线分别验收：[当前状态](../../../../docs/reports/mg-styles/status.md)、[历史记录](../../../../docs/reports/mg-styles/validation.md)；[共享捕获协议](../../html/README.md)。
