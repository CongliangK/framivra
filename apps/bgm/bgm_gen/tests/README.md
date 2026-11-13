# bgm_gen 测试

从 Framivra 根目录运行，或从任意目录传入测试的绝对路径；不要求进入 app、不改变 pytest 全局 cwd。

```bash
python3 -m pytest apps/bgm/bgm_gen/tests -q -ra
python3 -m pytest apps/bgm/bgm_gen/tests -q -ra -m slow
python3 -m pytest apps/bgm/bgm_gen/tests -q -ra -m service
python3 -m pytest apps/bgm/bgm_gen/tests -q -ra -m "fast or slow or service"
```

默认只选 fast，slow/service 记 deselected。`-m ""` 仍是默认 fast，不等于全档。每个本 app 用例必须有 fast/slow/service marker；规则不施加于仓内其他测试。

## 分档与前置

- **fast**：工程/schema、show/edit、MIDI/MusicXML、描述符、资源调度、合成、混音与 QC。numpy/pytest/FFmpeg 是基础测试依赖。新增[迁移集成测试](<test_integration.py>)隔离 XDG 数据根，不读取本机 runtime，实跑独立 cwd 的 synth 全链与双渲音频一致性，并验证外置配置、显式空 runtime、无 torch 导入及 demucs 无权重不启动子进程。
- **stock 资产回归**：保留旧 SF2/SFZ/sample、库大小/计数/许可断言。未提供外置 runtime/资产或某可选引擎时明确 skip，不制造假音源替代旧断言；用户提供配置后仍按原标准严格执行。自包含代码测试和新 synth 全链不因缺这些可选资产而跳过。
- **历史冻结样本**：旧 `out/` 中未 tracked 的 spec、音频、完整工程不随源码迁入；缺失时明确 skip。不会把 freshly rendered 文件冒充历史位级锚点。历史样本及旧版本测试结果由[固定提交](https://github.com/CongliangK/scoreweft/tree/804c24ecf2d3e0a55fbd5b2225d3b09da7e86f87/bgm_gen/tests)溯源。
- **slow**：demucs、matchering、外部音色/完整工程等环境测试；需用户本地模型/工具，demucs 必须有 `demucs.repo` 的本地 YAML 与 `.th` 权重。
- **service**：ComfyUI 及其自备节点/模型。服务不可达明确 skip；可达后运行会向该服务提交工作流，属于有计算与写产物的验证，不是健康检查。

外部工具位级冻结版本以 descriptors 的 binary_pin 为唯一事实源；已有 `skip_if_binary_version_drift` 仅跳过已知版本漂移的位级断言段，不修改基线哈希、不放宽原断言。描述符静态事实与磁盘深一致仍验证；sfizz 存在时二进制 pin 仍严格对拍。

## 临时产物与隔离

`BGM_TMP_ROOT` 指定本次测试产物目录；未设时创建专属系统临时目录。pytest 自身目录可用 `--basetemp` 指定。不会扫描清理其他任务的 `/tmp`，也不读旧仓本机 runtime。可用独立 `XDG_DATA_HOME` 隔离默认用户数据，或显式 `MUSIC_RUNTIME` 测试用户提供的环境。报告必须分列 passed / skipped / deselected 与未运行项目，默认 fast 通过不是全环境资格。

原覆盖盘点见[迁入审计记录](<COVERAGE_AUDIT.md>)；其中日期、计数与历史项目环境仅作溯源，当前实际执行以 pytest 结果为准。
