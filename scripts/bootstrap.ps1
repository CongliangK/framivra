# Native Windows PowerShell 5.1+ bootstrap; no Bash/WSL required.
# Source channel: official Git/HTTPS, not signed-release provenance.
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$InstallerArgs)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Find-Python {
    foreach ($name in @('py', 'python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $prefix = @()
        if ($name -eq 'py') { $prefix = @('-3') }
        $result = & $cmd.Source @prefix -c 'import sys; print(sys.executable) if sys.version_info >= (3,9) else sys.exit(1)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $result) {
            return [string]($result | Select-Object -Last 1)
        }
    }
    return $null
}

if ($InstallerArgs -contains '--help') {
    Write-Output 'Usage: .\bootstrap.ps1 [--yes] [--plan] [--no-system] [--update]'
    Write-Output 'Installs non-AI/CDP under %LOCALAPPDATA%\framivra. Native Windows x64; real-machine validation pending.'
    exit 0
}
$python = Find-Python
if (-not $python) {
    if (($InstallerArgs -contains '--plan') -or ($InstallerArgs -contains '--no-system')) {
        throw 'Python >=3.9 is missing; no changes made. Install Python before --plan/--no-system.'
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'Install App Installer (winget) and Python >=3.9, then rerun. Restricted/offline corporate installations require administrator preparation.'
    }
    if (-not ($InstallerArgs -contains '--yes')) {
        if ((Read-Host 'Install user-level Python 3.13 with winget? [y/N]') -notmatch '^(y|yes)$') { throw 'Cancelled' }
    }
    & winget install --id Python.Python.3.13 -e --scope user --accept-source-agreements --accept-package-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) { throw "Python installation failed: $LASTEXITCODE" }
    # Refresh current process PATH; never modify user/system PATH ourselves.
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    $python = Find-Python
    if (-not $python) {
        $candidate = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { $python = $candidate }
    }
    if (-not $python) { throw 'Python installed but is not discoverable. Reopen PowerShell and rerun.' }
}
$script = Join-Path ([IO.Path]::GetTempPath()) ('framivra-source-' + [guid]::NewGuid().ToString('N') + '.py')
try {
    Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/CongliangK/framivra/main/scripts/install_source.py' -OutFile $script
    & $python $script @InstallerArgs
    if ($LASTEXITCODE -ne 0) { throw "Framivra installation failed: $LASTEXITCODE (not registered ready)" }
} finally {
    # Only remove the unique bootstrap file created above, never an install tree.
    if (Test-Path -LiteralPath $script -PathType Leaf) { Remove-Item -LiteralPath $script }
}
