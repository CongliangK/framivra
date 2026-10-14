# install_windows.ps1 —— Windows 原生一行安装引导(可审计小脚本,零业务逻辑)
# 用法(PowerShell):
#   powershell -NoProfile -ExecutionPolicy Bypass -Command "iwr -UseBasicParsing <raw-url>/scripts/install_windows.ps1 -OutFile $env:TEMP\vt-i.ps1; & $env:TEMP\vt-i.ps1"
# 无人值守:引导脚本末尾追加参数 --non-interactive(透传给 install.py)。
# 交互向导:本脚本先保存后执行,stdin 即控制台,install.py 的 isatty 判定
#   天然成立,逐项提问由 install.py 的 input() 承担(无需 /dev/tty 重挂);
#   本脚本自身的 winget Python 引导保持 --silent(在 Python 就位之前,
#   install.py 尚未运行,问题面归属引导层)。
# 信任边界:本脚本经 HTTPS 从 GitHub raw 下载——首次安装信任的是「下载通道
#   + 仓库当前内容」,不自称独立签名验证(#41 keyless provenance 为后续正式
#   发布增强项;验证器缺失时正式安装门 fail-closed,不降级)。
param(
  # install.py 的 raw 下载地址(候选期钉 commit SHA;正式发布切 Releases 资产)
  [string]$InstallPyUrl = "",
  # 透传给 install.py 的全部参数(如 "-NonInteractiveFlag --non-interactive --dry-run")
  [string[]]$InstallArgs = @()
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

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
    $py = Find-Python
  }
  if (-not $py) {
    throw '[bootstrap] 无可用 Python:请安装 Python 3.9+(winget install Python.Python.3.12 或 python.org 下载)后重跑本命令'
  }
}

if (-not $InstallPyUrl) { throw '[bootstrap] 缺少 -InstallPyUrl(候选期应钉 commit SHA 的 raw 地址)' }
$target = Join-Path $env:TEMP 'vt-install.py'
Invoke-WebRequest -UseBasicParsing -Uri $InstallPyUrl -OutFile $target
Write-Host "[bootstrap] 已下载 install.py($([math]::Round((Get-Item $target).Length/1KB,1)) KB),启动安装器..."
# 同上:单元素候选($py=@('python'))时无守卫切片会带出 $null 杂散参数。
$pyArgs = if ($py.Length -gt 1) { $py[1..($py.Length - 1)] } else { @() }
& $py[0] $pyArgs $target @InstallArgs
exit $LASTEXITCODE
