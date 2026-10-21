# 柔和三维：关系分镜参考

[返回风格集合](../../README.md) · [打开典型 HTML](assets/html/soft-3d.html) · [场景与制作卡源码](assets/html/soft-3d.scene.js)

## 先看题材，再改制作思路

HTML 已有两种题材：**零件成为系统**（120 帧）；**两种材质建立关系**（120 帧）。制作思路、分镜、动作与编辑练习见页面下方制作卡，唯一 home 为相邻场景源码。

## 动画与媒体制作

去掉 `--dry` 会真正逐帧执行 Blender；此例源为原生 960×540，时间线 1920×1080 会放大素材，不称原生 1080p。每整数帧烘焙真实相机轨道/look-at、体积守恒的非均匀 squash/stretch、对象重排和关系杆；文字逐帧跟随相机保持视口布局。每章窗口包括最后章节都需留够转场完成与落定帧。这里的形变是解析网格缩放而非软体物理；质量参数、实际耗时和画面限制见[固定验证原文](https://github.com/CongliangK/framivra/blob/2f14b574f9ed431978e988ee2906c58912b45488/docs/reports/mg-styles/validation.md#连续3d源重生成与冻结资产消费分账)。连续 A/B 的已启动快照不继承后续源码修复；源版本及两套旧快照的方块/环体穿地局限须按该记录逐项判断。CPU 渲染耗时取实际日志估算，不把 `--dry`、场景检查或三底片合成称为完整逐帧验收。

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

### 验收限制

Cycles 原生540p上采样、有粒噪；解析形变非物理软体，HTML仅投影示意。2026-10-20冻结的 `continuous-ground-fixed/3d-{a,b}` 各120帧，原生960×540、64spp、CPU4、seed0/animated_seed=false/no-denoise。全部240源 PNG 的 SHA、序列/输入/源码及技术链核对通过，正 world-mesh 最低 z 闭合旧穿地，但不是物理模拟终验；图审为全部缩略帧及选定原生帧，并非所有帧原生全检。帧 profile 不代表全链墙钟或性能基准。

**外部源重生成与冻结资产消费分账**：外部 Blender 各新进程仅重渲 f47 一次，A严格 RGBA **FAIL**（518400像素中3个 RGB 像素差1级，meanabs 0.0000019290123456790124，0..255单位），B仅该单帧严格 **PASS**；未改容差、重试筛选或宣称全部源逐位一致。另一次 `frozen-consumption-replay/a,b` 逐文件 SHA 相同，各 all/strict exit0，120帧 decoded RGB SHA、PTS/时长与 MP4 SHA相同，只授予该冻结环境的资产消费 **PASS**；没有浏览器/Blender重启，不覆盖源重生成失败，原工程未改。精确环境、renderer/成片 SHA和回放证据见[固定验证原文](https://github.com/CongliangK/framivra/blob/2f14b574f9ed431978e988ee2906c58912b45488/docs/reports/mg-styles/validation.md#连续3d源重生成与冻结资产消费分账)，不转授当前源码。

OIDN 不受该 Blender build 支持；EEVEE 遭遇 EGL_BAD_MATCH/只读 cache 错误，以 exit130 终止，未改权限重试。Cycles CPU 通过不授予失败引擎资格。storyboard 的三张原生1080p/128spp底片仅组成240帧二维遮罩合成；源保持/擦入像素核对通过仍不等于完整 Blender 序列或连续三维运动。

## HTML 参考的限制（不代表媒体路径）

Canvas投影与材质示意；不是真实网格、灯光或PBR，不代替Blender逐帧渲染。没有声音。

三条路线分别验收：[资格与历史证据](../../README.md#验收边界)；[共享捕获协议](../../html/README.md)。
