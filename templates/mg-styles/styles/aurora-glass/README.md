# 极光玻璃：让光场聚焦语义

[返回风格集合](../../README.md) · [打开典型 HTML](assets/html/aurora-glass.html) · [场景与制作卡源码](assets/html/aurora-glass.js)

## 先看题材，再改制作思路

HTML 已有两种题材：**澄光：从声音到专注**（300 帧）；**晚霞：回顾与留言**（300 帧）。制作思路、分镜、动作与编辑练习见页面下方制作卡，唯一 home 为相邻场景源码。

## 动画与媒体制作

能力边界：图像空间光学非path tracing；局部扭曲需清晰时段阅读，HTML需OffscreenCanvas。

玻璃的 `chapters` 指定 chat/voice/focus 微 UI、内容、值及单位，`lens` 控制折射强度、色散和半径。连续光场与镜片运动承接章节，内容按章切换和短入场，不宣称旧内容完整光学变形转场。倾斜卡片按透视单应变换，透镜使用非均匀法线偏移、分通道色散和背景采样；光学属于解析图像空间近似，不是物理路径追踪。标题和说明保持独立可读区，近卡中的小字仍需实际检查。

媒体语义输入（优先 `readable` 版）：

- [aurora-glass-ideas-readable.json](assets/examples/aurora-glass-ideas-readable.json)
- [aurora-glass-reflection-readable.json](assets/examples/aurora-glass-reflection-readable.json)
- [aurora-glass-ideas.json](assets/examples/aurora-glass-ideas.json)
- [aurora-glass-reflection.json](assets/examples/aurora-glass-reflection.json)

从仓根预演（不写工程）：

```bash
python3 templates/mg-styles/build_media.py --style aurora-glass --content templates/mg-styles/styles/aurora-glass/assets/examples/aurora-glass-ideas-readable.json --output /tmp/my-aurora-glass --frames 300 --dry
```

[运行要求与交付](../../README.md#媒体工程与交付)；实现：[render_aurora_glass.py](scripts/render_aurora_glass.py)。

早期 [Canvas prototype 源码](scripts/prototype.js) 也就近保留；其统一标题/条目接口与本款 HTML、媒体语义输入不同，状态仍为 prototype，调用见[集合入口](../../README.md#canvas-prototype-接口)。

## HTML 参考的限制（不代表媒体路径）

解析图像空间折射/色散与透视平面参考；不是物理path tracing，也不是可交互产品。需支持OffscreenCanvas的现代浏览器；本环境没有重启浏览器验收。

三条路线分别验收：[资格与历史证据](../../README.md#验收边界)；[共享捕获协议](../../html/README.md)。
