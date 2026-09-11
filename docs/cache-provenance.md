# 缓存与回执：输入变化必须使旧结果失效

## 修复的问题

原缓存按文件存在、帧数或 HTML 时间戳判断命中。同路径替换同长素材、修改字幕，或两个目录使用同名 HTML 时，旧声音/画面可能继续被输出。失败的重跑也可能留下旧 QC 成功回执。

现在每次 CLI 调用流式计算素材、HTML、时间线、显式依赖与 vtx Python 实现的 SHA-256。结果存入 `cache/<input-fingerprint>/`；不读取旧式平铺缓存。HTML 缓存还区分绝对路径与时长，分段缓存区分段数与 GPU 请求。相同字节可以继续复用；任何被追踪输入变更都启用独立缓存。

这是保守的项目级失效策略：修改一条字幕也可能重建全部缓存。优先保证正确性，没有宣称最细粒度增量性能。每次 CLI 会读取输入字节，大素材有额外 I/O；`all` 仅建立一次上下文。

## HTML 依赖

外部 CSS、JavaScript、图片、字体等本地依赖需显式列出，例如：

```json
"project": {
  "fps": 30, "width": 1920, "height": 1080, "duration_frames": 180,
  "cache_dependencies": ["gfx/style.css", "gfx/logo.png"]
}
```

不存在或非文件依赖会报错。未声明的间接依赖、网络资源、运行时下载和浏览器/FFmpeg 升级不属于此指纹的覆盖范围；建议使用内嵌或明确声明的静态本地素材。升级运行依赖后请清缓存并重新运行。输入在一次命令期间应保持不变，同一项目不要同时运行多个写入命令。

## 阶段回执

`receipt.json` 绑定 input_fingerprint；输入变化不继承旧的成功状态。CLI 在阶段开始前撤销其旧成功及下游成功，失败重跑不能保留旧 QC PASS。audio 要求当前 prepare，video（有 gfx 时）要求当前 gfx，mux 要求当前 video/audio，qc 要求当前 mux。可以直接运行 `all` 完成依赖顺序。旧工程首次需完整重建一次；不直接认领旧产物。

历史缓存保留以便恢复和审计；工具的显式 `clean` 仍删除项目 cache/ 与 out/。本次不自动删除用户旧工程或媒体。回执是流程来源检查，不是对最终文件外部篡改的完整性证明。

## 回归

- `python -m unittest discover -s tests -v`：内容/时间戳/依赖/同名页面/回执失效测试，以及可选 Chrome/FFmpeg 集成用例。
- `python tests/run_cache_regression.py --repo <checkout> --output <new-dir> --ffmpeg <executable> --chrome <executable>`：初次渲染→同长素材替换且恢复时间戳→修改字幕/HTML→不清缓存重跑→独立冷启动对照。比较完整 180 帧的缩略 RGB 与解码 PCM。
- `tests/run_e2e.py` 与 `tests/verify_e2e.py`：12 秒单段/并行全流程、严格 QC、接缝和音频对照。

Windows 实测旧候选版本暖/冷最大单帧平均差 100.64/255、音频不一致且旧成片字节完全不变；修复版本差为 0、音频一致，成片已更新。这里是确定性合成用例的正确性结果，不是长视频或跨平台性能结论。
