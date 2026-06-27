@echo off
setlocal
set "BACKTEST_CMD_SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $path=$env:BACKTEST_CMD_SELF; $content=Get-Content -Raw -LiteralPath $path; $parts=$content -split '(?m)^# POWERSHELL_START\s*$',2; if($parts.Count -lt 2){throw 'PowerShell payload missing.'}; Invoke-Expression $parts[1]"
set "EXITCODE=%ERRORLEVEL%"
endlocal & exit /b %EXITCODE%

# POWERSHELL_START
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$root = Split-Path -Parent $env:BACKTEST_CMD_SELF
$serverFile = Join-Path $root "backtest\server.py"
$port = 5000
$hostName = "127.0.0.1"
$frontendUrl = "http://${hostName}:$port"
$healthUrl = "$frontendUrl/api/health"
$runId = [guid]::NewGuid().ToString("N")
$runtimeRoot = Join-Path ([System.IO.Path]::GetTempPath()) "BacktestDeskShortcut"
$logDir = Join-Path $runtimeRoot "logs"
$chromeProfile = Join-Path $runtimeRoot ("chrome-" + $runId)
$serverOut = Join-Path $logDir "server.out.log"
$serverErr = Join-Path $logDir "server.err.log"
$script:serverProcess = $null
$script:cleanupDone = $false

function Write-Step {
    param([string]$Message)
    Write-Host ("[Backtest Desk] " + $Message)
}

function Stop-ProcessTree {
    param([int]$ProcessId)
    if ($ProcessId -le 0 -or $ProcessId -eq $PID) {
        return
    }
    try {
        $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue
        foreach ($child in $children) {
            Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
        }
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    } catch {
    }
}

function Get-ProcessesByCommandLineText {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) {
        return @()
    }
    try {
        return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $_.CommandLine -and $_.CommandLine.IndexOf($Text, [StringComparison]::OrdinalIgnoreCase) -ge 0
        })
    } catch {
        return @()
    }
}

function Stop-ProcessesByCommandLineText {
    param(
        [string]$Text,
        [int[]]$ExcludeIds = @()
    )
    foreach ($item in Get-ProcessesByCommandLineText -Text $Text) {
        $id = [int]$item.ProcessId
        if ($ExcludeIds -contains $id -or $id -eq $PID) {
            continue
        }
        Stop-ProcessTree -ProcessId $id
    }
}

function Get-PortProcesses {
    param([int]$LocalPort)
    try {
        $ids = @(Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique)
        return @($ids | ForEach-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue } | Where-Object { $_ })
    } catch {
        return @()
    }
}

function Stop-PythonLikePortProcesses {
    param([int]$LocalPort)
    $allowedNames = @("python", "pythonw", "py", "cmd", "powershell", "pwsh")
    foreach ($process in Get-PortProcesses -LocalPort $LocalPort) {
        if ($process.Id -eq $PID) {
            continue
        }
        if ($allowedNames -contains $process.ProcessName.ToLowerInvariant()) {
            Stop-ProcessTree -ProcessId ([int]$process.Id)
        }
    }
}

function Get-ChromePath {
    $command = Get-Command chrome.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidates = @(
        (Join-Path ${env:ProgramFiles} "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:LOCALAPPDATA} "Google\Chrome\Application\chrome.exe")
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }

    throw "Chrome.exe was not found. Please install Google Chrome or add it to PATH."
}

function Get-Mt5Path {
    $programFiles = [Environment]::GetEnvironmentVariable("ProgramFiles")
    $programFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    $localAppData = [Environment]::GetEnvironmentVariable("LOCALAPPDATA")
    $candidates = New-Object System.Collections.Generic.List[string]

    foreach ($base in @($programFiles, $programFilesX86)) {
        if (-not [string]::IsNullOrWhiteSpace($base)) {
            $candidates.Add((Join-Path $base "MetaTrader 5\terminal64.exe"))
            $candidates.Add((Join-Path $base "MetaTrader 5\terminal.exe"))
        }
    }

    $registryRoots = @(
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )

    foreach ($item in @(Get-ItemProperty -Path $registryRoots -ErrorAction SilentlyContinue)) {
        $displayName = [string]$item.DisplayName
        $installLocation = [string]$item.InstallLocation
        if ($displayName -match "MetaTrader|MT5" -and -not [string]::IsNullOrWhiteSpace($installLocation)) {
            $candidates.Add((Join-Path $installLocation "terminal64.exe"))
            $candidates.Add((Join-Path $installLocation "terminal.exe"))
        }
    }

    foreach ($candidate in $candidates | Select-Object -Unique) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }

    foreach ($base in @($programFiles, $programFilesX86, $localAppData)) {
        if ([string]::IsNullOrWhiteSpace($base) -or -not (Test-Path -LiteralPath $base)) {
            continue
        }
        $found = Get-ChildItem -LiteralPath $base -Filter terminal64.exe -Recurse -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($found) {
            return $found.FullName
        }
    }

    return $null
}

