"""PowerShell bootstrap regressions; native execution requires PowerShell.

The native tests only use fixtures and the current Python. No HTTP/install runs.
"""
import http.server
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

BOOTSTRAP = Path(__file__).resolve().parents[2] / 'scripts/bootstrap.ps1'
POWERSHELL = shutil.which('powershell') or shutil.which('pwsh')


class BootstrapBundleContract(unittest.TestCase):
    def test_standalone_bundle_is_one_resolved_commit_and_retains_ref_semantics(self):
        text = BOOTSTRAP.read_text(encoding='utf-8')
        for name in ('install_source.py', 'install_downloads.py', 'install_native_windows.py',
                     'install_native_posix.py', 'install_runtime_fonts.py', 'demo_fonts.py', 'install_python_packages.py'):
            self.assertIn("'" + name + "'", text)
        self.assertIn('framivra/$commit/scripts/$name', text)
        self.assertIn("@('--source-commit', $commit)", text)
        self.assertNotIn("@('--ref', $commit)", text)
        self.assertNotIn('winget install', text)
        self.assertIn('$python = Find-Python\n        if (-not $python)', text)


@unittest.skipUnless(POWERSHELL, 'UNRUN: native PowerShell is not available')
class BootstrapPowerShell(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='framivra-ps-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def run_ps(self, body):
        script = self.base / 'test.ps1'
        prefix = ("$ErrorActionPreference = 'Stop'\n. $env:FRAMIVRA_TEST_BOOTSTRAP\n"
                  "function Test-ElevatedWindows { return $false }\n")
        script.write_text(prefix + body, encoding='utf-8-sig')
        env = dict(os.environ, FRAMIVRA_TEST_BOOTSTRAP=str(BOOTSTRAP),
                   FRAMIVRA_TEST_PYTHON=sys.executable, FRAMIVRA_TEST_TEMP=str(self.base),
                   LOCALAPPDATA=str(self.base / 'local-appdata'))
        result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                env=env, text=True, capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_parser_and_real_interpreter_probe(self):
        self.run_ps('''
$tokens = $null; $errors = $null
[System.Management.Automation.Language.Parser]::ParseFile($env:FRAMIVRA_TEST_BOOTSTRAP, [ref]$tokens, [ref]$errors) | Out-Null
if ($errors.Count) { throw ($errors | Out-String) }
if (-not (Test-Python $env:FRAMIVRA_TEST_PYTHON)) { throw 'Real Python was not validated' }
if (Test-Python (Join-Path $env:FRAMIVRA_TEST_TEMP 'nonexistent.exe')) { throw 'Accepted nonexistent Python' }
$statePath = Join-Path $env:FRAMIVRA_TEST_TEMP 'resume.json'
Write-ResumeState $statePath @{ validator = 'first' }
Write-ResumeState $statePath @{ validator = 'second' }
if ((Get-Content $statePath -Raw | ConvertFrom-Json).validator -ne 'second') { throw 'Atomic resume-state replacement failed' }
''')

    def test_elevated_write_stops_before_python_probe_network_or_root_creation(self):
        self.run_ps('''
$script:InstallerArgs = @('--yes')
function Test-ElevatedWindows { return $true }
function Find-Python { throw 'Python must not be probed in the rejected context' }
function Get-LockedPython { throw 'No download is permitted' }
function Get-SmallText { throw 'No bundle network is permitted' }
$failed = $false
try { Invoke-Bootstrap } catch {
    if ($_.Exception.Message -notmatch 'non-administrator PowerShell') { throw }
    $failed = $true
}
if (-not $failed) { throw 'Elevated write was accepted' }
if (Test-Path -LiteralPath $env:LOCALAPPDATA) { throw 'Rejected install created a root' }
''')

    def test_elevated_plan_remains_read_only(self):
        self.run_ps('''
$script:InstallerArgs = @('--plan')
function Test-ElevatedWindows { throw 'Read-only plan does not need elevation detection' }
function Find-Python { return $env:FRAMIVRA_TEST_PYTHON }
function Get-SmallText { throw 'Read-only plan must not access the network' }
Invoke-Bootstrap
if (Test-Path -LiteralPath $env:LOCALAPPDATA) { throw 'Plan created a root' }
''')

    def test_nonzero_installer_is_not_failure_if_real_python_is_now_usable(self):
        self.run_ps('''
$script:InstallerArgs = @('--yes')
$script:calls = 0
function Find-Python {
    $script:calls++
    if ($script:calls -gt 1) { return (Test-Python $env:FRAMIVRA_TEST_PYTHON) }
    return $null
}
function Get-LockedPython { return 'fixture-installer-never-executed.exe' }
function Start-Process { return @{ExitCode = -1978335189} }
function Get-SmallText([string]$Url) {
    if ($Url -match '/commits/main$') { return '{"sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}' }
    return 'import sys; assert "--source-commit" in sys.argv'
}
Invoke-Bootstrap
if ($script:calls -ne 2) { throw 'Installer result was not followed by rediscovery' }
''')

    def test_nonzero_without_working_python_still_fails(self):
        self.run_ps('''
$script:InstallerArgs = @('--yes')
function Find-Python { return $null }
function Get-LockedPython { return 'fixture-installer-never-executed.exe' }
function Start-Process { return @{ExitCode = -1978335189} }
$failed = $false
try { Invoke-Bootstrap } catch {
    if ($_.Exception.Message -notmatch 'no working x64 Python') { throw }
    $failed = $true
}
if (-not $failed) { throw 'Nonzero installer without Python incorrectly succeeded' }
''')

    def test_known_location_uses_a_real_probe_instead_of_existence(self):
        self.run_ps('''
$env:LOCALAPPDATA = $env:FRAMIVRA_TEST_TEMP
$env:ProgramFiles = ''
$directory = Join-Path $env:LOCALAPPDATA 'Programs\\Python\\Python313'
[IO.Directory]::CreateDirectory($directory) | Out-Null
$exe = Join-Path $directory 'python.exe'
[IO.File]::WriteAllText($exe, 'not an executable')
function Get-Command { return @() }
function Get-ChildItem {
    param([string]$LiteralPath, [switch]$Directory, [string]$Filter, [string]$ErrorAction)
    if ($LiteralPath -like 'HK*') { return @() }
    Microsoft.PowerShell.Management\\Get-ChildItem @PSBoundParameters
}
if (Find-Python) { throw 'Known-path file accepted without execution/version validation' }
$script:probed = $false
function Test-Python([string]$Executable, [string[]]$Prefix) {
    if ($Executable -eq $exe) { $script:probed = $true; return $env:FRAMIVRA_TEST_PYTHON }
    return $null
}
if ((Find-Python) -ne $env:FRAMIVRA_TEST_PYTHON -or -not $script:probed) { throw 'Known path was not probed' }
''')

    def test_changed_range_validator_stops_before_any_append(self):
        received = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                received.append((self.headers.get('Range'), self.headers.get('If-Range')))
                self.send_response(206)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Range', 'bytes 5-29941279/29941280')
                self.send_header('Content-Length', '0')
                self.send_header('ETag', '"changed"')
                self.end_headers()
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        endpoint = 'http://127.0.0.1:' + str(server.server_address[1])
        self.run_ps('''
$cache = Join-Path $env:FRAMIVRA_TEST_TEMP 'range-cache'
[IO.Directory]::CreateDirectory($cache) | Out-Null
$name = '5da012101ba2e17113a9a1cd901e9ce1441086f2b7ecb5de06ec94163899a190-python-3.13.17-amd64.exe'
$part = Join-Path $cache ($name + '.part')
[IO.File]::WriteAllText($part, 'first')
@{ url = 'https://www.python.org/ftp/python/3.13.17/python-3.13.17-amd64.exe'; validator = '"old"' } | ConvertTo-Json | Set-Content ($part + '.json')
function New-Request {
    # Loopback transport injection only: production still requires HTTPS.
    $request = [Net.HttpWebRequest]::Create('__ENDPOINT__')
    $request.Proxy = $null
    $request.Timeout = 3000; $request.ReadWriteTimeout = 3000
    return $request
}
$failed = $false
try { Get-LockedPython $cache 'official' } catch {
    if ($_.Exception.Message -notmatch 'Integrity failure: Python range validator changed') { throw }
    $failed = $true
}
if (-not $failed) { throw 'Changed validator was accepted' }
if ([IO.File]::ReadAllText($part) -ne 'first') { throw 'Changed representation was appended' }
'''.replace('__ENDPOINT__', endpoint))
        self.assertEqual(received, [('bytes=5-', '"old"')])

    def test_corrupt_cache_is_a_hard_stop_without_http(self):
        self.run_ps('''
$cache = Join-Path $env:FRAMIVRA_TEST_TEMP 'cache'
[IO.Directory]::CreateDirectory($cache) | Out-Null
$name = '5da012101ba2e17113a9a1cd901e9ce1441086f2b7ecb5de06ec94163899a190-python-3.13.17-amd64.exe'
[IO.File]::WriteAllText((Join-Path $cache $name), 'corrupt fixture')
function New-Request { throw 'HTTP must not be reached after integrity failure' }
$failed = $false
try { Get-LockedPython $cache 'cn' } catch {
    if ($_.Exception.Message -notmatch 'Integrity failure: cached Python SHA256 mismatch') { throw }
    $failed = $true
}
if (-not $failed) { throw 'Corrupt cache was accepted' }
''')


if __name__ == '__main__':
    unittest.main()
