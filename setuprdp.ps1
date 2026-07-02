# setuprdp.ps1 - interactive one-shot setup for the WINDOWS VPS/RDP.
# Installs deps, downloads server.py + vps_update.ps1 + monitor.ps1, PROMPTS for all
# config, writes token.txt/config.txt/webhook.txt (ascii, no BOM so Python can read them),
# and launches the bot + monitor (optionally as Scheduled Tasks that survive reboot).
#
#   powershell -ExecutionPolicy Bypass -File setuprdp.ps1
#
# Re-runnable: current values are offered as defaults - press Enter to keep them.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$Raw = "https://raw.githubusercontent.com/au290/rosblok/main"

function Ask($prompt, $default = "") {
    if ($default) { $prompt = "$prompt [$default]" }
    $v = Read-Host $prompt
    if (-not $v -and $default) { return $default }
    return $v
}
function WriteAscii($name, $text) {
    # ascii / no BOM - PowerShell's Out-File default is UTF-16, which Python can't parse
    [System.IO.File]::WriteAllText((Join-Path $PSScriptRoot $name), $text, (New-Object System.Text.ASCIIEncoding))
}
function LoadCfg($name) {
    $h = @{}
    if (Test-Path $name) {
        Get-Content $name | ForEach-Object {
            if ($_ -match '^\s*([^#=]+)=(.*)$') { $h[$matches[1].Trim()] = $matches[2].Trim() }
        }
    }
    return $h
}

Write-Host "== RDP setup ==" -ForegroundColor Cyan

# 1) python
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $py) { Write-Error "Python not found in PATH - install Python 3 first."; exit 1 }
Write-Host "[setup] python: $py"
Write-Host "[setup] installing discord.py + aiohttp..."
& $py -m pip install -q -U discord.py aiohttp

# 2) code
Write-Host "[setup] downloading scripts..."
foreach ($f in "server.py", "vps_update.ps1", "monitor.ps1") {
    Invoke-WebRequest -Uri "$Raw/$f" -OutFile $f -UseBasicParsing
}

# 3) config - prompt for EVERYTHING (existing values offered as defaults)
$cfg = LoadCfg "config.txt"
$tokDef = if (Test-Path token.txt) { (Get-Content token.txt -Raw).Trim() } else { "" }
$whDef  = if (Test-Path webhook.txt) { (Get-Content webhook.txt -Raw).Trim() } else { "" }
$phDef  = if ($cfg.ContainsKey("PHONES")) { $cfg["PHONES"] } else { "A,B" }

$tok = Ask "Discord bot token" $tokDef
$gid = Ask "Server (guild) ID"  $cfg["GUILD_ID"]
$key = Ask "Shared KEY (must match agent.py on the phones)" $cfg["KEY"]
$phs = Ask "Phone ids allowed"  $phDef
$wh  = Ask "Discord webhook URL for the monitor (blank to skip)" $whDef

WriteAscii "token.txt"  ($tok + "`n")
WriteAscii "config.txt" "GUILD_ID=$gid`nKEY=$key`nPHONES=$phs`n"
if ($wh) { WriteAscii "webhook.txt" ($wh + "`n") }

# 4) launch in visible windows (box is never rebooted, so no autostart needed)
function Launch($file) {
    $arg = "-ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot $file)`""
    Start-Process powershell -ArgumentList $arg
    Write-Host "[setup] launched: $file"
}

Launch "vps_update.ps1"    # server.py + auto-update
Launch "monitor.ps1"       # 24h stats -> stats.html + Discord webhook graph

Write-Host "== done ==" -ForegroundColor Green
Write-Host "bot log:  $PSScriptRoot\bot.log   (errors: bot.err.log)"
Write-Host "monitor:  $PSScriptRoot\stats.html   $(if ($wh) {'+ Discord webhook message'})"
