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
$script:pythonPath = $null

function Write-Step {
    param([string]$Message) 
    Write-Host ("[Algo Desk] " + $Message)
}

function Write-WarningStep {
    param([string]$Message)
    Write-Host ("[Algo Desk] WARNING: " + $Message) -ForegroundColor Yellow
}

function Assert-CommandAvailable {
    param(
        [string]$Name,
        [string]$InstallHint
    )
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "$Name was not found. $InstallHint"
    }
    return $command.Source
}

function Get-MajorVersion {
    param([string]$Text)
    $match = [regex]::Match($Text, '(\d+)')
    if (-not $match.Success) { return 0 }
    return [int]$match.Groups[1].Value
}

function Update-RepositorySafely {
    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    $gitDir = Join-Path $root ".git"
    if (-not $git -or -not (Test-Path -LiteralPath $gitDir)) {
        Write-Step "Git update skipped (Git or repository metadata not available)."
        return
    }

    & $git.Source -C $root diff --quiet --ignore-submodules --
    $worktreeClean = $LASTEXITCODE -eq 0
    & $git.Source -C $root diff --cached --quiet --ignore-submodules --
    $indexClean = $LASTEXITCODE -eq 0
    if (-not $worktreeClean -or -not $indexClean) {
        Write-WarningStep "Local tracked changes found; automatic pull skipped to protect your work."
        return
    }

    $branch = (& $git.Source -C $root branch --show-current 2>$null | Select-Object -First 1).Trim()
    $remote = (& $git.Source -C $root remote get-url origin 2>$null | Select-Object -First 1)
    if ([string]::IsNullOrWhiteSpace($branch) -or [string]::IsNullOrWhiteSpace($remote)) {
        Write-Step "Git update skipped (origin or current branch is not configured)."
        return
    }

    Write-Step "Checking GitHub for the latest $branch version..."
    $previousPrompt = $env:GIT_TERMINAL_PROMPT
    $env:GIT_TERMINAL_PROMPT = "0"
    try {
        $gitLog = Join-Path $runtimeRoot "git-update.log"
        $gitErr = Join-Path $runtimeRoot "git-update.err.log"
        Remove-Item -LiteralPath $gitLog -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $gitErr -Force -ErrorAction SilentlyContinue
        $fetch = Start-Process -FilePath $git.Source `
            -ArgumentList @("-C", "`"$root`"", "-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=10", "fetch", "--no-tags", "origin", $branch) `
            -WindowStyle Hidden -RedirectStandardOutput $gitLog -RedirectStandardError $gitErr -PassThru
        if (-not $fetch.WaitForExit(30000)) {
            Stop-ProcessTree -ProcessId ([int]$fetch.Id)
            Write-WarningStep "GitHub check timed out after 30 seconds; starting the local version."
            return
        }
        if ($fetch.ExitCode -ne 0) {
            $detail = (Get-Content -LiteralPath $gitErr -Tail 2 -ErrorAction SilentlyContinue) -join " "
            Write-WarningStep "Git update unavailable; starting local version. $detail"
            return
        }

        $runtimeChanges = @(& $git.Source -C $root diff --name-only HEAD FETCH_HEAD -- data instance 2>$null)
        if ($runtimeChanges.Count -gt 0) {
            Write-WarningStep "Remote update contains database/runtime files; automatic pull skipped to protect local trading data."
            return
        }

        & $git.Source -C $root merge --ff-only FETCH_HEAD
        if ($LASTEXITCODE -ne 0) {
            Write-WarningStep "Remote update is not a safe fast-forward; starting the local version without changing files."
            return
        }
        Write-Step "Repository is up to date."
    } finally {
        if ($null -eq $previousPrompt) { Remove-Item Env:\GIT_TERMINAL_PROMPT -ErrorAction SilentlyContinue } else { $env:GIT_TERMINAL_PROMPT = $previousPrompt }
    }
}

function Stop-ExistingAlgoDesk {
    Write-Step "Checking for an older Algo Desk session..."
    Stop-OrphanNextBuilds
    Stop-ProcessesByCommandLineText -Text $serverFile -ExcludeIds @($PID)
    Stop-NodeLikePortProcesses -LocalPort $port
    Start-Sleep -Seconds 2
}

function Stop-OrphanNextBuilds {
    foreach ($item in Get-ProcessesByCommandLineText -Text $root) {
        if ([int]$item.ProcessId -eq $PID) { continue }
        $line = [string]$item.CommandLine
        if ($line -match '(?i)next(\\|/|\.cmd|\s).*\sbuild(?:\s|$)') {
            Write-Step "Stopping orphan Next.js build process #$($item.ProcessId)."
            Stop-ProcessTree -ProcessId ([int]$item.ProcessId)
        }
    }
}

