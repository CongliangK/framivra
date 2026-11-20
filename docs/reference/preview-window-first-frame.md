# Fixed-wheel Launcher 私有首帧候选

这是 Linux [固定 wheel launcher](../../scripts/README.md#按职责查找) 的可选宿主协议，不是普通 `vtx` 命令。调用方须独立探测并取得当前 owner 对可撤销静图的授权；不与暖 SVG、全节目音频或自动续窗组合。能力证明本身不是发行、项目写入、缓存复用或 A/V 播放授权。

- `LAUNCHER --first-frame-capabilities`：独立 JSON 探测，不读工程。`schema` 为 `framivra.preview-window.first-frame/v1`，`available:true` 需要既有输入证明 ABI、首帧 ABI 及 Linux 窗口可用；另返 `launcher`、`wheel_sha256`、`runtime_projection_sha256` 和 `record_payload_limit_exclusive`。不保证某工程或请求会通过。
- `LAUNCHER -t /absolute/timeline.json timeline preview-window-first-frame --frame N --expected-content-version=sha256:HEX --render --include-media --prepare-inputs --nonce=HEX32`：有写态，仅在原 Bubblewrap 私有 tmpfs 写入。同一窗口、同一渲染、同一操作占位；调用方为每次请求生成新鲜的 128 位小写 hex nonce。拒绝额外参数、输出路径和相交的配置目录；旧 capability 探测与 v1 命令/输出不变。

## 分阶段 stdout

每条记录为 1 字节类型 + 4 字节大端无符号 JSON 字节长度 + 严格 ASCII JSON `{ "nonce": "…", "result": {…} }`，无换行。单条 JSON 长度必须满足 `0 < length < 8 MiB`；最多一条 `F`（可撤销静图），以及恰一条 `W`（终端窗口结果），其后只有 EOF。立即持续排空 stdout，写方拒绝超时的接收端。stderr 不是证明。调用方应核对 nonce、已配置的固定轮身份和各自期望的版本及窗口；不猜帧边界，不把裸字节、EOF 或首图视为成功。

- `F.result` 由外部受信 supervisor 构造，含 `schema`、`provisional:true`、固定轮身份、`content_version`、绝对整数 `frame`、左闭右开 `window`/首 `segment`、有理 `fps`、`width`/`height`、`mime:image/png`、`byte_length`、`sha256` 与 `base64`。SHA-256 是外发 PNG 字节的裸小写 hex；PNG 只保留 RGB8 所需块，无附属元数据。不含 input epoch、音频、A/V 成功或持久化引用。
- `W.result` 成功时是既有结算后的 `framivra.timeline.preview-window/v1` 窗口结果（包括 `input_epoch` 和固定轮身份）。**仅**完整且身份匹配的 `F`、`W.ok:true`、正确 EOF、进程退出码 0 同时满足才算操作成功；只有成功的窗口结果可继承既有 A/V 资格。失败、短包、余料、取消、变化、无可信结算或异常退出都须撤掉静图；不得仅凭首图或进程失联提前释放操作占位，不并发派第二个 render，不以静图当 A/V。成功结算前，`F` 独立可显示，不依赖 worker 正常退出。

## 核验边界

同一固定轮内的 namespace PID 1 在原窗口生产者的已封存首个合成段（真解码及精确呈现时钟）上提取，不另作静帧渲染。输入、实现与有效环境做前后复核；受控 PID 1 复用原 bwrap stdout，渲染子工具不继承该写端，没有新 socket/control、目录或父 fd。外部 supervisor 不透传 worker 宣称的票据：先核对私有帧及 nonce，独立算轮内首段位置、零呈现位置预期、精确版本、尺寸、PNG CRC/解压及 SHA，再重验 pinned wheel/projection、全绑定文件字节、mount 与窗口身份；只有这些检查成功才自行签发 `F`。末端仍要等待原 A/V 探测、清理、worker 完整结算、绑定 epoch 证明与 launcher 重验；早期和末端观察的环境须一致。

前后核验是同一绑定集的观察，不是原子快照或任意时刻新鲜性证明。可信计算基包括已核准 wheel 提取代码、Launcher、系统/隔离与媒体工具；不证明可在这些组件被攻陷时仍可靠。不以私有测点的用时替代真实 Launcher 边界测量；本候选动态验收未在实现轮执行。
