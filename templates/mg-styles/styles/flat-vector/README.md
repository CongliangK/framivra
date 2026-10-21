# 扁平信息图

[返回风格集合](../../README.md) · [打开典型 HTML](assets/html/flat-vector.html) · [场景与制作卡源码](assets/html/flat-vector.js)

## 先看题材，再改制作思路

HTML 已有两种题材：**把一个想法，变成可用产品**（180 帧）；**一栋楼的节能行动**（240 帧）。制作思路、分镜、动作与编辑练习见页面下方制作卡，唯一 home 为相邻场景源码。

## 动画与媒体制作

能力边界：受控因果图与基本形rig，非任意矢量/关节角色；正式声效未验。

扁平内容的 `origin/cards/links` 定义起点、数据卡和有向因果链，`final_order` 决定结尾重排，question/build/resolve 的阶段文字及帧窗口可换。预备压缩、冲出与过冲是角色动作；主体色块扩张擦除承接布局变化。图卡数值须有内容依据，程序不会替编辑判断因果关系。

媒体语义输入（下方预演命令给出起手例子）：

- [flat-vector-ideas.json](assets/examples/flat-vector-ideas.json)
- [flat-vector-energy.json](assets/examples/flat-vector-energy.json)

从仓根预演（不写工程）：

```bash
python3 templates/mg-styles/build_media.py --style flat-vector --content templates/mg-styles/styles/flat-vector/assets/examples/flat-vector-ideas.json --output /tmp/my-flat-vector --frames 180 --dry
```

[运行要求与交付](../../README.md#媒体工程与交付)；实现：[render_flat_vector.py](scripts/render_flat_vector.py)。

早期 [Canvas prototype 源码](scripts/prototype.js) 也就近保留；其统一标题/条目接口与本款 HTML、媒体语义输入不同，状态仍为 prototype，调用见[集合入口](../../README.md#canvas-prototype-接口)。

## HTML 参考的限制（不代表媒体路径）

程序图形参考；浏览器字体与成片质量仍需独立视觉验收。无内置声音。

三条路线分别验收：[资格与历史证据](../../README.md#验收边界)；[共享捕获协议](../../html/README.md)。
