# QC 测试

仓库根统一运行本域：

```bash
python3 -m unittest discover -s tests/qc -t . -v
```

每个 TestCase / test 方法仍可用完整模块名独立诊断。共享输入在 `_` 开头的辅助模块，不参与发现。可选浏览器、原生 worker 或媒体测试的 SKIP 不表示通过；完整真实验收仍使用既有严格媒体门和显式验收运行器。
