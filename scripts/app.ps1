[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status', 'build')]
    [string]$Action = 'status',

    [Parameter(Position = 1)]
    [ValidateSet('all', 'backend', 'frontend')]
    [string]$Target = 'all',

    [ValidateSet('local', 'ecs')]
    [string]$Environment = 'local',

    [string]$EcsHost = $(if ($env:APP_ECS_HOST) { $env:APP_ECS_HOST } else { 'aliyun-ecs' }),
    [string]$EcsRoot = $(if ($env:APP_ECS_ROOT) { $env:APP_ECS_ROOT } else { '/opt/webapp' }),
    [string]$ServiceName = $(if ($env:APP_SERVICE) { $env:APP_SERVICE } else { 'webapp' }),
    [int]$BackendPort = 8001,
    [int]$FrontendPort = 5173,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Split-Path -Parent $PSScriptRoot)
$RuntimeDir = if ($env:APP_RUNTIME_DIR) { [IO.Path]::GetFullPath($env:APP_RUNTIME_DIR) } else { Join-Path $RepoRoot '.runtime' }

function Fail([string]$Message, [int]$Code = 2) {
    [Console]::Error.WriteLine("ERROR: $Message")
    exit $Code
}

if ($Action -eq 'build' -and $Target -ne 'frontend') {
    Fail "build only supports target 'frontend'."
}

function Resolve-Python311 {
    $candidates = [System.Collections.Generic.List[string]]::new()
    if ($env:APP_PYTHON) { $candidates.Add($env:APP_PYTHON) }
    $candidates.Add((Join-Path $RepoRoot '.venv\Scripts\python.exe'))
    $pathPython = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pathPython) { $candidates.Add($pathPython.Source) }

    $workBuddyRoot = Join-Path $env:USERPROFILE '.workbuddy\binaries\python\versions'
    if (Test-Path -LiteralPath $workBuddyRoot) {
        Get-ChildItem -LiteralPath $workBuddyRoot -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { $candidates.Add((Join-Path $_.FullName 'python.exe')) }
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        & $candidate -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
        if ($LASTEXITCODE -eq 0) { return $candidate }
    }
    throw "Python 3.11+ not found. Create .venv or set APP_PYTHON to python.exe."
}

function Resolve-Node {
    $command = Get-Command node.exe -ErrorAction SilentlyContinue
    if (-not $command) { throw "Node.js not found in PATH." }
    return $command.Source
}

function Resolve-Pnpm {
    $command = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
    if (-not $command) { $command = Get-Command pnpm -ErrorAction SilentlyContinue }
    if (-not $command) { throw "pnpm not found in PATH." }
    return $command.Source
}

function Resolve-Ssh {
    $command = Get-Command ssh.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }

    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($git) {
        $candidate = [IO.Path]::GetFullPath((Join-Path (Split-Path $git.Source -Parent) '..\usr\bin\ssh.exe'))
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    throw "ssh.exe not found. Install OpenSSH or Git for Windows."
}

function Get-PidPath([string]$Name) { Join-Path $RuntimeDir "$Name.pid" }
function Get-MetaPath([string]$Name) { Join-Path $RuntimeDir "$Name.meta.json" }
function Get-StdoutPath([string]$Name) { Join-Path $RuntimeDir "$Name.stdout.log" }
function Get-StderrPath([string]$Name) { Join-Path $RuntimeDir "$Name.stderr.log" }

function Get-ServiceProcess([string]$Name) {
    $pidPath = Get-PidPath $Name
    if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) { return $null }
    $rawPid = (Get-Content -LiteralPath $pidPath -Raw -ErrorAction SilentlyContinue).Trim()
    $parsedPid = 0
    if (-not [int]::TryParse($rawPid, [ref]$parsedPid)) { return $null }
    return Get-Process -Id $parsedPid -ErrorAction SilentlyContinue
}

function Test-OwnedProcess([string]$Name, $ProcessInfo) {
    if (-not $ProcessInfo) { return $false }
    $metaPath = Get-MetaPath $Name
    if (-not (Test-Path -LiteralPath $metaPath -PathType Leaf)) { return $false }
    try {
        $meta = Get-Content -LiteralPath $metaPath -Raw | ConvertFrom-Json
        $expectedName = if ($Name -eq 'backend') { 'python' } else { 'node' }
        return [int]$meta.pid -eq $ProcessInfo.Id -and
            [int64]$meta.startTimeUtcTicks -eq $ProcessInfo.StartTime.ToUniversalTime().Ticks -and
            [string]$meta.processName -eq $expectedName -and
            [string]$meta.repoRoot -eq $RepoRoot -and
            [string]$meta.service -eq $Name
    } catch {
        return $false
    }
}

