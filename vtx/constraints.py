"""项目硬约束单一来源（工单 B02：issue #26 / F-04 / #4，2026-10-13）。

fps/分辨率约束曾三面各说各话（本单修复标的）：
  - timeline.schema.json：project.fps const 30（结构权威）；
  - vtx/infra.py Ctx：收「可整除 48000 的任意正整数」——24/25/50/60 全放行；
  - vtx/preview.html：示例 24fps，且 validate() 根本不校验 fps。
同一时间线三面判定漂移。本模块自此为唯一取值点：

  - infra.Ctx 构造时经 fps_error() 设硬门（ValueError，cli.py main 捕获打印）；
  - schema 为静态文件不做生成：project.fps 为 enum、width/height 为 const，
    取值是本模块 ALLOWED_FPS / ALLOWED_SIZES 的字面镜像（description 互指），
    漂移由 tests/test_fps_constraints.py 解析比对守护，不一致即测试 FAIL；
  - preview.html 内嵌 CONSTRAINTS 镜像（唯一允许的同步抄写点，同测试解析
    比对），validate() 据此判定 fps 与分辨率。

允许集语义（v2 契约冻结值；docs/DEV.md 决策 D9/D14/D24「全管线固定
30fps 精确（≠29.97）」）：
  - fps ∈ {30}：NTSC 非整数帧率（23.976/29.97/59.94）、PAL（25/50）、
    历史 24fps 时代与 60fps 一律拒绝；数值等值的 30.0 视同 30（JSON
    Schema const 的数学等值语义与 JS 单一 number 模型下三面均无法也
    无需区分，infra 侧归一为 int）；
  - 分辨率 ∈ {1920x1080}（投稿母版）；
  - 音频采样率 48kHz：30fps → 每帧整 1600 采样（atrim 采样网格不漂移）。

分辨率门只设在 schema/preview 两面：infra.Ctx 有意不设（渲染测试用
640x360 等降采样夹具跑速度，机制侧分辨率不是硬门——与本单 fps 三面
统一的标的无关）。
"""

# 音频采样率（Hz）。fps 须整除它 ⇒ 每帧采样数为整数（30fps → 1600）。
AUDIO_SAMPLE_RATE = 48000

# 项目时基白名单（单一来源；tuple 保序防重复，消费方一律做成员判定）。
ALLOWED_FPS = (30,)

# 分辨率白名单，(width, height) 序列（schema project.width/height 的镜像源）。
ALLOWED_SIZES = ((1920, 1080),)


def fps_error(fps):
    """fps 判定：合法返回 None，非法返回错误消息（infra 硬门直接 raise 该
    消息；tests/test_fps_constraints.py 三面边界矩阵以本函数为 infra 面判定）。
    bool 显式拒绝（bool 是 int 子类，True 若漏守将数值等值于 1）。
    """
    if isinstance(fps, bool) or not isinstance(fps, (int, float)):
        return (f"project.fps={fps!r} 无效：fps 必须为数字整数"
                f"（时基固定 {'/'.join(map(str, ALLOWED_FPS))}fps，"
                "NTSC 29.97 等非整数帧率不支持）")
    if isinstance(fps, float) and not fps.is_integer():
        return (f"project.fps={fps!r} 无效：非整数帧率不支持"
                f"（时基固定 {'/'.join(map(str, ALLOWED_FPS))}fps，≠29.97）")
    if fps not in ALLOWED_FPS:   # 数值等值成员判定：30.0 == 30 → 放行
        return (f"project.fps={fps!r} 无效：时基固定 "
                f"{'/'.join(map(str, ALLOWED_FPS))}fps"
                "（D24 换基，≠29.97；24/25/50/60 等其余帧率一律拒绝）")
    if AUDIO_SAMPLE_RATE % fps:
        # 白名单现值恒整除（48000 % 30 == 0）；留守卫防未来扩白名单时引入
        # 音频事件 atrim 采样网格漂移（每帧 48000//fps 采样须为整数）。
        return (f"project.fps={fps!r} 无效：不能整除音频采样率 "
                f"{AUDIO_SAMPLE_RATE}，每帧采样数非整数（atrim 网格将漂移）")
    return None


def size_error(width, height):
    """分辨率判定：合法返回 None，非法返回错误消息。仅 schema/preview 面
    消费（infra.Ctx 不设分辨率门——降采样测试夹具依赖，见模块头说明）。"""
    if (width, height) not in ALLOWED_SIZES:
        allowed = "、".join(f"{w}x{h}" for w, h in ALLOWED_SIZES)
        return (f"project.width/height={width!r}x{height!r} 无效："
                f"分辨率仅允许 {allowed}")
    return None
