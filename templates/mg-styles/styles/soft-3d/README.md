# 柔和三维：关系分镜参考

[返回风格集合](../../README.md) · [打开典型 HTML](assets/html/soft-3d.html) · [场景与制作卡源码](assets/html/soft-3d.scene.js)

## 先看题材，再改制作思路

HTML 已有两种题材：**零件成为系统**（120 帧）；**两种材质建立关系**（120 帧）。制作思路、分镜、动作与编辑练习见页面下方制作卡，唯一 home 为相邻场景源码。

## 动画与媒体制作

去掉 `--dry` 会真正逐帧执行 Blender；此例源为原生 960×540，时间线 1920×1080 会放大素材，不称原生 1080p。每整数帧烘焙真实相机轨道/look-at、体积守恒的非均匀 squash/stretch、对象重排和关系杆；文字逐帧跟随相机保持视口布局。每章窗口包括最后章节都需留够转场完成与落定帧。这里的形变是解析网格缩放而非软体物理；质量参数、实际耗时和画面限制见[验证记录](../../../../docs/reports/mg-styles/validation.md)。连续 A/B 的已启动快照不继承后续源码修复；源版本及两套旧快照的方块/环体穿地局限须按该记录逐项判断。CPU 渲染耗时取实际日志估算，不把 `--dry`、场景检查或三底片合成称为完整逐帧验收。

媒体语义输入（下方预演命令给出起手例子）：

- [three-d-continuous-a.json](assets/examples/three-d-continuous-a.json)
- [three-d-continuous-b.json](assets/examples/three-d-continuous-b.json)
- [three-d.json](assets/examples/three-d.json)
- [three-d-materials.json](assets/examples/three-d-materials.json)
- [three-d-story.json](assets/examples/three-d-story.json)

连续 A/B、三维分镜与早期材质输入是不同路线；`three-d-story.json` 的 240 帧可选择 `--three-d-mode storyboard`，不能把三张底片合成称为逐帧 Blender。

从仓根预演（不写工程）：

```bash
python3 templates/mg-styles/build_media.py --style soft-3d --content templates/mg-styles/styles/soft-3d/assets/examples/three-d-continuous-a.json --output /tmp/my-soft-3d --frames 120 --samples 64 --render-width 960 --render-height 540 --three-d-mode full --dry
```

[运行要求与交付](../../README.md#媒体工程与交付)；实现：[render_3d.py](scripts/render_3d.py)、[compose_3d_story.py](scripts/compose_3d_story.py)。

早期 [Canvas prototype 源码](scripts/prototype.js) 也就近保留；其统一标题/条目接口与本款 HTML、媒体语义输入不同，状态仍为 prototype，调用见[集合入口](../../README.md#canvas-prototype-接口)。

## HTML 参考的限制（不代表媒体路径）

Canvas投影与材质示意；不是真实网格、灯光或PBR，不代替Blender逐帧渲染。没有声音。

三条路线分别验收：[当前状态](../../../../docs/reports/mg-styles/status.md)、[历史记录](../../../../docs/reports/mg-styles/validation.md)；[共享捕获协议](../../html/README.md)。