function Remove-StalePid([string]$Name) {
    $pidPath = Get-PidPath $Name
    if (Test-Path -LiteralPath $pidPath) { Remove-Item -LiteralPath $pidPath -Force }
    $metaPath = Get-MetaPath $Name
    if (Test-Path -LiteralPath $metaPath) { Remove-Item -LiteralPath $metaPath -Force }
}

function Test-Http([string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -Method Get -TimeoutSec 2 -UseBasicParsing
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
    } catch {
        return $false
    }
}

function Test-PortOpen([int]$Port) {
    $client = [Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync('127.0.0.1', $Port)
        return $task.Wait(300) -and $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Wait-Http([string]$Url, [int]$Seconds = 20) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        if (Test-Http $Url) { return $true }
        Start-Sleep -Milliseconds 300
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}

function Start-LocalService([string]$Name) {
    if ($DryRun) {
        Write-Host "DRY-RUN local start $Name" -ForegroundColor DarkGray
        return
    }

    $existing = Get-ServiceProcess $Name
    if (Test-OwnedProcess $Name $existing) {
        Write-Host "$Name already running (PID $($existing.Id))."
        return
    }
    if ($existing) {
        throw "PID file for $Name points to another process (PID $($existing.Id)); refusing to overwrite it."
    }
    Remove-StalePid $Name
    New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
    $stdinPath = Join-Path $RuntimeDir 'empty.stdin'
    if (-not (Test-Path -LiteralPath $stdinPath)) {
        [IO.File]::WriteAllBytes($stdinPath, [byte[]]@())
    }

    if ($Name -eq 'backend') {
        $executable = Resolve-Python311
        $arguments = @(
            '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1',
            '--port', [string]$BackendPort, '--app-dir', $RepoRoot
        )
        $healthUrl = "http://127.0.0.1:$BackendPort/api/health"
        $servicePort = $BackendPort
    } else {
        $executable = Resolve-Node
        $vite = Join-Path $RepoRoot 'frontend\node_modules\vite\bin\vite.js'
        if (-not (Test-Path -LiteralPath $vite -PathType Leaf)) {
            throw "Vite is not installed. Run 'pnpm install' in frontend first."
        }
        $arguments = @($vite, '--host', '127.0.0.1', '--port', [string]$FrontendPort, '--strictPort')
        $healthUrl = "http://127.0.0.1:$FrontendPort/"
        $servicePort = $FrontendPort
    }

    if (Test-PortOpen $servicePort) {
        throw "Port $servicePort is already in use by a process not managed by this project."
    }

    $process = Start-Process -FilePath $executable -ArgumentList $arguments `
        -WorkingDirectory $(if ($Name -eq 'backend') { $RepoRoot } else { Join-Path $RepoRoot 'frontend' }) `
        -RedirectStandardInput $stdinPath `
        -RedirectStandardOutput (Get-StdoutPath $Name) `
        -RedirectStandardError (Get-StderrPath $Name) `
        -WindowStyle Hidden -PassThru
    Set-Content -LiteralPath (Get-PidPath $Name) -Value $process.Id -Encoding ascii
    @{
        pid = $process.Id
        startTimeUtcTicks = $process.StartTime.ToUniversalTime().Ticks
        processName = $process.ProcessName
        repoRoot = $RepoRoot
        service = $Name
    } | ConvertTo-Json | Set-Content -LiteralPath (Get-MetaPath $Name) -Encoding utf8

    if (-not (Wait-Http $healthUrl) -or $process.HasExited -or -not (Test-OwnedProcess $Name (Get-ServiceProcess $Name))) {
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
        Remove-StalePid $Name
        throw "$Name failed its health check at $healthUrl. See $(Get-StderrPath $Name)."
    }
    Write-Host "$Name started (PID $($process.Id)): $healthUrl" -ForegroundColor Green
}

function Stop-LocalService([string]$Name) {
    if ($DryRun) {
        Write-Host "DRY-RUN local stop $Name" -ForegroundColor DarkGray
        return
    }

    $processInfo = Get-ServiceProcess $Name
    if (-not $processInfo) {
        Remove-StalePid $Name
        Write-Host "$Name already stopped."
        return
    }
    if (-not (Test-OwnedProcess $Name $processInfo)) {
        throw "PID $($processInfo.Id) is not an owned $Name process; refusing to stop it."
    }
    Stop-Process -Id $processInfo.Id -Force
    Wait-Process -Id $processInfo.Id -Timeout 10 -ErrorAction SilentlyContinue
    Remove-StalePid $Name
    Write-Host "$Name stopped."
}

function Get-LocalStatus([string]$Name) {
    if ($DryRun) {
        Write-Host "DRY-RUN local status $Name" -ForegroundColor DarkGray
        return $true
    }

    $processInfo = Get-ServiceProcess $Name
    if (-not (Test-OwnedProcess $Name $processInfo)) {
        $pidText = if ($processInfo) { "PID $($processInfo.Id) is not owned" } else { 'no live PID' }
        Write-Host "$Name STOPPED ($pidText)" -ForegroundColor Red
        return $false
    }
    $url = if ($Name -eq 'backend') {
        "http://127.0.0.1:$BackendPort/api/health"
    } else {
        "http://127.0.0.1:$FrontendPort/"
    }
    if (-not (Test-Http $url)) {
        Write-Host "$Name UNHEALTHY (PID $($processInfo.Id), $url)" -ForegroundColor Yellow
        return $false
    }
    Write-Host "$Name RUNNING (PID $($processInfo.Id), $url)" -ForegroundColor Green
    return $true
}

function Build-LocalFrontend {
    if ($DryRun) {
        Write-Host "DRY-RUN local build frontend: pnpm build" -ForegroundColor DarkGray
        return
    }
    $pnpm = Resolve-Pnpm
    Push-Location (Join-Path $RepoRoot 'frontend')
    try {
        & $pnpm build
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed with exit code $LASTEXITCODE." }
    } finally {
        Pop-Location
    }
}

function Invoke-Local {
    if ($Action -eq 'build') { Build-LocalFrontend; return }

    $targets = if ($Target -eq 'all') { @('backend', 'frontend') } else { @($Target) }
    if ($Action -eq 'status') {
        $ok = $true
        foreach ($name in $targets) { if (-not (Get-LocalStatus $name)) { $ok = $false } }
        if (-not $ok) { exit 1 }
        return
    }
    if ($Action -eq 'stop') {
        foreach ($name in ($targets | Sort-Object { if ($_ -eq 'frontend') { 0 } else { 1 } })) {
            Stop-LocalService $name
        }
        return
    }
    if ($Action -eq 'start') {
        foreach ($name in $targets) { Start-LocalService $name }
        return
    }
    if ($Action -eq 'restart') {
        foreach ($name in ($targets | Sort-Object { if ($_ -eq 'frontend') { 0 } else { 1 } })) {
            Stop-LocalService $name
        }
        foreach ($name in $targets) { Start-LocalService $name }
    }
}

function Invoke-Ecs {
    if ($EcsHost -notmatch '^[A-Za-z0-9_.@-]+$') { Fail 'EcsHost contains unsupported characters.' }
    if ($EcsRoot -notmatch '^/[A-Za-z0-9._/-]+$') { Fail 'EcsRoot must be a safe absolute Linux path.' }
    if ($ServiceName -notmatch '^[A-Za-z0-9_.@-]+$') { Fail 'ServiceName contains unsupported characters.' }

    $remote = "cd '$EcsRoot' && APP_ROOT='$EcsRoot' APP_SERVICE='$ServiceName' bash scripts/app.sh '$Action' '$Target'"
    if ($DryRun) {
        Write-Host "DRY-RUN ecs $EcsHost $EcsRoot $Action $Target"
        Write-Host "ssh -o BatchMode=yes -o ConnectTimeout=10 $EcsHost $remote"
        return
    }
    $ssh = Resolve-Ssh
    & $ssh '-o' 'BatchMode=yes' '-o' 'ConnectTimeout=10' $EcsHost $remote
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

try {
    if ($Environment -eq 'ecs') { Invoke-Ecs } else { Invoke-Local }
} catch {
    Fail $_.Exception.Message 1
}