function Ensure-Mt5Open {
    $running = @(Get-Process -Name terminal64, terminal -ErrorAction SilentlyContinue | Where-Object {
        $path = ""
        try {
            $path = [string]$_.Path
        } catch {
        }
        $_.ProcessName -eq "terminal64" -or $path -match "MetaTrader 5|MT5"
    })

    if ($running.Count -gt 0) {
        Write-Step "MT5 is already open. It will be left running."
        return
    }

    $mt5Path = Get-Mt5Path
    if ([string]::IsNullOrWhiteSpace($mt5Path)) {
        Write-Step "MT5 executable was not found. Open MT5 manually if the app needs broker data."
        return
    }

    Write-Step "Opening MT5: $mt5Path"
    Start-Process -FilePath $mt5Path | Out-Null
}

function Get-PythonLauncher {
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        return [pscustomobject]@{
            FilePath = $python.Source
            ServerArgs = @("-m", "backtest.server")
            ProbeArgs = @("-c", "import MetaTrader5, flask, pandas, openpyxl")
            DisplayCommand = "python -m backtest.server"
            InstallCommand = "python -m pip install -r backtest\requirements.txt"
        }
    }

    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        return [pscustomobject]@{
            FilePath = $py.Source
            ServerArgs = @("-3", "-m", "backtest.server")
            ProbeArgs = @("-3", "-c", "import MetaTrader5, flask, pandas, openpyxl")
            DisplayCommand = "py -3 -m backtest.server"
            InstallCommand = "py -3 -m pip install -r backtest\requirements.txt"
        }
    }

    throw "Python was not found. Install Python and then run: python -m pip install -r backtest\requirements.txt"
}

function Test-PythonDependencies {
    $launcher = Get-PythonLauncher
    Write-Step "Checking Python requirements."
    $output = & $launcher.FilePath @($launcher.ProbeArgs) 2>&1
    if ($LASTEXITCODE -ne 0) {
        if ($output) {
            $output | ForEach-Object { Write-Host $_ }
        }
        throw "Python requirements are missing. Run this from the project folder: $($launcher.InstallCommand)"
    }
}

