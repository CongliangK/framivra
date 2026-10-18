# install_windows.ps1 —— Windows 一键安装引导(克隆/安全更新仓库 + 运行 vt.py init)
# 设计理念对齐 ibus-voice-ime 的 remote-install.sh:一键安装=快速初始化手段
# (克隆或安全更新仓库,再跑仓内既有幂等 init),不替代手动克隆(README「快速
# 开始」的 clone 路径始终可用),不依赖发版资产(Releases)与 git 以外的工具
# (不要求 gh CLI)。
# 用法(PowerShell;本脚本先保存后执行,stdin 即控制台,不吞交互):
#   powershell -NoProfile -ExecutionPolicy Bypass -Command "iwr -UseBasicParsing https://raw.githubusercontent.com/CongliangK/framivra/main/scripts/install_windows.ps1 -OutFile $env:TEMP\vt-i.ps1; & $env:TEMP\vt-i.ps1"
# 透传参数:脚本名后追加的参数原样交给 vt.py init(短横线开头须加引号),如
#   & $env:TEMP\vt-i.ps1 '--fix'                 # init --fix 自动装缺失基础依赖
#   & $env:TEMP\vt-i.ps1 -SkipUpdate '--json'    # 跳过更新 + init 机器可读输出
# 重复运行=安全更新:克隆干净(git status --porcelain 为空)时 fetch+reset
# --hard origin/<ref>;克隆变脏(含未跟踪文件)时拒绝自动更新、绝不静默丢弃
# 改动(可加 -SkipUpdate 跳过更新直接复用现有克隆)。上游新版本把本地被忽略
# 文件的同名路径转为跟踪文件(config.local.json 是必然场景)时,porcelain 判
# 脏看不见,碰撞预检同样拒绝更新,防 reset --hard 静默覆盖用户文件。
# 环境变量(空值/全空白/控制字符/短横线开头一律拒绝;空白仅 FRAMIVRA_DIR
# 放行——目录路径含空格合法,其余变量仍拒):
#   FRAMIVRA_DIR  默认 %LOCALAPPDATA%\framivra-repo(克隆目录;须为绝对路径,
#                 任一路径段不得以点或空格结尾;不得使用运行时数据根
#                 %LOCALAPPDATA%\framivra——安装运行数据会让克隆变脏,
#                 下一次引导拒绝自动更新)
#   FRAMIVRA_REPO 默认 https://github.com/CongliangK/framivra.git(可信镜像也可)
#   FRAMIVRA_REF  默认 stable;可选 vX.Y.Z / vX.Y.Z-rc.N / main(开发版)
# 信任边界:本脚本经 HTTPS 从 GitHub raw 下载——首次安装信任的是「下载通道 +
# 仓库当前内容」;发布者身份验证(keyless provenance,#41)随发版通道后续接入,
# 引导层不自称签名验证(可先取脚本审读后再执行)。
param(
  # 跳过更新检查,直接复用现有克隆(脏克隆也放行使用,不改动其内容)
  [switch]$SkipUpdate,
  # 其余参数原样透传给 vt.py init(如 '--fix' '--json')
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$InitArgs = @()
)
$ErrorActionPreference = 'Stop'

function Find-Python {
  foreach ($candidate in @(@('py', '-3'), @('python'))) {
    try {
      # 单元素候选(@('python'))时 1..0 是降序区间 @(1,0),无守卫切片会带出
      # $null 杂散参数——必须先判长度(PowerShell 越界切片语义确定;
      # hosted Windows job 实测执行)。
      $probeArgs = if ($candidate.Length -gt 1) { $candidate[1..($candidate.Length - 1)] } else { @() }
      $version = & $candidate[0] $probeArgs --version 2>$null
      if ($LASTEXITCODE -eq 0 -and $version -match 'Python 3\.(\d+)') {
        # 防 Microsoft Store stub:stub 的 --version 非零或无输出,已被上式过滤。
        if ([int]$Matches[1] -ge 9) { return $candidate }
      }
    } catch { continue }
  }
  return $null
}

