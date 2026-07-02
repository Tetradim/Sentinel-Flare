# Sentinel Flare Local Source Launcher
# Starts the FastAPI backend and Vite frontend on separate localhost ports.

param(
    [int]$BackendPort = 8002,
    [int]$FrontendPort = 3002,
    [switch]$NoBrowser,
    [switch]$InstallDeps,
    [switch]$SmokeTest
)

$ErrorActionPreference = "Stop"
$ProjectRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $ProjectRoot) { $ProjectRoot = (Get-Location).Path }

$DesktopPath = [Environment]::GetFolderPath("Desktop")
if (-not $DesktopPath) { $DesktopPath = Join-Path $HOME "Desktop" }
$LogFile = Join-Path $DesktopPath "Sentinel-Flare.log"
$OwnedProcesses = New-Object System.Collections.Generic.List[System.Diagnostics.Process]
$ShutdownStarted = $false
$CancelKeyPressHandler = $null

function Write-Status {
    param([string]$Message, [string]$Level = "INFO")
    $color = switch ($Level) {
        "OK" { "Green" }
        "WARN" { "Yellow" }
        "ERROR" { "Red" }
        default { "Cyan" }
    }
    Write-Host "[$Level] $Message" -ForegroundColor $color
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    Add-Content -Path $LogFile -Value "$timestamp [$Level] $Message" -Encoding UTF8
}

function Join-ProcessArguments {
    param([string[]]$Arguments)
    return (($Arguments | ForEach-Object {
        $arg = $_
        if ([string]::IsNullOrEmpty($arg)) {
            '""'
        } elseif ($arg -match '[\s"]') {
            '"' + $arg.Replace('"', '\"') + '"'
        } else {
            $arg
        }
    }) -join " ")
}

function Test-PortOpen {
    param([int]$Port)
    try {
        $client = New-Object Net.Sockets.TcpClient
        $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $connected = $async.AsyncWaitHandle.WaitOne(750, $false)
        if ($connected) { $client.EndConnect($async) }
        $client.Close()
        return $connected
    } catch {
        return $false
    }
}

function Wait-Port {
    param([int]$Port, [int]$Seconds = 45)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-PortOpen -Port $Port) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Test-HttpOk {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300)
    } catch {
        return $false
    }
}

function Wait-HttpOk {
    param([string]$Url, [int]$Seconds = 45)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-HttpOk -Url $Url) { return $true }
        Start-Sleep -Milliseconds 750
    }
    return $false
}

function Find-CommandPath {
    param([string[]]$Names)
    foreach ($name in $Names) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    return $null
}

function Find-Python {
    foreach ($candidate in @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $ProjectRoot "venv\Scripts\python.exe")
    )) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return Find-CommandPath -Names @("python3.13.exe", "python3.12.exe", "python3.11.exe", "python.exe")
}

function Find-Npm {
    return Find-CommandPath -Names @("npm.cmd", "npm.exe", "npm")
}

function Start-OwnedProcess {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory,
        [switch]$Visible
    )
    $startParams = @{
        FilePath = $FilePath
        WorkingDirectory = $WorkingDirectory
        PassThru = $true
    }
    if ($ArgumentList -and $ArgumentList.Count -gt 0) {
        $startParams.ArgumentList = Join-ProcessArguments -Arguments $ArgumentList
    }
    if (-not $Visible) {
        $startParams.WindowStyle = "Hidden"
    }
    $process = Start-Process @startParams
    $OwnedProcesses.Add($process)
    return $process
}

function Stop-ProcessTree {
    param([int]$ProcessId)
    try {
        $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue)
        foreach ($child in $children) {
            Stop-ProcessTree -ProcessId $child.ProcessId
        }
        $current = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if ($current) {
            Write-Status "Stopping process $($current.ProcessName) ($($current.Id))"
            Stop-Process -Id $current.Id -Force -ErrorAction SilentlyContinue
        }
    } catch {
    }
}

function Stop-OwnedProcesses {
    for ($i = $OwnedProcesses.Count - 1; $i -ge 0; $i--) {
        Stop-ProcessTree -ProcessId $OwnedProcesses[$i].Id
    }
}

function Invoke-LauncherCleanup {
    if ($script:ShutdownStarted) { return }
    $script:ShutdownStarted = $true
    Stop-OwnedProcesses
}