function Remove-ChromeProfile {
    if (-not (Test-Path -LiteralPath $chromeProfile)) {
        return
    }

    $resolvedProfile = (Resolve-Path -LiteralPath $chromeProfile -ErrorAction SilentlyContinue).Path
    $resolvedRuntime = (Resolve-Path -LiteralPath $runtimeRoot -ErrorAction SilentlyContinue).Path
    if ($resolvedProfile -and $resolvedRuntime -and $resolvedProfile.StartsWith($resolvedRuntime, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $chromeProfile -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Start-Server {
    $launcher = Get-PythonLauncher
    if (-not (Test-Path -LiteralPath $serverFile)) {
        throw "Backtest server was not found: $serverFile"
    }

    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    if (Test-Path -LiteralPath $serverOut) {
        Remove-Item -LiteralPath $serverOut -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $serverErr) {
        Remove-Item -LiteralPath $serverErr -Force -ErrorAction SilentlyContinue
    }

    Stop-ProcessesByCommandLineText -Text "backtest.server" -ExcludeIds @($PID)
    Stop-ProcessesByCommandLineText -Text $serverFile -ExcludeIds @($PID)
    Stop-PythonLikePortProcesses -LocalPort $port

    Write-Step "Starting server: $($launcher.DisplayCommand)"
    return Start-Process `
        -FilePath $launcher.FilePath `
        -ArgumentList @($launcher.ServerArgs) `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $serverOut `
        -RedirectStandardError $serverErr `
        -PassThru
}

function Wait-ForServer {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 90
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($script:serverProcess) {
            $script:serverProcess.Refresh()
            if ($script:serverProcess.HasExited) {
                return $false
            }
        }

        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return $true
            }
        } catch {
        }
        Start-Sleep -Milliseconds 700
    }

    return $false
}

function Start-IsolatedChrome {
    $chrome = Get-ChromePath

    Stop-ProcessesByCommandLineText -Text $chromeProfile -ExcludeIds @($PID)
    Remove-ChromeProfile
    New-Item -ItemType Directory -Path $chromeProfile -Force | Out-Null

    $args = @(
        "--app=$frontendUrl",
        "--user-data-dir=`"$chromeProfile`"",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble"
    )

    Write-Step "Opening Chrome: $frontendUrl"
    Start-Process -FilePath $chrome -ArgumentList $args | Out-Null
}

function Start-CleanupWatcher {
    param([int]$ServerPid)

    $configJson = @{
        ParentPid = $PID
        ServerPid = $ServerPid
        Port = $port
        ChromeProfile = $chromeProfile
        RuntimeRoot = $runtimeRoot
        ServerFile = $serverFile
        ServerModule = "backtest.server"
    } | ConvertTo-Json -Compress
    $configB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($configJson))

    $watcherSource = @"
`$ErrorActionPreference = 'SilentlyContinue'
`$config = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$configB64')) | ConvertFrom-Json
function Stop-ProcessTree {
    param([int]`$ProcessId)
    if (`$ProcessId -le 0 -or `$ProcessId -eq `$PID) { return }
    `$children = Get-CimInstance Win32_Process -Filter "ParentProcessId=`$ProcessId" -ErrorAction SilentlyContinue
    foreach (`$child in `$children) { Stop-ProcessTree -ProcessId ([int]`$child.ProcessId) }
    Stop-Process -Id `$ProcessId -Force -ErrorAction SilentlyContinue
}
function Stop-ByCommandLineText {
    param([string]`$Text)
    if ([string]::IsNullOrWhiteSpace(`$Text)) { return }
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        `$_.CommandLine -and `$_.CommandLine.IndexOf(`$Text, [StringComparison]::OrdinalIgnoreCase) -ge 0
    } | ForEach-Object {
        if ([int]`$_.ProcessId -ne `$PID -and [int]`$_.ProcessId -ne [int]`$config.ParentPid) {
            Stop-ProcessTree -ProcessId ([int]`$_.ProcessId)
        }
    }
}
function Stop-PortProcesses {
    param([int]`$LocalPort)
    `$names = @('python', 'pythonw', 'py', 'cmd', 'powershell', 'pwsh')
    Get-NetTCPConnection -LocalPort `$LocalPort -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object {
            `$process = Get-Process -Id `$_ -ErrorAction SilentlyContinue
            if (`$process -and (`$names -contains `$process.ProcessName.ToLowerInvariant())) {
                Stop-ProcessTree -ProcessId ([int]`$process.Id)
            }
        }
}
function Remove-ChromeProfile {
    `$profile = [string]`$config.ChromeProfile
    `$runtime = [string]`$config.RuntimeRoot
    if ([string]::IsNullOrWhiteSpace(`$profile) -or -not (Test-Path -LiteralPath `$profile)) { return }
    `$resolvedProfile = (Resolve-Path -LiteralPath `$profile -ErrorAction SilentlyContinue).Path
    `$resolvedRuntime = (Resolve-Path -LiteralPath `$runtime -ErrorAction SilentlyContinue).Path
    if (`$resolvedProfile -and `$resolvedRuntime -and `$resolvedProfile.StartsWith(`$resolvedRuntime, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath `$profile -Recurse -Force -ErrorAction SilentlyContinue
    }
}
while (Get-Process -Id ([int]`$config.ParentPid) -ErrorAction SilentlyContinue) {
    Start-Sleep -Seconds 1
}
Stop-ProcessTree -ProcessId ([int]`$config.ServerPid)
Stop-ByCommandLineText -Text ([string]`$config.ServerModule)
Stop-ByCommandLineText -Text ([string]`$config.ServerFile)
Stop-ByCommandLineText -Text ([string]`$config.ChromeProfile)
Stop-PortProcesses -LocalPort ([int]`$config.Port)
Start-Sleep -Seconds 1
Remove-ChromeProfile
"@

    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($watcherSource))
    $ps = (Get-Command powershell.exe -ErrorAction Stop).Source
    Start-Process -FilePath $ps -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $encoded) -WindowStyle Hidden | Out-Null
}

function Stop-AllStartedStuff {
    if ($script:cleanupDone) {
        return
    }
    $script:cleanupDone = $true

    Write-Step "Cleanup: closing Chrome app window, server, and port $port. MT5 will remain open."
    if ($script:serverProcess) {
        Stop-ProcessTree -ProcessId ([int]$script:serverProcess.Id)
    }
    Stop-ProcessesByCommandLineText -Text "backtest.server" -ExcludeIds @($PID)
    Stop-ProcessesByCommandLineText -Text $serverFile -ExcludeIds @($PID)
    Stop-ProcessesByCommandLineText -Text $chromeProfile -ExcludeIds @($PID)
    Stop-PythonLikePortProcesses -LocalPort $port
    Start-Sleep -Milliseconds 500
    Remove-ChromeProfile
}

try {
    Clear-Host
    Write-Step "Project folder: $root"
    Set-Location -LiteralPath $root

    Ensure-Mt5Open
    Test-PythonDependencies

    $script:serverProcess = Start-Server
    Start-CleanupWatcher -ServerPid ([int]$script:serverProcess.Id)

    if (-not (Wait-ForServer -Url $healthUrl -TimeoutSeconds 90)) {
        Write-Step "Server did not become ready. Last error log lines:"
        if (Test-Path -LiteralPath $serverErr) {
            Get-Content -LiteralPath $serverErr -Tail 60 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
        }
        throw "Server failed to start on $frontendUrl"
    }

    Start-IsolatedChrome
    Write-Host ""
    Write-Step "Ready: $frontendUrl"
    Write-Step "Close this CMD window to stop the server and close only this Chrome app window."
    Write-Step "MT5 will stay open."
    Write-Host ""

    while ($true) {
        $script:serverProcess.Refresh()
        if ($script:serverProcess.HasExited) {
            throw "The server process stopped. Check log: $serverErr"
        }
        Start-Sleep -Seconds 1
    }
} catch {
    Write-Host ""
    Write-Host ("[Backtest Desk] Error: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host ""
    Write-Host "This window will close in 8 seconds..."
    Start-Sleep -Seconds 8
    exit 1
} finally {
    Stop-AllStartedStuff
}
