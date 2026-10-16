# 资源预算接口

本专题归属 docs 层，覆盖 [Issue #44](https://github.com/CongliangK/framivra/issues/44) 的协作式资源记账。实现与 Python API 在 [vtx/resource_budget.py](../vtx/resource_budget.py)，验收在 [tests/test_resource_budget.py](../tests/test_resource_budget.py)。

## 能力与边界

一个池统一记账 CPU、RAM、磁盘和抽象 VRAM token。四维申请全成或全败，任何已记录申请之和不超过池的配置。释放、获取下一次快照或下一次申请时，确认退出的进程占用会被回收；没有常驻服务。调用方仍需在启动工作前申请，并在工作结束后释放。

这是预算接口，不是 OS/cgroup 强制隔离，不限制未接入程序的实际消耗，也不证明真实 GPU 的可用性、吞吐或并发安全。真实 GPU 调优归 #45/#65/#120/#121。现有渲染 CLI 尚未自动接入。

运行要求为 Linux /proc、POSIX flock 和 fstxn 的 fd-anchored 写能力。缺失能力显式报 ResourceUnavailable，不降级到仅线程锁。一个池仅用于同一机器、同一次启动、同一个 PID namespace 的合作进程；不支持跨主机、网络文件系统或跨容器共享。使用本地可靠文件系统；磁盘损坏和掉电持久化不在原子替换保证内。

## 单位与配置

Resources 的四个字段均为非负整数，不接受 bool、负数、浮点数或 NaN：

- cpu_millis：1000 表示一个逻辑 CPU 的预算
- ram_bytes：RAM 字节预算
- vram_bytes：抽象显存字节 token
- disk_bytes：磁盘字节预算

配置为零的维度不可用；请求该维度时抛 ResourceUnavailable。全零请求无工作意义，抛 ValueError。库存大于零但当前已占满属于 BudgetExceeded 或等待；单次申请超出整个池的上限立即抛 BudgetExceeded。

detect_inventory(disk_path) 只读当前运行机器：CPU affinity（不可读时退回 os.cpu_count）、/proc/meminfo 的 MemAvailable、指定目录所在卷的空闲磁盘。可见的 cgroup-v2 祖先 quota/headroom 会进一步收紧 CPU/RAM。sources 记录探测来源和 unavailable 原因；不可见的容器配额不能据此假定不存在。VRAM 默认 unavailable，只有显式传入可信 vram_bytes 才建立抽象库存，不调用 GPU 程序。模拟值必须标明模拟。

这是瞬时测量，其他程序随时可能消耗资源。运行者应按实测值预留余量并明确选择上限；不能复制其他机器的核心数、内存或显存。首次 create 之后池的上限固定，其他进程应打开同一账本，不要各自反复探测并创建不同配置。

## Python 使用方式

下面是 API 调用示意，含创建/改写预算账本的写操作，不属于零写态冒烟命令。应把账本放在明确指定的持久目录中，且不位于 cache/ 或 out/，避免项目垃圾回收删除它。跨项目需要同机共享预算时，所有参与者仍必须指定同一个账本；不同账本是彼此独立的池，不提供机器级合计保证。

```python
from pathlib import Path
from vtx.resource_budget import Resources, ResourceBudget, detect_inventory

path = Path(".vtx-resource-budget/pool.json")
inventory = detect_inventory(Path.cwd())
# 由运行者选择余量；示例保留一半实测 CPU/RAM/磁盘，不编造 VRAM。
measured = inventory.limits
limits = Resources(
    cpu_millis=measured.cpu_millis // 2,
    ram_bytes=measured.ram_bytes // 2,
    vram_bytes=0,
    disk_bytes=measured.disk_bytes // 2,
)
budget = ResourceBudget.create(path, limits)  # 单一初始化者，工作进程启动前执行
# 工作进程直接 budget = ResourceBudget(path)，复用持久上限。
with budget.acquire(Resources(cpu_millis=1000), timeout=5) as lease:
    pass  # 调用者在这里执行受预算约束的工作
print(budget.snapshot())
```

create 对已存在且配置一致的池幂等，配置不同、schema 不兼容或 namespace 不同均拒绝。正常打开不自动创建，不把缺失/坏 JSON 重置成空闲池。首次初始化应在启动工作进程前完成；并发初次创建可能拒绝尚未提交的初始化。

acquire 的 timeout=0 为立即尝试，timeout=None 等待，正有限数值指定秒数。等待线程锁、跨进程锁和容量都受同一 monotonic deadline 控制；取消传入有 is_set() 的 Event。取消与超时在落账前检查，落账后在同一事务锁内发现取消或超时会回滚本次申请；成功点之后的取消由调用方负责结束工作并释放。若落账后回滚写入遭遇 I/O 故障，会抛出存储错误并保守保留占用，直到拥有进程退出或停工后的人工恢复；不把未知状态当作释放成功。锁与容量的等待可中断，文件系统调用本身不是硬实时操作，不能承诺对挂死存储的系统调用强制中断。

已经交给调用方的 Lease 不因 timeout 到期而撤销；timeout 是申请截止时间，不是工作 TTL。租约仍存活时自动超时释放会允许正在运行的工作重叠，因此不采用这种行为。调用方收到取消后，应先停止其拥有的工作，再 release；预算接口本身绝不发送进程信号。

Lease 支持上下文管理，工作抛异常也释放。重复 release 返回 False。租约属于发起申请的 PID/starttime/boot 身份，fork 子进程不能释放父进程租约。模块导入后 fork 的解释器会显式报 ResourceUnavailable，避免继承的线程锁或 flock 描述符导致死锁；工作进程必须使用 spawn、subprocess 或 exec 启动。公平排队、优先级及无饥饿承诺不在本接口范围。

## 账本、退出回收与恢复

记账复用 [fstxn.py](../vtx/fstxn.py) 的路径线程锁、fd 锚定目录、普通文件验证和原子写，以及同名 .lock flock 协议。由于通用事务 API 为无限等待，预算内部只增加非阻塞锁等待适配器来检查 deadline/Event，没有第二个工作队列或调度服务。

退出检测复用 [proc_supervisor.py](../vtx/proc_supervisor.py) 的 boot/starttime 与 /proc 解析。PID 被复用、已消失和 zombie 状态可回收；身份读取不确定时保留占用。不会扫描后终止其他程序。预算归属于申请进程：该进程必须覆盖实际工作生命周期；自行派生的工作若比申请进程活得久，需由存活的实际工作进程自己申请，不能指望预算器追踪整个进程树。

snapshot 返回独立的 limits、used、available、active_leases、reclaimed_leases；snapshot/reap 会回写确认死亡的租约，故不是零写态检查。reap 可重复调用，无需后台轮询。

账本损坏、总占用超额、重复 JSON 键、非法身份及丢失账本都 fail-closed。已有 .lock 但账本不存在时，create 拒绝恢复，避免忘记仍在运行的申请。账本绑定 .lock 的文件身份，缺失或被替换的锁不会静默重建或继续使用。不要在使用中手改账本或删除/替换账本、.lock 或父目录；它们位于受信任的协作目录，不能作为抵御同用户恶意篡改的安全边界。原子写保证进程崩溃时只见完整旧版或新版，不能自动修复恶意篡改或磁盘损坏。

人工恢复应先确认整个池的工作均已停止，再保存诊断副本并以新的账本路径重新初始化；所有参与者一起切换，旧池不得继续工作。保留 .lock；清理它会制造多个锁域。fstxn 崩溃残留的隐藏 .tmp 不参与资源计算；本接口不扫描删除任意文件，也不自动替用户清空证据。

## 验证

以下测试只使用临时目录、CPU 线程/进程与模拟显存 token，不启动 GPU 程序、不改变 OS 设置。测试子进程只在自身测试范围内退出或被终止。

```bash
env python3 -m unittest tests.test_resource_budget -v
```

上述测试命令会写临时目录，属于显式测试写操作。该 unittest 模块命令不在当前 docs_smoke 的仓路径匹配面，必须实际运行；文档门通过不代表测试已执行，更不代表真实 GPU 或全平台验收。独立测试覆盖多进程/线程竞争、四维原子性、超额/资源缺失、取消/超时、锁等待、异常退出/僵尸/PID 复用、坏状态和失败写入。全仓回归与独立评审仍是合入前提。
