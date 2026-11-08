# Framivra DSH 侧栏 · 工作台视觉设计约定（DESIGN.md）

范围：仅 `integrations/dsh-sidebar`（宿主内嵌 React 客户端）。目标气质：剪辑工具（CapCut/Premiere 式）——低持久文字、画面优先、密度紧凑、深色单色调。本文只记录已定决策，供后续修改对照。

## 意图

- 使用者：在 DSH 会话里核对/微调 vtx 时间线的视频作者，屏幕常驻侧栏，720p 笔记本也要可用。
- 核心动词：选时间线 → 定帧 → 看画面 → （可选）复制/入稿。其他一切让位。
- 感觉：像监视器+时间线的工具，不像设置页。深色、低噪、控件短促。

## 布局铁律（720p 基准）

- 展开态对话框：94dvh；内部每个区域只允许**一个**滚动宿主，禁止三层嵌套滚动（`.fv-body` 展开时应 `overflow:hidden`，滚动只属于 monitor 内容、时间线 lanes、inspector 各自区域）。
- 监视器（`.fv-monitor`）：帧输入行（帧号 + 定位）`position:sticky` 固定不滚动；画面舞台 `max-height: 20dvh`；次级操作收进 `<details>`。
- 时间线（`.fv-timeline-pane`）：`min-height ≥ 300px`，lane 滚动发生在 `.fv-scroll` 内部，外层不裁剪。
- 次要入口（路径打开、验证边界、已有成片）一律默认折叠的 `<details>`，不占常驻空间。

## 持久文字预算

- 常驻可见文案以中文短语为主（≤12 字/控件动词：生成 / 复制 / 入稿 / 取消 / 刷新）。
- 法律/边界整句（写入授权、来源未知声明）保留语义但收进折叠区或 `title` 提示；确权 checkbox 标签用短语（如「允许写 cache/preview（仅本次）」），完整句放 `title` 与详情内。
- 禁止中英混排遗留（如 "Query stored blocks"、"Frame (blank = all)"）；面向用户的动词用中文业务语，不用实现描述。

## 密度与层级

- 主要控件 min-height 34px（行内小按钮 30px）；工作台正文与展开态查询按钮不低于 12px。窄侧栏的紧凑查询按钮目前为 11px，作为现有密度例外；帧号等 meta 数字使用 `tabular-nums`。
- 单色调 + 一处强调色（`--fv-accent`）；层级靠 surface 亮度微阶与 1px 低对比描边，不加阴影堆叠。
- 时间线 ruler/lane 网格是本工作台的签名元素，保持低噪、帧号 `tabular-nums`。

## 状态与边界展示

- 拒绝类文案（未定帧、版本过期、未验证成片）必须出现，但只占一行 + role=alert/status，细节进 `<details>`。
- 「已有成片」入口默认折叠；内部保留 provenance UNKNOWN 声明，不将文件存在当成与当前版本对应的证据。
