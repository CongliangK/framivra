# 综艺反应字幕：动作、情绪与留读

[返回风格集合](../../README.md) · [打开典型 HTML](assets/html/variety-captions.html) · [场景与制作卡源码](assets/html/variety-captions.js)

## 先看题材，再改制作思路

HTML 已有两种题材：**知识闯关：四个动作**（300 帧）；**夜间送件：短反应**（180 帧）。制作思路、分镜、动作与编辑练习见页面下方制作卡，唯一 home 为相邻场景源码。

## 动画与媒体制作

`reactions` 用 `[in,out)`、文字、情绪、punchFrame和逐字stagger显式对齐背景事件；字幕带与subject_rect保护主体。字体预检检查逐字揭示、入场及至少 6 帧完整显字；punchFrame 后的稳定停留由调用方编排，不保证人类读完长句；换背景需要重编cue，透明字幕作为独立视频层回到原位置。

媒体语义输入（优先 `readable` 版）：

- [captions.json](assets/examples/captions.json)
- [captions-night-readable.json](assets/examples/captions-night-readable.json)

从仓根预演（不写工程）：

```bash
python3 templates/mg-styles/build_media.py --style variety-captions --content templates/mg-styles/styles/variety-captions/assets/examples/captions.json --output /tmp/my-variety-captions --frames 300 --background /path/to/your-background.mp4 --dry
```

[运行要求与交付](../../README.md#媒体工程与交付)；实现：[captions.py](scripts/captions.py)。

早期 [Canvas prototype 源码](scripts/prototype.js) 也就近保留；其统一标题/条目接口与本款 HTML、媒体语义输入不同，状态仍为 prototype，调用见[集合入口](../../README.md#canvas-prototype-接口)。

## HTML 参考的限制（不代表媒体路径）

仅字幕带/主体禁区/节奏示意，不含真实背景视频，不宣称真人节目或自动识别。实际交付需将RGBA字幕接入真实素材、核对动作语义和听感；浏览器像素尚待外部验证。

三条路线分别验收：[当前状态](../../../../docs/reports/mg-styles/status.md)、[历史记录](../../../../docs/reports/mg-styles/validation.md)；[共享捕获协议](../../html/README.md)。