function Repair-NextCache {
    # Only clear the webpack/cache folder. Never delete the whole .next build
    # (that removes BUILD_ID and breaks production start).
    $cachePath = Join-Path $root ".next\cache"
    if (-not (Test-Path -LiteralPath $cachePath)) { return }
    $resolvedRoot = [IO.Path]::GetFullPath($root).TrimEnd('\')
    $resolvedCache = [IO.Path]::GetFullPath($cachePath)
    if (-not $resolvedCache.StartsWith($resolvedRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean an unsafe Next.js cache path: $resolvedCache"
    }
    Write-Step "Cleaning the Next.js cache folder for a reliable startup..."
    Remove-Item -LiteralPath $resolvedCache -Recurse -Force -ErrorAction SilentlyContinue
}

function Ensure-NodeEnvironment {
    $node = Assert-CommandAvailable -Name "node.exe" -InstallHint "Install Node.js 20 LTS or newer, then start Algo Desk again."
    $npm = Assert-CommandAvailable -Name "npm.cmd" -InstallHint "Repair the Node.js installation so npm is available."
    $nodeVersion = (& $node --version).Trim()
    if ((Get-MajorVersion $nodeVersion) -lt 20) {
        throw "Node.js $nodeVersion is unsupported. Install Node.js 20 LTS or newer."
    }
    Write-Step "Node.js $nodeVersion is ready."

    $lockFile = Join-Path $root "package-lock.json"
    $packageFile = Join-Path $root "package.json"
    if (-not (Test-Path -LiteralPath $packageFile) -or -not (Test-Path -LiteralPath $lockFile)) {
        throw "package.json or package-lock.json is missing."
    }
    $nodeModules = Join-Path $root "node_modules"
    $stampFile = Join-Path $nodeModules ".algo-package-lock.sha256"
    $lockHash = (Get-FileHash -LiteralPath $lockFile -Algorithm SHA256).Hash
    $savedHash = if (Test-Path -LiteralPath $stampFile) { (Get-Content -Raw -LiteralPath $stampFile).Trim() } else { "" }
    $nextPackage = Join-Path $nodeModules "next\package.json"
    if (-not (Test-Path -LiteralPath $nextPackage) -or $savedHash -ne $lockHash) {
        Write-Step "Installing exact frontend dependencies (first run or package change)..."
        & $npm ci --no-audit --no-fund --fetch-retries=2 --fetch-timeout=30000
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed. Check internet access and npm configuration." }
        Set-Content -LiteralPath $stampFile -Value $lockHash -Encoding Ascii
        Write-Step "Frontend dependencies are ready."
    } else {
        Write-Step "Frontend dependencies are already up to date."
    }
}

function Ensure-PythonEnvironment {
    $script:pythonPath = Assert-CommandAvailable -Name "python.exe" -InstallHint "Install 64-bit Python 3.10 or newer and enable Add Python to PATH."
    $pythonVersion = (& $script:pythonPath --version 2>&1 | Out-String).Trim()
    $pythonSupported = (& $script:pythonPath -c "import sys; print('yes' if sys.version_info >= (3, 10) else 'no')").Trim()
    if ($pythonSupported -ne "yes") { throw "$pythonVersion is unsupported. Install 64-bit Python 3.10 or newer." }
    Write-Step "$pythonVersion is ready."
    & $script:pythonPath -c "import MetaTrader5, sqlite3, zoneinfo" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Step "Installing missing Python/MT5 dependency..."
        & $script:pythonPath -m pip install --disable-pip-version-check --no-input --retries 2 --timeout 30 "MetaTrader5>=5.0.0"
        if ($LASTEXITCODE -ne 0) { throw "MetaTrader5 Python dependency installation failed." }
        & $script:pythonPath -c "import MetaTrader5, sqlite3, zoneinfo"
        if ($LASTEXITCODE -ne 0) { throw "Python dependencies are still unavailable after installation." }
    }
    Write-Step "Python dependencies are ready."
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
    $worker = Join-Path $root "python\algo_worker.py"
    try {
        $output = & $script:pythonPath $worker status 2>$null
        if ($LASTEXITCODE -eq 0 -and $output) {
            Write-Step "Python worker is ready."
        } else {
            throw "Python worker returned no valid status."
        }
    } catch {
        throw "Python worker check failed: $($_.Exception.Message)"
    }
}

function Ensure-NodeModules {
    $nextModule = Join-Path $root "node_modules\next"
    if (Test-Path -LiteralPath $nextModule) {
        Write-Step "Node dependencies are ready."
        return
    }

    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) {
        $npm = Get-Command npm -ErrorAction SilentlyContinue
    }
    if (-not $npm) {
        throw "npm was not found. Install Node.js LTS from https://nodejs.org , then run: npm install"
    }

    $packageJson = Join-Path $root "package.json"
    if (-not (Test-Path -LiteralPath $packageJson)) {
        throw "package.json was not found: $packageJson"
    }

    Write-Step "node_modules is missing. Running npm install (first run can take a few minutes)..."
    Push-Location -LiteralPath $root
    try {
        & $npm.Source install --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) {
            throw "npm install failed in: $root"
        }
    } finally {
        Pop-Location
    }

    if (-not (Test-Path -LiteralPath $nextModule)) {
        throw "npm install finished but package 'next' is still missing. Run 'npm install' manually in: $root"
    }

    Write-Step "Node dependencies installed."
}

