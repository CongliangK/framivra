# Native Windows PowerShell 5.1+; source channel is official Git/HTTPS.
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$InstallerArgs = @())
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Test-ElevatedWindows {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { return $false }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    try {
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } finally { $identity.Dispose() }
}

function Refresh-ProcessPath {
    # Re-read after a previous installer ran in this same, otherwise stale shell.
    $parts = @([Environment]::GetEnvironmentVariable('Path', 'Machine'),
               [Environment]::GetEnvironmentVariable('Path', 'User'), $env:Path)
    $env:Path = ($parts | Where-Object { $_ } | Select-Object -Unique) -join ';'
}

function Test-Python([string]$Executable, [string[]]$Prefix = @()) {
    try {
        # File existence, Windows Store aliases and winget exit codes are not proof.
        $result = & $Executable @Prefix -I -c 'import sys; print(sys.executable) if sys.version_info >= (3,9) and sys.maxsize > 2**32 else sys.exit(1)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $result) {
            $actual = [string]($result | Select-Object -Last 1)
            if (Test-Path -LiteralPath $actual -PathType Leaf) { return $actual }
        }
    } catch { }
    return $null
}

function Find-Python {
    Refresh-ProcessPath
    foreach ($name in @('py', 'python', 'python3')) {
        foreach ($cmd in @(Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue)) {
            $prefix = @()
            if ($name -eq 'py') { $prefix = @('-3') }
            $actual = Test-Python $cmd.Source $prefix
            if ($actual) { return $actual }
        }
    }
    # PEP 514 registration is authoritative even when this shell's PATH is old.
    foreach ($base in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore',
                        'HKLM:\Software\WOW6432Node\Python\PythonCore')) {
        foreach ($version in @(Get-ChildItem -LiteralPath $base -ErrorAction SilentlyContinue)) {
            $key = Join-Path $version.PSPath 'InstallPath'
            if (Test-Path -LiteralPath $key) {
                $properties = Get-ItemProperty -LiteralPath $key
                $candidates = @($properties.ExecutablePath)
                $directory = (Get-Item -LiteralPath $key).GetValue('')
                if ($directory) { $candidates += (Join-Path $directory 'python.exe') }
                foreach ($candidate in $candidates) {
                    if ($candidate) {
                        $actual = Test-Python $candidate
                        if ($actual) { return $actual }
                    }
                }
            }
        }
    }
    # Includes the exact per-user install location; never accept it without -c.
    foreach ($base in @($env:LOCALAPPDATA, $env:ProgramFiles)) {
        if (-not $base) { continue }
        $parent = $base
        if ($base -eq $env:LOCALAPPDATA) { $parent = Join-Path $base 'Programs\Python' }
        foreach ($directory in @(Get-ChildItem -LiteralPath $parent -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue)) {
            $actual = Test-Python (Join-Path $directory.FullName 'python.exe')
            if ($actual) { return $actual }
        }
    }
    return $null
}

function Assert-Physical([string]$Path) {
    $current = [IO.Path]::GetFullPath($Path)
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Integrity failure: reparse path $current" }
        }
        $parent = [IO.Path]::GetDirectoryName($current)
        if ($parent -eq $current) { break }
        $current = $parent
    }
}

function New-Request([string]$Url, [bool]$Domestic = $false) {
    $uri = [Uri]$Url
    if ($uri.Scheme -ne 'https') { throw 'Integrity failure: HTTPS required' }
    $request = [Net.HttpWebRequest]::Create($uri)
    $request.UserAgent = 'Framivra-bootstrap/1'
    $request.Timeout = 15000
    $request.ReadWriteTimeout = 30000
    $request.AllowAutoRedirect = $false
    if ($Domestic) { $request.Proxy = $null }
    else {
        $proxyUrl = $env:https_proxy
        if (-not $proxyUrl) { $proxyUrl = $env:HTTPS_PROXY }
        if (-not $proxyUrl) { $proxyUrl = $env:http_proxy }
        if (-not $proxyUrl) { $proxyUrl = $env:HTTP_PROXY }
        if ($proxyUrl) { $request.Proxy = New-Object Net.WebProxy($proxyUrl) }
    }
    return $request
}