$py = Find-Python
if (-not $py) {
  Write-Host '[bootstrap] 未找到 Python >=3.9,尝试 winget 用户级安装(免管理员)...'
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    winget install --id Python.Python.3.12 -e --scope user --silent
    if ($LASTEXITCODE -ne 0) { throw '[bootstrap] winget 安装 Python 失败;请手动安装 Python 3.12+ 后重试(https://python.org)' }
    # winget 用户级安装只写注册表 PATH,当前会话不自动可见;合并机器+用户
    # PATH 刷新本进程后复测(新终端天然生效)。
    $env:PATH = [Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('PATH', 'User')
    $py = Find-Python
  }
  if (-not $py) {
    throw '[bootstrap] 无可用 Python:请安装 Python 3.9+(winget install Python.Python.3.12 或 python.org 下载,新开终端后)重跑本命令'
  }
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Write-Host '[bootstrap] 未找到 git,尝试 winget 用户级安装 Git.Git(免管理员)...'
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    winget install --id Git.Git -e --scope user --silent
    if ($LASTEXITCODE -ne 0) { throw '[bootstrap] winget 安装 Git 失败;请手动安装 Git(https://git-scm.com)后重跑本命令' }
    $env:PATH = [Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('PATH', 'User')
  }
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw '[bootstrap] 无可用 git:请手动安装 Git(winget install Git.Git 或 https://git-scm.com 下载,新开终端生效后)重跑本命令'
  }
}

# PS 5.1 坑:EAP=Stop 下原生命令 stderr 一旦被重定向(2>$null)会升级为终止错误
# (NativeCommandError),故探查型(-Quiet)调用须临时降 EAP;成败一律以
# $LASTEXITCODE 判定(PS 5.1 不对原生命令的非零退出自动抛错)。
function Invoke-Git {
  param([string[]]$GitArgs, [switch]$Quiet)
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'SilentlyContinue'
  $out = $null
  try {
    if ($Quiet) { $out = & git @GitArgs 2>$null }
    else { & git @GitArgs | Out-Host }
  } finally { $ErrorActionPreference = $prev }
  [pscustomobject]@{ Output = $out; Code = $LASTEXITCODE }
}

# ignored→tracked 碰撞预检(数据丢失防线,移植自标杆 remote-install.sh 的
# 同名防线):porcelain 判脏看不见 ignored 文件——上游新版本把原 ignored 路径
# (必然场景:vt.py init 生成的 config.local.json,以及 out/、frames/、*.mp4
# 等被 .gitignore 忽略的产物)转为 tracked 时 porcelain 仍为空,reset --hard
# 会静默覆盖用户文件。在 fetch 之后、reset 之前比对「远端目标 commit 将跟踪
# 的路径集」(ls-tree)与「本地被忽略文件集」(ls-files -o -i
# --exclude-standard):完全同名,或目录前缀碰撞(ignored 文件 a 对 incoming
# 目录 a/x,或反之)都算冲突。特殊字符路径在非 -z 输出下由 git 统一做 C 风格
# 引号转义且一行一径,两侧遵循同一转义规则与同一仓库配置,按引号形态比对
# 语义等价;预检自身任何故障(git 非零退出)一律 fail-closed 拒绝更新。
function Assert-NoIgnoredCollision {
  param([string]$TargetRef)
  $tree = Invoke-Git -Quiet @('-C', $Dir, 'ls-tree', '-r', '--name-only', $TargetRef)
  if ($tree.Code -ne 0) { throw '[bootstrap] ignored 碰撞预检失败(ls-tree;fail-closed),拒绝自动更新。' }
  $files = Invoke-Git -Quiet @('-C', $Dir, 'ls-files', '-o', '-i', '--exclude-standard')
  if ($files.Code -ne 0) { throw '[bootstrap] ignored 碰撞预检失败(ls-files;fail-closed),拒绝自动更新。' }
  $incoming = @{}
  foreach ($line in @($tree.Output)) { if ($line) { $incoming["$line"] = $true } }
  $hits = @()
  foreach ($line in @($files.Output)) {
    if (-not $line) { continue }
    $clash = $incoming.ContainsKey("$line")
    if (-not $clash) {
      # ignored 文件的任一父目录将变成 tracked 文件(目录→文件):同一路径
      # 不能既是用户文件又是仓库目录,reset 同样会动用户数据。
      $parts = $line -split '/'
      for ($i = 1; $i -lt $parts.Count; $i++) {
        if ($incoming.ContainsKey(($parts[0..($i - 1)] -join '/'))) { $clash = $true; break }
      }
    }
    if (-not $clash) {
      # ignored 路径将是 incoming 某路径的前缀(文件→目录):同理碰撞。
      foreach ($t in $incoming.Keys) {
        if ($t.StartsWith("$line/", [StringComparison]::OrdinalIgnoreCase)) { $clash = $true; break }
      }
    }
    if ($clash) { $hits += $line }
  }
  if ($hits.Count -gt 0) {
    throw "[bootstrap] 本地被忽略的文件与新版本跟踪路径冲突,拒绝覆盖以防数据丢失: $($hits -join '; ')。处理:先把冲突文件备份到克隆目录外,删除整个克隆目录后重跑本命令(上游新版本已把上述路径转为跟踪文件,本地文件不会自动迁移);或临时改用其他 FRAMIVRA_DIR。"
  }
}

