# 包豪斯模块构成

[返回风格集合](../../README.md) · [打开典型 HTML](assets/html/bauhaus.html) · [场景与制作卡源码](assets/html/bauhaus.js)

## 先看题材，再改制作思路

HTML 已有两种题材：**让形状成为秩序**（180 帧）；**构成一种平衡**（180 帧）。制作思路、分镜、动作与编辑练习见页面下方制作卡，唯一 home 为相邻场景源码。

## 动画与媒体制作

包豪斯的 `grid/modules/layouts` 给出共享模数、基础形和各目标构图；`initial_layout/tick_frames/beats` 定义共同拍表、目标、center/corner/frame/alternate 轴和级联帧量。模块沿机械旋转与模数位移重组，逐顶点轨迹极值预检保护构图区；落定必须在排除右端点的窗口内。配色为纸/墨/红/黄/蓝五色，扁平路径另为六色，两者由各后端独立校验，不受 Canvas 四色接口限制。提示音仍是共用占位轨，不会随机械拍表自动同步。

媒体语义输入（下方预演命令给出起手例子）：

- [bauhaus-a.json](assets/examples/bauhaus-a.json)
- [bauhaus-b.json](assets/examples/bauhaus-b.json)

从仓根预演（不写工程）：

```bash
python3 templates/mg-styles/build_media.py --style bauhaus --content templates/mg-styles/styles/bauhaus/assets/examples/bauhaus-a.json --output /tmp/my-bauhaus --frames 180 --dry
```

[运行要求与交付](../../README.md#媒体工程与交付)；实现：[render_bauhaus.py](scripts/render_bauhaus.py)。

早期 [Canvas prototype 源码](scripts/prototype.js) 也就近保留；其统一标题/条目接口与本款 HTML、媒体语义输入不同，状态仍为 prototype，调用见[集合入口](../../README.md#canvas-prototype-接口)。

## HTML 参考的限制（不代表媒体路径）

程序图形参考；浏览器字体与成片质量仍需独立视觉验收。无内置声音。

三条路线分别验收：[当前状态](../../../../docs/reports/mg-styles/status.md)、[历史记录](../../../../docs/reports/mg-styles/validation.md)；[共享捕获协议](../../html/README.md)。
