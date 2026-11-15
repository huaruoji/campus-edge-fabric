<# Windows GPU setup and lifecycle. Token is read from env or hidden input. #>
param(
    [ValidateSet('Install','Check','Start','Stop','Status','Profile')][string]$Action = 'Status',
    [string]$Server = $env:CEF_SERVER,
    [string]$Name = $env:COMPUTERNAME,
    [string]$Tags = $env:COMPUTERNAME,
    [ValidateRange(1,64)][int]$Slots = 2,
    [ValidateRange(0,32)][int]$Device = 0,
    [string]$GpuUuid = $env:CEF_GPU_UUID,
    [string]$DataDir,
    [string]$VenvDir,
    [string]$Python = 'python',
    [switch]$CollectProfile
)
$ErrorActionPreference = 'Stop'
$repoDir = Split-Path $PSScriptRoot -Parent
if (-not $VenvDir) { $VenvDir = Join-Path $repoDir '.venv-gpu' }
$venvDir = [IO.Path]::GetFullPath($VenvDir)
$pythonExe = Join-Path $venvDir 'Scripts/python.exe'
$entry = Join-Path $PSScriptRoot 'gpu_worker.py'
if (-not $DataDir) { $DataDir = Join-Path $repoDir 'cef-gpu-data' }
$DataDir = [IO.Path]::GetFullPath($DataDir)

function Check-Exit { if ($LASTEXITCODE -ne 0) { throw "Command failed: exit $LASTEXITCODE" } }
$gpuArguments = @()
if ($GpuUuid) { $gpuArguments = @('--gpu-uuid', $GpuUuid) }
function Quote-Argument([string]$value) {
    # Windows CRT argument quoting, including paths ending in a backslash.
    '"' + [regex]::Replace([regex]::Replace($value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}
if ($Action -eq 'Install') {
    if (-not (Test-Path -LiteralPath $pythonExe)) { & $Python -m venv $venvDir; Check-Exit }
    & $pythonExe -m pip install --timeout 120 --retries 8 -r (Join-Path $PSScriptRoot 'gpu-requirements.txt') `
        -e (Join-Path $repoDir 'packages/cef-protocol') -e (Join-Path $repoDir 'packages/cef-worker') `
        -e (Join-Path $repoDir 'packages/cef-client')
    Check-Exit
    & $pythonExe $entry setup-model --data $DataDir
    Check-Exit
    & $pythonExe $entry check --data $DataDir --device $Device @gpuArguments
    Check-Exit
    return
}
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Run -Action Install first.' }
if ($Action -ne 'Start') {
    & $pythonExe $entry $Action.ToLowerInvariant() --data $DataDir --device $Device @gpuArguments
    Check-Exit
    return
}
if (-not $Server) { throw 'Pass -Server or set CEF_SERVER.' }
$state = & $pythonExe $entry status --data $DataDir | ConvertFrom-Json
Check-Exit
if ($state.alive) { Write-Host "Already running: PID $($state.pid)"; return }
New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
$previousToken = $env:CEF_TOKEN
$previousEncoding = $env:PYTHONIOENCODING
try {
    if (-not $env:CEF_TOKEN) {
        $secret = Read-Host 'CEF access token (hidden)' -AsSecureString
        $env:CEF_TOKEN = [System.Net.NetworkCredential]::new('', $secret).Password
    }
    $env:PYTHONIOENCODING = 'utf-8'
    $arguments = @($entry, 'run', '--data', $DataDir, '--server', $Server, '--name', $Name,
                   '--tags', $Tags, '--slots', "$Slots", '--device', "$Device")
    $arguments += $gpuArguments
    if ($CollectProfile) { $arguments += @('--profile-dir', (Join-Path $DataDir 'profiles')) }
    $commandLine = ($arguments | ForEach-Object { Quote-Argument $_ }) -join ' '
    $process = Start-Process -FilePath $pythonExe -ArgumentList $commandLine -WorkingDirectory $repoDir `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $DataDir 'stdout.log') `
        -RedirectStandardError (Join-Path $DataDir 'stderr.log') -PassThru
    $deadline = (Get-Date).AddSeconds(90)
    do {
        Start-Sleep -Milliseconds 500
        $process.Refresh()
        if ($process.HasExited) { throw "Worker exited. Check $DataDir/stderr.log" }
        $runtime = Join-Path $DataDir 'gpu-runtime.json'
        if (Test-Path -LiteralPath $runtime) {
            $state = Get-Content -LiteralPath $runtime -Raw | ConvertFrom-Json
            if ($state.ready -and $state.process_created -ge ([DateTimeOffset]$process.StartTime).ToUnixTimeSeconds()) {
                Write-Host "Registered GPU Worker $($state.worker_id), PID $($state.pid)"
                return
            }
        }
    } while ((Get-Date) -lt $deadline)
    throw "Startup has not confirmed registration yet. Check $DataDir/stderr.log or use -Action Status."
} finally {
    $env:CEF_TOKEN = $previousToken
    $env:PYTHONIOENCODING = $previousEncoding
}