function Ensure-ProductionBuild {
    $buildId = Join-Path $root ".next\BUILD_ID"
    if (Test-Path -LiteralPath $buildId) {
        Write-Step "Production build is ready."
        return
    }

    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) {
        $npm = Get-Command npm -ErrorAction SilentlyContinue
    }
    if (-not $npm) {
        throw "npm was not found. Install Node.js LTS from https://nodejs.org"
    }

    Write-Step "First run: building frontend (5-15 min on slow PCs - progress below)..."
    Write-Host ""
    Push-Location -LiteralPath $root
    try {
        $env:NEXT_TELEMETRY_DISABLED = "1"
        & $npm.Source run build
        if ($LASTEXITCODE -ne 0) {
            Write-Step "Normal build failed. Trying low-memory build ..."
            & $npm.Source run build:vps
            if ($LASTEXITCODE -ne 0) {
                throw "npm run build failed in: $root"
            }
        }
    } finally {
        Pop-Location
    }
    Write-Host ""
    if (-not (Test-Path -LiteralPath $buildId)) {
        throw "Build finished but .next\BUILD_ID is still missing."
    }
    Write-Step "Production build complete."
}

function Show-ServerLogSnippet {
    param(
        [string]$OutPath,
        [string]$ErrPath,
        [int]$TailLines = 8
    )

    foreach ($path in @($OutPath, $ErrPath)) {
        if ([string]::IsNullOrWhiteSpace($path)) {
            continue
        }
        if (-not (Test-Path -LiteralPath $path)) {
            continue
        }
        $name = Split-Path -Leaf $path
        $lines = @(Get-Content -LiteralPath $path -Tail $TailLines -ErrorAction SilentlyContinue)
        if ($lines.Count -eq 0) {
            continue
        }
        Write-Host ""
        Write-Host ("  --- " + $name + " ---") -ForegroundColor DarkGray
        $lines | ForEach-Object { Write-Host ("  " + $_) -ForegroundColor DarkGray }
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

    Write-Step "Starting server in production mode: node server.mjs"
    $previousNodeEnv = $env:NODE_ENV
    $previousHost = $env:HOST
    $previousPort = $env:PORT
    $previousTelemetry = $env:NEXT_TELEMETRY_DISABLED
    try {
        $env:NODE_ENV = "production"
        $env:HOST = $hostName
        $env:PORT = [string]$port
        $env:NEXT_TELEMETRY_DISABLED = "1"
        return Start-Process `
            -FilePath $node.Source `
            -ArgumentList @("`"$serverFile`"") `
            -WorkingDirectory $root `
            -WindowStyle Hidden `
            -RedirectStandardOutput $serverOut `
            -RedirectStandardError $serverErr `
            -PassThru
    } finally {
        if ($null -eq $previousNodeEnv) { Remove-Item Env:\NODE_ENV -ErrorAction SilentlyContinue } else { $env:NODE_ENV = $previousNodeEnv }
        if ($null -eq $previousHost) { Remove-Item Env:\HOST -ErrorAction SilentlyContinue } else { $env:HOST = $previousHost }
        if ($null -eq $previousPort) { Remove-Item Env:\PORT -ErrorAction SilentlyContinue } else { $env:PORT = $previousPort }
        if ($null -eq $previousTelemetry) { Remove-Item Env:\NEXT_TELEMETRY_DISABLED -ErrorAction SilentlyContinue } else { $env:NEXT_TELEMETRY_DISABLED = $previousTelemetry }
    }
}

function Wait-ForServer {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 300,
        [string]$OutLog,
        [string]$ErrLog
    )

    $started = Get-Date
    $deadline = $started.AddSeconds($TimeoutSeconds)
    $lastStatusAt = [datetime]::MinValue
    while ((Get-Date) -lt $deadline) {
        if ($script:serverProcess) {
            $script:serverProcess.Refresh()
            if ($script:serverProcess.HasExited) {
                Write-Step "Server process stopped early (exit code $($script:serverProcess.ExitCode))."
                return $false
            }
        }

        $elapsed = [int]((Get-Date) - $started).TotalSeconds
        if (((Get-Date) - $lastStatusAt).TotalSeconds -ge 10) {
            Write-Step "Still starting... ${elapsed}s / ${TimeoutSeconds}s"
            Show-ServerLogSnippet -OutPath $OutLog -ErrPath $ErrLog
            $lastStatusAt = Get-Date
        }

        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                # A different app may already own the port and answer this request
                # while our Node process is still failing with EADDRINUSE. Confirm
                # that the process we started survives before declaring readiness.
                Start-Sleep -Milliseconds 750
                if ($script:serverProcess) {
                    $script:serverProcess.Refresh()
                    if ($script:serverProcess.HasExited) {
                        Write-Step "Server process stopped during readiness validation (exit code $($script:serverProcess.ExitCode))."
                        return $false
                    }
                }
                return $true
            }
        } catch {
        }
        Start-Sleep -Milliseconds 1000
    }

    return $false
}

