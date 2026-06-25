@echo off
setlocal
set "ALGO_CMD_SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $path=$env:ALGO_CMD_SELF; $content=Get-Content -Raw -LiteralPath $path; $parts=$content -split '(?m)^# POWERSHELL_START\s*$',2; if($parts.Count -lt 2){throw 'PowerShell payload missing.'}; Invoke-Expression $parts[1]"
set "EXITCODE=%ERRORLEVEL%"
endlocal & exit /b %EXITCODE%

# POWERSHELL_START
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$root = Split-Path -Parent $env:ALGO_CMD_SELF
$serverFile = Join-Path $root "server.mjs"
$port = 3000
$hostName = "127.0.0.1"
$frontendUrl = "http://${hostName}:$port"
$runId = [guid]::NewGuid().ToString("N")
$runtimeRoot = Join-Path ([System.IO.Path]::GetTempPath()) "AlgoDeskShortcut"
$logDir = Join-Path $runtimeRoot "logs"
$chromeProfile = Join-Path $runtimeRoot ("chrome-" + $runId)
$serverOut = Join-Path $logDir "server.out.log"
$serverErr = Join-Path $logDir "server.err.log"
$script:serverProcess = $null

function Write-Step {
    param([string]$Message)
    Write-Host ("[Algo Desk] " + $Message)
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

function Stop-NodeLikePortProcesses {
    param([int]$LocalPort)
    $allowedNames = @("node", "npm", "cmd", "powershell", "pwsh")
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

    throw "Chrome.exe was not found. Please check that Google Chrome is installed and available in PATH."
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
        $_.ProcessName -eq "terminal64" -or ([string]$_.Path -match "MetaTrader 5|MT5")
    })
    if ($running.Count -gt 0) {
        Write-Step "MT5 is already open. Leaving the existing MT5 session running."
        return
    }

    $mt5Path = Get-Mt5Path
    if ([string]::IsNullOrWhiteSpace($mt5Path)) {
        Write-Step "MT5 executable was not found. The app will continue, but you may need to open MT5 manually."
        return
    }

    Write-Step "Opening MT5: $mt5Path"
    Start-Process -FilePath $mt5Path | Out-Null
}

function Test-PythonWorker {
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $python) {
        Write-Step "Python was not found. The backend will start, but API calls may fail."
        return
    }

    $worker = Join-Path $root "python\algo_worker.py"
    try {
        $output = & $python.Source $worker status 2>$null
        if ($LASTEXITCODE -eq 0 -and $output) {
            Write-Step "Python worker is ready."
        } else {
            Write-Step "Python worker check warning. Details may be available in the server logs."
        }
    } catch {
        Write-Step "Python worker check warning: $($_.Exception.Message)"
    }
}

