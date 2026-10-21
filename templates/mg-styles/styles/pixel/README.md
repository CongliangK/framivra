# 像素任务：动作承载意义

[返回风格集合](../../README.md) · [打开典型 HTML](assets/html/pixel.html) · [场景与制作卡源码](assets/html/pixel.scene.js)

## 先看题材，再改制作思路

HTML 已有两种题材：**知识碎片任务**（300 帧）；**社区修复任务**（240 帧）。制作思路、分镜、动作与编辑练习见页面下方制作卡，唯一 home 为相邻场景源码。

## 动画与媒体制作

`mission/steps/reward` 将任务、障碍动作与奖励串成故事。画面在原生320×180整数网格绘制并最近邻放大；jump/gap/duck/collect有不同动作，末段留出奖励阅读时间。文字是系统字体点阵化，不是专用像素字库。

媒体语义输入（下方预演命令给出起手例子）：

- [pixel.json](assets/examples/pixel.json)

媒体输入目前仅一份；第二题材在 HTML 场景中，不把它冒称第二套已交付媒体 JSON。

从仓根预演（不写工程）：

```bash
python3 templates/mg-styles/build_media.py --style pixel --content templates/mg-styles/styles/pixel/assets/examples/pixel.json --output /tmp/my-pixel --frames 300 --dry
```

[运行要求与交付](../../README.md#媒体工程与交付)；实现：[render_pixel.py](scripts/render_pixel.py)。

早期 [Canvas prototype 源码](scripts/prototype.js) 也就近保留；其统一标题/条目接口与本款 HTML、媒体语义输入不同，状态仍为 prototype，调用见[集合入口](../../README.md#canvas-prototype-接口)。

## HTML 参考的限制（不代表媒体路径）

Canvas整数格点设计参考；六倍放大的原生网格，系统字体须在目标设备验证。无音轨。

三条路线分别验收：[当前状态](../../../../docs/reports/mg-styles/status.md)、[历史记录](../../../../docs/reports/mg-styles/validation.md)；[共享捕获协议](../../html/README.md)。
