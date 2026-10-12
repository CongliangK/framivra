# F3 红队发现修复回执（research + sync）

## 结论

M4 与 MINOR 均已修复，全部指定验证通过。本件为指定任务验收记录，不新增操作权威层。未联网、未安装第三方依赖、未 commit；本轮只编辑 apps/research/ 与 apps/sync/，未修改 docs、版本三源、manifest 或其他智能体的改动。

## M4：正常空页不得丢弃前页数据

- [bilibili.py](/home/liangruochong/文档/01_活跃项目/framivra/apps/research/bilibili.py#L177-L202)：合法 `result=[]` 保留既有有限重试次数；重试耗尽后只停止当前关键词分页，继续处理其他关键词。整批最终有数据则保存并退出 0，最终零条才退出 2，不写伪成功空文件。已有结果合并去重逻辑不变。
- 风控仍由原请求校验层识别，绝不作为正常空页：`v_voucher/-352/-412` 的异常页最终失败返回 1，整批本轮结果不落盘。为保持原 21 项回归，已有异常有限重试和 voucher 重试后恢复语义不变；`--retry 1` 时立即返回 1。
- [README.md](/home/liangruochong/文档/01_活跃项目/framivra/apps/research/README.md#L24-L30) 同步空页退出语义。既有 A5 回执为前轮记录，其中“真空页丢弃前页”的边界由本轮修复替代，未改写历史回执。
- [research 测试](/home/liangruochong/文档/01_活跃项目/framivra/apps/research/tests/test_requests.py)：原正常空列表用例明确限定首页空，验证 `--pages 2 --retry 3` 返回 2、不保存；新增第 1 页有数据、第 2 页空时保存一次并返回 0、当前关键词耗尽后不请求第 3 页且继续下一关键词、首关键词空但后续关键词有数据时返回 0、第 2 页风控失败时不保存前页等场景。保留原 21 项覆盖，合计 25 项。

## MINOR：默认配置定位仓根

- [sync_audio.py](/home/liangruochong/文档/01_活跃项目/framivra/apps/sync/sync_audio.py#L31-L51)：默认配置路径改为 `Path(__file__).resolve().parents[2] / "config.local.json"`，与仓内解析惯例一致，不再误读 apps 层；`VTX_CONFIG` 覆盖优先以及配置解释器不存在时的回退逻辑不变。
- [sync 测试](/home/liangruochong/文档/01_活跃项目/framivra/apps/sync/tests/test_dep_guard.py#L90-L177)：新增临时 repo/apps/sync 布局 CLI 测试，在仓根配置存在的假解释器路径，并放置 apps 层干扰配置，断言指引只引用正确路径；另覆盖环境变量覆盖优先、配置路径不存在时回退已安装默认解释器、两个解释器均不存在时保留通用文案。
- 同一临时布局以本机基础 python3（确实无 scipy，不注入缺库 stub）运行三个真实 WAV 参数：退出 3，stderr 引用仓根配置指定的假解释器路径、无 traceback，三个 WAV 字节均未改动。该实测用例实际执行通过，未 skip。假解释器只检测存在性，不执行。

## 验证结果

以下均在本轮执行；PYTHONDONTWRITEBYTECODE=1 用于避免生成字节码。

```bash
# 工作目录 apps/research
 env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t .
# Ran 25 tests; OK; rc=0

# 工作目录 apps/sync
 env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t . -v
# Ran 11 tests; OK（无 skip）; rc=0

# 工作目录仓根
 env PYTHONDONTWRITEBYTECODE=1 VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor
# doctor 结论：通过；组件 health/requires 全绿；rc=0
 env PYTHONDONTWRITEBYTECODE=1 python3 apps/research/bilibili.py --help
# rc=0
 git diff --check -- apps/research/ apps/sync/
# rc=0
 git diff --check
# rc=0
```

首次全仓 diff 检查曾发现其他写域的行尾空白，已通知父智能体；再次检查时全仓已干净，F3 未越域修复。

## 边界

不保证解除 B 站风控；只修复正常分页耗尽行为，不将失败批次改成部分成功。sync 验证覆盖依赖守卫与配置指引，不宣称基础 python3 已具备完整音频同步依赖或执行过同步算法。