function Start-Server {
    $node = Get-Command node.exe -ErrorAction Stop
    if (-not (Test-Path -LiteralPath $serverFile)) {
        throw "server.mjs was not found: $serverFile"
    }

    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    if (Test-Path -LiteralPath $serverOut) {
        Remove-Item -LiteralPath $serverOut -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $serverErr) {
        Remove-Item -LiteralPath $serverErr -Force -ErrorAction SilentlyContinue
    }

    Stop-ProcessesByCommandLineText -Text $serverFile -ExcludeIds @($PID)
    Stop-NodeLikePortProcesses -LocalPort $port

    $previousHost = $env:HOST
    $previousPort = $env:PORT
    $env:HOST = $hostName
    $env:PORT = [string]$port
    try {
        Write-Step "Starting the frontend/backend server: node server.mjs"
        return Start-Process `
            -FilePath $node.Source `
            -ArgumentList @("`"$serverFile`"") `
            -WorkingDirectory $root `
            -WindowStyle Hidden `
            -RedirectStandardOutput $serverOut `
            -RedirectStandardError $serverErr `
            -PassThru
    } finally {
        if ($null -eq $previousHost) {
            Remove-Item Env:\HOST -ErrorAction SilentlyContinue
        } else {
            $env:HOST = $previousHost
        }
        if ($null -eq $previousPort) {
            Remove-Item Env:\PORT -ErrorAction SilentlyContinue
        } else {
            $env:PORT = $previousPort
        }
    }
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
    if (Test-Path -LiteralPath $chromeProfile) {
        $resolvedProfile = (Resolve-Path -LiteralPath $chromeProfile -ErrorAction SilentlyContinue).Path
        $resolvedRuntime = (Resolve-Path -LiteralPath $runtimeRoot -ErrorAction SilentlyContinue).Path
        if ($resolvedProfile -and $resolvedRuntime -and $resolvedProfile.StartsWith($resolvedRuntime, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $chromeProfile -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    New-Item -ItemType Directory -Path $chromeProfile -Force | Out-Null

    $args = @(
        "--new-window",
        "--user-data-dir=`"$chromeProfile`"",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        $frontendUrl
    )

    Write-Step "Opening a single Chrome frontend window: $frontendUrl"
    Start-Process -FilePath $chrome -ArgumentList $args | Out-Null
}

function Start-CleanupWatcher {
    param([int]$ServerPid)

    $configJson = @{
        ParentPid = $PID
        ServerPid = $ServerPid
        Port = $port
        ChromeProfile = $chromeProfile
        ServerFile = $serverFile
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
    `$names = @('node', 'npm', 'cmd', 'powershell', 'pwsh')
    Get-NetTCPConnection -LocalPort `$LocalPort -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object {
            `$process = Get-Process -Id `$_ -ErrorAction SilentlyContinue
            if (`$process -and (`$names -contains `$process.ProcessName.ToLowerInvariant())) {
                Stop-ProcessTree -ProcessId ([int]`$process.Id)
            }
        }
}
while (Get-Process -Id ([int]`$config.ParentPid) -ErrorAction SilentlyContinue) {
    Start-Sleep -Seconds 1
}
Stop-ProcessTree -ProcessId ([int]`$config.ServerPid)
Stop-ByCommandLineText -Text ([string]`$config.ServerFile)
Stop-ByCommandLineText -Text ([string]`$config.ChromeProfile)
Stop-PortProcesses -LocalPort ([int]`$config.Port)
"@

    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($watcherSource))
    $ps = (Get-Command powershell.exe -ErrorAction Stop).Source
    Start-Process -FilePath $ps -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $encoded) -WindowStyle Hidden | Out-Null
}

function Stop-AllStartedStuff {
    Write-Step "Cleanup: closing the Chrome frontend window, Node server, and port $port. MT5 will remain open."
    if ($script:serverProcess) {
        Stop-ProcessTree -ProcessId ([int]$script:serverProcess.Id)
    }
    Stop-ProcessesByCommandLineText -Text $serverFile -ExcludeIds @($PID)
    Stop-ProcessesByCommandLineText -Text $chromeProfile -ExcludeIds @($PID)
    Stop-NodeLikePortProcesses -LocalPort $port
}

try {
    Clear-Host
    Write-Step "Root: $root"
    Set-Location -LiteralPath $root

    Ensure-Mt5Open
    Test-PythonWorker

    $script:serverProcess = Start-Server
    Start-CleanupWatcher -ServerPid ([int]$script:serverProcess.Id)

    if (-not (Wait-ForServer -Url $frontendUrl -TimeoutSeconds 90)) {
        Write-Step "The server did not become ready. Error log:"
        if (Test-Path -LiteralPath $serverErr) {
            Get-Content -LiteralPath $serverErr -Tail 40 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
        }
        throw "Server failed to start on $frontendUrl"
    }

    Start-IsolatedChrome
    Write-Host ""
    Write-Step "Ready: $frontendUrl"
    Write-Step "When you close this CMD window, the frontend Chrome window and backend port will be closed. MT5 will not be closed."
    Write-Host ""

    while ($true) {
        $script:serverProcess.Refresh()
        if ($script:serverProcess.HasExited) {
            throw "The server process stopped. Check the logs: $serverErr"
        }
        Start-Sleep -Seconds 1
    }
} catch {
    Write-Host ""
    Write-Host ("[Algo Desk] Error: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host ""
    Write-Host "This window will close in 8 seconds..."
    Start-Sleep -Seconds 8
    exit 1
} finally {
    Stop-AllStartedStuff
}
