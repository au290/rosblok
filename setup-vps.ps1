[CmdletBinding()]
param(
    [string]$InstallDir = $(if ($env:PANEN_DIR) { $env:PANEN_DIR } else { Join-Path $env:USERPROFILE "panen" })
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Raw = "https://raw.githubusercontent.com/au290/rosblok/main"
$WebDir = Join-Path $InstallDir "web"
$ConfigPath = Join-Path $WebDir "config.txt"
$CredentialPath = Join-Path $WebDir ".credentials"
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Write-Step([string]$Message) {
    Write-Host "[vps-setup] $Message" -ForegroundColor Cyan
}

function Test-Python([string]$Candidate) {
    if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate)) { return $false }
    try {
        $result = & $Candidate -c "import sys; print(sys.executable)" 2>$null
        return $LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($result)
    } catch {
        return $false
    }
}

function Find-Python {
    $candidates = @()
    $commands = @(Get-Command python.exe -All -ErrorAction SilentlyContinue)
    foreach ($command in $commands) {
        if ($command.Source -notlike "*\WindowsApps\python.exe") {
            $candidates += $command.Source
        }
    }
    $candidates += @(Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName)
    $candidates += @(Get-ChildItem "$env:ProgramFiles\Python*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName)

    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (Test-Python $candidate) { return $candidate }
    }

    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        try {
            $resolved = (& $launcher.Source -3 -c "import sys; print(sys.executable)" 2>$null).Trim()
            if ($LASTEXITCODE -eq 0 -and (Test-Python $resolved)) { return $resolved }
        } catch { }
    }
    return $null
}

function Invoke-Checked([string]$FilePath, [string[]]$Arguments) {
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath exited with code $LASTEXITCODE"
    }
}

$Python = Find-Python
if (-not $Python) {
    $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $Winget) {
        throw "Python 3 was not found and winget is unavailable. Install Python 3, then rerun this command."
    }
    Write-Step "installing Python 3 with winget"
    Invoke-Checked $Winget.Source @(
        "install", "--id", "Python.Python.3.12", "--exact", "--silent",
        "--accept-package-agreements", "--accept-source-agreements"
    )
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    $Python = Find-Python
    if (-not $Python) { throw "Python installed, but python.exe could not be located. Open a new PowerShell window and rerun setup." }
}

Write-Step "using Python at $Python"
Write-Step "persistent configuration: $ConfigPath"
New-Item -ItemType Directory -Path (Join-Path $WebDir "assets") -Force | Out-Null

$Files = @(
    "web/server.py",
    "web/requirements.txt",
    "web/config.example.txt",
    "web/hoppers.example.json",
    "web/assets/index.html",
    "web/assets/app.js",
    "web/assets/styles.css"
)

