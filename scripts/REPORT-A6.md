# A6 · 脚手架最小协议合规 gfx 样页

结论：完成。全新 30 秒项目首次 `all` 跑通，QC **9/9**；doctor 离线检查通过，doc_gates **7/7 PASS**。未提交 git commit。

## 改动清单与边界

- [new_project.sh](<new_project.sh>)：创建项目时拷贝自包含模板至项目 `gfx/gfxA.html`；已有文件或符号链接均不覆盖；完成提示明确样页可直接运行及协议入口。
- [gfx-starter.html](<templates/gfx-starter.html>)：新增独立 HTML 模板，非 symlink，无外部 CSS/JS/图片/网络依赖。
- 本报告是任务验收记录，不另设机制文档权威；协议仍归 [gfx.md](<../skill/references/gfx.md#L7-L26>)。
- 只写 scripts/；未修改 apps/、skill/、skills/、docs/ 或版本三源。工作树其他席变更未触碰。

已只读既有 title-card/lower-third：两者均为 FPS=24，且使用深蓝/蓝紫配色，不满足本任务的 30fps/D22；因此手写最小图表页，不复制旧视觉风格。

## 协议逐条自检

| 要求 | 实现与证据 |
|---|---|
| URLSearchParams 读取 f | 页内同步读取 `?f=`，缺省 0；无自定义 onFrame 钩子 |
| FPS=30、N=192 | 常量固定，与脚手架 gfx clip 的 dur=192 对齐，局部时长 6.4s |
| 动画状态=f 纯函数 | 柱高度、数字均由局部 t 与 seg/eoc 确定，无可变时钟状态 |
| 禁时序/随机动画 | 无 CSS animation/transition、Date.now、Math.random、requestAnimationFrame |
| 固定画布 | html/body 1920×1080、background:transparent、overflow:hidden |
| 字体 | `"Noto Sans CJK SC", sans-serif`，标题56px、数据32px、标签28px |
| 末尾0.4秒淡出 | `1 - clamp01(seg(t, N/FPS - 0.4, N/FPS))`；帧180起淡出，192归零 |
| 自包含浅底 | sheet 为 #faf9f6，卡片 #fff；f=0 底色即完整可见，不依赖下层颜色 |
| pip安全区 | 卡片右界1320<1440，关键内容更靠左；f48像素检查整个(1440,600)-(1920,1080)仅RGB(250,249,246) |
| D22 | 近黑 #1a1a1a + 白/暖白 + 卡其/琥珀，无深蓝紫；单一三柱示例数据，不用大标题加文字列表 |
| 依赖声明 | 无外部项目资产，不需要额外 cache_dependencies；系统字体按协议使用 |
| 可选通道 | 不声明 Canvas 导出、静态跨度或 __vtx_render；默认查询参数导航即可，未启用 navfree |
| f独立有效且确定 | f0/f48 PNG不同；f48两次独立Chrome进程截图MD5相同 |

本页是有 aroll 下层的叠层，末尾统一淡出会露出实拍；不是无下层 gfx-full。布局采用整页浅底加数据卡，以保证起步可读和 QC 覆盖。

## 端到端证据

以下是已执行的写态测试记录（仅生成 /tmp 项目，不是零写态文档示范）：

```bash
bash -n scripts/new_project.sh
bash scripts/new_project.sh /tmp/a6_demo 30
ffmpeg -hide_banner -loglevel error \
  -f lavfi -i 'testsrc2=size=1920x1080:rate=30' \
  -f lavfi -i 'sine=frequency=440:sample_rate=48000' \
  -t 30 -c:v libx264 -preset ultrafast -crf 23 -pix_fmt yuv420p \
  -r 30 -c:a aac -b:a 192k -ac 2 /tmp/a6_demo/assets/v01.mp4
python3 vtx/vtx.py -t /tmp/a6_demo/timeline.json all
```

全部退出0。素材 ffprobe：1920×1080，r_frame_rate/avg_frame_rate 均30/1，音频48kHz双声道，duration=30.000000。192帧 gfx 使用 CDP 4 tabs，failed×0。首次 all 关键原始输出：

```text
✓ 校验通过：1 源 / 2 视频轨 2 clips / 1 字幕 / 2 音轨 2 事件
✓ 成片帧数 = 900  实测 900（容器元数据）
✓ 成片时长 ≈ 30.00s  实测 30.000s
✓ 分辨率 1920x1080
✓ 底轨切点帧比对 dst=0 src=v01:2  平均像素差 0.00/255
✓ gfx 窗口 gfxA 生效  采样 [48, 96, 144] 最大均值差 120.7，覆盖 95.2%
✓ 空窗无叠加泄漏 @frame 899  差异 0.04，覆盖 0.0%
✓ 响度归一 ≈ -14 LUFS  实测 -14.0 LUFS
✓ 解码域真峰 TP ≤ -1 dBFS  实测 -7.0 dBFS
✓ 2 轨并发窗口有能量  mean -10.3 dB @frame 24
QC：9/9 通过
完成： /tmp/a6_demo/out/final.mp4
```

完整运行日志：[e2e.log](</tmp/a6_demo/e2e.log>)；成片：[final.mp4](</tmp/a6_demo/out/final.mp4>)。

### 独立预览与双渲

按用户指定 CLI，独立 Chrome 无头截图（写态证据）：

```bash
google-chrome --headless --disable-gpu --no-sandbox --window-size=1920,1080 \
  --hide-scrollbars --default-background-color=00000000 \
  --screenshot=/tmp/a6_demo/f0.png 'file:///tmp/a6_demo/gfx/gfxA.html?f=0'
# 相同参数截图 f=48，两次独立调用分别输出 f48.png 与 f48_repeat.png。
```

| 截图 | MD5 |
|---|---|
| [f0.png](</tmp/a6_demo/f0.png>) | 3dc8a456b2bf647fa054258d9cb503ab |
| [f48.png](</tmp/a6_demo/f48.png>) | d7b9c3d4981d62c5c9776ef3385f6387 |
| [f48_repeat.png](</tmp/a6_demo/f48_repeat.png>) | d7b9c3d4981d62c5c9776ef3385f6387 |

PIL 断言：尺寸均1920×1080；f0与f48 RGBA像素不同；f48双渲 RGBA像素全等；pip 区域无墨迹。人工读图亦确认布局完整、白底近黑字和暖色柱、无黑块。

### 幂等

先对原样页重跑脚手架，MD5前后均 `80cda9bedc61ad715003d86066432ba7`。

为排除“覆盖成同样内容”的伪幂等证据，再给项目页追加 HTML 注释 sentinel，然后重跑脚手架：MD5前后均 `5a1120a635f7a6d0696d05e7a855e0bd`。因此自定义内容确实保留。模板自身未添加 sentinel。

### 仓门检查

```bash
env VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor
bash scripts/doc_gates.sh
```

退出码均0。原始输出：[doctor](</tmp/a6_doctor.log>)、[doc_gates](</tmp/a6_doc_gates.log>)。

```text
doctor 结论：✅ 通过
PASS [README.md:28] ×2 bash -n scripts/new_project.sh
doc_gates: registry=7 output=7 header=7 对账=OK; PASS=7 FAIL=0 SKIP=0
```

`git diff --check` 退出0。

## 风险 / 范围说明

- 仅使模板 gfx 起步跑通，仍需用户提供 CFR30 且含音轨的素材；初始示例不代表成片内容验收。
- 既有脚本每次重写 timeline/schema 的行为未改；此次不覆盖承诺仅针对 gfxA。已存在坏页亦保留，不会自动替换修复。
- 极短项目时长与固定192帧 gfx/200帧 voice 的既有冲突未扩写修复；本任务规定的30秒模板已实证通过。
- 修改 gfx clip 时长须同步 N；移除底轨或转为 gfx-full 时须按权威协议重新设计淡出。
- 三柱数据是占位示例；新页登记/真实数据溯源等产线配套不在本次最小脚手架修改范围，未新增项目 manifest/data-sources。
- audit 输出两条非阻断建议（示例口播覆盖率20.9%、示例语速0.64字/s），QC仍9/9。doctor 的 offline提示与 doc_gates 既有常鸣/降级提示仍存在，不是失败。
- /tmp 证据会随系统清理而消失，关键数值与原始QC输出已内嵌本报告。
