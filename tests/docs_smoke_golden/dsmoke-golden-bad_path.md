# dsmoke golden 负例：bad_path（微任务书修订 patch §六 档 1）

bash 围栏内引用不存在的仓内路径，`docs_smoke --golden` 必判 FAIL（路径不存在）。

```bash
env python3 vtx/__golden_no_such_a1b2c3.py --help
```
