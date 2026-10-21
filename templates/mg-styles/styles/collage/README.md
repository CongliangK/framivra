# 剪纸拼贴 · 重排旧关系

[返回风格集合](../../README.md) · [打开典型 HTML](assets/html/collage.html) · [场景与制作卡源码](assets/html/collage.js)

## 先看题材，再改制作思路

HTML 已有两种题材：**观察拼出想象**（240 帧）；**换个角度 · 延长末态**（240 帧）。制作思路、分镜、动作与编辑练习见页面下方制作卡，唯一 home 为相邻场景源码。

## 动画与媒体制作

拼贴的 `parts` 定义程序剪影、父子关系、局部支点、比例与运动量，`beats` 驱动 awaken/release/reframe/resolve 句拍，`exposure` 保持整张画面的定格节奏。关节和不同比例的组合形成观点，撕纸前沿承接结尾；这些是程序纸偶而非照片拼贴。可选 `evidence` 的数据必须带来源；没有自动理解音素或嘴部声音同步。

媒体语义输入（优先 `readable` 版）：

- [collage-curiosity.json](assets/examples/collage-curiosity.json)
- [collage-perspective-readable.json](assets/examples/collage-perspective-readable.json)
- [collage-perspective.json](assets/examples/collage-perspective.json)

从仓根预演（不写工程）：

```bash
python3 templates/mg-styles/build_media.py --style collage --content templates/mg-styles/styles/collage/assets/examples/collage-curiosity.json --output /tmp/my-collage --frames 240 --dry
```

[运行要求与交付](../../README.md#媒体工程与交付)；实现：[render_collage.py](scripts/render_collage.py)。

早期 [Canvas prototype 源码](scripts/prototype.js) 也就近保留；其统一标题/条目接口与本款 HTML、媒体语义输入不同，状态仍为 prototype，调用见[集合入口](../../README.md#canvas-prototype-接口)。

## HTML 参考的限制（不代表媒体路径）

原创建模参考，复用媒体语义 schema；浏览器字体、实际视频时序和音轨仍需单独验收。本页无声音。

三条路线分别验收：[当前状态](../../../../docs/reports/mg-styles/status.md)、[历史记录](../../../../docs/reports/mg-styles/validation.md)；[共享捕获协议](../../html/README.md)。
