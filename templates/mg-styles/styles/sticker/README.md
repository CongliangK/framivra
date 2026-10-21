# 贴纸解释：问题、结构与证据

[返回风格集合](../../README.md) · [打开典型 HTML](assets/html/sticker.html) · [场景与制作卡源码](assets/html/sticker.js)

## 先看题材，再改制作思路

HTML 已有两种题材：**手机：单位与证据**（360 帧）；**节水：比较基线**（360 帧）。制作思路、分镜、动作与编辑练习见页面下方制作卡，唯一 home 为相邻场景源码。

## 动画与媒体制作

贴纸的 `question/parts/evidence/conclusion` 分别组织问题、分解、数据证据与结论，`relations` 定义连接，四段 `beats` 用句子引导大画布镜头，末段拉远。证据包含值、单位、来源和 bar/ring 类型；正负柱使用零基线，环图仅用于百分比。示例均明确为教学示意数据。图形为程序白边贴纸，不能称照片抠图；换入真实证据时须连同来源和口径一起编辑。

媒体语义输入（优先 `readable` 版）：

- [phone-readable.json](assets/examples/phone-readable.json)
- [water-readable.json](assets/examples/water-readable.json)
- [phone.json](assets/examples/phone.json)
- [water.json](assets/examples/water.json)

从仓根预演（不写工程）：

```bash
python3 templates/mg-styles/build_media.py --style sticker --content templates/mg-styles/styles/sticker/assets/examples/phone-readable.json --output /tmp/my-sticker --frames 360 --dry
```

[运行要求与交付](../../README.md#媒体工程与交付)；实现：[render_sticker.py](scripts/render_sticker.py)。

早期 [Canvas prototype 源码](scripts/prototype.js) 也就近保留；其统一标题/条目接口与本款 HTML、媒体语义输入不同，状态仍为 prototype，调用见[集合入口](../../README.md#canvas-prototype-接口)。

## HTML 参考的限制（不代表媒体路径）

原创程序图标/白边贴纸参考，不含照片抠图。来源字段是编辑声明，渲染器不会核实。总览用于看关系，细证据需在近景阅读；HTML尚无本环境浏览器像素验收。

三条路线分别验收：[当前状态](../../../../docs/reports/mg-styles/status.md)、[历史记录](../../../../docs/reports/mg-styles/validation.md)；[共享捕获协议](../../html/README.md)。