function Register-LauncherShutdownHandlers {
    try {
        $script:CancelKeyPressHandler = [ConsoleCancelEventHandler]{
            param($sender, $eventArgs)
            $eventArgs.Cancel = $true
            Write-Status "Shutdown requested; stopping Sentinel Flare" "WARN"
            Invoke-LauncherCleanup
            exit 0
        }
        [Console]::CancelKeyPress += $script:CancelKeyPressHandler
    } catch {
    }
}

if ($SmokeTest) {
    Write-Status "Running launcher smoke test"
    $args = Join-ProcessArguments -Arguments @("run", "dev", "--", "--host", "127.0.0.1", "--port", "3002")
    if (-not $args.Contains("--port") -or -not $args.Contains("3002")) {
        throw "Frontend argument smoke test failed."
    }
    $backendArgs = Join-ProcessArguments -Arguments @("server.py")
    if (-not $backendArgs.Contains("server.py")) {
        throw "Backend argument smoke test failed."
    }
    Write-Status "Launcher smoke test passed" "OK"
    exit 0
}

Register-LauncherShutdownHandlers

try {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Sentinel Flare - Local Source" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Status "Project root: $ProjectRoot"
    Write-Status "Launcher log: $LogFile"

    if (-not (Test-Path (Join-Path $ProjectRoot "server.py"))) {
        throw "server.py not found in $ProjectRoot."
    }
    if (-not (Test-Path (Join-Path $ProjectRoot "package.json"))) {
        throw "package.json not found in $ProjectRoot."
    }

    $python = Find-Python
    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        if (-not $python) { throw "Python was not found. Install Python 3.11+ and rerun." }
        Write-Status "Creating backend virtual environment"
        & $python -m venv (Join-Path $ProjectRoot ".venv")
        $InstallDeps = $true
        $python = $venvPython
    } else {
        $python = $venvPython
    }

    if ($InstallDeps) {
        Write-Status "Installing backend dependencies"
        & $python -m pip install --upgrade pip
        & $python -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
    }

    $npm = Find-Npm
    if (-not $npm) { throw "npm was not found. Install Node.js/npm and rerun." }
    if ($InstallDeps -or -not (Test-Path (Join-Path $ProjectRoot "node_modules"))) {
        Write-Status "Installing frontend dependencies"
        Start-OwnedProcess -FilePath $npm -ArgumentList @("install") -WorkingDirectory $ProjectRoot -Visible | Wait-Process
    }

    $backendUrl = "http://127.0.0.1:$BackendPort"
    $frontendUrl = "http://127.0.0.1:$FrontendPort"
    $env:PORT = "$BackendPort"
    $env:VITE_BACKEND_URL = $backendUrl
    $env:REACT_APP_BACKEND_URL = $backendUrl

    if (-not (Test-PortOpen -Port $BackendPort)) {
        Write-Status "Starting backend on port $BackendPort"
        Start-OwnedProcess -FilePath $python -ArgumentList @("server.py") -WorkingDirectory $ProjectRoot | Out-Null
        if (-not (Wait-HttpOk -Url "$backendUrl/health" -Seconds 60)) {
            throw "Backend did not become healthy at $backendUrl/health."
        }
        Write-Status "Backend is ready" "OK"
    } else {
        Write-Status "Backend port $BackendPort is already open" "WARN"
    }

    if (-not (Test-PortOpen -Port $FrontendPort)) {
        Write-Status "Starting Vite frontend on port $FrontendPort"
        Start-OwnedProcess -FilePath $npm -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", "$FrontendPort") -WorkingDirectory $ProjectRoot | Out-Null
        if (-not (Wait-Port -Port $FrontendPort -Seconds 60)) {
            throw "Frontend did not open port $FrontendPort."
        }
        Write-Status "Frontend is ready" "OK"
    } else {
        Write-Status "Frontend port $FrontendPort is already open" "WARN"
    }

    if (-not $NoBrowser) {
        Start-Process $frontendUrl | Out-Null
    }

    Write-Host ""
    Write-Host "Ready: $frontendUrl" -ForegroundColor Green
    Write-Host "Backend: $backendUrl" -ForegroundColor Gray
    Write-Host "Close this window or press Ctrl+C to stop processes started by this launcher." -ForegroundColor Gray
    Write-Host ""

    while ($true) {
        foreach ($process in @($OwnedProcesses)) {
            if ($process.HasExited) {
                throw "Process $($process.Id) exited unexpectedly."
            }
        }
        Start-Sleep -Seconds 1
    }
} catch {
    Write-Status $_.Exception.Message "ERROR"
    exit 1
} finally {
    Invoke-LauncherCleanup
}