Write-Step "downloading web control plane into $InstallDir"
foreach ($relative in $Files) {
    $target = Join-Path $InstallDir ($relative -replace "/", "\")
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    Invoke-WebRequest "$Raw/$relative" -UseBasicParsing -OutFile $target
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    Copy-Item (Join-Path $WebDir "config.example.txt") $ConfigPath
}

function Get-ConfigValue([string]$Name) {
    $pattern = "^\s*" + [regex]::Escape($Name) + "\s*=(.*)$"
    foreach ($line in [IO.File]::ReadAllLines($ConfigPath)) {
        if ($line -match $pattern) { return $Matches[1].Trim() }
    }
    return ""
}

function Set-ConfigValue([string]$Name, [string]$Value) {
    $pattern = "^\s*" + [regex]::Escape($Name) + "\s*="
    $output = @()
    $found = $false
    foreach ($line in [IO.File]::ReadAllLines($ConfigPath)) {
        if ($line -match $pattern) {
            if (-not $found) { $output += "$Name=$Value" }
            $found = $true
        } else {
            $output += $line
        }
    }
    if (-not $found) { $output += "$Name=$Value" }
    [IO.File]::WriteAllLines($ConfigPath, [string[]]$output, $Utf8NoBom)
}

function Get-CredentialValue([string]$Name) {
    if (-not (Test-Path -LiteralPath $CredentialPath)) { return "" }
    $pattern = "^\s*" + [regex]::Escape($Name) + "\s*=(.*)$"
    foreach ($line in [IO.File]::ReadAllLines($CredentialPath)) {
        if ($line -match $pattern) { return $Matches[1].Trim() }
    }
    return ""
}

function Set-CredentialValue([string]$Name, [string]$Value) {
    $pattern = "^\s*" + [regex]::Escape($Name) + "\s*="
    $output = @()
    $found = $false
    if (Test-Path -LiteralPath $CredentialPath) {
        foreach ($line in [IO.File]::ReadAllLines($CredentialPath)) {
            if ($line -match $pattern) {
                if (-not $found) { $output += "$Name=$Value" }
                $found = $true
            } else {
                $output += $line
            }
        }
    }
    if (-not $found) { $output += "$Name=$Value" }
    [IO.File]::WriteAllLines($CredentialPath, [string[]]$output, $Utf8NoBom)
}

function New-Secret {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Ensure-Secret([string]$Name, [string]$Placeholder) {
    $value = Get-ConfigValue $Name
    if ([string]::IsNullOrWhiteSpace($value) -or $value -eq $Placeholder) {
        $value = Get-CredentialValue $Name
    }
    if ([string]::IsNullOrWhiteSpace($value) -or $value -eq $Placeholder) {
        $value = New-Secret
    }
    Set-ConfigValue $Name $value
    Set-CredentialValue $Name $value
    return $value
}

$KeyValue = Ensure-Secret "KEY" "CHANGE_ME_SHARED_SECRET"
$TokenValue = Ensure-Secret "WEB_TOKEN" "CHANGE_ME_WEB_TOKEN"
if ([string]::IsNullOrWhiteSpace((Get-ConfigValue "HOST"))) { Set-ConfigValue "HOST" "0.0.0.0" }
if ([string]::IsNullOrWhiteSpace((Get-ConfigValue "PORT"))) { Set-ConfigValue "PORT" "8090" }

$VenvDir = Join-Path $InstallDir ".venv"
Write-Step "creating Python virtualenv"
Invoke-Checked $Python @("-m", "venv", $VenvDir)
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
Invoke-Checked $VenvPython @("-m", "pip", "install", "--upgrade", "pip", "--disable-pip-version-check")
Invoke-Checked $VenvPython @("-m", "pip", "install", "-r", (Join-Path $WebDir "requirements.txt"), "--disable-pip-version-check")

$LauncherPath = Join-Path $InstallDir "run-web.ps1"
$Launcher = @'
$ErrorActionPreference = "Stop"
$AppDir = $PSScriptRoot
$PidFile = Join-Path $AppDir "web\server.pid"
$LogFile = Join-Path $AppDir "web\server.log"
$Python = Join-Path $AppDir ".venv\Scripts\python.exe"
$Server = Join-Path $AppDir "web\server.py"
[IO.File]::WriteAllText($PidFile, [string]$PID)
try {
    Set-Location $AppDir
    & $Python $Server *>> $LogFile
} finally {
    if ((Test-Path $PidFile) -and ((Get-Content $PidFile -Raw).Trim() -eq [string]$PID)) {
        Remove-Item $PidFile -Force
    }
}
'@
[IO.File]::WriteAllText($LauncherPath, $Launcher, $Utf8NoBom)

$PidFile = Join-Path $WebDir "server.pid"
if (Test-Path -LiteralPath $PidFile) {
    $OldPid = 0
    if ([int]::TryParse((Get-Content $PidFile -Raw).Trim(), [ref]$OldPid)) {
        $oldProcess = Get-Process -Id $OldPid -ErrorAction SilentlyContinue
        if ($oldProcess) {
            Write-Step "stopping previous web process $OldPid"
            Stop-Process -Id $OldPid -Force
            Start-Sleep -Milliseconds 500
        }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

$PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$LauncherArgs = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$LauncherPath`""
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if ($isAdmin -and (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
    $taskName = "HopperFleetWeb"
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) { Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue }
    $action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $LauncherArgs -WorkingDirectory $InstallDir
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -User "SYSTEM" -RunLevel Highest -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Write-Step "registered and started scheduled task $taskName"
} else {
    $startup = [Environment]::GetFolderPath("Startup")
    $shortcutPath = Join-Path $startup "HopperFleetWeb.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $PowerShellExe
    $shortcut.Arguments = $LauncherArgs
    $shortcut.WorkingDirectory = $InstallDir
    $shortcut.WindowStyle = 7
    $shortcut.Save()
    Start-Process -FilePath $PowerShellExe -ArgumentList $LauncherArgs -WindowStyle Hidden
    Write-Step "started server and added it to the current user's Startup folder"
}

$Port = 8090
[void][int]::TryParse((Get-ConfigValue "PORT"), [ref]$Port)
if ($isAdmin -and (Get-Command Get-NetFirewallRule -ErrorAction SilentlyContinue)) {
    $ruleName = "Hopper Fleet Web $Port"
    if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow | Out-Null
    }
}

$Healthy = $false
for ($attempt = 0; $attempt -lt 15; $attempt++) {
    Start-Sleep -Seconds 1
    try {
        $response = Invoke-WebRequest "http://127.0.0.1:$Port/healthz" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) { $Healthy = $true; break }
    } catch { }
}

if ($Healthy) {
    Write-Step "health check passed"
} else {
    Write-Warning "The server did not answer its health check. Review $WebDir\server.log"
}

Write-Host ""
Write-Host "[vps-setup] dashboard: http://agent.kqing.web.id/"
Write-Host "[vps-setup] local dashboard: http://127.0.0.1:$Port/"
Write-Host "[vps-setup] phone KEY: $(Get-ConfigValue 'KEY')"
Write-Host "[vps-setup] browser WEB_TOKEN: $(Get-ConfigValue 'WEB_TOKEN')"
