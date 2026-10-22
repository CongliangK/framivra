# SECURITY.md —— 安全策略

## 支持版本

本项目处于活跃单线开发状态，仅对最新发布版本（见仓根 `VERSION`）提供安全修复支持。请先升级到最新版本再报告问题。

## 信任边界与威胁模型

### 已防御面

- 不可信 timeline 的资产字段不得将写入引向允许根之外：路径收口见 [`ensure_inside_project`](vtx/pipeline/infra.py)，穿越、绝对路径及符号链接用例见 [`test_timeline_escape_safety.py`](tests/safety/test_timeline_escape_safety.py)。这不禁止正常的项目外素材读取，也不表示任意输入内容均安全。
- external 插件默认 disabled，须显式启用；启用即信任其执行代码，非沙箱隔离。契约见 [ARCHITECTURE §2.4](docs/ARCHITECTURE.md)，实现见 [`pluginlib/registry.py`](vtx/pluginlib/registry.py)。
- 保留清理执行器身份钉扎（[`hygiene.py`](vtx/cache/hygiene.py) / [`fstxn.remove_pinned`](vtx/runtime/fstxn.py)）与渲染窗口输入身份复核（[`cache_state.verify_input_identity`](vtx/cache/cache_state.py) / [`infra.py`](vtx/pipeline/infra.py)）。这些检查针对相应计划或输入身份变化，不承诺消除所有竞态。
- [`fstxn.py`](vtx/runtime/fstxn.py) 保留进程内锁、可用平台上的跨进程 flock 与临时文件原子替换；fd 锚定写依平台能力启用，能力不足时显式降级。锁约束协作写者，原子替换不等于恶意同用户进程隔离，也不承诺断电持久性；平台边界以该模块说明为准。

### 声明接受面

运行模型为单机普通用户，项目及本地 cache/out/回执目录由当前用户控制；不把本程序当作多租户、高权限守护进程或不可信代码沙箱。同用户级攻击者若已可改写这些目录或执行代码，本可直接修改产物；对其本地并发置换文件、目录或链接的攻击不提供隔离保证。共享不可信可写目录或提权运行不在此模型内。

这项本地边界不授予任意远程 HTML 或插件输入信任。HTML/JavaScript 会在浏览器中执行，插件会在本机执行；运行前应确认其来源与内容可信。timeline 路径校验不是 HTML 内容审计、网络访问隔离或插件沙箱；项目 HTML 渲染能力与既有普通输入校验均保留。

### 边界决定

依据 2026-10-14 裁决（[#97](https://github.com/CongliangK/framivra/issues/97)），本地 TOCTOU 类加固增量冻结，存量防线及对抗测试保留；新提议须先论证适用于上述威胁模型。[#59 的处置说明](https://github.com/CongliangK/framivra/issues/59#issuecomment-6167806735) 将本地竞态/预置链接加固与普通输入健壮性分开：该单已关闭，不据此重新开启冻结工作，也不将空串、目录 src、半途快照或错误提示等普通健壮性问题排除出维护范围。

## 漏洞报告渠道

**唯一渠道：GitHub 私密漏洞报告（private vulnerability reporting）**。请使用本仓库 GitHub 页面 Security 标签下的 "Report a vulnerability" 提交，不要公开开 issue。

报告时请尽量包含：影响面（哪条命令 / 哪个阶段）、最小复现步骤、涉及的环境（Python / ffmpeg / Chrome 版本）。

## 响应预期

- 确认收到：一般在 3 个工作日内。
- 评估与修复：视严重程度，通常在 14 天内给出评估结论与修复计划（严重问题优先）。
- 修复发布后会在发布说明中致谢报告者（除非报告者要求匿名）。

请勿在修复发布前公开披露未处理的漏洞细节。
