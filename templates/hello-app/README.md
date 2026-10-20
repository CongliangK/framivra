# hello-app —— 最小应用（app）范本

> v2 插件化架构的第三方 app 起手模板（ARCHITECTURE §4.2 / §0.2 / §3.1）。

## 用途

演示一个 app 的**合法最小形态**：app = 产出**独立素材**、不进 timeline 管线、
报错不影响渲染主线的可卸载组件（关停测试：关掉它，主链照样可信出片）。
本例的 `hello` 子命令落一个素材文件并打印路径——就是「app 产出素材」的最小模拟。

## 依赖

- Python ≥ 3.9（纯标准库，无外部二进制/包——manifest `requires` 全空数组）。

## 健康检查

```
env python3 templates/hello-app/health_check.py     # exit 0 = 健康
```

`app.json` 的 `health.type=command` 指向本脚本；doctor 经子进程探测（零跨目录
import），enabled 件的探针失败计入 doctor FAIL 门。

## 开启步骤（external 注册，默认 disabled）

templates/ **不在 registry 内置扫描范围**（内置只扫 `apps/` 与 `plugins/`），
须走 config.local.json 的 external 注册（v2 §2.4 安全节，文件 gitignored 不入库）：

1. 编辑 `config.local.json` 的 `external_apps`（模板见 `config.example.json`）：
   ```json
   "external_apps": [
     {"path": "templates/hello-app", "enabled": true}
   ]
   ```
   只写字符串 `"templates/hello-app"` = 注册但**默认 disabled**（安全节）。
2. `env VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor`——②组件段出现 `[app:external] hello-app`，
   enabled 后 health ✓ / requires ✓。
3. `env python3 vt.py apps list`——external 条目在列。

## 如何被 doctor 发现

发现三路（`vtx/pluginlib/registry.py`）：①内置扫描 `apps/`+`plugins/`（不含
templates/）；②config 的 `external_apps[]`/`external_plugins[]`——**本范本走的
就是这条路**（默认 disabled，显式 enable 才生效）；③`plugins/registry.json`
静态注册表（仅官方插件）。external 条目 resolve 后做白名单根校验：manifest
与 health 命令必须落在注册目录内，路径含 `..` 一律拒绝。

## 复制本目录改名起步

```bash
cp -r templates/hello-app templates/my-app && cd templates/my-app
# 1) 改名：目录名与 app.json 的 "name" 必须一致（^[a-z][a-z0-9-]*$）
# 2) 换实现：cli.py 的子命令换成你的素材产线；health_check.py 换成依赖自检
# 3) 补依赖：app.json 的 requires.binaries / python_packages / platforms
# 4) 注册：config.local.json 的 external_apps 加 {"path": "templates/my-app"}
# 5) 验证：env VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor && env python3 vt.py apps list
```

纪律红线（§3.1）：app 自包含（manifest+代码+README+tests 随目录走）、零
sys.path hack、不 import vtx 内部模块（需要内核能力走 `vtx.api` 公共面）。