function Assert-BootstrapVar {
  # AllowSpaces:目录路径(FRAMIVRA_DIR)放行空格——PS 数组传参(@('clone',...,
  # $Dir))无 POSIX word-splitting 威胁,git/Join-Path/Push-Location 对含空格
  # 路径均正常;默认目录随 %LOCALAPPDATA%(如用户名 "John Doe")天然含空格,
  # 拒绝会让含空格用户名的机器开箱即败(红队 N1)。REPO/REF 仍拒空白。
  param([string]$Name, [string]$Value, [switch]$AllowSpaces)
  if ($Value -notmatch '\S') { throw "[bootstrap] 环境变量 $Name 非法:不得为空或全空白。" }
  if ($Value -match '\p{Cc}') { throw "[bootstrap] 环境变量 $Name 非法:不得含控制字符。" }
  if (-not $AllowSpaces -and $Value -match '\s') { throw "[bootstrap] 环境变量 $Name 非法:不得含空白。" }
  if ($Value.StartsWith('-')) { throw "[bootstrap] 环境变量 $Name 非法:不得以短横线开头。" }
}

# LOCALAPPDATA 是默认目录与数据根比对的共同前提,缺失时无论是否显式设置
# FRAMIVRA_DIR 都无法继续(数据根守卫仍需 LOCALAPPDATA 构造),指引如实。
if (-not $env:LOCALAPPDATA) { throw '[bootstrap] 环境变量 LOCALAPPDATA 未设置(默认目录与数据根比对均依赖它),请在正常 Windows 会话中重跑。' }
# 显式空值保留给校验拒绝(而非回退默认),语义对齐标杆。
$Dir = if ($null -ne $env:FRAMIVRA_DIR) { $env:FRAMIVRA_DIR } else { Join-Path $env:LOCALAPPDATA 'framivra-repo' }
$Repo = if ($null -ne $env:FRAMIVRA_REPO) { $env:FRAMIVRA_REPO } else { 'https://github.com/CongliangK/framivra.git' }
$Ref = if ($null -ne $env:FRAMIVRA_REF) { $env:FRAMIVRA_REF } else { 'stable' }
Assert-BootstrapVar 'FRAMIVRA_DIR' $Dir -AllowSpaces
Assert-BootstrapVar 'FRAMIVRA_REPO' $Repo
Assert-BootstrapVar 'FRAMIVRA_REF' $Ref
$refCheck = Invoke-Git -Quiet @('check-ref-format', '--branch', $Ref)
if ($refCheck.Code -ne 0) { throw '[bootstrap] FRAMIVRA_REF 非法:须为有效的分支名。' }
$expectedCommit = $null
if ($Ref -eq 'stable') {
  $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/CongliangK/framivra/releases/latest' -Headers @{'User-Agent'='framivra-bootstrap'}
  if ($release.draft -ne $false -or $release.prerelease -ne $false) { throw '[bootstrap] 无公开稳定发行,不会回落 main。' }
  $Ref = $release.tag_name
}
if ($Ref -ceq 'main') {
  $sourceRef = 'refs/heads/main'
  $targetRef = 'refs/remotes/origin/main'
} else {
  if ($Ref -cnotmatch '^v[0-9]+\.[0-9]+\.[0-9]+(-rc\.[1-9][0-9]*)?$') { throw '[bootstrap] FRAMIVRA_REF 非法:只接受 stable、vX.Y.Z、vX.Y.Z-rc.N 或 main。' }
  if (-not [IO.Path]::IsPathRooted($Repo)) {
    $versionRelease = Invoke-RestMethod -Uri "https://api.github.com/repos/CongliangK/framivra/releases/tags/$Ref" -Headers @{'User-Agent'='framivra-bootstrap'}
    if ($versionRelease.tag_name -cne $Ref -or $versionRelease.draft -ne $false -or $versionRelease.prerelease -ne ($Ref.Contains('-rc.'))) {
      throw '[bootstrap] 指定版本不是匹配的公开发行,不会回落 main。'
    }
  }
  $sourceRef = "refs/tags/$Ref"
  $targetRef = $sourceRef
  $remoteTag = Invoke-Git -Quiet @('ls-remote', '--exit-code', $Repo, $sourceRef, "$sourceRef^{}")
  if ($remoteTag.Code -ne 0) { throw '[bootstrap] 发行 tag 查询失败,不会回落 main。' }
  $peeled = @($remoteTag.Output | Where-Object { $_ -match '\^\{\}$' })
  if ($peeled.Count -ne 1) { throw '[bootstrap] 发行须为 annotated tag。' }
  $expectedCommit = ($peeled[0] -split '\s+')[0]
}
Write-Host "[bootstrap] 选择 $Ref @ $expectedCommit"