function Start-ServerReliably {
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        Write-Step "Server startup attempt $attempt of 2..."
        $script:serverProcess = Start-Server
        if (Wait-ForServer -Url $frontendUrl -TimeoutSeconds 120 -OutLog $serverOut -ErrLog $serverErr) {
            return $true
        }

        Write-WarningStep "Server attempt $attempt did not become healthy."
        if ($script:serverProcess) {
            Stop-ProcessTree -ProcessId ([int]$script:serverProcess.Id)
            $script:serverProcess = $null
        }
        if (-not [string]::IsNullOrWhiteSpace($serverErr) -and (Test-Path -LiteralPath $serverErr)) {
            Get-Content -LiteralPath $serverErr -Tail 20 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
        }
        if ($attempt -lt 2) {
            Write-Step "Repairing generated files before automatic retry..."
            Stop-OrphanNextBuilds
            Repair-NextCache
            Ensure-ProductionBuild
        }
    }
    return $false
}

function Start-IsolatedChrome {
    try {
        $chrome = Get-ChromePath
    } catch {
        Write-WarningStep "Chrome was not found; opening Algo Desk in the default browser."
        Start-Process $frontendUrl | Out-Null
        return
    }

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
        ChromeProfile = $chromeProfile
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
while (Get-Process -Id ([int]`$config.ParentPid) -ErrorAction SilentlyContinue) {
    Start-Sleep -Seconds 1
}
Stop-ProcessTree -ProcessId ([int]`$config.ServerPid)
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    `$_.CommandLine -and `$_.CommandLine.IndexOf([string]`$config.ChromeProfile, [StringComparison]::OrdinalIgnoreCase) -ge 0
} | ForEach-Object {
    if ([int]`$_.ProcessId -ne `$PID) { Stop-ProcessTree -ProcessId ([int]`$_.ProcessId) }
}
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
    Stop-ProcessesByCommandLineText -Text $chromeProfile -ExcludeIds @($PID)
}

try {
    Clear-Host
    Write-Step "Root: $root"
    Set-Location -LiteralPath $root

    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
    Stop-ExistingAlgoDesk
    Update-RepositorySafely
    Ensure-NodeEnvironment
    Ensure-PythonEnvironment
    Ensure-Mt5Open
    Test-PythonWorker
    Ensure-NodeModules
    Ensure-ProductionBuild

    if (-not (Start-ServerReliably)) {
        Write-Step "The server did not become ready. Error log:"
        if (Test-Path -LiteralPath $serverErr) {
            $errorLog = Get-Content -LiteralPath $serverErr -Tail 40 -ErrorAction SilentlyContinue
            $errorLog | ForEach-Object { Write-Host $_ }
            if ($errorLog -match "ERR_MODULE_NOT_FOUND|Cannot find package 'next'") {
                Write-Host ""
                Write-Step "Fix: open CMD in the project folder and run: npm install"
            }
        }
        throw "Server failed to start on $frontendUrl"
    }
    Start-CleanupWatcher -ServerPid ([int]$script:serverProcess.Id)

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
    Write-Host ("Detailed logs: " + $serverErr)
    Write-Host ""
    Write-Host "Press any key to close this window..." -ForegroundColor Yellow
    try {
        $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    } catch {
        Start-Sleep -Seconds 30
    }
    exit 1
} finally {
    Stop-AllStartedStuff
}