function Get-SmallText([string]$Url) {
    $request = New-Request $Url
    $response = $null; $stream = $null; $memory = $null
    try {
        $response = $request.GetResponse()
        if ([int]$response.StatusCode -ne 200) { throw 'Integrity failure: unexpected metadata response' }
        $stream = $response.GetResponseStream()
        $memory = New-Object IO.MemoryStream
        $buffer = New-Object byte[] 16384
        while (($count = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            if ($memory.Length + $count -gt 2097152) { throw 'Integrity failure: oversized installer metadata' }
            $memory.Write($buffer, 0, $count)
        }
        return [Text.Encoding]::UTF8.GetString($memory.ToArray())
    } finally {
        if ($stream) { $stream.Dispose() }; if ($memory) { $memory.Dispose() }; if ($response) { $response.Dispose() }
    }
}

function Write-ResumeState([string]$Path, $State) {
    Assert-Physical $Path
    $temporary = $Path + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    Assert-Physical $temporary
    try {
        [IO.File]::WriteAllText($temporary, ($State | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
        if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($temporary, $Path, [NullString]::Value) }
        else { [IO.File]::Move($temporary, $Path) }
    } finally {
        if (Test-Path -LiteralPath $temporary) {
            Assert-Physical $temporary
            Remove-Item -LiteralPath $temporary
        }
    }
}

function Get-LockedPython([string]$Cache, [string]$Network) {
    $sha = '5da012101ba2e17113a9a1cd901e9ce1441086f2b7ecb5de06ec94163899a190'
    $size = 29941280L
    Assert-Physical $Cache
    [IO.Directory]::CreateDirectory($Cache) | Out-Null
    $lockPath = Join-Path $Cache ($sha + '.lock')
    Assert-Physical $lockPath
    try { $lock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) }
    catch { throw 'Another Python download is active or its cache is inaccessible; resume after it exits.' }
    try {
    $destination = Join-Path $Cache ($sha + '-python-3.13.17-amd64.exe')
    $part = $destination + '.part'; $metadata = $part + '.json'
    foreach ($path in @($destination, $part, $metadata)) { Assert-Physical $path }
    if (Test-Path -LiteralPath $destination) {
        if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sha) {
            throw 'Integrity failure: cached Python SHA256 mismatch; inspect cache before retrying'
        }
        return $destination
    }
    $urls = @('https://www.python.org/ftp/python/3.13.17/python-3.13.17-amd64.exe')
    if ($Network -eq 'cn') {
        $urls = @('https://mirrors.huaweicloud.com/python/3.13.17/python-3.13.17-amd64.exe',
                  'https://cdn.npmmirror.com/binaries/python/3.13.17/python-3.13.17-amd64.exe') + $urls
    }
    $state = $null
    if (Test-Path -LiteralPath $metadata) {
        try { $state = Get-Content -LiteralPath $metadata -Raw | ConvertFrom-Json } catch { throw 'Integrity failure: invalid Python resume record' }
        # Continue the previous accepted source before probing a different mirror.
        if ($urls -contains $state.url) { $urls = @($state.url) + @($urls | Where-Object { $_ -ne $state.url }) }
    }
    foreach ($url in $urls) {
        # Retry this source once; partial bytes remain available on interruption.
        for ($attempt = 0; $attempt -lt 2; $attempt++) {
            $response = $null; $incoming = $null; $output = $null
            try {
                $offset = 0L
                if ((Test-Path -LiteralPath $part) -and $state -and $state.url -eq $url -and $state.validator) {
                    $offset = (Get-Item -LiteralPath $part).Length
                }
                if ($offset -eq $size) {
                    if ((Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sha) { throw 'Integrity failure: resumed Python SHA256 mismatch' }
                    [IO.File]::Move($part, $destination)
                    return $destination
                }
                if ($offset -gt $size) { throw 'Integrity failure: oversized partial Python installer' }
                $domestic = ([Uri]$url).Host -ne 'www.python.org'
                $request = New-Request $url $domestic
                if ($offset -gt 0) {
                    $request.AddRange($offset)
                    $request.Headers.Add('If-Range', [string]$state.validator)
                }
                Write-Host "[bootstrap] Python download $url (resume $offset bytes)"
                $response = $request.GetResponse()
                $status = [int]$response.StatusCode
                if ($status -eq 206) {
                    $range = [string]$response.Headers['Content-Range']
                    if ($offset -eq 0 -or $range -notmatch ('^bytes ' + $offset + '-[0-9]+/' + $size + '$')) { throw 'Integrity failure: invalid Python Content-Range' }
                    $mode = [IO.FileMode]::Append
                } elseif ($status -eq 200) {
                    $offset = 0L; $mode = [IO.FileMode]::Create
                } elseif ($status -ge 300 -and $status -lt 400) {
                    throw 'Integrity failure: unexpected Python redirect'
                } else { throw "Python HTTP status $status" }
                $type = [string]$response.ContentType
                if ($type -match 'text/html|application/json') { throw 'Mirror returned HTML/JSON instead of Python exe' }
                $validator = [string]$response.Headers['ETag']
                if (-not $validator -or $validator.StartsWith('W/')) { $validator = [string]$response.Headers['Last-Modified'] }
                if ($status -eq 206 -and $validator -ne $state.validator) { throw 'Integrity failure: Python range validator changed or disappeared' }
                $state = @{ url = $url; validator = $validator }
                Write-ResumeState $metadata $state
                $output = New-Object IO.FileStream($part, $mode, [IO.FileAccess]::Write, [IO.FileShare]::None)
                $incoming = $response.GetResponseStream()
                $buffer = New-Object byte[] 131072
                $received = $offset
                while (($count = $incoming.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    $received += $count
                    if ($received -gt $size) { throw 'Integrity failure: oversized Python download' }
                    $output.Write($buffer, 0, $count)
                }
                $output.Dispose(); $output = $null
                if ($received -ne $size) { throw "Incomplete Python download: $received of $size bytes" }
                if ((Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sha) { throw 'Integrity failure: Python SHA256 mismatch' }
                [IO.File]::Move($part, $destination)
                return $destination
            } catch {
                if ($_.Exception.Message -match 'Integrity failure|trust relationship|certificate|SSL/TLS') { throw }
                Write-Warning ("Python transfer failed: " + $_.Exception.Message)
            } finally {
                if ($incoming) { $incoming.Dispose() }; if ($output) { $output.Dispose() }; if ($response) { $response.Dispose() }
            }
        }
    }
    throw 'Python download unavailable; partial cache retained. Rerun to resume.'
    } finally { $lock.Dispose() }
}

function Get-InstallerOption([string]$Name, [string]$Default) {
    for ($i = 0; $i -lt $InstallerArgs.Count; $i++) {
        if ($InstallerArgs[$i] -eq $Name) {
            if ($i + 1 -ge $InstallerArgs.Count) { throw "Missing value for $Name" }
            return $InstallerArgs[$i + 1]
        }
        if ($InstallerArgs[$i].StartsWith($Name + '=')) { return $InstallerArgs[$i].Substring($Name.Length + 1) }
    }
    return $Default
}

function Invoke-Bootstrap {
    if ($InstallerArgs -contains '--help') {
        Write-Output 'Usage: .\bootstrap.ps1 [--yes] [--plan] [--no-system] [--update] [--network cn|official]'
        Write-Output 'Native Windows x64; private installation, resumable original packages. Native validation pending.'
        return
    }
    $network = Get-InstallerOption '--network' 'cn'
    $root = Get-InstallerOption '--root' (Join-Path $env:LOCALAPPDATA 'framivra')
    if ($network -notin @('cn', 'official')) { throw '--network must be cn or official' }
    if (-not ($InstallerArgs -contains '--plan') -and (Test-ElevatedWindows)) {
        throw 'Per-user Windows installation requires a non-administrator PowerShell; rerun from an ordinary PowerShell. Existing files and ACLs were not changed.'
    }
    $python = Find-Python
    if (-not $python) {
        if (($InstallerArgs -contains '--plan') -or ($InstallerArgs -contains '--no-system')) { throw 'Python x64 >=3.9 is missing; no changes made.' }
        if (-not ($InstallerArgs -contains '--yes')) {
            if ((Read-Host 'Download verified user-level Python 3.13.17 and prepare Framivra? [y/N]') -notmatch '^(y|yes)$') { throw 'Cancelled' }
        }
        $installer = Get-LockedPython (Join-Path $root 'cache\downloads') $network
        $process = Start-Process -FilePath $installer -ArgumentList '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_test=0', 'Include_launcher=1', 'InstallLauncherAllUsers=0' -Wait -PassThru
        $installExit = $process.ExitCode
        # Even nonzero installers may have completed earlier; never equate an exit
        # code (including winget's no-upgrade code) with interpreter availability.
        $python = Find-Python
        if (-not $python) { throw "Python installation exit $installExit; no working x64 Python >=3.9 discovered. Resume after resolving installation." }
        if ($installExit -notin @(0, 3010)) { Write-Warning "Python installer exit $installExit; the newly discovered interpreter passed the real version/architecture probe." }
    }
    if ($InstallerArgs -contains '--plan') {
        Write-Output 'Read-only bootstrap plan: same-commit installer bundle; source, native original packages, PyPI and pinned fonts. No network or files changed.'
        return
    }
    # Resolve main once, then fetch every module from precisely this commit.
    $commit = (Get-SmallText 'https://api.github.com/repos/CongliangK/framivra/commits/main' | ConvertFrom-Json).sha
    if ($commit -notmatch '^[0-9a-f]{40}$') { throw 'Invalid official installer commit' }
    $bundle = Join-Path ([IO.Path]::GetTempPath()) ('framivra-source-' + [guid]::NewGuid().ToString('N'))
    Assert-Physical $bundle
    [IO.Directory]::CreateDirectory($bundle) | Out-Null
    try {
        foreach ($name in @('install_source.py', 'install_downloads.py', 'install_native_windows.py', 'install_native_posix.py', 'install_runtime_fonts.py', 'demo_fonts.py', 'install_python_packages.py')) {
            $text = Get-SmallText "https://raw.githubusercontent.com/CongliangK/framivra/$commit/scripts/$name"
            [IO.File]::WriteAllText((Join-Path $bundle $name), $text, (New-Object Text.UTF8Encoding($false)))
        }
        $arguments = @($InstallerArgs)
        if ((Get-InstallerOption '--ref' 'main') -eq 'main' -and -not (Get-InstallerOption '--source-commit' '')) {
            $arguments += @('--source-commit', $commit)
        }
        & $python (Join-Path $bundle 'install_source.py') @arguments
        if ($LASTEXITCODE -ne 0) { throw "Framivra installation failed: $LASTEXITCODE (not registered ready); rerun to resume verified downloads." }
    } finally {
        # Only our unique temporary bundle, never an installation tree/cache.
        Assert-Physical $bundle
        if (Test-Path -LiteralPath $bundle) { Remove-Item -LiteralPath $bundle -Recurse -Force }
    }
}

# Dot-sourcing exposes functions to the native PowerShell regression tests.
if ($MyInvocation.InvocationName -ne '.') { Invoke-Bootstrap }