# 克隆目录与运行时数据根分离:install.py 语义的运行时数据根是
# %LOCALAPPDATA%\framivra,混用会让克隆在首次安装后变脏,下一次引导拒绝自动
# 更新(对齐标杆红线)。守卫必须与消费同基准:$Dir 的全部消费点(Test-Path/
# Join-Path/Push-Location/git -C)走 PowerShell 提供程序位置,而 GetFullPath
# 走进程 CWD——相对路径会让两基准分裂(守卫比对一处、克隆落盘另一处,可把
# 克隆精准落进数据根),故一律拒绝相对路径(仅接受盘符绝对路径或 UNC,盘符
# 相对 C:foo 与当盘根相对 \foo 一并拒);Win32 落盘会剥除各路径段结尾的点/
# 空格,与字符串比对语义分裂,同样拒绝。之后再双侧 GetFullPath 规范化比对
# (此时输入已是绝对路径,GetFullPath 只做词法规范化,与消费一致;PS 字符串
# -eq 天生忽略大小写,合 Windows 路径语义;./与../中缀、尾斜杠、正反斜杠
# 差异均不得绕过)。
if (($Dir -notmatch '^[A-Za-z]:[\\/]') -and ($Dir -notmatch '^\\\\')) {
  throw "[bootstrap] FRAMIVRA_DIR 须为绝对路径(如 C:\Users\<你>\framivra-repo;相对路径会让守卫比对与实际克隆位置分裂),留空则用默认 %LOCALAPPDATA%\framivra-repo。"
}
foreach ($seg in ($Dir -split '[\\/]')) {
  if ($seg -match '[. ]$') {
    throw "[bootstrap] FRAMIVRA_DIR 非法:任一组成部分不得以点或空格结尾(Win32 落盘时会剥除,守卫比对与实际目录会分裂);请改用普通目录名。"
  }
}
$dataRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'framivra')).TrimEnd('\', '/')
$dirNorm = [IO.Path]::GetFullPath($Dir).TrimEnd('\', '/')
if ($dirNorm -eq $dataRoot) {
  throw '[bootstrap] FRAMIVRA_DIR 不得使用运行时数据根 %LOCALAPPDATA%\framivra(安装运行数据会让克隆变脏,下次更新被拒);请改用其他目录(默认 framivra-repo 即可)。'
}

$markerPath = Join-Path (Join-Path $Dir '.git') 'framivra-bootstrap'
if (Test-Path -LiteralPath $Dir) {
  if ((Get-Item -LiteralPath $Dir -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "[bootstrap] $Dir 是符号链接/联接点,不会覆盖;请设置 FRAMIVRA_DIR 使用真实目录。"
  }
  if (-not (Test-Path -LiteralPath (Join-Path $Dir '.git') -PathType Container)) {
    throw "[bootstrap] $Dir 已存在但不是可独立更新的 Git 克隆(缺 .git 目录),不会覆盖;请设置 FRAMIVRA_DIR 使用其他目录。"
  }
  $origin = Invoke-Git -Quiet @('-C', $Dir, 'remote', 'get-url', 'origin')
  if ($origin.Code -ne 0) { throw "[bootstrap] 现有克隆缺少 origin 远端,不会覆盖;请检查或改用其他 FRAMIVRA_DIR。" }
  if ("$($origin.Output)".Trim() -ne $Repo) {
    throw "[bootstrap] 现有克隆的 origin($($origin.Output))与 FRAMIVRA_REPO($Repo)不匹配,不会覆盖;请检查仓库地址或改用其他 FRAMIVRA_DIR。"
  }
  if ($SkipUpdate) {
    Write-Host '[bootstrap] 已指定 -SkipUpdate:跳过更新,直接复用现有克隆。'
  } else {
    # 判脏:porcelain 任何输出(含未跟踪文件)都算脏——拒绝自动更新,不静默
    # 丢弃用户改动;本脚本只判空不解析路径,带空格/非 ASCII 文件名不误判。
    $status = Invoke-Git -Quiet @('-C', $Dir, 'status', '--porcelain', '--untracked-files=all')
    if ($status.Code -ne 0) { throw '[bootstrap] 无法检查本地变更(git status 失败),拒绝自动更新。' }
    if ($status.Output) {
      throw "[bootstrap] 现有克隆有未提交变更(含未跟踪文件),拒绝自动更新(不会静默丢弃改动);请把需要的文件备份到克隆外后清理,或删除整个克隆目录让引导器重新拉取;也可加 -SkipUpdate 跳过更新直接使用现有克隆。"
    }
    Write-Host '[bootstrap] 正在更新已有的干净克隆...'
    # 显式 refspec 支持浅克隆更新,避免 reset 到过时的远端跟踪引用;失败时
    # 尚未重置任何本地文件。
    $fetch = Invoke-Git @('-C', $Dir, 'fetch', '--depth', '1', 'origin', "+${sourceRef}:${targetRef}")
    if ($fetch.Code -ne 0) {
      Write-Host '[bootstrap] 提示:请检查网络、仓库地址与分支;或把 FRAMIVRA_REPO 指向可信镜像后重试。'
      throw '[bootstrap] 获取远端分支失败,尚未改动本地文件。'
    }
    # 标签/简写歧义防护:一律用全限定 ref,防名为 origin/<ref> 的标签劫持 reset。
    $newHead = Invoke-Git -Quiet @('-C', $Dir, 'rev-parse', '--verify', "$targetRef^{commit}")
    if ($newHead.Code -ne 0 -or -not "$($newHead.Output)".Trim()) { throw '[bootstrap] 远端分支解析失败,尚未重置本地文件。' }
    if ($expectedCommit -and "$($newHead.Output)".Trim() -cne $expectedCommit) { throw '[bootstrap] tag 在安装期间移动,拒绝更新。' }
    # ignored→tracked 碰撞预检:先于 marker 检查与 reset 执行(拒绝路径绝无
    # 副作用;fetch 失败零落盘的既有性质保持——预检只读不写)。
    Assert-NoIgnoredCollision -TargetRef $targetRef
    $headNow = Invoke-Git -Quiet @('-C', $Dir, 'rev-parse', 'HEAD')
    $headStr = "$($headNow.Output)".Trim()
    $newStr = "$($newHead.Output)".Trim()
    # 本地提交保护:HEAD 与引导器上次同步标记(.git 内 marker)不一致说明用户
    # 提交过本地变更,浅克隆无法区分落后/分叉,拒绝自动重置(reset --hard 会
    # 丢弃本地提交);克隆后从未由引导器同步过的克隆首次更新按 HEAD==远端放行。
    if ($headStr -ne $newStr) {
      $synced = (Test-Path -LiteralPath $markerPath) -and ("$(Get-Content -LiteralPath $markerPath)".Trim() -eq $headStr)
      if (-not $synced) {
        throw '[bootstrap] 克隆的 HEAD 与引导器上次同步的不一致(可能存在本地提交);为防数据丢失拒绝自动更新,请自行处理(备份后删除整个克隆目录可重来)后再试。'
      }
      $reset = Invoke-Git @('-C', $Dir, 'reset', '--hard', $targetRef)
      if ($reset.Code -ne 0) { throw '[bootstrap] 更新本地克隆失败,请检查目录权限与 Git 状态。' }
    }
    Set-Content -LiteralPath $markerPath -Value $newStr -NoNewline
  }
} else {
  Write-Host "[bootstrap] 正在克隆仓库到 $Dir..."
  $clone = Invoke-Git @('clone', '--depth', '1', '-b', $Ref, '--', $Repo, $Dir)
  if ($clone.Code -ne 0) {
    Write-Host '[bootstrap] 提示:请检查网络、仓库地址与分支;或把 FRAMIVRA_REPO 指向可信镜像后重试。'
    throw "[bootstrap] 克隆失败,安装尚未开始;若留下半成品目录($Dir),请检查后自行处理或改用其他 FRAMIVRA_DIR。"
  }
  $head = Invoke-Git -Quiet @('-C', $Dir, 'rev-parse', 'HEAD')
  if ($expectedCommit -and "$($head.Output)".Trim() -cne $expectedCommit) { throw '[bootstrap] 克隆提交与发行 tag 不符,拒绝初始化。' }
  Set-Content -LiteralPath $markerPath -Value "$($head.Output)".Trim() -NoNewline
}

if (-not $SkipUpdate) {
  $installedHead = Invoke-Git -Quiet @('-C', $Dir, 'rev-parse', 'HEAD')
  if ($installedHead.Code -ne 0) { throw '[bootstrap] 无法记录安装提交。' }
  @{ref=$Ref; commit="$($installedHead.Output)".Trim()} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Dir '.git/framivra-release.json') -Encoding UTF8
}
if (-not (Test-Path -LiteralPath (Join-Path $Dir 'vt.py'))) {
  throw '[bootstrap] 仓库缺少 vt.py:请检查 FRAMIVRA_REPO/FRAMIVRA_REF 指向的是 Framivra 本仓。'
}
Write-Host '[bootstrap] 在克隆内运行 vt.py init(依赖自检 + 幂等生成 config.local.json;额外参数已原样透传)...'
# 同上:单元素候选($py=@('python'))时无守卫切片会带出 $null 杂散参数。
$pyArgs = if ($py.Length -gt 1) { $py[1..($py.Length - 1)] } else { @() }
$code = 1
Push-Location -LiteralPath $Dir
try {
  & $py[0] $pyArgs 'vt.py' 'init' @InitArgs
  $code = $LASTEXITCODE
} finally { Pop-Location }
if ($code -eq 0) {
  Write-Host ''
  Write-Host '[bootstrap] 完成。建议新开一个 PowerShell 终端(winget 新装的 Python/Git 需新终端才进 PATH)后使用:'
  Write-Host "  cd '$Dir'"
  Write-Host '  python vt.py doctor                    # 体检;缺基础依赖可 python vt.py init --fix 自动装'
}
exit $code
